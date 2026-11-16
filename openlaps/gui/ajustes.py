"""Rutas y ajustes persistentes de la interfaz (en %APPDATA%\\OpenLaps\\ajustes.json)."""
import ctypes
import json
import os
from pathlib import Path

# carpeta que contiene el paquete openlaps/ y el visor viewer/ (el repositorio o la carpeta instalada)
RAIZ = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
VISOR = RAIZ / "viewer"


def _documentos():
    """Carpeta Documentos real (también si OneDrive la redirige)."""
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


DATOS = Path(os.environ.get("APPDATA") or Path.home()) / "OpenLaps"
BORRADOR = DATOS / "borrador"
_ARCHIVO = DATOS / "ajustes.json"


def salida_por_defecto():
    # en una copia del repositorio se sigue usando salida/ como la línea de comandos
    if (RAIZ / ".git").exists():
        return str(RAIZ / "salida")
    return str(_documentos() / "OpenLaps")


DEFECTO = {
    "asistente_visto": False,
    "salida": None,       # None = salida_por_defecto()
    "ac": None,           # carpeta de assettocorsa elegida a mano (si no, se detecta)
    "calidad": "normal",
    "exportar_ac": True,
    "jugar_al_terminar": False,  # abrir Assetto Corsa en la pista al terminar de generarla
    "capa": "mapa",
    "vista": None,        # mapa y zona de la última sesión
    "idioma": None,       # "es" | "en"; None = el de Windows
    "borrador_auto": True,  # rehacer el borrador 3D al dejar quieta la zona
    "ver_osm": True,      # resaltar en el mapa lo que se va a construir
    "borrador_zona": None,  # zona del último borrador 3D (para no rehacerlo al abrir)
    "google3d": False,    # edificios de Google 3D en AC (vía Cesium ion); apagado: los de OpenLaps
    "token_cesium": "",   # token de Cesium ion para lo anterior (cuenta gratis)
}


def cargar():
    a = dict(DEFECTO)
    try:
        a.update(json.loads(_ARCHIVO.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return a


def guardar(cambios):
    a = cargar()
    a.update({k: v for k, v in cambios.items() if k in DEFECTO})
    DATOS.mkdir(parents=True, exist_ok=True)
    _ARCHIVO.write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")
    return a


def salida(a=None):
    return Path((a or cargar()).get("salida") or salida_por_defecto())
