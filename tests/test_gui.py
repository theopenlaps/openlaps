"""La interfaz de escritorio: opciones -> línea de comandos, caja de la precarga y servidor local."""
import json
import sys
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


def _bbox_arg(a):
    for i, x in enumerate(a):
        if x.startswith("--bbox="):
            return x.split("=", 1)[1]
        if x == "--bbox":
            return a[i + 1]
    raise AssertionError("--bbox no encontrado")


def test_argumentos_zona_y_calidad():
    from openlaps.__main__ import parser as cli_parser
    a = G.argumentos({"modo": "libre", "centro": [40.14, -3.42], "ancho": 1200, "alto": 600, "calidad": "rapida",
                      "ac": True, "sin": ["cc0", "inventada"], "vegetacion": 0.3}, "salida", nombre="x")
    # la zona es un rectángulo de ancho x alto: va como --bbox=...
    assert BBox.parse(_bbox_arg(a)) == BBox.around(40.14, -3.42, 1200, 600).__class__(
        *[round(v, 6) for v in (lambda b: (b.south, b.west, b.north, b.east))(BBox.around(40.14, -3.42, 1200, 600))])
    assert "--ruta" not in a and "--centro" not in a
    assert "--ac" in a and "--sin-cc0" in a and "--sin-inventada" not in a
    # la vegetación elegida va después de la de la calidad: en argparse gana la última
    assert a[len(a) - 1 - a[::-1].index("--vegetacion") + 1] == "0.30"

    # hemisferio sur (latitud negativa): argparse no debe confundir el '-' con una bandera
    a_sur = G.argumentos({"modo": "tramo", "centro": [-34.6037, -58.3816],
                          "ruta": [[-34.6037, -58.3816], [-34.608, -58.375]], "calidad": "normal"}, "salida", nombre="ba")
    ns = cli_parser().parse_args(a_sur)
    assert ns.bbox.startswith("-34.") and ns.ruta.startswith("-34.")


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
    assert _clave(caja_de(op)) == _clave(BBox.parse(_bbox_arg(a)))
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
    assert _clave(caja_de(op)) == _clave(BBox.parse(_bbox_arg(a)))


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


def test_renombrar_actualiza_vivo_lua(tmp_path):
    from openlaps import vivo as V
    from openlaps.gui import assetto
    sal = tmp_path / "salida"
    ac_dir = sal / "assetto_corsa" / "vieja"
    (ac_dir / "ui").mkdir(parents=True)
    (ac_dir / "vieja.kn5").write_bytes(b"kn5")
    V.escribir_lua(str(ac_dir), "", "", 40.0, -3.0, modo_espera=True, rect_horneado=[-10, -10, 10, 10], pista="vieja")
    assetto.renombrar(None, sal, "vieja", "nueva_ac")
    lua_txt = (sal / "assetto_corsa" / "nueva_ac" / "extension" / "vivo.lua").read_text(encoding="utf-8")
    assert "local PISTA_ID = 'nueva_ac'" in lua_txt


def test_control_ac_activar_vivo(tmp_path, monkeypatch):
    from openlaps.gui import ajustes, app as app_mod
    sal = tmp_path / "salida"
    pista_dir = sal / "assetto_corsa" / "demo"
    pista_dir.mkdir(parents=True)
    (pista_dir / "openlaps_geo.json").write_text(json.dumps({
        "lat0": 40.4168, "lon0": -3.7038,
        "rect": [-350.0, -350.0, 350.0, 350.0],
        "vivo_opc": {"calidad": "rapida", "region": "eu"},
    }), encoding="utf-8")
    sin_rect = sal / "assetto_corsa" / "antigua"
    sin_rect.mkdir(parents=True)
    (sin_rect / "openlaps_geo.json").write_text(json.dumps({"lat0": 40.0, "lon0": -3.0}), encoding="utf-8")

    monkeypatch.setattr(ajustes, "DATOS", tmp_path)
    monkeypatch.setattr(ajustes, "_ARCHIVO", tmp_path / "ajustes.json")
    monkeypatch.setattr(ajustes, "salida", lambda a=None: sal)
    ajustes.guardar({"ac": ""})
    api = app_mod.Api(servidor.Eventos())
    llamadas = []

    def falso_iniciar(op, s, ac=None, semilla=True, excluir=None, params=None):
        llamadas.append((op, s, semilla, excluir, params))
        api._vivo_ac.info = {"puerto": 9876, "nombre": op["nombre"], "centro": op["centro"],
                             "params": ajustes.vivo_params(params)}
        return {"puerto": 9876}

    monkeypatch.setattr(api._vivo_ac, "iniciar", falso_iniciar)
    monkeypatch.setattr(app_mod, "esperar_puerto", lambda p, timeout=15.0, proc=None: True)

    srv, puerto = app_mod._iniciar_control_ac(api, puerto=0)
    try:
        base = f"http://127.0.0.1:{puerto}"
        with pytest.raises(urllib.error.HTTPError) as e404:
            urllib.request.urlopen(f"{base}/activar?pista=no_existe&lat=40.4168&lon=-3.7038", timeout=5)
        assert e404.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as e409:
            urllib.request.urlopen(f"{base}/activar?pista=demo&lat=41.0&lon=-3.7038", timeout=5)
        assert e409.value.code == 409

        with pytest.raises(urllib.error.HTTPError) as e400:
            urllib.request.urlopen(f"{base}/activar?pista=antigua&lat=40.0&lon=-3.0", timeout=5)
        assert e400.value.code == 400

        with urllib.request.urlopen(f"{base}/parametros?lado=320&margen=96&intervalo=0.5&radio=400", timeout=5) as rp:
            rp_data = json.load(rp)
        assert rp_data["ok"] is True and rp_data["lado"] == 320 and rp_data["margen"] == 96
        assert rp_data["intervalo"] == 0.5 and rp_data["t_foco"] == 1.0 and rp_data["radio"] == 400

        with urllib.request.urlopen(f"{base}/activar?pista=demo&lat=40.4168&lon=-3.7038&lado=384&r_carga=500", timeout=5) as r:
            res = json.load(r)
        assert res["ok"] is True and res["url"] == "http://127.0.0.1:9876"
        assert res["L"] == 384 and res["r_carga"] == 500
        assert len(llamadas) == 1
        assert llamadas[0][2] is False and llamadas[0][3] == [-350.0, -350.0, 350.0, 350.0]
        assert llamadas[0][0]["calidad"] == "rapida" and llamadas[0][0]["region"] == "eu"
    finally:
        srv.shutdown()
        srv.server_close()


def test_vivo_params_y_cli_envivo(tmp_path, monkeypatch):
    from openlaps.gui import ajustes, envivo

    vp = ajustes.vivo_params({"vivo_lado": 999, "vivo_margen": 4, "vivo_intervalo": 0.01,
                              "vivo_trabajadores": 12, "vivo_r_carga": 800, "vivo_r_soltar": 500})
    assert vp["lado"] == 512 and vp["margen"] == 16 and vp["intervalo"] == 0.1
    assert vp["trabajadores"] == 8 and vp["r_carga"] == 800 and vp["r_soltar"] == 900

    captura = {}

    class FalsoProc:
        stdout = None

        def poll(self):
            return None

    def falso_popen(cmd, **kw):
        captura["cmd"] = cmd
        return FalsoProc()

    monkeypatch.setattr(envivo.subprocess, "Popen", falso_popen)
    monkeypatch.setattr(envivo.threading, "Thread", lambda *a, **kw: type("T", (), {"start": lambda self: None})())
    sv = envivo.EnVivo(lambda ev: None)
    sv.iniciar({"centro": [40.4, -3.7], "nombre": "test"}, tmp_path, semilla=False,
               excluir=[-100, -100, 100, 100],
               params={"vivo_lado": 384, "vivo_margen": 96, "vivo_intervalo": 0.4, "vivo_trabajadores": 3})
    cmd = captura["cmd"]
    assert cmd[cmd.index("--pedazo-m") + 1] == "384"
    assert cmd[cmd.index("--margen") + 1] == "96"
    assert cmd[cmd.index("--intervalo") + 1] == "0.40"
    assert cmd[cmd.index("--trabajadores") + 1] == "3"

    assert envivo.esperar_puerto(1, timeout=0.05, proc=FalsoProc()) is False


@pytest.mark.skipif(sys.platform != "win32", reason="los enlaces (junction) a content/tracks son de Windows")
def test_enlace_en_ac_renombrar_y_limpiar(tmp_path):
    """La pista enlazada se reconoce como enlazada (os.readlink devuelve la ruta con prefijo de ruta larga),
    renombrarla no deja el enlace viejo colgando y los enlaces a carpetas borradas se quitan."""
    from openlaps.gui import assetto
    ac = tmp_path / "ac"
    (ac / "content" / "tracks").mkdir(parents=True)
    sal = tmp_path / "sal"
    src = sal / "assetto_corsa" / "uno"
    (src / "ui").mkdir(parents=True)
    (src / "uno.kn5").write_bytes(b"x")
    (src / "models.ini").write_text("FILE=uno.kn5", encoding="utf-8")
    (sal / "uno.glb").write_bytes(b"x")
    assetto.instalar(str(ac), src)
    assert assetto.estado(str(ac), src) == "enlazada"
    assetto.renombrar(str(ac), sal, "uno", "dos")
    pistas = ac / "content" / "tracks"
    assert [p.name for p in pistas.iterdir()] == ["dos"]
    assert assetto.estado(str(ac), sal / "assetto_corsa" / "dos") == "enlazada"
    (sal / "assetto_corsa" / "dos").rename(sal / "assetto_corsa" / "tres")      # renombrada a mano: enlace roto
    assert assetto.limpiar_rotos(str(ac)) == ["dos"]
    assert not list(pistas.iterdir())
    (pistas / "ajena").mkdir()                                                 # una pista de verdad no se toca
    assert assetto.limpiar_rotos(str(ac)) == [] and (pistas / "ajena").is_dir()


def test_trafico_opcion_y_modo_de_juego():
    from openlaps.gui import assetto
    op = {"modo": "libre", "centro": [19.0, -99.0], "tam": 500, "nombre": "x", "ac": True}
    assert "--sin-trafico" not in G.argumentos(op, "sal")
    assert "--sin-trafico" in G.argumentos(dict(op, trafico=False), "sal")
    assert "--sin-trafico" not in G.argumentos(dict(op, trafico=False, ac=False), "sal")
    ini = "[RACE]\nTRACK=a\n__CM_CUSTOM_MODE=traffic\nCARS=1\n\n[SESSION_0]\nNAME=Traffic\nTYPE=1\n"
    sin = assetto._modo(ini, False)
    assert "__CM_CUSTOM_MODE" not in sin and "NAME=Practice" in sin and "CARS=1" in sin
    con = assetto._modo(sin, True)
    assert con.count("__CM_CUSTOM_MODE=traffic") == 1 and "NAME=Traffic" in con
    assert assetto._modo(con, True) == con


def test_zonas_en_vivo_en_mis_pistas(tmp_path, monkeypatch):
    """Una zona preparada en vivo desde la app sale en «Mis pistas» y se puede reanudar con sus opciones."""
    from openlaps.gui import ajustes, app, assetto
    monkeypatch.setattr(ajustes, "salida", lambda a=None: tmp_path)
    monkeypatch.setattr(assetto, "detectar", lambda preferida=None: None)
    api = app.Api(servidor.Eventos())
    op = {"modo": "libre", "centro": [40.4, -3.7], "ancho": 2000, "alto": 1500, "nombre": "Madrid centro"}
    monkeypatch.setattr(api._vivo, "iniciar", lambda *a, **k: {"puerto": 1, "nombre": "madrid_centro", "pista_ac": None,
                                                                 "zona": [2000, 1500], "url": "x"})
    assert api.vivo_iniciar(op)["ok"]
    (tmp_path / "vivo" / "madrid_centro" / "p_0_0.glb").write_bytes(b"x")
    z = [p for p in api.pistas() if p.get("vivo")]
    assert len(z) == 1 and z[0]["nombre"] == "madrid_centro" and z[0]["pedazos"] == 1 and z[0]["zona"] == [2000, 1500]
    r = api.vivo_reanudar("madrid_centro")
    assert r["ok"] and r["op"]["centro"] == [40.4, -3.7]
    assert "error" in api.vivo_reanudar("no_existe")


def test_espacio_medir_borrar_y_limpiar(tmp_path, monkeypatch):
    """El espacio que ocupa cada cosa, borrar una pista o una zona en vivo con todo lo suyo, quitar los pedazos de
    otra versión y el tope de la caché (se va primero lo menos usado)."""
    import os
    import time

    from openlaps import vivo
    from openlaps.gui import ajustes, assetto, espacio
    from openlaps.sources import cache
    sal = tmp_path / "sal"
    monkeypatch.setattr(ajustes, "salida", lambda a=None: sal)
    monkeypatch.setattr(ajustes, "DATOS", tmp_path / "datos")
    monkeypatch.setattr(assetto, "detectar", lambda preferida=None: None)
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    def pon(p, n):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * n)
    pon(sal / "uno.glb", 1000)
    pon(sal / "uno.json", 10)
    pon(sal / "assetto_corsa" / "uno" / "uno.kn5", 3000)
    pon(sal / "vivo" / "zona" / "p_0_0.glb", 500)
    pon(sal / "vivo" / "zona" / "p_0_0.olv", 300)
    pon(sal / "assetto_corsa" / "zona_vivo" / "zona_vivo.kn5", 700)
    (sal / "vivo" / "zona" / "vivo.json").write_text('{"version": %d}' % (vivo.VERSION - 1), encoding="utf-8")
    pon(tmp_path / "cache" / "a.tif", 4000)
    pon(tmp_path / "cache" / "b.tif", 4000)
    pon(tmp_path / "cache" / "c.csv.gz.idx.sqlite", 100)          # índice de un formato anterior
    pon(tmp_path / "local" / "OpenLaps" / "runtime" / "cache" / "x.whl", 50)
    pon(tmp_path / "local" / "OpenLaps" / "runtime" / "env" / "python.exe", 5)
    viejo = time.time() - 5 * 86400
    os.utime(tmp_path / "cache" / "a.tif", (viejo, viejo))
    os.utime(tmp_path / "cache" / "b.tif", (viejo + 86400, viejo + 86400))

    m = {p["clave"]: p for p in espacio.medir({})["partes"]}
    assert m["pistas"]["bytes"] == 4010 and m["pistas"]["n"] == 1
    assert m["vivo"]["bytes"] > 800 and m["vivo_ac"]["bytes"] == 700 and m["cache"]["bytes"] == 8100
    assert espacio.medir({})["total"] >= sum(p["bytes"] for p in m.values()) - 1

    libres = espacio.limpiar_obsoletos({})
    assert libres == 500 + 300 + 100 + 50
    (sal / "vivo" / "vacia").mkdir()                              # zona sin nada que conservar: se quita entera
    (sal / "vivo" / "vacia" / "vivo.json").write_text('{"version": 1}', encoding="utf-8")
    assert espacio.limpiar_obsoletos({}) == 14 and not (sal / "vivo" / "vacia").exists()
    assert (sal / "vivo" / "zona").exists()                       # esta conserva su pista de AC
    assert not (sal / "vivo" / "zona" / "p_0_0.glb").exists() and (sal / "vivo" / "zona" / "vivo.json").exists()
    assert (tmp_path / "cache" / "a.tif").exists()

    assert espacio.podar_cache(5000 / 1e9) == 4000                # el tope deja uno: se va el menos usado
    assert not (tmp_path / "cache" / "a.tif").exists() and (tmp_path / "cache" / "b.tif").exists()
    assert espacio.podar_cache(0) == 0

    assert espacio.borrar_vivo(None, sal, "zona") > 700
    assert not (sal / "vivo" / "zona").exists() and not (sal / "assetto_corsa" / "zona_vivo").exists()
    assert espacio.borrar_pista(None, sal, "uno") == 4010
    assert not (sal / "uno.glb").exists() and not (sal / "assetto_corsa" / "uno").exists()
    assert espacio.vaciar_cache() == 4000
