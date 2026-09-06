# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: binario unico 'drophunter-agent' (Linux) / 'drophunter-agent.exe' (Windows).

Uso (da raiz de agent/):
    pyinstaller build/drophunter-agent.spec --distpath dist --workpath build/_work --noconfirm

Sem segredo nenhum aqui: a config vem de ~/.drophunter/agent.toml em tempo de execucao.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

RAIZ = os.path.dirname(os.path.abspath(SPECPATH))  # agent/
ENTRADA = os.path.join(SPECPATH, "entry.py")

# python-socketio/engineio carregam drivers por nome em runtime; garante que o
# cliente asyncio (aiohttp) entre no binario.
ocultos = sorted(
    set(collect_submodules("engineio"))
    | set(collect_submodules("socketio"))
    | {"aiohttp", "aiohttp.client_ws", "socketio.async_client", "engineio.async_client",
       "engineio.async_drivers.aiohttp", "drophunter_agent.canal", "drophunter_agent.proxy",
       "drophunter_agent.empire_ws", "drophunter_agent.listabranca"}
)

a = Analysis(
    [ENTRADA],
    pathex=[RAIZ],
    binaries=[],
    datas=[],
    hiddenimports=ocultos,
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
    a.binaries,
    a.datas,
    [],
    name="drophunter-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != "win32",
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
