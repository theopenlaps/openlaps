"""Cobertura del suelo: clases de OSM, ríos dibujados como línea, mar desde la costa y cauces secos."""
import numpy as np
import pytest

from openlaps import cobertura as COB
from openlaps.geo import BBox, LocalProjection

LAT, LON = 40.0, -3.0


@pytest.fixture
def proj():
    return LocalProjection(BBox.around(LAT, LON, 400))


def _ll(proj, pts):
    lon, lat = proj.to_lonlat(np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float))
    return list(zip(lon.tolist(), lat.tolist()))


def _rect(proj):
    return (proj.xmin, proj.ymin, proj.xmax, proj.ymax)


def test_clase_osm():
    assert COB.clase_osm({"natural": "water"}) == COB.AGUA
    assert COB.clase_osm({"landuse": "reservoir"}) == COB.AGUA
    assert COB.clase_osm({"landuse": "forest"}) == COB.BOSQUE
    assert COB.clase_osm({"natural": "beach"}) == COB.ARENA
    assert COB.clase_osm({"natural": "scree"}) == COB.ROCA
    assert COB.clase_osm({"natural": "wetland"}) == COB.HUMEDAL
    assert COB.clase_osm({"amenity": "school"}) is None


def test_mar_desde_costa_queda_a_la_derecha():
    rect = (-100, -100, 100, 100)
    # costa recta de oeste a este: tierra a la izquierda (norte), mar a la derecha (sur)
    mar = COB.mar_desde_costa([np.array([[-150, 0], [150, 0]])], rect)
    assert mar is not None
    assert mar.area == pytest.approx(200 * 100, rel=0.01)
    assert mar.centroid.y < 0
    # al revés, el mar queda al norte
    mar2 = COB.mar_desde_costa([np.array([[150, 0], [-150, 0]])], rect)
    assert mar2.centroid.y > 0


def test_rio_linea_lago_y_cauce_seco(proj):
    rio = {"tags": {"waterway": "river", "width": "10"}, "coords": _ll(proj, [(-200, -50), (200, -50)])}
    arroyo = {"tags": {"waterway": "stream", "intermittent": "yes"}, "coords": _ll(proj, [(-200, 80), (200, 80)])}
    lago = {"tags": {"natural": "water"}, "outer": _ll(proj, [(0, 0), (40, 0), (40, 40), (0, 40), (0, 0)]), "inners": []}
    bosque = {"tags": {"landuse": "forest"}, "outer": _ll(proj, [(-150, 100), (-50, 100), (-50, 180), (-150, 180),
                                                                  (-150, 100)]), "inners": []}
    osm = {"ways": [rio, arroyo], "areas": [lago, bosque], "coast": []}
    cob = COB.Cobertura(osm, proj, _rect(proj), season="seca", res=2.0, log=lambda *a: None)
    assert len(cob.quieta) == 1 and cob.quieta[0].area == pytest.approx(1600, rel=0.05)
    assert sum(g.area for g in cob.corriente) == pytest.approx(10 * (proj.xmax - proj.xmin), rel=0.05)
    assert len(cob.secos) == 1                       # en temporada seca el arroyo intermitente no lleva agua
    clases = cob.stats["clases_pct"]
    assert clases["agua"] > 0 and clases["bosque"] > 0 and clases["cauce seco"] > 0
    # en temporada de lluvias el mismo arroyo sí es agua
    cob2 = COB.Cobertura(osm, proj, _rect(proj), season="lluvias", res=2.0, log=lambda *a: None)
    assert not cob2.secos and len(cob2.corriente) == 2


def test_alcantarilla_no_es_agua(proj):
    tubo = {"tags": {"waterway": "stream", "tunnel": "culvert"}, "coords": _ll(proj, [(-200, 0), (200, 0)])}
    cob = COB.Cobertura({"ways": [tubo], "areas": [], "coast": []}, proj, _rect(proj), res=2.0, log=lambda *a: None)
    assert cob.agua is None


def test_panel_se_dibuja(proj):
    lago = {"tags": {"natural": "water"}, "outer": _ll(proj, [(0, 0), (40, 0), (40, 40), (0, 40), (0, 0)]), "inners": []}
    cob = COB.Cobertura({"ways": [], "areas": [lago], "coast": []}, proj, _rect(proj), res=4.0, log=lambda *a: None)
    img = cob.panel(None, "prueba")
    assert img.width > 3 * cob.W * 0.9


class _TerrenoFalso:
    """Valle en V que baja hacia el este (pendiente 2 %), en una rejilla de 2 m."""

    def __init__(self):
        self.gx = np.arange(-100, 101, 2.0)
        self.gy = np.arange(-60, 61, 2.0)
        GX, GY = np.meshgrid(self.gx, self.gy)
        self.Z0 = self.z(GX, GY)
        self.Z = self.Z0.copy()

    def z(self, x, y):
        return 100.0 - 0.02 * np.asarray(x, float) + 0.15 * np.abs(np.asarray(y, float))


def test_rio_baja_con_el_valle_y_se_excava():
    from shapely.geometry import LineString, box

    from openlaps.agua import Agua

    class Cob:
        quieta = [box(-30, 30, -10, 50)]                      # charca en la ladera
        corriente = [LineString([(-100, 0), (100, 0)]).buffer(4, cap_style="flat")]

    terr = _TerrenoFalso()
    ag = Agua(Cob(), terr, log=lambda *a: None)
    w_oeste, w_este = ag.lamina(np.array([-80.0]), np.array([0.0]))[0], ag.lamina(np.array([80.0]), np.array([0.0]))[0]
    assert w_este < w_oeste                                   # la lámina baja con el río
    assert w_oeste < terr.z(-80, 6)                          # y queda por debajo de las orillas
    ag.tallar()
    j, i = np.searchsorted(terr.gy, 0.0), np.searchsorted(terr.gx, 0.0)
    assert terr.Z[j, i] < ag.lamina(np.array([0.0]), np.array([0.0]))[0]   # cauce bajo el agua
    # la charca tiene una sola cota y el terreno de dentro queda por debajo
    j2, i2 = np.searchsorted(terr.gy, 40.0), np.searchsorted(terr.gx, -20.0)
    assert terr.Z[j2, i2] < ag.nivel[0]
