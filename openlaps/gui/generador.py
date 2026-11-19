"""Lanza `python -m openlaps …` en un proceso aparte y traduce su salida a eventos de progreso.

En un proceso aparte la ventana no se congela, se puede cancelar matando el árbol de procesos y
un fallo del generador (memoria, red) no tumba la interfaz.
"""
import math
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

from .ajustes import RAIZ
from .textos import t

PASOS = 7
_PASO = re.compile(r"^(\d)/7 (.+)")
_NO_VENTANA = 0x08000000  # CREATE_NO_WINDOW: sin consola negra al lanzar desde pythonw
_PRIORIDAD_BAJA = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS: el borrador automático no frena el PC

# Ajustes por calidad. "borrador" es la vista previa 3D: malla gruesa y sin lo que más tarda.
CALIDADES = {
    "borrador": ["--res", "6", "--mpp", "1.0", "--vegetacion", "0", "--sin-horizonte", "--sin-cc0", "--sin-dosel"],
    "rapida": ["--res", "4", "--vegetacion", "0.5", "--sin-horizonte"],
    "normal": [],
    "detallada": ["--res", "1.5"],
}
OPCIONES_SIN = ["orto", "satelite", "dosel", "horizonte", "microsoft", "catastro", "overture", "cc0"]


def nombre_valido(texto):
    """'Tepoztlán centro' -> 'tepoztlan_centro' (lo que acepta --nombre y una carpeta de AC)."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:40] or "pista"


def _caja_ruta(ruta, margen=200.0):
    lats = [p[0] for p in ruta]
    lons = [p[1] for p in ruta]
    dlat = margen / 111_320.0
    dlon = margen / (111_320.0 * math.cos(math.radians(sum(lats) / len(lats))))
    return min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon


MAX_KM2 = 24.0  # zona máxima (pista normal o zona que se prepara entera en el modo en vivo)


def medidas(op):
    """(ancho, alto) de la zona en metros (las versiones anteriores solo tenían un cuadrado: tam)."""
    tam = float(op.get("tam", 800))
    return float(op.get("ancho") or tam), float(op.get("alto") or tam)


def caja_zona(op):
    from ..geo import BBox
    lat, lon = (float(v) for v in op["centro"])
    ancho, alto = medidas(op)
    return BBox.around(lat, lon, ancho, alto)


def zona_args(op, minimo=True):
    """("bbox", "s,w,n,e", ruta|None) tal como va a la línea de comandos. precarga.caja_de() reconstruye la
    caja desde aquí: así coincide con la del generador. La zona es un rectángulo de ancho × alto."""
    modo = op.get("modo", "libre")
    if modo == "libre":
        b = caja_zona(op)
        return "bbox", f"{b.south:.6f},{b.west:.6f},{b.north:.6f},{b.east:.6f}", None
    ruta = [(float(p[0]), float(p[1])) for p in op.get("ruta") or []]
    if len(ruta) < 2 or (minimo and modo == "circuito" and len(ruta) < 3):
        raise ValueError(t("ruta_2") if len(ruta) < 2 else t("ruta_3"))
    s, w, n, e = _caja_ruta(ruta)
    return "bbox", f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}", ";".join(f"{la:.7f},{lo:.7f}" for la, lo in ruta)


def argumentos(op, salida, nombre=None):
    """Opciones de la interfaz -> argumentos de la línea de comandos de openlaps."""
    modo = op.get("modo", "libre")
    nombre = nombre or nombre_valido(op.get("nombre"))
    a = ["--nombre", nombre, "--salida", str(salida), "--modo", modo]
    tipo, valor, extra = zona_args(op)
    a.append(f"--bbox={valor}")
    if extra:
        a.append(f"--ruta={extra}")
    if modo == "libre":
        ancho, alto = medidas(op)
        if ancho * alto / 1e6 > MAX_KM2 + 1e-6:
            raise ValueError(t("zona_grande", km=MAX_KM2))
    if op.get("vivo") and modo == "libre":
        raise ValueError(t("zona_es_vivo"))
    calidad = op.get("calidad", "normal")
    a += CALIDADES.get(calidad, [])
    if calidad != "borrador":
        if op.get("ac"):
            a.append("--ac")
            if op.get("trafico") is False:
                a.append("--sin-trafico")
        if op.get("titulo"):
            a += ["--titulo", str(op["titulo"])[:60]]
        if op.get("vegetacion") not in (None, "", 1, 1.0):   # va después: en argparse gana el último
            a += ["--vegetacion", f"{float(op['vegetacion']):.2f}"]
        for k in op.get("sin") or []:
            if k in OPCIONES_SIN:
                a.append(f"--sin-{k}")
    if op.get("temporada") in ("seca", "lluvias"):
        a += ["--temporada", op["temporada"]]
    if op.get("region") and op["region"] != "auto":
        a += ["--region", str(op["region"])]
    if op.get("dem"):
        a += ["--dem", str(op["dem"])]
    return a


def _python():
    """python.exe junto al intérprete actual (pythonw.exe no tiene salida estándar)."""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return str(cand if cand.exists() else exe)


class Trabajo:
    """Una generación en curso. `al_evento(dict)` recibe el progreso desde otro hilo."""

    def __init__(self, tipo, argv, al_evento, antes=None, prioridad_baja=False):
        """`antes()`: espera previa (la precarga de OSM de esta zona, si sigue en curso) para no
        lanzar la misma consulta dos veces a Overpass."""
        self.tipo, self.argv, self.al_evento, self.antes = tipo, argv, al_evento, antes
        self.prioridad_baja = prioridad_baja
        self.proc = None
        self.cancelado = False
        self.lineas = []
        self.t0 = 0.0
        self._hilo = None

    def iniciar(self):
        self.t0 = time.time()
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    def cancelar(self):
        """Mata el proceso y espera a que salga (así el siguiente no escribe a la vez el mismo .glb)."""
        self.cancelado = True
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=_NO_VENTANA)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass

    @property
    def activo(self):
        return self._hilo is not None and self._hilo.is_alive()

    def _correr(self):
        self.al_evento({"tipo": self.tipo, "estado": "inicio", "paso": 0, "pasos": PASOS})
        if self.antes:
            self.al_evento({"tipo": self.tipo, "estado": "linea", "paso": 0, "pasos": PASOS, "precarga": True})
            try:
                self.antes()
            except Exception:  # noqa: BLE001 - si la precarga falló, el generador lo reintenta
                pass
        if self.cancelado:
            self.al_evento({"tipo": self.tipo, "estado": "cancelado", "segundos": round(time.time() - self.t0)})
            return
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env["PYTHONPATH"] = str(RAIZ) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            self.proc = subprocess.Popen(
                [_python(), "-u", "-m", "openlaps", *self.argv], cwd=str(RAIZ), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=_NO_VENTANA | (_PRIORIDAD_BAJA if self.prioridad_baja else 0))
        except OSError as e:
            self.al_evento({"tipo": self.tipo, "estado": "error", "error": t("no_inicia", e=e), "registro": []})
            return
        self._leer()

    def _leer(self):
        paso, texto = 0, ""
        for linea in self.proc.stdout:
            linea = linea.rstrip()
            if not linea:
                continue
            self.lineas.append(linea)
            m = _PASO.match(linea)
            if m:
                paso, texto = int(m.group(1)), m.group(2)
            self.al_evento({"tipo": self.tipo, "estado": "linea", "paso": paso, "pasos": PASOS,
                            "texto": texto, "linea": linea, "segundos": round(time.time() - self.t0)})
        code = self.proc.wait()
        fin = {"tipo": self.tipo, "segundos": round(time.time() - self.t0), "registro": self.lineas[-400:]}
        if self.cancelado:
            fin["estado"] = "cancelado"
        elif code == 0:
            fin["estado"] = "listo"
        else:
            fin["estado"] = "error"
            fin["error"] = _resumen_error(self.lineas)
        self.al_evento(fin)


def _resumen_error(lineas):
    """La última línea útil de un traceback, en vez de volcarlo entero en la ventana."""
    for l in reversed(lineas):
        l = l.strip()
        if l and not l.startswith(("File ", "^", "~")) and l != "Traceback (most recent call last):":
            if "Overpass no respondió" in l or "URLError" in l or "getaddrinfo" in l:
                return t("sin_red")
            if "MemoryError" in l or "Unable to allocate" in l:
                return t("sin_memoria")
            return l[-300:]
    return t("sin_detalles")
