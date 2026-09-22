"""Sessões HTTP do agente (4.0.7): uma por finalidade, viva pelo processo.

Antes cada reconexão podia deixar socket para trás; agora o conector é único por
finalidade (canal do gateway, socket do Empire, teste de licença) com limites
pequenos, cache de DNS e limpeza de transporte TLS que o outro lado fechou.
"""

from __future__ import annotations

import aiohttp
import aiohttp.connector

#: Conexões por conector: o canal usa 1 WebSocket; o socket do Empire também.
LIMITE_CONEXOES = 4
LIMITE_POR_HOST = 2
#: DNS da Cloudflare/Empire muda pouco; 5 min evita uma consulta por reconexão.
TTL_DNS_S = 300


def precisa_limpeza_tls() -> bool:
    """``enable_cleanup_closed`` só faz sentido (e só é aceito sem aviso) no Python
    que ainda tem o bug do shutdown TLS (< 3.12.8 / 3.13.0); o aiohttp expõe isso."""
    return bool(getattr(aiohttp.connector, "NEEDS_CLEANUP_CLOSED", True))


def conector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        limit=LIMITE_CONEXOES,
        limit_per_host=LIMITE_POR_HOST,
        ttl_dns_cache=TTL_DNS_S,
        enable_cleanup_closed=precisa_limpeza_tls(),
    )


def sessao(
    *, timeout_s: float, trace: aiohttp.TraceConfig | None = None
) -> aiohttp.ClientSession:
    """``ClientSession`` sem proxy do ambiente, com o conector acima.

    ``timeout_s`` vale para o handshake HTTP (o WebSocket aberto não expira por ele).
    Quem cria fecha: ``async with`` ou ``await sessao.close()`` no encerramento.
    """
    return aiohttp.ClientSession(
        connector=conector(),
        trace_configs=[trace] if trace is not None else None,
        trust_env=False,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    )
