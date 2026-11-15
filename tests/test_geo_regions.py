import numpy as np
import pytest

from openlaps import regions
from openlaps.geo import BBox, LocalProjection


def test_bbox_around_has_requested_size():
    bb = BBox.around(19.43, -99.13, 800)
    proj = LocalProjection(bb)
    assert proj.xmax - proj.xmin == pytest.approx(800, rel=0.01)
    assert proj.ymax - proj.ymin == pytest.approx(800, rel=0.01)


def test_projection_is_centred_and_round_trips():
    bb = BBox.parse("40.5515,-3.5050,40.5685,-3.4740")
    proj = LocalProjection(bb)
    x, y = proj.to_local(proj.lon0, proj.lat0)
    assert abs(float(x)) < 1e-6 and abs(float(y)) < 1e-6
    lon = np.array([-3.50, -3.48])
    lat = np.array([40.56, 40.555])
    lon2, lat2 = proj.to_lonlat(*proj.to_local(lon, lat))
    np.testing.assert_allclose(lon2, lon, atol=1e-9)
    np.testing.assert_allclose(lat2, lat, atol=1e-9)


def test_padded_grows_every_side():
    bb = BBox(10, 20, 11, 21)
    p = bb.padded(100)
    assert p.south < bb.south and p.west < bb.west and p.north > bb.north and p.east > bb.east


@pytest.mark.parametrize("lat,lon,code", [
    (18.9853, -99.0995, "mx"),     # Tepoztlán
    (40.1405, -3.4226, "es"),      # Chinchón
    (28.46, -16.25, "es"),         # Canarias
    (48.85, 2.35, "fr"),           # París
    (52.52, 13.40, "eu"),          # Berlín
    (35.68, 139.69, "generic"),    # Tokio
])
def test_region_detection(lat, lon, code):
    assert regions.detect(lat, lon).code == code


def test_every_region_has_attribution_list():
    for r in regions.REGIONS.values():
        assert isinstance(r.attribution, list)
