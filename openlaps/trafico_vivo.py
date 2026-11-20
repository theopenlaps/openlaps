"""Tráfico en el modo en vivo: una ventana de carriles alrededor del coche.

El modo Traffic de CSP lee `traffic.json` una vez al arrancar, y en el modo en vivo el mapa no tiene fin. Aquí el
servidor calcula solo la red de calles (sin mallas) de una ventana de 1,6 km alrededor del coche, saca de ella los
carriles y los cruces con `trafico.construir` y se los da al script de la pista, que corre la misma simulación de CSP
(una copia dentro de la pista: ver `instalar_simulacion`). Cuando el coche se aleja del centro, el script pide la
ventana siguiente y pasa a ella los coches que ya tenía cerca.

Las alturas de aquí salen del relieve: el script las pega a la calzada de verdad con rayos contra la física de los
pedazos ya cargados (lo hace la propia simulación al crear cada carril).
"""
import argparse
import json
import os
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor

PASO = 400.0      # los centros de ventana caen en una rejilla de este paso (m)
MITAD = 800.0     # media ventana (m): la simulación quita los coches a 520 m, así que el borde nunca se ve
PAD_OSM = 200.0   # OSM alrededor de la ventana (calles que entran)
ADELANTO = 4.0    # la ventana se centra donde estará el coche en estos segundos…
ADELANTO_MAX = 300.0   # …sin pasarse de esta distancia (m)
GUARDADAS = 6     # ventanas ya calculadas que se conservan en memoria

HERRAMIENTA = ("extension/lua/tools/csp-traffic-tool", "extension/lua/tools/disabled/csp-traffic-tool")
COLORES = ("data_oem_colors_modern.txt", "data_oem_colors_vintage.txt")
MARCA = "openlaps.txt"
VERSION_SIM = "4"   # súbela si cambia lo que `instalar_simulacion` deja en la pista


def centro_ventana(x, y, vx=0.0, vy=0.0):
    """Celda (a, b) de la rejilla donde centrar la ventana de un coche en (x, y) locales que va a (vx, vy) m/s."""
    dx, dy = vx * ADELANTO, vy * ADELANTO
    d = (dx * dx + dy * dy) ** 0.5
    if d > ADELANTO_MAX:
        dx, dy = dx * ADELANTO_MAX / d, dy * ADELANTO_MAX / d
    return int(round((x + dx) / PASO)), int(round((y + dy) / PASO))


def rect_ventana(c, pad=0.0):
    cx, cy = c[0] * PASO, c[1] * PASO
    return cx - MITAD - pad, cy - MITAD - pad, cx + MITAD + pad, cy + MITAD + pad


def calcular(args_dict, lat0, lon0, c, elementos):
    """La ventana `c` lista para el script (JSON en bytes): los datos del editor de la herramienta de CSP en
    coordenadas de AC, sin semáforos (en el modo en vivo aún no hay postes que los enseñen), más su centro."""
    from . import pipeline, trafico
    from .geo import LocalProjection
    from .sources import osm as OSM
    t0 = time.time()
    args = argparse.Namespace(**args_dict)
    proj = LocalProjection.marco(lat0, lon0, rect_ventana(c))
    net = pipeline.red_de_calles(args, proj, OSM.parse({"elements": elementos}))
    textos = []
    datos = trafico.construir(net, None, log=textos.append)
    ac = trafico.a_ac(datos) if datos else {"lanes": [], "intersections": [], "areas": [], "rules": []}
    for k in ac["intersections"]:
        k.pop("trafficLight", None)
    ac.update(clave=f"{c[0]}_{c[1]}", centro=[c[0] * PASO, -c[1] * PASO], mitad=MITAD,
              resumen=(textos[-1].strip() if textos else "sin calles con tráfico") + f" ({time.time() - t0:.1f} s)")
    return json.dumps(ac, separators=(",", ":")).encode()


class Ventanas:
    """Lo que el servidor del modo en vivo guarda del tráfico: las ventanas hechas y la que se está calculando (en
    un proceso aparte, para no quitarle tiempo ni a los pedazos ni al servidor)."""

    def __init__(self, vivo):
        self.vivo = vivo
        self.hechas = {}       # clave -> bytes (las últimas GUARDADAS)
        self.en_curso = {}     # clave -> future
        self.fallos = {}       # clave -> instante del último fallo
        self.pool = None
        self.lock = threading.Lock()

    def pedir(self, x, z, vx=0.0, vz=0.0, actual=""):
        """Respuesta a /vivo/trafico para un coche en (x, z) de AC: los bytes de su ventana, o un dict con
        `apagado` (sin tráfico), `igual` (ya tiene la que le toca) o `espera` (aún no está)."""
        v = self.vivo
        if getattr(v.args, "sin_trafico", False):
            return {"apagado": True}
        c = centro_ventana(x, -z, vx, -vz)
        clave = f"{c[0]}_{c[1]}"
        if clave == actual:
            return {"igual": True}
        with self.lock:
            if clave in self.hechas:
                return self.hechas[clave]
            fut = self.en_curso.get(clave)
            if fut is not None:
                if not fut.done():
                    return {"espera": True}
                del self.en_curso[clave]
                try:
                    cuerpo = fut.result()
                except Exception as e:  # noqa: BLE001
                    self.fallos[clave] = time.time()
                    v.log(f"tráfico: la ventana {clave} falló: {e!r}")
                    return {"espera": True}
                self.hechas[clave] = cuerpo
                while len(self.hechas) > GUARDADAS:
                    del self.hechas[next(iter(self.hechas))]
                v.log(f"tráfico: ventana {clave}, {json.loads(cuerpo)['resumen']}")
                return cuerpo
            if self.en_curso or time.time() - self.fallos.get(clave, 0) < 15:
                return {"espera": True}
            rect = rect_ventana(c, PAD_OSM)
            if not v.osm.listas(rect):
                for k in v.osm.claves(rect):
                    v.osm.pedir(k, urgencia=400.0)
                return {"espera": True}
            if self.pool is None:
                from . import procesos
                self.pool = ProcessPoolExecutor(max_workers=1, initializer=procesos.morir_con_el_padre)
            self.en_curso[clave] = self.pool.submit(calcular, vars(v.args), v.proj.lat0, v.proj.lon0, c,
                                                    v.osm.elementos(rect))
            return {"espera": True}

    def cerrar(self):
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)


# =========================================================================== la simulación, dentro de la pista
def _enlazar(origen, destino):
    """Enlace duro (los modelos de los coches pesan 48 MB y están en el mismo disco que la pista); si no se
    puede, copia."""
    if os.path.exists(destino):
        if os.path.getsize(destino) == os.path.getsize(origen):
            return
        os.remove(destino)
    try:
        os.link(origen, destino)
    except OSError:
        shutil.copy2(origen, destino)


def instalar_simulacion(pista, ac, log=lambda m: None):
    """Deja en `<pista>/extension/trafico` la simulación de tráfico de CSP que haya en esa instalación de Assetto
    Corsa (código, modelos de coche y colores): un script de pista no puede leer la carpeta de la herramienta.
    Devuelve False si esa instalación no la trae (entonces el modo en vivo va sin tráfico)."""
    if not ac or not pista:
        return False
    herr = next((os.path.join(ac, *h.split("/")) for h in HERRAMIENTA
                 if os.path.isfile(os.path.join(ac, *h.split("/"), "src", "simulation", "TrafficSimulation.lua"))),
                None)
    dst = os.path.join(pista, "extension", "trafico")
    marca = os.path.join(dst, MARCA)
    datos = os.path.join(herr, "data") if herr else ""
    # sin la herramienta, o sin sus modelos de coche (CSP los baja la primera vez que se usa el modo Traffic)
    if not os.path.isdir(datos) or not any(f.endswith(".json") for f in os.listdir(datos)):
        return os.path.isfile(marca)
    sello = f"{VERSION_SIM} {os.path.getmtime(os.path.join(herr, 'src', 'simulation', 'TrafficSimulation.lua')):.0f}"
    if os.path.isfile(marca) and open(marca, encoding="utf-8").read().strip() == sello:
        return True
    try:
        for sub in ("lib", "src"):
            shutil.copytree(os.path.join(herr, sub), os.path.join(dst, sub), dirs_exist_ok=True)
        os.makedirs(os.path.join(dst, "data"), exist_ok=True)
        for f in os.listdir(os.path.join(herr, "data")):
            o = os.path.join(herr, "data", f)
            if os.path.isfile(o):
                _enlazar(o, os.path.join(dst, "data", f))
        for f in COLORES:
            o = os.path.join(ac, "extension", "config", f)
            if os.path.isfile(o):
                shutil.copy2(o, os.path.join(dst, "data", f))
        # las rutas fijas a la carpeta de la herramienta pasan a ser la de la copia (la da vivo.lua)
        for rel, cambios in (
                ("CarsList.lua", (("'extension/lua/tools/csp-traffic-tool/data'", "OPENLAPS_TRAFICO_DATOS"),
                                  ("'extension/config/data_oem_colors_modern.txt'",
                                   "OPENLAPS_TRAFICO_DATOS .. '/data_oem_colors_modern.txt'"),
                                  ("'extension/config/data_oem_colors_vintage.txt'",
                                   "OPENLAPS_TRAFICO_DATOS .. '/data_oem_colors_vintage.txt'"))),
                ("LicensePlateGenerator.lua", (("'License Plate:extension/lua/tools/csp-traffic-tool/data;Weight=Black'",
                                                "'License Plate:' .. OPENLAPS_TRAFICO_DATOS .. ';Weight=Black'"),)),
                # una línea de depuración que escribe la lista entera de vértices por cada carril: con los 1700
                # carriles de una ventana, el juego se quedaba 9 s parado al crearla
                # los coches cercanos, pegados al suelo de verdad: en un giro CSP traza una curva de Bézier en 3D
                # entre el carril de entrada y el de salida, y la pendiente de los extremos la saca del asfalto
                ("TrafficDriver.lua", (("    self.guide:calculateCurrentPosInto(self.pos, false)\n  end\n  if self.car ~= nil then\n",
                                        "    self.guide:calculateCurrentPosInto(self.pos, false)\n"
                                        "    if OPENLAPS_SUELO and distanceSquared < 25600 then OPENLAPS_SUELO(self.pos) end\n"
                                        "  end\n  if self.car ~= nil then\n"),)),
                # velocidad del tráfico ajustable dentro del juego (la de serie solo topaba la de cada modelo)
                ("TrafficDriver.lua", (("_mmin(self.maxSpeed, meta.speedLimit * (0.6 + 0.4 * self.speedy))",
                                        "_mmin(self.maxSpeed, meta.speedLimit * (0.6 + 0.4 * self.speedy))"
                                        " * (OPENLAPS_TRAFICO_VEL or 1)"),)),
                ("TrafficGraph.lua", (("      print(i, edge.toID, edge.fromID, edge.lane.name, self._gVertices)\n", ""),))):
            ruta = os.path.join(dst, "src", "simulation", rel)
            if not os.path.isfile(ruta):
                continue
            txt = open(ruta, encoding="utf-8").read()
            for a, b in cambios:
                txt = txt.replace(a, b)
            with open(ruta, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
        with open(marca, "w", encoding="utf-8") as f:
            f.write(sello)
    except OSError as e:
        log(f"tráfico: no se pudo copiar la simulación de CSP a la pista ({e})")
        return False
    log("tráfico: simulación de CSP copiada a la pista")
    return True
