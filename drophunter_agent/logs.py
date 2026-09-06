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
