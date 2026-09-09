"""Bandeja do sistema e inicialização com o Windows. Importação gráfica sempre tardia."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import time
from pathlib import Path

from drophunter_agent import __version__
from drophunter_agent.redact import REDATOR

log = logging.getLogger("drophunter.bandeja")

REGISTRO = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_REGISTRO = "DropHunter"
#: Queda menor que isto nao vira aviso na bandeja. Ordem do Diogo em 09/09: um ping
#: que nao chegou reconecta em segundos, e avisar cada piscada e so poluicao. Só
#: interessa o que ficou fora de verdade. O Telegram do cliente tem a própria
#: carência, maior (vigia do servidor).
SEGUNDOS_FORA_PARA_AVISAR = 180
#: laranja = conectado, cinza = desconectado, vermelho = licença recusada (identidade do site)
CORES = {
    "conectado": (239, 125, 43, 255),
    "desconectado": (115, 123, 135, 255),
    "recusado": (229, 72, 77, 255),
    "sem_config": (115, 123, 135, 255),
}


def cor_estado(estado) -> tuple[int, int, int, int]:
    return CORES.get(estado, CORES["desconectado"])


def _duracao(segundos) -> str:
    minutos = int(segundos) // 60
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"{horas} h" if not resto else f"{horas} h {resto} min"


def autostart_disponivel() -> bool:
    return sys.platform == "win32"


def comando_app(caminho=None) -> list[str]:
    """Linha que o Windows executa no logon: o .exe empacotado ou o módulo em dev."""
    if getattr(sys, "frozen", False):
        comando = [sys.executable]
    else:
        executavel = Path(sys.executable)
        if sys.platform == "win32" and executavel.with_name("pythonw.exe").exists():
            executavel = executavel.with_name("pythonw.exe")
        comando = [str(executavel), "-m", "drophunter_agent"]
    if caminho:
        comando += ["--config", str(Path(caminho).resolve())]
    return comando


def autostart_ativo() -> bool:
    if not autostart_disponivel():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRO, 0, winreg.KEY_READ) as chave:
            valor, _ = winreg.QueryValueEx(chave, NOME_REGISTRO)
            return bool(valor)
    except OSError:
        return False


def definir_autostart(ativo, comando=None) -> None:
    if not autostart_disponivel():
        return
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRO, 0, winreg.KEY_SET_VALUE) as chave:
        if ativo:
            winreg.SetValueEx(
                chave,
                NOME_REGISTRO,
                0,
                winreg.REG_SZ,
                subprocess.list2cmdline(comando or comando_app()),
            )
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(chave, NOME_REGISTRO)


def criar_icone(estado):
    """Logo do DropHunter recolorida em memória — nenhum .ico extra no pacote."""
    from PIL import Image, ImageOps

    with Image.open(Path(__file__).parent / "ui" / "assets" / "logo.png") as original:
        logo = original.convert("RGBA")
    logo = ImageOps.contain(logo, (56, 56))
    colorido = Image.new("RGBA", logo.size, cor_estado(estado))
    colorido.putalpha(logo.getchannel("A"))
    tela = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    tela.alpha_composite(colorido, ((64 - logo.width) // 2, (64 - logo.height) // 2))
    return tela


class Bandeja:
    """Menu do brief: Abrir DropHunter · Abrir painel · Pagamentos pendentes · Sair."""

    def __init__(self, *, abrir, painel, pagamentos, sair):
        import pystray

        self.estado = {"estado": "desconectado", "rotulo": "Desconectado", "pagamentos": 0}
        self._ultimo = None
        self._fora_desde = time.monotonic()
        self.icone = pystray.Icon(
            "DropHunter",
            criar_icone("desconectado"),
            self._titulo(),
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: self._titulo(), None, enabled=False),
                pystray.MenuItem("Abrir DropHunter", lambda: abrir(), default=True),
                pystray.MenuItem("Abrir painel", lambda: painel()),
                pystray.MenuItem(lambda item: self._rotulo_pagamentos(), lambda: pagamentos()),
                pystray.MenuItem("Sair", lambda: sair()),
            ),
        )

    def _titulo(self) -> str:
        return f"DropHunter {__version__} · {self.estado.get('rotulo', '')}"

    def _rotulo_pagamentos(self) -> str:
        pendentes = self.estado.get("pagamentos", 0)
        return f"Pagamentos pendentes ({pendentes})" if pendentes else "Pagamentos pendentes"

    def iniciar(self) -> None:
        self.icone.run_detached()

    def atualizar(self, estado) -> None:
        estado = REDATOR.estrutura(dict(estado))
        if estado == self._ultimo:
            return
        anterior = self.estado.get("estado")
        self.estado = estado
        self.icone.icon = criar_icone(estado.get("estado"))
        self.icone.title = self._titulo()
        self.icone.update_menu()
        self._ultimo = dict(estado)
        agora = time.monotonic()
        if estado.get("estado") == "conectado":
            if anterior != "conectado":
                fora = agora - (self._fora_desde if self._fora_desde is not None else agora)
                if fora >= SEGUNDOS_FORA_PARA_AVISAR:
                    self.avisar(f"DropHunter conectado · esteve fora por {_duracao(fora)}")
                self._fora_desde = None
        elif anterior == "conectado" or self._fora_desde is None:
            self._fora_desde = agora

    def avisar(self, mensagem) -> None:
        try:
            self.icone.notify(REDATOR.texto(mensagem), "DropHunter")
        except Exception:  # noqa: BLE001 - sistema sem notificação não derruba o app
            log.info("Aviso não exibido pela bandeja")

    def parar(self) -> None:
        self.icone.stop()
