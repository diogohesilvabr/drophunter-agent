"""Ponte local da extensão (Basic auth) e página de pagamento aberta pelo próprio app.

Duas portas no mesmo servidor de loopback, com regras diferentes:

* `/api/*` é da extensão do Chrome — Basic auth, como sempre;
* `/pagar` é do cliente — quem abre é o app, com um token de acesso de uso único que vira
  cookie de sessão. Ninguém precisa saber usuário e senha para confirmar um pagamento.
"""

from __future__ import annotations

import base64
import secrets
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from aiohttp import web

from drophunter_agent import __version__
from drophunter_agent.canal import CanalOffline
from drophunter_agent.config import gerar_senha
from drophunter_agent.pagamento import PagamentoLocalErro
from drophunter_agent.redact import Redator

__all__ = ["ApiLocal", "gerar_senha", "HOST_PADRAO", "PORTA_PADRAO", "USUARIO_PADRAO"]

HOST_PADRAO = "127.0.0.1"
PORTA_PADRAO = 8765
USUARIO_PADRAO = "drophunter"

UI = Path(__file__).parent / "ui"
COOKIE_SESSAO = "dh_pagar"
VALIDADE_ACESSO_S = 600
VALIDADE_SESSAO_S = 1800
#: quantos links de acesso podem estar vivos ao mesmo tempo (cada clique gera um)
ACESSOS_MAXIMOS = 32
#: a marca da página vem dos mesmos arquivos do app; nada de CSS/JS externo
ESTATICOS = {
    "/pagar/estilo.css": ("app.css", "text/css"),
    "/pagar/logo.png": ("assets/logo.png", "image/png"),
}
CABECALHOS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}
ABRA_PELO_APP = (
    '<p class="detalhe">Abra o DropHunter neste computador e clique em '
    "<strong>Ver e confirmar</strong> no cartão “Pagamentos pendentes”. "
    "O link tem validade curta e vale uma vez só.</p>"
)


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
        #: token de acesso -> vencimento; sessão do cookie -> vencimento (relógio monotônico)
        self._acessos: dict[str, float] = {}
        self._sessoes: dict[str, float] = {}
        self.app = web.Application(middlewares=[self._autenticar], client_max_size=1024 * 1024)
        self.app.router.add_get("/api/bot/status", self._status)
        for rota in ESTATICOS:
            self.app.router.add_get(rota, self._estatico)
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

    # ------------------------------------------------------------------- acesso
    def definir_senha(self, senha):
        """Senha nova invalida a antiga na hora — e derruba as sessões já abertas."""
        self._senha = senha or ""
        self.redator.adicionar(self._senha)
        self._acessos.clear()
        self._sessoes.clear()

    def url_pagar(self) -> str:
        """URL que o app abre: já autenticada, de uso único. Nunca vai para log."""
        return f"{self.url}/pagar?acesso={self.emitir_acesso()}"

    def emitir_acesso(self) -> str:
        self._limpar()
        while len(self._acessos) >= ACESSOS_MAXIMOS:
            self._acessos.pop(next(iter(self._acessos)))
        token = secrets.token_urlsafe(32)
        self._acessos[token] = time.monotonic() + VALIDADE_ACESSO_S
        return token

    def _limpar(self):
        agora = time.monotonic()
        for guardados in (self._acessos, self._sessoes):
            for chave in [k for k, prazo in guardados.items() if prazo <= agora]:
                guardados.pop(chave, None)

    @staticmethod
    def _achar(guardados, valor):
        if not isinstance(valor, str) or not valor.isascii() or not valor:
            return None
        return next((k for k in guardados if secrets.compare_digest(k, valor)), None)

    def _abrir_sessao(self, acesso) -> str:
        """Token de acesso vira sessão: uso único, some do lado do agente na troca."""
        self._limpar()
        achado = self._achar(self._acessos, acesso)
        if achado is None:
            return ""
        self._acessos.pop(achado, None)
        sessao = secrets.token_urlsafe(32)
        self._sessoes[sessao] = time.monotonic() + VALIDADE_SESSAO_S
        return sessao

    def _sessao_valida(self, request) -> bool:
        self._limpar()
        return self._achar(self._sessoes, request.cookies.get(COOKIE_SESSAO, "")) is not None

    def _basic_valido(self, request) -> bool:
        if not self._senha:
            return False
        try:
            tipo, credencial = request.headers.get("Authorization", "").split(" ", 1)
            bruto = base64.b64decode(credencial, validate=True).decode()
            esperado = f"{self.usuario}:{self._senha}".encode()
            return tipo.lower() == "basic" and secrets.compare_digest(bruto.encode(), esperado)
        except (ValueError, UnicodeError):
            return False

    @web.middleware
    async def _autenticar(self, request, handler):
        if request.path in ESTATICOS:  # só a marca: CSS e logo, sem nada do cliente
            return await handler(request)
        if request.path == "/pagar" or request.path.startswith("/pagar/"):
            return await self._porta_do_pagamento(request, handler)
        if self._senha and not self._basic_valido(request):
            return web.json_response(
                {"erro": "nao_autorizado"},
                status=401,
                headers={"WWW-Authenticate": 'Basic realm="drophunter-agent"'},
            )
        return await handler(request)

    async def _porta_do_pagamento(self, request, handler):
        """Sem `WWW-Authenticate`: pedir usuário e senha ao cliente é o atrito que sumiu."""
        if request.method == "GET" and request.path == "/pagar" and request.query.get("acesso"):
            sessao = self._abrir_sessao(request.query["acesso"])
            if not sessao:
                return self._pagina(
                    '<p class="detalhe erro">Este link já foi usado ou passou da validade.</p>'
                    + ABRA_PELO_APP,
                    401,
                    titulo="Link de pagamento vencido",
                )
            resposta = web.HTTPSeeOther("/pagar", headers=CABECALHOS)
            resposta.set_cookie(
                COOKIE_SESSAO,
                sessao,
                httponly=True,
                samesite="Strict",
                path="/pagar",
            )
            return resposta
        if self._sessao_valida(request) or self._basic_valido(request):
            return await handler(request)
        return self._pagina(
            '<p class="detalhe">Esta página só abre pelo aplicativo do DropHunter.</p>'
            + ABRA_PELO_APP,
            401 if self._senha else 403,
            titulo="Abra pelo DropHunter",
        )

    # ------------------------------------------------------------------ páginas
    async def _estatico(self, request):
        arquivo, tipo = ESTATICOS[request.path]
        return web.Response(body=(UI / arquivo).read_bytes(), content_type=tipo, headers=CABECALHOS)

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

    def _pagina(self, conteudo, status=200, *, titulo="Pagamento da mensalidade"):
        return web.Response(
            status=status,
            content_type="text/html",
            text=(
                '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<meta name="referrer" content="no-referrer">'
                "<title>DropHunter · Pagamento</title>"
                '<link rel="stylesheet" href="/pagar/estilo.css"></head>'
                '<body><main class="cartao"><header class="topo">'
                '<img src="/pagar/logo.png" alt="DropHunter" width="150">'
                '<span class="versao">versão ' + escape(__version__) + "</span></header>"
                "<h1>" + escape(titulo) + "</h1>" + conteudo + "</main></body></html>"
            ),
            headers=CABECALHOS,
        )

    async def _pagar(self, request):
        pagamentos = self.canal.pagamentos
        await pagamentos.expirar()
        conteudo = (
            '<p class="detalhe">Confira valor e destino antes de confirmar. '
            "Nenhum pagamento é automático: sem o seu clique, nada sai da sua conta.</p>"
        )
        if self.canal.pagamentos_bloqueados:
            conteudo += (
                '<p class="aviso">' + escape(self.canal.pagamentos_bloqueados) + "</p>"
            )
        if not pagamentos.pendentes:
            conteudo += '<p class="detalhe">Nenhum pagamento pendente. Pode fechar esta aba.</p>'
        for ident, q in pagamentos.pendentes.items():
            token = pagamentos.gerar_token(ident)
            vencimento = datetime.fromtimestamp(q["expires_at"], UTC).isoformat(timespec="minutes")
            alvo = escape(ident, quote=True)
            conteudo += (
                '<section class="pedido"><h2>Competência ' + escape(q["competencia"]) + "</h2>"
                '<dl class="resumo">'
                "<div><dt>Valor</dt><dd>" + escape(q["valor_coins"]) + " coins</dd></div>"
                "<div><dt>Destino (Steam64)</dt><dd>"
                + escape(q["destino_steam_id"])
                + "</dd></div>"
                "<div><dt>Vence em (UTC)</dt><dd>" + escape(vencimento) + "</dd></div></dl>"
                '<form method="post" action="/pagar/' + alvo + '/confirmar">'
                '<input type="hidden" name="token" value="' + token + '">'
                '<div class="botoes">'
                '<button type="submit" class="principal">Confirmar pagamento</button>'
                '<button type="submit" formaction="/pagar/'
                + alvo
                + '/cancelar">Cancelar</button></div></form></section>'
            )
        return self._pagina(conteudo)

    async def _confirmar(self, request):
        return await self._autorizar(request)

    async def _cancelar(self, request):
        return await self._autorizar(request, cancelar=True)

    async def _autorizar(self, request, *, cancelar=False):
        try:
            dados = await request.post()
            # Campo extra no formulario (pagina antiga em aba velha) e ignorado: o unico
            # dado que o consentimento usa e o token do pedido.
            resposta = await self.canal.pagamentos.autorizar(
                request.match_info["id"], dados.get("token"), cancelar=cancelar
            )
        except PagamentoLocalErro as exc:
            return self._pagina(
                '<p class="detalhe erro">' + escape(exc.motivo) + "</p>"
                '<p class="botoes"><a class="voltar" href="/pagar">Voltar</a></p>',
                exc.http,
                titulo="Não deu para autorizar",
            )
        return self._pagina(
            '<p class="detalhe">Resultado: <strong>'
            + escape(resposta["status"])
            + "</strong> · HTTP "
            + str(resposta["http"])
            + "</p><pre>"
            + escape(resposta["body"])
            + '</pre><p class="botoes"><a class="voltar" href="/pagar">Voltar</a></p>',
            titulo="Pedido concluído",
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
                # DADO, nao log: e a resposta que a extensao consome (fila de vendas,
                # link de troca do comprador). `corpo` adivinhava `token=` e apagava
                # o token do comprador — a Steam recusava com AccessDenied e a venda
                # nao saia (07/09/2026). Segredo conhecido continua sumindo aqui.
                text=self.redator.dados(texto),
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
