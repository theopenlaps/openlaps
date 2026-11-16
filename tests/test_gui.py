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
    a = G.argumentos({"modo": "libre", "centro": [40.14, -3.42], "ancho": 1200, "alto": 600, "calidad": "rapida",
                      "ac": True, "sin": ["cc0", "inventada"], "vegetacion": 0.3}, "salida", nombre="x")
    # la zona es un rectángulo de ancho x alto: va como --bbox
    assert BBox.parse(a[a.index("--bbox") + 1]) == BBox.around(40.14, -3.42, 1200, 600).__class__(
        *[round(v, 6) for v in (lambda b: (b.south, b.west, b.north, b.east))(BBox.around(40.14, -3.42, 1200, 600))])
    assert "--ruta" not in a and "--centro" not in a
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
    op = {"modo": "libre", "centro": [40.4215, -3.714], "ancho": 900, "alto": 1500}
    a = G.argumentos(op, "s", nombre="x")
    assert _clave(caja_de(op)) == _clave(BBox.parse(a[a.index("--bbox") + 1]))
    # compatibilidad: una vista guardada con solo "tam" sigue siendo un cuadrado
    assert G.medidas({"tam": 700}) == (700.0, 700.0)


def test_limite_de_zona_y_modo_en_vivo():
    assert G.MAX_KM2 == 24
    G.argumentos({"modo": "libre", "centro": [40.4, -3.7], "ancho": 6000, "alto": 4000}, "s", nombre="x")   # 24 km²
    with pytest.raises(ValueError):
        G.argumentos({"modo": "libre", "centro": [40.4, -3.7], "ancho": 6000, "alto": 4100}, "s", nombre="x")
    with pytest.raises(ValueError):     # con el modo en vivo activado no se genera una pista normal
        G.argumentos({"modo": "libre", "centro": [40.4, -3.7], "ancho": 1000, "alto": 1000, "vivo": True}, "s")
    from openlaps.gui.envivo import zona_recortada
    assert zona_recortada({"ancho": 3000, "alto": 2000}) == (3000.0, 2000.0)
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


def test_renombrar_pista_y_colision(tmp_path):
    from openlaps.gui import assetto
    sal = tmp_path / "salida"
    sal.mkdir()
    (sal / "vieja.glb").write_bytes(b"glb")
    (sal / "vieja.json").write_text("{}", encoding="utf-8")
    ac_dir = sal / "assetto_corsa" / "vieja"
    (ac_dir / "ui").mkdir(parents=True)
    (ac_dir / "vieja.kn5").write_bytes(b"kn5")
    (ac_dir / "models.ini").write_text("[MODEL_0]\nFILE=vieja.kn5\n", encoding="utf-8")
    (ac_dir / "ui" / "ui_track.json").write_text('{"name": "vieja"}', encoding="utf-8")

    assetto.renombrar(None, sal, "vieja", "nueva")
    assert not (sal / "vieja.glb").exists() and (sal / "nueva.glb").exists()
    assert (sal / "assetto_corsa" / "nueva" / "nueva.kn5").exists()
    assert "FILE=nueva.kn5" in (sal / "assetto_corsa" / "nueva" / "models.ini").read_text(encoding="utf-8")
    assert json.loads((sal / "assetto_corsa" / "nueva" / "ui" / "ui_track.json").read_text(encoding="utf-8"))["name"] == "nueva"

    (sal / "ocupada.glb").write_bytes(b"x")
    with pytest.raises(RuntimeError):
        assetto.renombrar(None, sal, "nueva", "ocupada")


def test_macro_rasters_y_msbuildings_indice(tmp_path):
    import gzip
    from openlaps.sources import msbuildings as MB, rasters as R
    chico = BBox(19.430, -99.135, 19.434, -99.131)
    m = R._macro(chico, 0.02, 0.036)
    assert m.south <= chico.south and m.north >= chico.north and m.west <= chico.west and m.east >= chico.east
    grande = BBox(19.40, -99.20, 19.48, -99.10)
    assert R._macro(grande, 0.02, 0.036) == grande

    gz = tmp_path / "test.csv.gz"
    ok = '{"type":"Feature","properties":{"height":12.0},"geometry":{"type":"Polygon","coordinates":[[[-99.133,19.432],[-99.132,19.432],[-99.132,19.433],[-99.133,19.432]]]}}\n'
    fuera = '{"type":"Feature","properties":{"height":8.0},"geometry":{"type":"Polygon","coordinates":[[[-99.250,19.550],[-99.249,19.550],[-99.249,19.551],[-99.250,19.550]]]}}\n'
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write(ok + fuera)
    idx = MB._asegurar_indice(str(gz), log=lambda *a: None)
    assert idx.endswith(".v2.idx.sqlite")
    import sqlite3
    with sqlite3.connect(idx) as con:
        rows = con.execute("SELECT linea FROM b WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
                           (chico.west, chico.east, chico.south, chico.north)).fetchall()
    assert len(rows) == 1 and "12.0" in rows[0][0]
