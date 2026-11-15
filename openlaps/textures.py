"""Texturas generadas por algoritmo (sin fotos ni IA): ruido, Voronoi y dibujo vectorial."""
import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .dds import normal_map

RNG = np.random.default_rng(1234)


def png(img):
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


def tile_noise(n, scale, rng, octaves=4):
    """Ruido de valor periódico (se repite sin costuras)."""
    out = np.zeros((n, n), np.float32)
    amp, tot = 1.0, 0.0
    s = scale
    for _ in range(octaves):
        g = rng.random((s, s)).astype(np.float32)
        img = Image.fromarray((g * 255).astype(np.uint8))
        # remuestreo periódico: se rodea la rejilla para que empalme
        big = np.tile(np.asarray(img), (3, 3))
        im = Image.fromarray(big).resize((n * 3, n * 3), Image.BICUBIC)
        out += amp * (np.asarray(im, np.float32)[n:2 * n, n:2 * n] / 255.0)
        tot += amp
        amp *= 0.5
        s *= 2
    return out / tot


def _rgb(base, var, noise):
    base = np.asarray(base, np.float32)
    a = base[None, None, :] * (1 + var * (noise[..., None] - 0.5) * 2)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def asphalt(n=512, rng=RNG, tone=88):
    nz = tile_noise(n, 8, rng, 5)
    speck = rng.random((n, n)) ** 30
    v = tone + 30 * (nz - 0.5) + 60 * speck - 25 * (rng.random((n, n)) ** 40)
    v = np.clip(v, 0, 255)
    rgb = np.stack([v, v, v * 1.02], -1)
    return Image.fromarray(rgb.astype(np.uint8))


def concrete(n=512, rng=RNG, joint=True):
    nz = tile_noise(n, 6, rng, 5)
    v = 168 + 28 * (nz - 0.5) + 10 * (rng.random((n, n)) - 0.5)
    img = Image.fromarray(np.clip(np.stack([v, v * 0.98, v * 0.94], -1), 0, 255).astype(np.uint8))
    if joint:
        d = ImageDraw.Draw(img)
        d.line([(0, 0), (n, 0)], fill=(95, 95, 92), width=3)
        d.line([(0, 0), (0, n)], fill=(95, 95, 92), width=3)
    return img


def delineator_tex(style="plain", n=128):
    """Poste delineador. "hito": blanco con franja negra inclinada y captafaros (hito de arista
    español, igual en buena parte de Europa); "plain": blanco liso."""
    img = Image.new("RGB", (n // 2, n), (238, 238, 234))
    if style == "hito":
        d = ImageDraw.Draw(img)
        w = n // 2
        y0 = int(n * 0.70)   # la v=0 del poste está en la base: la franja va abajo en la imagen = arriba en el poste
        d.polygon([(0, y0 + 10), (w, y0), (w, y0 + int(n * 0.16)), (0, y0 + int(n * 0.16) + 10)], fill=(22, 22, 22))
        d.rectangle([w * 0.3, y0 + 4, w * 0.7, y0 + int(n * 0.12)], fill=(236, 150, 30))
    return img


def paving_tiles(base, tiles=8, rng=RNG, mean=138.0):
    """Baldosa de acera: la textura base (CC0 o procedural) oscurecida a gris medio, con juntas y
    una variación de tono por baldosa."""
    a = np.asarray(base.convert("RGB").resize((512, 512), Image.LANCZOS), np.float32)
    lum = a.mean(-1, keepdims=True)
    a = lum * (mean / max(float(lum.mean()), 1.0)) * np.array([1.0, 0.99, 0.965])
    n = a.shape[0]
    step = n / tiles
    y, x = np.mgrid[0:n, 0:n]
    ti, tj = (y // step).astype(int), (x // step).astype(int)
    tone = rng.normal(1.0, 0.045, (tiles + 1, tiles + 1))[ti, tj]
    a *= tone[..., None]
    joint = ((y % step) < 3) | ((x % step) < 3)
    a[joint] *= 0.62
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def voronoi_stones(n=512, cells=70, rng=RNG, mortar=(120, 108, 92), palette=None, rect=False):
    """Empedrado (piedra bola) o adoquín: celdas de Voronoi periódicas."""
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    if rect:
        # adoquín en hilera: rectángulos 2:1 con juntas alternadas
        bw, bh = n // 8, n // 16
        row = (yy // bh).astype(int)
        xo = xx + (row % 2) * (bw / 2)
        ex = np.minimum(xo % bw, bw - xo % bw)
        ey = np.minimum(yy % bh, bh - yy % bh)
        edge = np.minimum(ex, ey)
        cid = (row * 97 + (xo // bw).astype(int)) % 997
        tone = (np.sin(cid * 12.9898) * 43758.5453) % 1
        base = np.array([150, 112, 92], np.float32)
        col = base[None, None] * (0.85 + 0.3 * tone[..., None])
        m = np.clip(edge / 3.0, 0, 1)[..., None]
        out = col * m + np.array(mortar, np.float32) * (1 - m)
    else:
        pts = rng.random((cells, 2)) * n
        allp = np.concatenate([pts + [dx * n, dy * n] for dx in (-1, 0, 1) for dy in (-1, 0, 1)])
        d1 = np.full((n, n), 1e9, np.float32)
        d2 = np.full((n, n), 1e9, np.float32)
        idx = np.zeros((n, n), np.int32)
        for i, (px, py) in enumerate(allp):
            d = (xx - px) ** 2 + (yy - py) ** 2
            closer = d < d1
            d2 = np.where(closer, d1, np.minimum(d2, d))
            idx = np.where(closer, i % cells, idx)
            d1 = np.where(closer, d, d1)
        edge = np.sqrt(d2) - np.sqrt(d1)
        palette = palette or [(118, 112, 104), (96, 92, 88), (138, 128, 112), (84, 80, 78), (122, 104, 90)]
        pal = np.array(palette, np.float32)
        col = pal[idx % len(pal)]
        dome = np.clip(1.15 - np.sqrt(d1) / (n / np.sqrt(cells)) * 0.9, 0.6, 1.15)[..., None]
        col = col * dome
        m = np.clip((edge - 2) / 4.0, 0, 1)[..., None]
        out = col * m + np.array(mortar, np.float32) * (1 - m)
    nz = tile_noise(n, 16, rng, 3)[..., None]
    out = out * (0.9 + 0.2 * nz)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def dirt(n=512, rng=RNG, base=(150, 122, 90)):
    nz = tile_noise(n, 6, rng, 5)
    img = _rgb(base, 0.22, nz)
    a = np.asarray(img, np.float32)
    peb = rng.random((n, n)) ** 25
    a = a + peb[..., None] * 70 - (rng.random((n, n)) ** 30)[..., None] * 50
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def paint(color, n=64, rng=RNG):
    nz = tile_noise(n, 4, rng, 3)
    return _rgb(color, 0.06, nz)


def tope(n=256):
    """Tope pintado de amarillo en franjas diagonales sobre asfalto."""
    img = asphalt(n, tone=80)
    d = ImageDraw.Draw(img)
    w = n // 8
    for k in range(-n, 2 * n, 2 * w):
        d.polygon([(k, 0), (k + w, 0), (k + w + n // 2, n), (k + n // 2, n)], fill=(232, 196, 40))
    return img


def facade(color, n=512, rng=RNG, door=False, base=None, style="mx"):
    """Fachada de 4 m x 3 m: aplanado de color, ventana y zoclo.

    style "mx": ventana con protecciones de herrería. style "eu": ventana alta con marco claro,
    contraventanas a los lados y, a veces, barandilla de balcón.

    Con `base` (foto de aplanado CC0) el color se aplica multiplicando, y se conserva la
    textura real del enjarre; ventanas, herrería y zoclo se dibujan encima.
    """
    w, h = n, int(n * 0.75)
    if base is not None:
        b = np.asarray(base.convert("RGB").resize((w, w), Image.LANCZOS), np.float32)[:h, :w]
        lum = b.mean(-1, keepdims=True)
        b = lum / max(float(lum.mean()), 1.0)
        img = Image.fromarray(np.clip(b * np.array(color, np.float32)[None, None, :], 0, 255).astype(np.uint8))
    else:
        nz = tile_noise(n, 8, rng, 4)[:h, :w]
        img = _rgb(color, 0.10, nz)
    d = ImageDraw.Draw(img)
    # zoclo inferior más oscuro
    zc = tuple(int(c * 0.55) for c in color)
    d.rectangle([0, h - h // 10, w, h], fill=zc)
    px = w / 4.0  # pixeles por metro
    if door:
        x0 = int(1.4 * px)
        d.rectangle([x0, int(h - 2.2 * px), int(x0 + 1.1 * px), h], fill=(70, 50, 40))
        d.rectangle([x0 + 4, int(h - 2.2 * px) + 4, int(x0 + 1.1 * px) - 4, h], outline=(40, 30, 25), width=3)
    elif style == "eu":
        x0, x1 = int(1.45 * px), int(2.55 * px)
        y0, y1 = int(h - 2.6 * px), int(h - 0.75 * px)
        d.rectangle([x0 - 8, y0 - 8, x1 + 8, y1 + 8], fill=(232, 228, 220))
        d.rectangle([x0, y0, x1, y1], fill=(52, 62, 72))
        d.rectangle([x0 + 6, y0 + 6, (x0 + x1) // 2 - 3, y1 - 6], fill=(78, 92, 106))
        d.line([((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)], fill=(220, 216, 208), width=5)
        shutter = [(70, 92, 70), (110, 74, 50), (60, 76, 96), (150, 140, 120)][int(rng.integers(0, 4))]
        sw = int(0.5 * px)
        for sx0 in (x0 - 8 - sw, x1 + 8):
            d.rectangle([sx0, y0 - 6, sx0 + sw, y1 + 6], fill=shutter)
            for yy in range(y0, y1, 9):
                d.line([(sx0 + 3, yy), (sx0 + sw - 3, yy)], fill=tuple(int(c * 0.75) for c in shutter), width=2)
        if rng.random() < 0.5:   # barandilla de balcón
            by = y1 - int(0.9 * px)
            d.rectangle([x0 - 14, y1, x1 + 14, y1 + 8], fill=(150, 146, 140))
            d.line([(x0 - 14, by), (x1 + 14, by)], fill=(30, 30, 30), width=4)
            for bx in range(x0 - 14, x1 + 16, 10):
                d.line([(bx, by), (bx, y1)], fill=(30, 30, 30), width=2)
    else:
        x0, x1 = int(1.0 * px), int(3.0 * px)
        y0, y1 = int(h - 2.3 * px), int(h - 1.0 * px)
        d.rectangle([x0 - 6, y0 - 6, x1 + 6, y1 + 10], fill=tuple(min(255, int(c * 1.15)) for c in color))
        d.rectangle([x0, y0, x1, y1], fill=(46, 58, 70))
        d.rectangle([x0 + 8, y0 + 8, (x0 + x1) // 2 - 4, y1 - 8], fill=(70, 86, 100))
        for bx in range(x0 + 10, x1, 18):  # protecciones de herrería
            d.line([(bx, y0), (bx, y1)], fill=(25, 25, 25), width=3)
        d.line([(x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2)], fill=(25, 25, 25), width=3)
    return img


def roof(kind, n=256, rng=RNG):
    if kind == "rojo":       # impermeabilizante rojo terracota
        nz = tile_noise(n, 6, rng, 4)
        return _rgb((168, 72, 52), 0.12, nz)
    if kind == "blanco":
        nz = tile_noise(n, 6, rng, 4)
        return _rgb((210, 206, 196), 0.08, nz)
    return concrete(n, rng, joint=False)


def roof_tiles(color, n=256, rng=RNG):
    """Teja curva (árabe) vista desde arriba: hileras de canales con luz y sombra, 2 m x 2 m."""
    y, x = np.mgrid[0:n, 0:n].astype(np.float32)
    rows, cols = 10, 12
    u = (x / n * cols) % 1.0
    v = (y / n * rows + (np.floor(x / n * cols) % 2) * 0.5) % 1.0
    ridge = np.clip(np.cos((u - 0.5) * np.pi), 0, 1) ** 0.6            # canal redondeado (brillo al centro)
    lap = 0.78 + 0.22 * np.clip(v * 3, 0, 1)            # sombra donde una teja monta sobre otra
    nz = tile_noise(n, 10, rng, 3)
    tint = 1.0 + 0.14 * (tile_noise(n, 3, rng, 2) - 0.5) * 2
    k = (0.55 + 0.45 * ridge) * lap * (0.9 + 0.2 * nz) * tint
    c = np.asarray(color, np.float32)[None, None, :] * k[..., None]
    return Image.fromarray(np.clip(c, 0, 255).astype(np.uint8), "RGB")


VEG_VARIANTS = 3


def tree_sprite(kind, n=256, rng=None, variant=0):
    """Árbol / cactus como imagen con transparencia, para planos cruzados."""
    from . import foliage
    if kind in ("encino", "tropical", "arbusto", "pino", "olivo"):
        # hojas fotográficas CC0 (con caché en disco); si no hay internet, el procedural
        import os
        from .sources.cache import CACHE_DIR
        path = os.path.join(CACHE_DIR, "sprites", f"{foliage.SPRITE_VERSION}_{kind}_{variant}.png")
        if os.path.exists(path):
            return Image.open(path).convert("RGBA")
        img = foliage.photo_sprite(kind, max(n, 512), variant)
        if img is not None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path)
            return img
        return foliage.pine(max(n, 512), variant) if kind == "pino" else foliage.broadleaf(kind, max(n, 512), variant)
    rng = rng or np.random.default_rng(sum(map(ord, kind)) + 31 * variant)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "encino":
        d.rectangle([n * 0.46, n * 0.55, n * 0.54, n], fill=(78, 60, 44, 255))
        for _ in range(140):
            cx, cy = rng.normal(0.5, 0.17) * n, rng.normal(0.38, 0.14) * n
            r = rng.uniform(0.04, 0.09) * n
            g = rng.integers(55, 105)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(int(g * 0.6), g, int(g * 0.45), 255))
    elif kind == "tropical":
        d.rectangle([n * 0.47, n * 0.5, n * 0.53, n], fill=(98, 82, 64, 255))
        for _ in range(170):
            cx, cy = rng.normal(0.5, 0.2) * n, rng.normal(0.33, 0.12) * n
            r = rng.uniform(0.04, 0.1) * n
            g = rng.integers(80, 140)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(int(g * 0.5), g, int(g * 0.35), 255))
    elif kind == "pino":
        d.rectangle([n * 0.48, n * 0.75, n * 0.52, n], fill=(70, 52, 38, 255))
        for i in range(9):
            y = n * (0.08 + i * 0.085)
            half = n * (0.06 + i * 0.035)
            g = 50 + i * 4
            d.polygon([(n * 0.5, y - n * 0.06), (n * 0.5 - half, y + n * 0.12), (n * 0.5 + half, y + n * 0.12)],
                      fill=(int(g * 0.55), g, int(g * 0.5), 255))
    elif kind == "palma":
        d.line([(n * 0.5, n), (n * 0.53, n * 0.22)], fill=(120, 100, 76, 255), width=int(n * 0.04))
        cx, cy = n * 0.53, n * 0.22
        for k in range(14):
            a = np.pi * (k / 13.0) + rng.normal(0, 0.08)
            for t in np.linspace(0.05, 1, 12):
                x = cx + np.cos(a) * t * n * 0.42
                y = cy - np.sin(a) * t * n * 0.18 + (t ** 2) * n * 0.22
                r = n * 0.025 * (1.2 - t)
                d.ellipse([x - r * 2, y - r, x + r * 2, y + r], fill=(60, 120, 50, 255))
    elif kind == "organo":   # cactus órgano (Pachycereus marginatus)
        for k in range(6):
            x = n * (0.25 + k * 0.1) + rng.normal(0, 3)
            top = n * rng.uniform(0.05, 0.35)
            w = n * 0.045
            d.rounded_rectangle([x - w, top, x + w, n], radius=int(w), fill=(70, 112, 70, 255))
            for s in range(3):
                d.line([(x - w * 0.5 + s * w * 0.5, top + 6), (x - w * 0.5 + s * w * 0.5, n)], fill=(52, 88, 54, 255), width=2)
    elif kind == "maguey":   # agave
        cx, cy = n * 0.5, n * 0.95
        for k in range(22):
            a = np.pi * rng.uniform(0.08, 0.92)
            L = n * rng.uniform(0.35, 0.6)
            ex, ey = cx + np.cos(a) * L, cy - np.sin(a) * L
            w = n * 0.035
            px, py = -np.sin(a) * w, -np.cos(a) * w
            d.polygon([(cx + px, cy + py), (cx - px, cy - py), (ex, ey)], fill=(96, 140, 120, 255))
    elif kind == "arbusto":
        for _ in range(80):
            cx, cy = rng.normal(0.5, 0.2) * n, rng.normal(0.7, 0.13) * n
            r = rng.uniform(0.04, 0.08) * n
            g = rng.integers(70, 110)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(int(g * 0.85), g, int(g * 0.55), 255))
    img = img.filter(ImageFilter.SMOOTH)
    a = np.asarray(img).copy()
    a[..., 3] = np.where(a[..., 3] > 100, 255, 0)  # alpha test limpio
    return foliage.shade_flat(Image.fromarray(a, "RGBA"), seed=variant)


def banner(text, n=512):
    """Lona de salida / meta."""
    img = Image.new("RGB", (n, n // 4), (24, 104, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, n, n // 4], outline=(240, 240, 240), width=8)
    try:
        from PIL import ImageFont
        f = ImageFont.load_default(size=n // 7)
    except Exception:  # noqa: BLE001
        f = None
    d.text((n / 2, n / 8), text, fill=(250, 250, 250), anchor="mm", font=f)
    return img


def _font(size):
    try:
        from PIL import ImageFont
        return ImageFont.load_default(size=size)
    except Exception:  # noqa: BLE001
        return None


def sign_alto(n=256):
    """SR-6 ALTO: octágono rojo con orilla y letras blancas (NOM-034)."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    import math as m
    pts = [(n / 2 + n * 0.48 * m.cos(m.pi / 8 + k * m.pi / 4), n / 2 + n * 0.48 * m.sin(m.pi / 8 + k * m.pi / 4)) for k in range(8)]
    d.polygon(pts, fill=(240, 240, 240, 255))
    pts2 = [(n / 2 + n * 0.44 * m.cos(m.pi / 8 + k * m.pi / 4), n / 2 + n * 0.44 * m.sin(m.pi / 8 + k * m.pi / 4)) for k in range(8)]
    d.polygon(pts2, fill=(196, 30, 36, 255))
    from .regions import active
    d.text((n / 2, n / 2), active().stop_text, fill=(250, 250, 250, 255), anchor="mm", font=_font(int(n * 0.27)))
    return img


def sign_speed(v, n=256):
    """SR-9 velocidad máxima: círculo blanco con anillo rojo, cifra negra y "km/h"."""
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([n * 0.12, n * 0.02, n * 0.88, n * 0.98], fill=(245, 245, 245, 255), outline=(30, 30, 30, 255), width=4)
    d.ellipse([n * 0.18, n * 0.08, n * 0.82, n * 0.72], fill=(196, 30, 36, 255))
    d.ellipse([n * 0.25, n * 0.15, n * 0.75, n * 0.65], fill=(245, 245, 245, 255))
    d.text((n / 2, n * 0.40), str(int(v)), fill=(20, 20, 20, 255), anchor="mm", font=_font(int(n * 0.22)))
    d.text((n / 2, n * 0.84), "km/h", fill=(20, 20, 20, 255), anchor="mm", font=_font(int(n * 0.13)))
    return img


def sign_curve(left, n=256):
    """Curva peligrosa: rombo amarillo (SP-6/SP-7, América) o triángulo de borde rojo (P-13, Europa)."""
    from .regions import active
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n / 2
    if active().curve_sign == "triangle":
        d.polygon([(c, 6), (n - 6, n - 14), (6, n - 14)], fill=(200, 30, 36, 255))
        d.polygon([(c, n * 0.2), (n - n * 0.17, n - n * 0.14), (n * 0.17, n - n * 0.14)], fill=(245, 245, 245, 255))
    else:
        d.polygon([(c, 4), (n - 4, c), (c, n - 4), (4, c)], fill=(20, 20, 20, 255))
        d.polygon([(c, 12), (n - 12, c), (c, n - 12), (12, c)], fill=(242, 190, 26, 255))
    sx = -1 if left else 1
    pts = []
    for k in range(20):
        t = k / 19
        pts.append((c - sx * n * 0.12 + sx * n * 0.22 * (t ** 2), c + n * 0.26 - t * n * 0.42))
    d.line(pts, fill=(20, 20, 20, 255), width=int(n * 0.07), joint="curve")
    tip = pts[-1]
    d.polygon([(tip[0] + sx * n * 0.10, tip[1] + n * 0.02), (tip[0] - sx * n * 0.06, tip[1] - n * 0.06),
               (tip[0] - sx * n * 0.02, tip[1] + n * 0.12)], fill=(20, 20, 20, 255))
    return img


def edge_fade(n=256, color=(122, 106, 86)):
    """Orilla (arcén de grava o terracería) que se desvanece: opaca junto al asfalto, transparente hacia fuera."""
    base = dirt(n, np.random.default_rng(3), base=color)
    a = np.asarray(base.convert("RGBA")).copy()
    u = np.linspace(1, 0, n)[None, :] ** 1.4
    nz = tile_noise(n, 8, np.random.default_rng(5), 3)
    a[..., 3] = np.clip((u * 1.25 - 0.15 + (nz - 0.5) * 0.5) * 255, 0, 255)
    return Image.fromarray(a, "RGBA")


def roadwear(n=256):
    """Huella de rodada: franja oscura y suave, casi transparente en las orillas."""
    a = np.zeros((n, n, 4), np.uint8)
    u = np.linspace(-1, 1, n)
    prof = np.exp(-(u / 0.45) ** 2)
    nz = tile_noise(n, 10, np.random.default_rng(9), 3)
    a[..., 0] = a[..., 1] = a[..., 2] = 30
    a[..., 3] = np.clip(prof[None, :] * (0.5 + 0.5 * nz) * 46, 0, 255)
    return Image.fromarray(a, "RGBA")


def grain(n=512, rng=RNG):
    """Detalle fino neutro (gris medio) para el shader multicapa."""
    nz = tile_noise(n, 32, rng, 4)
    v = 128 + (nz - 0.5) * 90 + (rng.random((n, n)) - 0.5) * 50
    return Image.fromarray(np.clip(np.stack([v, v, v], -1), 0, 255).astype(np.uint8))


def detail_ground(kind, n=512, rng=RNG):
    """Texturas de detalle que se repiten cada ~2 m sobre la base del terreno."""
    if kind == "pasto":
        nz = tile_noise(n, 24, rng, 4)
        blades = rng.random((n, n)) ** 6
        v = 120 + (nz - 0.5) * 70 + blades * 60
        return Image.fromarray(np.clip(np.stack([v * 0.95, v * 1.05, v * 0.8], -1), 0, 255).astype(np.uint8))
    if kind == "tierra":
        return dirt(n, rng, base=(132, 120, 104))
    if kind == "roca":
        return voronoi_stones(n, 40, rng, mortar=(110, 106, 100),
                              palette=[(140, 136, 128), (120, 116, 110), (156, 150, 140), (104, 100, 96)])
    # hojarasca
    nz = tile_noise(n, 16, rng, 4)
    img = _rgb((118, 104, 82), 0.25, nz)
    d = ImageDraw.Draw(img)
    for _ in range(900):
        x, y = rng.random(2) * n
        r = rng.uniform(2, 6)
        g = int(rng.integers(70, 140))
        d.ellipse([x - r, y - r * 0.6, x + r, y + r * 0.6], fill=(g, int(g * 0.8), int(g * 0.5)))
    return img


def lamp_head(n=64):
    a = np.zeros((n, n, 3), np.uint8)
    a[:] = (70, 72, 76)
    a[n // 2:, :] = (250, 236, 200)
    return Image.fromarray(a)


def register_all(scene, season="seca", cc0=None):
    """Crea texturas y materiales fijos. Guarda PNG (para glTF) e imagen PIL (para DDS).

    cc0: dict ranura -> (color, normal) de texturas fotográficas de ambientCG (opcional).
    """
    S = scene
    cc0 = cc0 or {}

    def tex(name, img):
        S.textures[name] = png(img)
        S.images[name] = img
        return name

    def nm(name, img, strength=2.0):
        n = name.replace(".png", "_nm.png")
        S.images[n] = normal_map(img, strength)
        return n

    rng = np.random.default_rng(7)
    from .regions import active as _act
    tone = _act().asphalt_tone
    photo = cc0["asfalto"][0] if "asfalto" in cc0 else asphalt(512, rng)
    # Calzada en dos escalas: textura "macro" del ancho de la vía (rodaduras, mancha central,
    # parches) y grano fotográfico fino como detalle del multicapa. Variantes por número de
    # carriles; los cruces usan una lisa con mapeo planar.
    # Grano: 4 capas (la foto girada/volteada) a escalas distintas, repartidas por una máscara
    # de ruido suave: así no se ve el mosaico de rombos de una sola foto repetida.
    for k in range(4):
        tex(f"asfalto_det{k}.png", asphalt_detail2(photo, squash=int(ASPHALT_VREP), variant=k))
        tex(f"asfalto_iso{k}.png", asphalt_detail2(photo, squash=1, variant=k))
    S.images["asfalto_det_nm.png"] = normal_map(S.images["asfalto_det0.png"], 2.5)
    S.images["asfalto_iso_nm.png"] = normal_map(S.images["asfalto_iso0.png"], 2.5)
    ml_props = {"magicMult": 1.0, "fresnelC": 0.0, "fresnelEXP": 4.0, "fresnelMaxLevel": 0.35,
                "tarmacSpecularMultiplier": 0.6, "detailNMMult": [0.0, 1.5, 12.0, 0, 0, 0, 0, 0, 0, 0]}
    variants = [("asfalto", 2, 0), ("asfalto_b", 2, 1), ("asfalto_c", 2, 2), ("asfalto_d", 2, 7), ("asfalto_e", 2, 8),
                ("asfalto_1", 1, 3), ("asfalto_1b", 1, 4), ("asfalto_1c", 1, 10),
                ("asfalto_4", 4, 5), ("asfalto_4b", 4, 6)]
    t = tex("asfalto_marcas.png", asphalt_marks_atlas())
    S.material("asfalto_marcas", t, blend=True, ambient=0.4, diffuse=0.4, specular=0.0)
    for name, lanes, seed in variants:
        tex(f"{name}.png", asphalt_macro3(lanes, tone, seed=seed))
        tex(f"{name}_m.png", asphalt_mask(seed))
        base = 2.0 * lanes + 3.0                     # repeticiones del grano a lo ancho de la vía
        S.material(name, f"{name}.png", kind="multilayer", specular=0.0, ambient=0.42, diffuse=0.42, spec_exp=8.0,
                   extra={"mask": f"{name}_m.png", "details": [f"asfalto_det{k}.png" for k in range(4)],
                          "detail_nm": "asfalto_det_nm.png",
                          "mult": [base, base * 1.45, base * 0.7, base * 2.1], "props": ml_props})
    tex("asfalto_cruce.png", asphalt_macro3(2, tone, seed=9, plain=True, h=512))
    tex("asfalto_cruce_m.png", asphalt_mask(9, 256, 256))
    S.material("asfalto_cruce", "asfalto_cruce.png", kind="multilayer", specular=0.0, ambient=0.42, diffuse=0.42,
               spec_exp=8.0, extra={"mask": "asfalto_cruce_m.png", "details": [f"asfalto_iso{k}.png" for k in range(4)],
                                    "detail_nm": "asfalto_iso_nm.png", "mult": [14.0, 20.0, 10.0, 29.0],
                                    "props": ml_props})
    S.images["mascara_r.png"] = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    for name, img, st in (("concreto", concrete(512, rng), 1.5), ("empedrado", voronoi_stones(512, 80, rng), 4.0),
                          ("adoquin", voronoi_stones(512, rng=rng, rect=True), 3.0), ("terraceria", dirt(512, rng), 2.5),
                          ("banqueta", concrete(256, rng), 1.5)):
        if name == "banqueta":
            # baldosa gris de acera (8 x 8 por repetición de 3 m, ~37 cm): el concreto CC0 casi
            # blanco hacía que las banquetas parecieran nieve
            base = cc0[name][0] if name in cc0 else img
            img = paving_tiles(base, 8, rng)
            t = tex(f"{name}.png", img)
            S.material(name, t, normal=nm(t, img, 2.0), ambient=0.4, diffuse=0.42, specular=0.03, spec_exp=8.0)
            continue
        if name in cc0:
            img, nrm = cc0[name]
            t = tex(f"{name}.png", img)
            nname = t.replace(".png", "_nm.png")
            S.images[nname] = nrm if nrm is not None else normal_map(img, st)
            S.material(name, t, normal=nname, ambient=0.45, diffuse=0.45, specular=0.04, spec_exp=10.0)
            continue
        t = tex(f"{name}.png", img)
        S.material(name, t, normal=nm(t, img, st), ambient=0.45, diffuse=0.45, specular=0.04, spec_exp=10.0)
    S.material("guarnicion", tex("guarnicion.png", paint((170, 168, 160), 64, rng)), ambient=0.45, diffuse=0.5)
    S.material("guarnicion_amarilla", tex("guarnicion_am.png", paint((222, 186, 40), 64, rng)), ambient=0.45, diffuse=0.5)
    S.material("raya_blanca", tex("raya_blanca.png", worn_paint((232, 232, 226), 128, rng)), specular=0.1, ambient=0.55, diffuse=0.55)
    S.material("raya_amarilla", tex("raya_amarilla.png", worn_paint((232, 178, 32), 128, rng)), specular=0.1, ambient=0.55, diffuse=0.55)
    S.material("tope", tex("tope.png", tope(256)), ambient=0.4, diffuse=0.4)
    S.material("defensa", tex("defensa.png", paint((175, 180, 185), 64, rng)), specular=0.6, spec_exp=60.0, ambient=0.25, diffuse=0.3)
    S.material("poste", tex("poste.png", paint((90, 90, 92), 64, rng)), specular=0.25)
    from .regions import active as _ra
    S.material("fantasma", tex("fantasma.png", delineator_tex(_ra().delineator)), ambient=0.5, diffuse=0.5)
    S.material("tinaco", tex("tinaco.png", paint((28, 28, 30), 64, rng)), specular=0.2)
    m = concrete(256, rng, joint=False)
    t = tex("muro.png", m)
    S.material("muro", t, normal=nm(t, m, 1.5))
    S.material("agua", tex("agua.png", paint((44, 78, 96), 64, rng)), specular=1.5, spec_exp=200.0, ambient=0.3, diffuse=0.15)
    S.material("puente", tex("puente.png", concrete(256, rng, joint=False)))
    img = voronoi_stones(256, cells=90, rng=rng, mortar=(142, 134, 120),
                         palette=[(158, 146, 126), (146, 136, 116), (166, 154, 134), (138, 128, 110)])
    t = tex("muro_piedra.png", img)
    S.material("muro_piedra", t, normal=nm(t, img, 2.5), ambient=0.45, diffuse=0.5)
    # césped de isletas de glorieta (foto CC0 si hay; si no, procedural)
    g = cc0["pasto"][0] if "pasto" in cc0 else dirt(512, rng, base=(92, 120, 60))
    g = np.asarray(g.convert("RGB").resize((512, 512)), np.float32)
    tgt = [96, 124, 62] if _act().code == "mx" else [118, 120, 72]    # verde seco en Europa del sur
    g = g * (np.array(tgt, np.float32) / np.maximum(g.reshape(-1, 3).mean(0), 1))
    t = tex("isleta.png", Image.fromarray(np.clip(g, 0, 255).astype(np.uint8)))
    S.material("isleta", t, ambient=0.42, diffuse=0.45, specular=0.0)
    for k in ("concreto", "rojo", "blanco"):
        img = roof(k, 256, rng)
        t = tex(f"azotea_{k}.png", img)
        S.material(f"azotea_{k}", t, normal=nm(t, img, 1.5), specular=0.06, spec_exp=12.0)
    plaster = cc0.get("aplanado", (None, None))[0]
    from .regions import active
    R = active()
    style = "mx" if R.roof == "flat" else "eu"
    for k, col in enumerate(NAVE_COLORS):
        img = nave_panel(col, 512, rng)
        t = tex(f"nave_{k}.png", img)
        S.material(f"nave_{k}", t, normal=nm(t, img, 1.0), ambient=0.48, diffuse=0.5, specular=0.12, spec_exp=20.0)
    img = chapa_techo(256, rng)
    t = tex("chapa.png", img)
    S.material("chapa", t, normal=nm(t, img, 1.5), ambient=0.45, diffuse=0.5, specular=0.15, spec_exp=25.0)
    for k, col in enumerate(R.roof_colors):
        img = roof_tiles(col, 256, rng)
        t = tex(f"tejado_{k}.png", img)
        S.material(f"tejado_{k}", t, normal=nm(t, img, 2.0), specular=0.05, spec_exp=10.0, ambient=0.45, diffuse=0.55)
    for i, col in enumerate(R.facade_colors):
        img = facade(col, 512, rng, base=plaster, style=style)
        t = tex(f"fachada_{i}.png", img)
        n = nm(t, img, 1.2)
        S.material(f"fachada_{i}", t, normal=n, ambient=0.5, diffuse=0.55, specular=0.06, spec_exp=10.0)
        # variantes más clara y más oscura (sin textura extra)
        S.material(f"fachada_{i}_d", t, normal=n, ambient=0.44, diffuse=0.48, specular=0.06, spec_exp=10.0)
        S.material(f"fachada_{i}_l", t, normal=n, ambient=0.55, diffuse=0.6, specular=0.06, spec_exp=10.0)
    img = facade((222, 200, 160), 512, rng, door=True, base=plaster)
    t = tex("iglesia.png", img)
    S.material("iglesia", t, normal=nm(t, img, 1.2))
    for k in ("encino", "tropical", "pino", "palma", "organo", "maguey", "arbusto", "olivo"):
        for v in range(VEG_VARIANTS):
            S.material(f"veg_{k}_{v}", tex(f"veg_{k}_{v}.png", tree_sprite(k, 256, variant=v)), alpha=True,
                       shadows=True, ambient=0.4, diffuse=0.35, specular=0.0)
    S.material("lona_salida", tex("lona_salida.png", banner("SALIDA")), ambient=0.6, diffuse=0.5)
    S.material("lona_meta", tex("lona_meta.png", banner("META")), ambient=0.6, diffuse=0.5)
    S.material("valla", tex("valla.png", paint((210, 60, 40), 64, rng)))
    # arcén: grava clara en Europa, terracería en México
    S.material("orilla", tex("orilla.png", edge_fade(256, (132, 122, 90) if _act().code != "mx" else (122, 106, 86))),
               blend=True, ambient=0.4, diffuse=0.4, specular=0.0)
    S.material("desgaste", tex("desgaste.png", roadwear(256)), blend=True, ambient=0.3, diffuse=0.3, specular=0.05)
    # colisiones: textura totalmente transparente (con RGB opaco se veían como cajas negras)
    S.material("invisible", tex("transparente.png", Image.new("RGBA", (16, 16), (0, 0, 0, 0))), kind="invisible",
               ambient=0.0, diffuse=0.0)
    S.material("senal_alto", tex("senal_alto.png", sign_alto()), alpha=True, ambient=0.6, diffuse=0.5, specular=0.2)
    S.material("senal_curva_izq", tex("senal_curva_izq.png", sign_curve(True)), alpha=True, ambient=0.6, diffuse=0.5)
    S.material("senal_curva_der", tex("senal_curva_der.png", sign_curve(False)), alpha=True, ambient=0.6, diffuse=0.5)
    S.material("lampara", tex("lampara.png", lamp_head()), ambient=0.6, diffuse=0.5, specular=0.2)
    # detalle del terreno (multicapa): R pasto, G tierra, B roca, A hojarasca
    src = {"pasto": "pasto", "tierra": "terraceria", "roca": "roca", "hojarasca": "hojarasca"}
    for k in ("pasto", "tierra", "roca", "hojarasca"):
        # el multicapa multiplica la base por el detalle: se deja casi neutro (gris ~140)
        base_img = cc0[src[k]][0].resize((512, 512), Image.LANCZOS) if src[k] in cc0 else detail_ground(k, 512, rng)
        d = np.asarray(base_img, np.float32)
        lum = d.mean(-1, keepdims=True)
        d = (lum * 0.7 + d * 0.3) * (140.0 / max(float(lum.mean()), 1.0))
        tex(f"det_{k}.png", Image.fromarray(np.clip(d, 0, 255).astype(np.uint8)))
    S.images["det_nm.png"] = (cc0["pasto"][1].resize((512, 512), Image.LANCZOS)
                              if "pasto" in cc0 and cc0["pasto"][1] is not None else normal_map(S.images["det_roca.png"], 2.5))


def speed_material(scene, v):
    name = f"senal_vel_{int(v)}"
    if name not in scene.materials:
        img = sign_speed(v)
        scene.textures[f"{name}.png"] = png(img)
        scene.images[f"{name}.png"] = img
        scene.material(name, f"{name}.png", alpha=True, ambient=0.6, diffuse=0.5, specular=0.2)
    return name


# ------------------------------------------------------------------ colores reales de OSM
def parse_colour(v):
    """building:colour / roof:colour de OSM (nombre CSS o #hex) -> (r, g, b) o None."""
    if not v:
        return None
    from PIL import ImageColor
    v = str(v).strip().split(";")[0]
    alias = {"brick": "#9c4a32", "terracotta": "#c0623e", "cream": "#efe6cf", "beige": "#d9c7a0",
             "sand": "#d6c08e", "ochre": "#cc9a3d", "stone": "#b8ab92"}
    try:
        return ImageColor.getrgb(alias.get(v.lower(), v))[:3]
    except ValueError:
        return None


def _quant(rgb, step=24):
    return tuple(int(min(255, round(c / step) * step)) for c in rgb)


def facade_material(scene, rgb, style="eu", cc0_plaster=None):
    """Material de fachada para un color real (cuantizado para no crear cientos de texturas)."""
    q = _quant(rgb)
    name = "fachada_c%02x%02x%02x" % q
    if name not in scene.materials:
        rng = np.random.default_rng(sum(q))
        img = facade(q, 512, rng, base=cc0_plaster, style=style)
        t = f"{name}.png"
        scene.textures[t] = png(img)
        scene.images[t] = img
        n = t.replace(".png", "_nm.png")
        scene.images[n] = normal_map(img, 1.2)
        scene.material(name, t, normal=n, ambient=0.5, diffuse=0.55, specular=0.06, spec_exp=10.0)
    return name


def roof_material(scene, rgb, pitched=True):
    q = _quant(rgb)
    name = ("tejado_c%02x%02x%02x" if pitched else "azotea_c%02x%02x%02x") % q
    if name not in scene.materials:
        rng = np.random.default_rng(sum(q) + 1)
        img = roof_tiles(q, 256, rng) if pitched else _rgb(q, 0.1, tile_noise(256, 6, rng, 4))
        t = f"{name}.png"
        scene.textures[t] = png(img)
        scene.images[t] = img
        n = t.replace(".png", "_nm.png")
        scene.images[n] = normal_map(img, 2.0 if pitched else 1.5)
        scene.material(name, t, normal=n, ambient=0.45, diffuse=0.55, specular=0.05, spec_exp=10.0)
    return name


# ------------------------------------------------------------------ asfalto "macro" (carretera suave)
def asphalt_macro(lanes=2, tone=116.0, w=512, h=1024, seed=0, plain=False):
    """Textura de calzada a escala grande: el ancho completo (u 0..1) por 2 anchos de largo (v).

    Lo que hace que una carretera se vea suave y real a media distancia no es el grano, sino
    las variaciones grandes: rodaduras algo más oscuras en cada carril, la franja central con
    manchas de aceite, orillas más claras (polvo, menos tráfico) y parches suaves. El grano
    fino lo pone el detalle del shader multicapa, solo de cerca.
    """
    rng = np.random.default_rng(100 + seed + 7 * lanes)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    lum = np.full((h, w), 1.0, np.float32)
    # parches grandes y suaves (bacheo, asfalto de distinta edad), periódicos en v
    def pnoise(cells_u, cells_v, oct_):
        acc = np.zeros((h, w), np.float32)
        amp = 1.0
        for o in range(oct_):
            cu, cv = cells_u * 2 ** o, cells_v * 2 ** o
            g = rng.random((cv + 1, cu + 1)).astype(np.float32)
            g[-1, :] = g[0, :]          # periódico a lo largo
            x = U * cu
            y = V * cv
            x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
            fx, fy = x - x0, y - y0
            fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
            x1, y1 = np.minimum(x0 + 1, cu), np.minimum(y0 + 1, cv)
            a = g[y0, x0] * (1 - fx) + g[y0, x1] * fx
            b = g[y1, x0] * (1 - fx) + g[y1, x1] * fx
            acc += (a * (1 - fy) + b * fy - 0.5) * amp
            amp *= 0.5
        return acc
    lum *= 1.0 + 0.07 * pnoise(3, 6, 3)
    if not plain:
        lw = 1.0 / max(lanes, 1)
        for k in range(lanes):
            c = (k + 0.5) * lw
            for d in (-0.24, 0.24):                       # dos rodaduras por carril
                lum *= 1.0 - 0.07 * np.exp(-((U - (c + d * lw)) / (0.055 * lw)) ** 2)
            # mancha central de aceite entre rodaduras
            lum *= 1.0 - 0.05 * np.exp(-((U - c) / (0.05 * lw)) ** 2) * (0.6 + 0.8 * np.clip(pnoise(1, 12, 2) + 0.5, 0, 1))
        # orillas más claras (polvo) y sellado de grietas longitudinal en la junta central
        edge = np.minimum(U, 1 - U)
        lum *= 1.0 + 0.06 * np.exp(-(edge / 0.035) ** 2)
        if lanes >= 2:
            seam = np.exp(-((U - 0.5) / 0.004) ** 2) * (np.abs(np.sin(V * np.pi * 9 + 3 * pnoise(1, 4, 1))) > 0.2)
            lum *= 1.0 - 0.10 * seam
    t = np.array([tone, tone * 0.995, tone * 0.985], np.float32)
    img = lum[..., None] * t[None, None, :]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def asphalt_detail(base_photo, n=512):
    """Grano fino para el multicapa a partir de la foto CC0: centrado en gris medio (0.5) para
    que module sin oscurecer, y comprimido 2:1 en v para compensar que la calzada usa u=ancho y
    v=2 anchos (así el grano queda redondo, no estirado)."""
    a = np.asarray(base_photo.convert("L").resize((n, n // 2), Image.LANCZOS), np.float32)
    a = 128 + (a - a.mean()) * 0.9
    a = np.vstack([a, a])
    return Image.fromarray(np.clip(np.stack([a, a, a], -1), 0, 255).astype(np.uint8), "RGB")


def worn_paint(color, n=128, rng=RNG):
    """Pintura de raya con desgaste: manchas donde asoma el asfalto y borde algo irregular."""
    nz = tile_noise(n, 6, rng, 4)
    nz2 = tile_noise(n, 2, rng, 2)
    c = np.asarray(color, np.float32)
    k = 1.0 - 0.28 * np.clip((nz - 0.62) * 4, 0, 1) - 0.06 * nz2
    img = c[None, None, :] * k[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


# ------------------------------------------------------------------ naves industriales
NAVE_COLORS = [(232, 232, 228), (214, 216, 214), (226, 220, 204), (176, 188, 198), (200, 204, 206)]


def nave_panel(color, n=512, rng=RNG):
    """Panel de chapa grecada (4 m x 3 m): nervios verticales cada 25 cm con luz y sombra, y
    churretes de suciedad que bajan; es lo que hace que una nave no parezca una casa."""
    h = int(n * 0.75)
    x = np.arange(n, dtype=np.float32)
    period = n / 16.0
    ph = (x % period) / period
    rib = np.where(ph < 0.18, 1.12, np.where(ph < 0.30, 0.82, 1.0))
    k = np.tile(rib[None, :], (h, 1))
    streak = tile_noise(n, 3, rng, 3)[:h, :n]
    k *= 0.95 + 0.08 * streak
    dirt = np.clip(np.linspace(0, 1, h)[:, None] * 0.12 * (0.5 + tile_noise(n, 6, rng, 2)[:h, :n]), 0, 0.15)
    k *= 1 - dirt
    img = np.asarray(color, np.float32)[None, None, :] * k[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def chapa_techo(n=256, rng=RNG):
    """Cubierta de chapa (sandwich) gris, nervios cada 1 m."""
    x = np.arange(n, dtype=np.float32)
    ph = (x % (n / 2.0)) / (n / 2.0)
    rib = np.where(ph < 0.08, 1.15, 1.0)
    k = np.tile(rib[None, :], (n, 1)) * (0.94 + 0.1 * tile_noise(n, 4, rng, 3))
    img = np.array([150, 152, 154], np.float32)[None, None, :] * k[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


# ------------------------------------------------------------------ asfalto v2: sin patrones
ASPHALT_VREP = 4.0   # la textura macro cubre el ancho (u) por 4 anchos de largo (v)


def _pnoise(rng, U, V, cells_u, cells_v, octaves):
    """Ruido de valor periódico en v (y en u si se quiere), suma de octavas, centrado en 0."""
    h, w = U.shape
    acc = np.zeros((h, w), np.float32)
    amp = 1.0
    for o in range(octaves):
        cu, cv = int(cells_u * 2 ** o), int(cells_v * 2 ** o)
        g = rng.random((cv + 1, cu + 1)).astype(np.float32)
        g[-1, :] = g[0, :]
        x, y = U * cu, V * cv
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = x - x0, y - y0
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        x1, y1 = np.minimum(x0 + 1, cu), np.minimum(y0 + 1, cv)
        a = g[y0 % (cv + 1), x0] * (1 - fx) + g[y0 % (cv + 1), x1] * fx
        b = g[y1 % (cv + 1), x0] * (1 - fx) + g[y1 % (cv + 1), x1] * fx
        acc += (a * (1 - fy) + b * fy - 0.5) * amp
        amp *= 0.5
    return acc


def asphalt_macro2(lanes=2, tone=116.0, seed=0, plain=False, w=512, h=2048):
    """Calzada a escala grande sin repeticiones visibles ni líneas rectas.

    - rodaduras que serpentean, cambian de intensidad y a ratos desaparecen;
    - parches de bacheo (rectángulos algo más oscuros y nuevos, con borde nítido);
    - sellados de grieta (líneas finas oscuras que zigzaguean) y alguna grieta transversal;
    - envejecido irregular: zonas más claras y desgastadas, otras más oscuras.
    Periódica solo en v (4 anchos de largo), para que repita sin costura.
    """
    rng = np.random.default_rng(500 + 13 * seed + 7 * lanes + (1000 if plain else 0))
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    lum = 1.0 + 0.09 * _pnoise(rng, U, V, 3, 10, 4)            # envejecido a varias escalas
    warm = 0.012 * _pnoise(rng, U, V, 2, 6, 2)                  # tono algo más cálido/frío
    if not plain:
        lw = 1.0 / max(lanes, 1)
        for k in range(lanes):
            c = (k + 0.5) * lw
            for d in (-0.24, 0.24):
                wander = 0.035 * lw * _pnoise(rng, U[:, :1], V[:, :1], 1, 6, 2)      # serpentea
                inten = np.clip(0.75 + 1.3 * _pnoise(rng, U[:, :1], V[:, :1], 1, 9, 2), 0, 1)
                width = 0.05 * lw * (1 + 0.5 * _pnoise(rng, U[:, :1], V[:, :1], 1, 7, 1))
                lum *= 1.0 - 0.075 * inten * np.exp(-((U - (c + d * lw + wander)) / width) ** 2)
            oil = np.clip(0.4 + 1.5 * _pnoise(rng, U, V, 2, 24, 2), 0, 1)
            lum *= 1.0 - 0.045 * oil * np.exp(-((U - c) / (0.06 * lw)) ** 2)
        edge = np.minimum(U, 1 - U)
        lum *= 1.0 + 0.05 * np.exp(-(edge / 0.04) ** 2) * (0.6 + 0.8 * np.clip(_pnoise(rng, U, V, 1, 8, 1) + 0.5, 0, 1))
    # parches de bacheo: algo más oscuros y con su propio grano, borde fino; envuelven en v
    if not plain:
        for _ in range(int(rng.integers(1, 4))):
            pw, ph = int(w * rng.uniform(0.15, 0.5)), int(h * rng.uniform(0.015, 0.05))
            x0, y0 = int(rng.uniform(0, w - pw)), int(rng.uniform(0, h))
            f = rng.uniform(0.9, 0.96)
            rows = np.arange(y0, y0 + ph) % h
            grain = 1 + 0.03 * (rng.random((ph, pw)) - 0.5)
            lum[rows[:, None], np.arange(x0, x0 + pw)[None, :]] *= f * grain
            lum[rows[[0, -1]][:, None], np.arange(x0, x0 + pw)[None, :]] *= 0.93
            lum[rows[:, None], np.array([x0, x0 + pw - 1])[None, :]] *= 0.93
    img = np.stack([lum * (1 + warm), lum, lum * (1 - warm)], -1) * tone
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(pil)
    # sellados de grieta: polilíneas finas que zigzaguean, sobre todo a lo largo
    for _ in range(int(rng.integers(2, 6))):
        x, y = rng.uniform(0.15, 0.85) * w, rng.uniform(0, h)
        pts = [(x, y)]
        for _ in range(int(rng.integers(10, 40))):
            x = float(np.clip(x + rng.normal(0, 4), 2, w - 2))
            y = y + rng.uniform(6, 22)
            pts.append((x, y))
        sc = int(tone * 0.7)
        for off in (0, -h):
            d.line([(px, py + off) for px, py in pts], fill=(sc, sc, sc), width=int(rng.integers(2, 4)))
    # alguna grieta transversal
    for _ in range(int(rng.integers(0, 3))):
        y = rng.uniform(0, h)
        pts = [(0, y)]
        x = 0
        while x < w:
            x += rng.uniform(8, 24)
            y += rng.normal(0, 3)
            pts.append((x, y))
        sc = int(tone * 0.6)
        d.line(pts, fill=(sc, sc, sc), width=2)
    return pil


def asphalt_detail2(photo, squash=4, n=1024, variant=0):
    """Grano fino centrado en gris medio, comprimido `squash`:1 en v (compensa la UV alargada).
    Cada variante se gira/voltea para que las cuatro capas del multicapa no coincidan."""
    im = photo.convert("L")
    if variant % 4 == 1:
        im = im.transpose(Image.ROTATE_90)
    elif variant % 4 == 2:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    elif variant % 4 == 3:
        im = im.transpose(Image.ROTATE_270)
    a = np.asarray(im.resize((n, n // squash), Image.LANCZOS), np.float32)
    a = 128 + (a - a.mean()) * (0.8 + 0.15 * variant)
    a = np.vstack([a] * squash)
    return Image.fromarray(np.clip(np.stack([a, a, a], -1), 0, 255).astype(np.uint8), "RGB")


def asphalt_mask(seed=0, w=256, h=1024):
    """Máscara RGBA del multicapa: reparte las 4 capas de grano con ruido suave (rompe el mosaico)."""
    rng = np.random.default_rng(900 + seed)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    ch = [np.clip(0.5 + 2.2 * _pnoise(rng, U, V, 2, 8, 3), 0, 1) for _ in range(4)]
    m = np.stack(ch, -1) + 1e-3
    m = m / m.sum(-1, keepdims=True)
    return Image.fromarray((m * 255).astype(np.uint8), "RGBA")


# ------------------------------------------------------------------ asfalto v3: orgánico
def _warped(rng, U, V, cu, cv, octaves, warp=0.35):
    """Ruido con deformación de dominio (domain warping): manchas orgánicas, sin formas
    geométricas. Periódico en v."""
    wu = _pnoise(rng, U, V, max(1, cu // 2), max(1, cv // 2), 2)
    wv = _pnoise(rng, U, V, max(1, cu // 2), max(1, cv // 2), 2)
    U2 = np.clip(U + warp * wu / max(cu, 1), 0, 0.9999)
    V2 = (V + warp * wv / max(cv, 1)) % 1.0
    return _pnoise(rng, U2, V2, cu, cv, octaves)


def asphalt_macro3(lanes=2, tone=116.0, seed=0, plain=False, w=512, h=2048):
    """Calzada a escala grande con aspecto real: todo con formas orgánicas, nada geométrico.

    Capas (todas de poco contraste, se suman sin dibujar figuras):
    - envejecido general con ruido deformado a varias escalas;
    - rodaduras muy suaves, rotas y serpenteantes (se intuyen, no se ven como rayas);
    - manchas de aceite / humedad irregulares al centro de cada carril;
    - parches de reparación de forma irregular (ruido recortado) con borde sellado fino;
    - grietas finas ramificadas, tenues.
    """
    rng = np.random.default_rng(700 + 17 * seed + 5 * lanes + (1000 if plain else 0))
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    lum = 1.0 + 0.07 * _warped(rng, U, V, 3, 12, 4) + 0.035 * _pnoise(rng, U, V, 12, 48, 2)
    warm = 0.012 * _warped(rng, U, V, 2, 6, 2)
    if not plain:
        lw = 1.0 / max(lanes, 1)
        for k in range(lanes):
            c = (k + 0.5) * lw
            for d in (-0.24, 0.24):
                wander = 0.05 * lw * _pnoise(rng, U[:, :1], V[:, :1], 1, 5, 2)
                broken = np.clip(0.55 + 1.6 * _warped(rng, U, V, 4, 30, 3), 0, 1)       # se corta a ratos
                lum *= 1.0 - 0.045 * broken * np.exp(-((U - (c + d * lw + wander)) / (0.07 * lw)) ** 2)
            stain = np.clip(1.8 * _warped(rng, U, V, 3, 26, 3, warp=0.6), 0, 1)
            lum *= 1.0 - 0.06 * stain * np.exp(-((U - c) / (0.09 * lw)) ** 2)
        # (los parches y grietas ya no van aquí: se ponen como calcomanías sueltas al azar)
        blob = _warped(rng, U, V, 2, 9, 3, warp=0.8) * 0 - 1
        thr = np.quantile(blob, 0.955)
        patch = blob > thr
        f = rng.uniform(0.93, 0.97)
        lum = np.where(patch, lum * f * (1 + 0.015 * _pnoise(rng, U, V, 30, 120, 1)), lum)
        from scipy.ndimage import binary_erosion
        edge = patch & ~binary_erosion(patch, iterations=2)
        lum = np.where(edge, lum * 0.95, lum)
        # orillas: polvo, algo más claras e irregulares
        e_ = np.minimum(U, 1 - U)
        lum *= 1.0 + 0.05 * np.exp(-(e_ / 0.045) ** 2) * np.clip(0.6 + 1.2 * _warped(rng, U, V, 2, 10, 2), 0, 1.4)
    img = np.stack([lum * (1 + warm), lum, lum * (1 - warm)], -1) * tone
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    if plain or True:      # grietas: calcomanías sueltas (asphalt_marks_atlas), no en la textura
        return pil
    # grietas finas y ramificadas, tenues (dibujadas en una capa aparte y mezcladas al 35 %)
    crack = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(crack)

    def branch(x, y, ang, n, wdt):
        pts = [(x, y)]
        for _ in range(n):
            ang += rng.normal(0, 0.35)
            x = float(np.clip(x + 6 * math.cos(ang), 1, w - 2))
            y = y + 6 * math.sin(ang) * 3.0          # la textura va comprimida 4:1 en v
            pts.append((x, y))
            if rng.random() < 0.06 and n > 6:
                branch(x, y, ang + rng.choice([-1, 1]) * rng.uniform(0.6, 1.2), n // 3, 1)
        for off in (0, -h, h):
            d.line([(px, py + off) for px, py in pts], fill=255, width=wdt)
    for _ in range(int(rng.integers(2, 5))):
        branch(rng.uniform(0.15, 0.85) * w, rng.uniform(0, h), math.pi / 2 + rng.normal(0, 0.3),
               int(rng.integers(15, 45)), int(rng.integers(1, 3)))
    a = np.asarray(pil, np.float32)
    cm = np.asarray(crack, np.float32)[..., None] / 255.0
    a = a * (1 - 0.35 * cm)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


# ------------------------------------------------------------------ marcas sueltas del asfalto
def asphalt_marks_atlas(cell=256, seed=4):
    """Atlas 4x2 de calcomanías: 6 grietas distintas (finas, ramificadas, transversales,
    en malla) y 2 bacheos irregulares. Se colocan sueltas y al azar sobre la calzada, así
    no se repiten en el mismo sitio como cuando iban dentro de la textura."""
    rng = np.random.default_rng(seed)
    W, H = cell * 4, cell * 2
    rgba = np.zeros((H, W, 4), np.float32)
    for k in range(8):
        cx, cy = (k % 4) * cell, (k // 4) * cell
        layer = Image.new("L", (cell, cell), 0)
        d = ImageDraw.Draw(layer)
        if k < 6:
            def branch(x, y, ang, n, wdt):
                pts = [(x, y)]
                for _ in range(n):
                    ang += rng.normal(0, 0.45)
                    x = x + 5 * math.cos(ang)
                    y = y + 5 * math.sin(ang)
                    if not (4 < x < cell - 4 and 4 < y < cell - 4):
                        break
                    pts.append((x, y))
                    if rng.random() < 0.08 and n > 5:
                        branch(x, y, ang + rng.choice([-1, 1]) * rng.uniform(0.7, 1.3), n // 2, max(1, wdt - 1))
                if len(pts) > 1:
                    d.line(pts, fill=255, width=wdt)
            if k == 4:      # grieta transversal
                branch(6, cell / 2, 0.0, 50, 2)
            elif k == 5:    # piel de cocodrilo (malla pequeña)
                for _ in range(14):
                    branch(rng.uniform(40, cell - 40), rng.uniform(40, cell - 40), rng.uniform(0, 6.28), 8, 1)
            else:
                branch(cell / 2 + rng.normal(0, 20), 8, math.pi / 2 + rng.normal(0, 0.2), 50, int(rng.integers(1, 3)))
            m = np.asarray(layer, np.float32) / 255.0
            from scipy.ndimage import gaussian_filter
            m = np.clip(gaussian_filter(m, 0.6) * 1.4, 0, 1) * 0.55
            col = np.array([36, 36, 36], np.float32)
        else:           # bacheo irregular: mancha con borde sellado
            u = np.linspace(0, 1, cell)
            U, V = np.meshgrid(u, u)
            r = np.hypot(U - 0.5, V - 0.5)
            ang = np.arctan2(V - 0.5, U - 0.5)
            wob = 0.32 + 0.06 * np.sin(3 * ang + rng.uniform(0, 6)) + 0.04 * np.sin(5 * ang + rng.uniform(0, 6))
            inside = (r < wob).astype(np.float32)
            from scipy.ndimage import gaussian_filter, binary_erosion
            edge = inside - binary_erosion(inside > 0.5, iterations=3).astype(np.float32)
            m = np.clip(gaussian_filter(inside, 1.0) * 0.18 + edge * 0.35, 0, 1)
            col = np.array([60, 60, 60], np.float32)
        rgba[cy:cy + cell, cx:cx + cell, :3] = col
        rgba[cy:cy + cell, cx:cx + cell, 3] = m * 255
    return Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA")


def island_material(scene, rgb):
    """Superficie de isleta del color medio que tiene en la ortofoto: si es claramente rojiza o
    azulada se trata como pintura (lisa); si no, como césped / tierra con grano."""
    q = _quant(rgb, 20)
    name = "isleta_c%02x%02x%02x" % q
    if name not in scene.materials:
        rng = np.random.default_rng(sum(q))
        painted = (q[0] > q[1] + 45) or (q[2] > q[0] + 30)
        nz = tile_noise(256, 6 if painted else 12, rng, 3)
        img = _rgb(q, 0.05 if painted else 0.18, nz)
        t = f"{name}.png"
        scene.textures[t] = png(img)
        scene.images[t] = img
        scene.material(name, t, ambient=0.45, diffuse=0.45, specular=0.05 if painted else 0.0)
    return name


def plate_material(scene, text):
    """Placa complementaria blanca con texto negro (p. ej. "150 m")."""
    name = "placa_" + "".join(ch for ch in text if ch.isalnum())
    if name not in scene.materials:
        w, h = 240, 100
        img = Image.new("RGBA", (w, h), (245, 245, 245, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([2, 2, w - 3, h - 3], outline=(20, 20, 20, 255), width=4)
        d.text((w / 2, h / 2), text, fill=(20, 20, 20, 255), anchor="mm", font=_font(56))
        t = f"{name}.png"
        scene.textures[t] = png(img)
        scene.images[t] = img
        scene.material(name, t, alpha=True, ambient=0.6, diffuse=0.5, specular=0.2)
    return name
