"""Logo do DropHunter para o build: onde achar e como tirar so o emblema.

Usado por ``gerar_ico.py`` (icone do .exe e dos atalhos) e ``gerar_bmp.py`` (imagens do
assistente do Inno Setup). Fica separado porque os dois precisam da MESMA logo: se cada
script escolhesse a sua, o instalador e o icone acabariam com desenhos diferentes.

A logo cheia e um lockup (emblema + a palavra "DropHunter"). Em 16x16 ou 55x55 a palavra
vira borrao, entao para tamanho pequeno se usa so o emblema — recortado achando a faixa
transparente que separa os dois, e nao por numero fixo (a logo pode ser trocada).
"""

from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent  # agent/
CANDIDATAS = [
    RAIZ / "drophunter_agent" / "ui" / "assets" / "logo.png",
    AQUI / "logo.png",
]


def achar_logo() -> Path:
    for c in CANDIDATAS:
        if c.is_file():
            return c
    raise SystemExit("nao achei a logo: esperava " + " ou ".join(str(c) for c in CANDIDATAS))


def abrir(origem: Path | None = None):
    from PIL import Image  # importado tarde: so o build precisa do Pillow

    return Image.open(origem or achar_logo()).convert("RGBA")


def emblema(img):
    """Recorta a parte de cima da logo, ate a primeira faixa transparente."""
    alfa = img.getchannel("A")
    largura, altura = img.size
    corte = altura
    visto = False
    for y in range(altura):
        cheia = sum(alfa.crop((0, y, largura, y + 1)).getdata()) > 200
        if cheia:
            visto = True
        elif visto:
            corte = y
            break
    recorte = img.crop((0, 0, largura, corte))
    caixa = recorte.getbbox()
    return recorte.crop(caixa) if caixa else recorte


def quadrado(img):
    """Centraliza numa moldura quadrada transparente (o Windows deforma icone torto)."""
    from PIL import Image

    lado = max(img.size)
    quadro = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quadro.paste(img, ((lado - img.width) // 2, (lado - img.height) // 2), img)
    return quadro
