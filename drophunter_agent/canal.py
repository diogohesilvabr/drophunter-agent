"""Um WebSocket com licença no cabeçalho; tarefas pertencem à conexão atual."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import platform
import random
import time
import uuid

import aiohttp

from drophunter_agent import __version__
from drophunter_agent.config import Config, salvar, steam64_valido
from drophunter_agent.empire_ws import EmpireSocket
from drophunter_agent.fingerprint import fingerprint
from drophunter_agent.pagamento import Pagamentos
from drophunter_agent.proxy import Proxy

log = logging.getLogger("drophunter.canal")


class CanalOffline(ConnectionError):
    """A extensão deve reter as ofertas enquanto não houver canal."""


class FechoCanal(ConnectionError):
    def __init__(self, codigo, motivo=""):
        self.codigo, self.motivo = codigo, motivo
        super().__init__("canal fechado")


class Canal:
    def __init__(self, cfg: Config, *, proxy: Proxy, feed: EmpireSocket, sleep=None, jitter=None):
        self.cfg, self.proxy, self.feed = cfg, proxy, feed
        self.redator = proxy.redator
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.random
        self._ws = None
        self._pronto = asyncio.Event()
        self._encerrar = asyncio.Event()
        self._envio = asyncio.Lock()
        self._pedidos: set[asyncio.Task] = set()
        self._extensao: dict[str, asyncio.Future] = {}
        self._heartbeat_s = 30.0
        self._inicio = time.monotonic()
        self._hello_enviado = False
        self._teve_hello = False
        self.motivo_parada = None
        self.tenant = ""
        self.pagamentos_bloqueados = ""
        self.pagamentos = Pagamentos(self)

    @property
    def online(self) -> bool:
        return self._pronto.is_set() and self._ws is not None and not self._ws.closed

    @property
    def status(self) -> str:
        return "ok" if self.online and not self.proxy.parado else "erro"

    def pedir_encerramento(self) -> None:
        self._encerrar.set()

    async def _sem_redirect(self, session, context, params):
        raise CanalOffline("redirecionamento do canal recusado")

    async def executar(self) -> None:
        atraso = 1
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(self._sem_redirect)
        try:
            async with aiohttp.ClientSession(
                trace_configs=[trace],
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                while not self._encerrar.is_set():
                    espera = None
                    self._teve_hello = False
                    try:
                        await self._sessao(session)
                    except FechoCanal as exc:
                        if exc.codigo == 4409:
                            self.motivo_parada = "outra máquina assumiu"
                            log.warning(self.motivo_parada)
                            break
                        if exc.codigo == 4401:
                            log.error(
                                "Licença recusada: %s. Nova tentativa em 5 minutos.",
                                self.redator.texto(exc.motivo),
                            )
                            espera = 300
                        else:
                            log.warning(
                                "Canal fechado (%s): %s", exc.codigo, self.redator.texto(exc.motivo)
                            )
                    except (
                        aiohttp.ClientError,
                        ConnectionError,
                        TimeoutError,
                        ValueError,
                        TypeError,
                    ) as exc:
                        log.warning(
                            "Canal indisponível: %s", self.redator.texto(type(exc).__name__)
                        )
                    except Exception as exc:  # noqa: BLE001 - httpx/DNS/qualquer lib: o agente nunca morre por sessao
                        log.warning(
                            "Sessão falhou (%s); tentando de novo",
                            self.redator.texto(type(exc).__name__),
                        )
                    if self._encerrar.is_set():
                        break
                    if self._teve_hello:
                        atraso = 1
                    if espera is None:
                        espera = min(60, atraso * (1 + self._jitter() / 2))
                        atraso = min(60, atraso * 2)
                    await self._esperar(espera)
        finally:
            await self.feed.parar()
            await self.proxy.close()

    async def _esperar(self, segundos) -> None:
        sono = asyncio.create_task(self._sleep(segundos))
        parar = asyncio.create_task(self._encerrar.wait())
        try:
            await asyncio.wait([sono, parar], return_when=asyncio.FIRST_COMPLETED)
            if sono.done():
                sono.result()
        finally:
            for tarefa in (sono, parar):
                tarefa.cancel()
            await asyncio.gather(sono, parar, return_exceptions=True)

    async def _sessao(self, session) -> None:
        async with session.ws_connect(
            self.cfg.server_url,
            headers={"Authorization": f"Bearer {self.cfg.licenca}"},
            heartbeat=20,
            max_msg_size=2 * 1024 * 1024,
        ) as ws:
            self._ws = ws
            self._hello_enviado = False
            self.proxy.conectar()
            tarefas = [
                asyncio.create_task(self._receber()),
                asyncio.create_task(self._operar()),
                asyncio.create_task(self._encerrar.wait()),
            ]
            try:
                feitas, _ = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
                for tarefa in tarefas:
                    if tarefa in feitas:
                        tarefa.result()
            finally:
                self._pronto.clear()
                self.proxy.desconectar()
                for futura in self._extensao.values():
                    if not futura.done():
                        futura.set_exception(CanalOffline("canal desconectado"))
                for tarefa in [*tarefas, *self._pedidos]:
                    tarefa.cancel()
                await asyncio.gather(*tarefas, *self._pedidos, return_exceptions=True)
                self._pedidos.clear()
                self._ws = None

    async def _operar(self) -> None:
        uid = await self.feed.preparar()
        hello = {
            "t": "hello",
            "version": __version__,
            "os": platform.system(),
            "arch": platform.machine(),
            "fingerprint": fingerprint(),
            "empire_user_id": uid,
            "local_api_port": self.cfg.local_api_port,
            "steam_configurada": bool(self.cfg.steam_api_key),
        }
        if self.cfg.steam_id64:
            hello["steam_id64"] = self.cfg.steam_id64
        self._hello_enviado = True
        await self.enviar(hello, antes_hello=True)
        await asyncio.wait_for(self._pronto.wait(), 30)
        await self.enviar({"t": "ws_status", "estado": self.feed.estado, "motivo": ""})
        await self.feed.iniciar()
        tarefas = [asyncio.create_task(self._heartbeats()), asyncio.create_task(self._eventos())]
        try:
            feitas, _ = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
            for tarefa in feitas:
                tarefa.result()
        finally:
            for tarefa in tarefas:
                tarefa.cancel()
            await asyncio.gather(*tarefas, return_exceptions=True)

    async def enviar(self, quadro, *, antes_hello=False) -> None:
        if not self._ws or self._ws.closed or (not antes_hello and not self.online):
            raise CanalOffline("sem canal com o servidor")
        limpo = self.redator.estrutura(quadro)
        # Corpo HTTP é uma string JSON: redigir também seus campos sensíveis.
        if isinstance(limpo.get("body"), str):
            limpo["body"] = self.redator.corpo(limpo["body"])
        async with self._envio:
            await self._ws.send_str(json.dumps(limpo, ensure_ascii=False, allow_nan=False))

    async def _receber(self) -> None:
        while True:
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    quadro = json.loads(msg.data)
                except (ValueError, RecursionError):
                    raise FechoCanal(1008, "JSON inválido") from None
                if not isinstance(quadro, dict):
                    raise FechoCanal(1008, "quadro precisa ser objeto")
                await self._despachar(quadro)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                codigo = msg.data if msg.type == aiohttp.WSMsgType.CLOSE else self._ws.close_code
                raise FechoCanal(codigo, msg.extra or "")

    async def _despachar(self, quadro) -> None:
        tipo = quadro.get("t")
        if tipo == "hello_ok" and self._hello_enviado:
            self._fixar_destino(quadro.get("plataforma_steam_id"))
            if self.online:
                return
            hb = quadro.get("heartbeat_s", 30)
            if (
                isinstance(hb, bool)
                or not isinstance(hb, (int, float))
                or not math.isfinite(hb)
                or not 0 < hb <= 60
            ):
                raise FechoCanal(1008, "heartbeat inválido")
            self._heartbeat_s = hb
            self.tenant = self.redator.texto(quadro.get("tenant", ""))
            self._teve_hello = True
            self._pronto.set()
            await self.pagamentos.reenviar()
            return
        if tipo == "update_available":
            log.warning(
                "Atualização %s disponível: %s",
                self.redator.texto(quadro.get("version")),
                self.redator.texto(quadro.get("url", "")),
            )
            if quadro.get("hard") is True:
                self.motivo_parada = "atualização obrigatória"
                self.pedir_encerramento()
            return
        if not self.online:
            raise FechoCanal(1008, "aguardando hello_ok")
        if tipo == "http_request":
            if not isinstance(quadro.get("id"), str) or not quadro["id"]:
                self.proxy.recusados += 1
                return
            # Limite adicional para o servidor não esgotar a memória com fila infinita.
            if len(self._pedidos) >= 256:
                self.proxy.recusados += 1
                await self.enviar(
                    {
                        "t": "http_response",
                        "id": quadro["id"],
                        "status": 0,
                        "body": "",
                        "headers": {},
                        "elapsed_ms": 0,
                        "erro": "recusado",
                    }
                )
            else:
                tarefa = asyncio.create_task(self._http(quadro))
                self._pedidos.add(tarefa)
                tarefa.add_done_callback(self._pedido_finalizado)
        elif tipo == "pagar_fatura":
            await self.pagamentos.receber(quadro)
        elif tipo == "ext_response":
            futura = (
                self._extensao.get(quadro.get("id")) if isinstance(quadro.get("id"), str) else None
            )
            if futura and not futura.done():
                futura.set_result(self.redator.estrutura(quadro))
        elif tipo == "stop":
            self.proxy.parar()
            await self.feed.parar()
            log.warning("Servidor pediu parada: %s", self.redator.texto(quadro.get("motivo", "")))
        elif tipo == "ws_emit":
            if not await self.feed.emitir(quadro.get("event"), quadro.get("data")):
                self.proxy.recusados += 1
        elif tipo == "ws_reconnect":
            await self.feed.reconectar()
        else:
            log.debug("Quadro desconhecido ignorado: %s", self.redator.texto(tipo))

    def _fixar_destino(self, destino):
        if destino is None:
            return
        if self.cfg.plataforma_steam_id and destino != self.cfg.plataforma_steam_id:
            self.pagamentos_bloqueados = "destino divergente"
            log.error(
                "ALERTA: destino divergente; pagamentos bloqueados. Confira agent.toml e reinicie."
            )
        elif not self.cfg.plataforma_steam_id and steam64_valido(destino):
            self.cfg.plataforma_steam_id = destino
            try:
                salvar(self.cfg)
            except (OSError, ValueError):
                self.cfg.plataforma_steam_id = ""
                self.pagamentos_bloqueados = "falha ao gravar destino"
                log.error("Pagamentos bloqueados: falha ao gravar destino local")
            else:
                log.info("destino de pagamento fixado: %s", self.redator.texto(destino))

    def _pedido_finalizado(self, tarefa) -> None:
        self._pedidos.discard(tarefa)
        if not tarefa.cancelled() and tarefa.exception():
            log.warning(
                "Resposta HTTP não entregue: %s",
                self.redator.texto(type(tarefa.exception()).__name__),
            )

    async def _http(self, quadro) -> None:
        await self.enviar(await self.proxy.executar(quadro))

    async def _heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            await self.enviar(
                {
                    "t": "heartbeat",
                    "status": self.status,
                    "ws_empire": "conectado" if self.feed.conectado else "desconectado",
                    "uptime_s": int(time.monotonic() - self._inicio),
                    "recusados": self.proxy.recusados,
                }
            )

    async def _eventos(self) -> None:
        while True:
            quadro = await self.feed.buffer.proximo()
            if not self.proxy.parado:
                await self.enviar(quadro)
            self.feed.buffer.confirmar(quadro)

    async def extensao(self, method, path, query, corpo, *, timeout_s=25) -> dict:
        if not self.online or self.proxy.parado:
            raise CanalOffline("sem canal ativo com o servidor; ofertas retidas")
        if len(self._extensao) >= 256:
            raise CanalOffline("canal ocupado; tente novamente")
        ident = uuid.uuid4().hex
        futura = asyncio.get_running_loop().create_future()
        self._extensao[ident] = futura
        try:
            await self.enviar(
                {
                    "t": "ext_request",
                    "id": ident,
                    "method": method,
                    "path": path,
                    "query": query,
                    "json": corpo,
                }
            )
            return await asyncio.wait_for(futura, timeout_s)
        finally:
            self._extensao.pop(ident, None)
            if not futura.done():
                futura.cancel()
