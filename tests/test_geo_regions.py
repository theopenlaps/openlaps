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


def test_ac_georef_matches_projection():
    from openlaps.export_ac import georef
    for bb in (BBox.around(19.43, -99.13, 800), BBox.around(40.1405, -3.4226, 800), BBox.around(-33.9, 151.2, 500)):
        proj = LocalProjection(bb)
        g = georef({"lat": proj.lat0, "lon": proj.lon0})  # sin epsg: se deduce igual que LocalProjection
        assert g["epsg"] == proj.epsg
        assert (g["lat0"], g["lon0"]) == (proj.lat0, proj.lon0)


@pytest.mark.parametrize("lat, lon, sub", [
    (19.43, -99.13, "mx_ciudad"),      # Ciudad de México
    (19.60, -99.05, "mx_ciudad"),      # Ecatepec (Valle de México)
    (18.985, -99.10, "mx_colonial"),   # Tepoztlán
    (21.02, -101.26, "mx_colonial"),   # Guanajuato
    (28.63, -106.08, "mx_norte"),      # Chihuahua
    (20.97, -89.62, "mx_sureste"),     # Mérida
    (20.10, -103.90, "mx_pueblo"),     # un pueblo de Jalisco
    (40.4168, -3.7038, ""),            # Madrid: el perfil de España
    (37.39, -5.99, "es_sur"),          # Sevilla
    (42.88, -8.54, "es_norte"),        # Santiago de Compostela
    (41.39, 2.17, "es_mediterraneo"),  # Barcelona
    (28.46, -16.25, "es_canarias"),
    (43.30, 5.37, "fr_sur"),           # Marsella
    (34.05, -118.25, "us_suroeste"),   # Los Ángeles
    (37.79, -122.41, "us_pacifico"),   # San Francisco
    (40.71, -74.00, ""),               # Nueva York
])
def test_subregion_por_lugar(lat, lon, sub):
    assert regions.elegir("auto", "auto", lat, lon).sub == sub


def test_subregion_elegida_y_coste():
    """Elegida a mano manda; una de otro país o «no» dejan el perfil del país. Ninguna añade materiales: las
    mismas 8 fachadas y las mismas normas de señalamiento que su país."""
    assert regions.elegir("mx", "mx_colonial", 19.43, -99.13).sub == "mx_colonial"
    assert regions.elegir("mx", "no", 19.43, -99.13) is regions.MX
    assert regions.elegir("es", "mx_norte", 40.0, -3.0) is regions.ES
    assert regions.elegir("auto", "es_sur", 19.43, -99.13) is regions.MX       # de otro país: no vale
    assert regions.set_active("es_sur").code == "es"
    regions.set_active("mx")
    assert set(regions._CAJAS) == set(regions.SUBS)
    for s in regions.SUBS.values():
        base = regions.REGIONS[s.code]
        assert len(s.facade_colors) == len(base.facade_colors) == 8 and s.sub_name and s.sub_name_en
        assert (s.center_mat, s.stop_text, s.ortho, s.dem, s.country) == \
            (base.center_mat, base.stop_text, base.ortho, base.dem, base.country)
