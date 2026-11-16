"""Modo en vivo desde la app: lanza `python -m openlaps.vivo` en un proceso aparte, sigue su salida y lo para.

Cuando la zona pasa de 5 km² la app no genera una pista estática: arranca este servicio con la zona pedida (recortada
a 5 km²) para que se prepare entera en segundo plano, crea la pista semilla de Assetto Corsa y deja lo demás para el
modo en vivo (se genera alrededor del coche mientras se conduce).
"""
import math
import os
import re
import socket
import subprocess
import threading
import time

from .ajustes import RAIZ
from .generador import MAX_KM2, _python, medidas, nombre_valido

_NO_VENTANA = 0x08000000


def _puerto_libre(preferido=8790):
    for p in [preferido] + list(range(8791, 8830)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def zona_recortada(op):
    """(ancho, alto) de lo que se prepara entero: la zona dibujada (la app ya no deja pasar de MAX_KM2; por si
    acaso, se reduce a escala)."""
    ancho, alto = medidas(op)
    km2 = ancho * alto / 1e6
    if km2 <= MAX_KM2:
        return ancho, alto
    f = math.sqrt(MAX_KM2 / km2)
    return ancho * f, alto * f


class EnVivo:
    def __init__(self, al_evento):
        self.al_evento = al_evento
        self.proc = None
        self.puerto = None
        self.nombre = None
        self.lineas = []
        self.semilla = None        # None | "haciendo" | "lista" | "error"
        self.info = {}             # lo que devolvió iniciar() (para que la interfaz lo recupere al recargarse)
        self.listo = False

    @property
    def activo(self):
        return self.proc is not None and self.proc.poll() is None

    def iniciar(self, op, salida, ac=None, semilla=True):
        """Arranca el servicio. `ac`: carpeta de Assetto Corsa donde instalar la pista semilla (o None)."""
        self.detener()
        lat, lon = (float(v) for v in op["centro"])
        ancho, alto = zona_recortada(op)
        self.nombre = nombre_valido(op.get("nombre") or "vivo")
        self.puerto = _puerto_libre()
        carpeta = os.path.join(str(salida), "vivo", self.nombre)
        argv = ["-u", "-m", "openlaps.vivo", "--centro", f"{lat:.6f},{lon:.6f}", "--puerto", str(self.puerto),
                "--zona", f"{ancho:.0f},{alto:.0f}", "--carpeta", carpeta]
        if semilla:
            argv += ["--semilla-ac", "--nombre-ac", f"{self.nombre}_vivo", "--ac-carpeta", ac or ""]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env["PYTHONPATH"] = str(RAIZ) + os.pathsep + env.get("PYTHONPATH", "")
        # cwd: la carpeta de salida (la semilla se escribe en salida/assetto_corsa/<nombre>_vivo)
        os.makedirs(str(salida), exist_ok=True)
        self.lineas, self.semilla, self.listo = [], ("haciendo" if semilla else None), False
        self.proc = subprocess.Popen([_python(), *argv], cwd=str(salida), env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1, creationflags=_NO_VENTANA)
        threading.Thread(target=self._leer, daemon=True).start()
        self.info = {"puerto": self.puerto, "nombre": self.nombre, "pista_ac": f"{self.nombre}_vivo" if semilla else None,
                     "zona": [round(ancho), round(alto)], "url": f"http://127.0.0.1:{self.puerto}/?vivo=1",
                     "centro": [lat, lon]}
        return {"puerto": self.puerto, "nombre": self.nombre, "pista_ac": f"{self.nombre}_vivo" if semilla else None,
                "zona": [round(ancho), round(alto)], "url": f"http://127.0.0.1:{self.puerto}/?vivo=1"}

    def _leer(self):
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for linea in proc.stdout:
            linea = linea.rstrip()
            if not linea:
                continue
            self.lineas = (self.lineas + [linea])[-500:]
            ev = {"tipo": "vivo", "linea": linea}
            if "tramo en vivo:" in linea:
                self.listo = True
                ev["estado"] = "listo"
            elif "pista semilla lista" in linea:
                self.semilla = "lista"
                ev["estado"] = "semilla"
            elif "semilla: ERROR" in linea:
                self.semilla = "error"
                ev["estado"] = "semilla_error"
            self.al_evento(ev)
        code = proc.wait()
        self.al_evento({"tipo": "vivo", "estado": "fin", "codigo": code, "registro": self.lineas[-200:]})

    def estado(self):
        return dict(self.info, activo=self.activo, semilla=self.semilla, listo=self.listo,
                    registro=self.lineas[-200:])

    def detener(self):
        if self.activo:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=_NO_VENTANA)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        return True


def esperar_puerto(puerto, segundos=20):
    t0 = time.time()
    while time.time() - t0 < segundos:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", puerto)) == 0:
                return True
        time.sleep(0.3)
    return False


_ = re  # (re se deja por si se amplía el análisis de la salida)
