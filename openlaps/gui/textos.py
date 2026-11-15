"""Mensajes que la parte de Python devuelve a la interfaz, en español e inglés."""
import ctypes

from . import ajustes

TEXTOS = {
    "es": {
        "ocupado": "Ya hay una generación en marcha.",
        "faltan_datos": "Faltan datos de la zona.",
        "carpeta_salida": "No se pudo usar la carpeta de salida: {e}",
        "no_inicia": "No se pudo iniciar el generador: {e}",
        "ruta_2": "Pon al menos la salida y la meta en el mapa.",
        "ruta_3": "Un circuito necesita al menos tres puntos.",
        "sin_red": "No hay conexión con los servidores de datos. Revisa tu internet e inténtalo otra vez.",
        "sin_memoria": "Se acabó la memoria. Prueba con una zona más pequeña o con calidad rápida.",
        "sin_detalles": "El generador se cerró sin dar detalles.",
        "buscar_fallo": "No se pudo buscar ({e}). Revisa tu conexión.",
        "coordenadas": "Coordenadas",
        "osm_fallo": "OpenStreetMap no respondió ({e}). La generación puede funcionar igual.",
        "solo_ventana": "Los diálogos de archivos solo funcionan en la ventana de OpenLaps.",
        "no_acs": "En esa carpeta no está acs.exe. Elige la carpeta assettocorsa.",
        "ac_no": "No encontré Assetto Corsa. Indica su carpeta en Ajustes.",
        "ac_sin_kn5": "Esta pista se generó sin la opción de Assetto Corsa. Vuelve a generarla con esa casilla marcada.",
        "ac_ya": "Ya estaba instalada; se actualiza sola cada vez que la vuelves a generar.",
        "ac_otra": "Ya hay otra pista llamada «{n}» en Assetto Corsa. Genera esta con otro nombre.",
        "ac_enlace": "No se pudo crear el enlace en {d}: {e}",
        "ac_ok": "Instalada. En Content Manager busca «openlaps» en la lista de pistas (pulsa F5 si ya estaba abierto).",
        "dem_tipo": "Relieve GeoTIFF (*.tif;*.tiff)",
    },
    "en": {
        "ocupado": "A generation is already running.",
        "faltan_datos": "Some area data is missing.",
        "carpeta_salida": "Couldn't use the output folder: {e}",
        "no_inicia": "Couldn't start the generator: {e}",
        "ruta_2": "Place at least the start and the finish on the map.",
        "ruta_3": "A circuit needs at least three points.",
        "sin_red": "Can't reach the data servers. Check your internet connection and try again.",
        "sin_memoria": "Ran out of memory. Try a smaller area or the fast quality.",
        "sin_detalles": "The generator closed without giving details.",
        "buscar_fallo": "Search failed ({e}). Check your connection.",
        "coordenadas": "Coordinates",
        "osm_fallo": "OpenStreetMap didn't respond ({e}). Generating may still work.",
        "solo_ventana": "File dialogs only work in the OpenLaps window.",
        "no_acs": "acs.exe isn't in that folder. Choose the assettocorsa folder.",
        "ac_no": "Assetto Corsa wasn't found. Set its folder in Settings.",
        "ac_sin_kn5": "This track was generated without the Assetto Corsa option. Generate it again with that box checked.",
        "ac_ya": "It was already installed; it updates by itself every time you generate it again.",
        "ac_otra": "There's already another track called “{n}” in Assetto Corsa. Generate this one with another name.",
        "ac_enlace": "Couldn't create the link in {d}: {e}",
        "ac_ok": "Installed. In Content Manager, search for “openlaps” in the track list (press F5 if it was already open).",
        "dem_tipo": "GeoTIFF elevation (*.tif;*.tiff)",
    },
}


def idioma_sistema():
    """'es' si Windows está en español, si no 'en'."""
    try:
        return "es" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x0A else "en"
    except (AttributeError, OSError):
        return "en"


def idioma():
    i = ajustes.cargar().get("idioma")
    return i if i in TEXTOS else idioma_sistema()


def t(clave, **kw):
    s = TEXTOS[idioma()].get(clave) or TEXTOS["es"][clave]
    return s.format(**kw) if kw else s
