"""Vistas del conductor a lo largo de la ruta de un .glb (modo tramo/circuito), en una hoja.

Sirve para comparar 1 a 1 con capturas de referencia (p. ej. un video de la misma carretera):
  python tools/comparar_ruta.py salida/m118_cobena.glb salida/m118_vistas.jpg --cada 150
  python tools/comparar_ruta.py escena.glb hoja.jpg --cada 150 --referencia ref.jpg   (lado a lado)
La cámara va a 1.2 m sobre la calzada, en el carril derecho, mirando 35 m adelante.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from render import load_glb, render  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--cada", type=float, default=150.0, help="metros entre vistas")
    ap.add_argument("--desde", type=float, default=0.0)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--ancho", type=int, default=480)
    ap.add_argument("--carril", type=float, default=1.7, help="desplazamiento a la derecha del eje (m)")
    ap.add_argument("--referencia", help="hoja de referencia con el mismo número de celdas, para ponerla al lado")
    ap.add_argument("--distancias", help="lista de distancias exactas (m) separadas por coma; sustituye a --cada/--n")
    ap.add_argument("--fov", type=float, default=62.0, help="campo de visión horizontal (90 para comparar con GoPro)")
    a = ap.parse_args()
    meshes, texs, dummies, extras = load_glb(a.glb)
    ruta = np.asarray(extras.get("ruta") or [], float)
    if len(ruta) < 2:
        sys.exit("el .glb no tiene ruta (genera en modo tramo o circuito)")
    seg = np.linalg.norm(np.diff(ruta[:, [0, 2]], axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    W, H = a.ancho, int(a.ancho * 0.5625)
    cells = []
    dists = ([float(x) for x in a.distancias.split(",")] if a.distancias
             else [a.desde + k * a.cada for k in range(a.n)])
    for d in dists:
        if d > s[-1] - 40:
            break
        p = np.array([np.interp(d, s, ruta[:, i]) for i in range(3)])
        q = np.array([np.interp(d + 35, s, ruta[:, i]) for i in range(3)])
        f = q - p
        f[1] = 0
        f /= max(np.linalg.norm(f), 1e-9)
        right = np.array([-f[2], 0, f[0]])     # glTF: x este, y arriba, z sur
        eye = p + right * a.carril + np.array([0, 1.2, 0])
        target = q + right * a.carril + np.array([0, 1.0, 0])
        img = render(meshes, texs, eye, target, W, H, a.fov)
        ImageDraw.Draw(img).text((6, 6), f"{d:.0f} m", fill=(255, 255, 0))
        cells.append(img)
        print(f"  vista a {d:.0f} m", flush=True)
    cols = a.cols
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * W, rows * H))
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * W, (i // cols) * H))
    if a.referencia:
        ref = Image.open(a.referencia).convert("RGB").resize(sheet.size)
        both = Image.new("RGB", (sheet.width * 2, sheet.height))
        both.paste(ref, (0, 0))
        both.paste(sheet, (sheet.width, 0))
        sheet = both
    sheet.save(a.out, quality=88)
    print("ok", a.out)


if __name__ == "__main__":
    main()
