"""Edificios: huellas de OSM + Microsoft, extruidas al estilo mexicano.

- Azotea plana con pretil (no techos de dos aguas): lo normal en casas de block y concreto.
- Impermeabilizante rojo, blanco o concreto gris en la azotea.
- Tinaco negro en más de la mitad de las casas.
- Fachadas de colores con ventanas con protecciones de herrería.
- Iglesias más altas, con campanario.
La altura sale de `height` o `building:levels`; si no hay, se estima según el tipo y el
tamaño (casas de 1-2 niveles, comercios y bodegas más altos).
"""
import math
import zlib

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import config as C
from .regions import active
from . import textures
from .roads import triangulate_polygon, wall_strip, box_mesh, _num


def _h(v):
    return zlib.crc32(str(v).encode()) / 0xFFFFFFFF


def collect(osm, ms_polys, proj, area_rect, log=print):
    """Devuelve lista de dict(poly, tags, height) en coordenadas locales."""
    xmin, ymin, xmax, ymax = area_rect
    clip = shapely.geometry.box(xmin + 1, ymin + 1, xmax - 1, ymax - 1)
    out = []

    def to_poly(outer, inners=()):
        ox, oy = proj.to_local([c[0] for c in outer], [c[1] for c in outer])
        holes = []
        for r in inners:
            hx, hy = proj.to_local([c[0] for c in r], [c[1] for c in r])
            holes.append(list(zip(hx, hy)))
        try:
            p = Polygon(list(zip(ox, oy)), holes).buffer(0)
        except Exception:  # noqa: BLE001
            return None
        return p if p.is_valid and not p.is_empty else None

    osm_polys = []
    for a in osm["areas"]:
        t = a["tags"]
        if "building" not in t or t.get("building") in ("no", "roof", "carport"):
            continue
        p = to_poly(a["outer"], a["inners"])
        if p is None:
            continue
        for g in getattr(p, "geoms", [p]):
            if g.area >= 6 and clip.contains(g.centroid):
                out.append({"poly": g, "tags": t, "height": None, "src": "osm"})
                osm_polys.append(g)
    out = _dedupe(out)
    osm_polys = [b["poly"] for b in out]
    n_osm = len(out)
    osm_union = unary_union(osm_polys) if osm_polys else None
    tree = shapely.STRtree(osm_polys) if osm_polys else None
    n_ms = 0
    for b in ms_polys or []:
        p = to_poly(b["outer"])
        if p is None or p.area < 12 or not clip.contains(p.centroid):
            continue
        if tree is not None and len(tree.query(p, predicate="intersects")):
            continue
        out.append({"poly": p, "tags": {"building": "yes"}, "height": b["height"], "src": "ms"})
        n_ms += 1
    log(f"  edificios: {n_osm} de OSM + {n_ms} de Microsoft = {len(out)}")
    return out


def _dedupe(items):
    """Quita edificios repetidos en OSM (misma huella como vía y como relación) y contornos de
    manzana que contienen las casas ya dibujadas: sus muros coinciden con los de otro edificio y
    las dos fachadas parpadean una encima de la otra."""
    if len(items) < 2:
        return items
    polys = [b["poly"] for b in items]
    tree = shapely.STRtree(polys)
    drop = set()
    covered = [0.0] * len(items)
    for i, p in enumerate(polys):
        for j in tree.query(p, predicate="intersects"):
            j = int(j)
            if j <= i or j in drop or i in drop:
                continue
            q = polys[j]
            inter = p.intersection(q).area
            small, big = (i, j) if p.area <= q.area else (j, i)
            a_s, a_b = polys[small].area, polys[big].area
            if inter < 0.5 * a_s:
                continue
            if a_b < 1.3 * a_s:
                # duplicado: se queda el que tiene más etiquetas
                drop.add(small if len(items[small]["tags"]) < len(items[big]["tags"]) else big)
            else:
                covered[big] += inter
    for k, b in enumerate(items):
        # contorno de manzana cubierto en más del 60 % por casas: sobra el contorno
        if covered[k] > 0.6 * polys[k].area:
            drop.add(k)
    kept = [b for k, b in enumerate(items) if k not in drop]
    # casas que quedaron dentro de un contorno que se conserva: fuera las casas
    if len(kept) != len(items):
        polys = [b["poly"] for b in kept]
        tree = shapely.STRtree(polys)
        inside = set()
        for i, p in enumerate(polys):
            for j in tree.query(p, predicate="intersects"):
                j = int(j)
                if j != i and polys[j].area > 1.3 * p.area and p.intersection(polys[j]).area > 0.8 * p.area:
                    inside.add(i)
        kept = [b for k, b in enumerate(kept) if k not in inside]
    return kept


def drop_on_roads(out, road_union):
    """Quita o recorta edificios que invaden la calzada (huellas desalineadas)."""
    if road_union is None:
        return out
    # Siempre se recorta: antes solo se hacía si el edificio invadía más del 15 % de su área, y
    # un edificio grande que cruza una calle estrecha (arcos, pasadizos, huellas desalineadas) se
    # quedaba entero y convertía la calle en un callejón sin salida.
    rt = road_union.buffer(0.3)
    shapely.prepare(rt)
    keep = []
    for b in out:
        if not shapely.intersects(rt, b["poly"]):
            keep.append(b)
            continue
        inter = b["poly"].intersection(rt).area
        if inter < 0.05:
            keep.append(b)
            continue
        if inter > 0.6 * b["poly"].area:
            continue
        rest = b["poly"].difference(rt).buffer(0)
        parts = [g for g in getattr(rest, "geoms", [rest]) if g.geom_type == "Polygon" and g.area >= 8]
        for k, g in enumerate(parts):
            nb = dict(b)
            nb["poly"] = g
            keep.append(nb)
    return keep


NAVE_KINDS = {"industrial", "warehouse", "barn", "farm_auxiliary", "factory", "manufacture", "hangar",
              "storage_tank", "retail", "supermarket", "commercial", "greenhouse", "cowshed", "stable"}


def height_of(b, urban_dense):
    t = b["tags"]
    hh = _num(t.get("height"))
    if hh:
        return max(2.6, hh)
    lv = _num(t.get("building:levels"))
    if lv:
        return lv * C.LEVEL_HEIGHT + 0.3
    if b.get("height"):
        return max(2.6, float(b["height"]))
    kind = t.get("building", "yes")
    area = b["poly"].area
    r = _h(b["poly"].centroid.wkt)
    if kind in ("church", "cathedral", "chapel"):
        return 12.0 + 6 * r
    if kind in ("industrial", "warehouse", "retail", "supermarket", "commercial", "factory", "manufacture", "hangar"):
        return 6.0 + 3 * r
    if kind in ("barn", "farm_auxiliary", "cowshed", "stable"):
        return 4.5 + 2 * r
    if kind in ("shed", "garage", "hut", "kiosk"):
        return 2.6
    if area > 600:
        return 7.0 + 4 * r
    opts = active().levels_default
    lv = opts[min(len(opts) - 1, int(r * len(opts)))]
    return lv * C.LEVEL_HEIGHT + 0.3


def build(scene, buildings, terrain, log=print):
    n_tinaco = 0
    for i, b in enumerate(buildings):
        poly = b["poly"].simplify(0.25)
        if poly.is_empty or poly.geom_type != "Polygon" or poly.area < 6:
            continue
        poly = shapely.geometry.polygon.orient(poly, 1.0)
        ext = np.asarray(poly.exterior.coords)[:, :2]
        gz = terrain.height(ext[:, 0], ext[:, 1])
        base = float(gz.min()) - 0.4
        # en pendiente, la planta baja está a media ladera (no en el punto más alto): con el máximo,
        # cuesta abajo la fachada se alargaba y el edificio parecía tener plantas de más
        ground = float(np.percentile(gz, 35))
        h = height_of(b, True)
        top = ground + h
        r = _h((i, poly.centroid.wkt))
        kind = b["tags"].get("building", "yes")
        church = kind in ("church", "cathedral", "chapel") or b["tags"].get("amenity") == "place_of_worship"
        var = ("", "_d", "_l")[int(_h((i, "v")) * 3) % 3]   # variante más oscura / más clara
        R = active()
        nfc = len(R.facade_colors)
        mat = "iglesia" if church else f"fachada_{int(r * nfc) % nfc}{var}"
        # naves (industria, almacén, granero, comercio grande): chapa grecada y cubierta baja de
        # chapa, no fachada de vivienda
        nave = not church and (kind in NAVE_KINDS or (kind == "yes" and poly.area > 900))
        if nave:
            mat = f"nave_{int(r * len(textures.NAVE_COLORS)) % len(textures.NAVE_COLORS)}"
        pitched = R.roof == "pitched" and not church and not nave and kind not in ("retail", "supermarket",
                                                                                   "commercial") and poly.area < 1500
        # lo que OSM sabe de este edificio manda sobre lo genérico (color de fachada y de techo,
        # forma del techo)
        tg = b["tags"]
        shape = tg.get("roof:shape")
        if shape == "flat":
            pitched = False
        elif shape in ("gabled", "hipped", "pyramidal", "half-hipped", "mansard", "gambrel") and not church:
            pitched = True
        fc = textures.parse_colour(tg.get("building:colour"))
        if fc and not church and not nave:
            mat = textures.facade_material(scene, fc, "mx" if R.roof == "flat" else "eu")
        rc = textures.parse_colour(tg.get("roof:colour"))
        roof_mat = textures.roof_material(scene, rc, pitched) if rc else None
        # muros (exterior hacia afuera) + pretil
        for ring in [poly.exterior] + list(poly.interiors):
            pts = np.asarray(ring.coords)[:, :2]
            bot = np.column_stack([pts, np.full(len(pts), base)])
            p, uv, f = wall_strip(bot, top + C.PARAPET - base, u_scale=4.0, v_scale=-3.0, both=False)
            # alinear la textura con el nivel del piso
            uv[:, 1] = (ground - p[:, 2]) / 3.0
            scene.add("edificios", mat, p, uv, f, phys="WALL")
            if pitched or (nave and shape != "flat"):
                continue
            # cara interior del pretil
            inner = np.column_stack([pts[::-1], np.full(len(pts), top)])
            p, uv, f = wall_strip(inner, C.PARAPET, u_scale=4.0, v_scale=3.0, both=False)
            scene.add("edificios", mat, p, uv, f)
        if nave and shape != "flat" and _hip_roof(scene, poly, top, roof_mat or "chapa", pitch=0.16, eave=0.25):
            continue
        if pitched and _hip_roof(scene, poly, top + C.PARAPET,
                                 roof_mat or f"tejado_{int(_h((i, 'r')) * len(R.roof_colors)) % len(R.roof_colors)}"):
            continue
        # azotea
        verts, faces = triangulate_polygon(poly, 50.0)
        if len(faces):
            roofk = "rojo" if r > 0.62 else ("blanco" if r > 0.5 else "concreto")
            pos = np.column_stack([verts, np.full(len(verts), top)])
            scene.add("edificios", roof_mat or f"azotea_{roofk}", pos, verts / 4.0, faces)
        # tinaco
        if R.tinaco and not church and kind in ("yes", "house", "residential", "detached", "apartments") \
                and 30 < poly.area < 500 and _h((i, "t")) < C.TINACO_PROB:
            n_tinaco += 1
            ip = poly.buffer(-1.2)
            if not ip.is_empty:
                rp = ip.representative_point()
                _cylinder(scene, rp.x, rp.y, top, 0.55, 1.35, "tinaco")
                p, uv, f = box_mesh((rp.x, rp.y, top), (1.3, 1.3, 0.25))
                scene.add("edificios", "azotea_concreto", p, uv, f)
        if church:
            # campanario en el vértice más lejano del centro
            c = np.array(poly.centroid.coords[0])
            far = ext[np.argmax(np.linalg.norm(ext - c, axis=1))]
            tw = far + (c - far) / max(np.linalg.norm(c - far), 1e-6) * 3.0
            p, uv, f = box_mesh((tw[0], tw[1], base), (4.5, 4.5, h + 9 - (base - ground)))
            uv = uv * np.array([1.1, 4.0])
            scene.add("edificios", "iglesia", p, uv, f, phys="WALL")
            _cylinder(scene, tw[0], tw[1], ground + h + 9, 1.8, 3.0, "azotea_blanco", seg=12, cone=True)
    log(f"  edificios construidos: {len(buildings)}, tinacos: {n_tinaco}")


def _hip_roof(scene, poly, z0, mat, pitch=0.58, eave=0.35):
    """Tejado a cuatro aguas aproximado: el contorno (con alero) sube hasta el mismo contorno
    encogido hacia dentro; arriba queda una cumbrera o un remate plano pequeño.

    No es un esqueleto recto exacto, pero en manzanas de casco antiguo (contornos casi
    rectangulares) da tejados creíbles. Devuelve False si el polígono es demasiado raro.
    """
    mrr = poly.minimum_rotated_rectangle
    xy = np.asarray(mrr.exterior.coords)[:4]
    sides = np.linalg.norm(np.diff(np.vstack([xy, xy[:1]]), axis=0), axis=1)
    half = float(sides.min()) / 2.0
    if half < 1.5:
        return False
    d = min(half * 0.92, 7.0)
    inner = None
    for _ in range(5):
        g = poly.buffer(-d, join_style=2, mitre_limit=3.0)
        if not g.is_empty and g.geom_type == "Polygon" and g.area > 0.05:
            inner = shapely.geometry.polygon.orient(g, 1.0)
            break
        d *= 0.8
    if inner is None:
        return False
    outer = poly.buffer(eave, join_style=2, mitre_limit=3.0)
    if outer.geom_type != "Polygon":
        return False
    outer = shapely.geometry.polygon.orient(outer, 1.0)
    h = (d + eave) * pitch
    oring = np.asarray(outer.exterior.coords)[:-1]
    iring = inner.exterior
    q = np.array([iring.interpolate(iring.project(shapely.Point(p))).coords[0] for p in oring])
    n = len(oring)
    pos, uv, faces = [], [], []
    for k in range(n):
        a, b = oring[k], oring[(k + 1) % n]
        qa, qb = q[k], q[(k + 1) % n]
        base = len(pos)
        pos += [(a[0], a[1], z0 - 0.1), (b[0], b[1], z0 - 0.1), (qb[0], qb[1], z0 + h), (qa[0], qa[1], z0 + h)]
        L = float(np.hypot(*(b - a)))
        slope = float(np.hypot(np.hypot(*(qa - a)), h))
        uv += [(0, 0), (L / 2, 0), (L / 2, slope / 2), (0, slope / 2)]
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    pos = np.array(pos, float)
    faces = np.array(faces)
    tri = pos[faces]
    nz = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2]
    faces[nz < 0] = faces[nz < 0][:, ::-1]
    scene.add("edificios", mat, pos, np.array(uv, float), faces)
    verts, f2 = triangulate_polygon(inner, 50.0)
    if len(f2):
        scene.add("edificios", mat, np.column_stack([verts, np.full(len(verts), z0 + h)]), verts / 2.0, f2)
    # tapa bajo el alero, mirando hacia abajo (que no se vea el interior desde la calle)
    verts, f3 = triangulate_polygon(outer, 50.0)
    if len(f3):
        scene.add("edificios", mat, np.column_stack([verts, np.full(len(verts), z0 - 0.1)]), verts / 2.0, f3[:, ::-1])
    return True


def _cylinder(scene, x, y, z, r, h, mat, seg=14, cone=False):
    a = np.linspace(0, 2 * math.pi, seg + 1)
    ring = np.column_stack([x + r * np.cos(a), y + r * np.sin(a)])
    top_r = 0.05 if cone else 1.0
    pos, uv, faces = [], [], []
    for k in range(seg + 1):
        pos.append((ring[k, 0], ring[k, 1], z))
        pos.append((x + (ring[k, 0] - x) * top_r, y + (ring[k, 1] - y) * top_r, z + h))
        uv += [(k / seg, 1), (k / seg, 0)]
    for k in range(seg):
        i = 2 * k
        faces += [(i, i + 2, i + 3), (i, i + 3, i + 1)]
    c = len(pos)
    pos.append((x, y, z + h))
    uv.append((0.5, 0.5))
    for k in range(seg):
        faces.append((2 * k + 1, 2 * k + 3, c))
    scene.add("edificios", mat, np.array(pos), np.array(uv), np.array(faces))
