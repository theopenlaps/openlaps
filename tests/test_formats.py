"""Formatos de salida: DDS, glTF, fast_lane.ai y KN5 (sin red)."""
import json
import struct

import numpy as np
import pytest
from PIL import Image

from openlaps import CREDIT, __version__, dds, export_ac, export_glb
from openlaps.mesh import MAX_VERTS, Scene


def _dds_header(data):
    magic, size, _flags, h, w, _pitch, _depth, mips = struct.unpack("<4sIIIIIII", data[:32])
    fourcc = data[84:88]
    return magic, size, h, w, mips, fourcc


def test_dds_opaque_is_dxt1_with_full_mip_chain():
    img = Image.new("RGB", (64, 32), (200, 120, 40))
    data = dds.encode(img)
    magic, size, h, w, mips, fourcc = _dds_header(data)
    assert (magic, size, fourcc) == (b"DDS ", 124, b"DXT1")
    assert (w, h) == (64, 32)
    assert mips == 7                       # 64x32 -> ... -> 1x1
    # DXT1: 8 bytes por bloque de 4x4 (mínimo un bloque por nivel)
    expected = sum(max(1, (64 >> i) // 4) * max(1, (32 >> i) // 4) * 8 for i in range(mips))
    assert len(data) == 128 + expected


def test_dds_with_transparency_is_dxt5():
    img = Image.new("RGBA", (16, 16), (10, 200, 10, 0))
    assert _dds_header(dds.encode(img))[5] == b"DXT5"


def test_ai_line_layout():
    poly = np.column_stack([np.linspace(0, 200, 50), np.sin(np.linspace(0, 3, 50)) * 20, np.zeros(50)])
    data = export_ac._ai_bytes(poly, 3.5)
    n = len(poly)
    assert len(data) == 16 + n * 20 + 4 + n * 18 * 4 + 4
    version, count = struct.unpack("<2i", data[:8])
    assert (version, count) == (7, n)
    # la distancia acumulada del último punto es la longitud de la línea
    last = struct.unpack("<4fi", data[16 + (n - 1) * 20:16 + n * 20])
    seg = np.linalg.norm(np.diff(poly, axis=0), axis=1).sum()
    assert last[3] == pytest.approx(seg, rel=1e-4)


def test_glb_header_and_credits(tiny_scene, tmp_path):
    path = tmp_path / "t.glb"
    export_glb.write(tiny_scene, str(path))
    b = path.read_bytes()
    magic, version, length = struct.unpack("<4sII", b[:12])
    assert (magic, version, length) == (b"glTF", 2, len(b))
    n = struct.unpack("<I", b[12:16])[0]
    gltf = json.loads(b[20:20 + n])
    assert gltf["asset"]["generator"] == f"openlaps {__version__}"
    assert gltf["asset"]["copyright"] == CREDIT
    assert any(node["name"] == "AC_START_0" for node in gltf["nodes"])


def test_kn5_is_consistent(tiny_scene, tmp_path):
    import kn5_check

    path = tmp_path / "t.kn5"
    export_ac.write_kn5(tiny_scene, str(path), log=lambda *_: None)
    kn5_check.check(str(path))             # lanza AssertionError si algo no cuadra


def test_large_pieces_are_split_for_16bit_indices():
    s = Scene()
    n = 400                                # rejilla de 400x400 = 160k vértices
    xs, ys = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    pos = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(n * n)])
    i = np.arange(n - 1)
    a = (i[:, None] * n + i[None, :]).ravel()
    faces = np.concatenate([np.column_stack([a, a + 1, a + n]), np.column_stack([a + 1, a + n + 1, a + n])])
    s.add("terreno", "suelo", pos, pos[:, :2], faces, normals=np.tile([0, 0, 1.0], (n * n, 1)))
    meshes = s.meshes()
    assert len(meshes) > 1
    assert all(len(m["pos"]) <= MAX_VERTS for m in meshes)
    assert sum(len(m["faces"]) for m in meshes) == len(faces)
