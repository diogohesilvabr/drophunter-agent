"""Gera as imagens do assistente do Inno Setup a partir da logo PNG.

Saidas em ``build/icone/`` (derivadas, fora do git — o workflow gera antes do ``iscc``):

    wizard-grande.bmp     164x314   painel das telas de Bem-vindo e Concluir
    wizard-grande-2x.bmp  328x628   a mesma, pra tela com DPI alto
    wizard-pequeno.bmp     55x55    canto das telas do meio
    wizard-pequeno-2x.bmp 110x110

BMP de 24 bits porque e o unico formato que o Inno 6 aceita nessas duas diretivas; por
isso o fundo e chapado em vez de transparente. As cores sao as do app da W1
(``drophunter_agent/ui/app.css``): fundo #0B0F17 -> #131A24 e o laranja #EF7D2B da marca.

Uso (da raiz de agent/):  python build/icone/gerar_bmp.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from marca import abrir, achar_logo, emblema  # noqa: E402

AQUI = Path(__file__).resolve().parent

FUNDO_TOPO = (11, 15, 23)
FUNDO_BASE = (19, 26, 36)
LARANJA = (239, 125, 43)

#: (nome, largura, altura, fracao da largura ocupada pela arte, painel alto)
#: painel alto = logo inteira no terco superior e faixa laranja no pe; o resto e o
#: quadradinho do canto, onde so cabe o emblema.
IMAGENS = [
    ("wizard-grande.bmp", 164, 314, 0.62, True),
    ("wizard-grande-2x.bmp", 328, 628, 0.62, True),
    ("wizard-pequeno.bmp", 55, 55, 0.74, False),
    ("wizard-pequeno-2x.bmp", 110, 110, 0.74, False),
]


def gerar(origem: Path, destino: Path, largura: int, altura: int, fracao: float, painel: bool):
    from PIL import Image  # importado tarde: so o build precisa do Pillow

    quadro = Image.new("RGB", (largura, altura), FUNDO_TOPO)
    pixels = quadro.load()
    for y in range(altura):
        t = y / max(altura - 1, 1)
        cor = tuple(round(a + (b - a) * t) for a, b in zip(FUNDO_TOPO, FUNDO_BASE, strict=True))
        for x in range(largura):
            pixels[x, y] = cor
    if painel:
        alto = max(2, altura // 100)
        for y in range(altura - alto, altura):
            for x in range(largura):
                pixels[x, y] = LARANJA

    logo = abrir(origem)
    if not painel:
        logo = emblema(logo)
    alvo = max(1, round(largura * fracao))
    escala = alvo / logo.width
    logo = logo.resize((alvo, max(1, round(logo.height * escala))), Image.LANCZOS)
    # na imagem alta a logo fica no terco superior; na quadrada, centralizada
    topo = round(altura * 0.16) if painel else (altura - logo.height) // 2
    quadro.paste(logo, ((largura - logo.width) // 2, topo), logo)
    quadro.save(destino, format="BMP")
    return destino


if __name__ == "__main__":
    origem = achar_logo()
    for nome, largura, altura, fracao, painel in IMAGENS:
        saida = gerar(origem, AQUI / nome, largura, altura, fracao, painel)
        print(f"imagem gerada: {saida} ({largura}x{altura}, a partir de {origem})")
