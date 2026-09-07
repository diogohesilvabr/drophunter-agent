"""Atualização pedida pelo cliente; nenhum download nasce de aviso do servidor."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from drophunter_agent import __version__

PAINEL = "https://www.drophunter.com.br/painel/"
RELEASE = "/diogohesilvabr/drophunter-agent/releases/download/"
HOSTS = {"github.com", "objects.githubusercontent.com"}


def versao(valor):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?", str(valor))
    if not m:
        raise ValueError("Versão inválida")
    return (*map(int, m.group(1, 2, 3)), {None: 3, "a": 0, "b": 1, "rc": 2}[m[4]], int(m[5] or 0))


def validar_url(url):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.hostname not in HOSTS
        or p.username
        or p.password
        or p.port not in (None, 443)
        or p.fragment
        or "\\" in url
    ):
        raise ValueError("Endereço de atualização recusado")
    return p


def metadados(quadro):
    numero = quadro["version"]
    versao(numero)
    url = quadro["url"]
    p = validar_url(url)
    prefixo = RELEASE + "v" + numero + "/"
    nome = p.path.removeprefix(prefixo)
    if (
        p.hostname != "github.com"
        or not p.path.startswith(prefixo)
        or p.query
        or nome not in {"DropHunter-Setup.exe", f"DropHunter-Setup-{numero}.exe"}
    ):
        raise ValueError("Use o instalador da release oficial pinada")
    sha = quadro["sha256"]
    if not isinstance(sha, str) or not re.fullmatch("[a-fA-F0-9]{64}", sha):
        raise ValueError("Servidor ainda não publicou a conferência desta versão")
    return dict(
        version=numero,
        url=url,
        sha256=sha.lower(),
        nome=nome,
        manifesto=urljoin(url, "SHA256SUMS.txt"),
    )


def iniciar_instalador(arquivo: Path, sha: str):
    """PowerShell do Windows sobrevive ao fechamento do app pelo Restart Manager.

    Não usa shell, RunAs nem credenciais. Caminhos são literais PowerShell escapados;
    o hash é conferido novamente no processo independente antes de executar o arquivo.
    """
    if platform.system() != "Windows" or not getattr(sys, "frozen", False):
        raise OSError("Instalação automática exige o app Windows empacotado")

    def literal(texto):
        return "'" + str(texto).replace("'", "''") + "'"

    exe = Path(sys.executable).resolve()
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    codigo = f"""
$ErrorActionPreference = 'Stop'
$arquivo = {literal(arquivo)}
$app = {literal(exe)}
$codigo = 1
$iniciou = $false
try {{
    if ((Get-FileHash -LiteralPath $arquivo -Algorithm SHA256).Hash -ne {literal(sha)}) {{
        throw 'Arquivo de atualização divergente'
    }}
    $iniciou = $true
    $opcoes = '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'
    $processo = Start-Process -FilePath $arquivo -ArgumentList $opcoes -PassThru -Wait
    $codigo = $processo.ExitCode
}} catch {{ $codigo = 1 }} finally {{
    if ($iniciou) {{
        try {{
            Start-Process -FilePath $app -WorkingDirectory {literal(exe.parent)}
        }} catch {{ $codigo = 1 }}
    }}
    Remove-Item -LiteralPath {literal(arquivo.parent)} -Recurse -Force -ErrorAction SilentlyContinue
}}
exit $codigo
"""
    codificado = base64.b64encode(codigo.encode("utf-16le")).decode("ascii")
    return subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


class Atualizacao:
    def __init__(
        self,
        canal,
        *,
        versao=__version__,
        sistema=None,
        transport=None,
        iniciar_instalador=iniciar_instalador,
        pasta_temp=None,
    ):
        self.canal = canal
        self.versao = versao
        self.sistema = sistema or platform.system()
        self.transport = transport
        self.iniciar_instalador = iniciar_instalador
        self.pasta_temp = pasta_temp
        self.oferta = None
        self._aviso = None
        self.ocupado = False
        self.estado = dict(mensagem="", acao="procurar", url="", erro=False, ocupado=False)

    def _estado(self, mensagem, *, acao="procurar", url="", erro=False):
        self.estado = dict(mensagem=mensagem, acao=acao, url=url, erro=erro, ocupado=self.ocupado)

    def _oferecer(self, quadro):
        if versao(quadro["version"]) <= versao(self.versao):
            self.oferta = None
            self._estado(f"Você já está na versão mais recente ({self.versao}).")
            return
        self.oferta = metadados(quadro)
        if self.sistema != "Windows":
            self._estado(
                "Atualização automática só no Windows; baixe o binário novo pelo painel.",
                url=self.oferta["url"],
            )
        else:
            self._estado(
                f"Versão {self.oferta['version']} disponível. Baixar e instalar.",
                acao="instalar",
                url=self.oferta["url"],
            )

    def instantaneo(self):
        quadro = getattr(self.canal(), "atualizacao", None)
        if quadro and quadro != self._aviso and not self.ocupado:
            self._aviso = dict(quadro)
            try:
                self._oferecer(quadro)
            except (ValueError, KeyError, TypeError):
                self.oferta = None
                self._estado(
                    "Há um aviso de atualização. Procure a versão ou baixe pelo painel.", url=PAINEL
                )
                self.estado["destaque"] = True
        return dict(self.estado, ocupado=self.ocupado)

    async def procurar(self):
        if self.ocupado:
            return
        self.oferta = None
        canal = self.canal()
        if not canal or not canal.online:
            self._estado(
                "Agente offline. Reconecte para procurar atualização.", erro=True, url=PAINEL
            )
            return
        self.ocupado = True
        self._estado("Procurando atualização…")
        try:
            quadro = await canal.consultar_atualizacao()
            self._oferecer(quadro)
        except Exception:  # resposta/timeout/rede não podem parar o agente
            self._estado(
                "Não consegui consultar a versão. Tente de novo ou baixe pelo painel.",
                erro=True,
                url=PAINEL,
            )
        finally:
            self.ocupado = False
            self.estado["ocupado"] = False

    async def _baixar(self, cliente, url, destino, limite, progresso=False):
        for _ in range(6):
            validar_url(url)
            async with cliente.stream("GET", url) as resposta:
                if resposta.is_redirect:
                    url = urljoin(url, resposta.headers["location"])
                    continue
                resposta.raise_for_status()
                total = int(resposta.headers.get("content-length", 0))
                if total > limite:
                    raise ValueError("Arquivo grande demais")
                tamanho = 0
                with destino.open("wb") as saida:
                    async for parte in resposta.aiter_bytes(65536):
                        tamanho += len(parte)
                        if tamanho > limite:
                            raise ValueError("Arquivo grande demais")
                        saida.write(parte)
                        if progresso:
                            percentual = min(100, tamanho * 100 // total) if total else None
                            self._estado(
                                f"Baixando… {percentual}%"
                                if percentual is not None
                                else "Baixando…",
                                acao="aguardar",
                            )
                return
        raise ValueError("Redirecionamentos demais")

    async def instalar(self):
        if self.ocupado or not self.oferta or self.sistema != "Windows":
            return
        self.ocupado = True
        pasta = None
        try:
            oferta = dict(self.oferta)
            pasta = Path(tempfile.mkdtemp(prefix="drophunter-update-", dir=self.pasta_temp))
            arquivo = pasta / "DropHunter-Setup.exe"
            manifesto = pasta / "SHA256SUMS.txt"
            self._estado("Baixando… 0%", acao="aguardar")
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False, trust_env=False, timeout=30
            ) as cliente:
                async with asyncio.timeout(900):
                    await self._baixar(cliente, oferta["manifesto"], manifesto, 65536)
                    linhas = [linha.split() for linha in manifesto.read_text().splitlines()]
                    hashes = [
                        p[0].lower()
                        for p in linhas
                        if len(p) == 2 and p[1].lstrip("*") == oferta["nome"]
                    ]
                    if hashes != [oferta["sha256"]]:
                        raise ValueError("Manifesto divergente")
                    await self._baixar(cliente, oferta["url"], arquivo, 512 * 1024 * 1024, True)
            self._estado("Conferindo o arquivo…", acao="aguardar")

            def conferir():
                with arquivo.open("rb") as entrada:
                    return hashlib.file_digest(entrada, "sha256").hexdigest()

            if await asyncio.to_thread(conferir) != oferta["sha256"]:
                raise ValueError("SHA-256 divergente")
            processo = self.iniciar_instalador(arquivo, oferta["sha256"])
            # O supervisor agora possui a pasta. Fechar a janela não pode apagar o
            # instalador antes que o processo independente termine de usá-lo.
            pasta_instalacao, pasta = pasta, None
            self._estado("Instalando. O programa vai fechar e abrir sozinho.", acao="aguardar")
            if processo is not None:
                while processo.poll() is None:
                    await asyncio.sleep(0.5)
                if processo.returncode != 0:
                    shutil.rmtree(pasta_instalacao, ignore_errors=True)
                    raise OSError("Instalador falhou")
                self._estado("Atualização instalada. Reabrindo o programa.", acao="aguardar")
                shutil.rmtree(pasta_instalacao, ignore_errors=True)
        except Exception:  # nenhuma falha de atualização encerra canal, proxy ou feed
            self.oferta = None
            self._estado(
                "Não consegui baixar, conferir ou instalar. Tente de novo ou baixe pelo painel.",
                erro=True,
                url=PAINEL,
            )
        finally:
            if pasta:
                shutil.rmtree(pasta, ignore_errors=True)
            self.ocupado = False
            self.estado["ocupado"] = False
