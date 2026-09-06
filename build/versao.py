"""Imprime a versao do agente lendo ``drophunter_agent/__init__.py``.

Existe para o workflow e o Inno Setup nao duplicarem o numero: a versao mora em UM
lugar so. Le como texto de proposito — importar o pacote exigiria as dependencias
instaladas, e este script roda antes do ``pip install``.

Uso (da raiz de agent/):  python build/versao.py
"""

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
INIT = RAIZ / "drophunter_agent" / "__init__.py"


def versao() -> str:
    texto = INIT.read_text(encoding="utf-8")
    achado = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", texto, re.MULTILINE)
    if not achado:
        raise SystemExit(f"nao achei __version__ em {INIT}")
    return achado.group(1)


if __name__ == "__main__":
    sys.stdout.write(versao() + "\n")
