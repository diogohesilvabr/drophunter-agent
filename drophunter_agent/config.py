"""Config local do agente: ~/.drophunter/agent.toml (chmod 600).

Aqui vive a chave do Empire. Ela e lida daqui e usada SO contra o Empire.
Nao existe caminho de codigo que a mande pro servidor.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tomllib
from dataclasses import dataclass, field
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
            dados = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        raise ConfigInvalida("Config inválida (TOML); confira o agent.toml") from None
    cfg = Config(
        licenca=str(dados.get("licenca", "")),
        empire_api_key=str(dados.get("empire_api_key", "")),
        server_url=str(dados.get("server_url", SERVER_URL_PADRAO)),
        steam_api_key=str(dados.get("steam_api_key", "") or ""),
        steam_id64=str(dados.get("steam_id64", "") or ""),
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


def salvar(cfg: Config, caminho: Path | None = None) -> Path:
    """Grava o arquivo com 0600 (e o diretorio com 0700). Sobrescreve com backup."""
    caminho = caminho or cfg.caminho or caminho_config()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(caminho.parent, stat.S_IRWXU)
    conteudo = (
        "# DropHunter Agent - config local. NAO compartilhe este arquivo.\n"
        "# A chave do Empire fica SO aqui; o servidor nunca a recebe.\n"
        f"server_url = {_toml_str(cfg.server_url)}\n"
        f"licenca = {_toml_str(cfg.licenca)}\n"
        f"empire_api_key = {_toml_str(cfg.empire_api_key)}\n"
        f"steam_api_key = {_toml_str(cfg.steam_api_key)}\n"
        f"steam_id64 = {_toml_str(cfg.steam_id64)}\n"
        f"log_level = {_toml_str(cfg.log_level)}\n"
        "# API local pra extensao Chrome (so 127.0.0.1). porta 0 desliga; senha vazia = sem auth\n"
        f"local_api_port = {int(cfg.local_api_port)}\n"
        f"api_local_usuario = {_toml_str(cfg.api_local_usuario)}\n"
        f"api_local_senha = {_toml_str(cfg.api_local_senha)}\n"
    )
    if caminho.exists():
        import time as _t

        backup = caminho.with_name(caminho.name + ".bak-" + _t.strftime("%Y%m%d-%H%M%S"))
        os.replace(caminho, backup)
        with contextlib.suppress(OSError):
            os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
    # cria ja com 0600: abre com O_CREAT|0600 antes de escrever qualquer byte
    fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(conteudo)
    with contextlib.suppress(OSError):
        os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)
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
