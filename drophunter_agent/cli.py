"""CLI: drophunter-agent init | run | status | --version. Mensagens em pt-BR."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import os
import signal
import sys
import warnings
from pathlib import Path

from drophunter_agent import __version__
from drophunter_agent.config import (
    API_LOCAL_PORTA_PADRAO,
    API_LOCAL_USUARIO_PADRAO,
    SERVER_URL_PADRAO,
    Config,
    ConfigInvalida,
    caminho_config,
    carregar,
    garantir_senha_local,
    gerar_senha,
    mascarar,
    salvar,
    steam64_valido,
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
    p_init = sub.add_parser("init", help="cria a config local (interativo)")
    p_init.add_argument(
        "--porta-local",
        type=int,
        default=API_LOCAL_PORTA_PADRAO,
        help=f"porta da API local pra extensao Chrome (padrao {API_LOCAL_PORTA_PADRAO}; 0 desliga)",
    )
    p_init.add_argument(
        "--sem-senha-local",
        action="store_true",
        help="API local sem usuario/senha (so se voce sabe o que esta fazendo)",
    )
    sub.add_parser("run", help="roda o agente em primeiro plano")
    sub.add_parser("status", help="mostra a config (sem segredos) e testa o servidor")
    args = parser.parse_args(argv)

    if args.comando is None:
        from drophunter_agent.app import main_app

        return main_app(args.config)
    try:
        if args.comando == "init":
            return cmd_init(
                args.config,
                porta_local=args.porta_local,
                senha_local=None if args.sem_senha_local else gerar_senha(),
            )
        if args.comando == "status":
            return cmd_status(args.config)
        if args.comando == "run":
            return cmd_run(args.config)
    except ConfigInvalida as exc:
        print(REDATOR.texto(f"ERRO: {exc}"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
    return 1


# --------------------------------------------------------------- assistente
def _steam_key_valida(chave: str) -> bool:
    c = (chave or "").strip()
    return len(c) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in c)


def _pausar(rc: int) -> int:
    """No Windows, o duplo clique abre um console que some ao sair: segura a janela pra
    a pessoa LER o erro. Em terminal de verdade (Linux/servico) nao atrapalha."""
    if os.name == "nt" and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPressione Enter para fechar.")
        except EOFError:
            pass
    return rc


def _assistente(caminho: Path | None) -> int:
    destino = caminho or caminho_config()
    try:
        if not destino.exists():
            rc = cmd_init(
                caminho,
                porta_local=API_LOCAL_PORTA_PADRAO,
                senha_local=gerar_senha(),
                simples=True,
            )
            if rc != 0:
                return _pausar(rc)
        return _pausar(cmd_run(caminho))
    except ConfigInvalida as exc:
        print(REDATOR.texto(f"ERRO: {exc}"), file=sys.stderr)
        return _pausar(2)
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130


# --------------------------------------------------------------------- init
def cmd_init(
    caminho: Path | None,
    *,
    porta_local: int = API_LOCAL_PORTA_PADRAO,
    senha_local: str | None = None,
    simples: bool = False,
) -> int:
    """``simples=True`` e o assistente do duplo clique: pede TUDO que o bot precisa
    (licenca, chave do Empire, chave da Steam, Steam64 — nada opcional, decisao do Diogo);
    URL do servidor e nivel de log ficam no padrao. ``init`` explicito pergunta tambem esses."""
    caminho = caminho or caminho_config()
    print(f"DropHunter Agent {__version__} - configuracao inicial")
    if simples:
        print("Vou pedir a licenca (esta na sua conta em www.drophunter.com.br, aba Configurações)")
        print("a sua chave da API do CSGOEmpire (csgoempire.com > Settings > API Key),")
        print("a sua chave da Steam Web API (steamcommunity.com/dev/apikey) e o seu Steam ID64.")
        print("Cole cada uma e aperte Enter. Por seguranca, o que voce cola NAO aparece na tela.")
        print("Tudo fica gravado SO neste PC; o site nunca recebe a chave.\n")
    else:
        print(f"O arquivo sera gravado em {caminho} com permissao 600 (so voce le).")
        print("A chave do Empire fica SO neste PC. O servidor nunca a recebe.\n")
    if caminho.exists() and not simples:
        resp = input("Ja existe uma config. Sobrescrever? (faco backup) [s/N]: ").strip().lower()
        if resp not in ("s", "sim", "y"):
            print("Nada alterado.")
            return 0
    if simples:
        server_url = SERVER_URL_PADRAO
    else:
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
    steam = _pedir_segredo("Chave da Steam Web API (steamcommunity.com/dev/apikey): ")
    if not _steam_key_valida(steam):
        print(
            "ERRO: a chave da Steam e obrigatoria (32 caracteres, em steamcommunity.com/dev/apikey; "
            "o bot usa pra conferir entregas e recusar trade de estranho).",
            file=sys.stderr,
        )
        return 2
    steam_id64 = input("Seu Steam ID64 (17 digitos, comeca com 7656 - veja em steamid.io): ").strip()
    if not steam64_valido(steam_id64):
        print("ERRO: Steam ID64 invalido (17 digitos comecando com 7656).", file=sys.stderr)
        return 2
    log_level = "INFO" if simples else (input("Nivel de log [INFO]: ").strip().upper() or "INFO")
    cfg = Config(
        licenca=licenca,
        empire_api_key=empire,
        server_url=server_url,
        steam_api_key=steam,
        steam_id64=steam_id64,
        log_level=log_level,
        local_api_port=porta_local,
        api_local_usuario=API_LOCAL_USUARIO_PADRAO,
        api_local_senha=senha_local or "",
    )
    cfg.validar()
    gravado = salvar(cfg, caminho)
    print(f"\nConfig gravada em {gravado}.")
    _imprimir_extensao(cfg, mostrar_senha=True)
    if simples:
        print("\nPronto! Conectando ao DropHunter... (deixe esta janela aberta; feche pra parar o bot)")
    else:
        print("Proximo passo: drophunter-agent run")
    return 0


def _imprimir_extensao(cfg: Config, *, mostrar_senha: bool) -> None:
    """Instrucoes pra configurar a extensao Chrome (aceite seguro + envio na Steam)."""
    if int(cfg.local_api_port) <= 0:
        print("\nAPI local pra extensao Chrome: DESLIGADA (local_api_port = 0).")
        return
    print("\n=== Extensao Chrome DropHunter (aceite seguro + envio) ===")
    print("Nas opcoes da extensao, preencha:")
    print(f"  URL do bot:  {cfg.api_local_url}")
    if cfg.api_local_senha:
        print(f"  Usuario:     {cfg.api_local_usuario}")
        if mostrar_senha:
            print(f"  Senha:       {cfg.api_local_senha}")
        else:
            print("  Senha:       (a que esta em api_local_senha no agent.toml)")
    else:
        print("  Usuario/senha: deixe em branco (API local sem senha)")
    print("A extensao so enxerga esta API local; sua chave do Empire nunca passa por ela.")


def _pedir_segredo(rotulo: str) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(rotulo).strip()
    except (EOFError, getpass.GetPassWarning):
        raise ConfigInvalida(
            "Não foi possível ler segredo sem eco; use um terminal interativo"
        ) from None


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
    print(f"  plataforma_steam_id: {cfg.plataforma_steam_id or 'ainda não fixado'}")
    print(f"  pagamento_teto_coins: {cfg.pagamento_teto_coins}")
    if int(cfg.local_api_port) > 0:
        auth = f"usuario {cfg.api_local_usuario}, com senha" if cfg.api_local_senha else "SEM senha"
        print(f"  API local:     {cfg.api_local_url} ({auth}) - extensao Chrome")
    else:
        print("  API local:     desligada (local_api_port = 0)")
    print(f"  servidor:      {_testar_servidor(cfg.server_url)}")
    return 0


def _testar_servidor(url: str) -> str:
    """Nao chama /hello de proposito: hello derrubaria um agente que ja esteja rodando."""
    import httpx

    try:
        from urllib.parse import urlsplit, urlunsplit

        partes = urlsplit(url)
        saude = urlunsplit(
            (
                {"wss": "https", "ws": "http"}.get(partes.scheme, partes.scheme),
                partes.netloc,
                "/saude",
                "",
                "",
            )
        )
        r = httpx.get(
            saude,
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
    configurar_log(cfg.log_level)
    garantir_senha_local(cfg)
    for s in cfg.segredos():
        REDATOR.adicionar(s)
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
