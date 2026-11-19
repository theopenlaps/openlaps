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
    "trafico": True,      # AC: generar los carriles de tráfico y abrir la pista en el modo Traffic de CSP
    "capa": "mapa",
    "vista": None,        # mapa y zona de la última sesión
    "idioma": None,       # "es" | "en"; None = el de Windows
    "borrador_auto": True,  # rehacer el borrador 3D al dejar quieta la zona
    "ver_osm": True,      # resaltar en el mapa lo que se va a construir
    "borrador_zona": None,  # zona del último borrador 3D (para no rehacerlo al abrir)
    "google3d": False,    # edificios de Google 3D en AC (vía Cesium ion); apagado: los de OpenLaps
    "token_cesium": "",   # token de Cesium ion para lo anterior (cuenta gratis)
    "vivo_lado": 256,         # tamaño de cada pedazo o tesela en modo en vivo (m)
    "vivo_margen": 64,        # margen alrededor de cada pedazo antes de recortar (m)
    "vivo_intervalo": 0.25,   # cada cuánto revisa y lanza teselas nuevas (s)
    "vivo_trabajadores": 0,   # procesos en paralelo (0 = automático)
    "vivo_radio": 300,        # radio urgente alrededor del coche (m)
    "vivo_radio_fondo": 1200, # radio de precalentado en segundo plano (m)
    "vivo_pad_osm": 200,      # margen extra de datos OSM alrededor del pedazo (m)
    "vivo_tesela_osm": 1024,  # tamaño de cada tesela de descarga OSM (m)
    "vivo_r_carga": 430,      # radio de carga en AC (m)
    "vivo_r_soltar": 640,     # radio para liberar pedazos lejanos en AC (m)
}


def vivo_params(a=None):
    """Parámetros del motor de teselas/modo en vivo acotados a rangos seguros."""
    d = a if a is not None else cargar()

    def _num(clave, lo, hi, tipo=float):
        try:
            v = tipo(d.get(clave, DEFECTO[clave]))
        except (TypeError, ValueError):
            v = tipo(DEFECTO[clave])
        return max(lo, min(hi, v))

    r_carga = _num("vivo_r_carga", 200, 1000, int)
    r_soltar = _num("vivo_r_soltar", max(350, r_carga + 100), 1500, int)
    intervalo = round(_num("vivo_intervalo", 0.1, 2.0, float), 2)
    return {
        "lado": _num("vivo_lado", 128, 512, int),
        "margen": _num("vivo_margen", 16, 160, int),
        "intervalo": intervalo,
        "trabajadores": _num("vivo_trabajadores", 0, 8, int),
        "radio": _num("vivo_radio", 150, 800, int),
        "radio_fondo": _num("vivo_radio_fondo", 300, 2400, int),
        "pad_osm": _num("vivo_pad_osm", 50, 400, int),
        "tesela_osm": _num("vivo_tesela_osm", 512, 2048, int),
        "r_carga": r_carga,
        "r_soltar": r_soltar,
        "t_foco": round(max(0.25, min(2.0, intervalo * 2.0)), 2),
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
