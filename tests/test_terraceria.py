"""Caminos sin asfalto: qué superficie se deduce cuando OSM no la dice y qué física lleva cada una."""
import numpy as np
import pytest

from openlaps import config as C
from openlaps import export_ac, regions, roads


class _Proj:
    def to_local(self, xs, ys):
        return np.asarray(xs, float), np.asarray(ys, float)


def _fisicas(vias, modo="auto", region="generic", urbano=False):
    """Física de cada vía (por su etiqueta `id`) en una red inventada con coordenadas en metros."""
    regions.set_active(regions.REGIONS[region])
    ids, ways = {}, []
    for i, (pts, tags) in enumerate(vias):
        nn = [ids.setdefault(tuple(p), len(ids) + 1) for p in pts]
        ways.append({"id": i + 1, "tags": dict(tags, id=str(i)), "nodes": nn, "coords": [tuple(map(float, p)) for p in pts]})
    osm = {"ways": ways, "nodes": [{"id": n, "tags": {}} for n in ids.values()]}
    net = roads.RoadNetwork(osm, _Proj(), lambda x, y: np.zeros(np.shape(x)), lambda x, y: np.full(np.shape(x), urbano),
                            (-900, -900, 900, 900), log=lambda *a: None, terraceria=modo)
    out = {}
    for e in net.edges:
        out.setdefault(int(e.tags["id"]), set()).add(e.phys)
    assert all(len(v) == 1 for v in out.values())
    return {k: v.pop() for k, v in out.items()}


SIN = {"highway": "unclassified"}
# tres vías en cruz para que no se fundan en una: 0 (oeste), 1 (este), 2 (norte)
CRUZ = [[(-300, 0), (0, 0)], [(0, 0), (300, 0)], [(0, 0), (0, 300)]]


def test_cada_superficie_de_osm_pisa_distinto():
    f = _fisicas([(CRUZ[0], dict(SIN, surface="gravel")), (CRUZ[1], dict(SIN, surface="sand")),
                  (CRUZ[2], dict(SIN, surface="asphalt")), ([(0, 0), (0, -300)], {"highway": "track"})])
    assert f == {0: "MXGRAVA", 1: "MXARENA", 2: "ROAD", 3: "LOOSE"}


def test_tracktype_y_el_mismo_camino_partido():
    # 1 no dice nada, pero es la continuación con el mismo nombre de un camino de grava
    f = _fisicas([(CRUZ[0], dict(SIN, name="Camino Real", tracktype="grade2")), (CRUZ[1], dict(SIN, name="Camino Real")),
                  (CRUZ[2], dict(SIN))])
    assert f[0] == "MXGRAVA" and f[1] == "MXGRAVA"
    assert f[2] == "MXGRAVA" or f[2] == "LOOSE"      # ramal de un camino de grava: tampoco es de asfalto
    assert _fisicas([(CRUZ[0], dict(SIN, name="Camino Real", tracktype="grade2")), (CRUZ[1], dict(SIN, name="Camino Real")),
                     (CRUZ[2], dict(SIN))], modo="osm") == {0: "ROAD", 1: "ROAD", 2: "ROAD"}   # solo `surface`, como antes


def test_una_brecha_no_vuelve_de_tierra_a_la_carretera():
    f = _fisicas([(CRUZ[0], SIN), (CRUZ[1], SIN), (CRUZ[2], {"highway": "track", "surface": "dirt"})])
    assert f == {0: "ROAD", 1: "ROAD", 2: "MXTIERRA"}


def test_senales_de_asfalto_ganan():
    f = _fisicas([(CRUZ[0], dict(SIN, surface="dirt", name="X")), (CRUZ[1], dict(SIN, name="X", lanes="2")),
                  (CRUZ[2], dict(SIN, name="X", lit="yes"))], modo="rural")
    assert f == {0: "MXTIERRA", 1: "ROAD", 2: "ROAD"}


@pytest.mark.parametrize("region, modo, urbano, espera", [
    ("generic", "auto", False, "ROAD"),     # sin indicios, asfalto
    ("mx", "auto", False, "LOOSE"),         # en México un camino rural sin dato es brecha
    ("mx", "auto", True, "ROAD"),           # pero no dentro de un pueblo
    ("mx", "osm", False, "ROAD"),
    ("generic", "rural", False, "LOOSE"),   # forzado
])
def test_regla_regional(region, modo, urbano, espera):
    vias = [(CRUZ[0], SIN), (CRUZ[1], SIN), (CRUZ[2], {"highway": "residential"})]
    f = _fisicas(vias, modo=modo, region=region, urbano=urbano)
    assert f[0] == f[1] == espera
    assert f[2] == ("LOOSE" if modo == "rural" else "ROAD")     # una calle con casas solo cambia si se fuerza


def test_surfaces_ini_lleva_la_grava_de_csp():
    ini = export_ac.surfaces_ini()
    claves = [k for k, *_ in export_ac.SURFACE_DEFS]
    for k in C.FISICAS_SUELTAS:
        bloque = ini.split(f"KEY={k}\n")[1].split("[SURFACE_")[0]
        assert "IS_VALID_TRACK=1" in bloque and "_EXT_SURFACE_TYPE=" in bloque
        assert float(bloque.split("FRICTION=")[1].split()[0]) < 0.9
        # AC busca la clave por prefijo en el nombre de la malla: ninguna puede empezar por otra
        assert not [o for o in claves if o != k and (k.startswith(o) or o.startswith(k))]


def _red(vias, **kw):
    regions.set_active(regions.REGIONS["generic"])
    ids, ways = {}, []
    for i, (pts, tags) in enumerate(vias):
        nn = [ids.setdefault(tuple(p), len(ids) + 1) for p in pts]
        ways.append({"id": i + 1, "tags": tags, "nodes": nn, "coords": [tuple(map(float, p)) for p in pts]})
    osm = {"ways": ways, "nodes": [{"id": n, "tags": {}} for n in ids.values()]}
    return roads.RoadNetwork(osm, _Proj(), lambda x, y: np.zeros(np.shape(x)), None, (-900, -900, 900, 900),
                             log=lambda *a: None, **kw)


def test_perfil_de_camino():
    net = _red([([(-400, 0), (0, 0)], {"highway": "track", "surface": "dirt"}),
                ([(0, 0), (400, 0)], {"highway": "track", "surface": "dirt"}),
                ([(0, 0), (0, 400)], {"highway": "tertiary"}), ([(0, 0), (0, -400)], {"highway": "tertiary"})])
    tierra = [e for e in net.edges if e.phys == "MXTIERRA"]
    assert len(tierra) == 2 and all(e.perfil is not None for e in tierra)
    assert all(e.perfil is None for e in net.edges if e.phys == "ROAD")
    e = tierra[0]
    s = np.linspace(e.s0 + 40, e.s1 - 40, 200)
    # roderas: el fondo queda por debajo de sus dos lados, en promedio a lo largo del tramo
    fondo = e.at(s, roads.MEDIA_VIA)[:, 2].mean()
    assert fondo < e.at(s, roads.MEDIA_VIA - 0.38)[:, 2].mean() - 0.01
    assert fondo < e.at(s, roads.MEDIA_VIA + 0.38)[:, 2].mean() - 0.01
    # baches a lo largo, pero suaves: un coche de calle los pasa
    z = e.at(np.arange(e.s0 + 40, e.s1 - 40, 0.5), 0.0)[:, 2]
    assert 0.01 < z.std() < 0.08 and np.abs(np.diff(z)).max() / 0.5 < 0.06
    # ancho irregular, sin pasarse
    ancho = np.linalg.norm(e.at(s, e.hw)[:, :2] - e.at(s, -e.hw)[:, :2], axis=1) / (2 * e.hw)
    assert ancho.std() > 0.01 and 0.8 < ancho.min() and ancho.max() < 1.2
    # en los extremos manda el cruce: la sección es la de siempre
    for s_ext in (e.s0, e.s1):
        p = e.at(s_ext, np.array([-e.hw, 0.0, e.hw]))
        assert np.allclose(p[:, 2], p[1, 2]) and np.isclose(np.linalg.norm(p[2, :2] - p[0, :2]), 2 * e.hw)
    # la malla tiene un vértice en el fondo de cada rodera
    assert all(np.isclose(roads._columnas(e), r).any() for r in e.perfil[4])


def test_perfil_no_depende_de_como_se_parta_la_via():
    # dos pedazos del modo en vivo ven la misma vía cortada en sitios distintos: la superficie debe ser la misma
    t = {"highway": "track", "surface": "gravel"}
    corta = _red([([(-300, 5), (300, 5)], t)]).edges[0]
    larga = _red([([(-600, 5), (300, 5)], t)]).edges[0]
    for o in (-1.0, 0.0, 0.75):
        a = corta.at(np.arange(100.0, 500.0, 7.0), o)            # x de -200 a 200
        b = larga.at(np.arange(400.0, 800.0, 7.0), o)
        assert np.allclose(a[:, :2], b[:, :2], atol=1e-6) and np.allclose(a[:, 2], b[:, 2], atol=1e-6)


# ------------------------------------------------------------------ fase 3: suelo, veredas y vados
def test_fisica_del_suelo_por_cobertura():
    from types import SimpleNamespace

    from openlaps import cobertura as COB
    from openlaps import terrain
    # rejilla llana de 40 x 20 m a 1 m; la mitad oeste es arena y la este bosque
    gx, gy = np.meshgrid(np.arange(41.0), np.arange(21.0))
    pos = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    idx = np.arange(gx.size).reshape(gx.shape)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    cls = np.full((20, 40), COB.BOSQUE, np.uint8)
    cls[:, :20] = COB.ARENA
    cob = SimpleNamespace(rect=(0, 0, 40, 20), res=1.0, W=40, H=20, cls=cls)
    partes = dict(terrain.fisica_del_suelo(pos, faces, cob))
    assert set(partes) == {"SAND", "DIRT"} and sum(len(f) for f in partes.values()) == len(faces)
    assert pos[partes["SAND"]][:, :, 0].max() <= 20 and pos[partes["DIRT"]][:, :, 0].min() >= 20
    # sin mapa, pasto; y una ladera de 45° pisa como roca
    assert [k for k, _ in terrain.fisica_del_suelo(pos, faces, None)] == ["GRASS"]
    ladera = pos.copy()
    ladera[:, 2] = ladera[:, 0]
    assert [k for k, _ in terrain.fisica_del_suelo(ladera, faces, cob)] == ["MXROCA"]


def test_veredas_solo_si_se_piden():
    vias = [([(-300, 0), (300, 0)], {"highway": "track"}), ([(0, 0), (0, 300)], {"highway": "path", "motor_vehicle": "no"}),
            ([(0, 0), (0, -300)], {"highway": "footway"})]
    assert {e.cls for e in _red(vias).edges} == {"track"}
    net = _red(vias, veredas=True)
    assert {e.cls for e in net.edges} == {"track", "path"}
    senda = next(e for e in net.edges if e.cls == "path")
    assert senda.phys == "MXTIERRA" and 2.0 < 2 * senda.hw < 3.5 and senda.perfil is not None


def test_vado_cruza_nodos_y_baja_la_calzada():
    from shapely.geometry import box
    # el camino va de oeste a este; el tramo de en medio (vía aparte, con ford=yes) cae dentro del arroyo
    t = {"highway": "track", "surface": "gravel"}
    vias = [([(-300, 0), (-6, 0)], t), ([(-6, 0), (6, 0)], dict(t, ford="yes")), ([(6, 0), (300, 0)], t),
            ([(-300, 60), (300, 60)], {"highway": "tertiary"})]
    net = _red(vias)
    antes = {e.id: e.Z.copy() for e in net.edges}
    vados = net.vadear([box(-5, -200, 5, 200)])
    assert vados and all(abs(x) < 8 and abs(y) < 1 for x, y, _, _ in vados)
    hondo = 0.25 + roads.VADO_HONDO          # relieve llano: el agua queda 25 cm bajo el suelo
    for e in net.edges:
        baja = antes[e.id] - e.Z
        lejos = np.hypot(e.P[:, 0], e.P[:, 1]) > 5 + roads.VADO_RAMPA + 1
        assert np.allclose(baja[lejos], 0)                      # lejos del agua nada cambia (ni la carretera)
        dentro = (np.abs(e.P[:, 0]) < 4) & (np.abs(e.P[:, 1]) < 1)
        assert np.allclose(baja[dentro], hondo)                 # dentro, la calzada queda bajo el agua
    # el agua pasa VADO_HONDO por encima de la calzada
    e = next(e for e in net.edges if e.cls == "track" and np.hypot(e.P[:, 0], e.P[:, 1]).min() < 1)
    assert np.isclose(vados[0][2] - e.Z[np.argmin(np.hypot(e.P[:, 0], e.P[:, 1]))], roads.VADO_HONDO, atol=0.02)
    # un río ancho sin marcar no se vadea; lo asfaltado tampoco
    assert _red([([(-300, 0), (300, 0)], t)]).vadear([box(-70, -200, 70, 200)]) == []
    assert _red([([(-300, 0), (300, 0)], {"highway": "tertiary"})]).vadear([box(-5, -200, 5, 200)]) == []


# ------------------------------------------------------------------ repaso: cunetas, rejilla y recortes de relieve
def _terreno_llano(alto=0.0, paso=2.0, lado=400.0):
    from openlaps import terrain
    t = terrain.Terrain.__new__(terrain.Terrain)
    t.res, t.log = paso, lambda *a: None
    n = int(round(2 * lado / paso)) + 1
    t.gx = np.linspace(-lado, lado, n)
    t.gy = np.linspace(-lado, lado, n)
    t.Z = np.full((n, n), alto)
    return t


def test_z_plano_con_rejilla_que_no_es_multiplo_del_paso():
    # 401 m entre 200 celdas: el paso real es 2.005 m, no los 2 m nominales
    t = _terreno_llano(lado=200.5)
    GX, GY = np.meshgrid(t.gx, t.gy)
    t.Z = 0.1 * GX + 0.05 * GY                      # un plano: la altura exacta se conoce en cualquier punto
    x, y = np.array([-200.5, 0.3, 123.4, 199.9]), np.array([10.0, -77.7, 150.2, -199.0])
    assert np.allclose(t.z_plano(t.Z, x, y), 0.1 * x + 0.05 * y, atol=1e-9)


def test_cunetas_a_los_lados_del_camino():
    t = _terreno_llano()
    net = _red([([(-300, 0), (300, 0)], {"highway": "track", "surface": "gravel"}),
                ([(-300, 100), (300, 100)], {"highway": "tertiary"})])
    camino = next(e for e in net.edges if e.cls == "track")
    from shapely.geometry import LineString
    calzada = LineString(camino.P).buffer(camino.hw * 1.1, cap_style="flat")
    metros = t.cunetas(net, ocupado=calzada)
    assert 800 < metros < 1250                       # las dos orillas de ~600 m, menos los extremos
    resto, franja = t.parches[-2], t.parches[-1]
    # la franja baja ~25 cm junto al camino y solo ahí; lo recortado del terreno conserva su altura exacta
    assert -0.45 < franja[0][:, 2].min() < -0.2 and np.abs(franja[0][:, 1]).max() < camino.hw * 1.2 + 2.5
    assert np.allclose(resto[0][:, 2], 0.0)
    assert t.quitar1.any() and not t.quitar1[np.abs(t.gy[:-1] - 100) < 4].any()     # la carretera asfaltada, sin cuneta
    assert _terreno_llano().cunetas(_red([([(-300, 0), (300, 0)], {"highway": "tertiary"})])) == 0.0


def test_recorte_de_relieve_encajado_en_los_pixeles(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from openlaps.geo import BBox
    from openlaps.sources import rasters
    datos = np.random.default_rng(3).uniform(0, 100, (60, 60)).astype(np.float32)
    ruta = str(tmp_path / "mde.tif")
    with rasterio.open(ruta, "w", driver="GTiff", height=60, width=60, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(10.0, 50.0, 0.001, 0.001)) as f:
        f.write(datos, 1)
    # dos cajas que se solapan y no empiezan en un borde de pixel: el mismo sitio debe dar la misma altura
    a = rasters._read_tiles([ruta], BBox(49.9612, 10.0103, 49.9871, 10.0412), 0.0, print)
    b = rasters._read_tiles([ruta], BBox(49.9655, 10.0188, 49.9893, 10.0507), 0.0, print)
    for r in (a, b):
        fil = round((50.0 - r.transform.f) / 0.001, 6)
        col = round((r.transform.c - 10.0) / 0.001, 6)
        assert fil == int(fil) and col == int(col)                       # empieza en un borde de pixel del original
        assert np.array_equal(r.data, datos[int(fil):int(fil) + r.data.shape[0], int(col):int(col) + r.data.shape[1]])
