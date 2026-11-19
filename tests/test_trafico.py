"""Tráfico para el modo Traffic de CSP (openlaps.trafico) sobre una red de calles inventada."""
import json

import numpy as np
import pytest

from openlaps import roads, trafico


class _Proj:
    """Proyección de mentira: las coordenadas de los nodos ya vienen en metros."""
    def to_local(self, xs, ys):
        return np.asarray(xs, float), np.asarray(ys, float)


def _red(vias, rect=(-500, -500, 500, 500), nodos_tags=None):
    ids, ways = {}, []
    for i, (pts, tags) in enumerate(vias):
        nn = [ids.setdefault(tuple(p), len(ids) + 1) for p in pts]
        ways.append({"id": i + 1, "tags": tags, "nodes": nn, "coords": [tuple(map(float, p)) for p in pts]})
    tags_de = {ids[k]: v for k, v in (nodos_tags or {}).items()}
    osm = {"ways": ways, "nodes": [{"id": n, "tags": tags_de.get(n, {})} for n in ids.values()]}
    return roads.RoadNetwork(osm, _Proj(), lambda x, y: np.zeros(np.shape(x)), None, rect, log=lambda *a: None), ids


RES = {"highway": "residential"}
PRIM = {"highway": "primary", "lanes": "4"}


@pytest.fixture(scope="module")
def cuadricula():
    vias = [
        ([(-650, 0), (-100, 0), (0, 0), (100, 0), (650, 0)], PRIM),             # avenida que sale del área
        ([(0, -300), (0, 0), (0, 300)], RES),                                   # cruce en +
        ([(100, 0), (100, 120), (100, 200)], RES),                               # sin salida: retorno
        ([(-100, 0), (-100, -200), (-250, -200)], {**RES, "oneway": "yes"}),    # sentido único
        ([(0, 300), (-250, 300), (-250, -200)], RES),                            # cierra el sentido único
    ]
    net, ids = _red(vias)
    datos = trafico.construir(net, None, log=lambda *a: None)
    return net, ids, datos


def test_carriles_y_cruces(cuadricula):
    _, _, d = cuadricula
    assert d is not None
    tipos = sorted(c["name"].split()[0] for c in d["intersections"])
    # cruces en (0,0), (-100,0) y (100,0); retornos en las dos puntas sin salida; unión donde el sentido único
    # se junta con la calle de doble sentido (cambia el número de carriles)
    assert (tipos.count("Cruce"), tipos.count("Retorno"), tipos.count("Union")) == (3, 2, 1)
    # la avenida (4 carriles, 2 por sentido) se puede cambiar de carril; la residencial no
    avenida = [c for c in d["lanes"] if c["name"].startswith("primary")]
    assert len(avenida) >= 8 and all(c["params"]["allowLaneChanges"] for c in avenida)
    assert not any(c["params"]["allowUTurns"] for c in d["lanes"])
    # roles: la avenida tiene más prioridad y más velocidad que la residencial
    roles = d["rules"]["laneRoles"]
    rp = roles[avenida[0]["role"] - 1]
    rr = roles[next(c for c in d["lanes"] if c["name"].startswith("residential"))["role"] - 1]
    assert rp["priority"] > rr["priority"] and rp["speedLimit"] > rr["speedLimit"]


def test_sentido_unico_y_lados(cuadricula):
    _, _, d = cuadricula
    # el sentido único (x=-100, de y=0 a y=-200) solo lleva carriles hacia el sur, y por la derecha
    su = [c for c in d["lanes"] if np.any((np.abs(c["points"][:, 0] + 100) < 3) & (c["points"][:, 1] < -50)
                                          & (c["points"][:, 1] > -150))]
    assert len(su) == 1 and su[0]["points"][-1, 1] < su[0]["points"][0, 1]
    # en una calle de doble sentido, quien va hacia el norte circula a la derecha (x mayor)
    def tramo(c):
        P = c["points"]
        return P[(np.abs(P[:, 0]) < 4) & (P[:, 1] > 100) & (P[:, 1] < 200)]
    v = [c for c in d["lanes"] if len(tramo(c)) >= 2]
    norte = [tramo(c) for c in v if tramo(c)[-1, 1] > tramo(c)[0, 1]]
    sur = [tramo(c) for c in v if tramo(c)[-1, 1] < tramo(c)[0, 1]]
    assert len(norte) == 1 and len(sur) == 1 and norte[0][:, 0].mean() > 0 > sur[0][:, 0].mean()


def test_grafo_como_csp(cuadricula):
    _, _, d = cuadricula
    r = trafico.revisar(trafico.a_ac(d))
    assert r["dentro_de_un_cruce"] == 0 and r["toques_de_mas"] == 0
    assert r["cruces_sin_entrada"] == 0 and r["cruces_sin_salida"] == 0 and r["cruces_vacios"] == 0
    assert r["conexo"] > 0.9
    # ningún carril se queda sin por dónde seguir, y todos entran a su cruce por un lado que CSP conserva
    assert r["entradas_sin_salida"] == 0 and r["entradas_sin_reglas"] == 0 and r["util"] == 1.0


def test_sin_cambios_de_sentido(cuadricula):
    _, _, d = cuadricula
    cruce = [c for c in d["intersections"] if c["name"].startswith("Cruce")]
    assert all(c.get("disallowedTrajectories") for c in cruce)
    ret = next(c for c in d["intersections"] if c["name"].startswith("Retorno"))
    assert "disallowedTrajectories" not in ret        # en el retorno, dar la vuelta es lo único que hay


def test_json_de_ac(cuadricula, tmp_path):
    _, _, d = cuadricula
    ac = trafico.a_ac(d)
    txt = json.dumps(ac)
    back = json.loads(txt)
    p = np.array(back["lanes"][0]["points"])
    q = d["lanes"][0]["points"]
    # AC: x = este, y = altura, z = sur
    assert np.allclose(p[:, 0], q[:, 0], atol=0.01) and np.allclose(p[:, 2], -q[:, 1], atol=0.01)
    assert set(back) == {"lanes", "intersections", "areas", "rules"}


def test_alto_baja_la_prioridad():
    vias = [([(-200, 0), (0, 0), (200, 0)], {"highway": "secondary"}),
            ([(0, -200), (0, -15), (0, 0), (0, 200)], RES)]
    net, ids = _red(vias, nodos_tags={(0, -15): {"highway": "stop"}})
    d = trafico.construir(net, None, log=lambda *a: None)
    c = next(c for c in d["intersections"] if c["name"].startswith("Cruce"))
    ofs = c.get("entryPriorityOffsets", [])
    assert len(ofs) == 1 and ofs[0]["offset"] < 0
    lane = next(x for x in d["lanes"] if x["id"] == ofs[0]["lane"])
    assert lane["points"][0, 1] < lane["points"][-1, 1] < 0        # el que llega desde el sur, donde está el alto


UNA = {"highway": "secondary", "oneway": "yes", "lanes": "2"}


def _unico_con_semaforo(extra=None, tags_ew=UNA):
    """Dos calles de un solo sentido (hacia el este y hacia el norte) que se cruzan en (0, 0), con semáforo."""
    vias = [([(-300, 0), (0, 0), (300, 0)], tags_ew),
            ([(0, -300), (0, 0), (0, 300)], UNA)] + (extra or [])
    net, _ = _red(vias, nodos_tags={(0, 0): {"highway": "traffic_signals"}})
    return trafico.construir(net, None, log=lambda *a: None)


def test_semaforo_y_carriles_fantasma_en_sentido_unico():
    d = _unico_con_semaforo()
    c = next(c for c in d["intersections"] if c["name"].startswith("Cruce"))
    luz = c["trafficLight"]
    assert luz["program"] == "Basic" and luz["params"]["duration"] >= 10
    # cada calle es de un solo sentido: por cada boca solo se entra o solo se sale, y lleva un carril fantasma
    fant = [x for x in d["lanes"] if x["name"].startswith(trafico.FANTASMA)]
    assert len(fant) == 4 and all(len(x["points"]) == 3 for x in fant)
    ids_f = {x["id"] for x in fant}
    reales = {x["id"] for x in d["lanes"]} - ids_f
    veto = {tuple(x) for x in c["disallowedTrajectories"]}
    ent_f = {a for a, b in veto if a in ids_f}
    sal_f = ids_f - ent_f
    assert len(ent_f) == 2 and len(sal_f) == 2
    # nadie puede ir a un fantasma ni salir de uno
    entran = {x["id"] for x in d["lanes"] if x["id"] in reales and np.hypot(*x["points"][-1, :2]) < 30}
    salen = {x["id"] for x in d["lanes"] if x["id"] in reales and np.hypot(*x["points"][0, :2]) < 30}
    assert len(entran) == 4 and len(salen) == 4
    assert all((a, f) in veto for a in entran for f in sal_f)
    assert all((f, b) in veto for f in ent_f for b in salen | sal_f)
    ac = trafico.a_ac(d)
    r = trafico.revisar(ac)
    assert r["semaforos"] == 1 and r["entradas_sin_luz"] == 0 and r["entradas_sin_reglas"] == 0
    assert r["entradas_sin_salida"] == 0 and r["carriles"] == len(reales)
    # las dos calles entran por lados de distinta paridad (CSP da el verde a los pares o a los impares) y las
    # luces van una por lado, con roja, ámbar y verde
    ca = next(x for x in ac["intersections"] if x.get("trafficLight"))
    K = trafico.enlaces_csp(ac)[ac["intersections"].index(ca)]
    lados = {x["lado"] for x in K["ent"] if ac["lanes"][x["carril"]]["id"] in entran}
    assert {k % 2 for k in lados} == {0, 1} and lados <= set(K["lados"])
    emis = ca["trafficLight"]["emissive"]
    assert len(emis) == len(ca["points"]) and "postes" not in ca["trafficLight"]
    for k in lados:
        items = emis[k - 1]["virtual"]["items"]
        assert emis[k - 1]["mode"] == 2 and len(items) % 3 == 0 and len(items) >= 3
        assert items[0]["pos"][1] > items[1]["pos"][1] > items[2]["pos"][1] > 2      # roja arriba (y = altura)
    assert all("_k" not in x for x in ac["lanes"])
    json.dumps(ac)
    assert len(trafico.postes_de_semaforo(d)) == sum(len(s.get("virtual", {}).get("items", [])) for s in emis) // 3


def test_carril_sin_salida_se_quita():
    # una calle de dos carriles de un sentido acaba en T contra otra de un sentido: CSP solo deja girar al carril
    # del lado del giro, así que el otro se quita en vez de dejar coches que desaparecen
    vias = [([(-300, 0), (0, 0), (300, 0)], {**UNA, "lanes": "3"}),
            ([(0, -300), (0, 0)], UNA)]
    net, _ = _red(vias)
    msgs = []
    d = trafico.construir(net, None, log=msgs.append)
    sur = [x for x in d["lanes"] if not x["name"].startswith(trafico.FANTASMA) and x["points"][0, 1] < -100]
    assert len(sur) == 1 and "quitados" in msgs[-1]
    r = trafico.revisar(trafico.a_ac(d))
    assert r["entradas_sin_salida"] == 0 and r["util"] == 1.0


def test_flechas_de_carril():
    # dos carriles hacia el este: el de la izquierda solo gira a la izquierda (norte), el otro sigue recto
    d = _unico_con_semaforo(tags_ew={**UNA, "turn:lanes": "left|through"})
    c = next(c for c in d["intersections"] if c["name"].startswith("Cruce"))
    veto = {tuple(x) for x in c["disallowedTrajectories"]}
    P = {x["id"]: x["points"] for x in d["lanes"] if not x["name"].startswith(trafico.FANTASMA)}
    oeste = sorted((i for i, p in P.items() if p[0, 0] < -100), key=lambda i: -P[i][0, 1])     # izquierda primero
    este = [i for i, p in P.items() if p[-1, 0] > 100]
    norte = [i for i, p in P.items() if p[-1, 1] > 100]
    assert len(oeste) == 2 and len(este) == 2 and len(norte) == 2
    assert all((oeste[0], s) in veto for s in este)                 # el de la izquierda no sigue recto
    assert any((oeste[0], s) not in veto for s in norte)
    assert all((oeste[1], s) in veto for s in norte)                # el de la derecha no gira
    assert any((oeste[1], s) not in veto for s in este)
    assert trafico.revisar(trafico.a_ac(d))["entradas_sin_salida"] == 0


def test_recto_sin_cruzarse():
    # avenida de un sentido con 4 carriles que sigue de frente: nadie salta del carril de un extremo al del otro
    d = _unico_con_semaforo(tags_ew={**UNA, "lanes": "4"})
    c = next(c for c in d["intersections"] if c["name"].startswith("Cruce"))
    veto = {tuple(x) for x in c["disallowedTrajectories"]}
    P = {x["id"]: x["points"] for x in d["lanes"] if not x["name"].startswith(trafico.FANTASMA)}
    oeste = sorted((i for i, p in P.items() if p[0, 0] < -100), key=lambda i: -P[i][0, 1])
    este = sorted((i for i, p in P.items() if p[-1, 0] > 100), key=lambda i: -P[i][-1, 1])
    assert len(oeste) == 4 and len(este) == 4
    for i, a in enumerate(oeste):
        for j, b in enumerate(este):
            assert ((a, b) in veto) == (abs(i - j) > 1)
