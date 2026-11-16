"""Edificios de Google 3D en AC desde la app (opcional, apagado por defecto).

Al jugar una pista con la opción encendida y un token de Cesium ion, se pone el script en la pista y se lanza
`python -m openlaps.google3d` en un proceso aparte (sirve la malla de Google en memoria mientras dura la partida). Con
la opción apagada se quita el script y la pista queda como la generó OpenLaps.
"""
import os
import subprocess
import threading

from .. import google3d as G
from .ajustes import RAIZ
from .envivo import _NO_VENTANA, _puerto_libre
from .generador import _python


class Google3D:
    def __init__(self):
        self.proc = None
        self.lineas = []
        self.pista = None

    @property
    def activo(self):
        return self.proc is not None and self.proc.poll() is None

    def preparar(self, carpeta_pista, glb, activar, token, vivo=None):
        """Antes de abrir AC: con la opción encendida instala el script y arranca el proceso; si no, lo quita.
        `vivo`: carpeta de los pedazos del modo en vivo (en lugar del .glb). Devuelve un aviso para la interfaz, o
        None."""
        self.detener()
        if not activar:
            G.quitar(str(carpeta_pista))
            return None
        if not token:
            G.quitar(str(carpeta_pista))
            return "google3d_sin_token"
        hay_calles = os.path.isdir(str(vivo)) if vivo else os.path.isfile(str(glb))
        if not os.path.isfile(os.path.join(str(carpeta_pista), "openlaps_geo.json")) or not hay_calles:
            G.quitar(str(carpeta_pista))
            return "google3d_sin_datos"
        puerto = _puerto_libre(G.PUERTO)
        G.instalar(str(carpeta_pista), puerto)
        # el token va por el entorno, no en la línea de comandos (no se ve en la lista de procesos)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", CESIUM_ION_TOKEN=token)
        env["PYTHONPATH"] = str(RAIZ) + os.pathsep + env.get("PYTHONPATH", "")
        self.lineas, self.pista = [], str(carpeta_pista)
        fuente = ["--vivo", str(vivo)] if vivo else ["--glb", str(glb)]
        self.proc = subprocess.Popen([_python(), "-u", "-m", "openlaps.google3d", str(carpeta_pista), *fuente,
                                      "--puerto", str(puerto)], env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1, creationflags=_NO_VENTANA)
        threading.Thread(target=self._leer, daemon=True).start()
        return None

    def _leer(self):
        for linea in self.proc.stdout:
            if linea.strip():
                self.lineas = (self.lineas + [linea.rstrip()])[-200:]

    def estado(self):
        return {"activo": self.activo, "pista": self.pista, "registro": self.lineas[-50:]}

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
