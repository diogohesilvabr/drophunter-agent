"""CLI: drophunter-agent init | run | status | --version. Mensagens em pt-BR."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import signal
import sys
from pathlib import Path

from drophunter_agent import __version__
from drophunter_agent.config import (
    SERVER_URL_PADRAO,
    Config,
    ConfigInvalida,
    caminho_config,
    carregar,
    mascarar,
    salvar,
)
from drophunter_agent.redact import REDATOR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="drophunter-agent",
        description="Agente DropHunter: guarda sua chave do Empire no seu PC e executa "
        "o que o servidor manda.",
    )
    parser.add_argument("--version", action="version", version=f"drophunter-agent {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="caminho do agent.toml (padrao: ~/.drophunter/agent.toml)",
    )
    sub = parser.add_subparsers(dest="comando")
    sub.add_parser("init", help="cria a config local (interativo)")
    sub.add_parser("run", help="roda o agente em primeiro plano")
    sub.add_parser("status", help="mostra a config (sem segredos) e testa o servidor")
    args = parser.parse_args(argv)

    if args.comando is None:
        parser.print_help()
        return 0
    try:
        if args.comando == "init":
            return cmd_init(args.config)
        if args.comando == "status":
            return cmd_status(args.config)
        if args.comando == "run":
            return cmd_run(args.config)
    except ConfigInvalida as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
    return 1


# --------------------------------------------------------------------- init
def cmd_init(caminho: Path | None) -> int:
    caminho = caminho or caminho_config()
    print(f"DropHunter Agent {__version__} - configuracao inicial")
    print(f"O arquivo sera gravado em {caminho} com permissao 600 (so voce le).")
    print("A chave do Empire fica SO neste PC. O servidor nunca a recebe.\n")
    if caminho.exists():
        resp = input("Ja existe uma config. Sobrescrever? (faco backup) [s/N]: ").strip().lower()
        if resp not in ("s", "sim", "y"):
            print("Nada alterado.")
            return 0
    server_url = input(f"URL do servidor [{SERVER_URL_PADRAO}]: ").strip() or SERVER_URL_PADRAO
    licenca = _pedir_segredo("Licenca (lic_...): ")
    if not licenca:
        print(
            "ERRO: a licenca e obrigatoria (veja na sua conta em drophunter.com.br).",
            file=sys.stderr,
        )
        return 2
    empire = _pedir_segredo("Chave da API do CSGOEmpire: ")
    if not empire:
        print(
            "ERRO: a chave do Empire e obrigatoria (csgoempire.com > Settings > API).",
            file=sys.stderr,
        )
        return 2
    steam = _pedir_segredo("Chave da Steam Web API (opcional, Enter pula): ")
    log_level = input("Nivel de log [INFO]: ").strip().upper() or "INFO"
    cfg = Config(
        licenca=licenca,
        empire_api_key=empire,
        server_url=server_url,
        steam_api_key=steam,
        log_level=log_level,
    )
    cfg.validar()
    gravado = salvar(cfg, caminho)
    print(f"\nConfig gravada em {gravado}.")
    print("Proximo passo: drophunter-agent run")
    return 0


def _pedir_segredo(rotulo: str) -> str:
    try:
        return getpass.getpass(rotulo).strip()
    except (EOFError, getpass.GetPassWarning):
        return input(rotulo).strip()


# ------------------------------------------------------------------- status
def cmd_status(caminho: Path | None) -> int:
    cfg = carregar(caminho)
    for s in cfg.segredos():
        REDATOR.adicionar(s)
    print(f"DropHunter Agent {__version__}")
    print(f"  config:        {cfg.caminho}")
    print(f"  servidor:      {cfg.server_url}")
    print(f"  licenca:       {mascarar(cfg.licenca)}")
    print(f"  chave Empire:  {'definida (fica so neste PC)' if cfg.empire_api_key else 'FALTANDO'}")
    print(f"  chave Steam:   {'definida' if cfg.steam_api_key else 'nao informada (opcional)'}")
    print(f"  log:           {cfg.log_level}")
    print(f"  servidor:      {_testar_servidor(cfg.server_url)}")
    return 0


def _testar_servidor(url: str) -> str:
    """Nao chama /hello de proposito: hello derrubaria um agente que ja esteja rodando."""
    import httpx

    try:
        r = httpx.get(
            url.rstrip("/") + "/saude",
            timeout=6.0,
            headers={"User-Agent": f"drophunter-agent/{__version__}"},
        )
        return f"alcancavel (HTTP {r.status_code})"
    except httpx.HTTPError as exc:
        return f"INALCANCAVEL ({type(exc).__name__})"


# ---------------------------------------------------------------------- run
def cmd_run(caminho: Path | None) -> int:
    from drophunter_agent.agent import Agente
    from drophunter_agent.logs import configurar_log

    cfg = carregar(caminho)
    for s in cfg.segredos():
        REDATOR.adicionar(s)
    configurar_log(cfg.log_level)
    agente = Agente(cfg)
    return asyncio.run(_rodar(agente))


async def _rodar(agente) -> int:
    loop = asyncio.get_running_loop()
    tarefa = asyncio.current_task()

    def _sinal(*_: object) -> None:
        print("\nEncerrando (Ctrl+C)...", flush=True)
        agente.pedir_encerramento()
        if tarefa is not None:
            loop.call_later(3.0, tarefa.cancel)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sinal)
        except (NotImplementedError, RuntimeError):  # Windows
            signal.signal(sig, lambda *_: _sinal())
    with contextlib.suppress(asyncio.CancelledError):
        await agente.executar()
    return 0 if agente.motivo_parada is None else 3
