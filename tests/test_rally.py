"""Modo rally: corredor de los tramos largos y notas de copiloto."""
from types import SimpleNamespace

import numpy as np

from openlaps import notas, pipeline, regions, roads


class _Proj:
    def to_local(self, xs, ys):
        return np.asarray(xs, float), np.asarray(ys, float)


def _arco(p, rumbo, radio, grados, izquierda=True):
    """Puntos cada metro de un arco que sale de `p` con `rumbo` (rad); devuelve (puntos, punto final, rumbo final)."""
    sg = 1.0 if izquierda else -1.0
    n = max(2, int(abs(np.radians(grados)) * radio))
    a = rumbo + sg * np.linspace(0, np.radians(grados), n)
    c = np.array(p) + radio * np.array([-np.sin(rumbo), np.cos(rumbo)]) * sg
    pts = c + radio * np.column_stack([np.sin(a), -np.cos(a)]) * sg
    return pts, pts[-1], a[-1]


def _recta(p, rumbo, largo):
    d = np.arange(1.0, largo + 1)
    pts = np.array(p) + np.outer(d, [np.cos(rumbo), np.sin(rumbo)])
    return pts, pts[-1], rumbo


def _ruta():
    """Recta, izquierda de 90° y radio 30, recta, horquilla a derechas de radio 9, recta."""
    p, r, trozos = (0.0, 0.0), 0.0, []
    for f, arg in ((_recta, (200,)), (_arco, (30, 90, True)), (_recta, (150,)), (_arco, (9, 180, False)), (_recta, (120,))):
        pts, p, r = f(p, r, *arg)
        trozos.append(pts)
    xy = np.vstack(trozos)
    return np.column_stack([xy, np.zeros(len(xy))])


def test_notas_de_una_ruta_conocida():
    ns = notas.calcular(_ruta())
    curvas = [n for n in ns if n["tipo"] == "curva"]
    assert [(n["lado"], n.get("horquilla", False)) for n in curvas] == [("izquierda", False), ("derecha", True)]
    izq, horq = curvas
    assert izq["grado"] == 3 and 25 <= izq["radio"] <= 40 and 190 <= izq["d"] <= 215 and 75 <= izq["giro"] <= 95
    assert horq["texto"] == "Horquilla derecha" and horq["giro"] >= 150
    assert ns[-1]["tipo"] == "meta" and abs(ns[-1]["d"] - (200 + 47 + 150 + 28 + 120)) < 12
    assert 120 <= izq["hasta"] <= 160 and notas.enlace(izq) == "150"         # recta de 150 m hasta la horquilla
    assert not [n for n in ns if n["tipo"] == "rasante"]                      # ruta llana


def test_rasante_y_circuito_sin_meta():
    r = _ruta()
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(r[:, :2], axis=0), axis=1))])
    r[:, 2] = np.where(s < 100, 0.08 * s, 8.0 - 0.08 * (s - 100))             # sube al 8 % y baja al 8 %: un cambio brusco
    ns = notas.calcular(r, cerrada=True)
    ras = [n for n in ns if n["tipo"] == "rasante"]
    assert len(ras) == 1 and 80 <= ras[0]["d"] <= 120 and ras[0]["salto"]
    assert ns[-1]["tipo"] != "meta"


def test_libro_de_ruta_y_script():
    ns = notas.calcular(_ruta())
    txt = notas.libro_de_ruta(ns, "Prueba")
    assert "Izquierda 3" in txt and "Horquilla derecha" in txt and "Meta" in txt
    lua = notas.lua(ns)
    assert lua.count("\n  {") == len(ns) and "function script.update(dt)" in lua and "ac.setMessage(" in lua
    assert '"150 m · Horquilla derecha"' in lua
    # solo se usa lo que un script de pista de CSP tiene de verdad (no puede dibujar un HUD propio)
    assert "drawUI" not in lua and "ui." not in lua


def test_ancho_del_corredor():
    a = SimpleNamespace(modo="tramo", ruta="1,1;2,2", corredor="auto")
    assert pipeline.ancho_corredor(a, 10.0) == 0.0                            # zona chica: el rectángulo entero
    assert pipeline.ancho_corredor(a, 60.0) == pipeline.CORREDOR_ANCHO
    assert pipeline.ancho_corredor(SimpleNamespace(modo="libre", ruta=None, corredor="auto"), 60.0) == 0.0
    assert pipeline.ancho_corredor(SimpleNamespace(modo="circuito", ruta="1,1;2,2", corredor="150"), 2.0) == 150.0
    assert pipeline.ancho_corredor(SimpleNamespace(modo="tramo", ruta="1,1;2,2", corredor="0"), 60.0) == 0.0


def test_la_red_se_acota_al_corredor():
    from shapely.geometry import LineString
    regions.set_active(regions.REGIONS["generic"])
    vias = [([(-800, 0), (800, 0)], {"highway": "tertiary"}), ([(0, 0), (0, 800)], {"highway": "residential"}),
            ([(-800, 600), (800, 600)], {"highway": "residential"})]
    ids, ways = {}, []
    for i, (pts, tags) in enumerate(vias):
        nn = [ids.setdefault(tuple(p), len(ids) + 1) for p in pts]
        ways.append({"id": i + 1, "tags": tags, "nodes": nn, "coords": [tuple(map(float, p)) for p in pts]})
    osm = {"ways": ways, "nodes": [{"id": n, "tags": {}} for n in ids.values()]}
    net = roads.RoadNetwork(osm, _Proj(), lambda x, y: np.zeros(np.shape(x)), None, (-900, -900, 900, 900),
                            log=lambda *a: None)
    antes = sum(b - a for e in net.edges for a, b in e.runs)
    net.acotar(LineString([(-800, 0), (800, 0)]).buffer(200))
    despues = {c: sum(b - a for e in net.edges if e.cls == c for a, b in e.runs) for c in ("tertiary", "residential")}
    assert despues["tertiary"] > 1500                                         # la ruta, entera
    assert 150 < despues["residential"] < 260                                 # la lateral, solo lo que cae dentro
    assert sum(despues.values()) < antes
    # la paralela de 600 m más allá no se construye, y la ruta se sigue encontrando dentro del corredor
    assert not [e for e in net.edges if e.runs and np.abs(e.P[:, 1] - 600).max() < 1]
    ruta, hw, _ = roads.route(net, [(-700, 0), (700, 0)])
    assert len(ruta) > 1000 and len(hw) == len(ruta)
