"""Entrada gráfica: janela nativa na thread principal, agente no seu próprio loop asyncio.

Sem display (servidor Linux, serviço) cai no console de sempre. O `.exe` do Windows aponta
pra `main_app()`; `python -m drophunter_agent` sem argumento também.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from drophunter_agent.config import ConfigInvalida, caminho_config, carregar
from drophunter_agent.redact import REDATOR

log = logging.getLogger("drophunter.app")

ESPERA_ESTADO = 0.5


def tem_display() -> bool:
    return sys.platform == "win32" or bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


class InstanciaUnica:
    """Lock do SO: o arquivo fica, mas o lock cai sozinho se o processo morrer."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.arquivo = None

    def adquirir(self) -> bool:
        self.caminho.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.caminho, os.O_CREAT | os.O_RDWR, 0o600)
        self.arquivo = os.fdopen(fd, "r+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if self.caminho.stat().st_size == 0:
                    self.arquivo.write(b"0")
                    self.arquivo.flush()
                self.arquivo.seek(0)
                msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.arquivo.close()
            self.arquivo = None
            return False

    def liberar(self) -> None:
        if self.arquivo:
            with contextlib.suppress(OSError):
                if sys.platform == "win32":
                    import msvcrt

                    self.arquivo.seek(0)
                    msvcrt.locking(self.arquivo.fileno(), msvcrt.LK_UNLCK, 1)
            self.arquivo.close()
            self.arquivo = None


class Aplicativo:
    """Cola entre a janela (`/app/*`), a bandeja e o agente que já existia."""

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.loop = None
        self.agente = None
        self.tarefa = None
        self.janela = None
        self.nativa = None
        self.bandeja = None
        self.encerrado = threading.Event()
        self.pronto = threading.Event()
        self._saindo = False
        self._reinicio = None
        self.erro = ""
        self.estado = {"estado": "desconectado", "rotulo": "Desconectado", "pagamentos": 0}
        self.thread = threading.Thread(target=self._rodar, name="drophunter-agente", daemon=True)

    # ------------------------------------------------------------------- thread
    def _rodar(self) -> None:
        try:
            asyncio.run(self._executar())
        except Exception as exc:  # noqa: BLE001 - a janela precisa continuar de pé pra mostrar o erro
            self.erro = "Não foi possível iniciar o aplicativo. Veja os logs."
            log.error("Falha no aplicativo: %s", REDATOR.texto(type(exc).__name__))
        finally:
            self.pronto.set()
            self.sair()

    async def _executar(self) -> None:
        from drophunter_agent.janela import Janela, estado_do_canal

        self.loop = asyncio.get_running_loop()
        self.janela = Janela(
            self.caminho,
            canal=lambda: self.agente.canal if self.agente else None,
            ao_salvar=self._aplicar_config,
            ao_sair=self.sair,
            ao_minimizar=self.esconder,
            url_pagamentos=self.url_pagamentos,
            erro=lambda: self.erro,
        )
        await self.janela.iniciar()
        try:
            if self.caminho.exists():
                try:
                    await self._iniciar_agente(carregar(self.caminho))
                except ConfigInvalida:
                    self.erro = "Configuração incompleta. Abra Configurações e informe as chaves."
            self.pronto.set()
            ativar = self.caminho.parent / "app.activate"
            while not self.encerrado.is_set():
                estado = estado_do_canal(self.agente.canal if self.agente else None)
                if not self.caminho.exists():
                    estado = dict(estado, estado="sem_config", rotulo="Sem configuração")
                self.estado = estado
                if self.bandeja:
                    self.bandeja.atualizar(
                        {
                            "estado": estado["estado"],
                            "rotulo": estado["rotulo"],
                            "pagamentos": estado["pagamentos"],
                        }
                    )
                if ativar.exists():
                    ativar.unlink(missing_ok=True)
                    self.mostrar()
                await asyncio.sleep(ESPERA_ESTADO)
        finally:
            await self._parar_agente()
            await self.janela.parar()

    # -------------------------------------------------------------------- agente
    async def _iniciar_agente(self, cfg) -> None:
        from drophunter_agent.agent import Agente

        await self._parar_agente()
        for segredo in cfg.segredos():
            REDATOR.adicionar(segredo)
        self.agente = Agente(cfg)
        self.tarefa = asyncio.create_task(self.agente.executar())
        self.tarefa.add_done_callback(self._agente_terminou)
        self.erro = ""

    async def _parar_agente(self) -> None:
        if self.agente:
            self.agente.pedir_encerramento()
        if self.tarefa:
            tarefa, self.tarefa = self.tarefa, None
            tarefa.remove_done_callback(self._agente_terminou)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(tarefa), 15)
            if not tarefa.done():
                tarefa.cancel()
                await asyncio.gather(tarefa, return_exceptions=True)
        self.agente = None

    def _agente_terminou(self, tarefa) -> None:
        if tarefa.cancelled():
            return
        if tarefa.exception():
            self.erro = "O agente parou. Abra Configurações e salve de novo pra reconectar."
            log.error("Agente encerrado: %s", REDATOR.texto(type(tarefa.exception()).__name__))

    async def _aplicar_config(self, cfg) -> None:
        """Salvar não fecha o app: derruba o canal antigo e sobe outro com a config nova."""
        await self._iniciar_agente(cfg)
        if self.bandeja:
            self.bandeja.avisar("Configurações salvas · reconectando")

    def url_pagamentos(self) -> str:
        api = getattr(self.agente, "api_local", None) if self.agente else None
        return api.url + "/pagar" if api and api.porta else ""

    # -------------------------------------------------------------------- janela
    @property
    def url(self) -> str:
        destino = "" if self.caminho.exists() else "#configuracoes"
        return self.janela.url_com_token + destino

    def mostrar(self) -> None:
        if self.nativa:
            with contextlib.suppress(Exception):
                self.nativa.show()
                self.nativa.restore()
                return
        webbrowser.open(self.url)

    def esconder(self) -> None:
        if self.nativa and self.bandeja:
            with contextlib.suppress(Exception):
                self.nativa.hide()
                return
        if self.bandeja:
            self.bandeja.avisar("O DropHunter continua na bandeja.")

    def abrir_painel(self) -> None:
        from drophunter_agent.janela import PAINEL

        webbrowser.open(PAINEL)

    def abrir_pagamentos(self) -> None:
        url = self.url_pagamentos()
        if url:
            webbrowser.open(url)
        elif self.bandeja:
            self.bandeja.avisar("Conecte o agente para ver pagamentos pendentes.")

    def sair(self) -> None:
        self._saindo = True
        self.encerrado.set()
        if self.bandeja:
            with contextlib.suppress(Exception):
                self.bandeja.parar()
        if self.nativa:
            with contextlib.suppress(Exception):
                self.nativa.destroy()

    def ao_fechar(self) -> bool:
        """X da janela = minimizar pra bandeja; sem bandeja, fechar encerra mesmo."""
        if not self._saindo and self.bandeja:
            with contextlib.suppress(Exception):
                self.nativa.hide()
            return False
        self.encerrado.set()
        return True

    # ----------------------------------------------------------------- interface
    def interface(self) -> None:
        try:
            from drophunter_agent.bandeja import Bandeja

            self.bandeja = Bandeja(
                abrir=self.mostrar,
                painel=self.abrir_painel,
                pagamentos=self.abrir_pagamentos,
                sair=self.sair,
            )
            self.bandeja.iniciar()
        except Exception:  # noqa: BLE001 - Linux sem bandeja: os controles ficam na janela
            self.bandeja = None
            log.warning("Bandeja indisponível neste ambiente")
        try:
            import webview

            self.nativa = webview.create_window(
                "DropHunter",
                self.url,
                width=980,
                height=720,
                min_size=(720, 560),
                background_color="#0b0f17",
                text_select=False,
            )
            self.nativa.events.closing += self.ao_fechar
            webview.start(debug=False, private_mode=True)
            return
        except Exception:  # noqa: BLE001 - sem WebView2/GTK a mesma página abre no navegador
            self.nativa = None
            log.warning("Janela nativa indisponível; usando o navegador padrão")
            if self.bandeja:
                self.bandeja.avisar("Janela nativa indisponível. O DropHunter abriu no navegador.")
        webbrowser.open(self.url)
        try:
            while not self.encerrado.wait(1):
                pass
        except KeyboardInterrupt:
            self.sair()


def executar_app(caminho) -> int:
    from drophunter_agent.logs import configurar_log_arquivo

    lock = InstanciaUnica(caminho.parent / "app.lock")
    if not lock.adquirir():
        # Já existe um DropHunter aberto: o arquivo faz a instância viva mostrar a janela.
        (caminho.parent / "app.activate").touch(mode=0o600)
        return 0
    app = None
    try:
        configurar_log_arquivo(caminho.parent / "logs")
        app = Aplicativo(caminho)
        app.thread.start()
        if not app.pronto.wait(30) or app.encerrado.is_set():
            return 2
        app.interface()
        return 0
    finally:
        if app:
            app.sair()
            app.thread.join(timeout=25)
        lock.liberar()


def main_app(caminho=None) -> int:
    if not tem_display():
        from drophunter_agent.cli import _assistente

        return _assistente(caminho)
    return executar_app(Path(caminho or caminho_config()))
