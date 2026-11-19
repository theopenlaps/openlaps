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
        "google3d_sin_token": "Los edificios de Google 3D necesitan un token de Cesium ion (Opciones avanzadas): "
                              "esta vez se usan los de OpenLaps.",
        "google3d_sin_datos": "Esta pista no tiene los datos para los edificios de Google 3D (vuelve a generarla): "
                              "esta vez se usan los de OpenLaps.",
        "ac_sin_kn5": "Esta pista se generó sin la opción de Assetto Corsa. Vuelve a generarla con esa casilla marcada.",
        "ac_ya": "Ya estaba instalada; se actualiza sola cada vez que la vuelves a generar.",
        "ac_otra": "Ya hay otra pista llamada «{n}» en Assetto Corsa.",
        "vivo_sin_zona": "No encuentro los datos de esa zona en vivo. Dibújala otra vez en el mapa.",
        "borrar_en_uso": "No se pudo borrar «{n}»: está en uso. Cierra Assetto Corsa y Content Manager y vuelve a intentarlo.",
        "borrar_ocupado": "Espera a que termine la generación (o detén el modo en vivo) antes de borrar.",
        "borrada": "«{n}» borrada: {mb} MB libres.",
        "liberado": "{mb} MB libres.",
        "ac_en_uso": "No se pudo quitar «{n}» de Assetto Corsa: cierra el juego y Content Manager y vuelve a intentarlo.",
        "ac_jugando_trafico": "Abriendo Assetto Corsa en «{p}» con tráfico… (Steam tiene que estar abierto)",
        "nombre_existe": "Ya existe una pista llamada «{n}». Elige otro nombre.",
        "renombrada": "Pista renombrada a «{n}».",
        "ac_enlace": "No se pudo crear el enlace en {d}: {e}",
        "ac_ok": "Instalada. En Content Manager busca «openlaps» en la lista de pistas (pulsa F5 si ya estaba abierto).",
        "dem_tipo": "Relieve GeoTIFF (*.tif;*.tiff)",
        "zona_grande": "La zona pasa del máximo de {km:g} km². Para más, activa el modo en vivo.",
        "zona_es_vivo": "Con el modo en vivo activado la zona se prepara en vivo.",
        "ac_abierto": "Assetto Corsa ya está abierto. Ciérralo y vuelve a pulsar «Jugar».",
        "ac_sin_race": "No encontré la configuración de Assetto Corsa (Documentos\\Assetto Corsa\\cfg\\race.ini). Abre el juego una vez desde Steam.",
        "ac_jugando": "Abriendo Assetto Corsa en «{p}»… (Steam tiene que estar abierto)",
        "vivo_ocupado": "El modo en vivo ya está en marcha.",
        "vivo_sin_csp": "El modo en vivo en Assetto Corsa necesita Custom Shaders Patch (gratis, se instala con Content Manager). En el navegador funciona igual.",
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
        "google3d_sin_token": "Google 3D buildings need a Cesium ion token (Advanced options): using OpenLaps "
                              "buildings this time.",
        "google3d_sin_datos": "This track lacks the data for Google 3D buildings (generate it again): using OpenLaps "
                              "buildings this time.",
        "ac_sin_kn5": "This track was generated without the Assetto Corsa option. Generate it again with that box checked.",
        "ac_ya": "It was already installed; it updates by itself every time you generate it again.",
        "ac_otra": "There's already another track called “{n}” in Assetto Corsa.",
        "vivo_sin_zona": "Can't find the data for that live area. Draw it again on the map.",
        "borrar_en_uso": "Couldn't delete “{n}”: it's in use. Close Assetto Corsa and Content Manager and try again.",
        "borrar_ocupado": "Wait for the generation to finish (or stop live mode) before deleting.",
        "borrada": "“{n}” deleted: {mb} MB freed.",
        "liberado": "{mb} MB freed.",
        "ac_en_uso": "Couldn't remove “{n}” from Assetto Corsa: close the game and Content Manager and try again.",
        "ac_jugando_trafico": "Opening Assetto Corsa on «{p}» with traffic… (Steam must be running)",
        "nombre_existe": "A track called “{n}” already exists. Choose another name.",
        "renombrada": "Track renamed to “{n}”.",
        "ac_enlace": "Couldn't create the link in {d}: {e}",
        "ac_ok": "Installed. In Content Manager, search for “openlaps” in the track list (press F5 if it was already open).",
        "dem_tipo": "GeoTIFF elevation (*.tif;*.tiff)",
        "zona_grande": "The area is over the {km:g} km² maximum. For more, turn on live mode.",
        "zona_es_vivo": "With live mode on, the area is prepared live.",
        "ac_abierto": "Assetto Corsa is already running. Close it and press «Play» again.",
        "ac_sin_race": "Couldn't find Assetto Corsa's settings (Documents\\Assetto Corsa\\cfg\\race.ini). Start the game once from Steam.",
        "ac_jugando": "Opening Assetto Corsa on «{p}»… (Steam must be running)",
        "vivo_ocupado": "Live mode is already running.",
        "vivo_sin_csp": "Live mode in Assetto Corsa needs Custom Shaders Patch (free, installed with Content Manager). It works in the browser anyway.",
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
