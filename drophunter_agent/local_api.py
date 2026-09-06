"""Ponte local da extensão: Basic auth e repasse cru pelo canal v2."""

from __future__ import annotations

import base64
import secrets

from aiohttp import web

from drophunter_agent import __version__
from drophunter_agent.canal import CanalOffline
from drophunter_agent.redact import Redator

HOST_PADRAO = "127.0.0.1"
PORTA_PADRAO = 8765
USUARIO_PADRAO = "drophunter"


def gerar_senha() -> str:
    return secrets.token_urlsafe(12)


class ApiLocal:
    def __init__(
        self,
        canal,
        *,
        host=HOST_PADRAO,
        porta=PORTA_PADRAO,
        usuario=USUARIO_PADRAO,
        senha="",
        estado=None,
    ):
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("API local só aceita loopback")
        self.canal = canal
        self.host, self.porta = host, porta
        self.usuario, self._senha = usuario, senha
        self._estado = estado or (lambda: {})
        self.redator = getattr(canal, "redator", None) or Redator()
        self.redator.adicionar(senha)
        self._runner = None
        self.app = web.Application(middlewares=[self._autenticar], client_max_size=1024 * 1024)
        self.app.router.add_get("/api/bot/status", self._status)
        self.app.router.add_route("*", "/api/extension/ping", self._encaminhar)
        self.app.router.add_route("*", "/api/sends/{rota:.*}", self._encaminhar)
        self.app.router.add_route("*", "/api/deliveries/{rota:.*}", self._encaminhar)

    @property
    def url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.porta}"

    async def iniciar(self):
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        try:
            site = web.TCPSite(self._runner, self.host, self.porta)
            await site.start()
            self.porta = site._server.sockets[0].getsockname()[1]
        except BaseException:
            await self.parar()
            raise

    async def parar(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    @web.middleware
    async def _autenticar(self, request, handler):
        if self._senha:
            try:
                tipo, credencial = request.headers.get("Authorization", "").split(" ", 1)
                bruto = base64.b64decode(credencial, validate=True).decode()
                esperado = f"{self.usuario}:{self._senha}".encode()
                autorizado = tipo.lower() == "basic" and secrets.compare_digest(
                    bruto.encode(), esperado
                )
            except (ValueError, UnicodeError):
                autorizado = False
            if not autorizado:
                return web.json_response(
                    {"erro": "nao_autorizado"},
                    status=401,
                    headers={"WWW-Authenticate": 'Basic realm="drophunter-agent"'},
                )
        return await handler(request)

    async def _status(self, request):
        return web.json_response(
            self.redator.estrutura(
                {
                    "running": True,
                    "version": __version__,
                    "agent": "drophunter-agent",
                    **self._estado(),
                }
            )
        )

    async def _encaminhar(self, request):
        try:
            corpo = await request.json() if request.can_read_body else None
            if corpo is not None and not isinstance(corpo, dict):
                return web.json_response({"erro": "json precisa ser objeto"}, status=400)
            resposta = await self.canal.extensao(
                request.method, request.path, dict(request.query), corpo, timeout_s=25
            )
            status = resposta.get("status")
            texto = resposta.get("body", "")
            content_type = resposta.get("content_type", "text/plain")
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or not 200 <= status <= 599
                or not isinstance(texto, str)
                or not isinstance(content_type, str)
                or any(c in content_type for c in "\r\n")
            ):
                return web.json_response({"erro": "resposta inválida do servidor"}, status=502)
            return web.Response(
                status=status,
                text=self.redator.corpo(texto),
                headers={"Content-Type": self.redator.texto(content_type)},
            )
        except CanalOffline:
            return web.json_response(
                {"erro": "sem canal ativo com o servidor; ofertas retidas"}, status=503
            )
        except TimeoutError:
            return web.json_response({"erro": "servidor não respondeu em 25 segundos"}, status=504)
        except (ValueError, UnicodeError):
            return web.json_response({"erro": "JSON inválido"}, status=400)
