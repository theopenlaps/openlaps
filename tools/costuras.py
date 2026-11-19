"""Mide las costuras entre pedazos vecinos del modo en vivo: huecos, solapes y escalones en el borde común.

Lee los .olv de una carpeta de pedazos (salida/vivo/<nombre>) y, para cada par de vecinos, muestrea una franja
de 3 m a caballo del borde: cuántos puntos de calzada o de suelo no cubre ninguno de los dos pedazos (hueco),
cuántos cubren los dos (solape: parpadeo) y cuánto difiere la altura a un lado y a otro (escalón).

Uso: python tools/costuras.py salida/vivo/<nombre> [--lado 256] [--max 40]
"""
import argparse
import os
import re
import struct

import numpy as np


def leer_olv(path):
    """[(nombre, material, física, pos Nx3 en ejes locales x este / y norte / z arriba, caras)]"""
    with open(path, "rb") as f:
        d = f.read()
    assert d[:4] == b"OLV1"
    n = struct.unpack_from("<I", d, 4)[0]
    o = 8
    out = []

    def s2():
        nonlocal o
        k = struct.unpack_from("<H", d, o)[0]
        o += 2
        t = d[o:o + k].decode()
        o += k
        return t
    for _ in range(n):
        nombre, mat, phys, _tex = s2(), s2(), s2(), s2()
        nv, ni = struct.unpack_from("<II", d, o)
        o += 8
        v = np.frombuffer(d, "<f4", nv * 8, o).reshape(nv, 8)
        o += nv * 32
        idx = np.frombuffer(d, "<u2", ni, o).reshape(-1, 3).astype(np.int64)
        o += ni * 2
        out.append((nombre, mat, phys, np.column_stack([v[:, 0], -v[:, 2], v[:, 1]]).astype(float), idx))
    return out


def _clase(nombre, mat, phys):
    if phys == "ROAD":
        return "calzada"
    if mat.startswith("tv_") or nombre.startswith("terreno"):
        return "suelo"
    return None


def _tri(mallas, clase, caja):
    x0, y0, x1, y1 = caja
    ts = []
    for nombre, mat, phys, pos, idx in mallas:
        if _clase(nombre, mat, phys) != clase or not len(idx):
            continue
        t = pos[idx]
        ok = (t[..., 0].max(1) >= x0) & (t[..., 0].min(1) <= x1) & (t[..., 1].max(1) >= y0) & (t[..., 1].min(1) <= y1)
        ts.append(t[ok])
    return np.concatenate(ts) if ts else np.zeros((0, 3, 3))


def _cubre(tris, pts):
    """Por punto: nº de triángulos que lo cubren en planta y la altura del más alto (nan si ninguno)."""
    n = np.zeros(len(pts), int)
    z = np.full(len(pts), np.nan)
    for t in tris:
        a, b, c = t[:, :2]
        m = (pts[:, 0] >= t[:, 0].min() - 1e-6) & (pts[:, 0] <= t[:, 0].max() + 1e-6) & \
            (pts[:, 1] >= t[:, 1].min() - 1e-6) & (pts[:, 1] <= t[:, 1].max() + 1e-6)
        if not m.any():
            continue
        p = pts[m]
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-12:
            continue
        w0 = ((b[1] - c[1]) * (p[:, 0] - c[0]) + (c[0] - b[0]) * (p[:, 1] - c[1])) / det
        w1 = ((c[1] - a[1]) * (p[:, 0] - c[0]) + (a[0] - c[0]) * (p[:, 1] - c[1])) / det
        w2 = 1 - w0 - w1
        ins = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        ii = np.flatnonzero(m)[ins]
        n[ii] += 1
        zz = w0[ins] * t[0, 2] + w1[ins] * t[1, 2] + w2[ins] * t[2, 2]
        z[ii] = np.fmax(z[ii], zz)
    return n, z


def medir_par(A, B, borde, eje, lado0, lado1, paso=0.25, ancho=1.5):
    """Costura entre los pedazos A (antes del borde) y B (después). eje 0: borde en x = `borde`; eje 1: en y."""
    s = np.arange(lado0 + paso / 2, lado1, paso)
    d = np.arange(-ancho + paso / 2, ancho, paso)
    S, D = np.meshgrid(s, d)
    pts = np.column_stack([borde + D.ravel(), S.ravel()] if eje == 0 else [S.ravel(), borde + D.ravel()])
    caja = (borde - ancho, lado0, borde + ancho, lado1) if eje == 0 else (lado0, borde - ancho, lado1, borde + ancho)
    out = {}
    for clase in ("calzada", "suelo"):
        ta, tb = _tri(A, clase, caja), _tri(B, clase, caja)
        if not len(ta) and not len(tb):
            continue
        na, za = _cubre(ta, pts)
        nb, zb = _cubre(tb, pts)
        # un punto es de la franja de esa clase si lo cubre alguien a menos de `paso` (para la calzada: la
        # calle cruza el borde); hueco = punto sin cubrir con calzada a ambos lados a lo largo del cruce
        cub = ((na + nb) > 0).reshape(D.shape)
        hay_antes = np.maximum.accumulate(cub, axis=0)
        hay_despues = np.maximum.accumulate(cub[::-1], axis=0)[::-1]
        hueco = (~cub) & hay_antes & hay_despues
        solape = ((na > 0) & (nb > 0)).reshape(D.shape)
        # escalón: altura de A justo antes del borde frente a la de B justo después
        k = len(d) // 2
        zA = za.reshape(D.shape)[k - 1]
        zB = zb.reshape(D.shape)[k]
        dz = np.abs(zA - zB)
        dz = dz[np.isfinite(dz)]
        out[clase] = {"m2": float(cub.sum() * paso * paso), "hueco_m2": float(hueco.sum() * paso * paso),
                      "solape_m2": float(solape.sum() * paso * paso),
                      "dz_max": float(dz.max()) if len(dz) else 0.0,
                      "dz_p95": float(np.percentile(dz, 95)) if len(dz) else 0.0, "n_dz": int(len(dz))}
    return out


def medir_carpeta(carpeta, lado=256.0, maximo=40):
    ps = {}
    for f in os.listdir(carpeta):
        m = re.match(r"^p_(-?\d+)_(-?\d+)\.olv$", f)
        if m:
            ps[(int(m[1]), int(m[2]))] = os.path.join(carpeta, f)
    pares = [(k, (k[0] + 1, k[1]), 0) for k in ps if (k[0] + 1, k[1]) in ps] + \
            [(k, (k[0], k[1] + 1), 1) for k in ps if (k[0], k[1] + 1) in ps]
    pares.sort(key=lambda p: abs(p[0][0]) + abs(p[0][1]))
    cache = {}

    def olv(k):
        if k not in cache:
            cache[k] = leer_olv(ps[k])
        return cache[k]
    tot = {}
    for a, b, eje in pares[:maximo]:
        # núcleo del pedazo (i, j): [i*L - L/2, (i+1)*L - L/2)
        borde = (a[eje] + 1) * lado - lado / 2
        otro = 1 - eje
        l0 = a[otro] * lado - lado / 2
        r = medir_par(olv(a), olv(b), borde, eje, l0, l0 + lado)
        for clase, v in r.items():
            t = tot.setdefault(clase, {"m2": 0.0, "hueco_m2": 0.0, "solape_m2": 0.0, "dz": [], "dz_max": 0.0, "pares": 0})
            t["m2"] += v["m2"]
            t["hueco_m2"] += v["hueco_m2"]
            t["solape_m2"] += v["solape_m2"]
            t["dz_max"] = max(t["dz_max"], v["dz_max"])
            t["dz"].append(v["dz_p95"])
            t["pares"] += 1
    return tot, len(pares)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta")
    ap.add_argument("--lado", type=float, default=256.0)
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()
    tot, n = medir_carpeta(a.carpeta, a.lado, a.max)
    print(f"{a.carpeta}: {n} costuras ({min(n, a.max)} medidas)")
    for clase, t in tot.items():
        print(f"  {clase:8s} franja {t['m2']:8.0f} m²  huecos {t['hueco_m2']:6.1f} m²  solapes {t['solape_m2']:6.1f} m²  "
              f"escalón p95 {np.mean(t['dz']) * 100:5.1f} cm (máx {t['dz_max'] * 100:.0f} cm)")


if __name__ == "__main__":
    main()
