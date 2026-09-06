"""Ponto de entrada do binario PyInstaller (equivale a `python -m drophunter_agent`)."""

import multiprocessing
import sys

from drophunter_agent.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
