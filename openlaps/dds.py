"""Codificador DDS (DXT1 / DXT5) con cadena completa de mipmaps, vectorizado con numpy.

Assetto Corsa espera texturas DDS comprimidas con mipmaps: ocupan 4-8 veces menos memoria de
video que un PNG descomprimido y no parpadean a distancia. El codificador usa el método de
"caja envolvente sobre el eje principal": por cada bloque de 4x4 se proyectan los colores sobre
la diagonal de su caja RGB, los extremos se cuantizan a RGB565 y cada pixel toma el más
cercano de los 4 colores de la paleta.
"""
import struct

import numpy as np
from PIL import Image


def _blocks(a):
    """(H, W, C) -> (nbloques, 16, C), rellenando a múltiplo de 4."""
    h, w, c = a.shape
    H, W = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (H, W) != (h, w):
        a = np.pad(a, ((0, H - h), (0, W - w), (0, 0)), mode="edge")
    b = a.reshape(H // 4, 4, W // 4, 4, c).transpose(0, 2, 1, 3, 4).reshape(-1, 16, c)
    return b


def _to565(c):
    c = np.clip(np.round(c), 0, 255).astype(np.uint32)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _from565(v):
    r = ((v >> 11) & 31) * 255 / 31
    g = ((v >> 5) & 63) * 255 / 63
    b = (v & 31) * 255 / 31
    return np.stack([r, g, b], -1).astype(np.float32)


def _color_blocks(rgb):
    """rgb: (n, 16, 3) float -> (n, 8) bytes de bloque DXT1 (modo de 4 colores)."""
    lo = rgb.min(1)
    hi = rgb.max(1)
    axis = hi - lo
    norm = np.maximum((axis * axis).sum(1, keepdims=True), 1e-6)
    t = ((rgb - lo[:, None, :]) * axis[:, None, :]).sum(2) / norm
    tmin, tmax = t.min(1), t.max(1)
    c_hi = lo + axis * tmax[:, None]
    c_lo = lo + axis * tmin[:, None]
    a = _to565(c_hi)
    b = _to565(c_lo)
    # en modo de 4 colores c0 > c1; si son iguales, todo apunta a c0
    swap = a < b
    a2 = np.where(swap, b, a)
    b2 = np.where(swap, a, b)
    eq = a2 == b2
    b2 = np.where(eq & (a2 > 0), a2 - 1, b2)
    a2 = np.where(eq & (a2 == 0), 1, a2)
    p0, p1 = _from565(a2), _from565(b2)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)          # (n, 4, 3)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3)                 # (n, 16, 4)
    idx = d.argmin(2).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((len(rgb), 8), np.uint8)
    out[:, 0:2] = a2.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = b2.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def _alpha_blocks(al):
    """al: (n, 16) float -> (n, 8) bytes de bloque alfa DXT5 (modo de 8 valores)."""
    a0 = np.clip(np.round(al.max(1)), 0, 255)
    a1 = np.clip(np.round(al.min(1)), 0, 255)
    same = a0 == a1
    a0 = np.where(same & (a0 < 255), a0 + 1, a0)
    a1 = np.where(same & (a0 == 255) & (a1 == 255), 254, a1)
    k = np.arange(8)
    w0 = np.array([1, 0, 6, 5, 4, 3, 2, 1]) / np.array([1, 1, 7, 7, 7, 7, 7, 7])
    w1 = np.array([0, 1, 1, 2, 3, 4, 5, 6]) / np.array([1, 1, 7, 7, 7, 7, 7, 7])
    pal = a0[:, None] * w0[None, :] + a1[:, None] * w1[None, :]               # (n, 8)
    idx = np.abs(al[:, :, None] - pal[:, None, :]).argmin(2).astype(np.uint64)  # (n, 16)
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1).astype(np.uint64)
    out = np.zeros((len(al), 8), np.uint8)
    out[:, 0] = a0.astype(np.uint8)
    out[:, 1] = a1.astype(np.uint8)
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def bleed(arr, thr=8):
    """Rellena el color de los pixeles transparentes con el del pixel opaco más cercano.

    Sin esto, el RGB bajo alfa 0 (negro) se mezcla en los bloques DXT y en los mipmaps, y las
    texturas recortadas (árboles, señales) salen con una orilla oscura punteada.
    """
    from scipy.ndimage import distance_transform_edt
    mask = arr[..., 3] > thr
    if mask.all() or not mask.any():
        return arr
    idx = distance_transform_edt(~mask, return_distances=False, return_indices=True)
    arr[..., :3] = arr[idx[0], idx[1], :3]
    return arr


def encode(img, alpha=None, max_size=None):
    """PIL Image -> bytes DDS. alpha=None decide según el canal alfa."""
    img = img.convert("RGBA")
    if max_size and max(img.size) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    a = np.asarray(img)
    if alpha is None:
        alpha = bool((a[..., 3] < 250).any())
    fourcc = b"DXT5" if alpha else b"DXT1"
    w, h = img.size
    levels = []
    cur = img
    while True:
        arr = np.asarray(cur).astype(np.float32)
        if alpha:
            arr = bleed(arr)
            cur = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
        bl = _blocks(arr)
        col = _color_blocks(bl[..., :3])
        if alpha:
            data = np.hstack([_alpha_blocks(bl[..., 3]), col]).tobytes()
        else:
            data = col.tobytes()
        levels.append(data)
        if cur.size == (1, 1):
            break
        nw, nh = max(1, cur.size[0] // 2), max(1, cur.size[1] // 2)
        cur = cur.resize((nw, nh), Image.BOX)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    hdr = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, len(levels[0]), 0, len(levels))
    hdr += b"\0" * 44
    hdr += struct.pack("<II4sIIIII", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    hdr += struct.pack("<IIIII", 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    assert len(hdr) == 128
    return hdr + b"".join(levels)


def normal_map(img, strength=2.0, blur=1.0):
    """Mapa normal en espacio tangente a partir de la luminancia (rojo = +x, verde = +y)."""
    from scipy.ndimage import gaussian_filter
    g = np.asarray(img.convert("L"), np.float32) / 255.0
    g = gaussian_filter(g, blur, mode="wrap")
    dx = (np.roll(g, -1, 1) - np.roll(g, 1, 1)) * strength
    dy = (np.roll(g, -1, 0) - np.roll(g, 1, 0)) * strength
    n = np.dstack([-dx, dy, np.ones_like(g)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    rgb = ((n * 0.5 + 0.5) * 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def decode_dxt1_preview(data):
    """Decodifica el nivel 0 de un DDS DXT1 (solo para pruebas)."""
    h, w = struct.unpack_from("<II", data, 12)
    nb = ((w + 3) // 4) * ((h + 3) // 4)
    blk = np.frombuffer(data, np.uint8, nb * 8, 128).reshape(-1, 8)
    c0 = blk[:, 0:2].copy().view("<u2")[:, 0].astype(np.uint32)
    c1 = blk[:, 2:4].copy().view("<u2")[:, 0].astype(np.uint32)
    bits = blk[:, 4:8].copy().view("<u4")[:, 0]
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    px = pal[np.arange(nb)[:, None], idx]                                        # (nb, 16, 3)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    im = px.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 3)
    return Image.fromarray(np.clip(im, 0, 255).astype(np.uint8)[:h, :w])
