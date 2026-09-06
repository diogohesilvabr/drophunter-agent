"""Composição do agente público; nenhuma dependência do bot ou servidor privado."""

from __future__ import annotations

import logging

from drophunter_agent.canal import Canal
from drophunter_agent.config import Config
from drophunter_agent.empire_ws import EmpireSocket
from drophunter_agent.local_api import ApiLocal
from drophunter_agent.proxy import Proxy

log = logging.getLogger("drophunter")


class Agente:
    def __init__(
        self, cfg: Config, *, transport_empire=None, sio_factory=None, api_local=True, **canal_kw
    ):
        cfg.validar()
        self.cfg = cfg
        self.proxy = Proxy(cfg, transport=transport_empire)
        self.feed = EmpireSocket(
            self.proxy.metadata,
            redator=self.proxy.redator,
            sio_factory=sio_factory,
            pode_autenticar=lambda: not self.proxy.parado,
        )
        self.canal = Canal(cfg, proxy=self.proxy, feed=self.feed, **canal_kw)
        self.api_local = (
            ApiLocal(
                self.canal,
                porta=cfg.local_api_port,
                usuario=cfg.api_local_usuario,
                senha=cfg.api_local_senha,
                estado=lambda: {"status": self.canal.status},
            )
            if (api_local and cfg.local_api_port > 0)
            else None
        )

    @property
    def motivo_parada(self):
        return self.canal.motivo_parada

    def pedir_encerramento(self):
        self.canal.pedir_encerramento()

    async def executar(self):
        try:
            if self.api_local:
                try:
                    await self.api_local.iniciar()
                    self.cfg.local_api_port = self.api_local.porta
                    log.info("API local da extensão: %s", self.api_local.url)
                except OSError:
                    log.error("Porta local ocupada; extensão indisponível nesta execução")
                    self.cfg.local_api_port = 0
            await self.canal.executar()
        finally:
            if self.api_local:
                await self.api_local.parar()
