"""Tráfico del modo en vivo: la ventana alrededor del coche y la copia de la simulación de CSP en la pista."""
import argparse
import os

from openlaps import trafico_vivo as TV


def test_ventana_segun_el_coche():
    assert TV.centro_ventana(0, 0) == (0, 0)
    assert TV.centro_ventana(190, -190) == (0, 0)
    assert TV.centro_ventana(210, 0) == (1, 0)
    # en marcha se centra por delante, pero nunca a más de ADELANTO_MAX
    assert TV.centro_ventana(0, 0, 60, 0) == (1, 0)
    assert TV.centro_ventana(0, 0, 500, 0) == (1, 0)
    assert TV.centro_ventana(0, 0, 0, -60) == (0, -1)
    assert TV.rect_ventana((1, -1)) == (-400.0, -1200.0, 1200.0, 400.0)
    x0, y0, x1, y1 = TV.rect_ventana((0, 0), 200)
    assert (x0, y1) == (-1000.0, 1000.0)
    # la ventana siempre deja al coche a más distancia del borde que la de quitar coches (520 m)
    assert TV.MITAD - TV.PASO / 2 - TV.ADELANTO_MAX <= TV.MITAD and TV.MITAD - TV.PASO / 2 > 520


class _OSM:
    def __init__(self, listas):
        self._listas, self.pedidas = listas, []

    def listas(self, rect):
        return self._listas

    def claves(self, rect):
        return [(0, 0), (1, 0)]

    def pedir(self, k, urgencia=0.0):
        self.pedidas.append(k)

    def elementos(self, rect):
        return []


class _Vivo:
    def __init__(self, sin_trafico=False, listas=False):
        self.args = argparse.Namespace(sin_trafico=sin_trafico)
        self.osm = _OSM(listas)
        self.mensajes = []

    def log(self, m):
        self.mensajes.append(m)


def test_pedir_ventana_sin_calcular():
    assert TV.Ventanas(_Vivo(sin_trafico=True)).pedir(0, 0) == {"apagado": True}
    v = _Vivo()
    w = TV.Ventanas(v)
    assert w.pedir(10, -10, actual="0_0") == {"igual": True}
    # AC da z hacia el sur: z = -450 es 450 m al norte, la celda (0, 1)
    assert w.pedir(0, -450, actual="0_1") == {"igual": True}
    # sin OSM de la zona todavía: se pide y se espera, sin lanzar ningún cálculo
    assert w.pedir(10, -10, actual="") == {"espera": True}
    assert v.osm.pedidas == [(0, 0), (1, 0)] and w.pool is None and not w.en_curso
    # una ventana ya hecha se devuelve tal cual
    w.hechas["0_0"] = b"{}"
    assert w.pedir(10, -10) == b"{}"


def _instalacion_csp(raiz, donde="extension/lua/tools/csp-traffic-tool", con_coches=True):
    herr = raiz.joinpath(*donde.split("/"))
    (herr / "src" / "simulation").mkdir(parents=True)
    (herr / "lib" / "luagraphs" / "data").mkdir(parents=True)
    (herr / "data").mkdir()
    (herr / "src" / "simulation" / "TrafficSimulation.lua").write_text("return {}\n", encoding="utf-8")
    (herr / "src" / "simulation" / "CarsList.lua").write_text(
        "local c = loadColorsList('extension/config/data_oem_colors_modern.txt')\n"
        "local dataDir = 'extension/lua/tools/csp-traffic-tool/data'\n", encoding="utf-8")
    (herr / "src" / "simulation" / "LicensePlateGenerator.lua").write_text(
        "ui.pushDWriteFont('License Plate:extension/lua/tools/csp-traffic-tool/data;Weight=Black')\n", encoding="utf-8")
    (herr / "lib" / "luagraphs" / "data" / "graph.lua").write_text("return {}\n", encoding="utf-8")
    (herr / "src" / "simulation" / "TrafficGraph.lua").write_text(
        "    if ok then\n      print(i, edge.toID, edge.fromID, edge.lane.name, self._gVertices)\n      x = 1\n",
        encoding="utf-8")
    if con_coches:
        (herr / "data" / "coche.json").write_text("{}", encoding="utf-8")
        (herr / "data" / "coche.kn5").write_bytes(b"x" * 1000)
    (raiz / "extension" / "config").mkdir(parents=True, exist_ok=True)
    for f in TV.COLORES:
        (raiz / "extension" / "config" / f).write_text("1,1,1,1\n", encoding="utf-8")
    return herr


def test_instalar_simulacion(tmp_path):
    ac, pista = tmp_path / "ac", tmp_path / "pista"
    pista.mkdir()
    assert not TV.instalar_simulacion(str(pista), str(ac))            # sin CSP: sin tráfico, y sin tocar la pista
    assert not (pista / "extension").exists()
    _instalacion_csp(ac, TV.HERRAMIENTA[1])                            # también vale si CSP la tiene desactivada
    assert TV.instalar_simulacion(str(pista), str(ac))
    dst = pista / "extension" / "trafico"
    assert (dst / "lib" / "luagraphs" / "data" / "graph.lua").exists() and (dst / TV.MARCA).exists()
    assert (dst / "data" / "coche.kn5").stat().st_size == 1000 and (dst / "data" / TV.COLORES[0]).exists()
    coches = (dst / "src" / "simulation" / "CarsList.lua").read_text(encoding="utf-8")
    assert "csp-traffic-tool" not in coches and "extension/config" not in coches
    assert "local dataDir = OPENLAPS_TRAFICO_DATOS\n" in coches
    assert "OPENLAPS_TRAFICO_DATOS .. '/data_oem_colors_modern.txt'" in coches
    assert "csp-traffic-tool" not in (dst / "src" / "simulation" / "LicensePlateGenerator.lua").read_text(encoding="utf-8")
    assert (dst / "src" / "simulation" / "TrafficGraph.lua").read_text(encoding="utf-8") == "    if ok then\n      x = 1\n"
    # ya instalada: no se vuelve a copiar (un cambio a mano en la copia se queda)
    (dst / "src" / "simulation" / "CarsList.lua").write_text("mio", encoding="utf-8")
    assert TV.instalar_simulacion(str(pista), str(ac))
    assert (dst / "src" / "simulation" / "CarsList.lua").read_text(encoding="utf-8") == "mio"
    # una instalación de CSP sin los modelos de coche no sirve para una pista nueva
    ac2, pista2 = tmp_path / "ac2", tmp_path / "pista2"
    pista2.mkdir()
    _instalacion_csp(ac2, con_coches=False)
    assert not TV.instalar_simulacion(str(pista2), str(ac2))
    assert not os.path.exists(pista2 / "extension" / "trafico" / TV.MARCA)


def test_refrescar_lua_conserva_lo_de_la_pista(tmp_path):
    """El script de una pista ya generada se pone al día (tráfico propio) sin perder servidor, origen ni caja."""
    from openlaps import vivo
    vivo.escribir_lua(str(tmp_path), "http://127.0.0.1:8790", "['0_0'] = true, ['1_0'] = true", 19.4195, -99.162,
                      modo_espera=True, rect_horneado=(-250, -250, 250, 250), pista="mi_pista", lado=256,
                      r_carga=400, r_soltar=700)
    ruta = tmp_path / "extension" / "vivo.lua"
    nuevo = ruta.read_text(encoding="utf-8")
    assert "__" not in "".join(ln for ln in nuevo.splitlines() if ln.startswith("local ") and "= " in ln)
    # una versión vieja del script: sin el bloque del tráfico ni su interruptor
    i, j = nuevo.index("-- ---------------------------------------------------------------- tráfico"), nuevo.index(
        "-- ---------------------------------------------------------------- ciclo")
    viejo = (nuevo[:i] + nuevo[j:]).replace("local TRAFICO_PISTA = true", "-- (sin tráfico)")
    ruta.write_text(viejo, encoding="utf-8")
    assert vivo.refrescar_lua(str(tmp_path))
    assert ruta.read_text(encoding="utf-8") == nuevo
    # y el interruptor de la app se conserva al refrescar
    assert vivo.actualizar_control_lua(str(tmp_path), trafico=False)
    assert vivo.refrescar_lua(str(tmp_path))
    txt = ruta.read_text(encoding="utf-8")
    assert "local TRAFICO_PISTA = false" in txt and "local PISTA_ID = 'mi_pista'" in txt
    assert "local RECT_HORNEADO = { -250.0, -250.0, 250.0, 250.0 }" in txt and "local vivoActivo = not true" in txt
    assert not vivo.refrescar_lua(str(tmp_path / "no_hay"))


def test_proceso_de_trabajo_sale_al_morir_su_padre(tmp_path):
    """Un proceso con `morir_con_el_padre` no se queda huérfano (en Windows se quedaban reservando 1 GB cada uno)."""
    import subprocess
    import sys
    import time
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(TV.__file__)))
    marca = tmp_path / "hijo.pid"
    hijo = ("import os, sys, time; sys.path.insert(0, sys.argv[1]); from openlaps import procesos; "
            "procesos.morir_con_el_padre(); open(sys.argv[2], 'w').write(str(os.getpid())); time.sleep(60)")
    padre = ("import subprocess, sys, time; "
             "subprocess.Popen([sys.executable, '-c', sys.argv[3], sys.argv[1], sys.argv[2]]); time.sleep(60)")
    # el intérprete de verdad, no el lanzador del entorno virtual (en Windows es otro proceso intermedio)
    exe = getattr(sys, "_base_executable", None) or sys.executable
    p = subprocess.Popen([exe, "-c", padre, raiz, str(marca), hijo])
    t0 = time.time()
    while not marca.exists() or not marca.read_text():
        assert time.time() - t0 < 20
        time.sleep(0.1)
    pid = int(marca.read_text())
    time.sleep(0.5)
    p.kill()
    p.wait()

    def vivo(pid):
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
            return str(pid) in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    t0 = time.time()
    while vivo(pid) and time.time() - t0 < 10:
        time.sleep(0.2)
    assert not vivo(pid)
