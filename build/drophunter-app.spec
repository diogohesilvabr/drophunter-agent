# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: o app com janela, 'DropHunter Agent' — SEM console (Windows).

Uso (da raiz de agent/):
    python build/icone/gerar_ico.py
    pyinstaller build/drophunter-app.spec --distpath dist --workpath build/_work --noconfirm

Saida: pasta ``dist/DropHunter Agent/`` (onedir de proposito — abre rapido e nao dispara o
falso-positivo de antivirus que o onefile dispara, porque nao se desempacota em %TEMP%).
O Inno Setup (``installer/drophunter.iss``) empacota essa pasta.

Sem segredo aqui: a config vem de ~/.drophunter/agent.toml em tempo de execucao.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

RAIZ = os.path.dirname(os.path.abspath(SPECPATH))  # agent/
ENTRADA = os.path.join(SPECPATH, "entry_app.py")
ICONE = os.path.join(SPECPATH, "icone", "drophunter.ico")
UI = os.path.join(RAIZ, "drophunter_agent", "ui")

# O app e da frente W1. Se ela ainda nao commitou, o build para AQUI com a razao na cara
# em vez de gerar um .exe que abre e morre.
if not os.path.isfile(os.path.join(RAIZ, "drophunter_agent", "app.py")):
    raise SystemExit(
        "drophunter_agent/app.py nao existe: aguardando a frente W1 (app com janela + "
        "bandeja). O build da versao de linha de comando (build/drophunter-agent.spec) "
        "nao depende disso."
    )

# python-socketio/engineio carregam drivers por nome em runtime; pywebview e pystray
# escolhem o backend (GTK/Qt/EdgeChromium, Win32/AppIndicator) tambem por nome.
ocultos = set(collect_submodules("engineio")) | set(collect_submodules("socketio"))
ocultos |= {
    "aiohttp",
    "aiohttp.client_ws",
    "socketio.async_client",
    "engineio.async_client",
    "engineio.async_drivers.aiohttp",
    "drophunter_agent.canal",
    "drophunter_agent.proxy",
    "drophunter_agent.empire_ws",
    "drophunter_agent.listabranca",
    "drophunter_agent.app",
}
dados = []
for pacote in ("webview", "pystray"):
    try:
        ocultos |= set(collect_submodules(pacote))
        dados += collect_data_files(pacote)
    except Exception:  # pacote ausente: o build falha depois, com a mensagem do import
        pass
if sys.platform == "win32":
    ocultos |= {"clr_loader", "webview.platforms.edgechromium", "pystray._win32"}

# HTML/CSS/logo do assistente da W1, se ja existirem.
if os.path.isdir(UI):
    dados.append((UI, os.path.join("drophunter_agent", "ui")))

a = Analysis(
    [ENTRADA],
    pathex=[RAIZ],
    binaries=[],
    datas=dados,
    hiddenimports=sorted(ocultos),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "test", "pytest", "respx", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DropHunterAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICONE if os.path.isfile(ICONE) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DropHunter Agent",
)
