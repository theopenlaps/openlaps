"""Tramo en vivo: recorte de pedazos, marco común y conversión de la API oficial de OSM."""
import numpy as np

from openlaps import vivo as V
from openlaps.geo import LocalProjection
from openlaps.mesh import Scene
from openlaps.sources import osm as OSM


def _malla_grande(scene):
    # una franja de 600 m de triángulos de 10 m (como una calle que cruza varios pedazos)
    xs = np.arange(-300.0, 301.0, 10.0)
    pos = np.array([[x, y, 0.0] for x in xs for y in (-5.0, 5.0)])
    f = []
    for k in range(len(xs) - 1):
        a, b, c, d = 2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2
        f += [(a, d, c), (a, c, b)]
    scene.add("carretera", "asfalto", pos, pos[:, :2], f, phys="ROAD")


def _arbol(scene, x, y):
    # un árbol de planos cruzados que cruza el borde: va entero a un solo pedazo
    q = np.array([[x - 3, y, 0], [x + 3, y, 0], [x + 3, y, 8], [x - 3, y, 8],
                  [x, y - 3, 0], [x, y + 3, 0], [x, y + 3, 8], [x, y - 3, 8]], float)
    scene.add("vegetacion", "veg_pino_0", q, np.zeros((8, 2)), [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)])


def _caras(scene):
    return sum(len(p.faces) for ps in scene.pieces.values() for p in ps)


def test_recorte_sin_duplicados_ni_huecos():
    total, arboles = 0, 0
    for i in (-1, 0, 1):
        s = Scene()
        _malla_grande(s)
        _arbol(s, V.L / 2 - 1.0, 0.0)          # centro 1 m antes del borde entre los pedazos 0 y 1
        V.recortar(s, V.nucleo(i, 0))
        total += sum(len(p.faces) for (g, _, _), ps in s.pieces.items() if g == "carretera" for p in ps)
        arboles += sum(len(p.faces) for (g, _, _), ps in s.pieces.items() if g == "vegetacion" for p in ps)
    # la franja va de -300 a 300 y los pedazos -1..1 cubren de -384 a 384: cada triángulo una sola vez
    assert total == 2 * 60
    assert arboles == 4                        # el árbol entero, y una sola vez


def test_pedazo_de_y_nucleo():
    for x, y in [(0, 0), (127.9, -127.9), (128.1, 0), (-500, 300)]:
        i, j = V.pedazo_de(x, y)
        x0, y0, x1, y1 = V.nucleo(i, j)
        assert x0 <= x < x1 and y0 <= y < y1


def test_marco_comparte_origen():
    a = LocalProjection.marco(40.4168, -3.7038, (0, 0, 256, 256))
    b = LocalProjection.marco(40.4168, -3.7038, (256, 0, 512, 256))
    assert (a.ox, a.oy) == (b.ox, b.oy)
    bb = b.bbox()
    lon, lat = b.to_lonlat(np.array([256, 512, 256, 512]), np.array([0, 0, 256, 256]))
    assert (lon >= bb.west - 1e-9).all() and (lon <= bb.east + 1e-9).all()     # la caja contiene el rectángulo
    assert (lat >= bb.south - 1e-9).all() and (lat <= bb.north + 1e-9).all()


def test_api_osm_a_formato_overpass():
    xml = b"""<osm>
      <node id="1" lat="40.0" lon="-3.0"/><node id="2" lat="40.001" lon="-3.0"/>
      <node id="3" lat="40.001" lon="-3.001"><tag k="natural" v="tree"/></node>
      <node id="4" lat="40.0" lon="-3.001"/>
      <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
      <way id="11"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="building" v="yes"/></way>
      <way id="12"><nd ref="1"/><nd ref="99"/><tag k="highway" v="service"/></way>
      <way id="13"><nd ref="1"/><nd ref="2"/><tag k="name" v="sin interes"/></way>
    </osm>"""
    els = OSM._api_a_overpass(xml)
    tipos = sorted((e["type"], e["id"]) for e in els)
    assert tipos == [("node", 3), ("way", 10), ("way", 11)]   # sin nodos sueltos, vías incompletas ni sin interés
    d = OSM.parse({"elements": els})
    assert len(d["ways"]) == 1 and len(d["areas"]) == 1 and len(d["nodes"]) == 1


def test_respaldo_de_material():
    bib = {"fachada_0", "azotea_concreto", "isleta", "banqueta", "asfalto"}
    assert V.respaldo("asfalto", bib) == "asfalto"
    assert V.respaldo("fachada_cc0a878", bib) == "fachada_0"
    assert V.respaldo("isleta_cb4b4a0", bib) == "isleta"
    assert V.respaldo("tv_1_2_003", bib) == "tv_1_2_003"
    assert V.respaldo("raro", bib) == "banqueta"


def test_pedazo_recibe_el_tunel_entero():
    """Un túnel que sale del pedazo llega completo (todos sus tramos y las calles de sus bocas) aunque esté lejos:
    así los dos pedazos de la costura calculan el mismo perfil."""
    lat0, lon0 = 40.4168, -3.7038
    proj = LocalProjection.marco(lat0, lon0, (-1, -1, 1, 1))
    t = V.TeselasOSM(proj, lambda m: None)

    def via(i, nodos, xs, **tags):
        lon, lat = proj.to_lonlat(np.array(xs, float), np.zeros(len(xs)))
        geo = [{"lon": float(a), "lat": float(b)} for a, b in zip(lon, lat)]
        el = {"type": "way", "id": i, "nodes": nodos, "geometry": geo, "tags": {"highway": "primary", **tags}}
        return el, t.caja([(g["lon"], g["lat"]) for g in geo])

    els = [via(1, [1, 2], [0, 300]),                                    # calle normal en el pedazo (0, 0)
           via(2, [2, 3], [300, 900], tunnel="yes"),                    # túnel que empieza en el borde de su OSM
           via(3, [3, 4], [900, 1400], tunnel="yes"),                   # sigue lejos, fuera de lo que toca
           via(4, [4, 5], [1400, 1600]),                                # calle de la otra boca
           via(5, [5, 6], [1600, 1900]),                                # más allá: no hace falta
           via(6, [7, 8], [1500, 1700], highway="footway", tunnel="yes")]
    for k in t.claves((-2000, -2000, 2000, 2000)):
        t.datos[k] = []
    t.datos[(0, 0)] = els[:2]
    t.datos[(1, 0)] = els[2:]
    out, ext, faltan = t.del_pedazo(0, 0)
    ids = {el["id"] for el in out}
    assert {1, 2, 3, 4} <= ids and 5 not in ids and 6 not in ids
    assert ext[2] > 1550 and not faltan
    # sin túneles no se añade nada
    t.datos[(0, 0)], t.datos[(1, 0)] = [els[0]], []
    assert t.del_pedazo(0, 0)[1] is None


def test_recortar_y_cubierto_con_exclusion():
    s = Scene()
    _malla_grande(s)
    _arbol(s, 0, 0)
    _arbol(s, 200, 0)
    V.recortar(s, (-300, -300, 300, 300), excluir=(-100, -100, 100, 100))
    for p in s.pieces[("carretera", "asfalto", "ROAD")]:
        c = p.pos[p.faces].mean(1)[:, :2]
        dentro = (c[:, 0] >= -100) & (c[:, 0] < 100) & (c[:, 1] >= -100) & (c[:, 1] < 100)
        assert not dentro.any()
    veg = [p for (g, _, _), ps in s.pieces.items() if g == "vegetacion" for p in ps]
    assert len(veg) == 1 and veg[0].pos[:, 0].mean() > 150

    v = object.__new__(V.Vivo)
    v.excluir = (-200.0, -200.0, 200.0, 200.0)
    assert v._cubierto((0, 0)) is True
    assert v._cubierto((1, 0)) is False
    v.excluir = (-100.0, -100.0, 100.0, 100.0)
    assert v._cubierto((0, 0)) is False


def test_export_normal_y_vivo_en_espera(tmp_path):
    from openlaps import export_ac

    ini = export_ac.surfaces_ini()
    assert "WAV_PITCH=extended-0" in ini
    assert "[_SCRIPTING_PHYSICS]\nALLOW_TRACK_SCRIPTS=1" in ini
    assert "WAV_PITCH=extended-0" not in export_ac.surfaces_ini(extendido=False)

    s = Scene()
    s.textures["det_tierra.png"] = b""
    s.material("asfalto", "det_tierra.png")
    s.add("carretera", "asfalto", np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float),
          np.zeros((3, 2)), [(0, 1, 2)], phys="ROAD")
    s.add("col", "invisible", np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float),
          np.zeros((3, 2)), [(0, 1, 2)], phys="WALL")
    for k in [k for k in s.pieces if k[0] == "col"]:
        del s.pieces[k]
    export_ac.agregar_biblioteca(s)
    assert any(k[:2] == ("biblioteca", "asfalto") for k in s.pieces)
    assert any(k[:2] == ("biblioteca", "tv_base") for k in s.pieces)

    g = export_ac.georef({"lat": 40.0, "lon": -3.0, "epsg": 32630,
                          "rect": [-100, -80, 100, 80], "vivo_opc": {"calidad": "rapida"}})
    assert g["lat0"] == 40.0 and g["lon0"] == -3.0
    assert g["rect"] == [-100, -80, 100, 80] and g["vivo_opc"] == {"calidad": "rapida"}

    V.escribir_lua(str(tmp_path), "", "", 40.0, -3.0, modo_espera=True,
                   rect_horneado=[-100, -80, 100, 80], pista="mi_pista")
    lua_txt = (tmp_path / "extension" / "vivo.lua").read_text(encoding="utf-8")
    assert "local PISTA_ID = 'mi_pista'" in lua_txt
    assert "local RECT_HORNEADO = { -100.0, -80.0, 100.0, 80.0 }" in lua_txt
    assert "local vivoActivo = not true" in lua_txt
    assert "ui.addSettings" in lua_txt

    assert V.actualizar_control_lua(str(tmp_path), control_url="http://127.0.0.1:9999", pista="nueva",
                                    lado=320, r_carga=500, r_soltar=750, t_foco=0.8)
    lua_upd = (tmp_path / "extension" / "vivo.lua").read_text(encoding="utf-8")
    assert "local CONTROL_URL = 'http://127.0.0.1:9999'" in lua_upd
    assert "local PISTA_ID = 'nueva'" in lua_upd
    assert "local L = 320" in lua_upd
    assert "local R_CARGA, R_SOLTAR = 500, 750" in lua_upd
    assert "local T_FOCO = 0.8" in lua_upd


def test_configurar_parametros_vivo():
    try:
        V.configurar(lado=320, margen=96, pad_osm=150, tesela_osm=768)
        assert V.nucleo(0, 0) == (-160.0, -160.0, 160.0, 160.0)
        assert V.rect_pedazo(0, 0) == (-406.0, -406.0, 406.0, 406.0)
        assert V.pedazo_de(170.0, -170.0) == (1, -1)
        assert V.TeselasOSM.lado == 768.0
    finally:
        V.configurar(lado=256.0, margen=64.0, pad_osm=200.0, tesela_osm=1024.0)


def test_layer_osm_con_multiples_valores():
    from openlaps import roads
    assert roads._layer(None) == 0
    assert roads._layer("-3;-1") == -1
    assert roads._layer("-1;0") == 0
    assert roads._layer("2") == 2
    assert roads._layer("subsuelo") == 0


def test_osm_download_respaldo_api_y_precarga_cancelable(tmp_path, monkeypatch):
    from openlaps.geo import BBox
    from openlaps.sources import cache

    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))

    def falso_post(*a, **kw):
        raise TimeoutError("overpass caido")

    llamadas_api = []

    def falsa_api(bb, log=print, timeout=30):
        llamadas_api.append(bb)
        return {"elements": [{"type": "node", "id": 1, "lat": bb.south, "lon": bb.west, "tags": {"natural": "tree"}}]}

    monkeypatch.setattr(OSM, "post_form", falso_post)
    monkeypatch.setattr(OSM, "download_api", falsa_api)
    out = OSM.download(BBox(19.41, -99.14, 19.42, -99.13), log=lambda *a: None)
    assert len(llamadas_api) == 1 and len(out["elements"]) == 1

    # un espejo que tarda no detiene la descarga: tras el escalón se pregunta a la siguiente fuente y gana la primera
    import time
    monkeypatch.setattr(OSM, "ESCALON", 0.05)

    def post_lento(ep, *a, **kw):
        time.sleep(5.0)
        raise TimeoutError("lento")

    monkeypatch.setattr(OSM, "post_form", post_lento)
    t = time.time()
    out = OSM.download(BBox(19.43, -99.14, 19.44, -99.13), log=lambda *a: None)
    assert len(llamadas_api) == 2 and len(out["elements"]) == 1 and time.time() - t < 2.0

    from openlaps.__main__ import parser as cli_parser
    ns = cli_parser().parse_args(["--centro=19.43,-99.13", "--ac", "--res", "1.5"])
    assert not hasattr(ns, "calidad") and ns.res == 1.5

    from openlaps.gui.precarga import Precarga
    pr = object.__new__(Precarga)
    import threading
    pr._cv = threading.Condition()
    pr._pendiente = ("k1", BBox(0, 0, 1, 1))
    pr._en_curso = None
    pr._hechas = {}
    assert pr.esperar("k1", timeout=5, cancelar=lambda: True) is None
