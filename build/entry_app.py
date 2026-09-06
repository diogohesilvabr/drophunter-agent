"""Ponto de entrada do app com janela (sem console) — ver ``build/drophunter-app.spec``.

Equivale a ``python -m drophunter_agent`` sem argumentos: chama ``main_app()`` da frente W1
(assistente na primeira vez, bandeja depois). O ``entry.py`` ao lado continua sendo o da
versao de linha de comando.
"""

import multiprocessing
import sys

from drophunter_agent.app import main_app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main_app())
