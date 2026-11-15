"""Perfiles por región: lo que cambia de un país a otro.

Cada perfil reúne las normas de señalamiento, el estilo de construcción, la vegetación y las
fuentes de datos abiertas de mejor calidad que existen para esa zona (ortofoto y relieve).
El perfil activo se elige al empezar (`set_active`) y los módulos lo consultan con `active()`.

Fuentes de alta resolución usadas:
- España: ortofoto PNOA máxima actualidad (IGN, 25 cm, CC BY 4.0 scne.es) y MDT05 del IGN
  (relieve del terreno a 5 m, CC BY 4.0 scne.es).
- Francia: BD ORTHO (IGN, 20 cm, Licence Ouverte Etalab 2.0) vía Géoplateforme.
- México: no hay ortofoto abierta de alta resolución; se usa Sentinel-2 (10 m) y, si se pasa
  con --dem, el CEM 3.0 del INEGI.
"""
from dataclasses import dataclass, field


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
    attribution: list = field(default_factory=list)


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
    ortho="pnoa", dem="mdt05",
    attribution=["Ortofoto: PNOA cedido por © Instituto Geográfico Nacional de España (CC BY 4.0, scne.es)",
                 "Relieve: MDT05 © Instituto Geográfico Nacional de España (CC BY 4.0, scne.es)"],
)

FR = Region(
    code="fr", name="Francia", country="France",
    line_w=0.12, line_w_fast=0.15, dash_rural=(3.0, 10.0), dash_urban=(3.0, 3.5),
    roof="pitched", roof_colors=[(150, 80, 60), (90, 92, 100), (170, 96, 70), (110, 110, 112)],
    species=("encino", "pino", "arbusto", "tropical"),
    ortho="geopf",
    attribution=["Ortofoto: BD ORTHO © IGN (Licence Ouverte Etalab 2.0), vía Géoplateforme"],
)

EU = Region(code="eu", name="Europa (genérico)", country="", roof="pitched",
            species=("encino", "pino", "arbusto", "tropical"))

GENERIC = Region(code="generic", name="Genérico", country="")

REGIONS = {r.code: r for r in (MX, ES, FR, EU, GENERIC)}

_active = MX


def detect(lat, lon):
    """Perfil por coordenadas (cajas aproximadas; en fronteras manda la opción --region)."""
    if 14.4 <= lat <= 32.8 and -118.5 <= lon <= -86.6:
        return MX
    if (35.9 <= lat <= 43.9 and -9.6 <= lon <= 3.4) or (38.6 <= lat <= 40.1 and 1.1 <= lon <= 4.4) \
            or (27.5 <= lat <= 29.5 and -18.3 <= lon <= -13.3):
        return ES
    if 41.3 <= lat <= 51.2 and -5.3 <= lon <= 9.7:
        return FR
    if 34.0 <= lat <= 71.5 and -25.0 <= lon <= 45.0:
        return EU
    return GENERIC


def set_active(region):
    global _active
    _active = region if isinstance(region, Region) else REGIONS[region]
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
