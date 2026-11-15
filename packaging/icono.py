"""Dibuja el icono de OpenLaps: «OL» en itálica de carreras, con chaflanes y un tajo diagonal en cada
letra; la O en el amarillo de la raya central y la L en blanco, con contorno oscuro para que se lea
igual sobre la barra de tareas clara u oscura.

    python packaging/icono.py   ->  openlaps/gui/openlaps.ico y openlaps/gui/web/icono.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

RAIZ = Path(__file__).resolve().parents[1]
L = 1024                      # lienzo de trabajo; se reduce al final
K = 0.38                      # inclinación
G, C = 120, 70                # grosor del trazo y chaflán
X0, X1, Y0, Y1 = 140, 550, 210, 815   # caja de la O
XL = 615                      # trazo vertical de la L
AMARILLO, AMARILLO_OSC = (232, 178, 30), (176, 122, 10)
BLANCO, BLANCO_OSC = (240, 238, 232), (168, 166, 160)
OSCURO, VACIO = (21, 23, 26), (0, 0, 0, 0)


def _poli(d, pts, color):
    d.polygon([(x + K * (L / 2 - y), y) for x, y in pts], fill=color)


def dibujar():
    im = Image.new("RGBA", (L, L), VACIO)
    d = ImageDraw.Draw(im)
    # O: esquinas cortadas arriba-izquierda y abajo-derecha, sombra abajo y tajo en el trazo izquierdo
    _poli(d, [(X0 + C, Y0), (X1, Y0), (X1, Y1 - C), (X1 - C, Y1), (X0, Y1), (X0, Y0 + C)], AMARILLO)
    _poli(d, [(X0, Y1 - 34), (X1 - C + 34, Y1 - 34), (X1 - C, Y1), (X0, Y1)], AMARILLO_OSC)
    _poli(d, [(X0 + G, Y0 + G), (X1 - G, Y0 + G), (X1 - G, Y1 - G), (X0 + G, Y1 - G)], VACIO)
    _poli(d, [(X0 - 5, 600), (X0 + G + 5, 520), (X0 + G + 5, 570), (X0 - 5, 650)], VACIO)
    # L: mismos chaflanes, partida por un tajo diagonal paralelo al de la O
    _poli(d, [(XL + C, Y0), (XL + G, Y0), (XL + G, 420), (XL, 500), (XL, Y0 + C)], BLANCO)
    _poli(d, [(XL + G, 470), (XL + G, Y1 - G), (930, Y1 - G), (930, Y1 - C), (930 - C, Y1), (XL, Y1), (XL, 550)], BLANCO)
    _poli(d, [(XL, Y1 - 34), (930 - C + 34, Y1 - 34), (930 - C, Y1), (XL, Y1)], BLANCO_OSC)
    # contorno oscuro
    alfa = im.split()[3].filter(ImageFilter.MaxFilter(53))
    base = Image.new("RGBA", im.size, OSCURO + (0,))
    base.putalpha(alfa)
    base.alpha_composite(im)
    return base


if __name__ == "__main__":
    im = dibujar()
    ico = RAIZ / "openlaps" / "gui" / "openlaps.ico"
    im.resize((256, 256), Image.LANCZOS).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    im.resize((128, 128), Image.LANCZOS).save(RAIZ / "openlaps" / "gui" / "web" / "icono.png")
    print(ico)
