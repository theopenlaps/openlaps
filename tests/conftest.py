import os
import sys

import numpy as np
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))   # para importar kn5_check


@pytest.fixture
def tiny_scene():
    """Escena mínima: un cuadro de asfalto con colisión, un poste sin física y un punto de salida."""
    from openlaps.mesh import Scene

    s = Scene()
    img = Image.new("RGB", (64, 64), (90, 90, 90))
    s.textures["asfalto.png"] = b""      # el exportador usa la imagen PIL de scene.images
    s.images["asfalto.png"] = img
    s.material("asfalto", "asfalto.png")
    pos = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
    uv = pos[:, :2] / 10
    s.add("calles", "asfalto", pos, uv, [[0, 1, 2], [0, 2, 3]], phys="ROAD")
    s.add("detalles", "asfalto", pos + [0, 0, 1], uv, [[0, 1, 2], [0, 2, 3]])
    s.dummy("AC_START_0", (5, 5, 0.5), (0, 1, 0))
    return s
