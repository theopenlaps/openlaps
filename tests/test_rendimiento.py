"""Las optimizaciones de velocidad no deben cambiar el resultado."""
import math

import numpy as np
import shapely
from shapely.geometry import Point
from shapely.ops import unary_union

from openlaps import terrain, textures
from openlaps.geo import BBox, LocalProjection
from openlaps.roads import _trocear
from openlaps.sources.rasters import _transformar, _transformer


def _pnoise_numpy(rng, U, V, cells_u, cells_v, octaves):
    """La versión original con numpy, de referencia."""
    h, w = U.shape
    acc = np.zeros((h, w), np.float32)
    amp = 1.0
    for o in range(octaves):
        cu, cv = int(cells_u * 2 ** o), int(cells_v * 2 ** o)
        g = rng.random((cv + 1, cu + 1)).astype(np.float32)
        g[-1, :] = g[0, :]
        x, y = U * cu, V * cv
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = x - x0, y - y0
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        x1, y1 = np.minimum(x0 + 1, cu), np.minimum(y0 + 1, cv)
        a = g[y0 % (cv + 1), x0] * (1 - fx) + g[y0 % (cv + 1), x1] * fx
        b = g[y1 % (cv + 1), x0] * (1 - fx) + g[y1 % (cv + 1), x1] * fx
        acc += (a * (1 - fy) + b * fy - 0.5) * amp
        amp *= 0.5
    return acc


def test_pnoise_numba_identico_a_numpy():
    U, V = np.meshgrid((np.arange(64) + 0.5) / 64, (np.arange(256) + 0.5) / 256)
    for args in ((3, 12, 4), (1, 9, 2), (12, 48, 2)):
        a = textures._pnoise(np.random.default_rng(5), U, V, *args)
        b = _pnoise_numpy(np.random.default_rng(5), U, V, *args)
        assert np.array_equal(a, b)
    # también con una sola columna (rodaduras)
    a = textures._pnoise(np.random.default_rng(1), U[:, :1], V[:, :1], 1, 6, 2)
    b = _pnoise_numpy(np.random.default_rng(1), U[:, :1], V[:, :1], 1, 6, 2)
    assert np.array_equal(a, b)


def test_hash_noise_numba_identico():
    x, y = np.meshgrid(np.linspace(-300, 300, 97), np.linspace(-120, 480, 61))

    def ref(x, y, scale, seed):
        xs, ys = x / scale, y / scale
        xi, yi = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
        xf, yf = xs - xi, ys - yi

        def h(a, b):
            v = (a * 374761393 + b * 668265263 + seed * 2246822519) & 0xFFFFFFFF
            v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
            return (v & 0xFFFF) / 65535.0
        u = xf * xf * (3 - 2 * xf)
        v = yf * yf * (3 - 2 * yf)
        return (h(xi, yi) * (1 - u) * (1 - v) + h(xi + 1, yi) * u * (1 - v)
                + h(xi, yi + 1) * (1 - u) * v + h(xi + 1, yi + 1) * u * v)
    for scale, seed in ((9.0, 11), (40.0, 1), (3.0, 7)):
        assert np.array_equal(terrain._hash_noise(x, y, scale, seed), ref(x, y, scale, seed))


def test_trocear_en_dos_niveles_conserva_el_area():
    rng = np.random.default_rng(0)
    poly = unary_union([Point(x, y).buffer(5).union(shapely.box(x - 200, y - 1, x + 200, y + 1))
                        for x, y in rng.uniform(0, 400, (40, 2))])
    poly = max(getattr(poly, "geoms", [poly]), key=lambda g: g.area)
    piezas = _trocear(poly, 3.0)
    assert math.isclose(sum(g.area for g in piezas), poly.area, rel_tol=1e-9)
    for g in piezas:                      # ninguna pieza pasa de una celda de 3 m
        a, b, c, d = g.bounds
        assert c - a <= 3.0 + 1e-6 and d - b <= 3.0 + 1e-6


def test_transformacion_aproximada_milimetrica():
    p = LocalProjection(BBox.around(40.4168, -3.7036, 800))
    g = np.linspace(-5000, 5000, 400)
    X, Y = np.meshgrid(g, g)
    t = _transformer(p.epsg, "EPSG:3857")
    a = t.transform(X.ravel() + p.ox, Y.ravel() + p.oy)
    b = _transformar(t, X.ravel() + p.ox, Y.ravel() + p.oy)
    assert np.hypot(a[0] - b[0], a[1] - b[1]).max() < 0.01
