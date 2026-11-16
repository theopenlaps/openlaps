"""Edificios de Google 3D (opcional): instalar/quitar el script sin tocar lo demás y leer la calzada del .glb."""
import os

import numpy as np

from openlaps import google3d as G


def test_instalar_y_quitar_respeta_el_ini(tmp_path):
    ext = tmp_path / "extension"
    ext.mkdir()
    base = "[BASIC]\nLATITUDE=40.4\n\n[SCRIPT_0]\nSCRIPT=otro.lua\n"
    (ext / "ext_config.ini").write_text(base, encoding="utf-8")
    n = G.instalar(str(tmp_path), 8800)
    assert n == 1
    ini = (ext / "ext_config.ini").read_text(encoding="utf-8")
    assert "SCRIPT=google3d_ac.lua" in ini and "SCRIPT=otro.lua" in ini
    lua = (ext / G.LUA).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8800" in lua and "8772" not in lua
    G.instalar(str(tmp_path), 8801)                      # reinstalar no duplica
    assert (ext / "ext_config.ini").read_text(encoding="utf-8").count("google3d_ac.lua") == 1
    assert G.quitar(str(tmp_path))
    assert not (ext / G.LUA).exists()
    assert (ext / "ext_config.ini").read_text(encoding="utf-8").strip() == base.strip()
    assert not G.quitar(str(tmp_path))                   # apagado: no hay nada que quitar


def test_calles_desde_glb(tmp_path):
    from openlaps import export_glb
    from openlaps.mesh import Scene
    sc = Scene()
    from PIL import Image
    sc.images["gris"] = Image.new("RGB", (4, 4), (80, 80, 80))
    sc.textures["gris"] = None
    sc.material("asfalto", "gris")
    pos = np.array([[0, 0, 650.0], [20, 0, 650.0], [20, 8, 651.0], [0, 8, 651.0]])
    sc.add("calzada", "asfalto", pos, pos[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]), phys="ROAD")
    ruta = str(tmp_path / "p.glb")
    export_glb.write(sc, ruta, {})
    c = G.Calles(ruta)
    # glTF: x este, y arriba, z sur (z = -norte)
    assert c.en(c.via, np.array([10.0]), np.array([-4.0]))[0]
    assert not c.en(c.via, np.array([10.0]), np.array([-30.0]))[0]
    assert c.en(c.franja, np.array([10.0]), np.array([-10.0]))[0]
    assert abs(float(c.suelo(np.array([10.0]), np.array([-4.0]))[0]) - 650.5) < 0.2
    assert abs(float(c.suelo(np.array([10.0]), np.array([-40.0]))[0]) - 651.0) < 0.2   # lejos: la calzada más cercana
