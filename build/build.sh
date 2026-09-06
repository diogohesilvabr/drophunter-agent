#!/usr/bin/env bash
# Gera agent/dist/drophunter-agent (Linux). Rode de dentro de agent/ com o venv ativo
# ou passe o python: PY=.venv/bin/python build/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-python3}"
"$PY" -m PyInstaller build/drophunter-agent.spec --distpath dist --workpath build/_work --noconfirm --clean
ls -la dist/drophunter-agent*
./dist/drophunter-agent --version
