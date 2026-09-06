"""Log legivel (pt-BR) com redacao de segredo em TODA linha, inclusive tracebacks."""

from __future__ import annotations

import logging
import sys

from drophunter_agent.redact import REDATOR

_NIVEIS = {"DEBUG": "dbg", "INFO": "info", "WARNING": "aviso", "ERROR": "ERRO", "CRITICAL": "FATAL"}


class FormatadorRedigido(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.nivel_br = _NIVEIS.get(record.levelname, record.levelname.lower())
        try:
            texto = super().format(record)
        except Exception:
            texto = f"{record.levelname} (mensagem de log invalida)"
        return REDATOR.texto(texto)


def configurar_log(nivel: str = "INFO", stream=None) -> logging.Logger:
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(FormatadorRedigido("%(asctime)s %(nivel_br)-5s %(message)s", "%H:%M:%S"))
    raiz.addHandler(handler)
    raiz.setLevel(getattr(logging, str(nivel).upper(), logging.INFO))
    # bibliotecas falam demais e podem ecoar cabecalhos
    for nome in ("httpx", "httpcore", "engineio", "socketio", "aiohttp", "asyncio"):
        logging.getLogger(nome).setLevel(logging.WARNING)
    return logging.getLogger("drophunter")


def configurar_log_arquivo(pasta):
    """Modo sem console: rotação local com o mesmo redator de todos os logs."""
    import os
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True, mode=0o700)
    arquivo = pasta / "drophunter.log"
    fd = os.open(arquivo, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(fd)
    logger = configurar_log()
    raiz = logging.getLogger()
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        arquivo, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(FormatadorRedigido("%(asctime)s %(nivel_br)-5s %(message)s"))
    raiz.addHandler(handler)
    return logger
