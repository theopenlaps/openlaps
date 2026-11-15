"""La interfaz de escritorio: opciones -> línea de comandos, caja de la precarga y servidor local."""
import json
import urllib.error
import urllib.request

import pytest

from openlaps.geo import BBox
from openlaps.gui import generador as G
from openlaps.gui import servidor
from openlaps.gui.precarga import _clave, caja_de


def test_nombre_valido():
    assert G.nombre_valido("Chinchón centro") == "chinchon_centro"
    assert G.nombre_valido("  ") == "pista"
    assert len(G.nombre_valido("x" * 100)) == 40


def test_argumentos_zona_y_calidad():
    a = G.argumentos({"modo": "libre", "centro": [40.14, -3.42], "tam": 800, "calidad": "rapida", "ac": True,
                      "sin": ["cc0", "inventada"], "vegetacion": 0.3}, "salida", nombre="x")
    assert a[a.index("--centro") + 1] == "40.140000,-3.420000"
    assert a[a.index("--tam") + 1] == "800"
    assert "--ac" in a and "--sin-cc0" in a and "--sin-inventada" not in a
    # la vegetación elegida va después de la de la calidad: en argparse gana la última
    assert a[len(a) - 1 - a[::-1].index("--vegetacion") + 1] == "0.30"


def test_borrador_no_exporta_ac():
    a = G.argumentos({"modo": "libre", "centro": [1, 2], "calidad": "borrador", "ac": True}, "b", nombre="borrador")
    assert "--ac" not in a and "--sin-horizonte" in a


def test_ruta_necesita_puntos():
    with pytest.raises(ValueError):
        G.argumentos({"modo": "tramo", "ruta": [[1, 2]]}, "s")
    with pytest.raises(ValueError):
        G.argumentos({"modo": "circuito", "ruta": [[1, 2], [1, 3]]}, "s")


def test_caja_de_precarga_coincide_con_el_generador():
    """La precarga solo sirve si su caja es idéntica a la que construye pipeline.run."""
    op = {"modo": "libre", "centro": [40.4215, -3.714], "tam": 900}
    a = G.argumentos(op, "s", nombre="x")
    lat, lon = (float(v) for v in a[a.index("--centro") + 1].split(","))
    assert _clave(caja_de(op)) == _clave(BBox.around(lat, lon, float(a[a.index("--tam") + 1])))
    op = {"modo": "tramo", "ruta": [[23.63, -105.84], [23.635, -105.83]]}
    a = G.argumentos(op, "s", nombre="x")
    assert _clave(caja_de(op)) == _clave(BBox.parse(a[a.index("--bbox") + 1]))


class _Api:
    def hola(self, nombre):
        return {"hola": nombre}

    def _secreto(self):
        return "no"


@pytest.fixture
def srv(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "fuera.txt").write_text("secreto", encoding="utf-8")
    s, puerto = servidor.iniciar({"app": tmp_path / "web"}, _Api(), servidor.Eventos(), "tok")
    yield f"http://127.0.0.1:{puerto}"
    s.shutdown()


def _post(url, token, cuerpo=b"[]"):
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers={"X-OpenLaps-Token": token})
    return urllib.request.urlopen(req, timeout=5)


def test_servidor_api_con_token(srv):
    with _post(f"{srv}/api/hola", "tok", json.dumps(["mundo"]).encode()) as r:
        assert json.load(r) == {"hola": "mundo"}
    for metodo, token in (("hola", "malo"), ("_secreto", "tok")):
        with pytest.raises(urllib.error.HTTPError):
            _post(f"{srv}/api/{metodo}", token)


def test_servidor_no_sale_de_su_carpeta(srv):
    with urllib.request.urlopen(f"{srv}/app/index.html", timeout=5) as r:
        assert r.read() == b"ok"
    for ruta in ("/app/../fuera.txt", "/app/%2e%2e/fuera.txt", "/otra/index.html"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(srv + ruta, timeout=5)
