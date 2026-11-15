"""Cobertura del suelo: qué hay en cada sitio (agua, mar, bosque, roca, arena...) y de dónde sale.

Se fusionan, de menos a más prioridad:
  1. ESA WorldCover 10 m (todo el planeta, pero grueso: un río de 8 m no sale).
  2. Áreas de OSM (bosques, prados, cultivos, roquedos, playas, humedales, zonas urbanas).
  3. Agua que ve el satélite: índice NDWI de Sentinel-2 (verde frente a infrarrojo), aunque OSM
     no la tenga dibujada.
  4. Agua de OSM: lagos, embalses y ríos dibujados como área; ríos, canales y arroyos dibujados
     como línea (con su `width` o un ancho típico por tipo); el mar, a partir de la línea de costa.
Los arroyos y lagunas intermitentes (`intermittent=yes`) se tratan como cauce seco en la
temporada seca.

El resultado sirve para dos cosas: el generador sabe qué superficies son agua (malla de agua y
cauce excavado, ver `agua.py`) y `panel()` dibuja un mapa que dice qué es cada sitio, con la
fuente de cada dato y las discrepancias entre OSM y el satélite.
"""
import math

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFont
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import polygonize, unary_union

from . import config as C

AGUA, MAR, HUMEDAL, CAUCE_SECO, BOSQUE, MATORRAL, PRADERA, CULTIVO, ROCA, ARENA, NIEVE, URBANO = range(1, 13)
CLASES = {
    AGUA: ("agua", (38, 110, 205)), MAR: ("mar", (18, 55, 135)), HUMEDAL: ("humedal", (90, 165, 160)),
    CAUCE_SECO: ("cauce seco", (205, 172, 110)), BOSQUE: ("bosque", (28, 98, 42)),
    MATORRAL: ("matorral", (140, 150, 72)), PRADERA: ("pradera", (150, 205, 95)),
    CULTIVO: ("cultivo", (232, 212, 125)), ROCA: ("roca / suelo desnudo", (150, 140, 130)),
    ARENA: ("arena / playa", (238, 222, 172)), NIEVE: ("nieve / hielo", (246, 246, 252)),
    URBANO: ("urbano", (198, 82, 72)),
}
FUENTES = {1: "OSM", 2: "WorldCover", 3: "Sentinel-2 (NDWI)"}

WC_A_CLASE = {C.WC_TREE: BOSQUE, C.WC_SHRUB: MATORRAL, C.WC_GRASS: PRADERA, C.WC_CROP: CULTIVO,
              C.WC_BUILT: URBANO, C.WC_BARE: ROCA, C.WC_SNOW: NIEVE, C.WC_WATER: AGUA, C.WC_WETLAND: HUMEDAL,
              C.WC_MANGROVE: HUMEDAL, C.WC_MOSS: PRADERA}

# ancho típico (m) de los cauces dibujados como línea, si OSM no trae `width`
ANCHO_CAUCE = {"river": 14.0, "canal": 8.0, "tidal_channel": 6.0, "stream": 2.5, "brook": 2.0,
               "ditch": 1.2, "drain": 1.5, "canyon": 3.0}
_FLUYE = {"river", "stream", "canal", "rapids", "stream_pool", "ditch", "drain", "tidal_channel"}
# orden de pintado de las áreas de OSM (lo último manda)
_ORDEN = (CULTIVO, PRADERA, MATORRAL, BOSQUE, URBANO, ROCA, ARENA, NIEVE, HUMEDAL)
NDWI_AGUA = 0.3          # agua segura aunque WorldCover no la vea
NDWI_AGUA_WC = 0.05      # agua si además WorldCover dice agua
AREA_MIN_SAT = 1000.0    # m²: manchas más pequeñas que esto no cuentan como agua del satélite
ORILLA_SAT = 8.0         # m de orilla que no cuentan como discrepancia OSM/satélite


def fuente(size=13):
    """Fuente con tildes para los paneles (la que trae Pillow no tiene acentos)."""
    for nombre in ("DejaVuSans.ttf", "arial.ttf", "segoeui.ttf", "Arial.ttf", "LiberationSans-Regular.ttf",
                   "/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _num(v):
    try:
        return float(str(v).split()[0].replace(",", "."))
    except (TypeError, ValueError, IndexError):
        return None


def es_intermitente(tags):
    return tags.get("intermittent") == "yes" or tags.get("seasonal") not in (None, "no") \
        or tags.get("ephemeral") == "yes"


def clase_osm(tags):
    """Clase de cobertura de un área de OSM (None si no dice nada del suelo)."""
    n, lu, le, ww = tags.get("natural"), tags.get("landuse"), tags.get("leisure"), tags.get("waterway")
    if n == "water" or lu in ("reservoir", "basin") or ww in ("riverbank", "dock"):
        return AGUA
    if n in ("bay", "strait"):
        return MAR
    if n == "wetland":
        return HUMEDAL
    if n == "wood" or lu == "forest":
        return BOSQUE
    if n in ("scrub", "heath"):
        return MATORRAL
    if n == "grassland" or lu in ("meadow", "grass", "village_green", "recreation_ground") or le in ("park", "garden"):
        return PRADERA
    if lu in ("farmland", "orchard", "vineyard", "farmyard", "plant_nursery", "allotments", "greenhouse_horticulture"):
        return CULTIVO
    if n in ("bare_rock", "scree", "shingle", "rock", "cliff", "stone") or lu == "quarry":
        return ROCA
    if n in ("sand", "beach", "dune"):
        return ARENA
    if n == "glacier":
        return NIEVE
    if lu in ("residential", "commercial", "industrial", "retail", "construction"):
        return URBANO
    return None


def _fluye(tags):
    return tags.get("water") in _FLUYE or tags.get("waterway") == "riverbank"


def mar_desde_costa(coast_lines, rect):
    """Polígono de mar dentro de `rect` a partir de líneas de costa locales (tierra a la izquierda).

    Se parte el rectángulo por las líneas de costa y se vota, cara por cara, con puntos a un metro
    a la derecha (mar) y a la izquierda (tierra) de cada tramo de costa."""
    bb = box(*rect)
    lines = [LineString(c) for c in coast_lines if len(c) >= 2]
    lines = [ln for ln in lines if ln.intersects(bb)]
    if not lines:
        return None
    noded = unary_union([bb.exterior] + [ln.intersection(bb) for ln in lines])
    faces = [f for f in polygonize(noded) if f.area > 1.0]
    if len(faces) < 2:
        return None
    votos = np.zeros(len(faces))
    tree = shapely.STRtree(faces)
    for ln in lines:
        c = np.asarray(ln.coords)
        for a, b in zip(c[:-1], c[1:]):
            m = (a + b) / 2
            if not bb.contains(Point(m)):
                continue
            d = b - a
            L = float(np.hypot(*d))
            if L < 0.5:
                continue
            nr = np.array([d[1], -d[0]]) / L      # normal a la derecha del sentido de la línea
            for p, v in ((m + nr, 1.0), (m - nr, -1.0)):
                for k in tree.query(Point(p), predicate="intersects"):
                    votos[int(k)] += v
    mar = [f for f, v in zip(faces, votos) if v > 0]
    return unary_union(mar) if mar else None


class Cobertura:
    """Clasificación del suelo del área y geometría del agua (coordenadas locales)."""

    def __init__(self, osm, proj, rect, lc=None, ndwi=None, season="seca", res=None, log=print):
        self.proj, self.rect, self.season = proj, rect, season
        xmin, ymin, xmax, ymax = rect
        lado = max(xmax - xmin, ymax - ymin)
        self.res = res or max(2.0, lado / 1200.0)
        self.W = int(math.ceil((xmax - xmin) / self.res))
        self.H = int(math.ceil((ymax - ymin) / self.res))
        self.tr = Affine(self.res, 0, xmin, 0, -self.res, ymax)
        px = xmin + (np.arange(self.W) + 0.5) * self.res
        py = ymax - (np.arange(self.H) + 0.5) * self.res
        self.PX, self.PY = np.meshgrid(px, py)
        bb = box(*rect)

        def loc(coords):
            x, y = proj.to_local([c[0] for c in coords], [c[1] for c in coords])
            return np.column_stack([x, y])

        def poly(outer, inners=()):
            try:
                p = Polygon(loc(outer), [loc(r) for r in inners if len(r) >= 4]).buffer(0)
            except Exception:  # noqa: BLE001
                return None
            return p if not p.is_empty else None

        # ---------------------------------------------------------------- agua de OSM
        quieta, corriente, secos_poly, land = [], [], [], {k: [] for k in _ORDEN}
        for a in osm.get("areas", []):
            t = a["tags"]
            k = clase_osm(t)
            if k is None or len(a["outer"]) < 4:
                continue
            p = poly(a["outer"], a.get("inners", []))
            if p is None or not p.intersects(bb):
                continue
            if k == AGUA:
                if es_intermitente(t) and season == "seca":
                    secos_poly.append(p)
                else:
                    (corriente if _fluye(t) else quieta).append(p)
            elif k == MAR:
                quieta.append(p)
            elif k in land:
                land[k].append(p)
        self.secos = []               # (coords Nx2, ancho) de cauces secos dibujados como línea
        n_lineas = 0
        for w in osm.get("ways", []):
            t = w["tags"]
            ww = t.get("waterway")
            if not ww:
                continue
            c = loc(w["coords"])
            closed = len(c) >= 4 and np.allclose(c[0], c[-1])
            if ww in ("riverbank", "dock") and closed:
                p = poly(w["coords"])
                if p is not None:
                    corriente.append(p)
                continue
            if ww not in ANCHO_CAUCE or t.get("tunnel") in ("yes", "culvert", "flume") or t.get("location") == "underground":
                continue
            ancho = _num(t.get("width")) or ANCHO_CAUCE[ww]
            ln = LineString(c)
            if not ln.intersects(bb.buffer(ancho)):
                continue
            if es_intermitente(t) and season == "seca":
                self.secos.append((c, max(ancho, 1.5)))
                continue
            corriente.append(ln.buffer(max(ancho, 1.0) / 2, cap_style="flat", join_style="round"))
            n_lineas += 1
        self.mar = mar_desde_costa([loc(c) for c in osm.get("coast", [])], rect)
        if self.mar is not None:
            quieta.append(self.mar)

        q, cr = _union_clip(quieta, bb), _union_clip(corriente, bb)
        if cr is not None and q is not None:
            cr = cr.difference(q)          # un río que entra en un embalse: manda el nivel del embalse
        partes = [g for g in (q, cr) if g is not None and not g.is_empty]
        osm_agua = unary_union(partes) if partes else None

        # ---------------------------------------------------------------- agua del satélite
        self.ndwi = ndwi.sample(proj, self.PX, self.PY, order=1) if ndwi is not None else None
        wc = lc.sample(proj, self.PX, self.PY, order=0).astype(np.int32) if lc is not None else None
        sat_extra = None
        self.sat_agua = None
        if self.ndwi is not None:
            m = self.ndwi > NDWI_AGUA
            if wc is not None:
                # en zona construida, un NDWI alto suele ser sombra de edificios altos o vidrio
                # oscuro (en el centro de San Francisco salían charcas): ahí solo vale si
                # WorldCover también ve agua; la nieve también da NDWI positivo
                m &= (wc != C.WC_BUILT) & (wc != C.WC_SNOW)
                m |= (self.ndwi > NDWI_AGUA_WC) & (wc == C.WC_WATER)
            m = ndimage.binary_opening(m, np.ones((3, 3)))
            lab, n = ndimage.label(m)
            if n:
                areas = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1)) * self.res ** 2
                keep = np.isin(lab, np.nonzero(areas >= AREA_MIN_SAT)[0] + 1)
                self.sat_agua = keep
                polys = [shape(g) for g, v in features.shapes(keep.astype(np.uint8), mask=keep, transform=self.tr) if v]
                if polys:
                    sat = unary_union(polys).buffer(0).simplify(self.res * 0.75)
                    extra = sat.difference(osm_agua.buffer(self.res * 3)) if osm_agua is not None else sat
                    sat_extra = []
                    for g in getattr(extra, "geoms", [extra]):
                        if g.geom_type != "Polygon" or g.area < AREA_MIN_SAT:
                            continue
                        compact = 4 * math.pi * g.area / max(g.length ** 2, 1e-6)
                        (quieta if compact > 0.15 else corriente).append(g)
                        sat_extra.append(g)
        q, cr = _union_clip(quieta, bb), _union_clip(corriente, bb)
        if cr is not None and q is not None:
            cr = cr.difference(q)
        self.quieta = [g for g in _polys(q)]
        self.corriente = [g for g in _polys(cr)]
        todo = self.quieta + self.corriente
        self.agua = unary_union(todo) if todo else None
        self.sat_extra = unary_union(sat_extra) if sat_extra else None

        # ---------------------------------------------------------------- mapa de clases
        cls = np.zeros((self.H, self.W), np.uint8)
        src = np.zeros((self.H, self.W), np.uint8)
        if wc is not None:
            for k, v in WC_A_CLASE.items():
                cls[wc == k] = v
            src[cls > 0] = 2
        for k in _ORDEN:
            self._pintar(cls, src, land[k], k, 1)
        if self.sat_agua is not None:
            cls[self.sat_agua] = AGUA
            src[self.sat_agua] = 3
        self._pintar(cls, src, secos_poly, CAUCE_SECO, 1)
        self._pintar(cls, src, [LineString(c).buffer(w / 2) for c, w in self.secos], CAUCE_SECO, 1)
        self._pintar(cls, src, [g for g in _polys(osm_agua)], AGUA, 1)
        if sat_extra:
            self._pintar(cls, src, sat_extra, AGUA, 3)
        if self.mar is not None:
            self._pintar(cls, src, [self.mar], MAR, 1)
        self.cls, self.src = cls, src
        self._osm_agua = osm_agua
        self.stats = self._estadisticas(osm_agua)
        a = self.stats["agua_m2"]
        log(f"  cobertura: agua {a['total'] / 1e4:.2f} ha (OSM áreas/ríos {a['osm'] / 1e4:.2f}, "
            f"{n_lineas} cauces dibujados como línea, mar {a['mar'] / 1e4:.2f}, solo satélite {a['solo_satelite'] / 1e4:.2f})"
            + (f", {len(self.secos)} cauces secos" if self.secos else ""))

    def _pintar(self, cls, src, geoms, k, fuente):
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return
        m = features.rasterize(((g, 1) for g in geoms), out_shape=cls.shape, transform=self.tr, fill=0,
                               all_touched=k in (AGUA, CAUCE_SECO)).astype(bool)
        cls[m] = k
        src[m] = fuente

    def _estadisticas(self, osm_agua):
        px = self.res ** 2
        tot = self.cls.size
        cl = {CLASES[k][0]: round(float((self.cls == k).sum()) / tot * 100, 2) for k in CLASES if (self.cls == k).any()}
        fu = {FUENTES[k]: round(float((self.src == k).sum()) / tot * 100, 1) for k in FUENTES if (self.src == k).any()}
        agua = {
            "total": float(sum(g.area for g in self.quieta + self.corriente)),
            "osm": float(osm_agua.area) if osm_agua is not None else 0.0,
            "mar": float(self.mar.intersection(box(*self.rect)).area) if self.mar is not None else 0.0,
            "solo_satelite": float(self.sat_extra.area) if self.sat_extra is not None else 0.0,
        }
        if self.ndwi is not None and osm_agua is not None and not osm_agua.is_empty:
            m = features.rasterize(((g, 1) for g in _polys(osm_agua)), out_shape=self.cls.shape,
                                   transform=self.tr, fill=0).astype(bool)
            # agua de OSM en la que el satélite no ve agua (embalse bajo, río tapado por árboles...),
            # sin contar los primeros 8 m desde la orilla: con píxeles de 10 m la orilla y los ríos
            # estrechos siempre salen mezclados
            m = ndimage.binary_erosion(m, iterations=max(1, int(round(ORILLA_SAT / self.res))))
            agua["osm_no_visible"] = float((m & (self.ndwi < 0.0)).sum() * px)
        return {"clases_pct": cl, "fuentes_pct": fu, "agua_m2": {k: round(v) for k, v in agua.items()}}

    # ------------------------------------------------------------------ diagnóstico visual
    def panel(self, imagen=None, titulo=""):
        """Imagen de diagnóstico: foto aérea | qué es cada sitio | agua según OSM y según el satélite."""
        W, H = self.W, self.H
        esc = min(1.0, 700.0 / max(W, H))
        w, h = max(1, int(W * esc)), max(1, int(H * esc))
        if imagen is not None:
            rgb = imagen.sample(self.proj, self.PX, self.PY, order=1)
            foto = Image.fromarray(np.clip(rgb[..., :3], 0, 255).astype(np.uint8))
        else:
            foto = Image.new("RGB", (W, H), (60, 60, 60))
        pal = np.zeros((256, 3), np.uint8)
        pal[0] = (70, 70, 70)
        for k, (_, c) in CLASES.items():
            pal[k] = c
        mapa = Image.fromarray(pal[self.cls])
        # agua: azul = OSM y satélite coinciden; rojo = OSM dice agua y el satélite no la ve;
        # amarillo = el satélite ve agua que OSM no tiene
        ag = np.full((H, W, 3), 35, np.uint8)
        osm_m = features.rasterize(((g, 1) for g in _polys(self._osm_agua)), out_shape=(H, W), transform=self.tr,
                                   fill=0, all_touched=True).astype(bool) if self._osm_agua is not None else np.zeros((H, W), bool)
        sat_m = (self.ndwi > NDWI_AGUA_WC) if self.ndwi is not None else np.zeros((H, W), bool)
        if self.ndwi is not None:
            ag[osm_m] = (40, 120, 230)
            nucleo = ndimage.binary_erosion(osm_m, iterations=max(1, int(round(ORILLA_SAT / self.res))))
            ag[nucleo & ~sat_m] = (225, 60, 50)
            ag[~osm_m & (self.sat_agua if self.sat_agua is not None else sat_m)] = (240, 210, 40)
        else:
            ag[osm_m] = (40, 120, 230)
        seco = self.cls == CAUCE_SECO
        ag[seco] = (205, 172, 110)
        agua_img = Image.fromarray(ag)
        cols = [foto.resize((w, h)), mapa.resize((w, h), Image.NEAREST), agua_img.resize((w, h), Image.NEAREST)]
        leyenda = [(CLASES[k][1], f"{CLASES[k][0]} {self.stats['clases_pct'].get(CLASES[k][0], 0):.1f}%")
                   for k in CLASES if CLASES[k][0] in self.stats["clases_pct"]]
        ley_agua = [((40, 120, 230), "agua: OSM y satélite"), ((225, 60, 50), "OSM sin agua visible"),
                    ((240, 210, 40), "agua solo en satélite"), ((205, 172, 110), "cauce seco")]
        top, filas = 22, max(len(leyenda), len(ley_agua))
        out = Image.new("RGB", (w * 3 + 8, top + h + 18 + 15 * filas + 30), (18, 18, 18))
        d = ImageDraw.Draw(out)
        d.font = fuente(13)
        d.text((6, 5), titulo or "Cobertura del suelo", fill=(255, 230, 80))
        for i, (im, t) in enumerate(zip(cols, ("imagen aérea", "qué es cada sitio", "agua: OSM frente a satélite"))):
            out.paste(im, (i * (w + 4), top))
            d.text((i * (w + 4) + 4, top + h + 3), t, fill=(230, 230, 230))
        y0 = top + h + 20
        for j, (c, t) in enumerate(leyenda):
            d.rectangle([w + 8, y0 + 15 * j, w + 20, y0 + 15 * j + 11], fill=c)
            d.text((w + 26, y0 + 15 * j), t, fill=(220, 220, 220))
        for j, (c, t) in enumerate(ley_agua):
            d.rectangle([2 * w + 12, y0 + 15 * j, 2 * w + 24, y0 + 15 * j + 11], fill=c)
            d.text((2 * w + 30, y0 + 15 * j), t, fill=(220, 220, 220))
        a = self.stats["agua_m2"]
        txt = (f"agua {a['total'] / 1e4:.2f} ha · OSM {a['osm'] / 1e4:.2f} · mar {a['mar'] / 1e4:.2f} · "
               f"solo satélite {a['solo_satelite'] / 1e4:.2f}"
               + (f" · OSM sin agua visible {a['osm_no_visible'] / 1e4:.2f}" if "osm_no_visible" in a else "")
               + "  |  fuentes: " + ", ".join(f"{k} {v:.0f}%" for k, v in self.stats["fuentes_pct"].items()))
        d.text((6, y0 + 15 * filas + 8), txt, fill=(200, 200, 200))
        return out


def _union_clip(geoms, bb):
    if not geoms:
        return None
    g = unary_union(geoms).intersection(bb)
    return None if g.is_empty else g


def _polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
