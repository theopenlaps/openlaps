"""Perfiles por región: lo que cambia de un país a otro.

Cada perfil reúne las normas de señalamiento, el estilo de construcción, la vegetación y las
fuentes de datos abiertas de mejor calidad que existen para esa zona (ortofoto y relieve).
El perfil activo se elige al empezar (`set_active`) y los módulos lo consultan con `active()`.

Fuentes de alta resolución usadas:
- España: ortofoto PNOA máxima actualidad (IGN, 25 cm, CC BY 4.0 scne.es), MDT05 del IGN
  (relieve del terreno a 5 m, CC BY 4.0 scne.es) y edificios del Catastro con sus plantas reales
  (salvo País Vasco y Navarra).
- Francia: BD ORTHO (IGN, 20 cm, Licence Ouverte Etalab 2.0) vía Géoplateforme.
- México: no hay ortofoto abierta de alta resolución; se usa Sentinel-2 (10 m) y, si se pasa
  con --dem, el CEM 3.0 del INEGI.
- Estados Unidos: ortofoto NAIP (USDA, 60 cm, dominio público), relieve 3DEP del USGS (1 m con
  LiDAR, dominio público) y alturas de edificios de Overture Maps (ODbL).
"""
from dataclasses import dataclass, field, replace


@dataclass
class Region:
    code: str
    name: str
    country: str                    # nombre para ui_track.json (Content Manager lo usa para la bandera)
    # señalamiento horizontal
    center_mat: str = "raya_blanca"  # raya separadora de sentidos
    left_edge_mat: str = "raya_blanca"  # orilla izquierda en vías de un sentido
    line_w: float = 0.10            # ancho de raya en carretera convencional (m)
    line_w_fast: float = 0.15       # en autopista / autovía
    dash_rural: tuple = (3.5, 9.0)  # (pintado, sin pintar) en m
    dash_urban: tuple = (2.0, 5.5)
    curb_yellow_corners: bool = False
    # señalamiento vertical
    stop_text: str = "STOP"
    curve_sign: str = "triangle"    # "triangle" (Europa) o "diamond" (América)
    # edificios
    roof: str = "pitched"           # "flat" (azotea) o "pitched" (tejado a cuatro aguas)
    roof_colors: list = field(default_factory=lambda: [(168, 84, 58), (150, 74, 52), (182, 102, 70), (120, 112, 104)])
    tinaco: bool = False
    facade_colors: list = field(default_factory=lambda: [
        (236, 230, 216), (228, 214, 188), (214, 196, 160), (200, 176, 140), (240, 238, 232),
        (210, 190, 170), (186, 160, 128), (226, 220, 206)])
    levels_default: tuple = (2, 3, 4)   # niveles típicos (al azar) si OSM no dice nada
    asphalt_tone: float = 92.0      # gris medio del asfalto (0-255): México oscuro, España más claro
    pass_sight: tuple = (3.0, 30.0)  # visibilidad mínima para raya discontinua: a·v + b (m, v en km/h)
    delineator: str = "plain"       # "plain" (fantasma blanco) o "hito" (blanco con franja negra, Europa)
    delineator_gap: tuple = (25.0, 10.0)   # separación en recta y en curva (m)
    # vegetación: especies permitidas (las demás se cambian por la más parecida)
    species: tuple = ("encino", "pino", "arbusto", "tropical", "palma")
    # fuentes de datos
    ortho: str = ""                 # "" | "pnoa" | "geopf"
    dem: str = ""                   # "" | "mdt05"
    cadastre: str = ""              # "" | "catastro" (huellas y plantas reales de cada edificio)
    heights: str = ""               # "" | "overture" (alturas de edificios de Overture Maps)
    ortho_mpp: float = 0.25         # resolución a la que se pide la ortofoto (m/px)
    # calle y ciudad
    lane_mult: float = 1.0          # multiplicador del ancho de carril por defecto
    sidewalk_w: float = 1.8         # ancho de banqueta (m)
    center_double: bool = False     # raya central doble continua donde no se puede rebasar (EE. UU.)
    parking_default: bool = False   # en ciudad, estacionamiento en ambos lados si OSM no dice nada
    speed_unit: str = "kmh"         # "kmh" | "mph": unidades de las señales de velocidad
    facade_style: str = ""          # estilo de fachada de casas ("" = según el techo: mx / eu; "us" = tablilla)
    roof_kind: str = "tiles"        # "tiles" (teja) | "shingles" (tela asfáltica)
    flat_from_floors: int = 0       # desde cuántas plantas la azotea es plana (0 = según roof)
    tower_floors: int = 0           # desde cuántas plantas la fachada es de torre de oficinas (0 = nunca)
    attribution: list = field(default_factory=list)
    # subregión (ver SUBREGIONES): "" en el perfil del país
    sub: str = ""
    sub_name: str = ""
    sub_name_en: str = ""


MX = Region(
    code="mx", name="México", country="Mexico",
    center_mat="raya_amarilla", left_edge_mat="raya_amarilla",   # NOM-034-SCT2
    line_w=0.15, line_w_fast=0.15, dash_rural=(5.0, 10.0), dash_urban=(3.0, 5.0),
    curb_yellow_corners=True, stop_text="ALTO", curve_sign="diamond",
    roof="flat", tinaco=True,
    facade_colors=[(196, 86, 52), (226, 170, 60), (40, 92, 160), (214, 52, 120), (236, 232, 222),
                   (110, 160, 90), (232, 140, 110), (180, 170, 150)],
    levels_default=(1, 1, 2, 2, 3),
    species=("encino", "pino", "arbusto", "tropical", "palma", "organo", "maguey"),
)

ES = Region(
    code="es", name="España", country="Spain",
    # Norma 8.2-IC: rayas blancas de 10 cm (15 en autovía); discontinua M-1.3 en carretera
    line_w=0.10, line_w_fast=0.15, dash_rural=(3.5, 9.0), dash_urban=(2.0, 5.5),
    stop_text="STOP", curve_sign="triangle", roof="pitched",
    levels_default=(1, 2, 2, 2, 2, 2, 3),   # pueblo típico; en ciudad OSM suele traer building:levels
    asphalt_tone=116.0, delineator="hito", delineator_gap=(50.0, 20.0), pass_sight=(2.3, 0.0),
    species=("encino", "pino", "arbusto", "tropical", "palma", "olivo"),
    ortho="pnoa", dem="mdt05", cadastre="catastro",
    attribution=["Ortofoto: PNOA cedido por © Instituto Geográfico Nacional de España (CC BY 4.0, scne.es)",
                 "Relieve: MDT05 © Instituto Geográfico Nacional de España (CC BY 4.0, scne.es)",
                 "Edificios y número de plantas: © Dirección General del Catastro (servicio INSPIRE)"],
)

FR = Region(
    code="fr", name="Francia", country="France",
    line_w=0.12, line_w_fast=0.15, dash_rural=(3.0, 10.0), dash_urban=(3.0, 3.5),
    roof="pitched", roof_colors=[(150, 80, 60), (90, 92, 100), (170, 96, 70), (110, 110, 112)],
    species=("encino", "pino", "arbusto", "tropical"),
    ortho="geopf",
    attribution=["Ortofoto: BD ORTHO © IGN (Licence Ouverte Etalab 2.0), vía Géoplateforme"],
)

US = Region(
    code="us", name="Estados Unidos", country="USA",
    # MUTCD 11.ª ed.: amarilla entre sentidos (doble continua sin rebase), blanca entre carriles y
    # orilla derecha, rayas de 4-6 in; discontinua de 10 ft pintados / 30 ft sin pintar
    center_mat="raya_amarilla", left_edge_mat="raya_amarilla", line_w=0.10, line_w_fast=0.15,
    dash_rural=(3.05, 9.14), dash_urban=(3.05, 9.14), center_double=True,
    stop_text="STOP", curve_sign="diamond", speed_unit="mph",
    roof="pitched", roof_kind="shingles", flat_from_floors=4, tower_floors=8, facade_style="us",
    roof_colors=[(84, 84, 86), (66, 62, 60), (106, 94, 82), (122, 118, 112)],
    facade_colors=[(238, 236, 230), (214, 200, 172), (190, 194, 194), (166, 184, 198), (198, 174, 138),
                   (170, 178, 152), (226, 214, 186), (150, 156, 164)],
    levels_default=(1, 1, 2, 2, 2), asphalt_tone=82.0, lane_mult=1.03, sidewalk_w=2.4, parking_default=True,
    species=("encino", "pino", "arbusto", "tropical", "palma"),
    ortho="naip", ortho_mpp=0.5, dem="3dep", heights="overture",
    attribution=["Ortofoto: NAIP, USDA Farm Service Agency (dominio público), vía USGS The National Map",
                 "Relieve: USGS 3D Elevation Program (3DEP) (dominio público)",
                 "Alturas de edificios: © Overture Maps Foundation, © OpenStreetMap contributors, Microsoft, Esri (ODbL 1.0)"],
)

EU = Region(code="eu", name="Europa (genérico)", country="", roof="pitched",
            species=("encino", "pino", "arbusto", "tropical"))

GENERIC = Region(code="generic", name="Genérico", country="")

REGIONS = {r.code: r for r in (MX, ES, FR, US, EU, GENERIC)}


# =========================================================================== subregiones
# Dentro de un país no se construye igual en todas partes: en México las casas de colores son cosa de los pueblos
# del centro y del sur, no de las colonias de bloque gris de una gran ciudad ni del norte; en España el pueblo
# blanco andaluz no se parece a la piedra y la pizarra del norte. Una subregión es el perfil del país con unos
# pocos campos cambiados (colores de fachada y de tejado, forma del techo, plantas típicas, especies). Tiene
# siempre las mismas 8 fachadas: no añade materiales ni texturas a la pista, solo cambia cuáles son.
#
# Las cajas son (lat sur, lat norte, lon oeste, lon este), aproximadas a propósito: el estilo cambia poco a poco
# y en el borde se puede elegir a mano (--subregion, o «Estilo de la zona» en la app).
def _sub(base, sub, nombre, nombre_en, **cambios):
    return replace(base, sub=sub, sub_name=nombre, sub_name_en=nombre_en, **cambios)


_MX_PUEBLO = _sub(
    MX, "mx_pueblo", "Pueblo (mezcla de color, blanco y bloque)", "Town (mix of colour, white and bare block)",
    # el pueblo corriente: la mitad pintado de color, lo demás blanco, beige o bloque sin pintar
    facade_colors=[(236, 232, 222), (214, 196, 160), (158, 152, 144), (196, 110, 78), (226, 186, 96),
                   (96, 132, 170), (222, 150, 140), (140, 170, 130)])
_MX_COLONIAL = _sub(
    MX, "mx_colonial", "Pueblo colonial de colores", "Colourful colonial town",
    facade_colors=list(MX.facade_colors))
_MX_CIUDAD = _sub(
    MX, "mx_ciudad", "Gran ciudad (bloque y concreto)", "Big city (block and concrete)",
    # colonias de autoconstrucción y edificios: gris de bloque, concreto, blancos y beiges; el color es la excepción
    facade_colors=[(150, 146, 140), (172, 168, 160), (228, 224, 214), (206, 190, 160), (222, 206, 170),
                   (160, 166, 170), (176, 112, 88), (224, 196, 120)],
    levels_default=(1, 2, 2, 2, 3, 3, 4))
_MX_NORTE = _sub(
    MX, "mx_norte", "Norte (desierto, tonos arena)", "North (desert, sand tones)",
    facade_colors=[(226, 212, 184), (236, 230, 216), (204, 184, 150), (214, 198, 170), (240, 236, 228),
                   (190, 170, 140), (222, 190, 150), (200, 196, 186)],
    levels_default=(1, 1, 1, 2, 2),
    species=("encino", "pino", "arbusto", "palma", "organo", "maguey"))
_MX_SURESTE = _sub(
    MX, "mx_sureste", "Sureste (blanco y pastel, trópico)", "Southeast (white and pastel, tropics)",
    facade_colors=[(242, 238, 228), (238, 214, 140), (232, 196, 170), (180, 210, 200), (240, 228, 200),
                   (214, 150, 120), (226, 232, 236), (236, 170, 150)],
    levels_default=(1, 1, 1, 2),
    species=("encino", "arbusto", "tropical", "palma"))

_ES_NORTE = _sub(
    ES, "es_norte", "Norte (piedra, blanco y pizarra)", "North (stone, white and slate)",
    facade_colors=[(232, 230, 224), (214, 212, 206), (190, 186, 178), (168, 162, 152), (238, 236, 230),
                   (204, 196, 182), (150, 146, 140), (222, 214, 198)],
    roof_colors=[(96, 98, 104), (150, 74, 52), (120, 112, 104), (168, 84, 58)],
    species=("encino", "pino", "arbusto", "tropical"))
_ES_SUR = _sub(
    ES, "es_sur", "Sur (pueblo blanco)", "South (white village)",
    facade_colors=[(246, 244, 238), (240, 238, 230), (236, 232, 222), (244, 240, 232), (228, 222, 206),
                   (238, 226, 196), (232, 236, 236), (222, 210, 186)],
    roof_colors=[(182, 102, 70), (196, 120, 84), (168, 84, 58), (176, 110, 80)])
_ES_MEDITERRANEO = _sub(
    ES, "es_mediterraneo", "Mediterráneo (ocres y salmón)", "Mediterranean (ochre and salmon)",
    facade_colors=[(232, 214, 180), (226, 196, 150), (214, 170, 130), (240, 232, 214), (206, 150, 116),
                   (236, 222, 160), (222, 206, 190), (196, 180, 150)])
_ES_CANARIAS = _sub(
    ES, "es_canarias", "Canarias (blanco, color y azotea)", "Canary Islands (white, colour and flat roofs)",
    facade_colors=[(244, 242, 236), (238, 214, 120), (96, 150, 170), (214, 110, 80), (240, 236, 226),
                   (130, 170, 120), (232, 180, 150), (226, 222, 210)],
    roof="flat", species=("pino", "arbusto", "tropical", "palma"))

_FR_SUR = _sub(
    FR, "fr_sur", "Sur (ocres y teja)", "South (ochre and clay tiles)",
    facade_colors=[(232, 208, 160), (226, 190, 140), (214, 170, 120), (240, 226, 196), (206, 160, 120),
                   (236, 214, 180), (222, 196, 170), (198, 176, 140)],
    roof_colors=[(182, 102, 70), (196, 120, 84), (168, 84, 58), (176, 110, 80)],
    species=("encino", "pino", "arbusto", "tropical", "olivo", "palma"))

_US_SUROESTE = _sub(
    US, "us_suroeste", "Suroeste (estuco y teja)", "Southwest (stucco and clay tiles)",
    facade_colors=[(226, 212, 188), (214, 196, 166), (236, 228, 212), (198, 178, 146), (222, 200, 170),
                   (206, 186, 160), (240, 236, 228), (188, 160, 130)],
    roof_kind="tiles", roof_colors=[(168, 84, 58), (150, 74, 52), (182, 102, 70), (120, 112, 104)])
_US_PACIFICO = _sub(
    US, "us_pacifico", "Costa del Pacífico (madera pintada en pastel)", "Pacific coast (pastel painted wood)",
    facade_colors=[(232, 226, 210), (196, 206, 198), (214, 196, 170), (170, 186, 196), (226, 206, 186),
                   (160, 168, 150), (206, 190, 200), (150, 156, 164)])
_US_SURESTE = _sub(
    US, "us_sureste", "Sureste y Golfo (estuco pastel)", "Southeast and Gulf (pastel stucco)",
    facade_colors=[(240, 232, 214), (236, 214, 180), (226, 196, 176), (190, 214, 206), (240, 238, 232),
                   (222, 206, 160), (206, 196, 214), (214, 222, 226)])

# por país, en el orden en que se prueban sus cajas (la primera que contiene el punto gana)
SUBREGIONES = {
    "mx": [_MX_CIUDAD, _MX_COLONIAL, _MX_SURESTE, _MX_NORTE, _MX_PUEBLO],
    "es": [_ES_CANARIAS, _ES_NORTE, _ES_SUR, _ES_MEDITERRANEO],
    "fr": [_FR_SUR],
    "us": [_US_SUROESTE, _US_SURESTE, _US_PACIFICO],
}
SUBS = {s.sub: s for lista in SUBREGIONES.values() for s in lista}

_CAJAS = {
    # zonas metropolitanas: Valle de México, Guadalajara, Monterrey, Puebla, Toluca, Tijuana, León, Querétaro,
    # Ciudad Juárez, Mérida no (sureste)
    "mx_ciudad": [(19.05, 19.75, -99.40, -98.85), (20.50, 20.80, -103.50, -103.20), (25.55, 25.85, -100.50, -100.10),
                  (18.95, 19.15, -98.32, -98.10), (19.22, 19.36, -99.72, -99.52), (32.40, 32.56, -117.13, -116.80),
                  (21.05, 21.20, -101.76, -101.58), (20.53, 20.68, -100.50, -100.34), (31.60, 31.78, -106.56, -106.30)],
    # Guanajuato, San Miguel de Allende, Oaxaca, San Cristóbal, Campeche, Tlacotalpan, Tepoztlán, Taxco, Pátzcuaro,
    # Zacatlán / Cuetzalan, Valle de Bravo, Izamal
    "mx_colonial": [(20.98, 21.05, -101.30, -101.22), (20.88, 20.95, -100.78, -100.70), (17.03, 17.10, -96.76, -96.69),
                    (16.70, 16.77, -92.67, -92.60), (19.82, 19.87, -90.56, -90.50), (18.60, 18.63, -95.67, -95.64),
                    (18.95, 19.02, -99.13, -99.06), (18.53, 18.58, -99.63, -99.58), (19.49, 19.54, -101.63, -101.58),
                    (19.90, 20.05, -97.99, -97.50), (19.17, 19.22, -100.15, -100.10), (20.92, 20.95, -89.04, -89.00)],
    # península de Yucatán, Tabasco y la costa de Chiapas
    "mx_sureste": [(17.8, 21.7, -92.5, -86.6), (17.2, 18.7, -94.2, -92.5)],
    "mx_norte": [(24.0, 32.8, -118.5, -97.0)],
    "mx_pueblo": [(14.4, 32.8, -118.5, -86.6)],
    "es_canarias": [(27.5, 29.5, -18.3, -13.3)],
    # Galicia, Asturias, Cantabria, País Vasco y el norte de Navarra, León y Burgos
    "es_norte": [(42.3, 43.9, -9.6, -1.4), (41.8, 42.3, -9.0, -6.6)],
    # Andalucía, Murcia y el sur de Extremadura
    "es_sur": [(35.9, 38.6, -7.6, -0.6)],
    # costa de Cataluña, Comunidad Valenciana y Baleares
    "es_mediterraneo": [(38.6, 40.6, -0.9, 0.6), (40.6, 42.5, 0.3, 3.4), (38.6, 40.1, 1.1, 4.4)],
    "fr_sur": [(41.3, 44.6, -1.9, 9.7)],
    # sur de California, Arizona, Nuevo México, sur de Nevada y oeste de Texas
    "us_suroeste": [(31.3, 37.0, -121.0, -100.0), (25.8, 31.3, -106.7, -100.0)],
    # Florida y la costa del Golfo
    "us_sureste": [(24.3, 31.0, -98.0, -79.8)],
    "us_pacifico": [(37.0, 49.5, -125.0, -120.0)],
}


def subregiones(code):
    """Subregiones de ese país (lista vacía si no tiene)."""
    return list(SUBREGIONES.get(code, []))


def detect_sub(region, lat, lon):
    """Subregión de ese punto dentro del país `region`, o el propio perfil del país si no cae en ninguna."""
    for s in SUBREGIONES.get(region.code, []):
        for la0, la1, lo0, lo1 in _CAJAS.get(s.sub, ()):
            if la0 <= lat <= la1 and lo0 <= lon <= lo1:
                return s
    return region


def elegir(region, sub, lat, lon):
    """Perfil para generar: `region` y `sub` como llegan de la línea de comandos ("auto" o un código; en `sub`
    también "no" para quedarse con el perfil del país)."""
    r = detect(lat, lon) if region in (None, "", "auto") else REGIONS[SUBS[region].code if region in SUBS else region]
    if region in SUBS:
        return SUBS[region]
    if sub in (None, "", "auto"):
        return detect_sub(r, lat, lon)
    if sub in SUBS and SUBS[sub].code == r.code:
        return SUBS[sub]
    return r                     # "no", o una subregión de otro país: el perfil del país

_active = MX


def detect(lat, lon):
    """Perfil por coordenadas (cajas aproximadas; en fronteras manda la opción --region)."""
    if 14.4 <= lat <= 32.8 and -118.5 <= lon <= -86.6 and lat < _frontera_us_mx(lon):
        return MX
    if (24.3 <= lat <= 49.5 and -125.0 <= lon <= -66.8 and lat < _frontera(_FRONTERA_CA, lon)) or (51.0 <= lat <= 71.5 and -170.0 <= lon <= -129.0)             or (18.8 <= lat <= 22.4 and -160.5 <= lon <= -154.7):
        return US
    if (35.9 <= lat <= 43.9 and -9.6 <= lon <= 3.4) or (38.6 <= lat <= 40.1 and 1.1 <= lon <= 4.4) \
            or (27.5 <= lat <= 29.5 and -18.3 <= lon <= -13.3):
        return ES
    if 41.3 <= lat <= 51.2 and -5.3 <= lon <= 9.7:
        return FR
    if 34.0 <= lat <= 71.5 and -25.0 <= lon <= 45.0:
        return EU
    return GENERIC


# frontera EE. UU.-México aproximada (lon, lat) de oeste a este: Tijuana, Arizona, El Paso,
# Big Bend y el río Bravo hasta Brownsville
_FRONTERA = [(-118.5, 32.53), (-117.12, 32.53), (-114.72, 32.72), (-111.07, 31.33), (-108.21, 31.33),
             (-108.21, 31.78), (-106.53, 31.78), (-104.9, 30.6), (-103.3, 29.0), (-102.4, 29.8),
             (-101.4, 29.77), (-100.3, 28.3), (-99.5, 27.5), (-99.1, 26.4), (-98.2, 26.06), (-97.5, 25.885),
             (-97.15, 25.95), (-86.6, 25.95)]
# frontera EE. UU.-Canadá aproximada: paralelo 49 al oeste y Grandes Lagos / San Lorenzo al este
_FRONTERA_CA = [(-125.0, 49.0), (-95.15, 49.0), (-94.8, 49.3), (-93.0, 48.6), (-89.6, 48.0), (-84.6, 46.5),
                (-82.4, 45.3), (-82.4, 43.0), (-83.1, 42.25), (-81.5, 42.6), (-79.0, 42.9), (-79.0, 43.3),
                (-76.4, 43.6), (-76.3, 44.2), (-74.7, 45.0), (-71.5, 45.0), (-71.1, 45.3), (-70.0, 46.4),
                (-69.2, 47.45), (-67.8, 47.1), (-67.8, 45.7), (-67.0, 45.0), (-66.8, 44.8)]


def _frontera(puntos, lon):
    xs, ys = zip(*puntos)
    import numpy as np
    return float(np.interp(lon, xs, ys))


def _frontera_us_mx(lon):
    """Latitud de la frontera EE. UU.-México en esa longitud (al norte, EE. UU.)."""
    return _frontera(_FRONTERA, lon)


def set_active(region):
    global _active
    _active = region if isinstance(region, Region) else (REGIONS.get(region) or SUBS[region])
    return _active


def active():
    return _active


def map_species(kind):
    """Cambia una especie que no existe en la región por la más parecida."""
    sp = _active.species
    if kind in sp:
        return kind
    sub = {"organo": "arbusto", "maguey": "arbusto", "palma": "tropical", "tropical": "encino"}
    k = sub.get(kind, "encino")
    return k if k in sp else sp[0]
