"""Socket.io do Empire: handshake local e eventos crus, sem interpretar leilões."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

import socketio

from drophunter_agent.redact import REDATOR, Redator

log = logging.getLogger("drophunter.socket")


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
    ):
        self._metadata = metadata
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
        if not self._pausado:
            self.buffer.adicionar(
                {"t": "ws_status", "estado": estado, "motivo": self.redator.texto(motivo)}
            )

    def _evento(self, evento: str, dados: Any) -> None:
        if not self._pausado:
            self.redator.registrar_credenciais(dados)
            self.buffer.adicionar(
                self.redator.estrutura(
                    {"t": "ws_event", "event": evento, "data": dados, "ts": time.time()}
                )
            )

    def _registrar(self) -> None:
        ns = "/trade"

        @self._sio.on("connect", namespace=ns)
        async def conectado(*args):
            self._estado("autenticando")

        @self._sio.on("disconnect", namespace=ns)
        async def desconectado(*args):
            self._estado("desconectado", args[0] if args else "")

        @self._sio.on("connect_error", namespace=ns)
        async def erro(*args):
            self._estado("desconectado", "falha na conexão")

        @self._sio.on("init", namespace=ns)
        async def iniciar(dados):
            self._evento("init", dados)
            if not isinstance(dados, dict) or self._pausado:
                return
            if dados.get("authenticated") is False:
                if self._pode_autenticar():
                    self._auth = await self._metadata()
                    await self.preparar()
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

        @self._sio.on("*", namespace=ns)
        async def evento(event, *args):
            self._evento(event, args[0] if len(args) == 1 else list(args))

    async def iniciar(self) -> None:
        self._pausado = False
        if self._tarefa is None or self._tarefa.done():
            self._tarefa = asyncio.create_task(self._loop(), name="socket-empire")

    async def _loop(self) -> None:
        atraso = 1
        while True:
            try:
                if self._pode_autenticar() and not self._pausado:
                    uid = await self.preparar()
                    await self._sio.connect(
                        "wss://trade.csgoempire.com",
                        socketio_path="/s/",
                        namespaces=["/trade"],
                        transports=["websocket"],
                        headers={"User-Agent": f"{uid} API Bot"},
                    )
                    atraso = 1
                    await self._sio.wait()
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
