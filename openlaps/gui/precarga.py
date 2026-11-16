"""Descarga por adelantado lo que más tarda la primera vez en una zona.

Medido con la caché vacía (800 m): Overpass ~100 s, Catastro ~60 s, Microsoft 10-30 s, ortofoto
~16 s y el resto (relieve, WorldCover, Sentinel-2) < 10 s. En cuanto el usuario deja quieta la
zona en el mapa se piden esas descargas con exactamente la misma consulta que hará el generador, así
que cuando pulsa «Borrador 3D» o «Generar» ya están en la caché de disco. La misma respuesta de
Overpass sirve para dibujar las calles en el mapa.

Solo hay una consulta a Overpass en curso a la vez (el servidor limita las conexiones por IP): si
llega otra zona mientras tanto, se atiende la última y las intermedias se descartan.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import regions
from ..geo import BBox
from ..sources import osm as OSM, catastro, msbuildings, overture, rasters

_nada = lambda *a, **k: None  # noqa: E731


def _clave(bbox):
    return f"{bbox.south:.7f},{bbox.west:.7f},{bbox.north:.7f},{bbox.east:.7f}"


class Precarga:
    def __init__(self):
        self._cv = threading.Condition()
        self._pendiente = None        # (clave, bbox) que espera turno
        self._en_curso = None         # clave que se está descargando
        self._hechas = {}             # clave -> datos de Overpass | Exception (las últimas)
        self._extras = ThreadPoolExecutor(max_workers=3, thread_name_prefix="precarga")
        self._extras_pedidos = set()
        threading.Thread(target=self._trabajar, daemon=True, name="precarga-osm").start()

    def pedir(self, bbox):
        """Encola la zona (sustituye a la que esperaba turno) y devuelve su clave."""
        k = _clave(bbox)
        with self._cv:
            if isinstance(self._hechas.get(k), Exception):
                del self._hechas[k]          # falló antes: se reintenta
            if k not in self._hechas and k != self._en_curso:
                self._pendiente = (k, bbox)
                self._cv.notify_all()
        self._pedir_extras(k, bbox)
        return k

    def esperar(self, k, timeout=180, cancelar=None):
        """Datos de Overpass de esa zona; None si otra zona la sustituyó, expira o se cancela."""
        fin = time.monotonic() + timeout
        with self._cv:
            while True:
                if k in self._hechas:
                    break
                if k != self._en_curso and (self._pendiente or ("",))[0] != k:
                    return None
                if (cancelar and cancelar()) or time.monotonic() >= fin:
                    return None
                self._cv.wait(timeout=0.5)
            r = self._hechas[k]
        if isinstance(r, Exception):
            raise r
        return r

    def descargando(self, k):
        with self._cv:
            return k == self._en_curso or (self._pendiente or ("",))[0] == k

    def _trabajar(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pendiente is not None)
                k, bbox = self._pendiente
                self._pendiente, self._en_curso = None, k
            try:
                r = OSM.download(bbox.padded(200), log=_nada)   # igual que pipeline.run
            except Exception as e:  # noqa: BLE001
                r = e
            with self._cv:
                self._hechas[k] = r
                while len(self._hechas) > 6:
                    self._hechas.pop(next(iter(self._hechas)))
                self._en_curso = None
                self._cv.notify_all()

    def _pedir_extras(self, k, bbox):
        """Catastro, Microsoft y Overture, en segundo plano (no bloquean la vista previa)."""
        if k in self._extras_pedidos:
            return
        self._extras_pedidos.add(k)
        R = regions.detect((bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2)
        # mismas cajas y argumentos que pipeline.run: la caché de rásters usa ambos como clave
        big, cerca = bbox.padded(450), bbox.padded(60)
        dem = {"mdt05": rasters.mdt05, "3dep": rasters.dem_3dep}.get(R.dem, rasters.copernicus_dem)
        tareas = [(dem, big), (rasters.worldcover, big), (rasters.sentinel2_ndwi, cerca, "seca")]
        if R.ortho:
            tareas.append((lambda b, log: rasters.ortho(b, R.ortho, mpp=R.ortho_mpp, log=log), cerca))
        else:
            tareas.append((rasters.sentinel2, cerca, "seca"))
        tareas.append((msbuildings.download, bbox))
        if R.cadastre == "catastro":
            tareas.append((catastro.download, bbox))
        if R.heights == "overture":
            tareas.append((overture.download, bbox))
        tareas.append((rasters.canopy_height, bbox))    # solo la usa la pista completa: al final
        for f, *args in tareas:
            self._extras.submit(_silencio, f, *args)


def _silencio(f, bbox, *args):
    try:
        f(bbox, *args, log=_nada)
    except Exception:  # noqa: BLE001 - es solo adelantar trabajo; el generador lo reintenta
        pass


def caja_de(op):
    """La BBox exacta que usará el generador para estas opciones (mismo redondeo que la línea de
    comandos), para que las claves de la caché coincidan."""
    from .generador import zona_args
    tipo, valor, _ = zona_args(op, minimo=False)
    return BBox.parse(valor)
