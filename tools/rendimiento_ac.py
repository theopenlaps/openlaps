"""Estima lo que le cuesta a Assetto Corsa dibujar una pista: llamadas de dibujo y triángulos a la vista.

AC dibuja cada malla del KN5 por separado (una llamada por malla y por pasada) y solo descarta las que quedan
fuera del campo de visión o más lejos que su `lodOut`; no hay oclusión. En una pista grande manda el número de
mallas a la vista (CPU), no los triángulos. Este script lo mide desde puntos repartidos por la calzada, mirando
en 8 direcciones, y cuenta aparte lo que entra en las sombras (mallas que las proyectan cerca de la cámara).

Uso: python tools/rendimiento_ac.py pista.kn5 [otra.kn5 …]
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

import numpy as np

FOV_H = math.radians(95.0)      # campo de visión horizontal (16:9 con el FOV vertical habitual de AC)
SOMBRAS_M = 380.0               # alcance de la última cascada de sombras


def leer(kn5):
    aqui = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory() as d:
        j = os.path.join(d, "k.json")
        subprocess.run([sys.executable, os.path.join(aqui, "kn5_inspect.py"), kn5, "--json", j], check=True,
                       stdout=subprocess.DEVNULL)
        with open(j) as f:
            return json.load(f)


def grupo(nombre):
    n = re.sub(r"^\d+[A-Z]+_", "", nombre)
    return n.split("_")[0]


def medir(datos, max_puntos=160):
    ms = [m for m in datos["meshes"] if m["rend"] and m["vis"] and grupo(m["name"]) != "biblioteca"]
    c = np.array([m["bs"][:3] for m in ms], float)
    r = np.array([m["bs"][3] for m in ms], float)
    lod = np.array([m["lod"][1] for m in ms], float)
    tri = np.array([m["t"] for m in ms], float)
    cast = np.array([m["cast"] for m in ms], bool)
    blend = {m["name"] for m in datos["materials"] if m["blend"]}
    opaco = np.array([m["mat"] not in blend for m in ms], bool)
    via = np.array([bool(re.match(r"^\d+ROAD_", m["name"])) for m in ms])
    pts = c[via] if via.any() else c
    if len(pts) > max_puntos:
        pts = pts[np.linspace(0, len(pts) - 1, max_puntos).astype(int)]
    pts = pts + [0, 1.2, 0]
    dc, tr, so = [], [], []
    medio = FOV_H / 2
    for p in pts:
        d = c - p
        dist = np.linalg.norm(d, axis=1)
        cerca = (lod <= 0) | (dist - r < lod)
        so.append(int((cast & opaco & cerca & (dist - r < SOMBRAS_M)).sum()))
        ang_r = np.arcsin(np.clip(r / np.maximum(dist, 1e-6), 0, 1))
        dentro = dist < r
        az = np.arctan2(d[:, 2], d[:, 0])
        for k in range(8):
            h = k * math.pi / 4
            da = np.abs((az - h + math.pi) % (2 * math.pi) - math.pi)
            ve = cerca & (dentro | (da < medio + ang_r))
            dc.append(int(ve.sum()))
            tr.append(float(tri[ve].sum()))
    por = defaultdict(lambda: [0, 0])
    for m in ms:
        g = por[grupo(m["name"])]
        g[0] += 1
        g[1] += m["t"]
    tex = sum(t["size"] for t in datos["textures"]) / 1e6
    return {"mallas": len(ms), "triangulos": int(tri.sum()), "materiales": len(datos["materials"]),
            "texturas": len(datos["textures"]), "texturas_mb": round(tex),
            "dibujo_mediana": int(np.median(dc)), "dibujo_p95": int(np.percentile(dc, 95)),
            "tri_mediana": int(np.median(tr)), "tri_p95": int(np.percentile(tr, 95)),
            "sombras_mediana": int(np.median(so)), "sombras_p95": int(np.percentile(so, 95)),
            "grupos": dict(sorted(por.items(), key=lambda kv: -kv[1][0]))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kn5", nargs="+")
    ap.add_argument("--grupos", action="store_true", help="desglose por grupo de malla")
    a = ap.parse_args()
    for k in a.kn5:
        m = medir(leer(k))
        print(f"{os.path.basename(k)}: {m['mallas']} mallas, {m['triangulos'] / 1e6:.2f} M triángulos, "
              f"{m['materiales']} materiales, {m['texturas']} texturas ({m['texturas_mb']} MB)")
        print(f"  a la vista: {m['dibujo_mediana']} llamadas (p95 {m['dibujo_p95']}), "
              f"{m['tri_mediana'] / 1e6:.2f} M triángulos (p95 {m['tri_p95'] / 1e6:.2f} M); "
              f"en sombras: {m['sombras_mediana']} mallas (p95 {m['sombras_p95']})")
        if a.grupos:
            for g, (n, t) in m["grupos"].items():
                print(f"    {g:<18} {n:>5} mallas {t:>10,} triángulos")


if __name__ == "__main__":
    main()
