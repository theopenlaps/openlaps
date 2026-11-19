"""Revisa el tráfico de una pista de AC (data/traffic.json) sin abrir el juego.

    python tools/trafico_check.py salida/assetto_corsa/pista [--png plano.png] [--zona x0,z0,x1,z1]

Une carriles y cruces como CSP (ver openlaps.trafico.revisar) e imprime el resumen. Con --png dibuja la planta:
carriles en gris con una flecha al final, carriles fantasma en rojo y cruces en azul (retornos en verde, uniones
en naranja, con semáforo en morado: los lados con verde en la primera fase van gruesos, y un punto por luz).
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from openlaps import trafico  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pista", help="carpeta de la pista o ruta a traffic.json")
    ap.add_argument("--png")
    ap.add_argument("--zona", help="x0,z0,x1,z1 en coordenadas de AC para el plano")
    ap.add_argument("--px", type=float, default=2.0, help="píxeles por metro")
    a = ap.parse_args()
    ruta = a.pista if a.pista.endswith(".json") else os.path.join(a.pista, "data", "traffic.json")
    with open(ruta, encoding="utf-8") as f:
        d = json.load(f)
    r = trafico.revisar(d)
    for k, v in r.items():
        print(f"{k}: {v}")
    km = sum(np.linalg.norm(np.diff(np.array(c["points"])[:, [0, 2]], axis=0), axis=1).sum() for c in d["lanes"]
             if not c["name"].startswith(trafico.FANTASMA)) / 1000
    print(f"km de carril: {km:.1f}; prohibidos: {sum(len(c.get('disallowedTrajectories', [])) for c in d['intersections'])}; "
          f"con prioridad de entrada: {sum(len(c.get('entryPriorityOffsets', [])) for c in d['intersections'])}")
    if not a.png:
        return
    todos = np.vstack([np.array(c["points"]) for c in d["lanes"]])
    if a.zona:
        x0, z0, x1, z1 = map(float, a.zona.split(","))
    else:
        x0, z0 = todos[:, 0].min() - 10, todos[:, 2].min() - 10
        x1, z1 = todos[:, 0].max() + 10, todos[:, 2].max() + 10
    W, H = int((x1 - x0) * a.px), int((z1 - z0) * a.px)
    img = Image.new("RGB", (W, H), (250, 250, 248))
    dr = ImageDraw.Draw(img)

    def tp(P):
        P = np.asarray(P, float)
        return [((x - x0) * a.px, (z - z0) * a.px) for x, z in P[:, [0, 2]]]
    colores = {"Cruce": (60, 110, 220), "Retorno": (40, 160, 70), "Union": (230, 140, 30)}
    for c, K in zip(d["intersections"], trafico.enlaces_csp(d)):
        pts = tp(c["points"])
        luz = c.get("trafficLight")
        dr.polygon(pts, outline=(150, 40, 190) if luz else colores.get(c["name"].split()[0], (0, 0, 0)), width=2)
        if luz:
            for k in K["lados"]:
                if k % 2 == 0:
                    dr.line([pts[k - 1], pts[k % len(pts)]], fill=(150, 40, 190), width=5)
            for s in luz["emissive"]:
                for it in (s.get("virtual") or {}).get("items", [])[::3]:
                    x, y = tp([it["pos"]])[0]
                    dr.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(220, 0, 0))
    for c in d["lanes"]:
        P = np.array(c["points"])
        q = tp(P)
        if c["name"].startswith(trafico.FANTASMA):
            dr.line(q, fill=(230, 60, 60), width=2)
            continue
        dr.line(q, fill=(110, 110, 110), width=1)
        if len(q) >= 2:
            (ax, ay), (bx, by) = q[-2], q[-1]
            v = np.array([bx - ax, by - ay])
            v = v / max(np.linalg.norm(v), 1e-9) * 2.5 * a.px
            nrm = np.array([-v[1], v[0]]) * 0.5
            dr.polygon([(bx, by), (bx - v[0] + nrm[0], by - v[1] + nrm[1]), (bx - v[0] - nrm[0], by - v[1] - nrm[1])],
                       fill=(20, 20, 20))
    img.save(a.png)
    print(a.png)


if __name__ == "__main__":
    main()
