"""Ponte local da extensão: Basic auth e repasse cru pelo canal v2."""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime
from html import escape

from aiohttp import web

from drophunter_agent import __version__
from drophunter_agent.canal import CanalOffline
from drophunter_agent.pagamento import PagamentoLocalErro
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
        self.app.router.add_get("/pagar", self._pagar)
        self.app.router.add_post("/pagar/{id}/confirmar", self._confirmar)
        self.app.router.add_post("/pagar/{id}/cancelar", self._cancelar)
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
        if not self._senha and (request.path == "/pagar" or request.path.startswith("/pagar/")):
            return self._pagina("Configure api_local_senha no agent.toml e reinicie o agente.", 403)
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
                    **self._status_pagamento(),
                }
            )
        )

    def _status_pagamento(self):
        cfg = getattr(self.canal, "cfg", None)
        if cfg is None:
            return {}
        return {
            "plataforma_steam_id": cfg.plataforma_steam_id,
            "pagamento_teto_coins": str(cfg.pagamento_teto_coins),
            "pagamentos_bloqueados": self.canal.pagamentos_bloqueados,
        }

    def _pagina(self, conteudo, status=200):
        return web.Response(
            status=status,
            content_type="text/html",
            text=(
                '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>Pagamento DropHunter</title><body><h1>Pagamento DropHunter</h1>"
                + conteudo
                + "</body></html>"
            ),
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; form-action 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'"
                ),
            },
        )

    async def _pagar(self, request):
        pagamentos = self.canal.pagamentos
        await pagamentos.expirar()
        conteudo = (
            "<p>Confira valor e destino antes de confirmar. Nenhum pagamento é automático.</p>"
        )
        if self.canal.pagamentos_bloqueados:
            conteudo += "<p>" + escape(self.canal.pagamentos_bloqueados) + "</p>"
        if not pagamentos.pendentes:
            conteudo += "<p>Nenhum pagamento pendente.</p>"
        for ident, q in pagamentos.pendentes.items():
            token = pagamentos.gerar_token(ident)
            vencimento = datetime.fromtimestamp(q["expires_at"], UTC).isoformat()
            conteudo += (
                "<section><h2>Competência " + escape(q["competencia"]) + "</h2>"
                "<p>Valor: " + escape(q["valor_coins"]) + " coins</p>"
                "<p>Destino: " + escape(q["destino_steam_id"]) + "</p>"
                "<p>Vencimento (UTC): " + escape(vencimento) + "</p>"
                '<form method="post" action="/pagar/' + escape(ident, quote=True) + '/confirmar">'
                '<input type="hidden" name="token" value="' + token + '">'
                '<label>Código 2FA (opcional): <input type="password" name="codigo_2fa" '
                'inputmode="numeric" autocomplete="off" maxlength="8"></label>'
                "<p>Só preencha se o Empire recusar pedindo 2FA. Nesse caso, solicite "
                "um novo pedido no site e confirme aqui com o código.</p>"
                '<button type="submit">Confirmar pagamento</button>'
                '<button type="submit" formaction="/pagar/'
                + escape(ident, quote=True)
                + '/cancelar">Cancelar</button></form></section>'
            )
        return self._pagina(conteudo)

    async def _confirmar(self, request):
        return await self._autorizar(request)

    async def _cancelar(self, request):
        return await self._autorizar(request, cancelar=True)

    async def _autorizar(self, request, *, cancelar=False):
        try:
            dados = await request.post()
            resposta = await self.canal.pagamentos.autorizar(
                request.match_info["id"],
                dados.get("token"),
                cancelar=cancelar,
                codigo_2fa=dados.get("codigo_2fa", ""),
            )
        except PagamentoLocalErro as exc:
            return self._pagina(escape(exc.motivo), exc.http)
        return self._pagina(
            "<p>Resultado: " + escape(resposta["status"]) + "</p>"
            "<p>HTTP: "
            + str(resposta["http"])
            + "</p><pre>"
            + escape(resposta["body"])
            + '</pre><a href="/pagar">Voltar</a>'
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
