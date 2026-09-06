"""Config local do agente: ~/.drophunter/agent.toml (chmod 600).

Aqui vive a chave do Empire. Ela e lida daqui e usada SO contra o Empire.
Nao existe caminho de codigo que a mande pro servidor.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SERVER_URL_PADRAO = "wss://www.drophunter.com.br/api/agent/ws"
NIVEIS_LOG = ("DEBUG", "INFO", "WARNING", "ERROR")
API_LOCAL_PORTA_PADRAO = 8765
API_LOCAL_USUARIO_PADRAO = "drophunter"


class ConfigInvalida(Exception):
    """Config ausente ou incompleta. A mensagem ja vem pronta pra mostrar ao usuario."""


def diretorio_config() -> Path:
    raiz = os.environ.get("DROPHUNTER_HOME")
    return Path(raiz) if raiz else Path.home() / ".drophunter"


def caminho_config() -> Path:
    return diretorio_config() / "agent.toml"


@dataclass
class Config:
    licenca: str
    empire_api_key: str
    server_url: str = SERVER_URL_PADRAO
    steam_api_key: str = ""
    steam_id64: str = ""
    plataforma_steam_id: str = ""
    pagamento_teto_coins: Decimal = Decimal("100")
    log_level: str = "INFO"
    #: API local pra extensao Chrome (127.0.0.1). porta 0 = desligada; senha vazia = sem auth
    local_api_port: int = API_LOCAL_PORTA_PADRAO
    api_local_usuario: str = API_LOCAL_USUARIO_PADRAO
    api_local_senha: str = ""
    caminho: Path | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # nunca imprimir segredo por acidente
        return (
            f"Config(server_url='configurada', licenca={mascarar(self.licenca)!r}, "
            f"empire_api_key={'definida' if self.empire_api_key else 'vazia'}, "
            f"steam_api_key={'definida' if self.steam_api_key else 'vazia'}, "
            f"log_level={self.log_level!r}, local_api_port={self.local_api_port})"
        )

    def segredos(self) -> list[str]:
        return [
            s
            for s in (self.empire_api_key, self.steam_api_key, self.licenca, self.api_local_senha)
            if s
        ]

    @property
    def api_local_url(self) -> str:
        return f"http://127.0.0.1:{self.local_api_port}"

    def validar(self) -> None:
        faltando = []
        if not self.licenca.strip():
            faltando.append("licenca")
        if not self.empire_api_key.strip():
            faltando.append("empire_api_key")
        if faltando:
            raise ConfigInvalida(
                "Config incompleta: falta "
                + ", ".join(faltando)
                + f" em {self.caminho or caminho_config()}. Rode 'drophunter-agent init'."
            )
        try:
            url = urlsplit(self.server_url)
            _ = url.port
        except ValueError:
            raise ConfigInvalida("server_url inválida") from None
        if (
            url.scheme not in {"ws", "wss", "http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ConfigInvalida("server_url exige WebSocket sem credenciais, query ou fragmento")
        if url.scheme in {"ws", "http"} and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigInvalida("server_url exige wss fora do loopback")
        esquema = {"https": "wss", "http": "ws"}.get(url.scheme, url.scheme)
        self.server_url = urlunsplit(
            (esquema, url.netloc, url.path.rstrip("/") or "/api/agent/ws", "", "")
        )
        if self.steam_id64 and (
            len(self.steam_id64) != 17
            or not self.steam_id64.isascii()
            or not self.steam_id64.isdecimal()
        ):
            raise ConfigInvalida("steam_id64 precisa ter 17 dígitos")
        if self.plataforma_steam_id and not steam64_valido(self.plataforma_steam_id):
            raise ConfigInvalida("plataforma_steam_id precisa ser Steam64 (7656 e 17 dígitos)")
        try:
            self.pagamento_teto_coins = Decimal(str(self.pagamento_teto_coins))
            if not self.pagamento_teto_coins.is_finite() or self.pagamento_teto_coins <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ConfigInvalida(
                "pagamento_teto_coins precisa ser decimal positivo finito"
            ) from None
        self.log_level = self.log_level.upper()
        if self.log_level not in NIVEIS_LOG:
            self.log_level = "INFO"
        try:
            self.local_api_port = int(self.local_api_port)
        except (TypeError, ValueError):
            raise ConfigInvalida("local_api_port precisa ser um numero (0 desliga)") from None
        if not 0 <= self.local_api_port <= 65535:
            raise ConfigInvalida("local_api_port fora da faixa 0-65535")
        self.api_local_usuario = (self.api_local_usuario or API_LOCAL_USUARIO_PADRAO).strip()


def mascarar(valor: str, mostrar: int = 4) -> str:
    """'lic_abcdef...' -> 'lic_abcd…' (so pra tela; nunca o valor inteiro)."""
    if not valor:
        return "(vazio)"
    prefixo = "lic_" if valor.startswith("lic_") else ""
    corpo = valor[len(prefixo) :]
    return f"{prefixo}{corpo[:mostrar]}…"


def carregar(caminho: Path | None = None) -> Config:
    caminho = caminho or caminho_config()
    if not caminho.exists():
        raise ConfigInvalida(
            f"Nao achei a config em {caminho}.\n"
            "Rode 'drophunter-agent init' para criar (precisa da licenca e da chave do Empire)."
        )
    try:
        with open(caminho, "rb") as f:
            dados = tomllib.load(f, parse_float=Decimal)
    except tomllib.TOMLDecodeError:
        raise ConfigInvalida("Config inválida (TOML); confira o agent.toml") from None
    cfg = Config(
        licenca=str(dados.get("licenca", "")),
        empire_api_key=str(dados.get("empire_api_key", "")),
        server_url=str(dados.get("server_url", SERVER_URL_PADRAO)),
        steam_api_key=str(dados.get("steam_api_key", "") or ""),
        steam_id64=str(dados.get("steam_id64", "") or ""),
        plataforma_steam_id=dados.get("plataforma_steam_id", ""),
        pagamento_teto_coins=dados.get("pagamento_teto_coins", 100),
        log_level=str(dados.get("log_level", "INFO")),
        local_api_port=dados.get(
            "local_api_port", dados.get("api_local_porta", API_LOCAL_PORTA_PADRAO)
        ),
        api_local_usuario=str(dados.get("api_local_usuario", API_LOCAL_USUARIO_PADRAO) or ""),
        api_local_senha=str(dados.get("api_local_senha", "") or ""),
        caminho=caminho,
    )
    cfg.validar()
    _avisar_permissao(caminho)
    return cfg


def _toml_str(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def steam64_valido(valor) -> bool:
    return isinstance(valor, str) and re.fullmatch(r"7656[0-9]{13}", valor) is not None


def _toml_valor(valor) -> str:
    if isinstance(valor, str):
        return _toml_str(valor)
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    if isinstance(valor, list):
        return "[" + ", ".join(_toml_valor(v) for v in valor) + "]"
    if isinstance(valor, dict):
        return "{" + ", ".join(f"{_toml_str(k)} = {_toml_valor(v)}" for k, v in valor.items()) + "}"
    if isinstance(valor, (int, float, Decimal)):
        return str(valor).lower().replace("infinity", "inf")
    raise ConfigInvalida("Tipo de configuração não suportado")


def _gravar_atomico(caminho: Path, conteudo: str) -> None:
    fd, temporario = tempfile.mkstemp(prefix=".agent-", dir=caminho.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.chmod(temporario, 0o600)
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporario, caminho)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporario)


def salvar(cfg: Config, caminho: Path | None = None) -> Path:
    """Substitui atomicamente com 0600, preservando campos e backup privado."""
    cfg.validar()
    caminho = caminho or cfg.caminho or caminho_config()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(caminho.parent, 0o700)
    dados = {}
    anterior = None
    if caminho.exists():
        anterior = caminho.read_text(encoding="utf-8")
        dados = tomllib.loads(anterior, parse_float=Decimal)
    for campo in (
        "server_url",
        "licenca",
        "empire_api_key",
        "steam_api_key",
        "steam_id64",
        "log_level",
        "local_api_port",
        "api_local_usuario",
        "api_local_senha",
        "plataforma_steam_id",
        "pagamento_teto_coins",
    ):
        dados[campo] = getattr(cfg, campo)
    conteudo = "# DropHunter Agent — config privada; não compartilhe.\n" + "".join(
        f"{k if re.fullmatch(r'[A-Za-z0-9_-]+', k) else _toml_str(k)} = {_toml_valor(v)}\n"
        for k, v in dados.items()
    )
    if anterior is not None:
        backup = caminho.with_name(
            caminho.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        _gravar_atomico(backup, anterior)
    _gravar_atomico(caminho, conteudo)
    cfg.caminho = caminho
    return caminho


def _avisar_permissao(caminho: Path) -> None:
    if os.name != "posix":
        return
    try:
        modo = stat.S_IMODE(caminho.stat().st_mode)
    except OSError:
        return
    if modo & 0o077:
        import logging

        logging.getLogger("drophunter").warning(
            "A config %s esta legivel por outros usuarios (modo %o). Corrigindo para 600.",
            caminho,
            modo,
        )
        with contextlib.suppress(OSError):
            os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)
