"""Socket.io do Empire: handshake local e eventos crus, sem interpretar leilões."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

import socketio

from drophunter_agent import sessoes
from drophunter_agent.redact import REDATOR, Redator

log = logging.getLogger("drophunter.socket")

#: 4.0.8: esperas entre tentativas do metadata no ``init`` (``Retry-After`` manda, com teto)
ESPERAS_METADATA_S = (5, 15, 30, 60)
TETO_ESPERA_S = 120.0
#: vigia do socket: conectado sem ``identify`` confirmado / sem ``new_item`` = refaz do zero
VIGIA_S = 15.0
IDENTIFY_LIMITE_S = 180.0
SEM_ITEM_LIMITE_S = 300.0


def _retry_after(exc: Exception) -> float | None:
    resposta = getattr(exc, "response", None)
    try:
        return float(resposta.headers["retry-after"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _motivo(exc: Exception) -> str:
    resposta = getattr(exc, "response", None)
    status = getattr(resposta, "status_code", None)
    return f"HTTP {status}" if status else type(exc).__name__


class BufferEventos:
    def __init__(self, *, relogio=time.monotonic, max_eventos=20000, max_bytes=16 * 1024 * 1024):
        self._relogio = relogio
        self._max_eventos = max_eventos
        self._max_bytes = max_bytes
        self._fila: deque = deque()
        self._bytes = 0
        self.disponivel = asyncio.Event()

    def _podar(self) -> None:
        corte = self._relogio() - 60
        while self._fila and (
            self._fila[0][0] < corte
            or len(self._fila) > self._max_eventos
            or self._bytes > self._max_bytes
        ):
            self._bytes -= self._fila.popleft()[2]
        if not self._fila:
            self.disponivel.clear()

    @property
    def pendentes(self) -> list[dict]:
        self._podar()
        return [q for _, q, _ in self._fila]

    def adicionar(self, quadro: dict) -> None:
        tamanho = len(json.dumps(quadro, ensure_ascii=False).encode())
        self._fila.append((self._relogio(), quadro, tamanho))
        self._bytes += tamanho
        self.disponivel.set()
        self._podar()

    async def proximo(self) -> dict:
        while True:
            self._podar()
            if self._fila:
                return self._fila[0][1]
            await self.disponivel.wait()

    def confirmar(self, quadro: dict) -> None:
        if self._fila and self._fila[0][1] is quadro:
            self._bytes -= self._fila.popleft()[2]
        self._podar()

    def limpar(self) -> None:
        self._fila.clear()
        self._bytes = 0
        self.disponivel.clear()


class EmpireSocket:
    def __init__(
        self,
        metadata,
        *,
        redator: Redator,
        sio_factory=None,
        pode_autenticar=lambda: True,
        buffer=None,
        relogio=time.monotonic,
        dormir=asyncio.sleep,
        vigia_s=VIGIA_S,
        identify_limite_s=IDENTIFY_LIMITE_S,
        sem_item_limite_s=SEM_ITEM_LIMITE_S,
    ):
        self._metadata = metadata
        self._relogio = relogio
        self._dormir = dormir
        self._vigia_s = vigia_s
        self._identify_limite_s = identify_limite_s
        self._sem_item_limite_s = sem_item_limite_s
        #: 4.0.8: marcos do vigia (``_relogio``) e o pedido de refazer a conexão
        self._conectou_em: float | None = None
        self._identificou_em: float | None = None
        self._ultimo_item_novo = 0.0
        self._derrubar: str | None = None
        self.redator = redator
        self._pode_autenticar = pode_autenticar
        self.buffer = buffer or BufferEventos()
        self._sio = (sio_factory or socketio.AsyncClient)(
            reconnection=False, logger=False, engineio_logger=False
        )
        self._auth: dict | None = None
        self._tarefa: asyncio.Task | None = None
        self._pausado = False
        self.estado = "desconectado"
        #: sessão aiohttp do socket.io (4.0.7): uma pelo processo, entregue ao
        #: engineio no lugar da que ele criava (e fechava) a cada reconexão
        self._http = None
        self._registrar()

    @property
    def conectado(self) -> bool:
        return self.estado == "conectado"

    async def preparar(self) -> int:
        if self._auth is None:
            self._auth = await self._metadata()
        user = self._auth.get("user")
        uid = user.get("id") if isinstance(user, dict) else None
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            raise ValueError("metadata sem identificação numérica do usuário")
        if not all(
            isinstance(self._auth.get(k), str) and self._auth[k]
            for k in ("socket_token", "socket_signature")
        ):
            raise ValueError("metadata sem autenticação do socket")
        self.redator.registrar_credenciais(self._auth)
        REDATOR.registrar_credenciais(self._auth)
        return uid

    def _estado(self, estado: str, motivo="") -> None:
        self.estado = estado
        if estado == "conectado":
            self._identificou_em = self._relogio()
        if not self._pausado:
            self.buffer.adicionar(
                {"t": "ws_status", "estado": estado, "motivo": self.redator.texto(motivo)}
            )

    def _evento(self, evento: str, dados: Any) -> None:
        if evento == "new_item":
            self._ultimo_item_novo = self._relogio()
        if not self._pausado:
            self.redator.registrar_credenciais(dados)
            self.buffer.adicionar(
                self.redator.estrutura(
                    {"t": "ws_event", "event": evento, "data": dados, "ts": time.time()}
                )
            )

    def _on(self, evento: str):
        """``@self._sio.on`` que não deixa exceção escapar (4.0.8).

        O socket.io roda cada handler numa task solta: exceção ali vira só
        "Task exception was never retrieved" e o handler morre calado. Foi assim que um
        429 no metadata deixou o socket 9h30 conectado sem ``identify`` (24/09/2026).
        """

        def registrar(fn):
            async def blindado(*args):
                try:
                    await fn(*args)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - o vigia refaz a conexão se preciso
                    log.warning(
                        "Evento %s do socket Empire falhou: %s",
                        evento,
                        self.redator.texto(_motivo(exc)),
                    )

            return self._sio.on(evento, namespace="/trade")(blindado)

        return registrar

    async def _autenticar(self) -> bool:
        """Metadata + validação com espera crescente; False = esgotou (conexão será refeita).

        Loga no começo do episódio e no fim, nunca por tentativa."""
        esperas = list(ESPERAS_METADATA_S)
        falhas = 0
        while True:
            try:
                self._auth = await self._metadata()
                await self.preparar()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._auth = None
                falhas += 1
                motivo = self.redator.texto(_motivo(exc))
                if not esperas or self._pausado:
                    log.error(
                        "Autenticação do socket Empire esgotou (%d tentativas, %s); "
                        "refazendo a conexão (metadata)",
                        falhas,
                        motivo,
                    )
                    return False
                espera = min(_retry_after(exc) or esperas[0], TETO_ESPERA_S)
                esperas.pop(0)
                if falhas == 1:
                    log.warning(
                        "Metadata do socket Empire falhou (%s); tentando de novo com espera "
                        "crescente, 1a em %ss",
                        motivo,
                        espera,
                    )
                await self._dormir(espera)
                continue
            if falhas:
                log.info("Socket Empire autenticado após %d falha(s)", falhas)
            return True

    def _registrar(self) -> None:
        ns = "/trade"

        @self._on("connect")
        async def conectado(*args):
            self._conectou_em = self._relogio()
            self._identificou_em = None
            self._estado("autenticando")

        @self._on("disconnect")
        async def desconectado(*args):
            self._estado("desconectado", args[0] if args else "")

        @self._on("connect_error")
        async def erro(*args):
            self._estado("desconectado", "falha na conexão")

        @self._on("init")
        async def iniciar(dados):
            self._evento("init", dados)
            if not isinstance(dados, dict) or self._pausado:
                return
            if dados.get("authenticated") is False:
                if self._pode_autenticar() and not await self._autenticar():
                    self._derrubar = "autenticação esgotada"
                    return
                if self._auth:
                    await self._sio.emit(
                        "identify",
                        {
                            "uid": self._auth["user"]["id"],
                            "model": self._auth["user"],
                            "authorizationToken": self._auth["socket_token"],
                            "signature": self._auth["socket_signature"],
                        },
                        namespace=ns,
                    )
            elif dados.get("authenticated") is True:
                await self._sio.emit("filters", {}, namespace=ns)
                self._estado("conectado")

        @self._on("*")
        async def evento(event, *args):
            self._evento(event, args[0] if len(args) == 1 else list(args))

    def _motivo_para_refazer(self) -> str | None:
        if self._derrubar:
            return self._derrubar
        agora = self._relogio()
        if self.estado != "conectado":
            if (
                self._conectou_em is not None
                and agora - self._conectou_em > self._identify_limite_s
            ):
                return f"sem identify confirmado há {int(agora - self._conectou_em)} s"
            return None
        desde = max(self._ultimo_item_novo, self._identificou_em or 0.0)
        if agora - desde > self._sem_item_limite_s:
            return f"sem new_item há {int(agora - desde)} s"
        return None

    async def _vigiar(self) -> None:
        """Socket conectado mas cego (sem ``identify`` ou sem ``new_item``): derruba para
        o ``_loop`` refazer tudo do zero (metadata novo)."""
        while True:
            await asyncio.sleep(self._vigia_s)
            motivo = self._motivo_para_refazer()
            if motivo:
                log.warning("Socket Empire cego (%s); refazendo a conexão", motivo)
                self._auth = None
                await self._sio.disconnect()
                return

    async def iniciar(self) -> None:
        self._pausado = False
        if self._tarefa is None or self._tarefa.done():
            self._tarefa = asyncio.create_task(self._loop(), name="socket-empire")

    def _garantir_sessao(self) -> None:
        """Entrega a sessão única ao cliente engineio real (``sio.eio``); um cliente
        falso (testes) não tem ``eio`` e segue sem sessão."""
        eio = getattr(self._sio, "eio", None)
        if eio is None or not hasattr(eio, "external_http"):
            return
        if self._http is None or self._http.closed:
            self._http = sessoes.sessao(timeout_s=30)
        if eio.http is not self._http:
            eio.http = self._http
            eio.external_http = True

    async def fechar(self) -> None:
        """Encerramento do processo: para o socket e fecha a sessão HTTP."""
        await self.parar()
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

    async def _loop(self) -> None:
        atraso = 1
        while True:
            try:
                if self._pode_autenticar() and not self._pausado:
                    self._garantir_sessao()
                    uid = await self.preparar()
                    await self._sio.connect(
                        "wss://trade.csgoempire.com",
                        socketio_path="/s/",
                        namespaces=["/trade"],
                        transports=["websocket"],
                        headers={"User-Agent": f"{uid} API Bot"},
                    )
                    atraso = 1
                    self._derrubar = None
                    vigia = asyncio.create_task(self._vigiar(), name="vigia-socket-empire")
                    try:
                        await self._sio.wait()
                    finally:
                        vigia.cancel()
                        await asyncio.gather(vigia, return_exceptions=True)
                else:
                    self.buffer._podar()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._estado("desconectado", type(exc).__name__)
                log.warning(
                    "Socket Empire indisponível: %s", self.redator.texto(type(exc).__name__)
                )
                await self._sio.disconnect()
                self._auth = None
            await asyncio.sleep(atraso)
            atraso = min(atraso * 2, 60)

    async def emitir(self, evento: str, dados: Any) -> bool:
        # O protocolo só documenta filters. Identify pertence ao handshake local.
        if (
            evento != "filters"
            or not isinstance(dados, dict)
            or self._pausado
            or not self.conectado
            or not self._pode_autenticar()
        ):
            return False
        await self._sio.emit(evento, dados, namespace="/trade")
        return True

    async def reconectar(self) -> None:
        if not self._pode_autenticar() or self._pausado:
            return
        await self.parar()
        self._auth = None
        await self.iniciar()

    async def parar(self) -> None:
        self._pausado = True
        if self._tarefa:
            self._tarefa.cancel()
            await asyncio.gather(self._tarefa, return_exceptions=True)
            self._tarefa = None
        await self._sio.disconnect()
        self.estado = "desconectado"
        self.buffer.limpar()
