"""Executor de comandos (PROTOCOLO.md): executa EXATAMENTE o payload, nao recalcula.

Vencido -> nao executa, reporta 'expirado'. Repetido -> ignorado. O corpo do Empire
volta redigido e truncado pro servidor decidir o proximo passo.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

from drophunter_agent.empire import EmpireRest, RespostaEmpire
from drophunter_agent.redact import Redator

log = logging.getLogger("drophunter.comando")

TIPOS_EMPIRE = {"bid", "list", "reprice", "cancel", "dispute", "mark_received"}
TIPOS_LOCAIS = {"set_config", "stop", "update_available"}
TAMANHO_MAX_CORPO = 4000


class ComandoInvalido(Exception):
    pass


class Executor:
    def __init__(self, empire: EmpireRest, redator: Redator, agora=None) -> None:
        self._empire = empire
        self._redator = redator
        self._agora = agora or time.time
        self._vistos: OrderedDict[str, None] = OrderedDict()

    def ja_visto(self, cmd_id: str) -> bool:
        if cmd_id in self._vistos:
            return True
        self._vistos[cmd_id] = None
        while len(self._vistos) > 5000:
            self._vistos.popitem(last=False)
        return False

    def expirado(self, cmd: dict) -> bool:
        exp = cmd.get("expires_at")
        if exp is None:
            return False
        try:
            return float(exp) < self._agora()
        except (TypeError, ValueError):
            return False

    async def executar(self, cmd: dict) -> dict:
        """Devolve o resultado pronto pra POST /api/agent/results."""
        cmd_id = str(cmd.get("id") or "")
        tipo = str(cmd.get("type") or "")
        payload = cmd.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if self.expirado(cmd):
            log.warning("Comando %s (%s) chegou vencido; descartado", cmd_id, tipo)
            return self._resultado(cmd_id, "expirado", None, "comando vencido antes de executar")
        if tipo not in TIPOS_EMPIRE:
            return self._resultado(cmd_id, "erro", None, f"tipo de comando desconhecido: {tipo}")
        try:
            resp = await self._despachar(tipo, payload)
        except ComandoInvalido as exc:
            log.error("Comando %s (%s) invalido: %s", cmd_id, tipo, exc)
            return self._resultado(cmd_id, "erro", None, f"payload invalido: {exc}")
        except Exception as exc:  # rede com o Empire
            log.error("Comando %s (%s) falhou: %s: %s", cmd_id, tipo, type(exc).__name__, exc)
            return self._resultado(
                cmd_id, "erro", None, f"erro de rede: {type(exc).__name__}: {exc}"
            )
        status = "ok" if resp.ok else "erro"
        log.info("Comando %s (%s) -> HTTP %s (%s)", cmd_id, tipo, resp.http, status)
        return self._resultado(cmd_id, status, resp.http, resp.texto)

    async def _despachar(self, tipo: str, p: dict) -> RespostaEmpire:
        if tipo == "bid":
            return await self._empire.bid(_int(p, "auction_id"), _num(p, "coins"))
        if tipo == "list":
            return await self._empire.listar(_int(p, "item_id"), _num(p, "coins"))
        if tipo == "reprice":
            return await self._empire.reprecificar(_int(p, "deposit_id"), _num(p, "coins"))
        if tipo == "cancel":
            return await self._empire.cancelar(_int(p, "deposit_id"))
        if tipo == "dispute":
            return await self._empire.disputar(_int(p, "tradeoffer_id"))
        if tipo == "mark_received":
            return await self._empire.marcar_recebido(_int(p, "tradeoffer_id"))
        raise ComandoInvalido(tipo)

    def _resultado(self, cmd_id: str, status: str, http: int | None, corpo: Any) -> dict:
        texto = self._redator.texto(corpo if corpo is not None else "")
        if len(texto) > TAMANHO_MAX_CORPO:
            texto = texto[:TAMANHO_MAX_CORPO] + "…"
        return {
            "command_id": cmd_id,
            "status": status,
            "http": http,
            "body": texto,
            "ts": time.time(),
        }


def _int(p: dict, chave: str) -> int:
    v = p.get(chave)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ComandoInvalido(f"{chave} ausente ou nao inteiro") from None


def _num(p: dict, chave: str) -> float:
    v = p.get(chave)
    if isinstance(v, bool) or v is None:
        raise ComandoInvalido(f"{chave} ausente")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ComandoInvalido(f"{chave} nao numerico") from None
    if f <= 0:
        raise ComandoInvalido(f"{chave} precisa ser positivo")
    return f
