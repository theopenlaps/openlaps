"""Constantes y reglas por defecto, ajustadas a México.

Referencias de diseño:
- NOM-034-SCT2-2011 (señalamiento horizontal): raya separadora de sentidos AMARILLA,
  raya en la orilla y separadora de carriles BLANCAS, rayas de 15 cm en carretera y
  segmentos discontinuos de 5 m pintados / 10 m sin pintar.
- Manual de Proyecto Geométrico de Carreteras (SCT): carriles de 3.5 m en red federal,
  acotamientos de 1 a 2.5 m y sobreelevación (peralte) máxima de 10 %, normalmente 8 %.
"""

# --- Clases de vía de OSM que se pueden conducir ---------------------------------
DRIVABLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "track", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}

# Calles peatonales: en los cascos antiguos son calles enteras (Preciados, Arenal…), así que entran a la red
# como calle pavimentada a ras de suelo (sin rayas ni banquetas); si no, quedaban como terreno y las calles
# que llegan a ellas terminaban en una caja de bordillos
PAVED_PATHS = {"pedestrian"}
NETWORK = DRIVABLE | PAVED_PATHS

# Caminos a pie que solo se pintan en la textura del suelo (veredas)
PATHS = {"footway", "path", "steps", "cycleway", "bridleway"}
# ...salvo estas, que con --veredas entran a la red como brechas angostas de tierra (offroad): las sendas de
# monte. Las aceras y carriles bici (footway, cycleway) y las escaleras nunca.
VEREDAS = {"path", "bridleway"}

# Vías en túnel que NO se construyen: rampas y pasillos de estacionamientos subterráneos (en el centro de una
# ciudad hay decenas y cada una dejaba una boca de túnel en medio de la calle); los túneles de verdad
# (de primary a residential) sí
TUNNEL_SKIP_CLASSES = {"service", "track"}
# con --tuneles principales (por defecto) solo se construyen los túneles de estas clases
TUNNEL_MAIN_CLASSES = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}
TUNNEL_SKIP_SERVICE = {"parking_aisle", "driveway", "parking", "drive-through"}

# ancho de carril (m), carriles por defecto si es doble sentido, acotamiento (m),
# velocidad de proyecto (km/h), sigma del suavizado vertical (m), prioridad
ROAD_CLASS = {
    #               carril  carriles  acot  vel  sigma prio
    "motorway":      (3.50,  2,       2.50, 110, 45.0, 10),
    "trunk":         (3.50,  2,       2.00,  90, 35.0, 9),
    "primary":       (3.50,  2,       1.50,  80, 28.0, 8),
    "secondary":     (3.25,  2,       1.00,  70, 22.0, 7),
    "tertiary":      (3.00,  2,       0.50,  60, 16.0, 6),
    "unclassified":  (2.90,  2,       0.25,  50, 13.0, 5),
    "road":          (2.90,  2,       0.00,  40, 12.0, 4),
    "residential":   (3.25,  2,       0.00,  40, 10.0, 4),
    "living_street": (2.75,  2,       0.00,  20, 8.0,  3),
    "service":       (3.00,  1,       0.00,  30, 8.0,  2),
    "track":         (3.20,  1,       0.00,  30, 9.0,  1),
    "pedestrian":    (3.00,  2,       0.00,  20, 6.0,  0),
    # veredas conducibles: una senda real mide menos de 1 m; se ensancha a lo justo para un coche
    "path":          (2.60,  1,       0.00,  20, 5.0,  0),
    "bridleway":     (2.60,  1,       0.00,  20, 5.0,  0),
    "motorway_link": (3.75,  1,       1.50,  60, 25.0, 8),
    "trunk_link":    (3.75,  1,       1.00,  50, 20.0, 7),
    "primary_link":  (3.50,  1,       0.50,  50, 16.0, 6),
    "secondary_link":(3.50,  1,       0.50,  40, 14.0, 5),
    "tertiary_link": (3.25,  1,       0.25,  40, 12.0, 4),
}

# Clases que llevan raya central / de orilla si están pavimentadas
MARKED_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary",
                  "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
# Clases que llevan banqueta cuando pasan por zona urbana
SIDEWALK_CLASSES = {"primary", "secondary", "tertiary", "unclassified", "residential",
                    "living_street", "road", "primary_link", "secondary_link", "tertiary_link"}
# Clases rurales que llevan peralte, fantasmas y defensas
RURAL_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                 "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}

# --- Superficies --------------------------------------------------------------
# superficie OSM -> (material visual, clave física de Assetto Corsa)
SURFACE_MAP = {
    "asphalt": "asfalto", "paved": "asfalto", "chipseal": "asfalto",
    "concrete": "concreto", "concrete:plates": "concreto", "concrete:lanes": "concreto",
    "sett": "adoquin", "paving_stones": "adoquin", "bricks": "adoquin",
    "cobblestone": "empedrado", "unhewn_cobblestone": "empedrado", "stone": "empedrado",
    "unpaved": "terraceria", "dirt": "terraceria", "earth": "terraceria", "ground": "terraceria",
    "gravel": "terraceria", "fine_gravel": "terraceria", "compacted": "terraceria",
    "sand": "terraceria", "mud": "terraceria", "grass": "terraceria", "pebblestone": "terraceria",
}
PHYS_KEY = {"asfalto": "ROAD", "concreto": "ROAD", "adoquin": "MXCOBBLE",
            "empedrado": "MXCOBBLE", "terraceria": "LOOSE"}
# Caminos sin asfalto: todos llevan el material visual "terraceria", pero cada superficie de OSM pisa distinto.
# LOOSE queda para lo compactado o sin detalle (unpaved, compacted); las claves no pueden empezar por otra
# clave de surfaces.ini (AC las busca por prefijo en el nombre de la malla).
PHYS_SUELTA = {"gravel": "MXGRAVA", "fine_gravel": "MXGRAVA", "pebblestone": "MXGRAVA",
               "dirt": "MXTIERRA", "earth": "MXTIERRA", "ground": "MXTIERRA", "mud": "MXTIERRA",
               "sand": "MXARENA", "grass": "MXPASTO"}
FISICAS_SUELTAS = ("LOOSE", "MXGRAVA", "MXTIERRA", "MXARENA", "MXPASTO")
# tracktype de OSM -> superficie cuando falta `surface` (grade1 es firme: asfalto o muy compactado)
TRACKTYPE = {"grade2": "gravel", "grade3": "dirt", "grade4": "ground", "grade5": "grass"}
# clases menores: las únicas a las que se les deduce terracería si OSM no dice de qué son
CLASES_MENORES = {"unclassified", "road", "residential", "living_street", "service", "track"}

# --- Banquetas y edificios ------------------------------------------------------
SIDEWALK_WIDTH = 1.8         # m
CURB_HEIGHT = 0.16           # m (guarnición)
LEVEL_HEIGHT = 3.0           # m por nivel
PARAPET = 0.45               # pretil de azotea
TINACO_PROB = 0.55           # casas con tinaco en la azotea

# colores de fachada típicos (RGB)
FACADE_COLORS = [
    (196, 86, 52),    # terracota
    (226, 170, 60),   # ocre / amarillo mostaza
    (40, 92, 160),    # azul cobalto
    (214, 52, 120),   # rosa mexicano
    (236, 232, 222),  # blanco cal
    (110, 160, 90),   # verde
    (232, 140, 110),  # salmón
    (180, 170, 150),  # gris obra negra
]

# --- Clases ESA WorldCover --------------------------------------------------------
WC_TREE, WC_SHRUB, WC_GRASS, WC_CROP, WC_BUILT, WC_BARE = 10, 20, 30, 40, 50, 60
WC_SNOW, WC_WATER, WC_WETLAND, WC_MANGROVE, WC_MOSS = 70, 80, 90, 95, 100

# color base del suelo por clase, (temporada seca, temporada de lluvias)
GROUND_COLORS = {
    WC_TREE:     ((74, 78, 46),   (52, 78, 38)),
    WC_SHRUB:    ((146, 128, 86), (110, 118, 66)),
    WC_GRASS:    ((170, 150, 90), (104, 136, 62)),
    WC_CROP:     ((150, 120, 84), (110, 130, 70)),
    WC_BUILT:    ((140, 124, 104), (124, 116, 96)),
    WC_BARE:     ((184, 160, 124), (176, 152, 118)),
    WC_SNOW:     ((235, 235, 240), (235, 235, 240)),
    WC_WATER:    ((52, 84, 100),  (52, 84, 100)),
    WC_WETLAND:  ((86, 100, 64),  (70, 100, 56)),
    WC_MANGROVE: ((50, 74, 44),   (44, 74, 40)),
    WC_MOSS:     ((120, 120, 90), (110, 120, 80)),
}


def cerrada_a_coches(tags):
    """¿Prohibida a los coches? En OSM la etiqueta más concreta manda: access=no con motor_vehicle=yes (un tramo
    de la Castellana en la plaza del Doctor Marañón) sí se circula; access=no con solo psv o bus, no. Antes
    bastaba access=no para quitarla y quedaba un hueco en medio de la avenida."""
    for k in ("motorcar", "motor_vehicle", "vehicle", "access"):
        v = tags.get(k)
        if v is not None:
            return v in ("no", "private")
    return False
