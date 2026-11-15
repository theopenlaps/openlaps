"""Edificios del Catastro: lectura del GML INSPIRE y sustitución de las huellas de OSM."""
import pytest
from pyproj import Transformer

from openlaps import buildings as B
from openlaps.geo import BBox, LocalProjection
from openlaps.sources import catastro

# Dos partes de la misma parcela (6 y 1 plantas) y un sótano sin plantas sobre rasante,
# en la Puerta del Sol (ETRS89 / UTM 30, EPSG:25830).
GML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<gml:FeatureCollection gml:id="ES.SDGC.BU">
<gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0443101VK4704C_part1">
 <gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">
 440300 4474250 440320 4474250 440320 4474270 440300 4474270 440300 4474250</gml:posList>
 </gml:LinearRing></gml:exterior></gml:PolygonPatch>
 <bu-ext2d:numberOfFloorsAboveGround>6</bu-ext2d:numberOfFloorsAboveGround>
</bu-ext2d:BuildingPart></gml:featureMember>
<gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0443101VK4704C_part2">
 <gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">
 440320 4474250 440330 4474250 440330 4474270 440320 4474270 440320 4474250</gml:posList>
 </gml:LinearRing></gml:exterior></gml:PolygonPatch>
 <bu-ext2d:numberOfFloorsAboveGround>1</bu-ext2d:numberOfFloorsAboveGround>
</bu-ext2d:BuildingPart></gml:featureMember>
<gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.0443101VK4704C_part3">
 <gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">
 440300 4474230 440310 4474230 440310 4474240 440300 4474240 440300 4474230</gml:posList>
 </gml:LinearRing></gml:exterior></gml:PolygonPatch>
 <bu-ext2d:numberOfFloorsAboveGround>0</bu-ext2d:numberOfFloorsAboveGround>
</bu-ext2d:BuildingPart></gml:featureMember>
</gml:FeatureCollection>"""


@pytest.fixture
def parts(tmp_path):
    p = tmp_path / "bu.gml"
    p.write_text(GML, encoding="latin-1")
    inv = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)
    return catastro.parse(str(p), inv)


def test_parse_reads_floors_and_geometry(parts):
    assert [p["floors"] for p in parts] == [6, 1, 0]
    assert {p["ref"] for p in parts} == {"0443101VK4704C"}
    lon, lat = parts[0]["outer"][0]
    assert lon == pytest.approx(-3.7036, abs=1e-3) and lat == pytest.approx(40.4168, abs=1e-3)


def test_catastro_replaces_osm_footprint_and_keeps_its_tags(parts):
    proj = LocalProjection(BBox.around(40.4168, -3.7036, 400))
    rect = (proj.xmin, proj.ymin, proj.xmax, proj.ymax)
    # una huella de OSM que cubre las dos partes, con color de fachada y una altura que no se hereda
    ring = [parts[0]["outer"][0], parts[1]["outer"][1], parts[1]["outer"][2], parts[0]["outer"][3],
            parts[0]["outer"][0]]
    osm = {"areas": [{"tags": {"building": "yes", "building:colour": "#e0d0b0", "building:levels": "2"},
                      "outer": ring, "inners": []}]}
    out = B.collect(osm, [], proj, rect, log=lambda *a: None, cat_parts=parts)
    assert all(b["src"] == "catastro" for b in out)
    floors = sorted(b["floors"] for b in out)
    assert floors == [1, 6]                       # el sótano (0 plantas) no se construye
    tall = max(out, key=lambda b: b["floors"])
    assert tall["tags"].get("building:colour") == "#e0d0b0"
    assert "building:levels" not in tall["tags"]
    assert B.height_of(tall, True) == pytest.approx(B.cat_height(6))


def test_churches_stay_from_osm(parts):
    proj = LocalProjection(BBox.around(40.4168, -3.7036, 400))
    rect = (proj.xmin, proj.ymin, proj.xmax, proj.ymax)
    ring = parts[0]["outer"]
    osm = {"areas": [{"tags": {"building": "church"}, "outer": ring, "inners": []}]}
    out = B.collect(osm, [], proj, rect, log=lambda *a: None, cat_parts=parts)
    srcs = sorted(b["src"] for b in out)
    assert srcs == ["catastro", "osm"]            # la iglesia de OSM y la parte de 1 planta
    assert next(b for b in out if b["src"] == "catastro")["floors"] == 1
