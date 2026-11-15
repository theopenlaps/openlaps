"""Perfil de Estados Unidos y arreglos de calles (velocidades en mph, estacionamiento, rasante)."""
import numpy as np
import pytest

from openlaps import regions, roads


@pytest.mark.parametrize("lat,lon,code", [
    (37.79, -122.41, "us"),     # San Francisco
    (29.76, -95.37, "us"),      # Houston (dentro de la caja de México)
    (31.76, -106.49, "us"),     # El Paso
    (31.69, -106.42, "mx"),     # Ciudad Juárez
    (32.51, -117.04, "mx"),     # Tijuana
    (32.72, -117.16, "us"),     # San Diego
    (25.90, -97.50, "us"),      # Brownsville
    (25.87, -97.50, "mx"),      # Matamoros
    (43.65, -79.38, "generic"),  # Toronto (Canadá)
    (49.28, -123.12, "generic"),  # Vancouver
    (61.2, -149.9, "us"),       # Anchorage
    (21.3, -157.86, "us"),      # Honolulu
])
def test_deteccion_con_fronteras(lat, lon, code):
    assert regions.detect(lat, lon).code == code


def test_velocidad_en_mph():
    assert roads._velocidad("25 mph") == pytest.approx(40.2, abs=0.1)
    assert roads._velocidad("50") == 50
    assert roads._velocidad("none") is None


def test_estacionamiento():
    regions.set_active("es")
    assert roads._estacionamiento({"parking:both": "lane"}, "residential", True) == pytest.approx(4.6)
    assert roads._estacionamiento({"parking:lane:right": "diagonal", "parking:lane:left": "no"},
                                  "residential", True) == pytest.approx(4.6)
    assert roads._estacionamiento({}, "residential", True) == 0.0       # en España no se supone
    regions.set_active("us")
    try:
        assert roads._estacionamiento({}, "residential", True) == pytest.approx(4.6)
        assert roads._estacionamiento({}, "residential", False) == 0.0  # fuera de ciudad no
        assert roads._estacionamiento({}, "motorway", True) == 0.0
        assert roads._estacionamiento({"parking:both": "no"}, "residential", True) == 0.0
    finally:
        regions.set_active("mx")


def test_senal_de_velocidad_en_mph():
    from openlaps import textures
    regions.set_active("us")
    try:
        img = textures.sign_speed(40.2)          # 25 mph
        a = np.asarray(img)
        assert a[..., 3].max() == 255
        # R2-1 es un rectángulo blanco sin el círculo rojo de Europa
        assert (a[..., 0] > 180).sum() > 0 and ((a[..., 0] > 150) & (a[..., 1] < 60)).sum() == 0
    finally:
        regions.set_active("mx")


def test_acuerdos_verticales_quitan_el_bache():
    s = np.arange(0, 120, 1.5)
    z = 0.02 * s + 0.6 * np.exp(-((s - 60) / 4.0) ** 2)      # rampa con un bache de 60 cm
    z2 = roads._acuerdos_verticales(s, z, 40.0)
    k = np.diff(np.diff(z2) / 1.5) / 1.5
    assert np.abs(k).max() <= 1.0 / roads.radio_vertical_min(40.0) + 1e-3
    assert z2[0] == z[0] and z2[-1] == z[-1]                 # los extremos (cruces) no se mueven
    assert np.abs(z2 - z).max() <= 2.0 + 1e-9
