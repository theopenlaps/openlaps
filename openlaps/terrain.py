"""Terreno: de un modelo de elevación a una malla tallada y texturizada.

1. dsm_to_dtm: Copernicus es un modelo de SUPERFICIE (incluye árboles y techos). Donde
   WorldCover dice bosque o zona urbana se aplica una apertura morfológica (mínimo y luego
   máximo en una ventana de ~70 m), que quita los bultos estrechos y deja el terreno.
   Con un MDE del INEGI (que ya es terreno) este paso se salta.
2. Remuestreo bicúbico a la rejilla de la malla (2-4 m).
3. Tallado: cada vértice busca el punto de calle más cercano (árbol k-d). Dentro de la franja
   plana se pone justo debajo de la calzada; fuera se mezcla con el terreno original con un
   ancho que crece con el desnivel (talud ~1:1.5), lo que da terraplenes y cortes creíbles.
4. Textura: por cada tesela se hornea una imagen con la cobertura de suelo, ruido continuo,
   roca en pendientes fuertes, orillas de terracería, veredas, estacionamientos y sombras de
   contacto de los edificios.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.ndimage import gaussian_filter, grey_opening, map_coordinates
from scipy.spatial import cKDTree

from . import config as C
from .textures import png


class Terrain:
    def __init__(self, proj, dem, landcover, dem_is_dtm, res, pad=300.0, log=print):
        self.proj = proj
        self.res = res
        self.log = log
        # rejilla de trabajo de 10 m, con margen para calles que se salen del área
        self.wres = 10.0
        x0, x1 = proj.xmin - pad, proj.xmax + pad
        y0, y1 = proj.ymin - pad, proj.ymax + pad
        nx = int(math.ceil((x1 - x0) / self.wres)) + 1
        ny = int(math.ceil((y1 - y0) / self.wres)) + 1
        self.wx0, self.wy0 = x0, y0
        gx = x0 + np.arange(nx) * self.wres
        gy = y0 + np.arange(ny) * self.wres
        GX, GY = np.meshgrid(gx, gy)
        z = dem.sample(proj, GX, GY, order=1)
        self.lc_work = landcover.sample(proj, GX, GY, order=0).astype(np.uint8) if landcover is not None else None
        if not dem_is_dtm:
            z = self.dsm_to_dtm(z)
        self.zw = gaussian_filter(z, 0.8)
        self.landcover = landcover
        log(f"  relieve: {self.zw.min():.0f}-{self.zw.max():.0f} m s.n.m.")

    def dsm_to_dtm(self, z):
        if self.lc_work is None:
            return gaussian_filter(z, 1.0)
        size = 7  # 70 m
        opened = grey_opening(z, size=(size, size))
        opened = gaussian_filter(opened, 1.5)
        lc = self.lc_work
        mask = np.zeros_like(z)
        mask[lc == C.WC_TREE] = 0.85
        mask[lc == C.WC_BUILT] = 0.9
        mask[lc == C.WC_MANGROVE] = 0.85
        mask = gaussian_filter(mask, 2.0)
        excess = np.maximum(0.0, z - (opened + 0.5))
        return z - mask * excess

    def z(self, x, y):
        """Altura del terreno sin tallar (bicúbica sobre la rejilla de 10 m)."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        c = (x - self.wx0) / self.wres
        r = (y - self.wy0) / self.wres
        return map_coordinates(self.zw, [r.ravel(), c.ravel()], order=3, mode="nearest").reshape(x.shape)

    # ------------------------------------------------------------------ malla final
    def build_grid(self):
        p = self.proj
        nx = int(round((p.xmax - p.xmin) / self.res)) + 1
        ny = int(round((p.ymax - p.ymin) / self.res)) + 1
        self.gx = np.linspace(p.xmin, p.xmax, nx)
        self.gy = np.linspace(p.ymin, p.ymax, ny)
        GX, GY = np.meshgrid(self.gx, self.gy)
        self.Z0 = self.z(GX, GY)
        self.Z = self.Z0.copy()
        self.hole = np.zeros_like(self.Z, bool)
        self.log(f"  rejilla del terreno: {nx} x {ny} vértices a {self.res:.1f} m")

    def flatten_urban(self, carve_pts, urban_fn, sigma_m=14.0):
        """Aplana las manzanas: en zona urbana el suelo sigue el nivel de sus calles.

        Se rasteriza la altura de las calles, se suaviza con una convolución normalizada
        (promedio gaussiano ponderado) y el terreno urbano se acerca a esa superficie,
        conservando solo un 25 % del relieve propio (que en un DSM suele ser techos).
        """
        if urban_fn is None or len(carve_pts) == 0:
            return
        GX, GY = np.meshgrid(self.gx, self.gy)
        ci = np.clip(np.round((carve_pts[:, 0] - self.gx[0]) / self.res).astype(int), 0, len(self.gx) - 1)
        ri = np.clip(np.round((carve_pts[:, 1] - self.gy[0]) / self.res).astype(int), 0, len(self.gy) - 1)
        sz = np.zeros_like(self.Z)
        cnt = np.zeros_like(self.Z)
        np.add.at(sz, (ri, ci), carve_pts[:, 2])
        np.add.at(cnt, (ri, ci), 1.0)
        sg = sigma_m / self.res
        bs, bc = gaussian_filter(sz, sg), gaussian_filter(cnt, sg)
        S = np.where(bc > 1e-6, bs / np.maximum(bc, 1e-6), self.Z)
        cover = np.clip(bc / (bc.max() * 0.02 + 1e-9), 0, 1)
        U = gaussian_filter(urban_fn(GX.ravel(), GY.ravel()).reshape(self.Z.shape).astype(float), 8.0 / self.res)
        w = U * cover
        target = S + np.clip(self.Z - S, -1.5, 1.5) * 0.25
        self.Z = self.Z * (1 - w) + target * w

    def carve(self, carve_pts, tunnel_pts=None):
        if len(carve_pts) == 0:
            return
        GX, GY = np.meshgrid(self.gx, self.gy)
        flat = np.column_stack([GX.ravel(), GY.ravel()])
        tree = cKDTree(carve_pts[:, :2])
        maxr = float(carve_pts[:, 3].max()) + 46.0   # alcance del talud más ancho (45 m)
        d, ii = tree.query(flat, k=6, distance_upper_bound=maxr)
        valid = np.isfinite(d)
        ii = np.where(valid, ii, 0)
        hw = carve_pts[ii, 3]
        zr = carve_pts[ii, 2]
        edge = np.where(valid, d - hw, 1e9)               # distancia a la orilla de la franja plana
        best = np.argmin(edge, axis=1)
        rows = np.arange(len(flat))
        e = edge[rows, best]
        zroad = zr[rows, best]
        z0 = self.Z.ravel().copy()
        dz = np.abs(z0 - zroad)
        # talud ~1:3 (antes 1:1.6): más tendido y natural, y estira menos la ortofoto
        blend = np.clip(dz * 3.0, 3.0, 45.0)
        t = np.clip(e / blend, 0, 1)
        t = t * t * (3 - 2 * t)
        inside = e <= 0
        znew = np.where(inside, zroad - 0.25, (zroad - 0.08) * (1 - t) + z0 * t)
        affected = e < blend
        z = np.where(affected, znew, z0)
        self.Z = z.reshape(self.Z.shape)
        if tunnel_pts is not None and len(tunnel_pts):
            tt = cKDTree(tunnel_pts[:, :2])
            d2, j2 = tt.query(flat, distance_upper_bound=float(tunnel_pts[:, 3].max()) + 1)
            ok = np.isfinite(d2)
            j2 = np.where(ok, j2, 0)
            near = ok & (d2 < tunnel_pts[j2, 3]) & (self.Z.ravel() - tunnel_pts[j2, 2] < 7.0)
            self.hole = near.reshape(self.Z.shape)

    def height(self, x, y):
        """Altura de la malla final (bilineal)."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        c = (x - self.gx[0]) / (self.gx[1] - self.gx[0])
        r = (y - self.gy[0]) / (self.gy[1] - self.gy[0])
        return map_coordinates(self.Z, [r.ravel(), c.ravel()], order=1, mode="nearest").reshape(x.shape)

    def normals(self):
        gy, gx = np.gradient(self.Z, self.gy, self.gx)
        n = np.dstack([-gx, -gy, np.ones_like(self.Z)])
        return n / np.linalg.norm(n, axis=2, keepdims=True)

    # ------------------------------------------------------------------ teselas
    def build_mesh(self, scene, painter, tile_m=128.0, mpp=0.5):
        N = self.normals()
        p = self.proj
        nx, ny = len(self.gx), len(self.gy)
        step = int(round(tile_m / self.res))
        ti = 0
        self.tiles = []
        for j0 in range(0, ny - 1, step):
            for i0 in range(0, nx - 1, step):
                i1, j1 = min(nx - 1, i0 + step), min(ny - 1, j0 + step)
                xs, ys = self.gx[i0:i1 + 1], self.gy[j0:j1 + 1]
                GX, GY = np.meshgrid(xs, ys)
                Z = self.Z[j0:j1 + 1, i0:i1 + 1]
                H = self.hole[j0:j1 + 1, i0:i1 + 1]
                pos = np.column_stack([GX.ravel(), GY.ravel(), Z.ravel()])
                nrm = N[j0:j1 + 1, i0:i1 + 1].reshape(-1, 3)
                x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
                uv = np.column_stack([(GX.ravel() - x0) / (x1 - x0), (y1 - GY.ravel()) / (y1 - y0)])
                w, h = len(xs), len(ys)
                idx = np.arange(w * h).reshape(h, w)
                a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
                c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
                faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
                hv = H.ravel()
                faces = faces[~(hv[faces].any(1))]
                name = f"terreno_{ti:03d}"
                tex = f"{name}.png"
                img = painter.paint_tile(x0, y0, x1, y1, mpp, self)
                self.tiles.append((x0, y0, x1, y1, img))
                scene.textures[tex] = png(img)
                scene.images[tex] = img
                scene.images[f"{name}_mask.png"] = painter.last_mask
                rep = tile_m / 2.0   # una repetición del detalle cada 2 m
                scene.material(name, tex, kind="multilayer", specular=0.0, ambient=0.4, diffuse=0.36, spec_exp=5.0,
                               extra={"mask": f"{name}_mask.png",
                                      "details": ["det_pasto.png", "det_tierra.png", "det_roca.png", "det_hojarasca.png"],
                                      "detail_nm": "det_nm.png", "mult": rep,
                                      "props": {"magicMult": 1.0, "fresnelC": 0.0, "fresnelEXP": 4.0, "fresnelMaxLevel": 0.0,
                                                "detailNMMult": [0.0, 1.0, rep, 0, 0, 0, 0, 0, 0, 0]}})
                scene.add("terreno", name, pos, uv, faces, normals=nrm, phys="GRASS")
                ti += 1
        self.log(f"  terreno: {ti} teselas de {tile_m:.0f} m con textura horneada a {mpp} m/px")


# --------------------------------------------------------------------------------------
def _hash_noise(x, y, scale, seed=0):
    """Ruido de valor continuo en coordenadas del mundo (empalma entre teselas)."""
    xs, ys = x / scale, y / scale
    xi, yi = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    xf, yf = xs - xi, ys - yi

    def h(a, b):
        v = (a * 374761393 + b * 668265263 + seed * 2246822519) & 0xFFFFFFFF
        v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
        return (v & 0xFFFF) / 65535.0
    u = xf * xf * (3 - 2 * xf)
    v = yf * yf * (3 - 2 * yf)
    return (h(xi, yi) * (1 - u) * (1 - v) + h(xi + 1, yi) * u * (1 - v)
            + h(xi, yi + 1) * (1 - u) * v + h(xi + 1, yi + 1) * u * v)


def fbm(x, y, base, octaves=4, seed=0):
    tot, amp, s, norm = 0.0, 1.0, base, 0.0
    for o in range(octaves):
        tot = tot + amp * _hash_noise(x, y, s, seed + o)
        norm += amp
        amp *= 0.5
        s /= 2.0
    return tot / norm


class GroundPainter:
    """Hornea la textura de cada tesela de terreno."""

    def __init__(self, landcover, proj, season, road_polys, paths, areas, buildings_poly, water_poly, s2=None,
                 ortho=None):
        self.s2 = s2
        self.ortho = ortho
        self.lc = landcover
        self.proj = proj
        self.season = 0 if season == "seca" else 1
        self.road_polys = road_polys
        self.paths = paths            # lista de (coords Nx2, ancho)
        self.areas = areas            # lista de (polígono, color RGB)
        self.buildings = buildings_poly
        self.water = water_poly

    def paint_tile(self, x0, y0, x1, y1, mpp, terrain):
        W = int(round((x1 - x0) / mpp))
        H = int(round((y1 - y0) / mpp))
        px = x0 + (np.arange(W) + 0.5) * mpp
        py = y1 - (np.arange(H) + 0.5) * mpp
        PX, PY = np.meshgrid(px, py)
        # cobertura de suelo a 10 m, con borde difuminado por ruido para que no se vean cuadros
        jx = PX + (fbm(PX, PY, 9.0, 2, 11) - 0.5) * 14
        jy = PY + (fbm(PX, PY, 9.0, 2, 23) - 0.5) * 14
        if self.lc is not None:
            cls = self.lc.sample(self.proj, jx, jy, order=0).astype(np.int32)
        else:
            cls = np.full(PX.shape, C.WC_GRASS)
        col = np.zeros(PX.shape + (3,), np.float32)
        for k, cc in C.GROUND_COLORS.items():
            m = cls == k
            if m.any():
                col[m] = cc[self.season]
        unknown = ~np.isin(cls, list(C.GROUND_COLORS))
        col[unknown] = C.GROUND_COLORS[C.WC_GRASS][self.season]
        col = np.asarray(Image.fromarray(col.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), np.float32)
        # ruido a varias escalas (manchas de pasto, tierra)
        n1 = fbm(PX, PY, 40.0, 4, 1)
        n2 = fbm(PX, PY, 3.0, 3, 5)
        col *= (0.78 + 0.34 * n1)[..., None] * (0.9 + 0.2 * n2)[..., None]
        dirt = np.array([150, 125, 95], np.float32)
        patch = np.clip((fbm(PX, PY, 25.0, 3, 9) - 0.62) * 4, 0, 1)[..., None]
        col = col * (1 - patch * 0.6) + dirt * patch * 0.6
        # imagen Sentinel-2 (10 m) como base: da los colores y manchas reales del lugar;
        # lo procedural solo aporta el grano fino que el satélite no ve
        if self.ortho is not None:
            # ortofoto de 20-25 cm: es la base; el ruido solo aporta grano
            ph = self.ortho.sample(self.proj, PX, PY, order=1)
            ok = ph.sum(-1) > 15
            ph = ph * (0.94 + 0.12 * n2)[..., None]
            col = np.where(ok[..., None], ph * 0.94 + col * 0.06, col)
        elif self.s2 is not None:
            sat = self.s2.sample(self.proj, PX, PY, order=3)
            ok = sat.sum(-1) > 15
            sat = np.clip(sat * 1.05, 0, 255) * (0.88 + 0.24 * n2)[..., None]
            # en zona construida el píxel de 10 m mezcla techos con suelo: pesa menos
            wsat = np.where(cls == C.WC_BUILT, 0.3, 0.78)[..., None]
            col = np.where(ok[..., None], sat * wsat + col * (1 - wsat), col)
        # roca en pendientes fuertes
        gz = terrain.height(PX, PY)
        gy_, gx_ = np.gradient(gz, mpp)
        slope = np.degrees(np.arctan(np.hypot(gx_, gy_)))
        rockw = np.clip((slope - 32) / 12, 0, 1)
        rock = rockw[..., None] * (0.6 + 0.4 * n2[..., None])
        col = col * (1 - rock) + np.array([128, 118, 108], np.float32) * rock
        # máscara del shader multicapa: R pasto, G tierra, B roca, A hojarasca
        m = np.zeros(PX.shape + (4,), np.float32)
        m[..., 0] = np.isin(cls, [C.WC_GRASS, C.WC_CROP, C.WC_SHRUB, C.WC_WETLAND, C.WC_MOSS])
        m[..., 1] = np.isin(cls, [C.WC_BARE, C.WC_BUILT, C.WC_SNOW]) | ~np.isin(cls, list(C.GROUND_COLORS))
        m[..., 3] = np.isin(cls, [C.WC_TREE, C.WC_MANGROVE])
        m[..., 1] = np.maximum(m[..., 1], patch[..., 0])
        m[..., 2] = rockw
        m[..., :2] *= (1 - rockw)[..., None]
        m[..., 3] *= (1 - rockw)
        m = np.asarray(Image.fromarray((m * 255).astype(np.uint8), "RGBA").filter(ImageFilter.GaussianBlur(3)), np.float32)
        tot = np.maximum(m.sum(-1, keepdims=True), 1)
        self.last_mask = Image.fromarray(np.clip(m / tot * 255, 0, 255).astype(np.uint8), "RGBA")
        img = Image.fromarray(col.clip(0, 255).astype(np.uint8), "RGB")
        d = ImageDraw.Draw(img, "RGBA")

        def to_px(coords):
            c = np.asarray(coords)
            return [((x - x0) / mpp, (y1 - y) / mpp) for x, y in c[:, :2]]

        from shapely.geometry import box as sbox
        tb = sbox(x0 - 5, y0 - 5, x1 + 5, y1 + 5)
        for poly, color in self.areas:
            if not poly.intersects(tb):
                continue
            for g in _polys(poly.intersection(tb)):
                d.polygon(to_px(g.exterior.coords), fill=color + (200,))
        if self.water is not None and self.water.intersects(tb):
            for g in _polys(self.water.intersection(tb)):
                d.polygon(to_px(g.exterior.coords), fill=(48, 80, 96, 255))
        for coords, width in self.paths:
            c = np.asarray(coords)
            if c[:, 0].max() < x0 - 5 or c[:, 0].min() > x1 + 5 or c[:, 1].max() < y0 - 5 or c[:, 1].min() > y1 + 5:
                continue
            d.line(to_px(c), fill=(156, 132, 100, 230), width=max(1, int(width / mpp)), joint="curve")
        # orilla de terracería y sombra de contacto bajo las calles
        for poly, e in self.road_polys:
            if not poly.intersects(tb):
                continue
            g = poly.buffer(1.4 if not e.urban else 0.6).intersection(tb)
            for gg in _polys(g):
                d.polygon(to_px(gg.exterior.coords), fill=(120, 104, 84, 210))
        if self.buildings is not None and self.buildings.intersects(tb):
            for gg in _polys(self.buildings.buffer(0.9).intersection(tb)):
                d.polygon(to_px(gg.exterior.coords), fill=(70, 62, 54, 150))
        return img.filter(ImageFilter.SMOOTH)


def mosaic(tiles, proj, mpp_out=2.0):
    """Une las texturas horneadas en una sola vista cenital (vista previa)."""
    W = int((proj.xmax - proj.xmin) / mpp_out) + 1
    H = int((proj.ymax - proj.ymin) / mpp_out) + 1
    out = Image.new("RGB", (W, H), (0, 0, 0))
    for x0, y0, x1, y1, img in tiles:
        w = max(1, int(round((x1 - x0) / mpp_out)))
        h = max(1, int(round((y1 - y0) / mpp_out)))
        out.paste(img.resize((w, h), Image.LANCZOS), (int((x0 - proj.xmin) / mpp_out), int((proj.ymax - y1) / mpp_out)))
    return out


def _polys(g):
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
