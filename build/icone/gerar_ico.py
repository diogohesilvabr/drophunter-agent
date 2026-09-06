"""Gera ``build/icone/drophunter.ico`` (multi-resolucao) a partir da logo PNG.

O ``.ico`` NAO entra no git (e derivado, e ``agent/build/*/`` esta no .gitignore da raiz);
o workflow roda este script antes do PyInstaller e do Inno Setup. A ``logo.png`` ao lado
esta versionada a forca (``git add -f``) — se acrescentar arquivo nesta pasta, force
tambem, senao o ignore da raiz engole.

Usa so o EMBLEMA da logo (ver ``marca.py``): num icone de 16x16 a palavra "DropHunter"
vira borrao cinza, e icone borrado e a primeira coisa que faz o produto parecer amador.

Fonte da logo, em ordem: ``drophunter_agent/ui/assets/logo.png`` (a copia que a frente W1
poe dentro do pacote) e, se ela ainda nao existir, ``build/icone/logo.png``.

Uso (da raiz de agent/):  python build/icone/gerar_ico.py [saida.ico]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from marca import abrir, achar_logo, emblema, quadrado  # noqa: E402

TAMANHOS = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

AQUI = Path(__file__).resolve().parent
SAIDA_PADRAO = AQUI / "drophunter.ico"


def gerar(origem: Path, destino: Path) -> Path:
    quadro = quadrado(emblema(abrir(origem)))
    destino.parent.mkdir(parents=True, exist_ok=True)
    quadro.save(destino, format="ICO", sizes=TAMANHOS)
    return destino


if __name__ == "__main__":
    destino = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SAIDA_PADRAO
    origem = achar_logo()
    gerar(origem, destino)
    print(f"icone gerado: {destino} (a partir de {origem})")
