"""HTTP externo sem decisões de negócio, com credenciais exclusivamente locais."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import httpx

from drophunter_agent.config import Config
from drophunter_agent.listabranca import BASES, Recusado, validar
from drophunter_agent.redact import MASCARA, REDATOR, Redator

LIMITE_CORPO = 1024 * 1024


class Proxy:
    def __init__(self, cfg: Config, *, transport=None, redator: Redator | None = None):
        self.cfg = cfg
        self.redator = redator or Redator(cfg.segredos())
        for segredo in cfg.segredos():
            self.redator.adicionar(segredo)
            REDATOR.adicionar(segredo)
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False, timeout=20
        )
        self._semaforo = asyncio.Semaphore(8)
        self._online = False
        self.parado = True
        self.recusados = 0

    def conectar(self) -> None:
        self._online = True
        self.parado = False
        self.recusados = 0

    def desconectar(self) -> None:
        self._online = False
        self.parado = True

    def parar(self) -> None:
        self.parado = True

    async def close(self) -> None:
        self.desconectar()
        await self._client.aclose()

    async def metadata(self) -> dict:
        """Só o handshake local chama isto, depois de abrir o canal autenticado."""
        if not self._online or self.parado:
            raise ConnectionError("canal indisponível para autenticar o Empire")
        async with self._client.stream(
            "GET",
            BASES["empire"][0] + "/metadata/socket",
            headers={"Authorization": f"Bearer {self.cfg.empire_api_key}"},
        ) as resposta:
            resposta.raise_for_status()
            bruto, truncado = await self._ler(resposta)
        if truncado:
            raise ValueError("metadata maior que o limite")
        dados = json.loads(bruto)
        if not isinstance(dados, dict):
            raise ValueError("metadata inválido")
        self.redator.registrar_credenciais(dados)
        REDATOR.registrar_credenciais(dados)
        return dados

    async def _ler(self, resposta: httpx.Response) -> tuple[str, bool]:
        # A margem permite redigir segredo que atravessa a borda do truncamento.
        margem = max((len(s.encode()) for s in self.redator.segredos), default=0)
        limite = LIMITE_CORPO + max(4096, margem)
        dados = bytearray()
        async for pedaco in resposta.aiter_bytes(chunk_size=65536):
            dados.extend(pedaco[: limite + 1 - len(dados)])
            if len(dados) > limite:
                break
        return dados.decode("utf-8", "replace"), len(dados) > LIMITE_CORPO

    async def executar(self, quadro: dict[str, Any]) -> dict:
        inicio = time.monotonic()
        resposta = {
            "t": "http_response",
            "id": quadro.get("id"),
            "status": 0,
            "body": "",
            "headers": {},
            "elapsed_ms": 0,
            "erro": None,
        }
        try:
            if self.parado or not self._online:
                resposta["erro"] = "parado"
                return self.redator.dados(resposta)
            url = validar(quadro)
            alvo = quadro["alvo"]
            params = dict(quadro.get("params") or {})
            headers = {}
            if alvo == "empire":
                headers["Authorization"] = f"Bearer {self.cfg.empire_api_key}"
            elif alvo == "steam":
                if not self.cfg.steam_api_key:
                    # PROTOCOLO v2: sem chave Steam local nao e "recusado" (isso e bug do
                    # servidor); e um 403 que o delivery do bot ja sabe tratar.
                    resposta.update(
                        status=403,
                        body='{"erro":"steam_api_key_nao_configurada"}',
                        headers={"content-type": "application/json"},
                        elapsed_ms=int((time.monotonic() - inicio) * 1000),
                    )
                    return self.redator.dados(resposta)
                params["key"] = self.cfg.steam_api_key
            tempo = quadro.get("timeout_s", 20)
            async with asyncio.timeout(tempo):
                async with self._semaforo:
                    if self.parado or not self._online:
                        resposta["erro"] = "parado"
                        return self.redator.dados(resposta)
                    envio = {"json": quadro.get("json")}
                    if alvo == "steam" and quadro["method"] == "POST":
                        envio = {
                            "data": {**(quadro.get("json") or {}), "key": self.cfg.steam_api_key}
                        }
                        params = {}
                    async with self._client.stream(
                        quadro["method"],
                        url,
                        params=params,
                        **envio,
                        headers=headers,
                        timeout=tempo,
                    ) as externo:
                        corpo, truncado = await self._ler(externo)
                        if quadro["path"] == "/metadata/socket" and truncado:
                            # Metadata pode renovar tokens; JSON incompleto é descartado.
                            corpo = MASCARA
                        else:
                            if quadro["path"] == "/metadata/socket":
                                # O metadata RENOVA `socket_token`/`socket_signature`, que
                                # sao segredos do cliente e nao podem subir. Antes eles
                                # sumiam por adivinhacao; agora que a redacao de dado so
                                # apaga o que CONHECE, eles precisam ser registrados aqui,
                                # antes de redigir. Mesma coisa que `metadata()` ja faz.
                                with contextlib.suppress(Exception):
                                    novos = json.loads(corpo)
                                    self.redator.registrar_credenciais(novos)
                                    REDATOR.registrar_credenciais(novos)
                            # DADO, nao log: e a resposta do Empire que vira o estado do
                            # bot. `corpo` ADIVINHA segredo (o padrao `token=` e a chave
                            # chamada "token") e apagava o token do link de troca do
                            # comprador — a Steam recusava a oferta com AccessDenied (15)
                            # e a venda nao saia. `dados` apaga so os segredos CONHECIDOS
                            # (a chave do Empire e a da Steam do cliente), que e o que a
                            # custodia zero exige. Ver o comentario em `redact.dados`.
                            corpo = self.redator.dados(corpo)
                        bruto = corpo.encode("utf-8")
                        truncado = truncado or len(bruto) > LIMITE_CORPO
                        resposta.update(
                            status=externo.status_code,
                            body=bruto[:LIMITE_CORPO].decode("utf-8", "ignore"),
                            headers={
                                k: v
                                for k, v in externo.headers.items()
                                if k in {"retry-after", "content-type"}
                            },
                        )
                        if truncado:
                            resposta["truncado"] = True
        except Recusado:
            self.recusados += 1
            resposta["erro"] = "recusado"
        except (TimeoutError, httpx.TimeoutException):
            resposta["erro"] = "timeout"
        except (httpx.HTTPError, ValueError, TypeError):
            resposta["erro"] = "rede"
        finally:
            resposta["elapsed_ms"] = int((time.monotonic() - inicio) * 1000)
        return self.redator.dados(resposta)
