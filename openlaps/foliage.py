"""Sprites de árboles pintados por algoritmo (sin fotos ni IA), para planos cruzados.

Antes cada copa eran círculos planos de un solo color: se veían recortados y de caricatura.
Aquí la copa se arma con cientos de racimos de hojas, cada uno sombreado como una esfera con
luz desde arriba a la izquierda, más:
- oclusión: el interior y la parte baja de la copa son más oscuros;
- textura de hoja de alta frecuencia y huecos en la orilla (deja ver el cielo, como un árbol real);
- ramas visibles entrando en la copa y tronco con luz lateral;
- variantes por especie (semilla distinta), para que no se repita el mismo árbol.
El alfa queda en 0/255 (alpha test limpio) y el color se extiende bajo las zonas transparentes
(ver dds.bleed) para que no salgan orillas oscuras.
"""
import math

import numpy as np
from PIL import Image, ImageDraw

LIGHT = np.array([-0.45, -0.65, 0.62])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

# especie -> (color base de hoja RGB, variación, forma)
LEAF = {
    "encino":   ((62, 84, 40), 0.22),
    "tropical": ((58, 104, 42), 0.25),
    "pino":     ((34, 62, 44), 0.18),
    "arbusto":  ((86, 100, 54), 0.25),
    "olivo":    ((98, 108, 76), 0.15),    # verde grisáceo plateado
}


def _value_noise(n, cell, rng):
    """Ruido suave (0-1) de n x n con celdas de `cell` pixeles."""
    m = int(math.ceil(n / cell)) + 2
    g = rng.random((m, m))
    y, x = np.mgrid[0:n, 0:n] / cell
    x0, y0 = x.astype(int), y.astype(int)
    fx, fy = x - x0, y - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = g[y0, x0] * (1 - fx) + g[y0, x0 + 1] * fx
    b = g[y0 + 1, x0] * (1 - fx) + g[y0 + 1, x0 + 1] * fx
    return a * (1 - fy) + b * fy


class _Canvas:
    def __init__(self, n, rng):
        self.n = n
        self.rgb = np.zeros((n, n, 3), np.float32)
        self.a = np.zeros((n, n), np.float32)
        self.leaf = 0.75 + 0.5 * _value_noise(n, max(2, n // 128), rng) * _value_noise(n, max(3, n // 64), rng) * 1.6
        self.rng = rng

    def blob(self, cx, cy, rx, ry, color, depth, holes=0.0):
        """Racimo elíptico sombreado como esfera. depth 0 (atrás) .. 1 (frente)."""
        n = self.n
        x0, x1 = int(max(0, cx - rx - 1)), int(min(n, cx + rx + 2))
        y0, y1 = int(max(0, cy - ry - 1)), int(min(n, cy + ry + 2))
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dx, dy = (xx - cx) / rx, (yy - cy) / ry
        d2 = dx * dx + dy * dy
        leaf = self.leaf[y0:y1, x0:x1]
        edge = 1.0 - 0.28 * (leaf - 0.75)          # orilla irregular, hecha de hojas
        inside = d2 < edge * edge
        if holes > 0:
            inside &= ~((d2 > 0.45) & (self.rng.random(d2.shape) < holes))
        if not inside.any():
            return
        nz = np.sqrt(np.clip(1.0 - d2, 0.0, 1.0))
        shade = np.clip(dx * LIGHT[0] + dy * LIGHT[1] + nz * LIGHT[2], 0.0, 1.0)
        k = (0.42 + 0.7 * shade) * (0.62 + 0.38 * depth) * leaf
        c = np.asarray(color, np.float32)[None, None, :] * k[..., None]
        sel = inside
        self.rgb[y0:y1, x0:x1][sel] = c[sel]
        self.a[y0:y1, x0:x1][sel] = 1.0

    def image(self):
        out = np.zeros((self.n, self.n, 4), np.uint8)
        out[..., :3] = np.clip(self.rgb, 0, 255).astype(np.uint8)
        out[..., 3] = np.where(self.a > 0.5, 255, 0)
        return out


def _trunk(d, n, x_base, y_top, width, color=(84, 64, 46)):
    """Tronco con luz lateral (franja clara a la izquierda) y raíz más ancha."""
    w = width
    for i in range(int(w)):
        t = i / max(w - 1, 1)
        k = 1.15 - 0.6 * t
        col = tuple(int(min(255, c * k)) for c in color)
        x = x_base - w / 2 + i
        d.line([(x - w * 0.15 * (1 - t), n), (x, y_top)], fill=col + (255,), width=1)


def _branches(d, n, rng, x_base, y_from, crown_cx, crown_cy, crown_r, count, color=(78, 60, 44)):
    for _ in range(count):
        a = rng.uniform(-2.4, -0.7)
        L = crown_r * rng.uniform(0.45, 0.9)
        sx = x_base + rng.normal(0, 2)
        sy = y_from - rng.uniform(0, crown_r * 0.4)
        ex = crown_cx + math.cos(a) * L * rng.choice([-1, 1]) * 0.9
        ey = crown_cy + math.sin(a) * L * 0.5
        d.line([(sx, sy), ((sx + ex) / 2, (sy + ey) / 2 - crown_r * 0.05), (ex, ey)],
               fill=color + (255,), width=max(2, int(n * 0.008)), joint="curve")


def _crown_points(rng, count, cx, cy, rx, ry, flat_bottom=0.55, lobes=5):
    """Puntos de una copa hecha de varios lóbulos (no una esfera perfecta), aplanada abajo."""
    lc = []
    for i in range(lobes):
        a = 2 * math.pi * i / lobes + rng.normal(0, 0.4)
        rr = rng.uniform(0.35, 0.6)
        lc.append((math.cos(a) * rr, rng.uniform(-0.45, 0.3), rng.uniform(0.45, 0.65)))
    lc.append((0.0, 0.0, 0.75))
    pts = []
    while len(pts) < count:
        ux, uy, ur = lc[rng.integers(len(lc))]
        u, v = rng.normal(0, 0.5, 2) * ur
        if u * u + v * v > ur * ur:
            continue
        u, v = ux + u, uy + v
        if u * u + v * v > 1.0:
            continue
        if v > flat_bottom:   # la copa se aplana abajo
            v = flat_bottom + (v - flat_bottom) * 0.35
        pts.append((cx + u * rx, cy + v * ry))
    return np.array(pts)


def broadleaf(kind, n=512, variant=0):
    rng = np.random.default_rng(1000 + 37 * variant + sum(map(ord, kind)))
    base, var = LEAF[kind]
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "arbusto":
        cx, cy, rx, ry = n * 0.5, n * 0.66, n * 0.42, n * 0.3
        trunk_top = None
        nblob, rblob = 650, (0.02, 0.045)
    else:
        lean = rng.normal(0, 0.03) * n
        cx, cy = n * 0.5 + lean, n * (0.47 if kind == "encino" else (0.5 if kind == "olivo" else 0.44))
        rx = n * (0.47 if kind == "encino" else 0.48) * rng.uniform(0.88, 1.0)
        ry = n * (0.44 if kind == "encino" else 0.4) * rng.uniform(0.9, 1.05)
        trunk_top = cy + ry * 0.1
        _trunk(d, n, n * 0.5, trunk_top, n * 0.065)
        _branches(d, n, rng, n * 0.5, trunk_top, cx, cy, rx, 9)
        nblob, rblob = 900, (0.018, 0.04)
    cv = _Canvas(n, rng)
    # ramas y tronco ya dibujados: se pasan al lienzo como base
    base_img = np.asarray(img).astype(np.float32)
    m = base_img[..., 3] > 0
    cv.rgb[m] = base_img[m, :3]
    cv.a[m] = 1.0
    pts = _crown_points(rng, nblob, cx, cy, rx, ry)
    # de atrás hacia adelante: profundidad aleatoria, los del frente al final
    depth = rng.random(len(pts))
    order = np.argsort(depth)
    for i in order:
        x, y = pts[i]
        r = rng.uniform(*rblob) * n
        tint = 1.0 + rng.normal(0, var)
        col = (base[0] * tint * rng.uniform(0.92, 1.08), base[1] * tint, base[2] * tint * rng.uniform(0.9, 1.1))
        # oclusión: el interior y la parte baja más oscuros
        rel = math.hypot((x - cx) / rx, (y - cy) / ry)
        low = np.clip((y - (cy - ry)) / (2 * ry), 0, 1)
        occl = 0.55 + 0.45 * min(1.0, rel * 0.6 + depth[i] * 0.6) - 0.18 * low
        cv.blob(x, y, r * rng.uniform(1.0, 1.35), r, col, max(0.0, occl), holes=0.12)
    return Image.fromarray(cv.image(), "RGBA")


def pine(n=512, variant=0):
    """Pino / oyamel: cono de pisos de ramas colgantes, verde azulado oscuro."""
    rng = np.random.default_rng(2000 + 53 * variant)
    base, var = LEAF["pino"]
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = n * 0.5 + rng.normal(0, 0.01) * n
    top, bottom = n * 0.03, n * 0.86
    _trunk(d, n, cx, n * 0.2, n * 0.045, color=(74, 54, 40))
    cv = _Canvas(n, rng)
    base_img = np.asarray(img).astype(np.float32)
    m = base_img[..., 3] > 0
    cv.rgb[m] = base_img[m, :3]
    cv.a[m] = 1.0
    tiers = rng.integers(12, 17)
    for t in range(tiers):
        f = (t + 1) / tiers
        y_t = top + (bottom - top) * f
        half = n * (0.04 + 0.36 * f ** 0.9) * rng.uniform(0.85, 1.1)
        # cada piso: ramas que salen del tronco y cuelgan hacia la punta
        nb = int(40 + 110 * f)
        for _ in range(nb):
            side = rng.choice([-1, 1])
            u = rng.random() ** 0.7
            x = cx + side * u * half
            y = y_t - n * 0.05 * (1 - u) + n * 0.03 * u * u + rng.normal(0, n * 0.014)
            r = n * rng.uniform(0.012, 0.024) * (0.7 + 0.5 * f)
            tint = 1.0 + rng.normal(0, var)
            col = (base[0] * tint, base[1] * tint, base[2] * tint * rng.uniform(0.95, 1.15))
            depth = 0.35 + 0.65 * (1 - abs(u - 0.6)) * rng.uniform(0.7, 1.0) - 0.15 * (1 - f)
            cv.blob(x, y, r * 1.9, r * 0.75, col, depth, holes=0.05)
    return Image.fromarray(cv.image(), "RGBA")


def shade_flat(img, strength=0.35, seed=0):
    """Para los sprites dibujados con PIL (palma, órgano, maguey): luz de izquierda a derecha y
    textura, para que no se vean como recortes planos."""
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    n = a.shape[1]
    rng = np.random.default_rng(seed)
    grad = 1.0 + strength * (0.5 - np.linspace(0, 1, n))[None, :]
    noise = 0.88 + 0.24 * _value_noise(a.shape[0], max(2, n // 100), rng)
    a[..., :3] *= (grad * noise)[..., None]
    a[..., :3] = np.clip(a[..., :3], 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


# ------------------------------------------------------------------ hojas fotográficas (CC0)
# Hojas reales recortadas de ambientCG (CC0): cada racimo de la copa se rellena con hojas de
# verdad, giradas, escaladas y sombreadas según su posición; el tronco lleva corteza fotográfica.
LEAF_ASSETS = {
    "encino": ("LeafSet026", (58, 74, 40), 0.024),      # hoja dentada, oscurecida: encina / roble
    "tropical": ("LeafSet010", (70, 104, 46), 0.03),    # hoja tipo arce: plátano de sombra
    "arbusto": ("LeafSet026", (92, 104, 58), 0.022),
    "olivo": ("LeafSet026", (104, 112, 82), 0.018),     # hoja pequeña gris verdosa
    "pino": ("PineNeedles001", (40, 66, 44), 0.05),     # agujas (vienen secas: se recolorean)
}
_STAMPS = {}
_BARK = {}
SPRITE_VERSION = "foto1"


def _leaf_stamps(asset):
    """Separa las hojas de un atlas CC0 (componentes conexas del alfa) -> lista de arrays RGBA."""
    if asset in _STAMPS:
        return _STAMPS[asset]
    from scipy import ndimage
    from .sources.cc0 import load_cutout
    im = load_cutout(asset, log=lambda *a: None)
    out = []
    if im is not None:
        a = np.asarray(im).astype(np.float32)
        lab, n = ndimage.label(a[..., 3] > 40)
        for sl in ndimage.find_objects(lab):
            crop = a[sl]
            if crop.shape[0] * crop.shape[1] < 400:
                continue
            out.append(crop)
    _STAMPS[asset] = out
    return out


def _bark(n):
    if n in _BARK:
        return _BARK[n]
    from .sources.cc0 import load
    r = load("Bark012", 1024, log=lambda *a: None)
    _BARK[n] = None if r is None else np.asarray(r[0].resize((n // 4, n)), np.float32)
    return _BARK[n]


def _prep(stamp, size_px, angle, rgb_target):
    """Escala y gira una hoja, y la retiñe al color objetivo conservando su detalle."""
    im = Image.fromarray(np.clip(stamp, 0, 255).astype(np.uint8), "RGBA")
    s = size_px / max(im.size)
    im = im.resize((max(2, int(im.size[0] * s)), max(2, int(im.size[1] * s))), Image.LANCZOS)
    im = im.rotate(angle, expand=True, resample=Image.BICUBIC)
    a = np.asarray(im).astype(np.float32)
    lum = a[..., :3].mean(-1, keepdims=True)
    m = max(float(lum[a[..., 3] > 40].mean()) if (a[..., 3] > 40).any() else 1.0, 1.0)
    a[..., :3] = (lum / m) * np.asarray(rgb_target, np.float32)
    return a


class _PhotoCanvas(_Canvas):
    def __init__(self, n, rng, stamps, leaf_px):
        super().__init__(n, rng)
        self.variants = []
        for st in stamps:
            for ang in (0, 60, 120, 180, 240, 300):
                for sc in (0.75, 1.0, 1.3):
                    self.variants.append(_prep(st, leaf_px * sc, ang + rng.normal(0, 12), (128, 128, 128)))

    def blob(self, cx, cy, rx, ry, color, depth, holes=0.0):
        if not self.variants:
            return super().blob(cx, cy, rx, ry, color, depth, holes)
        n = self.n
        area = math.pi * rx * ry
        k = int(np.clip(area / 140.0, 2, 30))
        col = np.asarray(color, np.float32) / 128.0
        for _ in range(k):
            u, v = self.rng.normal(0, 0.45, 2)
            if u * u + v * v > 1.0:
                continue
            x, y = cx + u * rx, cy + v * ry
            nz = math.sqrt(max(0.0, 1 - u * u - v * v))
            shade = max(0.0, u * LIGHT[0] + v * LIGHT[1] + nz * LIGHT[2])
            kk = (0.45 + 0.7 * shade) * (0.62 + 0.38 * depth)
            st = self.variants[int(self.rng.integers(len(self.variants)))]
            h, w = st.shape[:2]
            x0, y0 = int(x - w / 2), int(y - h / 2)
            xa, ya = max(0, x0), max(0, y0)
            xb, yb = min(n, x0 + w), min(n, y0 + h)
            if xb <= xa or yb <= ya:
                continue
            sub = st[ya - y0:yb - y0, xa - x0:xb - x0]
            al = (sub[..., 3:4] / 255.0)
            al = np.where(al > 0.35, 1.0, 0.0)          # alfa recortado (alpha test limpio)
            c = sub[..., :3] * col * kk
            dst = self.rgb[ya:yb, xa:xb]
            dst[:] = c * al + dst * (1 - al)
            self.a[ya:yb, xa:xb] = np.maximum(self.a[ya:yb, xa:xb], al[..., 0])


def _bark_trunk(cv, n, x_base, y_top, width, mask=None):
    """Pega corteza fotográfica sobre el tronco ya dibujado (con luz lateral), solo donde `mask`."""
    bark = _bark(n)
    if bark is None:
        return
    x0, x1 = int(x_base - width / 2), int(x_base + width / 2)
    y0 = int(y_top)
    if x1 <= x0 or y0 >= n:
        return
    w = x1 - x0
    strip = np.asarray(Image.fromarray(bark.astype(np.uint8)).resize((w, n - y0)), np.float32)
    lit = (1.15 - 0.55 * np.linspace(0, 1, w))[None, :, None]
    sel = cv.a[y0:n, x0:x1] > 0
    if mask is not None:
        sel &= mask[y0:n, x0:x1]
    region = cv.rgb[y0:n, x0:x1]
    region[sel] = (strip * lit * 0.85)[sel]


def photo_sprite(kind, n=512, variant=0):
    """Sprite con hojas fotográficas; None si no hay hojas CC0 (sin internet)."""
    asset, base, leaf_rel = LEAF_ASSETS[kind]
    stamps = _leaf_stamps(asset)
    if not stamps:
        return None
    rng = np.random.default_rng(3000 + 41 * variant + sum(map(ord, kind)))
    leaf_px = leaf_rel * n
    if kind == "pino":
        img = pine(n, variant)              # silueta y pisos del pino procedural...
        cv = _PhotoCanvas(n, rng, stamps, leaf_px)
        a = np.asarray(img).astype(np.float32)
        m = a[..., 3] > 0
        cv.rgb[m] = a[m, :3]
        cv.a[m] = 1.0
        trunk_m = m & (a[..., 0] > 60) & (a[..., 0] > a[..., 1])
        _bark_trunk(cv, n, n * 0.5, n * 0.2, n * 0.045, trunk_m)
        # ...cubiertos de mechones de agujas reales
        ys, xs = np.nonzero(m)
        for _ in range(2600):
            i = int(rng.integers(len(xs)))
            x, y = xs[i], ys[i]
            if a[y, x, 0] > 70 and a[y, x, 2] < 60:   # no sobre el tronco
                continue
            tint = 1.0 + rng.normal(0, 0.15)
            cv.blob(x, y, n * 0.018, n * 0.012, (base[0] * tint, base[1] * tint, base[2] * tint), 0.5 + 0.5 * rng.random())
        return Image.fromarray(cv.image(), "RGBA")
    # frondosas: la misma copa por racimos, pero cada racimo hecho de hojas reales
    proc = broadleaf(kind, n, variant)
    cv = _PhotoCanvas(n, rng, stamps, leaf_px)
    a = np.asarray(proc).astype(np.float32)
    trunk = (a[..., 3] > 0) & (a[..., 0] > a[..., 1])          # tronco y ramas (marrones)
    cv.rgb[trunk] = a[trunk, :3]
    cv.a[trunk] = 1.0
    if kind != "arbusto":
        _bark_trunk(cv, n, n * 0.5, 0, n * 0.065, trunk)
    leaves = (a[..., 3] > 0) & ~trunk
    ys, xs = np.nonzero(leaves)
    if len(xs) == 0:
        return proc
    cx, cy = xs.mean(), ys.mean()
    rx, ry = (xs.max() - xs.min()) / 2, (ys.max() - ys.min()) / 2
    cnt = int(len(xs) / (leaf_px * leaf_px) * 0.9)
    for _ in range(min(cnt, 1800)):
        i = int(rng.integers(len(xs)))
        x, y = xs[i], ys[i]
        rel = math.hypot((x - cx) / max(rx, 1), (y - cy) / max(ry, 1))
        low = (y - (cy - ry)) / max(2 * ry, 1)
        depth = float(np.clip(0.4 + 0.5 * rel - 0.2 * low + rng.normal(0, 0.12), 0, 1))
        tint = 1.0 + rng.normal(0, 0.12)
        cv.blob(x, y, leaf_px * 1.6, leaf_px * 1.3, (base[0] * tint, base[1] * tint, base[2] * tint), depth)
    return Image.fromarray(cv.image(), "RGBA")
