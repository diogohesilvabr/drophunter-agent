"""Rotas e bases permitidas pelo PROTOCOLO v2; qualquer desvio é recusado."""

from __future__ import annotations

import math
import re
from typing import Any

BASES = {
    "empire": ("https://csgoempire.com/api/v2", "https://trade.csgoempire.com/api/v2"),
    "steam": ("https://api.steampowered.com",),
    "steam_community": ("https://steamcommunity.com",),
}
ROTAS = {
    "empire": (
        ("GET", "/metadata/socket"),
        ("GET", "/trading/user/balance"),
        ("GET", "/trading/user/inventory"),
        ("GET", "/trading/user/portfolio"),
        ("GET", "/trading/user/trades"),
        ("GET", "/trading/trades"),
        ("GET", "/trading/items"),
        ("GET", "/trading/items/{id}"),
        ("GET", "/trading/items/{id}/listings"),
        ("GET", "/trading/item/{id}"),
        ("GET", "/trading/item/{id}/sales"),
        ("GET", "/trading/deposit/{id}"),
        ("POST", "/trading/deposit"),
        ("POST", "/trading/deposit/{id}/bid"),
        ("POST", "/trading/deposit/{id}/cancel"),
        ("POST", "/trading/deposit/{id}/sent"),
        ("POST", "/trading/deposit/{id}/received"),
        ("POST", "/trading/deposit/{id}/dispute"),
        ("PATCH", "/trading/deposit/{id}"),
        ("GET", "/user/transactions"),
    ),
    "steam": (
        ("GET", "/IEconService/GetTradeOffers/v1/"),
        ("POST", "/IEconService/DeclineTradeOffer/v1/"),
        ("GET", "/ISteamUser/GetPlayerSummaries/v2/"),
    ),
    "steam_community": (("GET", "/inventory/{steam_id}/{app}/{context}"),),
}
_PADROES = {
    alvo: [
        (metodo, re.compile(re.sub(r"\{(?:id|steam_id|app|context)\}", "[0-9]+", rota)))
        for metodo, rota in rotas
    ]
    for alvo, rotas in ROTAS.items()
}
_CAMPOS = {"t", "id", "alvo", "method", "path", "params", "json", "timeout_s", "base"}
_CREDENCIAIS = {"key", "api_key", "apikey", "authorization", "access_token"}


class Recusado(ValueError):
    """O quadro não pertence ao contrato permitido."""


def validar(quadro: dict[str, Any]) -> str:
    if not isinstance(quadro, dict) or quadro.keys() - _CAMPOS:
        raise Recusado("campos fora do contrato")
    alvo, metodo, rota = (quadro.get(c) for c in ("alvo", "method", "path"))
    if not all(isinstance(v, str) for v in (alvo, metodo, rota)) or alvo not in BASES:
        raise Recusado("alvo, método ou rota inválidos")
    base = quadro.get("base", BASES[alvo][0])
    if base not in BASES[alvo]:
        raise Recusado("base não permitida")
    params, corpo = quadro.get("params"), quadro.get("json")
    if params is not None and not isinstance(params, dict):
        raise Recusado("params precisa ser objeto")
    if params and metodo != "GET":
        raise Recusado("params só em GET")
    if corpo is not None and not isinstance(corpo, dict):
        raise Recusado("json precisa ser objeto ou null")
    for dados in (params, corpo):
        if dados and any(str(k).lower() in _CREDENCIAIS for k in dados):
            raise Recusado("credencial só pode ser local")
    tempo = quadro.get("timeout_s", 20)
    if (
        isinstance(tempo, bool)
        or not isinstance(tempo, (float, int))
        or not math.isfinite(tempo)
        or not 0 < tempo <= 30
    ):
        raise Recusado("timeout fora de 0–30 segundos")
    if not any(metodo == m and padrao.fullmatch(rota) for m, padrao in _PADROES[alvo]):
        raise Recusado("rota ou método não permitido")
    return base + rota
