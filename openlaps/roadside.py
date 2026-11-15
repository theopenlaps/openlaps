"""Laterales de la carretera: señalización vertical, guardarraíl bionda, tendido eléctrico y vallas.

Lo que hace creíble una carretera además del asfalto. Todo sale de datos de OSM cuando existen
(ceda el paso, STOP, pasos de peatones, pueblos, líneas eléctricas, vallas) y, donde OSM no dice
nada, de las reglas de la norma del país (perfil `regions.py`):

- Glorietas: ceda el paso (R-1) + glorieta obligatoria (R-402) en cada entrada, y aviso de
  glorieta (P-4) unos 150 m antes en carretera.
- Pasos de peatones: señal S-13 a ambos lados.
- Prohibido adelantar (R-305) al empezar cada tramo de raya continua de más de 100 m, y fin de
  prohibición (R-502) al acabar.
- Entrada y salida de poblado (S-500 / S-510) con el nombre real del pueblo de OSM.
- Paneles direccionales azules con flechas blancas en el exterior de las curvas cerradas.
- Guardarraíl bionda de doble onda, postes cada 4 m y extremos abatidos hasta el suelo.
- Postes y cables del tendido eléctrico (catenaria), y vallas de malla de las parcelas.
"""
import math

import numpy as np
import shapely
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from . import textures, regions
from .roads import box_mesh, wall_strip

RED, WHITE, BLUE, BLACK = (200, 30, 36), (245, 245, 245), (24, 78, 160), (20, 20, 20)


# ------------------------------------------------------------------ texturas de señales
def _font(size):
    return textures._font(size)


def _canvas(n=256, w=None):
    return Image.new("RGBA", (w or n, n), (0, 0, 0, 0))


def sign_give_way(n=256):
    """R-1 ceda el paso: triángulo invertido, borde rojo."""
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.polygon([(6, 10), (n - 6, 10), (n / 2, n - 8)], fill=RED)
    d.polygon([(n * 0.2, n * 0.17), (n * 0.8, n * 0.17), (n / 2, n * 0.78)], fill=WHITE)
    if regions.active().code == "mx":
        d.text((n / 2, n * 0.36), "CEDA EL\nPASO", fill=BLACK, anchor="mm", font=_font(int(n * 0.09)), align="center")
    return im


def sign_roundabout(n=256):
    """R-402 glorieta obligatoria: círculo azul con tres flechas blancas en giro."""
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.ellipse([4, 4, n - 4, n - 4], fill=BLUE, outline=WHITE, width=5)
    c, r = n / 2, n * 0.27
    for k in range(3):
        a0 = math.radians(90 + 120 * k)
        pts = [(c + r * math.cos(a0 + t), c - r * math.sin(a0 + t)) for t in np.linspace(0.25, 1.65, 12)]
        d.line(pts, fill=WHITE, width=int(n * 0.07))
        ax, ay = pts[-1]
        ang = a0 + 1.65 + math.pi / 2
        tip = (ax + 0.09 * n * math.cos(ang), ay - 0.09 * n * math.sin(ang))
        left = (ax + 0.07 * n * math.cos(ang + 2.4), ay - 0.07 * n * math.sin(ang + 2.4))
        right = (ax + 0.07 * n * math.cos(ang - 2.4), ay - 0.07 * n * math.sin(ang - 2.4))
        d.polygon([tip, left, right], fill=WHITE)
    return im


def _warning(n=256):
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.polygon([(n / 2, 6), (n - 6, n - 14), (6, n - 14)], fill=RED)
    d.polygon([(n / 2, n * 0.2), (n * 0.83, n * 0.86), (n * 0.17, n * 0.86)], fill=WHITE)
    return im, d


def sign_roundabout_ahead(n=256):
    """P-4 intersección con circulación giratoria."""
    im, d = _warning(n)
    c, cy, r = n / 2, n * 0.6, n * 0.12
    for k in range(3):
        a0 = math.radians(90 + 120 * k)
        pts = [(c + r * math.cos(a0 + t), cy - r * math.sin(a0 + t)) for t in np.linspace(0.3, 1.7, 10)]
        d.line(pts, fill=BLACK, width=int(n * 0.04))
    return im


def sign_crossing(n=256):
    """S-13 paso para peatones: cuadrado azul, triángulo blanco con peatón y cebra."""
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, n - 4, n - 4], fill=BLUE, outline=WHITE, width=4)
    d.polygon([(n / 2, n * 0.12), (n * 0.88, n * 0.84), (n * 0.12, n * 0.84)], fill=WHITE)
    for k in range(4):
        x0 = n * (0.28 + 0.12 * k)
        d.rectangle([x0, n * 0.74, x0 + n * 0.07, n * 0.8], fill=BLACK)
    hx, hy = n * 0.5, n * 0.36
    d.ellipse([hx - n * 0.035, hy - n * 0.035, hx + n * 0.035, hy + n * 0.035], fill=BLACK)
    d.line([(hx, hy + n * 0.04), (hx - n * 0.02, n * 0.56)], fill=BLACK, width=int(n * 0.035))
    d.line([(hx - n * 0.02, n * 0.56), (hx - n * 0.08, n * 0.7)], fill=BLACK, width=int(n * 0.03))
    d.line([(hx - n * 0.02, n * 0.56), (hx + n * 0.06, n * 0.7)], fill=BLACK, width=int(n * 0.03))
    d.line([(hx - n * 0.06, n * 0.46), (hx + n * 0.07, n * 0.44)], fill=BLACK, width=int(n * 0.03))
    return im


def sign_no_overtaking(n=256):
    """R-305 adelantamiento prohibido: anillo rojo, coche rojo y coche negro."""
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.ellipse([4, 4, n - 4, n - 4], fill=RED)
    d.ellipse([n * 0.12, n * 0.12, n * 0.88, n * 0.88], fill=WHITE)
    for cx, col in ((n * 0.37, RED), (n * 0.63, BLACK)):
        d.rounded_rectangle([cx - n * 0.09, n * 0.33, cx + n * 0.09, n * 0.68], radius=int(n * 0.04), fill=col)
        d.rectangle([cx - n * 0.06, n * 0.38, cx + n * 0.06, n * 0.46], fill=WHITE)
    return im


def sign_end_restriction(n=256):
    """R-502 fin de prohibiciones: círculo blanco con borde negro fino y franja diagonal."""
    im = _canvas(n)
    d = ImageDraw.Draw(im)
    d.ellipse([4, 4, n - 4, n - 4], fill=WHITE, outline=BLACK, width=3)
    for k in range(-2, 3):
        off = k * n * 0.035
        d.line([(n * 0.22 + off, n * 0.78 + off), (n * 0.78 + off, n * 0.22 + off)], fill=(60, 60, 60), width=3)
    return im


def sign_town(name, end=False, n=256):
    """S-500 entrada a poblado (blanco con borde negro y nombre) / S-510 fin (con franja roja)."""
    w = int(n * 2.2)
    im = _canvas(n, w)
    d = ImageDraw.Draw(im)
    d.rectangle([3, 3, w - 3, n - 3], fill=WHITE, outline=BLACK, width=6)
    size = int(n * 0.32)
    while size > 16:
        f = _font(size)
        try:
            tw = d.textlength(name, font=f)
        except Exception:  # noqa: BLE001
            tw = len(name) * size * 0.6
        if tw < w * 0.86:
            break
        size -= 4
    d.text((w / 2, n / 2), name, fill=BLACK, anchor="mm", font=_font(size))
    if end:
        d.line([(w * 0.08, n * 0.85), (w * 0.92, n * 0.15)], fill=RED, width=int(n * 0.07))
    return im


def chevron_panel(left, n=256):
    """Panel direccional: fondo azul y flechas blancas (en España), apuntando hacia la curva."""
    w = int(n * 1.6)
    im = Image.new("RGBA", (w, n), BLUE + (255,))
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, w - 3, n - 3], outline=WHITE, width=5)
    sx = -1 if left else 1
    for k in range(3):
        cx = w * (0.28 + 0.22 * k)
        d.polygon([(cx - sx * n * 0.12, n * 0.18), (cx + sx * n * 0.06, n * 0.5), (cx - sx * n * 0.12, n * 0.82),
                   (cx - sx * n * 0.02, n * 0.82), (cx + sx * n * 0.16, n * 0.5), (cx - sx * n * 0.02, n * 0.18)],
                  fill=WHITE)
    return im


def _mat(scene, name, img, alpha=True):
    if name not in scene.materials:
        t = f"{name}.png"
        scene.textures[t] = textures.png(img)
        scene.images[t] = img
        scene.material(name, t, alpha=alpha, ambient=0.6, diffuse=0.5, specular=0.2)
    return name


# ------------------------------------------------------------------ colocación de señales
def place_signs(net, scene, osm, proj, log=print):
    from .extras import _sign
    counts = {}

    def add(k):
        counts[k] = counts.get(k, 0) + 1

    def ok(e, s):
        return any(a + 1 <= s <= b - 1 for a, b in net.ranges(e))

    give = _mat(scene, "senal_ceda", sign_give_way())
    rb_sign = _mat(scene, "senal_glorieta", sign_roundabout())
    rb_ahead = _mat(scene, "senal_aviso_glorieta", sign_roundabout_ahead())
    cross = _mat(scene, "senal_peatones", sign_crossing())
    nopass = _mat(scene, "senal_no_adelantar", sign_no_overtaking())
    endres = _mat(scene, "senal_fin_prohibicion", sign_end_restriction())
    stop = "senal_alto"
    inc = net.incident()
    rb_nodes = set()
    for e in net.edges:
        if e.tags.get("junction") in ("roundabout", "circular"):
            rb_nodes |= {e.a, e.b}
    # 1. entradas de glorieta: ceda el paso + glorieta obligatoria; aviso antes en carretera
    for nid in rb_nodes:
        if not net.node_inside(nid):
            continue
        for e, end in inc.get(nid, []):
            if e.tags.get("junction") in ("roundabout", "circular"):
                continue
            if e.oneway and end == 0:
                continue                      # por esta calle se sale de la glorieta
            s = (e.s0 + 2.5) if end == 0 else (e.s1 - 2.5)
            if not ok(e, s):
                continue
            side, ts = (1, -1) if end == 0 else (-1, 1)
            _sign(scene, e, s, side, None, give, h=1.9, size=0.9, travel_sign=ts)
            _sign(scene, e, s, side, None, rb_sign, h=2.75, size=0.6, travel_sign=ts, post=False)
            paint_give_way(scene, e, (e.s0 + 0.6) if end == 0 else (e.s1 - 0.6), ts)
            add("glorieta")
            if not e.urban and e.length > 200:
                sa = (e.s0 + 150) if end == 0 else (e.s1 - 150)
                if ok(e, sa):
                    _sign(scene, e, sa, side, None, rb_ahead, travel_sign=ts)
                    add("aviso glorieta")
    # 2. ceda el paso y STOP de OSM (fuera de glorietas): en la calle que llega al cruce
    where = {}
    for e in net.edges:
        if e.nodes:
            tree = cKDTree(e.P[:, :2])
            pts = np.array([net.node_xy[n] for n in e.nodes])
            _, ii = tree.query(pts)
            for nid_, i in zip(e.nodes, ii):
                where.setdefault(nid_, []).append((e, float(e.s[i])))
    for nid, tags in net.node_tags.items():
        hw_ = tags.get("highway")
        if hw_ not in ("give_way", "stop") or nid not in where:
            continue
        e, s = where[nid][0]
        # cruce más cercano en esa calle: la señal mira a quien va hacia él
        to_a = s < e.length / 2
        side, ts = (1, -1) if to_a else (-1, 1)
        if ok(e, s) and not any(nid == x for x in rb_nodes):
            _sign(scene, e, s, side, None, give if hw_ == "give_way" else stop, h=1.9, size=0.85, travel_sign=ts)
            if hw_ == "give_way":
                paint_give_way(scene, e, (e.s0 + 0.6) if to_a else (e.s1 - 0.6), ts)
            add("ceda" if hw_ == "give_way" else "stop")
    # 3. pasos de peatones: S-13 a ambos lados
    for nid, tags in net.node_tags.items():
        if not (tags.get("highway") == "crossing" or "crossing" in tags) or nid not in where:
            continue
        for e, s in where[nid][:1]:
            for side, ts, ds in ((-1, 1, -1.5), (1, -1, 1.5)):
                if ts == -1 and e.oneway:
                    continue
                if ok(e, s + ds):
                    _sign(scene, e, s + ds, side, None, cross, h=2.1, size=0.6, travel_sign=ts)
            add("peatones")
    # 4. prohibido adelantar / fin de prohibición (desde las zonas de raya continua)
    for e, s_a, s_b in getattr(net, "nopass", []):
        for sp, side, ts, mat in ((s_a, -1, 1, nopass), (s_b, -1, 1, endres), (s_b, 1, -1, nopass), (s_a, 1, -1, endres)):
            if ts == -1 and e.oneway:
                continue
            if ok(e, sp):
                _sign(scene, e, sp, side, None, mat, travel_sign=ts)
        add("prohibido adelantar")
    # 5. entrada y salida de poblado: donde una calle pasa de rural a urbana siguiendo recto
    places = [n for n in osm["nodes"] if n["tags"].get("place") in ("city", "town", "village", "hamlet", "suburb")
              and n["tags"].get("name")]
    if places:
        px, py = proj.to_local([n["lon"] for n in places], [n["lat"] for n in places])
        ptree = cKDTree(np.column_stack([px, py]))
        for nid, lst in inc.items():
            if not net.node_inside(nid):
                continue
            rur = [(e, end) for e, end in lst if not e.urban and e.cls in ("primary", "secondary", "tertiary", "unclassified", "trunk")]
            urb = [(e, end) for e, end in lst if e.urban]
            if not rur or not urb:
                continue
            e, end = urb[0]
            d, j = ptree.query(net.node_xy[nid])
            if d > 3500:
                continue
            name = places[j]["tags"]["name"]
            slug = "".join(ch for ch in name.lower() if ch.isalnum())[:24]
            enter = _mat(scene, f"senal_poblado_{slug}", sign_town(name))
            leave = _mat(scene, f"senal_fin_poblado_{slug}", sign_town(name, end=True))
            s = (e.s0 + 15) if end == 0 else (e.s1 - 15)
            if ok(e, s):
                side, ts = (1, -1) if end == 0 else (-1, 1)
                ts_in = -ts                   # quien entra al pueblo circula alejándose del nodo
                _sign(scene, e, s, -side, None, enter, h=1.9, size=0.6, aspect=2.2, travel_sign=ts_in)
                _sign(scene, e, s, side, None, leave, h=1.9, size=0.6, aspect=2.2, travel_sign=ts)
                add("poblado")
    # 6. paneles direccionales en el exterior de curvas cerradas (carretera)
    pl = _mat(scene, "panel_curva_izq", chevron_panel(True))
    pr = _mat(scene, "panel_curva_der", chevron_panel(False))
    from scipy.ndimage import gaussian_filter1d
    for e in net.edges:
        if e.urban or e.length < 80 or e.cls not in ("primary", "secondary", "tertiary", "unclassified", "trunk"):
            continue
        h = np.unwrap(np.arctan2(e.T[:, 1], e.T[:, 0]))
        kap = gaussian_filter1d(np.gradient(h, e.s), 6, mode="nearest")
        sharp = np.abs(kap) > 1 / 110.0
        i = 0
        while i < len(sharp):
            if not sharp[i]:
                i += 1
                continue
            j = i
            while j < len(sharp) and sharp[j]:
                j += 1
            s_in, s_out = e.s[i], e.s[j - 1]
            if s_out - s_in > 15:
                left = float(np.mean(kap[i:j])) > 0
                outer = -1 if left else 1         # exterior de la curva
                for sp in np.arange(s_in + 5, s_out, 16.0):
                    if ok(e, sp):
                        # una placa por sentido, en el lado exterior, mirando a cada sentido
                        _sign(scene, e, sp, outer, None, pl if left else pr, h=1.1, size=0.5, aspect=1.6,
                              travel_sign=1, extra_o=1.6)
                        if not e.oneway:
                            _sign(scene, e, sp, outer, None, pr if left else pl, h=1.1, size=0.5, aspect=1.6,
                                  travel_sign=-1, post=False, extra_o=1.6)
                        add("paneles curva")
            i = j
    log("  laterales: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


# ------------------------------------------------------------------ marcas viales de ceda el paso
def paint_give_way(scene, e, s_line, ts):
    """Línea de ceda el paso discontinua (M-4.2) y triángulo alargado (M-6.5) en el carril de
    entrada, como en la carretera real. ts: sentido de quien llega (+1 = hacia s creciente)."""
    from .roads import DZ_RAYA
    hw = e.hw
    if e.oneway:
        o0, o1 = -hw + 0.25, hw - 0.25
    else:
        o0, o1 = (-hw + 0.25, -0.15) if ts == 1 else (0.15, hw - 0.25)
    pos, faces = [], []

    def poly(pts):
        base = len(pos)
        pos.extend(pts)
        for k in range(1, len(pts) - 1):
            faces.append((base, base + k, base + k + 1))
    back = -ts                           # "antes de la línea" para quien llega
    o = o0
    while o + 0.6 < o1 + 0.01:           # trazos de 0.8 m con huecos de 0.4 m, 0.4 m de ancho
        oa, ob = o, min(o + 0.8, o1)
        sa, sb = s_line, s_line + back * 0.4
        poly([e.at(sa, oa), e.at(sa, ob), e.at(sb, ob), e.at(sb, oa)])
        o += 1.2
    oc = (o0 + o1) / 2
    s_apex, s_base = s_line + back * 2.0, s_line + back * 5.6
    poly([e.at(s_apex, oc), e.at(s_base, oc + 0.6), e.at(s_base, oc - 0.6)])
    if not faces:
        return
    P = np.array(pos)
    P[:, 2] += DZ_RAYA
    F = np.array(faces)
    tri = P[F]
    up = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2]
    F[up < 0] = F[up < 0][:, ::-1]
    uv = np.zeros((len(P), 2))
    scene.add("rayas", "raya_blanca", P, uv, F)


# ------------------------------------------------------------------ torres de alta tensión
def pylon(scene, x, y, z, yaw, H=28.0, arm=5.5):
    """Torre de celosía: tronco piramidal con caras de celosía (recorte alfa, doble cara),
    crucetas y remate. Colisión en la base."""
    c, s = math.cos(yaw), math.sin(yaw)

    def corner(hx, hy, zz):
        return (x + c * hx - s * hy, y + s * hx + c * hy, zz)
    levels = [(0.0, 3.0), (H * 0.8, 0.9), (H, 0.55)]
    pos, uv, faces = [], [], []
    for (z0, a0), (z1, a1) in zip(levels[:-1], levels[1:]):
        for (sx0, sy0), (sx1, sy1) in (((-1, -1), (1, -1)), ((1, -1), (1, 1)), ((1, 1), (-1, 1)), ((-1, 1), (-1, -1))):
            base = len(pos)
            pos += [corner(sx0 * a0, sy0 * a0, z + z0), corner(sx1 * a0, sy1 * a0, z + z0),
                    corner(sx1 * a1, sy1 * a1, z + z1), corner(sx0 * a1, sy0 * a1, z + z1)]
            vt = (z1 - z0) / 4.0
            uv += [(0, 0), (1, 0), (1, vt), (0, vt)]
            faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    P = np.array(pos)
    F = np.array(faces)
    F = np.concatenate([F, F[:, ::-1]])
    scene.add("tendido", "celosia", P, np.array(uv, float), F)
    for hz, w in ((H * 0.72, arm), (H * 0.88, arm * 0.8)):
        p, u, f = box_mesh((x, y, z + hz), (0.5, 2 * w, 0.6), yaw)
        scene.add("tendido", "torre", p, u, f)
    p, u, f = box_mesh((x, y, z - 0.5), (6.0, 6.0, 3.0), yaw)
    scene.add("col", "invisible", p, u, f, phys="WALL")


# ------------------------------------------------------------------ guardarraíl bionda
_BIONDA = np.array([  # perfil de la bionda (doble onda), (desplazamiento lateral, altura) en m
    (0.00, 0.44), (0.05, 0.47), (0.09, 0.52), (0.05, 0.57), (0.00, 0.60),
    (0.05, 0.63), (0.09, 0.68), (0.05, 0.73), (0.00, 0.76)])


def bionda(scene, pts, side_normals, post_every=4.0, terminal=8.0, group="defensa"):
    """Guardarraíl de doble onda a lo largo de `pts` (N x 3, base sobre el terreno o la orilla).

    side_normals: N x 2, hacia la calzada (la onda sobresale hacia ella). Extremos abatidos:
    los últimos `terminal` metros bajan hasta el suelo, como los abatimientos reales.
    """
    pts = np.asarray(pts, float)
    if len(pts) < 2:
        return 0.0
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))])
    L = s[-1]
    if L < 4:
        return 0.0
    drop = np.clip(np.minimum(s, L - s) / terminal, 0, 1)        # 0 en los extremos, 1 en medio
    drop = drop * drop * (3 - 2 * drop)
    nrm = np.asarray(side_normals, float)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    k = len(_BIONDA)
    n = len(pts)
    pos = np.zeros((n, k, 3))
    for j, (off, hgt) in enumerate(_BIONDA):
        pos[:, j, :2] = pts[:, :2] + nrm * off
        pos[:, j, 2] = pts[:, 2] + (hgt - 0.1) * drop + 0.1 * drop + (-0.05) * (1 - drop)
    P = pos.reshape(-1, 3)
    uv = np.column_stack([np.repeat(s / 4.0, k), np.tile(np.linspace(0, 1, k), n)])
    faces = []
    for i in range(n - 1):
        for j in range(k - 1):
            a, b = i * k + j, i * k + j + 1
            c, d = (i + 1) * k + j + 1, (i + 1) * k + j
            faces += [(a, d, c), (a, c, b)]
    faces = np.array(faces)
    both = np.concatenate([faces, faces[:, ::-1]])
    scene.add(group, "defensa", P, uv, both, phys="WALL")
    # postes de perfil C cada 4 m (solo donde la bionda está a altura completa)
    for sp in np.arange(post_every / 2, L, post_every):
        i = int(np.searchsorted(s, sp))
        i = min(max(i, 1), n - 1)
        if drop[i] < 0.95:
            continue
        d = pts[i, :2] - pts[i - 1, :2]
        yaw = math.atan2(d[1], d[0])
        c = pts[i, :2] - nrm[i] * 0.12
        p, puv, pf = box_mesh((c[0], c[1], pts[i, 2] - 0.5), (0.06, 0.12, 1.2), yaw)
        scene.add(group, "poste", p, puv, pf)
    return L


# ------------------------------------------------------------------ tendido eléctrico
def power_lines(scene, osm, proj, terrain, rect):
    """Postes (línea de baja/media) o torres (alta) en cada vértice de la línea y 3 cables en catenaria."""
    xmin, ymin, xmax, ymax = rect
    n_sup = 0
    for ln in osm.get("power", []):
        tall = ln["tags"].get("power") == "line"
        x, y = proj.to_local([c[0] for c in ln["coords"]], [c[1] for c in ln["coords"]])
        P = np.column_stack([x, y])
        inside = (P[:, 0] > xmin + 2) & (P[:, 0] < xmax - 2) & (P[:, 1] > ymin + 2) & (P[:, 1] < ymax - 2)
        if inside.sum() < 2:
            continue
        P = P[inside]
        z = terrain.height(P[:, 0], P[:, 1])
        H = 28.0 if tall else 9.0
        arm = 5.5 if tall else 0.8
        tops = []
        for i in range(len(P)):
            d = P[min(i + 1, len(P) - 1)] - P[max(i - 1, 0)]
            yaw = math.atan2(d[1], d[0])
            if tall:
                pylon(scene, P[i, 0], P[i, 1], z[i], yaw, H=H, arm=arm)
            else:
                p, uv, f = box_mesh((P[i, 0], P[i, 1], z[i] - 0.5), (0.25, 0.25, H + 0.5), yaw)
                scene.add("tendido", "poste_madera", p, uv, f, phys="WALL")
            nx, ny = -math.sin(yaw), math.cos(yaw)
            p, uv, f = box_mesh((P[i, 0], P[i, 1], z[i] + H - 0.3), (0.12, 2 * arm, 0.12), yaw)
            scene.add("tendido", "poste", p, uv, f)
            tops.append([(P[i, 0] + nx * o, P[i, 1] + ny * o, z[i] + H - 0.15) for o in (-arm, 0.0, arm)])
            n_sup += 1
        # cables: 3 por vano, con flecha (catenaria aproximada por parábola)
        for i in range(len(tops) - 1):
            for c in range(3):
                a, b = np.array(tops[i][c]), np.array(tops[i + 1][c])
                span = np.linalg.norm(b[:2] - a[:2])
                t = np.linspace(0, 1, 12)
                sag = (0.02 if not tall else 0.03) * span
                line = a[None, :] * (1 - t)[:, None] + b[None, :] * t[:, None]
                line[:, 2] -= 4 * sag * t * (1 - t)
                p, uv, f = wall_strip(line - np.array([0, 0, 0.015]), 0.03, u_scale=10, v_scale=1, both=True)
                scene.add("tendido", "cable", p, uv, f)
    return n_sup


# ------------------------------------------------------------------ vallas de malla
def fences(scene, osm, proj, terrain, rect, road_union=None, max_dist=120.0):
    """Valla de malla de 1.6 m (textura con postes y alambre, recorte alfa), solo cerca de vías."""
    xmin, ymin, xmax, ymax = rect
    box = shapely.box(xmin + 1, ymin + 1, xmax - 1, ymax - 1)
    near = road_union.buffer(max_dist) if road_union is not None else box
    keep_out = road_union.buffer(0.8) if road_union is not None else None
    total = 0.0
    for b in osm.get("barriers", []):
        if b["tags"].get("barrier") != "fence":
            continue
        x, y = proj.to_local([c[0] for c in b["coords"]], [c[1] for c in b["coords"]])
        line = shapely.LineString(np.column_stack([x, y])).intersection(box).intersection(near)
        if keep_out is not None:
            line = line.difference(keep_out)
        for g in getattr(line, "geoms", [line]):
            if g.is_empty or g.geom_type != "LineString" or g.length < 3:
                continue
            pts = np.asarray(shapely.segmentize(g, 2.5).coords)[:, :2]
            z = terrain.height(pts[:, 0], pts[:, 1]) - 0.05
            p, uv, f = wall_strip(np.column_stack([pts, z]), 1.6, u_scale=2.5, v_scale=1.6, both=True)
            uv[:, 1] = np.where(uv[:, 1] > 0.5, 0.0, 1.0)     # alto de la textura = alto de la valla
            scene.add("vallas_malla", "malla", p, uv, f, phys="WALL")
            total += g.length
    return total


def register_materials(scene):
    """Materiales de este módulo (cables, torres, postes de madera, malla)."""
    def mat(name, img, **kw):
        if name not in scene.materials:
            t = f"{name}.png"
            scene.textures[t] = textures.png(img)
            scene.images[t] = img
            scene.material(name, t, **kw)
    mat("cable", Image.new("RGB", (8, 8), (40, 40, 42)), ambient=0.4, diffuse=0.3)
    mat("torre", textures.paint((150, 152, 150), 64), ambient=0.45, diffuse=0.5, specular=0.3)
    mat("poste_madera", textures.paint((96, 80, 62), 64), ambient=0.45, diffuse=0.5)
    # celosía de torre: montantes en los bordes y diagonales en X (recorte alfa)
    n = 256
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    g = (150, 154, 156, 255)
    d.rectangle([0, 0, 10, n], fill=g)
    d.rectangle([n - 11, 0, n, n], fill=g)
    d.line([(0, 0), (n, n)], fill=g, width=7)
    d.line([(n, 0), (0, n)], fill=g, width=7)
    d.rectangle([0, 0, n, 6], fill=g)
    mat("celosia", im, alpha=True, ambient=0.5, diffuse=0.5, specular=0.3)
    # malla: postes cada 2.5 m (bordes de la textura) y alambre en rombo
    n = 256
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k in range(-n, 2 * n, 14):
        d.line([(k, 0), (k + n, n)], fill=(150, 150, 146, 255), width=1)
        d.line([(k, n), (k + n, 0)], fill=(150, 150, 146, 255), width=1)
    d.rectangle([0, 0, 6, n], fill=(110, 112, 110, 255))
    d.line([(0, 2), (n, 2)], fill=(120, 120, 118, 255), width=3)
    mat("malla", im, alpha=True, ambient=0.5, diffuse=0.5, specular=0.2)
