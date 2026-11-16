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
