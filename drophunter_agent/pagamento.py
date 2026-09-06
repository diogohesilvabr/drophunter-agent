"""Tip exclusivamente após consentimento local, com destino e teto fixados no PC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import secrets
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from drophunter_agent.config import diretorio_config, steam64_valido
from drophunter_agent.redact import MASCARA, Redator

URL_TIP = "https://csgoempire.com/api/v2/user/tip"


class PagamentoLocalErro(Exception):
    def __init__(self, http: int, motivo: str):
        self.http, self.motivo = http, motivo
        super().__init__(motivo)


def _identificador(valor) -> bool:
    return isinstance(valor, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", valor) is not None


def _vencimento(valor) -> float:
    if isinstance(valor, str):
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        if data.tzinfo is None:
            raise ValueError
        valor = data.timestamp()
    if isinstance(valor, bool) or not isinstance(valor, (float, int)) or not math.isfinite(valor):
        raise ValueError
    return float(valor)


def _corpo_redigido(bruto, redator):
    def limpar(valor):
        if isinstance(valor, dict):
            return {
                k: MASCARA
                if re.sub(r"[^a-z0-9]", "", k.lower())
                in {"code", "codigo2fa", "mfa", "otp", "totp"}
                else limpar(v)
                for k, v in valor.items()
            }
        if isinstance(valor, list):
            return [limpar(v) for v in valor]
        if isinstance(valor, (int, float)) and redator.contem_segredo(str(valor)):
            return MASCARA
        return valor

    with contextlib.suppress(ValueError, RecursionError):
        bruto = json.dumps(limpar(json.loads(bruto)), ensure_ascii=False)
    return redator.texto(redator.corpo(bruto))


class Pagamentos:
    def __init__(self, canal):
        self.canal = canal
        self.cfg = canal.cfg
        self.pendentes: dict[str, dict] = {}
        self.tokens: dict[str, tuple[str, float]] = {}
        self.vistos: set[str] = set()
        self.concluidos: set[str] = set()
        self.faturas_reservadas: set[str] = set()
        self.resultados: list[dict] = []
        self._lock = asyncio.Lock()
        self._envio = asyncio.Lock()
        self.diario = diretorio_config() / "pagamentos.jsonl"
        self.falha_diario = False
        self._carregar()

    def _carregar(self):
        try:
            if not self.diario.exists():
                return
            os.chmod(self.diario, 0o600)
            with self.diario.open(encoding="utf-8") as arquivo:
                for linha in arquivo:
                    q = json.loads(linha)
                    if not _identificador(q.get("id")) or not _identificador(q.get("fatura_id")):
                        raise ValueError
                    self.vistos.add(q["id"])
                    if q["status"] == "iniciado":
                        self.faturas_reservadas.add(q["fatura_id"])
                    elif q["status"] == "ok":
                        self.concluidos.add(q["id"])
                        self.faturas_reservadas.add(q["fatura_id"])
                    elif q["status"] == "erro":
                        self.faturas_reservadas.discard(q["fatura_id"])
                    else:
                        raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            self.falha_diario = True

    def _registrar(self, q, status):
        self.diario.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.diario.parent, 0o700)
        fd = os.open(self.diario, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as arquivo:
            os.chmod(self.diario, 0o600)
            arquivo.write(
                json.dumps(
                    {
                        "id": q["id"],
                        "fatura_id": q["fatura_id"],
                        "status": status,
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
            arquivo.flush()
            os.fsync(arquivo.fileno())

    def _recusa(self, q):
        if self.canal.pagamentos_bloqueados:
            return "recusado_local", self.canal.pagamentos_bloqueados
        if self.falha_diario:
            return "recusado_local", "diário de pagamentos indisponível; confira o extrato"
        if (
            not steam64_valido(self.cfg.plataforma_steam_id)
            or q["destino_steam_id"] != self.cfg.plataforma_steam_id
        ):
            return "recusado_local", "destino diferente do fixado localmente"
        if Decimal(q["valor_coins"]) > self.cfg.pagamento_teto_coins:
            return "recusado_local", "valor acima do teto local"
        if q["expires_at"] <= time.time():
            return "expirado", "pedido vencido"
        return None

    async def _resultado(self, q, status, http=0, body=""):
        resposta = self.canal.redator.estrutura(
            {
                "t": "fatura_paga",
                "id": q.get("id"),
                "fatura_id": q.get("fatura_id"),
                "status": status,
                "http": http,
                "body": self.canal.redator.corpo(body),
                "ts": time.time(),
            }
        )
        self.resultados.append(resposta)
        await self.reenviar()
        return resposta

    async def reenviar(self):
        async with self._envio:
            while self.resultados and self.canal.online:
                try:
                    await self.canal.enviar(self.resultados[0])
                except (ConnectionError, OSError, RuntimeError):
                    return
                self.resultados.pop(0)

    async def expirar(self):
        vencidos = [ident for ident, q in self.pendentes.items() if q["expires_at"] <= time.time()]
        for ident in vencidos:
            q = self.pendentes.pop(ident, None)
            self.tokens.pop(ident, None)
            if q is not None:
                await self._resultado(q, "expirado", body="pedido vencido")

    async def receber(self, quadro):
        await self.expirar()
        ident = quadro.get("id")
        if _identificador(ident):
            if ident in self.vistos:
                return
            self.vistos.add(ident)
        try:
            if not _identificador(ident) or not _identificador(quadro.get("fatura_id")):
                raise ValueError
            competencia = quadro.get("competencia")
            if not isinstance(competencia, str) or not re.fullmatch(
                r"[0-9]{4}-(0[1-9]|1[0-2])", competencia
            ):
                raise ValueError
            bruto = quadro.get("valor_coins")
            if (
                not isinstance(bruto, (str, int, float))
                or isinstance(bruto, bool)
                or len(str(bruto)) > 32
            ):
                raise ValueError
            valor = Decimal(str(bruto))
            if (
                not valor.is_finite()
                or valor <= 0
                or valor > Decimal("1e12")
                or valor.as_tuple().exponent < -2
            ):
                raise ValueError
            if not steam64_valido(quadro.get("destino_steam_id")):
                raise ValueError
            vencimento = _vencimento(quadro.get("expires_at"))
            if vencimento > time.time() + 86400:
                raise ValueError
            q = {
                "id": ident,
                "fatura_id": quadro["fatura_id"],
                "competencia": competencia,
                "valor_coins": str(valor),
                "destino_steam_id": quadro["destino_steam_id"],
                "expires_at": vencimento,
            }
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            await self._resultado(quadro, "recusado_local", body="campos de pagamento inválidos")
            return
        recusa = self._recusa(q)
        if recusa:
            await self._resultado(q, recusa[0], body=recusa[1])
        elif q["fatura_id"] in self.faturas_reservadas or any(
            p["fatura_id"] == q["fatura_id"] for p in self.pendentes.values()
        ):
            await self._resultado(
                q, "recusado_local", body="fatura já pendente ou executada; confira o extrato"
            )
        elif len(self.pendentes) >= 100:
            await self._resultado(q, "recusado_local", body="limite de pedidos pendentes")
        else:
            self.pendentes[ident] = q

    def gerar_token(self, ident):
        token = secrets.token_urlsafe(32)
        self.tokens[ident] = token, time.monotonic() + 600
        return token

    async def autorizar(self, ident, token, *, cancelar=False, codigo_2fa=""):
        async with self._lock:
            if not self.canal.online:
                raise PagamentoLocalErro(503, "reconecte o agente")
            if self.canal.proxy.parado:
                raise PagamentoLocalErro(503, "parado")
            guardado = self.tokens.get(ident)
            if (
                not guardado
                or not isinstance(token, str)
                or not token.isascii()
                or not secrets.compare_digest(guardado[0], token)
                or time.monotonic() >= guardado[1]
                or ident not in self.pendentes
            ):
                raise PagamentoLocalErro(403, "token inválido, vencido ou já utilizado")
            if not isinstance(codigo_2fa, str):
                raise PagamentoLocalErro(400, "código 2FA inválido")
            if codigo_2fa and not re.fullmatch(r"[0-9]{6,8}", codigo_2fa):
                raise PagamentoLocalErro(400, "código 2FA deve ter de 6 a 8 dígitos")
            self.tokens.pop(ident)
            q = self.pendentes.pop(ident)
            if cancelar:
                return await self._resultado(q, "cancelado")
            recusa = self._recusa(q)
            if recusa:
                return await self._resultado(q, recusa[0], body=recusa[1])
            try:
                self._registrar(q, "iniciado")
            except OSError:
                self.falha_diario = True
                return await self._resultado(
                    q, "recusado_local", body="não foi possível gravar o diário local"
                )
            self.faturas_reservadas.add(q["fatura_id"])
            corpo = {
                "steam_id": q["destino_steam_id"],
                "amount": str(int(Decimal(q["valor_coins"]) * 100)),
            }
            if codigo_2fa:
                corpo["code"] = codigo_2fa
            redator = Redator([*self.cfg.segredos(), token, codigo_2fa])
            http, status, body = (
                0,
                "erro",
                "resultado incerto; confira o extrato antes de outro pedido",
            )
            definitivo = False
            try:
                async with asyncio.timeout(20):
                    async with self.canal.proxy._client.stream(
                        "POST",
                        URL_TIP,
                        json=corpo,
                        headers={"Authorization": f"Bearer {self.cfg.empire_api_key}"},
                        timeout=20,
                        follow_redirects=False,
                    ) as resposta:
                        http = resposta.status_code
                        bruto, truncado = await self.canal.proxy._ler(resposta)
                body = (
                    _corpo_redigido(bruto, redator)
                    if not truncado
                    else "resposta excede limite; confira o extrato"
                )
                try:
                    dados = json.loads(bruto) if not truncado else None
                except (ValueError, RecursionError):
                    dados = None
                if 200 <= http < 300 and isinstance(dados, dict) and dados.get("success") is True:
                    status, definitivo = "ok", True
                    self.concluidos.add(ident)
                elif isinstance(dados, dict) and dados.get("success") is False and http < 500:
                    definitivo = True
            except (httpx.HTTPError, TimeoutError, ValueError):
                pass
            # Falha/queda sem resposta conclusiva mantém a reserva durável da fatura.
            if definitivo:
                try:
                    self._registrar(q, status)
                    if status == "erro":
                        self.faturas_reservadas.discard(q["fatura_id"])
                except OSError:
                    self.falha_diario = True
            return await self._resultado(q, status, http=http, body=body)
