"""Loop principal do agente (asyncio).

hello -> config -> feed do Empire (chave local) -> eventos em lote (<=2s) ->
long-poll de comandos -> executa no Empire -> reporta. Heartbeat a cada 30s.
401 do servidor -> para tudo, mostra motivo, tenta de novo a cada 5 min.
Rede caiu -> backoff exponencial (1s..60s).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from typing import Any

import httpx

from drophunter_agent import __version__
from drophunter_agent.commands import TIPOS_LOCAIS, Executor
from drophunter_agent.config import Config
from drophunter_agent.empire import EmpireFeed, EmpireRest, saldo_coins
from drophunter_agent.fingerprint import descricao_so, fingerprint
from drophunter_agent.redact import REDATOR, Redator
from drophunter_agent.server import (
    LicencaRecusada,
    ServidorClient,
    ServidorIndisponivel,
    ServidorRecusou,
)

log = logging.getLogger("drophunter")

INTERVALO_HEARTBEAT_S = 30.0
INTERVALO_LOTE_S = 2.0
TAMANHO_LOTE_MAX = 300
ESPERA_401_S = 300.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0

# evento do feed do Empire -> tipo do protocolo
_MAPA_FEED = {
    "new_item": "auction_new",
    "updated_item": "auction_update",
    "deleted_item": "auction_end",
}


class ParadaDoAgente(Exception):
    """Parada definitiva pedida pelo servidor (update_available hard, stop exit)."""


class Agente:
    def __init__(
        self,
        cfg: Config,
        *,
        redator: Redator | None = None,
        transport_servidor: httpx.AsyncBaseTransport | None = None,
        transport_empire: httpx.AsyncBaseTransport | None = None,
        empire_base_url: str | None = None,
        sio_factory=None,
        intervalo_heartbeat_s: float = INTERVALO_HEARTBEAT_S,
        intervalo_lote_s: float = INTERVALO_LOTE_S,
        espera_401_s: float = ESPERA_401_S,
        poll_min_s: float = 1.0,
        sleep=None,
    ) -> None:
        self.cfg = cfg
        self.redator = redator or REDATOR
        for s in cfg.segredos():
            self.redator.adicionar(s)
            REDATOR.adicionar(s)  # o formatador de log e global: precisa conhecer os segredos
        self._sleep = sleep or asyncio.sleep
        self._hb_s = intervalo_heartbeat_s
        self._lote_s = intervalo_lote_s
        self._espera_401_s = espera_401_s
        self._poll_min_s = poll_min_s

        self.servidor = ServidorClient(
            cfg.server_url, cfg.licenca, self.redator, transport=transport_servidor
        )
        kw: dict[str, Any] = {"transport": transport_empire}
        if empire_base_url:
            kw["base_url"] = empire_base_url
        self.empire = EmpireRest(cfg.empire_api_key, **kw)
        self.feed = EmpireFeed(self.empire, self._ao_evento_feed, sio_factory=sio_factory)
        self.executor = Executor(self.empire, self.redator, agora=self.servidor.agora_servidor)

        self.status = "idle"  # idle | watching | erro
        self.saldo: float | None = None
        self.config_servidor: dict = {}
        self.ativo = True  # False = 'stop' do servidor (pausa); volta com set_config active
        self.motivo_parada: str | None = None
        self._fila_eventos: asyncio.Queue[dict] = asyncio.Queue(maxsize=20000)
        self._tarefas: list[asyncio.Task] = []
        self._encerrar = asyncio.Event()
        self._watch_ids: set[int] | None = None
        self.eventos_enviados = 0
        self.comandos_executados = 0

    # ----------------------------------------------------------------- ciclo
    async def executar(self) -> None:
        """Roda ate Ctrl+C, 'stop' definitivo ou update obrigatorio."""
        log.info(
            "DropHunter Agent %s | servidor %s | licenca %s",
            __version__,
            self.cfg.server_url,
            _mascara(self.cfg.licenca),
        )
        atraso = BACKOFF_MIN_S
        try:
            while not self._encerrar.is_set():
                try:
                    await self._sessao()
                    atraso = BACKOFF_MIN_S
                except LicencaRecusada as exc:
                    self.status = "erro"
                    self.motivo_parada = exc.motivo
                    await self._parar_tarefas()
                    log.error("Servidor recusou a licenca (401): %s", exc.motivo)
                    log.error(
                        "Parei de operar. Tento de novo em %.0f min. Confira a licenca "
                        "na sua conta em %s",
                        self._espera_401_s / 60,
                        self.cfg.server_url,
                    )
                    await self._sleep(self._espera_401_s)
                except ServidorIndisponivel as exc:
                    self.status = "erro"
                    await self._parar_tarefas()
                    espera = atraso + random.uniform(0, atraso / 2)
                    log.warning("Servidor indisponivel (%s). Tento de novo em %.0fs", exc, espera)
                    await self._sleep(espera)
                    atraso = min(atraso * 2, BACKOFF_MAX_S)
                except ServidorRecusou as exc:
                    self.status = "erro"
                    await self._parar_tarefas()
                    log.error("Servidor recusou a chamada (%s). Tento de novo em 60s", exc)
                    await self._sleep(60)
                except ParadaDoAgente as exc:
                    log.error("Encerrando: %s", exc)
                    self.motivo_parada = str(exc)
                    break
        finally:
            await self._parar_tarefas()
            await self.feed.parar()
            await self.servidor.close()
            await self.empire.close()
            log.info("Agente encerrado.")

    def pedir_encerramento(self) -> None:
        self._encerrar.set()

    async def _sessao(self) -> None:
        """Uma sessao com o servidor: hello + tarefas ate alguma falhar."""
        resposta = await self.servidor.hello(descricao_so(), fingerprint())
        self.motivo_parada = None
        self._aplicar_config(resposta.get("config") or {})
        self._checar_versao(resposta)
        log.info(
            "Servidor aceitou o agente. Config recebida com %d chave(s).", len(self.config_servidor)
        )
        if self.ativo:
            await self.feed.iniciar()
            self.status = "watching"
        else:
            self.status = "idle"

        self._tarefas = [
            asyncio.create_task(self._loop_heartbeat(), name="heartbeat"),
            asyncio.create_task(self._loop_eventos(), name="eventos"),
            asyncio.create_task(self._loop_comandos(), name="comandos"),
            asyncio.create_task(self._encerrar.wait(), name="encerrar"),
        ]
        feito, _ = await asyncio.wait(self._tarefas, return_when=asyncio.FIRST_COMPLETED)
        for t in feito:
            if t.get_name() == "encerrar":
                return
            exc = t.exception()
            if exc is not None:
                raise exc

    async def _parar_tarefas(self) -> None:
        tarefas, self._tarefas = self._tarefas, []
        for t in tarefas:
            t.cancel()
        for t in tarefas:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self.feed.parar()

    # -------------------------------------------------------------- tarefas
    async def _loop_heartbeat(self) -> None:
        while True:
            await self._atualizar_saldo()
            resp = await self.servidor.heartbeat(self.status, self.saldo)
            self._checar_versao(resp)
            log.debug("heartbeat %s saldo=%s", self.status, self.saldo)
            await self._sleep(self._hb_s)

    async def _atualizar_saldo(self) -> None:
        try:
            # sem retry: o heartbeat nao pode atrasar por causa de 429 do Empire
            meta = await self.empire.metadata(tentativas=1)
            saldo = saldo_coins(meta)
            if saldo is not None:
                self.saldo = saldo
        except Exception as exc:
            log.debug("saldo do Empire indisponivel: %s", exc)

    async def _loop_eventos(self) -> None:
        while True:
            lote = await self._juntar_lote()
            if not lote:
                continue
            await self.servidor.eventos(lote)
            self.eventos_enviados += len(lote)
            log.debug("%d evento(s) enviados", len(lote))

    async def _juntar_lote(self) -> list[dict]:
        lote: list[dict] = []
        try:
            primeiro = await asyncio.wait_for(self._fila_eventos.get(), timeout=self._lote_s)
        except TimeoutError:
            return lote
        lote.append(primeiro)
        limite = time.monotonic() + self._lote_s
        while len(lote) < TAMANHO_LOTE_MAX:
            restante = limite - time.monotonic()
            if restante <= 0:
                break
            try:
                lote.append(await asyncio.wait_for(self._fila_eventos.get(), timeout=restante))
            except TimeoutError:
                break
        return lote

    async def _loop_comandos(self) -> None:
        while True:
            t0 = time.monotonic()
            comandos = await self.servidor.comandos()
            for cmd in comandos:
                await self._tratar_comando(cmd)
            # servidor que responde vazio na hora (sem long-poll) nao pode virar
            # busy-loop: garante um respiro minimo entre chamadas
            if not comandos and (time.monotonic() - t0) < self._poll_min_s:
                await asyncio.sleep(self._poll_min_s)

    async def _tratar_comando(self, cmd: dict) -> None:
        cmd_id = str(cmd.get("id") or "")
        tipo = str(cmd.get("type") or "")
        if not cmd_id:
            log.warning("Comando sem id ignorado: %s", self.redator.texto(cmd)[:200])
            return
        if self.executor.ja_visto(cmd_id):
            log.debug("Comando %s repetido; ignorado", cmd_id)
            return
        parar = False
        if tipo in TIPOS_LOCAIS:
            resultado, parar = await self._comando_local(cmd)
        else:
            resultado = await self.executor.executar(cmd)
            self.comandos_executados += 1
        await self.servidor.resultado(resultado)
        if parar:
            raise ParadaDoAgente(resultado["body"])

    async def _comando_local(self, cmd: dict) -> tuple[dict, bool]:
        """Devolve (resultado, parar_definitivamente)."""
        cmd_id = str(cmd["id"])
        tipo = cmd["type"]
        payload = cmd.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        base = {"command_id": cmd_id, "status": "ok", "http": None, "body": "", "ts": time.time()}
        if self.executor.expirado(cmd) and tipo != "update_available":
            return {
                **base,
                "status": "expirado",
                "body": "comando vencido antes de executar",
            }, False
        if tipo == "set_config":
            self._aplicar_config(payload)
            if self.ativo and not self.feed.conectado and self._tarefas:
                await self.feed.iniciar()
                self.status = "watching"
            return {**base, "body": f"config aplicada ({len(payload)} chave(s))"}, False
        if tipo == "stop":
            motivo = str(payload.get("reason") or "pedido do servidor")
            self.ativo = False
            self.status = "idle"
            await self.feed.parar()
            log.warning("Servidor pediu PARADA (%s). Feed fechado; sigo ouvindo comandos.", motivo)
            if payload.get("exit"):
                return {**base, "body": f"parada definitiva: {motivo}"}, True
            return {**base, "body": f"pausado: {motivo}"}, False
        if tipo == "update_available":
            url = str(payload.get("url") or "")
            versao = str(payload.get("version") or "?")
            hard = bool(payload.get("hard"))
            log.warning("=" * 70)
            log.warning(
                "NOVA VERSAO DO AGENTE DISPONIVEL: %s (voce esta na %s)", versao, __version__
            )
            log.warning("Baixe em: %s", url or "(URL nao informada)")
            if hard:
                log.warning("Esta atualizacao e OBRIGATORIA. Parando o agente.")
            log.warning("=" * 70)
            corpo = f"update {versao} visto; hard={hard}"
            return {**base, "body": corpo}, hard
        return {**base, "status": "erro", "body": f"tipo local desconhecido: {tipo}"}, False

    # --------------------------------------------------------------- helpers
    def _aplicar_config(self, novo: dict) -> None:
        if not isinstance(novo, dict):
            return
        self.config_servidor.update(novo)
        ids = novo.get("watch_auction_ids")
        if ids is None and "watch_auction_ids" in novo:
            self._watch_ids = None
        elif isinstance(ids, list):
            self._watch_ids = set()
            for i in ids:
                with contextlib.suppress(TypeError, ValueError):
                    self._watch_ids.add(int(i))
        if "active" in novo:
            self.ativo = bool(novo["active"])

    def _checar_versao(self, resp: dict) -> None:
        minimo = resp.get("min_version") if isinstance(resp, dict) else None
        if minimo and _versao_tupla(str(minimo)) > _versao_tupla(__version__):
            log.warning(
                "Servidor exige versao >= %s (voce esta na %s). Atualize: %s",
                minimo,
                __version__,
                resp.get("update_url") or self.cfg.server_url,
            )

    async def _ao_evento_feed(self, evento: str, payload: Any) -> None:
        """Feed do Empire -> fila de eventos do protocolo (payload cru)."""
        tipo = _MAPA_FEED.get(evento)
        if tipo == "auction_end":
            ids = payload if isinstance(payload, list) else [payload]
            for i in ids:
                self._enfileirar(tipo, {"id": i})
            return
        if tipo is not None:
            itens = payload if isinstance(payload, list) else [payload]
            for raw in itens:
                if tipo == "auction_update" and self._watch_ids is not None:
                    try:
                        if int(raw.get("id")) not in self._watch_ids:
                            continue
                    except (AttributeError, TypeError, ValueError):
                        continue
                self._enfileirar(tipo, raw)
            return
        # qualquer outro evento do Empire (trade_status, notificacoes...) vai cru,
        # embrulhado com o nome do evento, como trade_update
        self._enfileirar("trade_update", {"event": evento, "payload": payload})

    def _enfileirar(self, tipo: str, data: Any) -> None:
        ev = {"id": f"evt_{uuid.uuid4().hex}", "ts": time.time(), "type": tipo, "data": data}
        try:
            self._fila_eventos.put_nowait(ev)
        except asyncio.QueueFull:
            log.warning("Fila de eventos cheia; descartando %s", tipo)

    def log_servidor(self, texto: str) -> None:
        """Evento agent_log: so texto redigido."""
        self._enfileirar("agent_log", {"text": self.redator.texto(texto)[:1000]})


def _versao_tupla(v: str) -> tuple:
    partes = []
    for p in v.strip().lstrip("v").split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        partes.append(int(num or 0))
    return tuple(partes)


def _mascara(licenca: str) -> str:
    from drophunter_agent.config import mascarar

    return mascarar(licenca)
