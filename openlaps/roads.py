"""Red de carreteras: del grafo de OSM a superficies 3D conducibles.

Pasos del algoritmo (ver README para la explicación larga):
 1. Grafo: se parten las vías de OSM en los nodos compartidos (cruces) y se vuelven a unir
    las aristas que solo se tocan de dos en dos y tienen los mismos atributos.
 2. Planta: cada vértice de OSM se redondea con un arco de radio mínimo según la clase de vía
    y se remuestrea cada 1.5 m.
 3. Alzado: se muestrea el terreno, se filtra con mediana + gaussiana (sigma según clase)
    y se fija la altura de cada cruce para que todas las calles que llegan coincidan.
    Puentes y túneles ignoran el terreno: van en línea recta entre sus extremos.
 4. Peralte: con la curvatura y la velocidad de proyecto, e = v^2 / (127 R), máximo 8 %.
 5. Cruces: cada calle se recorta donde su orilla deja de chocar con la de su vecina y el
    hueco se rellena con el polígono convexo de los extremos recortados.
 6. Rayas (NOM-034): amarilla al centro, blancas en orilla y carriles; continua donde la
    distancia de visibilidad (en planta y en alzado) no alcanza para rebasar.
 7. Topes, pasos peatonales, banquetas con guarnición, defensas metálicas donde hay
    terraplén alto, fantasmas (indicadores de alineamiento), puentes y túneles.
"""
import heapq
import math
import re
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.spatial import cKDTree
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union
import mapbox_earcut as earcut

from . import config as C
from . import regions

DS = 1.5  # paso de remuestreo (m)
# Altura de las calcomanías sobre la calzada (m). El búfer de profundidad de AC pierde
# precisión con la distancia: con menos de ~2 cm las rayas parpadean (z-fighting) a 50-100 m.
# Orden de abajo hacia arriba: calzada < desgaste (DZ_DESGASTE) < rayas y pasos (DZ_RAYA).
DZ_DESGASTE = 0.018
DZ_RAYA = 0.032
# metros que cubre una repetición de cada textura de calzada
TEX_SCALE = {"asfalto": 3.0, "concreto": 3.0, "empedrado": 2.5, "adoquin": 2.0, "terraceria": 4.0}


# --------------------------------------------------------------------------------------
# utilidades geométricas
# --------------------------------------------------------------------------------------
def _num(v, default=None):
    if v is None:
        return default
    m = re.match(r"\s*([\d.]+)", str(v))
    try:
        return float(m.group(1)) if m else default
    except ValueError:
        return default


def resample(pts, step):
    pts = np.asarray(pts, float)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    keep = np.concatenate([[True], seg > 1e-6])
    pts = pts[keep]
    if len(pts) < 2:
        return pts, np.zeros(len(pts))
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    n = max(2, int(math.ceil(s[-1] / step)) + 1)
    si = np.linspace(0, s[-1], n)
    return np.column_stack([np.interp(si, s, pts[:, 0]), np.interp(si, s, pts[:, 1])]), si


def fillet(pts, radius):
    """Redondea cada vértice interior con un arco de radio `radius` (limitado por los tramos)."""
    pts = np.asarray(pts, float)
    if len(pts) < 3 or radius <= 0:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        u, v = a - b, c - b
        lu, lv = np.linalg.norm(u), np.linalg.norm(v)
        if lu < 1e-6 or lv < 1e-6:
            continue
        u, v = u / lu, v / lv
        cosang = float(np.clip(np.dot(u, v), -1, 1))
        ang = math.acos(cosang)          # ángulo interior en el vértice
        if ang > math.radians(176):      # casi recto: no hace falta arco
            out.append(b)
            continue
        half = ang / 2
        t = radius / math.tan(half)
        tmax = 0.5 * min(lu, lv)
        if t > tmax:
            t = tmax
        r = t * math.tan(half)
        p1, p2 = b + u * t, b + v * t
        bis = u + v
        bis /= np.linalg.norm(bis)
        center = b + bis * (r / math.sin(half))
        a1 = math.atan2(*(p1 - center)[::-1])
        a2 = math.atan2(*(p2 - center)[::-1])
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        nseg = max(2, int(abs(da) * r / 1.0))
        for k in range(nseg + 1):
            aa = a1 + da * k / nseg
            out.append(center + r * np.array([math.cos(aa), math.sin(aa)]))
    out.append(pts[-1])
    return np.array(out)


def tangents(P):
    T = np.gradient(P, axis=0)
    ln = np.linalg.norm(T, axis=1, keepdims=True)
    ln[ln < 1e-9] = 1
    return T / ln


def convex_hull_2d(points):
    pts = sorted(set(map(tuple, np.round(points, 4))))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def triangulate_polygon(poly, densify=2.0):
    """Triangula un polígono shapely (con huecos) con earcut, densificando las orillas."""
    poly = shapely.segmentize(shapely.geometry.polygon.orient(poly, 1.0), densify)
    rings = [np.asarray(poly.exterior.coords)[:-1, :2]] + [np.asarray(r.coords)[:-1, :2] for r in poly.interiors]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return np.zeros((0, 2)), np.zeros((0, 3), int)
    verts = np.vstack(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = earcut.triangulate_float64(verts, ends)
    return verts, np.asarray(idx, np.int64).reshape(-1, 3)


def iter_polys(g):
    if g is None or g.is_empty:
        return
    if isinstance(g, Polygon):
        yield g
    elif hasattr(g, "geoms"):
        for x in g.geoms:
            yield from iter_polys(x)


# --------------------------------------------------------------------------------------
# estructuras
# --------------------------------------------------------------------------------------
@dataclass
class Edge:
    id: int
    nodes: list            # ids de nodos OSM
    tags: dict
    cls: str
    a: int = 0             # nodo inicial (id)
    b: int = 0             # nodo final (id)
    # atributos derivados
    lanes: int = 2
    lane_w: float = 3.0
    shoulder: float = 0.0
    hw: float = 3.5        # media anchura de la calzada (incluye acotamientos)
    oneway: bool = False
    mat: str = "asfalto"
    phys: str = "ROAD"
    bridge: bool = False
    tunnel: bool = False
    layer: int = 0
    speed: float = 40
    sigma: float = 12
    prio: int = 1
    urban: bool = False
    # geometría muestreada
    s: np.ndarray = None
    P: np.ndarray = None
    T: np.ndarray = None
    N: np.ndarray = None
    Z: np.ndarray = None
    B: np.ndarray = None   # peralte con signo (pendiente transversal)
    s0: float = 0.0        # recorte en el extremo a
    s1: float = 0.0        # recorte en el extremo b (posición s)
    runs: list = field(default_factory=list)   # tramos dentro del área (s_ini, s_fin)
    sidewalk: bool = False

    @property
    def length(self):
        return float(self.s[-1])

    def at(self, s, o=0.0):
        """Punto 3D de la superficie en la abscisa s y desplazamiento lateral o (izquierda +)."""
        s = np.asarray(s, float)
        px = np.interp(s, self.s, self.P[:, 0])
        py = np.interp(s, self.s, self.P[:, 1])
        nx = np.interp(s, self.s, self.N[:, 0])
        ny = np.interp(s, self.s, self.N[:, 1])
        z = np.interp(s, self.s, self.Z)
        b = np.interp(s, self.s, self.B)
        o = np.asarray(o, float)
        return np.stack(np.broadcast_arrays(px + nx * o, py + ny * o, z - b * o), -1)

    def frame(self, s):
        tx = np.interp(s, self.s, self.T[:, 0])
        ty = np.interp(s, self.s, self.T[:, 1])
        return np.array([tx, ty])

    def key_attrs(self):
        return (self.cls, round(self.hw, 2), self.mat, self.bridge, self.tunnel, self.layer, self.oneway)


class RoadNetwork:
    def __init__(self, osm, proj, dem_z, urban_fn, area_rect, log=print):
        self.proj = proj
        self.dem_z = dem_z            # función (x, y) -> z del terreno sin tallar
        self.urban_fn = urban_fn      # función (x, y) -> bool
        self.rect = area_rect         # (xmin, ymin, xmax, ymax)
        self.log = log
        self.node_xy = {}
        self.node_tags = {}
        self.edges = []
        self.node_z = {}
        self.junctions = {}           # nodo -> dict con polígono del cruce
        self._build_graph(osm)
        self._geometry()
        self._elevation()
        self._merge_forks()
        self._superelevation()
        self._trims()
        self._runs()
        self._islands()

    # ---------------------------------------------------------------- 1. grafo
    def _build_graph(self, osm):
        ways = [w for w in osm["ways"] if w["tags"].get("highway") in C.DRIVABLE
                and w["tags"].get("access") not in ("private", "no")
                and w["tags"].get("area") != "yes" and len(w["nodes"]) == len(w["coords"])]
        for w in ways:
            x, y = self.proj.to_local([c[0] for c in w["coords"]], [c[1] for c in w["coords"]])
            for nid, xx, yy in zip(w["nodes"], x, y):
                self.node_xy[nid] = (float(xx), float(yy))
        for n in osm["nodes"]:
            self.node_tags[n["id"]] = n["tags"]
            if n["id"] not in self.node_xy:
                pass
        use = {}
        for w in ways:
            for i, nid in enumerate(w["nodes"]):
                use[nid] = use.get(nid, 0) + (1 if 0 < i < len(w["nodes"]) - 1 else 1)
            use[w["nodes"][0]] = use.get(w["nodes"][0], 0) + 1
            use[w["nodes"][-1]] = use.get(w["nodes"][-1], 0) + 1
        raw = []
        for w in ways:
            seq = w["nodes"]
            cur = [seq[0]]
            for nid in seq[1:]:
                cur.append(nid)
                if use.get(nid, 0) >= 2 and nid != seq[-1]:
                    raw.append((cur, w["tags"]))
                    cur = [nid]
            if len(cur) >= 2:
                raw.append((cur, w["tags"]))
        edges = []
        for i, (nodes, tags) in enumerate(raw):
            e = Edge(id=i, nodes=list(nodes), tags=tags, cls=tags["highway"])
            self._attrs(e)
            e.a, e.b = nodes[0], nodes[-1]
            edges.append(e)
        self.edges = self._merge_deg2(edges)
        self.log(f"  red: {len(ways)} vías OSM -> {len(self.edges)} aristas")

    def _attrs(self, e):
        t = e.tags
        lane_w, def_lanes, shoulder, speed, sigma, prio = C.ROAD_CLASS.get(e.cls, C.ROAD_CLASS["road"])
        e.oneway = t.get("oneway") in ("yes", "1", "true", "-1") or t.get("junction") == "roundabout" \
            or e.cls in ("motorway", "motorway_link")
        lanes = _num(t.get("lanes"))
        if lanes is None:
            lanes = def_lanes if not e.oneway else (2 if e.cls in ("motorway", "trunk") else 1)
        lanes = int(max(1, min(lanes, 8)))
        e.lanes, e.lane_w, e.shoulder = lanes, lane_w, shoulder
        e.speed = _num(t.get("maxspeed"), speed) or speed
        e.sigma, e.prio = sigma, prio
        surf = t.get("surface")
        if surf in C.SURFACE_MAP:
            e.mat = C.SURFACE_MAP[surf]
        elif e.cls == "track":
            e.mat = "terraceria"
        else:
            e.mat = "asfalto"
        e.phys = C.PHYS_KEY[e.mat]
        e.bridge = t.get("bridge") not in (None, "no")
        e.tunnel = t.get("tunnel") not in (None, "no", "building_passage")
        e.layer = int(_num(t.get("layer"), 0) or 0)
        width = _num(t.get("width"))
        carriage = lanes * lane_w
        if e.oneway and lanes == 1 and e.cls in ("residential", "unclassified", "tertiary", "living_street"):
            carriage = lane_w * 1.7   # calle de un sentido con cochera / estacionamiento
        e.hw = (width if width and 2.5 < width < 40 else carriage) / 2.0

    def _merge_deg2(self, edges):
        adj = {}
        for e in edges:
            adj.setdefault(e.a, []).append(e)
            adj.setdefault(e.b, []).append(e)
        alive = {e.id: e for e in edges}
        for nid in list(adj.keys()):
            inc = [e for e in adj[nid] if e.id in alive]
            if len(inc) != 2:
                continue
            e1, e2 = inc
            if e1 is e2 or e1.key_attrs() != e2.key_attrs():
                continue
            n1 = e1.nodes if e1.b == nid else e1.nodes[::-1]
            n2 = e2.nodes if e2.a == nid else e2.nodes[::-1]
            if e1.oneway and (e1.b != nid or e2.a != nid):
                continue  # no unir sentidos únicos opuestos
            e1.nodes = n1 + n2[1:]
            e1.a, e1.b = e1.nodes[0], e1.nodes[-1]
            if e1.a == e1.b:
                e1.nodes = n1 + n2[1:]
            del alive[e2.id]
            for n in (e2.a, e2.b):
                lst = adj.get(n, [])
                adj[n] = [e1 if x is e2 else x for x in lst]
        out = list(alive.values())
        for i, e in enumerate(out):
            e.id = i
        return out

    def incident(self):
        inc = {}
        for e in self.edges:
            inc.setdefault(e.a, []).append((e, 0))
            inc.setdefault(e.b, []).append((e, 1))
        return inc

    # ---------------------------------------------------------------- 2. planta
    def _geometry(self):
        rmin = {"motorway": 250, "trunk": 120, "primary": 60, "secondary": 40, "tertiary": 25,
                "unclassified": 15, "residential": 6, "living_street": 4, "service": 4, "track": 8}
        for e in self.edges:
            pts = np.array([self.node_xy[n] for n in e.nodes])
            r = rmin.get(e.cls.replace("_link", ""), 10)
            if e.cls.endswith("_link"):
                r = 30
            pts = fillet(pts, r)
            P, s = resample(pts, DS)
            if len(P) >= 5:
                Q = P.copy()
                Q[1:-1] = gaussian_filter1d(P, 1.0, axis=0, mode="nearest")[1:-1]
                P, s = resample(Q, DS)
            e.P, e.s = P, s
            e.T = tangents(P)
            e.N = np.column_stack([-e.T[:, 1], e.T[:, 0]])
            if self.urban_fn is not None:
                e.urban = bool(np.mean(self.urban_fn(P[:, 0], P[:, 1])) > 0.5)
            # acotamiento solo fuera de poblado
            if not e.urban and "width" not in e.tags:
                e.hw += e.shoulder
            e.sidewalk = e.urban and e.cls in C.SIDEWALK_CLASSES and not e.tunnel
        self.edges = [e for e in self.edges if e.P is not None and len(e.P) >= 2 and e.s[-1] > 0.5]

    # ---------------------------------------------------------------- 3. alzado
    def _chains(self):
        """Une los tramos que siguen rectos a través de un cruce (la misma calle) en cadenas.

        En cada nodo se emparejan los extremos más opuestos (ángulo > ~148°), prefiriendo la
        misma calle por nombre. Devuelve listas de (arista, sentido_directo).
        """
        inc = self.incident()
        link = {}
        for nid, lst in inc.items():
            cand = []
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    (e1, a1), (e2, a2) = lst[i], lst[j]
                    if e1 is e2 or e1.bridge or e2.bridge or e1.tunnel or e2.tunnel:
                        continue
                    t1 = e1.T[0] if a1 == 0 else -e1.T[-1]
                    t2 = e2.T[0] if a2 == 0 else -e2.T[-1]
                    d = float(np.dot(t1, t2) / max(np.linalg.norm(t1) * np.linalg.norm(t2), 1e-9))
                    if d > -0.85:
                        continue
                    same = e1.tags.get("name") and e1.tags.get("name") == e2.tags.get("name")
                    cand.append((d - (0.3 if same else 0.0), (e1.id, a1), (e2.id, a2)))
            used = set()
            for _, k1, k2 in sorted(cand):
                if k1 in used or k2 in used:
                    continue
                used |= {k1, k2}
                link[k1], link[k2] = k2, k1
        byid = {e.id: e for e in self.edges}
        seen, chains = set(), []
        for e in self.edges:
            if e.id in seen:
                continue
            # retroceder hasta el inicio de la cadena (o dar la vuelta si es un anillo)
            cur, fwd = e.id, True
            for _ in range(len(self.edges)):
                back = link.get((cur, 0 if fwd else 1))
                if back is None or back[0] == e.id:
                    break
                cur, fwd = back[0], back[1] == 1   # si entra por su extremo final, se recorre al revés
                if cur in seen:
                    break
            chain = []
            for _ in range(len(self.edges)):
                if cur in seen:
                    break
                seen.add(cur)
                chain.append((byid[cur], fwd))
                nxt = link.get((cur, 1 if fwd else 0))
                if nxt is None:
                    break
                cur, fwd = nxt[0], nxt[1] == 0
            chains.append(chain)
        return chains

    def _elevation(self):
        prof = {}
        # perfil suavizado a lo largo de cada cadena completa: sin quiebres en los cruces rectos
        for chain in self._chains():
            pts, owners = [], []
            for e, fwd in chain:
                P = e.P if fwd else e.P[::-1]
                start = 1 if pts else 0
                pts.append(P[start:, :2])
                owners.append((e, fwd, start, len(P)))
            P = np.vstack(pts)
            z = self.dem_z(P[:, 0], P[:, 1])
            s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
            sig_m = max(e.sigma for e, _ in chain)
            k = max(3, int(round(18.0 / DS)) | 1)
            if len(z) > k:
                z = median_filter(z, size=k, mode="nearest")
            zs = gaussian_filter1d(z, sig_m / DS, mode="nearest") if len(z) > 3 else z
            pos = 0
            for e, fwd, start, n in owners:
                if start:
                    seg = np.concatenate([[zs[pos - 1]], zs[pos:pos + n - 1]])
                    pos += n - 1
                else:
                    seg = zs[pos:pos + n]
                    pos += n
                prof[e.id] = seg if fwd else seg[::-1]
        # altura de cada nodo: promedio de los extremos de las calles normales que llegan
        # la vía de mayor jerarquía casi no se mueve (peso x3 por cada nivel de prioridad); las
        # secundarias se adaptan a ella con una transición larga
        acc = {}
        for e in self.edges:
            w = 0.05 if (e.bridge or e.tunnel) else 3.0 ** e.prio
            for nid, val in ((e.a, prof[e.id][0]), (e.b, prof[e.id][-1])):
                a = acc.setdefault(nid, [0.0, 0.0])
                a[0] += val * w
                a[1] += w
        self.node_z = {n: v[0] / v[1] for n, v in acc.items()}
        for e in self.edges:
            za, zb = self.node_z[e.a], self.node_z[e.b]
            t = e.s / max(e.s[-1], 1e-6)
            if e.bridge or e.tunnel:
                L = e.s[-1]
                crest = (min(1.5, 0.01 * L) if e.bridge else 0.0) * 4 * t * (1 - t)
                e.Z = za + (zb - za) * t + crest
            else:
                # corrección hacia la altura del nodo con una campana coseno (pendiente nula en el
                # nodo): no cambia la pendiente de la calle al cruzar, solo sube o baja la zona
                zs = prof[e.id]
                L = e.s[-1]
                D = max(min(12.0, L * 0.5), min(60.0, L * 0.5))
                wa = 0.5 * (1 + np.cos(np.pi * np.clip(e.s / D, 0, 1)))
                wb = 0.5 * (1 + np.cos(np.pi * np.clip((L - e.s) / D, 0, 1)))
                e.Z = zs + (za - zs[0]) * wa + (zb - zs[-1]) * wb

    def _merge_forks(self, max_angle=50.0):
        """Bifurcaciones de ángulo cerrado: mientras las dos calzadas se enciman en planta, comparten
        altura (el promedio), con una transición coseno de 12 m cuando se separan.

        Sin esto, en una bifurcación con pendiente (una calle sube y otra baja) una calzada quedaba
        encima de la otra con un escalón de hasta 2.5 m y el terreno asomando entre ambas.
        """
        cos_max = math.cos(math.radians(max_angle))
        changed = 0
        for nid, lst in self.incident().items():
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    (e1, a1), (e2, a2) = lst[i], lst[j]
                    if e1 is e2 or e1.bridge or e2.bridge or e1.tunnel or e2.tunnel:
                        continue
                    t1 = e1.T[0] if a1 == 0 else -e1.T[-1]
                    t2 = e2.T[0] if a2 == 0 else -e2.T[-1]
                    if float(np.dot(t1, t2) / max(np.linalg.norm(t1) * np.linalg.norm(t2), 1e-9)) < cos_max:
                        continue
                    # distancia desde el nodo (abscisa "saliendo") en cada arista
                    d1 = e1.s if a1 == 0 else e1.s[-1] - e1.s
                    d2 = e2.s if a2 == 0 else e2.s[-1] - e2.s
                    lim = e1.hw + e2.hw + 0.5
                    # ¿hasta dónde se enciman? (centro de e1 contra la polilínea de e2)
                    from scipy.spatial import cKDTree
                    t2tree = cKDTree(e2.P[:, :2])
                    sep, _ = t2tree.query(e1.P[:, :2])
                    over = (sep < lim) & (d1 < min(60.0, e1.length))
                    if not over.any():
                        continue
                    reach = float(d1[over].max())
                    if reach < 1.0:
                        continue
                    trans = 12.0
                    w1 = np.clip(1 - (d1 - reach) / trans, 0, 1)
                    w2 = np.clip(1 - (d2 - reach) / trans, 0, 1)
                    w1 = 0.5 - 0.5 * np.cos(np.pi * w1)
                    w2 = 0.5 - 0.5 * np.cos(np.pi * w2)
                    z1 = e1.Z.copy()
                    z2 = e2.Z.copy()
                    z2_on_1 = np.interp(d1, np.sort(d2), z2[np.argsort(d2)])
                    z1_on_2 = np.interp(d2, np.sort(d1), z1[np.argsort(d1)])
                    e1.Z = z1 + w1 * ((z1 + z2_on_1) / 2 - z1)
                    e2.Z = z2 + w2 * ((z2 + z1_on_2) / 2 - z2)
                    changed += 1
        return changed


    # ---------------------------------------------------------------- glorietas
    def _islands(self):
        """Isletas centrales de las glorietas: el hueco que encierran las vías junction=roundabout
        (OSM las parte en tramos y no dibuja el centro), menos el ancho de la calzada."""
        self.islands, self.islands_union = [], None
        rb = [e for e in self.edges if e.tags.get("junction") in ("roundabout", "circular")]
        if not rb:
            return
        lines = unary_union([shapely.LineString(e.P[:, :2]) for e in rb])
        hw = float(np.median([e.hw for e in rb]))
        for p in getattr(shapely.polygonize([lines]), "geoms", []):
            A, L = p.area, p.length
            if A < 40 or A > 40000 or 4 * math.pi * A / (L * L) < 0.55:
                continue
            isl = p.buffer(-(hw + 0.4))
            if isl.is_empty or isl.area < 6 or isl.geom_type != "Polygon":
                continue
            self.islands.append(isl)
        if self.islands:
            self.islands_union = unary_union(self.islands)
            shapely.prepare(self.islands_union)
        self.log(f"  glorietas: {len(self.islands)} isletas centrales")

    # ---------------------------------------------------------------- 4. peralte
    def _superelevation(self):
        for e in self.edges:
            e.B = np.zeros(len(e.s))
            if e.urban or e.cls not in C.RURAL_CLASSES or e.mat == "terraceria" or len(e.s) < 7:
                continue
            h = np.unwrap(np.arctan2(e.T[:, 1], e.T[:, 0]))
            kappa = gaussian_filter1d(np.gradient(h, e.s), 8.0 / DS, mode="nearest")
            R = 1.0 / np.maximum(np.abs(kappa), 1e-5)
            v = e.speed
            emax = 0.08
            ee = np.clip(v * v / (127.0 * R), 0, emax)
            e.B = gaussian_filter1d(np.sign(kappa) * ee, 12.0 / DS, mode="nearest")
            # se anula en los extremos para casar con los cruces
            ramp = np.clip(np.minimum(e.s, e.s[-1] - e.s) / 20.0, 0, 1)
            e.B *= ramp

    # ---------------------------------------------------------------- 5. cruces
    def _trims(self):
        inc = self.incident()
        for e in self.edges:
            e.s0, e.s1 = 0.0, e.length
        for nid, lst in inc.items():
            if len(lst) < 3 and not (len(lst) == 2 and lst[0][0] is lst[1][0]):
                continue
            dirs, hws = [], []
            for e, end in lst:
                sp = min(6.0, e.length * 0.5)
                p0 = e.at(0 if end == 0 else e.length)[:2]
                p1 = e.at(sp if end == 0 else e.length - sp)[:2]
                d = p1 - p0
                n = np.linalg.norm(d)
                dirs.append(d / n if n > 1e-6 else np.array([1.0, 0]))
                hws.append(e.hw)
            ang = np.array([math.atan2(d[1], d[0]) for d in dirs])
            order = np.argsort(ang)
            m = len(lst)
            trims = np.zeros(m)
            for oi in range(m):
                i = order[oi]
                for nb in (order[(oi + 1) % m], order[(oi - 1) % m]):
                    if nb == i:
                        continue
                    th = abs((ang[nb] - ang[i] + math.pi) % (2 * math.pi) - math.pi)
                    th = max(th, math.radians(12))
                    if th > math.radians(170):
                        need = 0.0
                    else:
                        need = hws[nb] / math.sin(th) + hws[i] / math.tan(th)
                    trims[i] = max(trims[i], need)
            margin = 1.5
            for k, (e, end) in enumerate(lst):
                r = float(np.clip(trims[k] + margin, max(hws) * 0.6, 28.0))
                r = min(r, e.length * (0.5 if e.a != e.b else 0.25) - 0.05)
                if end == 0:
                    e.s0 = max(e.s0, r)
                else:
                    e.s1 = min(e.s1, e.length - r)
            self.junctions[nid] = {"inc": lst}

    def _runs(self):
        xmin, ymin, xmax, ymax = self.rect
        for e in self.edges:
            inside = (e.P[:, 0] > xmin) & (e.P[:, 0] < xmax) & (e.P[:, 1] > ymin) & (e.P[:, 1] < ymax)
            runs, start = [], None
            for i, ok in enumerate(inside):
                if ok and start is None:
                    start = i
                if (not ok or i == len(inside) - 1) and start is not None:
                    end = i if ok else i - 1
                    if end > start:
                        runs.append((float(e.s[start]), float(e.s[end])))
                    start = None
            e.runs = runs

    def node_inside(self, nid):
        x, y = self.node_xy[nid]
        xmin, ymin, xmax, ymax = self.rect
        return xmin + 2 < x < xmax - 2 and ymin + 2 < y < ymax - 2

    def ranges(self, e, s_lo=None, s_hi=None):
        lo = e.s0 if s_lo is None else s_lo
        hi = e.s1 if s_hi is None else s_hi
        out = []
        for a, b in e.runs:
            a2, b2 = max(a, lo), min(b, hi)
            if b2 - a2 > 0.3:
                out.append((a2, b2))
        return out


# --------------------------------------------------------------------------------------
# generación de mallas
# --------------------------------------------------------------------------------------
def _svals(e, a, b, step=DS):
    inner = e.s[(e.s > a) & (e.s < b)]
    sv = np.concatenate([[a], inner, [b]])
    if step < DS:
        sv, _ = resample(np.column_stack([sv, np.zeros_like(sv)]), step)
        sv = sv[:, 0]
    return sv


def strip(e, sv, offsets, dz=0.0, u_scale=4.0, v_scale=4.0, u0=None):
    """Malla rectangular sobre la superficie de la vía: filas = s, columnas = desplazamiento."""
    offsets = np.asarray(offsets, float)
    ns, no = len(sv), len(offsets)
    S, O = np.meshgrid(sv, offsets, indexing="ij")
    pos = e.at(S.ravel(), O.ravel())
    pos[:, 2] += dz
    uu = (O.ravel() if u0 is None else (O.ravel() - u0)) / u_scale
    uv = np.column_stack([uu, S.ravel() / v_scale])
    idx = np.arange(ns * no).reshape(ns, no)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    # orientación: s hacia adelante, o hacia la izquierda -> normal hacia arriba
    faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    # comprobar que la normal apunte arriba; si no, invertir
    if len(faces):
        p = pos[faces[0]]
        if np.cross(p[1] - p[0], p[2] - p[0])[2] < 0:
            faces = faces[:, ::-1]
    return pos, uv, faces


def box_mesh(center, size, yaw=0.0):
    """Caja (cx,cy,cz base) de tamaño (sx,sy,sz)."""
    cx, cy, cz = center
    sx, sy, sz = size
    c, s = math.cos(yaw), math.sin(yaw)
    corners = []
    for dz in (0, sz):
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = dx * sx / 2, dy * sy / 2
            corners.append((cx + c * x - s * y, cy + s * x + c * y, cz + dz))
    corners = np.array(corners)
    quads = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7)]
    pos, uv, faces = [], [], []
    for q in quads:
        base = len(pos)
        for k, vi in enumerate(q):
            pos.append(corners[vi])
            uv.append(((k in (1, 2)) * 1.0, (k in (2, 3)) * 1.0))
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return np.array(pos), np.array(uv), np.array(faces)


def wall_strip(points_bottom, height, u_scale=4.0, v_scale=3.0, both=True):
    """Muro vertical siguiendo una polilínea 3D (cara hacia la derecha del avance)."""
    pb = np.asarray(points_bottom, float)
    n = len(pb)
    pt = pb.copy()
    pt[:, 2] += height
    pos = np.vstack([pt, pb])
    d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pb[:, :2], axis=0), axis=1))])
    uv = np.vstack([np.column_stack([d / u_scale, np.zeros(n)]),
                    np.column_stack([d / u_scale, np.full(n, height / v_scale)])])
    i = np.arange(n - 1)
    faces = np.concatenate([np.column_stack([i, i + n, i + n + 1]), np.column_stack([i, i + n + 1, i + 1])])
    if both:
        faces = np.concatenate([faces, faces[:, ::-1]])
    return pos, uv, faces


def build_roads(net: RoadNetwork, scene, buildings_poly=None, water_poly=None, urban_poly=None, log=print,
                osm_guardrails=False, ortho=None, proj=None):
    """Genera calzadas, cruces, rayas, topes, banquetas, defensas, puentes y túneles.

    Devuelve un dict con los datos que necesitan el terreno, la vegetación y la textura del suelo.
    """
    carve_pts = []   # (x, y, z, radio_plano)
    road_polys = []
    surf_pts = []    # puntos de superficie para consultar altura (banquetas)
    tunnel_pts = []
    stats = {"km": 0.0, "topes": 0, "pasos": 0, "defensa_m": 0.0, "cruces": 0}
    skirts = []

    # ---- calzadas
    for e in net.edges:
        for a, b in net.ranges(e):
            sv = _svals(e, a, b)
            offs = np.array([-e.hw, 0.0, e.hw])
            if e.mat == "asfalto":
                # u = ancho completo (0..1), v = 4 anchos; variante y desfase al azar por tramo para
                # que dos calles (o dos tramos seguidos) nunca muestren el mismo dibujo
                k = (e.id * 2654435761) % 997
                if e.lanes >= 4:
                    mat = ("asfalto_4", "asfalto_4b")[k % 2]
                elif e.lanes <= 1 or 2 * e.hw < 5.0:
                    mat = ("asfalto_1", "asfalto_1b", "asfalto_1c")[k % 3]
                else:
                    mat = ("asfalto", "asfalto_b", "asfalto_c", "asfalto_d", "asfalto_e")[k % 5]
                pos, uv, faces = strip(e, sv, offs, u_scale=2 * e.hw, v_scale=8 * e.hw, u0=-e.hw)
                uv[:, 1] += (k % 101) / 101.0
            else:
                mat = e.mat
                ts = TEX_SCALE.get(e.mat, 4.0)
                pos, uv, faces = strip(e, sv, offs, u_scale=ts, v_scale=ts)
            scene.add("calzada", mat, pos, uv, faces, phys=e.phys)
            stats["km"] += (b - a) / 1000
            # faldón lateral (tapa huecos con el terreno): se añade al final, cuando ya se sabe
            # dónde hay otra calzada al lado (ahí el faldón asomaba como un muro en sierra)
            skirts.append((e, sv, mat))
            left = e.at(sv, e.hw)[:, :2]
            right = e.at(sv, -e.hw)[:, :2]
            ring = np.vstack([left, right[::-1]])
            if len(ring) >= 4:
                pg = Polygon(ring).buffer(0)
                if not pg.is_empty:
                    road_polys.append((pg, e))
            dense = _svals(e, a, b, 1.0)
            cen = e.at(dense, 0.0)
            if e.tunnel:
                tunnel_pts.append(np.column_stack([cen, np.full(len(cen), e.hw + 1.0)]))
            elif not e.bridge:
                # puntos a lo ancho de la sección con su altura real (incluye el peralte):
                # interiores con radio pequeño y las orillas con el margen plano exterior
                margin = (C.SIDEWALK_WIDTH + 0.4) if e.sidewalk else 0.8
                offs = np.linspace(-e.hw, e.hw, max(3, int(2 * e.hw / 1.0) + 1))
                S, O = np.meshgrid(dense, offs, indexing="ij")
                sec = e.at(S.ravel(), O.ravel())
                rad = np.where(np.abs(O.ravel()) >= e.hw - 1e-6, margin, 0.75)
                carve_pts.append(np.column_stack([sec, rad]))
            # orillas de la calzada cada metro: de aquí sale la altura de las banquetas
            for o in (-e.hw, e.hw):
                surf_pts.append(e.at(dense, o))

    # ---- cruces
    for nid, j in net.junctions.items():
        if not net.node_inside(nid):
            continue
        pts3 = []
        best = None
        for e, end in j["inc"]:
            s = e.s0 if end == 0 else e.s1
            pts3.append(e.at(s, e.hw))
            pts3.append(e.at(s, -e.hw))
            if best is None or e.prio > best.prio:
                best = e
        pts3 = np.array(pts3)
        hull = convex_hull_2d(pts3[:, :2])
        if len(hull) < 3:
            continue
        tree = cKDTree(pts3[:, :2])
        _, ii = tree.query(hull)
        hz = pts3[ii, 2]
        cx, cy = net.node_xy[nid]
        cz = net.node_z[nid]
        poly = Polygon(hull)
        # contorno del cruce: donde el borde coincide con el final de una calzada se usan
        # exactamente los mismos vértices que ella (orilla, centro, orilla), para que no haya
        # uniones en T (rendijas de subpíxel que parpadean); el resto se densifica cada 2 m
        inc = j["inc"]
        ring = []
        for h in range(len(hull)):
            ia, ib = int(ii[h]), int(ii[(h + 1) % len(hull)])
            pa, pb = pts3[ia, :2], pts3[ib, :2]
            ring.append(pa)
            if ia // 2 == ib // 2 and ia != ib:
                e_, end_ = inc[ia // 2]
                s_ = e_.s0 if end_ == 0 else e_.s1
                ring.append(e_.at(s_, 0.0)[:2])
            else:
                L_ = float(np.linalg.norm(pb - pa))
                k_ = int(L_ // 2.0)
                for t_ in np.arange(1, k_ + 1) / (k_ + 1):
                    ring.append(pa + (pb - pa) * t_)
        verts = np.array(ring, float)
        idx = earcut.triangulate_float64(verts, np.array([len(verts)], np.uint32))
        faces = np.asarray(idx, np.int64).reshape(-1, 3)
        rb_node = any(e.tags.get("junction") in ("roundabout", "circular") for e, _ in inc)
        if net.islands_union is not None and poly.intersects(net.islands_union):
            # el polígono convexo del cruce invadía el centro de la glorieta: se recorta
            cut = poly.difference(net.islands_union.buffer(0.05))
            parts = [g for g in iter_polys(cut) if g.area > 0.5]
            if not parts:
                continue
            poly = parts[0] if len(parts) == 1 else unary_union(parts)
            vs, fs, off = [], [], 0
            for g in parts:
                v_, f_ = triangulate_polygon(g, 2.0)
                vs.append(v_)
                fs.append(f_ + off)
                off += len(v_)
            verts, faces = np.vstack(vs), np.vstack(fs)
        if len(faces) == 0:
            continue
        # altura: ponderación por distancia inversa a los extremos (y el centro)
        refp = np.vstack([pts3[:, :2], [[cx, cy]]])
        refz = np.concatenate([pts3[:, 2], [cz]])
        d = np.linalg.norm(verts[:, None, :] - refp[None, :, :], axis=2) + 0.5
        w = 1.0 / d ** 2
        z = (w * refz).sum(1) / w.sum(1)
        # costura exacta con cada calle: los vértices que caen sobre el borde final de una
        # calzada toman la altura de esa calzada en ese punto (con su peralte), para que no
        # haya escalón ni rendija entre la calle y el cruce
        for e, end in j["inc"]:
            s = e.s0 if end == 0 else e.s1
            L = e.at(s, e.hw)[:2]
            R = e.at(s, -e.hw)[:2]
            v = R - L
            vv = float(v @ v)
            if vv < 1e-6:
                continue
            t = np.clip(((verts - L) @ v) / vv, 0.0, 1.0)
            dist = np.linalg.norm(verts - (L + t[:, None] * v), axis=1)
            on = dist < 0.05
            if on.any():
                o = e.hw - 2.0 * e.hw * t[on]
                z[on] = e.at(np.full(on.sum(), s), o)[:, 2]
        pos = np.column_stack([verts, z])
        if best.mat == "asfalto":
            uv = verts / 14.0
            scene.add("calzada", "asfalto_cruce", pos, uv, faces, phys=best.phys)
        else:
            uv = verts / TEX_SCALE.get(best.mat, 4.0)
            scene.add("calzada", best.mat, pos, uv, faces, phys=best.phys)
        road_polys.append((poly, best))
        stats["cruces"] += 1
        if not rb_node and len(inc) >= 2:
            _corner_fillets(net, scene, inc, poly, best, refp, refz, road_polys, carve_pts, surf_pts)
        carve_pts.append(np.column_stack([pos, np.full(len(pos), 1.2)]))
        # puntos interiores cada metro con la misma interpolación, para tallar todo el cruce
        bx0, by0, bx1, by1 = poly.bounds
        gx, gy = np.meshgrid(np.arange(bx0, bx1, 1.0), np.arange(by0, by1, 1.0))
        gx, gy = gx.ravel(), gy.ravel()
        inside = shapely.contains_xy(poly, gx, gy)
        if inside.any():
            gp = np.column_stack([gx[inside], gy[inside]])
            dd = np.linalg.norm(gp[:, None, :] - refp[None, :, :], axis=2) + 0.5
            ww = 1.0 / dd ** 2
            gz = (ww * refz).sum(1) / ww.sum(1)
            carve_pts.append(np.column_stack([gp, gz, np.full(len(gp), 1.0)]))
        # solo el contorno del cruce (los vértices de earcut están todos en el contorno)
        surf_pts.append(pos)

    if net.islands:
        _island_meshes(net, scene, carve_pts, ortho, proj)
    road_union = unary_union([p for p, _ in road_polys]) if road_polys else None
    _add_skirts(scene, skirts, road_union)

    # ---- rayas, pasos peatonales y topes
    _markings(net, scene, stats)
    _crossings_and_bumps(net, scene, stats)

    # ---- banquetas
    surf_all = np.vstack(surf_pts) if surf_pts else np.zeros((0, 3))
    sw_poly = None
    if road_union is not None and len(surf_all):
        sw_poly, sw_carve = _sidewalks(net, scene, road_polys, road_union, surf_all, buildings_poly, water_poly,
                                       urban_poly)
        if len(sw_carve):
            carve_pts.append(sw_carve)

    # ---- defensas, fantasmas, puentes, túneles
    _roadside(net, scene, stats, osm_guardrails)
    _structures(net, scene)

    carve = np.vstack(carve_pts) if carve_pts else np.zeros((0, 4))
    tun = np.vstack(tunnel_pts) if tunnel_pts else np.zeros((0, 4))
    log(f"  carreteras: {stats['km']:.1f} km, {stats['cruces']} cruces, {stats['topes']} topes, "
        f"{stats['pasos']} pasos peatonales, {stats['defensa_m']:.0f} m de defensa")
    return {"carve": carve, "tunnel": tun, "road_union": road_union, "road_polys": road_polys,
            "sidewalks": sw_poly, "stats": stats}


# --------------------------------------------------------------------------------------
def _add_skirts(scene, skirts, road_union):
    """Faldón de 0.8 m bajo cada orilla de calzada, salvo donde lo de fuera es otra calzada."""
    other = road_union.buffer(-0.05) if road_union is not None else None
    if other is not None:
        shapely.prepare(other)
    for e, sv, mat in skirts:
        for side in (-1, 1):
            top = e.at(sv, side * e.hw)
            probe = e.at(sv, side * (e.hw + 0.6))[:, :2]
            free = np.ones(len(sv), bool) if other is None else ~shapely.contains_xy(other, probe[:, 0], probe[:, 1])
            if side > 0:
                top, free = top[::-1], free[::-1]
            i = 0
            n = len(top)
            while i < n - 1:
                if not (free[i] and free[i + 1]):
                    i += 1
                    continue
                j = i
                while j + 1 < n and free[j + 1]:
                    j += 1
                seg = top[i:j + 1].copy()
                if len(seg) >= 2:
                    seg[:, 2] -= 0.8
                    wp, wuv, wf = wall_strip(seg, 0.8, both=False)
                    scene.add("calzada", mat, wp, wuv, wf, phys=None)
                i = j + 1


def _corner_fillets(net, scene, inc, poly, best, refp, refz, road_polys, carve_pts, surf_pts):
    """Curvas de bordillo en las esquinas del cruce (radio 3.5 m en ciudad, 6 m en carretera).

    El cruce era el polígono convexo de las puntas de las calles: la esquina quedaba en chaflán
    recto y la banqueta con picos. Aquí se cierra (dilatar y erosionar) la unión del cruce con el
    arranque de cada calle; lo que se añade son justo las curvas de esquina. La banqueta, que se
    calcula después como franja alrededor de la calzada, sigue la curva sola.
    """
    r_f = 3.5 if any(e.urban for e, _ in inc) else 6.0
    parts = [poly]
    edge_pts = []
    for e, end in inc:
        s_a = e.s0 if end == 0 else e.s1
        s_b = min(e.s1, s_a + 18.0) if end == 0 else max(e.s0, s_a - 18.0)
        lo, hi = sorted((s_a, s_b))
        if hi - lo < 1.0:
            continue
        sv_ = np.linspace(lo, hi, max(3, int((hi - lo) / 1.0) + 1))
        Lp, Rp = e.at(sv_, e.hw), e.at(sv_, -e.hw)
        edge_pts += [Lp, Rp]
        g = Polygon(np.vstack([Lp[:, :2], Rp[::-1, :2]])).buffer(0)
        if not g.is_empty:
            parts.append(g)
    if not edge_pts:
        return
    U = unary_union(parts)
    F = U.buffer(r_f, join_style=1, quad_segs=6).buffer(-r_f, join_style=1, quad_segs=6).difference(U.buffer(0.01))
    E = np.vstack(edge_pts)
    etree = cKDTree(E[:, :2])
    mat = "asfalto_cruce" if best.mat == "asfalto" else best.mat
    for fp in iter_polys(F):
        if fp.area < 0.3 or fp.area > 150:
            continue
        v, f = triangulate_polygon(fp, 1.0)
        if len(f) == 0:
            continue
        a2 = np.abs((v[f[:, 1], 0] - v[f[:, 0], 0]) * (v[f[:, 2], 1] - v[f[:, 0], 1])
                    - (v[f[:, 1], 1] - v[f[:, 0], 1]) * (v[f[:, 2], 0] - v[f[:, 0], 0]))
        f = f[a2 > 1e-5]
        if len(f) == 0:
            continue
        d = np.linalg.norm(v[:, None, :] - refp[None, :, :], axis=2) + 0.5
        w = 1.0 / d ** 2
        z = (w * refz).sum(1) / w.sum(1)
        dd, jj = etree.query(v)
        near = dd < 0.35                      # pegado a la orilla de una calle: su altura exacta
        z[near] = E[jj[near], 2]
        pos = np.column_stack([v, z])
        uv = v / (14.0 if best.mat == "asfalto" else TEX_SCALE.get(best.mat, 4.0))
        scene.add("calzada", mat, pos, uv, f, phys=best.phys)
        road_polys.append((fp, best))
        carve_pts.append(np.column_stack([pos, np.full(len(pos), 1.0)]))
        surf_pts.append(pos)


def _island_meshes(net, scene, carve_pts, ortho=None, proj=None):
    """Isleta central elevada 15 cm con bordillo. Con ortofoto, del color real de la isleta
    (césped, tierra, grava o pintada, como la roja de Cobeña); si no, césped."""
    rb = [e for e in net.edges if e.tags.get("junction") in ("roundabout", "circular")]
    P = np.vstack([e.at(_svals(e, e.s0, e.s1, 1.0), o) for e in rb for o in (e.hw, -e.hw)])
    tree = cKDTree(P[:, :2])

    def zf(xy):
        d, ii = tree.query(xy, k=min(6, len(P)))
        w = 1.0 / (d ** 2 + 0.25)
        return (w * P[ii, 2]).sum(1) / w.sum(1)
    for isl in net.islands:
        v, f = triangulate_polygon(isl, 2.0)
        if len(f) == 0:
            continue
        z = zf(v)
        mat = "isleta"
        if ortho is not None and proj is not None:
            from . import textures as _tx
            x0, y0, x1, y1 = isl.bounds
            gx, gy = np.meshgrid(np.linspace(x0, x1, 24), np.linspace(y0, y1, 24))
            ins = shapely.contains_xy(isl.buffer(-0.8), gx.ravel(), gy.ravel())
            if ins.sum() > 10:
                col = ortho.sample(proj, gx.ravel()[ins], gy.ravel()[ins], order=1)
                col = col[col.sum(-1) > 30]
                if len(col):
                    mat = _tx.island_material(scene, np.median(col, axis=0))
        scene.add("isleta", mat, np.column_stack([v, z + 0.15]), v / 3.0, f, phys="GRASS")
        carve_pts.append(np.column_stack([v, z, np.full(len(v), 1.2)]))
        # también el interior (rejilla de 2 m): si no, el terreno natural asomaba por el césped
        x0, y0, x1, y1 = isl.bounds
        gx, gy = np.meshgrid(np.arange(x0, x1, 2.0), np.arange(y0, y1, 2.0))
        gx, gy = gx.ravel(), gy.ravel()
        ins = shapely.contains_xy(isl, gx, gy)
        if ins.any():
            g = np.column_stack([gx[ins], gy[ins]])
            carve_pts.append(np.column_stack([g, zf(g), np.full(len(g), 1.5)]))
        ring = np.asarray(shapely.segmentize(shapely.geometry.polygon.orient(isl, 1.0).exterior, 1.5).coords)[:, :2]
        zt = zf(ring) + 0.15
        p, uv, ff = wall_strip(np.column_stack([ring, zt - 0.47]), 0.47, u_scale=1, v_scale=1, both=False)
        scene.add("isleta", "guarnicion", p, uv, ff, phys="WALL")


def _visibility(e, forward=True, step=3.0, max_d=400.0, dem_z=None):
    """Distancia de visibilidad en cada punto, en planta y en alzado, en un sentido.

    Para cada punto i se buscan los puntos j hacia adelante; j es visible si la recta
    ojo(i)->objeto(j) (1.08 m sobre la calzada) no pasa por debajo del perfil de la calle en
    ningún punto intermedio y, fuera de la calzada, no la tapa el terreno real (+0.6 m de
    matorral). Antes se exigía no salir de un corredor de orilla + 2 m: en una curva amplia entre
    campos abiertos eso prohibía adelantar siempre (raya continua donde la real es discontinua).
    Sin `dem_z` se usa el corredor como antes.
    """
    sv = np.arange(0, e.length, step)
    if len(sv) < 3:
        return sv, np.full(len(sv), max_d)
    P = e.at(sv, 0.0)
    if not forward:
        P = P[::-1]
    n = len(P)
    m = int(max_d / step)
    out = np.zeros(n)
    eye = 1.08
    lat = e.hw + 2.0
    ptree = cKDTree(P[:, :2]) if dem_z is not None else None
    for i in range(n):
        j_end = min(n - 1, i + m)
        Q = P[i + 1:j_end + 1]
        M = len(Q)
        if M < 2:
            out[i] = max_d
            continue
        a = P[i]
        ab = Q[:, :2] - a[:2]                    # candidatos j
        L2 = np.maximum((ab * ab).sum(1), 1e-9)
        ap = ab                                  # puntos intermedios k (mismo conjunto)
        dot = ap @ ab.T                          # [k, j]
        t = np.clip(dot / L2[None, :], 0, 1)
        hd2 = (ap * ap).sum(1)[:, None] - 2 * t * dot + t * t * L2[None, :]
        zl = (a[2] + eye) + t * ((Q[:, 2] + eye)[None, :] - (a[2] + eye))
        kk = np.arange(M)[:, None]
        jj = np.arange(M)[None, :]
        if dem_z is None:
            bad = (hd2 > lat * lat) | (zl < Q[:, 2][:, None] + 0.15)
            bad &= kk < jj
            blocked = bad.any(0)
        else:
            bad = (zl < Q[:, 2][:, None] + 0.15) & (hd2 <= lat * lat) & (kk < jj)
            blocked = bad.any(0)
            # terreno fuera de la calzada: 6 muestras a lo largo de cada línea de vista
            ts = np.linspace(0.15, 0.85, 6)
            px = a[0] + ab[None, :, 0] * ts[:, None]
            py = a[1] + ab[None, :, 1] * ts[:, None]
            zline = (a[2] + eye) + ts[:, None] * ((Q[:, 2] + eye)[None, :] - (a[2] + eye))
            tz = dem_z(px.ravel(), py.ravel()).reshape(px.shape)
            # sobre la calzada manda su perfil (ya revisado arriba); el terreno solo fuera de ella
            dlat, _ = ptree.query(np.column_stack([px.ravel(), py.ravel()]))
            off = (dlat > lat).reshape(px.shape)
            blocked |= (((tz + 0.6) > zline) & off).any(0)
        if blocked.any():
            first = int(np.argmax(blocked))
            out[i] = first * step
        else:
            out[i] = max_d if j_end == n - 1 else M * step
    if not forward:
        out = out[::-1]
    return sv, out


def _clean_runs(flag, sv, min_true, min_false):
    """Elimina islas cortas (histeresis) en una señal booleana a lo largo de s."""
    f = flag.copy()
    for target, minlen in ((True, min_true), (False, min_false)):
        i = 0
        while i < len(f):
            if f[i] == target:
                j = i
                while j < len(f) and f[j] == target:
                    j += 1
                if sv[min(j, len(sv) - 1)] - sv[i] < minlen and 0 < i and j < len(f):
                    f[i:j] = not target
                i = j
            else:
                i += 1
    return f


def _line(scene, e, a, b, offset, width, mat, dashed=None):
    if b - a < 0.5:
        return
    if dashed:
        on, off = dashed
        s = a + 1.0
        while s < b:
            s2 = min(b, s + on)
            sv = _svals(e, s, s2, 0.75)
            p, uv, f = strip(e, sv, [offset - width / 2, offset + width / 2], dz=DZ_RAYA, u_scale=1, v_scale=1)
            scene.add("rayas", mat, p, uv, f)
            s = s2 + off
    else:
        sv = _svals(e, a, b)
        p, uv, f = strip(e, sv, [offset - width / 2, offset + width / 2], dz=DZ_RAYA, u_scale=1, v_scale=1)
        scene.add("rayas", mat, p, uv, f)


def _markings(net, scene, stats):
    net.nopass = []
    ka, kb = regions.active().pass_sight
    psd_tab = lambda v: ka * v + kb  # noqa: E731 - visibilidad mínima para permitir rebase (m)
    for e in net.edges:
        if e.cls not in C.MARKED_CLASSES or e.mat in ("terraceria", "empedrado", "adoquin") or e.tunnel and e.length < 30:
            continue
        rngs = net.ranges(e, e.s0 + 2.0, e.s1 - 2.0)
        if not rngs:
            continue
        R = regions.active()
        fast = e.cls in ("motorway", "trunk", "motorway_link", "trunk_link")
        w = R.line_w_fast if fast else (R.line_w if not e.urban else min(R.line_w, 0.12))
        dash = R.dash_rural if not e.urban else R.dash_urban
        cmat = R.center_mat
        carriage = e.lanes * e.lane_w
        edge_o = min(carriage / 2.0, e.hw - 0.2)
        if e.oneway:
            for a, b in rngs:
                _line(scene, e, a, b, -edge_o, w, "raya_blanca")            # orilla derecha
                _line(scene, e, a, b, edge_o, w, R.left_edge_mat)           # orilla izquierda (amarilla en MX)
                for k in range(1, e.lanes):
                    o = -edge_o + k * (2 * edge_o / e.lanes)
                    _line(scene, e, a, b, o, w, "raya_blanca", dash)
            continue
        # doble sentido: visibilidad de rebase en ambos sentidos
        sv, vf = _visibility(e, True, dem_z=net.dem_z)
        _, vb = _visibility(e, False, dem_z=net.dem_z)
        if len(sv) < 2:
            continue
        psd = psd_tab(e.speed)
        can_pass = (vf >= psd) & (vb >= psd) & (not e.urban)
        can_pass = _clean_runs(can_pass, sv, 60.0, 30.0)
        # zonas de prohibido adelantar de más de 100 m (para poner R-305 / R-502 en el lateral)
        i0 = None
        for i in range(len(sv) + 1):
            blocked = i < len(sv) and not can_pass[i]
            if blocked and i0 is None:
                i0 = i
            elif not blocked and i0 is not None:
                s_a, s_b = sv[i0], sv[i - 1]
                if s_b - s_a > 100 and not e.urban:
                    net.nopass.append((e, float(s_a), float(s_b)))
                i0 = None
        for a, b in rngs:
            if not e.urban or e.cls in ("primary", "secondary", "trunk"):
                _line(scene, e, a, b, -edge_o, w, "raya_blanca")
                _line(scene, e, a, b, edge_o, w, "raya_blanca")
            # raya central por tramos según visibilidad
            i = 0
            while i < len(sv):
                j = i
                while j < len(sv) and can_pass[j] == can_pass[i]:
                    j += 1
                seg_a = sv[i]
                seg_b = sv[j] if j < len(sv) else e.length
                sa, sb = max(a, seg_a), min(b, seg_b)
                if sb - sa > 0.5:
                    if e.lanes >= 4:
                        _line(scene, e, sa, sb, 0.12, w, cmat)
                        _line(scene, e, sa, sb, -0.12, w, cmat)
                    else:
                        _line(scene, e, sa, sb, 0.0, w, cmat, dash if can_pass[i] else None)
                i = j
            per_dir = max(1, e.lanes // 2)
            for k in range(1, per_dir):
                o = k * (edge_o / per_dir)
                _line(scene, e, a, b, o, w, "raya_blanca", dash)
                _line(scene, e, a, b, -o, w, "raya_blanca", dash)


def _node_positions(net):
    pos = {}
    for e in net.edges:
        if not e.nodes:
            continue
        pts = np.array([net.node_xy[n] for n in e.nodes])
        tree = cKDTree(e.P)
        _, ii = tree.query(pts)
        for nid, i in zip(e.nodes, ii):
            pos.setdefault(nid, []).append((e, float(e.s[i])))
    return pos


def _crossings_and_bumps(net, scene, stats):
    where = _node_positions(net)
    for nid, tags in net.node_tags.items():
        if nid not in where:
            continue
        tc = tags.get("traffic_calming")
        cr = tags.get("highway") == "crossing" or "crossing" in tags
        for e, s in where[nid]:
            if not any(a + 1 < s < b - 1 for a, b in net.ranges(e, e.s0, e.s1)):
                continue
            if tc and tc not in ("no", "island", "chicane", "choker"):
                if tc == "rumble_strip":
                    L, H, flat = 0.5, 0.015, 0.0
                    reps = [s - 3, s - 1.5, s, s + 1.5, s + 3]
                elif tc == "table":
                    L, H, flat = 2.0, 0.09, 3.0
                    reps = [s]
                else:  # tope (bump, hump, yes, cushion...)
                    L, H, flat = 2.2, 0.10, 0.0
                    reps = [s]
                for sc in reps:
                    tot = L + flat
                    sv = np.linspace(sc - tot / 2, sc + tot / 2, 14)
                    offs = np.linspace(-e.hw, e.hw, 6)
                    p, uv, f = strip(e, sv, offs, u_scale=1.0, v_scale=1.0)
                    t = (sv - sc)
                    prof = np.where(np.abs(t) <= flat / 2, H,
                                    H * np.cos(np.clip((np.abs(t) - flat / 2) / L * math.pi, 0, math.pi / 2)) ** 2)
                    p[:, 2] += np.repeat(prof, len(offs)) + 0.005
                    scene.add("topes", "tope", p, uv, f, phys=e.phys)
                stats["topes"] += 1
            elif cr and e.mat in ("asfalto", "concreto"):
                # cebra: franjas paralelas al eje de 0.4 m con 0.4 m de separación, 3 m de largo
                o = -e.hw + 0.3
                while o + 0.4 < e.hw - 0.3:
                    sv = np.array([s - 1.5, s + 1.5])
                    p, uv, f = strip(e, sv, [o, o + 0.4], dz=DZ_RAYA, u_scale=1, v_scale=1)
                    scene.add("rayas", "raya_blanca", p, uv, f)
                    o += 0.8
                stats["pasos"] += 1
            break


def _sidewalks(net, scene, road_polys, road_union, surf_all, buildings_poly, water_poly, urban_poly):
    """Banquetas con guarnición.

    La altura de cada punto de la banqueta es la de las orillas de calzada más cercanas
    (promedio ponderado de varias, no solo la más cercana: con una sola, en esquinas y calles
    en pendiente la banqueta saltaba de una calle a otra y salían triángulos torcidos). El
    polígono se trocea en celdas de 3 m para que haya vértices a lo ancho y la superficie siga
    la pendiente. Devuelve también puntos para tallar el terreno debajo de toda la banqueta.
    """
    sw_src = [p for p, e in road_polys if e.sidewalk]
    if not sw_src:
        return None, np.zeros((0, 4))
    belt = unary_union([p.buffer(C.SIDEWALK_WIDTH, join_style=1, quad_segs=6) for p in sw_src])   # esquinas redondeadas
    sw = belt.difference(road_union.buffer(0.02))
    if urban_poly is not None:
        sw = sw.intersection(urban_poly)
    if water_poly is not None:
        sw = sw.difference(water_poly)
    if net.islands_union is not None:
        sw = sw.difference(net.islands_union.buffer(0.3))   # la isleta tiene su propio bordillo
    sw = sw.buffer(-0.25).buffer(0.25)
    tree = cKDTree(surf_all[:, :2])
    k = min(6, len(surf_all))

    def road_z(xy):
        # promedio de los puntos de orilla más cercanos, pero solo los que están casi tan cerca
        # como el más cercano: así la banqueta sigue a SU calle y no se va a la altura de otra
        # calle paralela o más alta (eso hacía rampas y losas flotando)
        d, ii = tree.query(xy, k=k)
        if k == 1:
            d, ii = d[:, None], ii[:, None]
        near = d <= d[:, :1] + 1.0
        w = near / (d ** 2 + 0.25)
        return (w * surf_all[ii, 2]).sum(1) / w.sum(1)

    jpts = np.array([net.node_xy[n] for n in net.junctions]) if net.junctions else np.zeros((0, 2))
    jtree = cKDTree(jpts) if len(jpts) else None
    road_edge = road_union.boundary
    shapely.prepare(road_edge)
    carve = []
    cell = 3.0
    medians = []
    for poly in iter_polys(sw):
        if poly.area < 1.5:
            continue
        # mediana / isleta encerrada entre calzadas (casi todo su contorno pega a una calle): no es
        # banqueta; como banqueta elevada salía una losa inclinada entre dos vías a distinta altura.
        # Se hace calzada a ras (zona cebreada), que pasa suave de una vía a la otra.
        ring_pts = np.asarray(shapely.segmentize(poly.exterior, 1.0).coords)[:, :2]
        if shapely.dwithin(road_edge, shapely.points(ring_pts), 0.4).mean() > 0.8:
            v, f = triangulate_polygon(poly, 1.5)
            if len(f):
                zr = road_z(v)
                scene.add("calzada", "asfalto_cruce", np.column_stack([v, zr + 0.01]), v / 14.0, f, phys="ROAD")
                carve.append(np.column_stack([v, zr, np.full(len(v), 1.0)]))
                medians.append(poly)
            continue
        # trocear en celdas para tener vértices interiores
        x0, y0, x1, y1 = poly.bounds
        pieces = [poly]
        if (x1 - x0) > cell or (y1 - y0) > cell:
            gx = np.arange(math.floor(x0 / cell) * cell, x1, cell)
            gy = np.arange(math.floor(y0 / cell) * cell, y1, cell)
            boxes = [shapely.box(x, y, x + cell, y + cell) for x in gx for y in gy]
            inter = shapely.intersection(poly, boxes)
            pieces = [g for g in inter if not g.is_empty]
        for piece in pieces:
            for pg in iter_polys(piece):
                if pg.area < 0.01:
                    continue
                verts, faces = triangulate_polygon(pg, 1.5)
                if len(faces) == 0:
                    continue
                # fuera astillas: earcut deja triángulos sin área en planta cuando hay vértices
                # alineados; con alturas distintas se ven como triángulos verticales raros
                v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
                a2 = np.abs((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1]) - (v1[:, 1] - v0[:, 1]) * (v2[:, 0] - v0[:, 0]))
                faces = faces[a2 > 1e-5]
                if len(faces) == 0:
                    continue
                zr = road_z(verts)
                pos = np.column_stack([verts, zr + C.CURB_HEIGHT])
                scene.add("banqueta", "banqueta", pos, verts / 3.0, faces, phys="PAVEMENT")
                carve.append(np.column_stack([verts, zr, np.full(len(verts), 0.9)]))
        # guarnición: muro vertical en cada anillo (cara hacia afuera del polígono)
        po = shapely.geometry.polygon.orient(poly, 1.0)
        for ring in [po.exterior] + list(po.interiors):
            r = np.asarray(shapely.segmentize(ring, 1.5).coords)[:, :2]
            if len(r) < 2:
                continue
            zt = road_z(r) + C.CURB_HEIGHT
            mid = (r[:-1] + r[1:]) / 2
            # junto a la calle: guarnición (amarilla cerca de las esquinas); atrás: muro que baja
            # más, para tapar el hueco cuando el terreno del predio queda más bajo
            near_road = shapely.dwithin(road_edge, shapely.points(mid), 0.3)
            seg_yellow = np.zeros(len(mid), bool)
            if jtree is not None:
                dj, _ = jtree.query(mid)
                seg_yellow = (dj < 12.0) & near_road & regions.active().curb_yellow_corners
            kind = np.where(near_road, np.where(seg_yellow, 2, 1), 0)
            i = 0
            while i < len(kind):
                j = i
                while j + 1 < len(kind) and kind[j + 1] == kind[i]:
                    j += 1
                drop = 0.32 if kind[i] else 1.2
                seg = np.column_stack([r[i:j + 2], zt[i:j + 2] - C.CURB_HEIGHT - drop])
                mat = "guarnicion_amarilla" if kind[i] == 2 else "guarnicion"
                # wall_strip pone la cara a la derecha del avance; el anillo CCW -> afuera
                p, uv, f = wall_strip(seg, C.CURB_HEIGHT + drop, u_scale=1, v_scale=1, both=False)
                scene.add("banqueta", mat, p, uv, f, phys=None)
                i = j + 1
    if medians:
        sw = sw.difference(unary_union(medians))
    return sw, (np.vstack(carve) if carve else np.zeros((0, 4)))


def _roadside(net, scene, stats, osm_guardrails=False):
    # Si OSM ya tiene guardarraíles dibujados en la zona, esos mandan (barriers.py) y aquí solo se
    # ponen donde el terraplén es muy alto; si no, se ponen por heurística desde 2.5 m de caída.
    min_drop = 6.0 if osm_guardrails else 2.5
    for e in net.edges:
        if e.urban or e.cls not in C.RURAL_CLASSES or e.bridge or e.tunnel:
            continue
        for a, b in net.ranges(e, e.s0 + 3, e.s1 - 3):
            sv = np.arange(a, b, 2.0)
            if len(sv) < 4:
                continue
            for side in (-1, 1):
                off = side * (e.hw + 3.0)
                road = e.at(sv, side * e.hw)
                probe = e.at(sv, off)
                tz = net.dem_z(probe[:, 0], probe[:, 1])
                drop = road[:, 2] - tz
                # terraplén alto, o terraplén moderado en curva cerrada (donde de verdad se pone)
                kap_s = np.abs(np.interp(sv, e.s, np.gradient(np.unwrap(np.arctan2(e.T[:, 1], e.T[:, 0])), e.s)))
                need = (drop > min_drop) | ((drop > 1.2) & (kap_s > 1 / 250.0))
                need = _clean_runs(need, sv, 20.0, 10.0)
                # extender 4 m
                k = 2
                need = np.convolve(need.astype(float), np.ones(2 * k + 1), "same") > 0
                i = 0
                while i < len(sv):
                    if not need[i]:
                        i += 1
                        continue
                    j = i
                    while j < len(sv) and need[j]:
                        j += 1
                    seg_s = sv[i:j]
                    if len(seg_s) >= 3:
                        # bionda de doble onda con postes cada 4 m y extremos abatidos
                        from .roadside import bionda
                        o = side * (e.hw + 0.6)
                        base = e.at(seg_s, o)
                        nx = np.interp(seg_s, e.s, e.N[:, 0])
                        ny = np.interp(seg_s, e.s, e.N[:, 1])
                        toward = -side * np.column_stack([nx, ny])     # hacia la calzada
                        stats["defensa_m"] += bionda(scene, base, toward)
                    i = j
                # fantasmas donde no hay defensa
                h = np.unwrap(np.arctan2(e.T[:, 1], e.T[:, 0]))
                kap = np.abs(np.interp(sv, e.s, np.gradient(h, e.s)))
                last = -1e9
                for idx, sp in enumerate(sv):
                    g_str, g_cur = regions.active().delineator_gap
                    gap = g_cur if kap[idx] > 1 / 300 else g_str
                    if need[idx] or sp - last < gap:
                        continue
                    last = sp
                    c = e.at(sp, side * (e.hw + 0.9))
                    p, uv, f = box_mesh((c[0], c[1], c[2] - 0.2), (0.12, 0.12, 1.2), math.atan2(*e.frame(sp)[::-1]))
                    scene.add("senales", "fantasma", p, uv, f)


def _structures(net, scene):
    for e in net.edges:
        if not (e.bridge or e.tunnel):
            continue
        for a, b in net.ranges(e, 0.0, e.length):
            sv = _svals(e, a, b)
            if e.bridge:
                for side in (-1, 1):
                    o = side * (e.hw + 0.15)
                    base = e.at(sv, o)
                    pts = base if side < 0 else base[::-1]
                    p, uv, f = wall_strip(pts, 0.95, both=True)
                    scene.add("puente", "puente", p, uv, f, phys="WALL")
                # losa inferior
                p, uv, f = strip(e, sv, [-e.hw - 0.3, e.hw + 0.3], dz=-1.2)
                f = f[:, ::-1]
                scene.add("puente", "puente", p, uv, f)
                for side in (-1, 1):
                    top = e.at(sv, side * (e.hw + 0.3))
                    pts = top[::-1] if side > 0 else top
                    bot = pts.copy()
                    bot[:, 2] -= 1.2
                    p, uv, f = wall_strip(bot, 1.2, both=False)
                    scene.add("puente", "puente", p, uv, f)
                # pilas cada 30 m si hay altura
                for sp in np.arange(a + 15, b - 10, 30.0):
                    c = e.at(sp, 0.0)
                    gz = float(net.dem_z(np.array([c[0]]), np.array([c[1]]))[0])
                    hgt = c[2] - 1.2 - gz
                    if hgt > 2.0:
                        yaw = math.atan2(*e.frame(sp)[::-1])
                        p, uv, f = box_mesh((c[0], c[1], gz - 1.0), (1.4, e.hw * 1.2, hgt + 1.0), yaw)
                        scene.add("puente", "puente", p, uv, f, phys="WALL")
            else:
                for side in (-1, 1):
                    o = side * (e.hw + 0.6)
                    base = e.at(sv, o)
                    pts = base[::-1] if side < 0 else base   # cara hacia adentro
                    p, uv, f = wall_strip(pts, 5.8, both=False)
                    scene.add("tunel", "muro", p, uv, f, phys="WALL")
                p, uv, f = strip(e, sv, [-e.hw - 0.6, e.hw + 0.6], dz=5.8)
                scene.add("tunel", "muro", p, uv, f[:, ::-1])


# --------------------------------------------------------------------------------------
# ruta para tramo / circuito
# --------------------------------------------------------------------------------------
def _snap(net, p):
    """Punto más cercano sobre cualquier calle dentro del área: (arista, s)."""
    best = (None, 0.0, 1e18)
    for e in net.edges:
        if not e.runs:
            continue
        d = np.linalg.norm(e.P - p, axis=1)
        i = int(np.argmin(d))
        if d[i] < best[2]:
            best = (e, float(e.s[i]), float(d[i]))
    if best[0] is None:
        raise RuntimeError("no hay calles cerca de un punto de la ruta")
    return best[0], best[1]


def _shortest(net, adj, src, dst):
    """Dijkstra entre dos puntos sobre aristas (arista, s). Devuelve lista de (arista, s_ini, s_fin)."""
    es, ss = src
    et, st = dst
    if es is et and (st >= ss or not es.oneway):
        return [(es, ss, st)]
    start = "SRC"
    dist = {start: 0.0}
    prev = {}
    pq = [(0.0, 0, start)]
    cnt = 1
    # salidas del punto de origen hacia los extremos de su arista
    first = [(es.b, es.length - ss, (es, ss, es.length))]
    if not es.oneway:
        first.append((es.a, ss, (es, ss, 0.0)))
    end_cost = {et.a: (st, (et, 0.0, st))}
    if not et.oneway:
        end_cost[et.b] = (et.length - st, (et, et.length, st))
    best_end, best_val = None, 1e18
    while pq:
        d, _, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18) or d >= best_val:
            continue
        if u == start:
            nbrs = [(v, L, piece) for v, L, piece in first]
        else:
            if u in end_cost and d + end_cost[u][0] < best_val:
                best_val, best_end = d + end_cost[u][0], u
            nbrs = [(v, L, (e, 0.0, e.length) if rev == 0 else (e, e.length, 0.0)) for v, L, e, rev in adj.get(u, [])]
        for v, L, piece in nbrs:
            nd = d + L
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, piece)
                heapq.heappush(pq, (nd, cnt, v))
                cnt += 1
    if best_end is None:
        raise RuntimeError("no hay camino entre dos puntos de la ruta (¿calles de un sentido?)")
    seq = [end_cost[best_end][1]]
    cur = best_end
    while cur != start:
        u, piece = prev[cur]
        seq.append(piece)
        cur = u
    return seq[::-1]


def route(net: RoadNetwork, waypoints_xy, closed=False):
    """Camino más corto que pasa por los puntos dados (pegados a la calle más cercana).

    Devuelve la polilínea 3D del eje, la media anchura en cada punto y la lista de piezas
    (arista, s_ini, s_fin) recorridas.
    """
    adj = {}
    for e in net.edges:
        if not e.runs:
            continue
        adj.setdefault(e.a, []).append((e.b, e.length, e, 0))
        if not e.oneway:
            adj.setdefault(e.b, []).append((e.a, e.length, e, 1))
    snaps = [_snap(net, np.asarray(p, float)) for p in waypoints_xy]
    if closed:
        snaps.append(snaps[0])
    pieces = []
    for a, b in zip(snaps[:-1], snaps[1:]):
        pieces += _shortest(net, adj, a, b)
    pts, hws = [], []
    for e, s0, s1 in pieces:
        if abs(s1 - s0) < 0.5:
            continue
        n = max(2, int(abs(s1 - s0) / 1.0) + 1)
        sv = np.linspace(s0, s1, n)
        p = e.at(sv, 0.0)
        if pts:
            p = p[1:]
        pts.append(p)
        hws.append(np.full(len(p), e.hw))
    if not pts:
        raise RuntimeError("la ruta quedó vacía (¿puntos demasiado juntos?)")
    return np.vstack(pts), np.concatenate(hws), pieces
