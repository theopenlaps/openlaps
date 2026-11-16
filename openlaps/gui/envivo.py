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

from .ajustes import RAIZ, vivo_params
from .generador import CALIDADES, MAX_KM2, OPCIONES_SIN, _python, medidas, nombre_valido

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


def esperar_puerto(puerto, timeout=15.0, proc=None):
    """Espera a que `127.0.0.1:puerto` acepte conexiones (o aborta si el proceso cae)."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(puerto)), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


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

    def iniciar(self, op, salida, ac=None, semilla=True, excluir=None, params=None):
        """Arranca el servicio. `ac`: carpeta de Assetto Corsa donde instalar la pista semilla (o None)."""
        self.detener()
        vp = vivo_params(params)
        lat, lon = (float(v) for v in op["centro"])
        self.nombre = nombre_valido(op.get("nombre") or "vivo")
        self.puerto = _puerto_libre()
        carpeta = os.path.join(str(salida), "vivo", self.nombre)
        argv = ["-u", "-m", "openlaps.vivo", f"--centro={lat:.6f},{lon:.6f}", "--puerto", str(self.puerto),
                "--carpeta", carpeta, "--pedazo-m", str(vp["lado"]), "--margen", str(vp["margen"]),
                "--pad-osm", str(vp["pad_osm"]), "--tesela-osm", str(vp["tesela_osm"]),
                "--intervalo", f"{vp['intervalo']:.2f}", "--radio", str(vp["radio"]),
                "--radio-fondo", str(vp["radio_fondo"]), "--r-carga", str(vp["r_carga"]),
                "--r-soltar", str(vp["r_soltar"]), "--t-foco", f"{vp['t_foco']:.2f}"]
        if vp["trabajadores"] > 0:
            argv += ["--trabajadores", str(vp["trabajadores"])]
        if excluir is not None:
            ex0, ey0, ex1, ey1 = (float(v) for v in excluir)
            argv.append(f"--excluir={ex0:.2f},{ey0:.2f},{ex1:.2f},{ey1:.2f}")
            ancho, alto = max(1.0, ex1 - ex0), max(1.0, ey1 - ey0)
        else:
            ancho, alto = zona_recortada(op)
            argv += ["--zona", f"{ancho:.0f},{alto:.0f}"]
        calidad = op.get("calidad", "normal")
        argv += CALIDADES.get(calidad, [])
        if calidad != "borrador":
            if op.get("res") is not None:
                argv += ["--res", str(op["res"])]
            if op.get("mpp") is not None:
                argv += ["--mpp", str(op["mpp"])]
            if op.get("vegetacion") not in (None, "", 1, 1.0):
                argv += ["--vegetacion", f"{float(op['vegetacion']):.2f}"]
            for k in op.get("sin") or []:
                if k in OPCIONES_SIN:
                    argv.append(f"--sin-{k}")
        if op.get("temporada") in ("seca", "lluvias"):
            argv += ["--temporada", op["temporada"]]
        if op.get("region") and op["region"] != "auto":
            argv += ["--region", str(op["region"])]
        if op.get("dem"):
            argv += ["--dem", str(op["dem"])]
        if op.get("tuneles") in ("principales", "todos", "ninguno"):
            argv += ["--tuneles", op["tuneles"]]
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
                     "centro": [lat, lon], "excluir": [float(v) for v in excluir] if excluir is not None else None,
                     "params": vp}
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


_ = re  # (re se deja por si se amplía el análisis de la salida)
