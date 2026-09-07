"""Páginas do app (`/app/*`) num servidor de loopback com token de sessão.

Quem coleta as chaves na instalação é o instalador (frente W2); aqui o cliente só vê o
estado da conexão e ALTERA as chaves. O token é gerado a cada abertura da janela: outro
processo do mesmo PC não lê nem grava chave por estas rotas.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import time
import webbrowser
from dataclasses import replace
from pathlib import Path

import httpx
from aiohttp import web

from drophunter_agent import __version__
from drophunter_agent.config import (
    API_LOCAL_USUARIO_PADRAO,
    Config,
    ConfigInvalida,
    carregar,
    gerar_senha,
    salvar,
    steam64_valido,
)
from drophunter_agent.redact import REDATOR

log = logging.getLogger("drophunter.janela")

UI = Path(__file__).parent / "ui"
CAMPOS = ("licenca", "empire_api_key", "steam_api_key", "steam_id64")
PAINEL = "https://www.drophunter.com.br/painel/"
#: confirmado em docs.csgoempire.com/reference/getting-started-with-your-api (06/09/2026)
EMPIRE_APIKEY = "https://csgoempire.com/trading/apikey"
STEAM_APIKEY = "https://steamcommunity.com/dev/apikey"
LINKS = {"painel": PAINEL, "licenca": PAINEL, "empire": EMPIRE_APIKEY, "steam": STEAM_APIKEY}
EMPIRE_METADATA = "https://csgoempire.com/api/v2/metadata/socket"
STEAM_PERFIL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
ESTATICOS = {
    "app.css": ("app.css", "text/css"),
    "app.js": ("app.js", "text/javascript"),
    "assets/logo.png": ("assets/logo.png", "image/png"),
}
CABECALHOS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
}


def mascarar_fim(valor: str, mostrar: int = 4) -> str:
    """Só os últimos 4 vão pra tela; o resto nunca sai do agent.toml."""
    valor = (valor or "").strip()
    if not valor:
        return ""
    return "•" * 8 + valor[-mostrar:] if len(valor) > mostrar else "•" * 8


def formatos_validos(dados) -> dict[str, bool]:
    def valor(nome):
        v = dados.get(nome, "")
        return v.strip() if isinstance(v, str) else ""

    return {
        "licenca": bool(re.fullmatch(r"lic_[A-Za-z0-9]{40}", valor("licenca"))),
        # O Empire não publica tamanho fixo: token ASCII imprimível, sem espaço nem quebra.
        "empire_api_key": bool(re.fullmatch(r"[!-~]{8,4096}", valor("empire_api_key"))),
        "steam_api_key": bool(re.fullmatch(r"[A-Fa-f0-9]{32}", valor("steam_api_key"))),
        "steam_id64": steam64_valido(valor("steam_id64")),
    }


ROTULOS = {
    "licenca": "licença",
    "empire_api_key": "chave do Empire",
    "steam_api_key": "chave da Steam",
    "steam_id64": "Steam64",
}


def estado_do_canal(canal, *, agora=None) -> dict:
    """Estado que a janela mostra e a bandeja pinta. Função pura: só lê o canal."""
    agora = agora if agora is not None else time.time()
    vazio = {
        "estado": "desconectado",
        "rotulo": "Desconectado",
        "motivo": "",
        "conta": "",
        "heartbeat_s": None,
        "tentativa_s": None,
        "pagamentos": 0,
    }
    if canal is None:
        return vazio
    recusada = getattr(canal, "licenca_recusada", "")
    parado = getattr(canal, "motivo_parada", None)
    heartbeat = getattr(canal, "ultimo_heartbeat", 0.0) or 0.0
    dados = dict(
        vazio,
        conta=getattr(canal, "tenant", "") or "",
        motivo=REDATOR.texto(getattr(canal, "ultimo_erro", "") or ""),
        heartbeat_s=int(max(0, agora - heartbeat)) if heartbeat else None,
        pagamentos=len(getattr(getattr(canal, "pagamentos", None), "pendentes", ()) or ()),
    )
    if recusada:
        return dict(
            dados,
            estado="recusado",
            rotulo="Licença recusada",
            motivo=REDATOR.texto(recusada),
        )
    if canal.online and canal.status == "ok":
        return dict(dados, estado="conectado", rotulo="Conectado", motivo="", tentativa_s=None)
    if parado:
        return dict(dados, estado="recusado", rotulo="Parado", motivo=REDATOR.texto(str(parado)))
    retomar = getattr(canal, "retomar_em", 0.0) or 0.0
    return dict(dados, tentativa_s=int(max(0, retomar - agora)) if retomar > agora else None)


class Janela:
    """Servidor das páginas do app. Só loopback, só com o token da sessão atual."""

    def __init__(
        self,
        caminho,
        *,
        canal=None,
        ao_salvar=None,
        ao_sair=None,
        ao_minimizar=None,
        url_pagamentos=None,
        pagamentos_ativos=None,
        erro=None,
        transport=None,
        testar_licenca=None,
        abrir=None,
        host="127.0.0.1",
    ):
        self.caminho = Path(caminho)
        self._canal = canal or (lambda: None)
        self.ao_salvar = ao_salvar
        self.ao_sair = ao_sair
        self.ao_minimizar = ao_minimizar
        self.url_pagamentos = url_pagamentos or (lambda: "")
        # Predicado separado do link: o estado é lido a cada 2 s e não pode gastar token.
        self.pagamentos_ativos = pagamentos_ativos or (lambda: bool(self.url_pagamentos()))
        self._erro = erro or (lambda: "")
        self.transport = transport
        self._testar_licenca = testar_licenca
        self._abrir = abrir or webbrowser.open
        self.host = host
        self.porta = 0
        self._runner = None
        self._testes = asyncio.Semaphore(1)
        self._gravacao = asyncio.Lock()
        self.renovar()
        self.aplicacao = web.Application(
            middlewares=[self._autorizar], client_max_size=32 * 1024
        )
        self.aplicacao.router.add_get("/app/estado", self.rota_estado)
        self.aplicacao.router.add_post("/app/testar/{servico}", self.rota_testar)
        self.aplicacao.router.add_post("/app/salvar", self.rota_salvar)
        self.aplicacao.router.add_post("/app/autostart", self.rota_autostart)
        self.aplicacao.router.add_post("/app/senha-local", self.rota_senha_local)
        self.aplicacao.router.add_post("/app/abrir", self.rota_abrir)
        self.aplicacao.router.add_post("/app/janela", self.rota_janela)
        self.aplicacao.router.add_get("/app/{recurso:.*}", self.rota_pagina)

    # ------------------------------------------------------------------ sessão
    def renovar(self) -> str:
        self.token = secrets.token_urlsafe(32)
        REDATOR.adicionar(self.token)
        return self.token

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.porta}"

    @property
    def url_com_token(self) -> str:
        return f"{self.url}/app/?token={self.token}"

    async def iniciar(self) -> None:
        self._runner = web.AppRunner(self.aplicacao, access_log=None)
        await self._runner.setup()
        try:
            site = web.TCPSite(self._runner, self.host, self.porta)
            await site.start()
            self.porta = site._server.sockets[0].getsockname()[1]
        except BaseException:
            await self.parar()
            raise

    async def parar(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    def autorizado(self, request) -> bool:
        try:
            remoto = ipaddress.ip_address((request.remote or "").strip("[]"))
            anfitriao = (request.host or "").rsplit(":", 1)[0].strip("[]")
            origem = request.headers.get("Origin")
            enviado = request.query.get("token") or request.headers.get("X-DropHunter-Token", "")
            return (
                remoto.is_loopback
                and anfitriao in {"127.0.0.1", "::1", "localhost"}
                and secrets.compare_digest(enviado, self.token)
                and (not origem or origem == f"http://{request.host}")
            )
        except (ValueError, TypeError):
            return False

    @web.middleware
    async def _autorizar(self, request, handler):
        if not self.autorizado(request):
            return web.json_response(
                {"ok": False, "mensagem": "Sessão inválida. Reabra a janela pela bandeja."},
                status=403,
                headers=CABECALHOS,
            )
        return await handler(request)

    # ------------------------------------------------------------------ páginas
    async def rota_pagina(self, request):
        recurso = request.match_info["recurso"]
        if recurso in ESTATICOS:
            arquivo, tipo = ESTATICOS[recurso]
            return web.Response(
                body=(UI / arquivo).read_bytes(), content_type=tipo, headers=CABECALHOS
            )
        if recurso not in ("", "configuracoes"):
            raise web.HTTPNotFound()
        html = (UI / "index.html").read_text(encoding="utf-8")
        return web.Response(
            text=html.replace("__TOKEN__", self.token).replace("__VERSAO__", __version__),
            content_type="text/html",
            headers=CABECALHOS,
        )

    # ------------------------------------------------------------------- estado
    def config_atual(self):
        try:
            return carregar(self.caminho) if self.caminho.exists() else None
        except (ConfigInvalida, OSError):
            return None

    def instantaneo(self) -> dict:
        from drophunter_agent.bandeja import autostart_ativo, autostart_disponivel

        cfg = self.config_atual()
        estado = estado_do_canal(self._canal())
        erro = self._erro()
        if erro and estado["estado"] != "conectado":
            estado = dict(estado, motivo=REDATOR.texto(erro))
        if cfg is None:
            estado = dict(
                estado,
                estado="sem_config",
                rotulo="Sem configuração",
                motivo="Informe suas chaves para conectar.",
            )
        # `campos` entra DEPOIS do redator: ele apaga por nome de chave (licenca,
        # empire_api_key…) e engoliria a máscara de quatro dígitos que a tela precisa.
        payload = REDATOR.estrutura(
            {
                **estado,
                "versao": __version__,
                "painel": PAINEL,
                "autostart": autostart_ativo(),
                "autostart_disponivel": autostart_disponivel(),
                "pagamentos_url": bool(self.pagamentos_ativos()),
            }
        )
        payload["campos"] = {
            campo: mascarar_fim(getattr(cfg, campo, "") if cfg else "") for campo in CAMPOS
        }
        # A extensão do Chrome precisa VER usuário/senha da API local; só a máscara sai daqui
        # (o valor inteiro sai por /app/senha-local, sob o token da janela).
        ligada = bool(cfg and int(cfg.local_api_port) > 0)
        payload["extensao"] = {
            "url": cfg.api_local_url if ligada else "",
            "usuario": (cfg.api_local_usuario if ligada else "") or "",
            "senha": mascarar_fim(cfg.api_local_senha) if ligada else "",
        }
        return payload

    async def rota_estado(self, request):
        return web.json_response(self.instantaneo(), headers=CABECALHOS)

    # -------------------------------------------------------------------- dados
    async def _dados(self, request) -> dict:
        if request.content_length and request.content_length > 32 * 1024:
            raise ValueError("corpo grande demais")
        dados = await request.json()
        if not isinstance(dados, dict):
            raise ValueError("json precisa ser objeto")
        for campo in ("licenca", "empire_api_key", "steam_api_key"):
            if isinstance(dados.get(campo), str) and dados[campo].strip():
                REDATOR.adicionar(dados[campo].strip())
        return dados

    def _mesclar(self, dados) -> dict:
        """Campo em branco = 'não mexi nele': vale o valor já gravado (a tela só vê a máscara)."""
        cfg = self.config_atual()
        valores = {}
        for campo in CAMPOS:
            bruto = dados.get(campo)
            texto = bruto.strip() if isinstance(bruto, str) else ""
            valores[campo] = texto or (getattr(cfg, campo, "") if cfg else "")
        return valores

    # ------------------------------------------------------------------- testar
    async def rota_testar(self, request):
        try:
            dados = await self._dados(request)
            async with self._testes:
                resultado = await self.testar(request.match_info["servico"], dados)
            return web.json_response(resultado, headers=CABECALHOS)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response(
                {"ok": False, "mensagem": "Confira os campos informados."},
                status=400,
                headers=CABECALHOS,
            )

    async def testar(self, servico, dados) -> dict:
        exigidos = {
            "licenca": ["licenca"],
            "empire": ["empire_api_key"],
            "steam": ["steam_api_key", "steam_id64"],
        }
        if servico not in exigidos:
            return {"ok": False, "mensagem": "Não sei testar isso."}
        valores = self._mesclar(dados)
        for campo in ("licenca", "empire_api_key", "steam_api_key"):
            if valores[campo]:
                REDATOR.adicionar(valores[campo])
        validos = formatos_validos(valores)
        faltando = [ROTULOS[c] for c in exigidos[servico] if not validos[c]]
        if faltando:
            return {"ok": False, "mensagem": "Confira o formato: " + ", ".join(faltando) + "."}
        if servico == "licenca":
            testar = self._testar_licenca
            if testar is None:
                from drophunter_agent.canal import testar_licenca as testar
            cfg = self.config_atual()
            return await testar(
                valores["licenca"], server_url=cfg.server_url if cfg else None
            )
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=15, trust_env=False, follow_redirects=False
            ) as cliente:
                if servico == "empire":
                    r = await cliente.get(
                        EMPIRE_METADATA,
                        headers={"Authorization": "Bearer " + valores["empire_api_key"]},
                    )
                else:
                    r = await cliente.get(
                        STEAM_PERFIL,
                        params={
                            "key": valores["steam_api_key"],
                            "steamids": valores["steam_id64"],
                        },
                    )
            if r.status_code in (401, 403):
                return {
                    "ok": False,
                    "mensagem": "Chave inválida ou sem permissão. Copie a chave de novo.",
                }
            if r.status_code != 200:
                return {"ok": False, "mensagem": "Serviço indisponível agora. Tente de novo."}
            corpo = r.json()
            if servico == "empire":
                usuario = corpo["user"]
                steam = str(usuario.get("steam_id", ""))
                nome = str(usuario.get("steam_name") or usuario["id"])
                return {
                    "ok": True,
                    "mensagem": "Empire conectado · " + REDATOR.texto(nome),
                    "steam_id64": steam if steam64_valido(steam) else "",
                }
            jogador = next(
                p
                for p in corpo["response"]["players"]
                if p.get("steamid") == valores["steam_id64"]
            )
            nome = REDATOR.texto(str(jogador["personaname"]))
            return {"ok": True, "mensagem": "Perfil Steam · " + nome}
        except (httpx.HTTPError, TimeoutError, OSError):
            return {"ok": False, "mensagem": "Não foi possível conectar. Confira a internet."}
        except (ValueError, KeyError, TypeError, StopIteration, AttributeError):
            return {
                "ok": False,
                "mensagem": "Não deu pra confirmar a conta. Confira a chave e o Steam64.",
            }

    # ------------------------------------------------------------------- salvar
    async def rota_salvar(self, request):
        try:
            dados = await self._dados(request)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response(
                {"ok": False, "mensagem": "Confira os campos informados."},
                status=400,
                headers=CABECALHOS,
            )
        async with self._gravacao:
            resultado, status = await self.salvar(dados)
        return web.json_response(resultado, status=status, headers=CABECALHOS)

    async def salvar(self, dados) -> tuple[dict, int]:
        valores = self._mesclar(dados)
        invalidos = [ROTULOS[c] for c, ok in formatos_validos(valores).items() if not ok]
        if invalidos:
            return (
                {"ok": False, "mensagem": "Confira: " + ", ".join(invalidos) + "."},
                400,
            )
        atual = self.config_atual()
        try:
            cfg = replace(atual, **valores) if atual else Config(**valores)
            # O instalador pode deixar a senha da API local em branco: nasce aqui.
            cfg.api_local_senha = cfg.api_local_senha or gerar_senha()
            cfg.api_local_usuario = cfg.api_local_usuario or API_LOCAL_USUARIO_PADRAO
            for segredo in cfg.segredos():
                REDATOR.adicionar(segredo)
            salvar(cfg, self.caminho)
        except ConfigInvalida as exc:
            return {"ok": False, "mensagem": REDATOR.texto(str(exc))}, 400
        except OSError:
            return (
                {"ok": False, "mensagem": "Não deu pra gravar. Confira a permissão da pasta."},
                500,
            )
        mensagem = "Configurações salvas · reconectando…"
        if self.ao_salvar:
            try:
                await self.ao_salvar(cfg)
            except Exception as exc:  # noqa: BLE001 - a config já está no disco
                log.error("Falha ao reconectar: %s", REDATOR.texto(type(exc).__name__))
                mensagem = "Configurações salvas. Reinicie o DropHunter para conectar."
        return {"ok": True, "mensagem": mensagem}, 200

    # ------------------------------------------------------------- senha local
    async def rota_senha_local(self, request):
        """Mostrar/copiar/gerar a senha da API local — é o que a extensão do Chrome pede."""
        try:
            dados = await self._dados(request)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        cfg = self.config_atual()
        if cfg is None or int(cfg.local_api_port) <= 0:
            return web.json_response(
                {"ok": False, "mensagem": "Configure as chaves primeiro."},
                status=400,
                headers=CABECALHOS,
            )
        acao = dados.get("acao")
        if acao not in ("mostrar", "gerar"):
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        if acao == "mostrar" and cfg.api_local_senha:
            return web.json_response(
                {"ok": True, "senha": cfg.api_local_senha, "mensagem": ""}, headers=CABECALHOS
            )
        async with self._gravacao:
            return await self._trocar_senha_local(cfg)

    async def _trocar_senha_local(self, cfg):
        cfg.api_local_senha = gerar_senha()
        try:
            salvar(cfg, self.caminho)
        except (ConfigInvalida, OSError):
            return web.json_response(
                {"ok": False, "mensagem": "Não deu pra gravar. Confira a permissão da pasta."},
                status=500,
                headers=CABECALHOS,
            )
        REDATOR.adicionar(cfg.api_local_senha)
        mensagem = "Senha nova em uso. Atualize a extensão do Chrome com ela."
        if self.ao_salvar:
            try:
                await self.ao_salvar(cfg)
            except Exception as exc:  # noqa: BLE001 - a senha já está no disco
                log.error("Falha ao reconectar: %s", REDATOR.texto(type(exc).__name__))
                mensagem = "Senha nova gravada. Reinicie o DropHunter para ela valer."
        return web.json_response(
            {"ok": True, "senha": cfg.api_local_senha, "mensagem": mensagem}, headers=CABECALHOS
        )

    # ---------------------------------------------------------------- comandos
    async def rota_autostart(self, request):
        from drophunter_agent.bandeja import (
            autostart_ativo,
            autostart_disponivel,
            comando_app,
            definir_autostart,
        )

        try:
            dados = await self._dados(request)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        if not autostart_disponivel():
            return web.json_response(
                {"ok": False, "ativo": False, "mensagem": "Só existe no Windows."},
                headers=CABECALHOS,
            )
        try:
            definir_autostart(bool(dados.get("ativo")), comando_app(self.caminho))
        except OSError:
            return web.json_response(
                {
                    "ok": False,
                    "ativo": autostart_ativo(),
                    "mensagem": "Não deu pra alterar a inicialização com o Windows.",
                },
                headers=CABECALHOS,
            )
        return web.json_response(
            {"ok": True, "ativo": autostart_ativo(), "mensagem": ""}, headers=CABECALHOS
        )

    async def rota_abrir(self, request):
        """A página nunca escolhe a URL: manda um nome, o agente abre o endereço fixo."""
        try:
            dados = await self._dados(request)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        alvo = dados.get("alvo")
        url = LINKS.get(alvo) or (self.url_pagamentos() if alvo == "pagamentos" else "")
        if not url:
            return web.json_response(
                {"ok": False, "mensagem": "Endereço indisponível agora."},
                status=404,
                headers=CABECALHOS,
            )
        try:
            self._abrir(url)
        except Exception:  # noqa: BLE001 - navegador ausente não derruba o app
            # O link de pagamento carrega o token de acesso: ele não é repetido na tela.
            recado = (
                "Não consegui abrir o navegador."
                if alvo == "pagamentos"
                else "Abra no navegador: " + url
            )
            return web.json_response({"ok": False, "mensagem": recado}, headers=CABECALHOS)
        return web.json_response({"ok": True, "mensagem": ""}, headers=CABECALHOS)

    async def rota_janela(self, request):
        try:
            dados = await self._dados(request)
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        acao = dados.get("acao")
        if acao == "minimizar" and self.ao_minimizar:
            self.ao_minimizar()
        elif acao == "sair" and self.ao_sair:
            asyncio.get_running_loop().call_later(0.3, self.ao_sair)
        else:
            return web.json_response({"ok": False}, status=400, headers=CABECALHOS)
        return web.json_response({"ok": True, "mensagem": ""}, headers=CABECALHOS)
