"""Config local do agente: ~/.drophunter/agent.toml (chmod 600).

Aqui vive a chave do Empire. Ela e lida daqui e usada SO contra o Empire.
Nao existe caminho de codigo que a mande pro servidor.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SERVER_URL_PADRAO = "https://www.drophunter.com.br"
NIVEIS_LOG = ("DEBUG", "INFO", "WARNING", "ERROR")


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
    log_level: str = "INFO"
    caminho: Path | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # nunca imprimir segredo por acidente
        return (
            f"Config(server_url={self.server_url!r}, licenca={mascarar(self.licenca)!r}, "
            f"empire_api_key={'definida' if self.empire_api_key else 'vazia'}, "
            f"steam_api_key={'definida' if self.steam_api_key else 'vazia'}, "
            f"log_level={self.log_level!r})"
        )

    def segredos(self) -> list[str]:
        return [s for s in (self.empire_api_key, self.steam_api_key, self.licenca) if s]

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
        if not self.server_url.startswith(("http://", "https://")):
            raise ConfigInvalida("server_url precisa comecar com http:// ou https://")
        self.server_url = self.server_url.rstrip("/")
        self.log_level = self.log_level.upper()
        if self.log_level not in NIVEIS_LOG:
            self.log_level = "INFO"


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
    except tomllib.TOMLDecodeError as exc:
        raise ConfigInvalida(f"Config {caminho} invalida (TOML): {exc}") from None
    cfg = Config(
        licenca=str(dados.get("licenca", "")),
        empire_api_key=str(dados.get("empire_api_key", "")),
        server_url=str(dados.get("server_url", SERVER_URL_PADRAO)),
        steam_api_key=str(dados.get("steam_api_key", "") or ""),
        log_level=str(dados.get("log_level", "INFO")),
        caminho=caminho,
    )
    cfg.validar()
    _avisar_permissao(caminho)
    return cfg


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
        f"log_level = {_toml_str(cfg.log_level)}\n"
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
