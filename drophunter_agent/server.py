"""Cliente do servidor DropHunter (PROTOCOLO.md). Cinco rotas, HTTPS puro.

Toda chamada leva `Authorization: Bearer <licenca>`. 401 -> LicencaRecusada (o
agente para de operar). Falha de rede -> ServidorIndisponivel (o agente espera e
tenta de novo). TODO JSON de saida passa pelo redator: mesmo que algum texto de
terceiro (corpo do Empire, mensagem de excecao) contenha a chave, ela nao sai.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from drophunter_agent import __version__
from drophunter_agent.redact import Redator

log = logging.getLogger("drophunter.servidor")

TIMEOUT_PADRAO = 15.0
WAIT_COMANDOS_S = 25


class LicencaRecusada(Exception):
    """401 do servidor: licenca inexistente, revogada ou vencida."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class ServidorIndisponivel(Exception):
    """Rede caiu, timeout, 5xx: tentar de novo com backoff."""


class ServidorRecusou(Exception):
    """4xx que nao e 401 (ex.: 400 payload invalido, 409 outro agente ativo)."""

    def __init__(self, http: int, corpo: str) -> None:
        super().__init__(f"HTTP {http}: {corpo[:200]}")
        self.http = http
        self.corpo = corpo


class ServidorClient:
    def __init__(
        self,
        server_url: str,
        licenca: str,
        redator: Redator,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_PADRAO,
    ) -> None:
        self._redator = redator
        self._licenca = licenca
        self._client = httpx.AsyncClient(
            base_url=server_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {licenca}",
                "User-Agent": f"drophunter-agent/{__version__}",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )
        # diferenca relogio servidor - local, atualizada a cada resposta com server_time
        self.desvio_relogio_s: float = 0.0
        self.server_time: float | None = None

    async def close(self) -> None:
        await self._client.aclose()

    def agora_servidor(self) -> float:
        return time.time() + self.desvio_relogio_s

    # ----------------------------------------------------------------- rotas
    async def hello(self, so: str, fingerprint: str) -> dict:
        return await self._post(
            "/api/agent/hello",
            {"version": __version__, "os": so, "fingerprint": fingerprint, "ts": time.time()},
        )

    async def heartbeat(self, status: str, saldo: float | None) -> dict:
        return await self._post(
            "/api/agent/heartbeat",
            {"version": __version__, "status": status, "balance": saldo, "ts": time.time()},
        )

    async def eventos(self, lote: list[dict]) -> dict:
        return await self._post("/api/agent/events", {"events": lote})

    async def comandos(self, wait: int = WAIT_COMANDOS_S) -> list[dict]:
        dados = await self._get("/api/agent/commands", {"wait": wait}, timeout=wait + 10)
        if isinstance(dados, list):
            return [c for c in dados if isinstance(c, dict)]
        if isinstance(dados, dict):
            lista = dados.get("commands") or []
            return [c for c in lista if isinstance(c, dict)]
        return []

    async def resultado(self, resultado: dict) -> dict:
        return await self._post("/api/agent/results", resultado)

    # -------------------------------------------------------------- transporte
    async def _post(self, rota: str, corpo: dict) -> dict:
        corpo = self._redator.estrutura(corpo)
        self._garantir_sem_segredo(rota, corpo)
        return await self._executar("POST", rota, json=corpo)

    async def _get(self, rota: str, params: dict, timeout: float) -> Any:
        return await self._executar("GET", rota, params=params, timeout=timeout)

    def _garantir_sem_segredo(self, rota: str, corpo: Any) -> None:
        """Ultima barreira: se mesmo depois da redacao sobrou segredo, NAO manda."""
        texto = json.dumps(corpo, ensure_ascii=False, default=str)
        for seg in self._redator.segredos:
            if seg != self._licenca and seg in texto:
                raise RuntimeError(f"bloqueado: segredo detectado no corpo de {rota}")

    async def _executar(self, metodo: str, rota: str, **kw: Any) -> Any:
        try:
            resp = await self._client.request(metodo, rota, **kw)
        except httpx.HTTPError as exc:
            raise ServidorIndisponivel(f"{metodo} {rota}: {type(exc).__name__}: {exc}") from None
        if resp.status_code == 401:
            raise LicencaRecusada(_motivo_401(resp))
        if resp.status_code >= 500:
            raise ServidorIndisponivel(f"{metodo} {rota}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ServidorRecusou(resp.status_code, self._redator.texto(resp.text))
        if not resp.content:
            return {}
        try:
            dados = resp.json()
        except ValueError:
            raise ServidorIndisponivel(f"{metodo} {rota}: resposta nao e JSON") from None
        if isinstance(dados, dict) and isinstance(dados.get("server_time"), (int, float)):
            self.server_time = float(dados["server_time"])
            self.desvio_relogio_s = self.server_time - time.time()
        return dados


def _motivo_401(resp: httpx.Response) -> str:
    try:
        dados = resp.json()
        if isinstance(dados, dict):
            for chave in ("motivo", "reason", "detail", "error", "message"):
                if dados.get(chave):
                    return str(dados[chave])
    except ValueError:
        pass
    return (resp.text or "licenca recusada pelo servidor").strip()[:200]
