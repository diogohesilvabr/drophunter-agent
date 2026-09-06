"""Fingerprint TOLERANTE da maquina (PROJETO.md item 4): serve pra o servidor
reconhecer 'e o mesmo PC de sempre', nao pra travar licenca em hardware.
Nada de MAC/IP. Hash de: machine-id (se houver) + hostname + SO + arquitetura + usuario."""

from __future__ import annotations

import getpass
import hashlib
import platform
import socket
from pathlib import Path


def _machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            continue
    if platform.system() == "Windows":
        try:
            import winreg  # type: ignore[import-not-found]

            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        except Exception:
            pass
    return ""


def fingerprint() -> str:
    partes = [
        _machine_id(),
        socket.gethostname() or "",
        platform.system(),
        platform.machine(),
        _usuario(),
    ]
    return hashlib.sha256("|".join(partes).encode("utf-8", "replace")).hexdigest()[:24]


def _usuario() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def descricao_so() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"
