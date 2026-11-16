"""Busca escalones en lo que se conduce:

- solapes: dos superficies con física (calzada, cruces, plazas, banquetas…) que se pisan a poca distancia en
  altura (entre 3 y 60 cm), en una rejilla de 0,5 m;
- costuras: saltos de más de 5 cm entre celdas vecinas de 0,2 m de calzada (una calle que no casa con su cruce);
- huecos: rendijas sin calzada dentro de la calzada (asoma el terreno: el coche mete la rueda y salta).
  Las guarniciones no cuentan: solo se mira la calzada.
En el juego se notan como golpes secos aunque a la vista apenas se vean.

  python tools/escalones.py salida/madrid_sol.glb [--min 0.03] [--max 0.6] [--n 20]
  python tools/escalones.py salida/vivo/<centro>/p_*.glb     (varios pedazos del modo en vivo)
"""
import argparse
import glob
import os
import sys

import numpy as np
from numba import njit

sys.path.insert(0, os.path.dirname(__file__))
from render import load_glb  # noqa: E402

CELDA = 0.5
K = 6   # alturas guardadas por celda


@njit(cache=True)
def _rasterizar(tris, x0, z0, nx, nz, celda, alturas, n):
    for t in range(tris.shape[0]):
        ax, ay, az = tris[t, 0, 0], tris[t, 0, 1], tris[t, 0, 2]
        bx, by, bz = tris[t, 1, 0], tris[t, 1, 1], tris[t, 1, 2]
        cx, cy, cz = tris[t, 2, 0], tris[t, 2, 1], tris[t, 2, 2]
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-9:
            continue
        i0 = max(0, int((min(ax, bx, cx) - x0) / celda))
        i1 = min(nx - 1, int((max(ax, bx, cx) - x0) / celda) + 1)
        j0 = max(0, int((min(az, bz, cz) - z0) / celda))
        j1 = min(nz - 1, int((max(az, bz, cz) - z0) / celda) + 1)
        for i in range(i0, i1 + 1):
            px = x0 + (i + 0.5) * celda
            for j in range(j0, j1 + 1):
                pz = z0 + (j + 0.5) * celda
                l1 = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / d
                l2 = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / d
                l3 = 1.0 - l1 - l2
                if l1 < 0 or l2 < 0 or l3 < 0:
                    continue
                y = l1 * ay + l2 * by + l3 * cy
                k = n[i, j]
                if k < alturas.shape[2]:
                    alturas[i, j, k] = y
                    n[i, j] = k + 1


def escalones(rutas, dmin=0.03, dmax=0.6):
    tris = []
    for r in rutas:
        for m in load_glb(r)[0]:
            nm = m["name"]
            # superficies con física de suelo (no muros ni el terreno: el terreno va debajo a propósito)
            if not nm.startswith("1") or nm.startswith(("1WALL", "1GRASS")):
                continue
            tris.append(m["pos"][m["idx"]])
    if not tris:
        return np.zeros((0, 3)), 0
    T = np.concatenate(tris).astype(np.float64)
    x0, z0 = T[:, :, 0].min(), T[:, :, 2].min()
    nx = int((T[:, :, 0].max() - x0) / CELDA) + 2
    nz = int((T[:, :, 2].max() - z0) / CELDA) + 2
    alturas = np.full((nx, nz, K), np.nan)
    n = np.zeros((nx, nz), np.int64)
    _rasterizar(T, x0, z0, nx, nz, CELDA, alturas, n)
    cubierto = int((n > 0).sum())
    malos = []
    for i, j in zip(*np.nonzero(n >= 2)):
        h = np.sort(alturas[i, j, :n[i, j]])
        dif = np.diff(h)
        # pares cercanos en altura (un puente a 5 m por encima no cuenta) pero no iguales
        sel = (dif >= dmin) & (dif <= dmax)
        if sel.any():
            malos.append((x0 + (i + 0.5) * CELDA, z0 + (j + 0.5) * CELDA, float(dif[sel].max())))
    return np.array(malos).reshape(-1, 3), cubierto


def costuras(rutas, celda=0.2, dmin=0.05, dmax=0.6):
    """Saltos entre celdas vecinas de la calzada (la superficie de arriba en cada celda, sin contar lo que pasa
    a más de dmax por encima o por debajo: puentes, túneles)."""
    tris = []
    for r in rutas:
        for m in load_glb(r)[0]:
            if m["name"].startswith("1") and "calzada" in m["name"]:
                tris.append(m["pos"][m["idx"]])
    if not tris:
        return np.zeros((0, 3))
    T = np.concatenate(tris).astype(np.float64)
    x0, z0 = T[:, :, 0].min(), T[:, :, 2].min()
    nx = int((T[:, :, 0].max() - x0) / celda) + 2
    nz = int((T[:, :, 2].max() - z0) / celda) + 2
    alturas = np.full((nx, nz, K), np.nan)
    n = np.zeros((nx, nz), np.int64)
    _rasterizar(T, x0, z0, nx, nz, celda, alturas, n)
    malos = []
    for di, dj in ((1, 0), (0, 1)):
        a = alturas[:nx - di, :nz - dj]
        b = alturas[di:, dj:]
        # para cada capa de a, la capa de b más cercana en altura
        d = np.abs(a[:, :, :, None] - b[:, :, None, :])
        with np.errstate(all="ignore"):
            dm = np.nanmin(d.reshape(*d.shape[:3], -1), axis=3)       # (nx, nz, K)
            peor = np.nanmax(np.where(dm < dmax, dm, np.nan), axis=2)
        sel = peor > dmin
        for i, j in zip(*np.nonzero(sel)):
            malos.append((x0 + (i + 0.5 + di / 2) * celda, z0 + (j + 0.5 + dj / 2) * celda, float(peor[i, j])))
    return np.array(malos).reshape(-1, 3)


def huecos(rutas, celda=0.2):
    """Celdas sin calzada rodeadas de calzada (cierre morfológico de 0,6 m)."""
    from scipy.ndimage import binary_closing
    tris = []
    for r in rutas:
        for m in load_glb(r)[0]:
            if m["name"].startswith("1") and "calzada" in m["name"]:
                tris.append(m["pos"][m["idx"]])
    if not tris:
        return np.zeros((0, 3))
    T = np.concatenate(tris).astype(np.float64)
    x0, z0 = T[:, :, 0].min(), T[:, :, 2].min()
    nx = int((T[:, :, 0].max() - x0) / celda) + 2
    nz = int((T[:, :, 2].max() - z0) / celda) + 2
    alturas = np.full((nx, nz, 1), np.nan)
    n = np.zeros((nx, nz), np.int64)
    _rasterizar(T, x0, z0, nx, nz, celda, alturas, n)
    cub = n > 0
    h = binary_closing(cub, structure=np.ones((3, 3), bool), iterations=2) & ~cub
    i, j = np.nonzero(h)
    return np.c_[x0 + (i + 0.5) * celda, z0 + (j + 0.5) * celda, np.full(len(i), celda)]


def agrupar(malos, radio=4.0):
    grupos = []
    for x, z, d in sorted(malos.tolist(), key=lambda v: -v[2]):
        for g in grupos:
            if (g[0] - x) ** 2 + (g[1] - z) ** 2 < radio ** 2:
                g[3] += 1
                break
        else:
            grupos.append([x, z, d, 1])
    return grupos


def main():
    p = argparse.ArgumentParser()
    p.add_argument("glb", nargs="+")
    p.add_argument("--min", type=float, default=0.03)
    p.add_argument("--max", type=float, default=0.6)
    p.add_argument("--n", type=int, default=20)
    a = p.parse_args()
    rutas = [f for g in a.glb for f in (glob.glob(g) or [g])]
    malos, cubierto = escalones(rutas, a.min, a.max)
    grupos = agrupar(malos)
    print(f"{len(rutas)} archivo(s): {cubierto * CELDA * CELDA:,.0f} m² con física de suelo, "
          f"{len(malos)} celdas con escalón ({len(malos) * CELDA * CELDA:,.0f} m²) en {len(grupos)} sitios")
    for x, z, d, k in grupos[:a.n]:
        print(f"  x={x:8.1f} z={z:8.1f}  escalón {d * 100:4.0f} cm  ({k} celdas)")
    hu = huecos(rutas)
    gh = agrupar(hu)
    print(f"huecos en la calzada: {len(hu)} celdas de 0,2 m en {len(gh)} sitios")
    for x, z, d, k in sorted(gh, key=lambda g: -g[3])[:a.n]:
        print(f"  x={x:8.1f} z={z:8.1f}  ({k} celdas)")
    cos = costuras(rutas)
    gc = agrupar(cos)
    print(f"costuras de calzada con salto > 5 cm: {len(cos)} en {len(gc)} sitios")
    for x, z, d, k in gc[:a.n]:
        print(f"  x={x:8.1f} z={z:8.1f}  salto {d * 100:4.0f} cm  ({k} bordes)")


if __name__ == "__main__":
    main()
