"""Busca defectos geométricos en un .glb: superficies que deberían ser casi planas (calzada,
banqueta, cruces) con triángulos muy inclinados (esquirlas, losas torcidas) y calles con
quiebres bruscos de pendiente. Saca una lista de los peores sitios y, con --vistas, una hoja de
renders de cerca de cada uno.

  python tools/defectos.py salida/madrid_opera.glb
  python tools/defectos.py salida/madrid_opera.glb --vistas salida/defectos_opera.jpg --n 12
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(__file__))
from render import load_glb, render  # noqa: E402

PLANAS = ("calzada", "banqueta_banqueta", "cruce", "asfalto")
INCL_MAX = 0.35        # pendiente transversal/longitudinal razonable en una calle: < ~19°


def triangulos(meshes, claves, excluir=("guarnicion",)):
    tris, nombres = [], []
    for m in meshes:
        n = m["name"]
        if not any(k in n for k in claves) or any(k in n for k in excluir):
            continue
        p = m["pos"][m["idx"]]
        tris.append(p)
        nombres += [n] * len(p)
    return (np.concatenate(tris) if tris else np.zeros((0, 3, 3))), np.array(nombres)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb")
    ap.add_argument("--vistas", help="hoja con renders de cerca de los peores sitios")
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args()
    meshes, texs, dummies, extras = load_glb(a.glb)
    T, nombres = triangulos(meshes, PLANAS)
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    nrm = np.cross(e1, e2)
    area = np.linalg.norm(nrm, axis=1) / 2
    ok = area > 1e-4
    T, nombres, nrm, area = T[ok], nombres[ok], nrm[ok], area[ok]
    ny = np.abs(nrm[:, 1]) / np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)
    pend = np.sqrt(np.maximum(1 - ny ** 2, 0)) / np.maximum(ny, 1e-6)      # tan(inclinación)
    # las caras casi verticales son faldones a propósito (tapan la junta con el terreno); una
    # losa torcida es una cara que debería ser horizontal y está inclinada, sin llegar a pared
    plana = ny > 0.2
    T, nombres, pend, area = T[plana], nombres[plana], pend[plana], area[plana]
    malos = pend > INCL_MAX
    print(f"{len(T):,} triángulos de superficie (sin faldones); {malos.sum():,} con pendiente > {INCL_MAX:.0%} "
          f"({area[malos].sum():.0f} m² de {area.sum():.0f} m²)")
    por_tipo = {}
    for k in PLANAS:
        sel = np.char.find(nombres.astype(str), k) >= 0
        if sel.any():
            por_tipo[k] = (int((malos & sel).sum()), int(sel.sum()))
    print("  por tipo (malos / total):", por_tipo)
    # agrupar los malos en sitios de 6 m
    c = T[malos].mean(1)
    sitios = []
    if len(c):
        tree = cKDTree(c[:, [0, 2]])
        usado = np.zeros(len(c), bool)
        orden = np.argsort(-pend[malos])
        for i in orden:
            if usado[i]:
                continue
            vec = tree.query_ball_point(c[i, [0, 2]], 6.0)
            usado[vec] = True
            sitios.append((len(vec), float(pend[malos][i]), c[i], nombres[malos][i]))
        sitios.sort(key=lambda s: -s[0])
    # quiebres de rasante: diferencia de altura entre vértices de calzada muy cercanos en planta
    R, _ = triangulos(meshes, ("calzada",))
    if len(R):
        nr = np.cross(R[:, 1] - R[:, 0], R[:, 2] - R[:, 0])
        R = R[np.abs(nr[:, 1]) / np.maximum(np.linalg.norm(nr, axis=1), 1e-12) > 0.2]
    V = R.reshape(-1, 3)
    escalones = 0
    if len(V):
        tv = cKDTree(V[:, [0, 2]])
        pares = tv.query_pairs(0.05, output_type="ndarray")
        if len(pares):
            dz = np.abs(V[pares[:, 0], 1] - V[pares[:, 1], 1])
            escalones = int((dz > 0.08).sum())
    print(f"  escalones en la calzada (vértices a < 5 cm en planta con > 8 cm de desnivel): {escalones:,}")
    lat0, lon0 = extras.get("centro", [0, 0])
    for k, (n, p, cc, nom) in enumerate(sitios[:a.n]):
        print(f"  {k + 1:2d}. {n:4d} tri, pendiente máx {p:.1f}  en x={cc[0]:.1f} z={cc[2]:.1f} (y={cc[1]:.1f})  [{nom}]")
    if a.vistas and sitios:
        cells = []
        for n, p, cc, nom in sitios[:a.n]:
            eye = cc + np.array([7.0, 3.5, 7.0])
            img = render(meshes, texs, eye, cc, 480, 300, 60)
            ImageDraw.Draw(img).text((5, 5), f"{n} tri  pend {p:.1f}  {nom[:40]}", fill=(255, 255, 0))
            cells.append(img)
        cols = 2
        rows = (len(cells) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * 480, rows * 300))
        for i, im in enumerate(cells):
            sheet.paste(im, ((i % cols) * 480, (i // cols) * 300))
        sheet.save(a.vistas, quality=88)
        print("ok", a.vistas)


if __name__ == "__main__":
    main()
