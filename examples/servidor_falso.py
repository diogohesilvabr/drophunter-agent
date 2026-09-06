"""Demonstração local do protocolo v2. Não lê agent.toml nem acessa APIs reais."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging

import httpx
from aiohttp import web

from drophunter_agent.canal import Canal
from drophunter_agent.config import Config
from drophunter_agent.empire_ws import EmpireSocket
from drophunter_agent.local_api import ApiLocal
from drophunter_agent.logs import configurar_log
from drophunter_agent.proxy import Proxy

log = logging.getLogger("demo")


class SocketFalso:
    def __init__(self, **kwargs):
        self.handlers = {}
        self.fim = asyncio.Event()

    def on(self, evento, namespace=None):
        def registrar(fn):
            self.handlers[evento] = fn
            return fn

        return registrar

    async def connect(self, *args, **kwargs):
        self.fim.clear()
        await self.handlers["connect"]()
        await self.handlers["init"]({"authenticated": False})
        await self.handlers["init"]({"authenticated": True})
        await self.handlers["*"]("new_item", [{"id": 123, "market_name": "Item de demonstração"}])

    async def emit(self, *args, **kwargs):
        pass

    async def wait(self):
        await self.fim.wait()

    async def disconnect(self):
        self.fim.set()


def http_falso(request):
    if request.url.path.endswith("/metadata/socket"):
        return httpx.Response(
            200,
            json={
                "user": {"id": 42},
                "socket_token": "token-demo-local",
                "socket_signature": "assinatura-demo-local",
            },
        )
    return httpx.Response(200, json={"items": [], "demo": True})


async def executar(duracao: float):
    configurar_log()

    async def gateway(request):
        if request.headers.get("Authorization") != "Bearer lic_demo_sem_validade":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        log.info("hello recebido: versão %s, usuário %s", hello["version"], hello["empire_user_id"])
        await ws.send_json({"t": "hello_ok", "tenant": "demo", "heartbeat_s": 2})
        for ident, rota in (("permitido", "/trading/items"), ("bloqueado", "/user/tip")):
            await ws.send_json(
                {"t": "http_request", "id": ident, "alvo": "empire", "method": "GET", "path": rota}
            )
        async for mensagem in ws:
            if mensagem.type != web.WSMsgType.TEXT:
                continue
            q = json.loads(mensagem.data)
            if q["t"] == "ext_request":
                await ws.send_json(
                    {
                        "t": "ext_response",
                        "id": q["id"],
                        "status": 200,
                        "body": '{"demo":true,"sends":[],"cancels":[]}',
                        "content_type": "application/json",
                    }
                )
            elif q["t"] == "http_response":
                log.info("HTTP %s: status=%s erro=%s", q["id"], q["status"], q["erro"])
            else:
                log.info("Recebido %s %s", q["t"], q.get("event", ""))
        return ws

    app = web.Application()
    app.router.add_get("/api/agent/ws", gateway)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    porta = site._server.sockets[0].getsockname()[1]
    cfg = Config(
        licenca="lic_demo_sem_validade",
        empire_api_key="chave-demo-sem-validade",
        server_url=f"ws://127.0.0.1:{porta}/api/agent/ws",
    )
    proxy = Proxy(cfg, transport=httpx.MockTransport(http_falso))
    feed = EmpireSocket(
        proxy.metadata,
        redator=proxy.redator,
        sio_factory=SocketFalso,
        pode_autenticar=lambda: not proxy.parado,
    )
    canal = Canal(cfg, proxy=proxy, feed=feed)
    api = ApiLocal(canal, porta=0, usuario="demo", senha="demo")
    await api.iniciar()
    cfg.local_api_port = api.porta
    log.info("API local: %s — Basic auth de demonstração: demo / demo", api.url)
    log.info("Somente fakes em memória e loopback. Ctrl+C encerra.")
    tarefa = asyncio.create_task(canal.executar())
    try:
        if duracao > 0:
            await asyncio.sleep(duracao)
        else:
            await tarefa
    finally:
        canal.pedir_encerramento()
        await tarefa
        await api.parar()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duracao", type=float, default=0, help="segundos; 0 espera Ctrl+C")
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(executar(args.duracao))
