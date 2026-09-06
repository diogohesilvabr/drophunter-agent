"""O minimo do CSGOEmpire que o agente precisa, reimplementado do zero.

REST (https://csgoempire.com/api/v2, Bearer <chave local>):
  GET  /metadata/socket                 -> user, socket_token, socket_signature (e saldo)
  POST /trading/deposit/{id}/bid        {"bid_value": coins*100}
  POST /trading/deposit                 {"items":[{"id": item_id, "coin_value": coins*100}]}
  PATCH /trading/deposit/{id}           {"coin_value": coins*100}   (reprice)
  POST /trading/deposit/{id}/cancel
  POST /trading/deposit/{id}/dispute
  POST /trading/deposit/{id}/received

Feed (Socket.IO): wss://trade.csgoempire.com, path /s/, namespace /trade,
header User-Agent "<uid> API Bot". No 'init' {authenticated:false} emite 'identify'
{uid, model, authorizationToken, signature}; autenticado -> emite 'filters' {}.
Eventos do feed: new_item / updated_item / deleted_item (arrays) e outros.

A chave so aparece no cabecalho Authorization deste cliente. Nunca em log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger("drophunter.empire")

EMPIRE_API = "https://csgoempire.com/api/v2"
EMPIRE_WS = "wss://trade.csgoempire.com"
EMPIRE_WS_PATH = "/s/"
EMPIRE_WS_NS = "/trade"

# eventos que ninguem consome e que afogam o feed (96% do volume no bot antigo)
FEED_IGNORAR = frozenset({"updated_seller_online_status"})


class RespostaEmpire:
    __slots__ = ("http", "texto", "dados")

    def __init__(self, http: int | None, texto: str, dados: Any = None) -> None:
        self.http = http
        self.texto = texto
        self.dados = dados

    @property
    def ok(self) -> bool:
        return self.http is not None and 200 <= self.http < 300


class _Balde:
    """Token bucket simples: 30 req/min, rajada 30 (mesmo limite do bot atual)."""

    def __init__(self, capacidade: int = 30, por_minuto: int = 30) -> None:
        self._cap = capacidade
        self._taxa = por_minuto / 60.0
        self._tokens = float(capacidade)
        self._ts = time.monotonic()

    async def pegar(self) -> None:
        while True:
            agora = time.monotonic()
            self._tokens = min(self._cap, self._tokens + (agora - self._ts) * self._taxa)
            self._ts = agora
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await asyncio.sleep((1 - self._tokens) / self._taxa)


class EmpireRest:
    def __init__(
        self,
        api_key: str,
        base_url: str = EMPIRE_API,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) drophunter-agent",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )
        self._balde = _Balde()
        self._meta_cache: tuple[float, dict | None] = (0.0, None)

    async def close(self) -> None:
        await self._client.aclose()

    async def chamar(
        self, metodo: str, rota: str, json: Any = None, tentativas: int = 3
    ) -> RespostaEmpire:
        """Ate `tentativas` (retry curto em 429/5xx e rede). Nunca levanta por HTTP: o
        status e o corpo voltam pro servidor decidir. Levanta so em erro de rede."""
        ultimo: Exception | None = None
        for tentativa in range(tentativas):
            await self._balde.pegar()
            try:
                resp = await self._client.request(metodo, rota, json=json)
            except httpx.HTTPError as exc:
                ultimo = exc
                await asyncio.sleep(1.5 * (tentativa + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                espera = _retry_after(resp, tentativa)
                log.warning(
                    "Empire respondeu %s em %s %s; espero %.1fs",
                    resp.status_code,
                    metodo,
                    rota,
                    espera,
                )
                if tentativa < tentativas - 1:
                    await asyncio.sleep(espera)
                    continue
            dados = None
            with contextlib.suppress(ValueError):
                dados = resp.json()
            return RespostaEmpire(resp.status_code, resp.text, dados)
        raise ConnectionError(f"Empire inalcancavel em {metodo} {rota}: {type(ultimo).__name__}")

    # --------------------------------------------------------------- acoes
    async def metadata(self, max_idade_s: float = 20.0, tentativas: int = 3) -> dict:
        """user + socket_token + socket_signature (+ saldo). Cache curto: feed e
        heartbeat pedem isso quase junto e o Empire limita a rota (429)."""
        ts, dados = self._meta_cache
        if dados is not None and (time.monotonic() - ts) < max_idade_s:
            return dados
        r = await self.chamar("GET", "/metadata/socket", tentativas=tentativas)
        if r.http == 404:  # rota alternativa citada no brief do 4.0
            r = await self.chamar("GET", "/trading/user/metadata", tentativas=tentativas)
        if not r.ok or not isinstance(r.dados, dict):
            raise ConnectionError(f"metadata do Empire falhou: HTTP {r.http}")
        self._meta_cache = (time.monotonic(), r.dados)
        return r.dados

    async def bid(self, auction_id: int, coins: float) -> RespostaEmpire:
        return await self.chamar(
            "POST", f"/trading/deposit/{int(auction_id)}/bid", {"bid_value": centavos(coins)}
        )

    async def listar(self, item_id: int, coins: float) -> RespostaEmpire:
        return await self.chamar(
            "POST",
            "/trading/deposit",
            {"items": [{"id": int(item_id), "coin_value": centavos(coins)}]},
        )

    async def reprecificar(self, deposit_id: int, coins: float) -> RespostaEmpire:
        return await self.chamar(
            "PATCH", f"/trading/deposit/{int(deposit_id)}", {"coin_value": centavos(coins)}
        )

    async def cancelar(self, deposit_id: int) -> RespostaEmpire:
        return await self.chamar("POST", f"/trading/deposit/{int(deposit_id)}/cancel")

    async def disputar(self, tradeoffer_id: int) -> RespostaEmpire:
        return await self.chamar("POST", f"/trading/deposit/{int(tradeoffer_id)}/dispute")

    async def marcar_recebido(self, tradeoffer_id: int) -> RespostaEmpire:
        return await self.chamar("POST", f"/trading/deposit/{int(tradeoffer_id)}/received")


def centavos(coins: Any) -> int:
    """coins (10.4) -> inteiro em centesimos de coin (1040), sem erro de float."""
    from decimal import ROUND_HALF_UP, Decimal

    return int((Decimal(str(coins)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def saldo_coins(meta: dict) -> float | None:
    user = meta.get("user") if isinstance(meta, dict) else None
    if isinstance(user, dict) and isinstance(user.get("balance"), (int, float)):
        return round(user["balance"] / 100.0, 2)
    return None


def _retry_after(resp: httpx.Response, tentativa: int) -> float:
    try:
        base = float(resp.headers.get("Retry-After", 2 ** (tentativa + 1)))
    except ValueError:
        base = 2 ** (tentativa + 1)
    return min(base, 30.0) + random.uniform(0, 2)


# ------------------------------------------------------------------ feed
FeedCallback = Callable[[str, Any], Awaitable[None]]


class EmpireFeed:
    """Socket.IO do Empire. Entrega (nome_do_evento, payload_cru) ao callback.

    Reconecta sozinho (auth fresca via metadata) quando cai ou fica mudo >75s.
    """

    def __init__(
        self,
        rest: EmpireRest,
        ao_evento: FeedCallback,
        url: str = EMPIRE_WS,
        path: str = EMPIRE_WS_PATH,
        namespace: str = EMPIRE_WS_NS,
        sio_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._rest = rest
        self._cb = ao_evento
        self._url = url
        self._path = path
        self._ns = namespace
        self._sio_factory = sio_factory or _sio_padrao
        self._sio: Any = None
        self._meta: dict = {}
        self._ultimo_evento = 0.0
        self._fechando = False
        self._task: asyncio.Task | None = None
        self.conectado = False
        self.autenticado = False

    @property
    def uid(self) -> Any:
        return (self._meta.get("user") or {}).get("id")

    async def iniciar(self) -> None:
        self._fechando = False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._supervisor(), name="empire-feed")

    async def parar(self) -> None:
        self._fechando = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self._desconectar()

    async def _supervisor(self) -> None:
        atraso = 1.0
        while not self._fechando:
            try:
                if self._sio is None or not self._sio.connected:
                    await self._conectar()
                    atraso = 1.0
                await asyncio.sleep(15)
                mudo = (time.monotonic() - self._ultimo_evento) > 75
                if self._sio is not None and self._sio.connected and mudo:
                    log.warning("Feed do Empire mudo ha mais de 75s; reconectando")
                    await self._desconectar()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "Feed do Empire falhou (%s: %s); tento de novo em %.0fs",
                    type(exc).__name__,
                    exc,
                    atraso,
                )
                await self._desconectar()
                await asyncio.sleep(atraso + random.uniform(0, 1))
                atraso = min(atraso * 2, 60.0)

    async def _conectar(self) -> None:
        self._meta = await self._rest.metadata()
        self._sio = self._sio_factory()
        self._registrar()
        await self._sio.connect(
            self._url,
            socketio_path=self._path,
            transports=["websocket"],
            namespaces=[self._ns],
            headers={"User-Agent": f"{self.uid or 0} API Bot"},
            wait_timeout=12,
        )
        self._ultimo_evento = time.monotonic()
        self.conectado = True
        log.info("Feed do Empire conectado (uid %s)", self.uid)

    async def _desconectar(self) -> None:
        self.conectado = False
        self.autenticado = False
        sio, self._sio = self._sio, None
        if sio is not None:
            with contextlib.suppress(Exception):
                await sio.disconnect()

    def _registrar(self) -> None:
        ns = self._ns
        sio = self._sio

        @sio.on("init", namespace=ns)
        async def _init(data: Any) -> None:
            if not isinstance(data, dict):
                return
            if data.get("authenticated") is False:
                user = self._meta.get("user") or {}
                await sio.emit(
                    "identify",
                    {
                        "uid": user.get("id"),
                        "model": user,
                        "authorizationToken": self._meta.get("socket_token"),
                        "signature": self._meta.get("socket_signature"),
                    },
                    namespace=ns,
                )
            elif data.get("authenticated") is True:
                self.autenticado = True
                await sio.emit("filters", {}, namespace=ns)
                log.info("Feed do Empire autenticado; recebendo leiloes")

        @sio.on("disconnect", namespace=ns)
        async def _disc() -> None:
            self.conectado = False
            self.autenticado = False
            log.warning("Feed do Empire desconectou")

        @sio.on("*", namespace=ns)
        async def _todos(evento: str, *args: Any) -> None:
            self._ultimo_evento = time.monotonic()
            if evento in FEED_IGNORAR:
                return
            payload: Any = args[0] if len(args) == 1 else list(args)
            try:
                await self._cb(str(evento), payload)
            except Exception as exc:
                log.error("Erro tratando evento %s do feed: %s", evento, exc)


def _sio_padrao() -> Any:
    import socketio

    return socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
