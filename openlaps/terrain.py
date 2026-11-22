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
import shapely
from shapely.ops import unary_union
from numba import njit
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
        # alineada a múltiplos de 10 m: así dos áreas vecinas (pedazos del modo en vivo) muestrean el relieve en
        # los mismos puntos y sus bordes casan
        x0 = math.floor((proj.xmin - pad) / self.wres) * self.wres
        y0 = math.floor((proj.ymin - pad) / self.wres) * self.wres
        x1, y1 = proj.xmax + pad, proj.ymax + pad
        nx = int(math.ceil((x1 - x0) / self.wres)) + 1
        ny = int(math.ceil((y1 - y0) / self.wres)) + 1
        self.wx0, self.wy0 = x0, y0
        gx = x0 + np.arange(nx) * self.wres
        gy = y0 + np.arange(ny) * self.wres
        GX, GY = np.meshgrid(gx, gy)
        # media de 4 muestras por celda: con un MDE fino (1-5 m) una sola muestra cada 10 m recogía el ruido del
        # lidar (bordillos, coches, muretes) y el relieve salía con bultos que el MDE no tiene
        q = self.wres / 4
        z = np.mean([dem.sample(proj, GX + dx, GY + dy, order=1) for dx in (-q, q) for dy in (-q, q)], axis=0)
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
        # umbral fijo (antes, el 2 % del máximo del área: dos pedazos vecinos del modo en vivo, con máximos
        # distintos, aplanaban distinto y el terreno no casaba en la costura)
        cover = np.clip(bc / (0.06 * (self.res / 2.0) ** 2), 0, 1)
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
        d, ii = tree.query(flat, k=8, distance_upper_bound=maxr)
        valid = np.isfinite(d)
        ii = np.where(valid, ii, 0)
        hw = carve_pts[ii, 3]
        zr = carve_pts[ii, 2]
        edge = np.where(valid, d - hw, 1e9)               # distancia a la orilla de la franja plana
        e = edge.min(axis=1)
        # altura de la calle: media de los puntos cercanos con mucho más peso para el más pegado. Con solo el más
        # cercano, la altura saltaba donde el vértice cambiaba de un punto a otro (de una calle a la de al lado,
        # o a lo largo de una calle en cuesta) y el talud salía a escalones
        w = np.where(valid, 1.0 / (np.maximum(edge, 0.0) + 0.3) ** 4, 0.0)
        zroad = (w * zr).sum(axis=1) / np.maximum(w.sum(axis=1), 1e-12)
        z0 = self.Z.ravel().copy()
        zroad = np.where(valid.any(axis=1), zroad, z0)
        dz = np.abs(z0 - zroad)
        # talud ~1:3 (antes 1:1.6): más tendido y natural, y estira menos la ortofoto. El ancho del talud se
        # suaviza en la rejilla: depende del desnivel de cada vértice y de uno al siguiente cambiaba a saltos
        blend = np.clip(dz * 3.0, 3.0, 45.0)
        blend = np.maximum(3.0, gaussian_filter(blend.reshape(self.Z.shape), 4.0 / self.res).ravel())
        t = np.clip(e / blend, 0, 1)
        t = t * t * t * (t * (6 * t - 15) + 10)          # sin quiebre de pendiente ni al pie ni en la cabeza del talud
        inside = e <= 0
        znew = np.where(inside, zroad - 0.25, (zroad - 0.08) * (1 - t) + z0 * t)
        affected = e < blend
        z = np.where(affected, znew, z0).reshape(self.Z.shape)
        # segunda pasada: alisado del talud. Solo fuera de la franja de la calle (dentro manda la calzada), con
        # todo su efecto a partir de ~4 m de la orilla y nada donde el relieve no se tocó
        eg = e.reshape(self.Z.shape)
        m = np.clip((eg - 0.6) / 3.5, 0.0, 1.0)
        m = m * m * (3 - 2 * m) * gaussian_filter(affected.reshape(self.Z.shape).astype(float), 3.0 / self.res)
        for _ in range(2):
            z = z * (1 - m) + gaussian_filter(z, 2.5 / self.res) * m
        self.Z = z

    def bajo_calles(self, carve_pts, calles, margen=0.2):
        """Dentro de las calzadas el terreno nunca queda por encima de la calle. El tallado toma la altura de la
        franja más cercana y, donde se juntan dos (una calle peatonal pegada a la banqueta de otra, más alta),
        el terreno asomaba por encima del adoquín: los rombos marrones a lo largo de las fachadas."""
        if calles is None or calles.is_empty or len(carve_pts) == 0:
            return
        interior = carve_pts[carve_pts[:, 3] <= 0.8]          # puntos del interior de las calzadas
        if not len(interior):
            return
        GX, GY = np.meshgrid(self.gx, self.gy)
        dentro = shapely.contains_xy(calles, GX, GY)
        if not dentro.any():
            return
        d, ii = cKDTree(interior[:, :2]).query(np.column_stack([GX[dentro], GY[dentro]]), k=3)
        zr = interior[ii, 2].min(axis=1)
        self.Z[dentro] = np.minimum(self.Z[dentro], zr - margen)

    def z_plano(self, Zg, x, y):
        """Altura exacta de la malla del terreno (los mismos triángulos que build_mesh) en (x, y)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        # el paso real de la rejilla: el área rara vez mide un múltiplo exacto de `res`, y con `res` la altura
        # salía de un punto corrido hasta 1 m en el otro extremo de la pista
        fx_ = (x - self.gx[0]) / (self.gx[1] - self.gx[0])
        fy_ = (y - self.gy[0]) / (self.gy[1] - self.gy[0])
        i = np.clip(np.floor(fx_).astype(int), 0, len(self.gx) - 2)
        j = np.clip(np.floor(fy_).astype(int), 0, len(self.gy) - 2)
        fx, fy = fx_ - i, fy_ - j
        za, zb = Zg[j, i], Zg[j, i + 1]
        zc, zd = Zg[j + 1, i + 1], Zg[j + 1, i]
        abc = za + fx * (zb - za) + fy * (zc - zb)       # triángulo (a, b, c): fx >= fy
        acd = za + fy * (zd - za) + fx * (zc - zd)       # triángulo (a, c, d): fy > fx
        return np.where(fx >= fy, abc, acd)

    def cubrir_tuneles(self, huella, bocas, tunnel_pts, scene, alto=5.8, tierra=0.8):
        """Terreno sobre los túneles, como en los juegos: un mapa de alturas no puede pasar por
        encima de un hueco, así que se QUITAN los triángulos del terreno que tocan la huella del
        túnel y se sustituyen por

        - una tapa recortada exactamente en la línea de los muros, a la altura del terreno o, si
          el terreno queda por debajo, encima del techo con `tierra` m de tierra;
        - la parte de esos triángulos que cae fuera de la huella, a su altura original exacta
          (empalma sin rendijas con el terreno de alrededor);
        - un muro de contención en la línea de los muros donde la tapa queda más alta que el
          terreno de fuera, y un muro frontal sobre la abertura en cada boca.

        Así el salto entre el terreno sobre el túnel y una rampa baja de al lado es vertical, en
        el muro. Antes la rejilla de 2 m lo hacía con pendientes bruscas: conos y "tiendas" de
        terreno junto a las bocas, picos dentro del túnel o la losa del techo a la vista."""
        self.parches = []
        if huella is None or huella.is_empty or not len(tunnel_pts):
            return
        from shapely.geometry import LineString
        from .roads import iter_polys, triangulate_polygon
        tt = cKDTree(tunnel_pts[:, :2])

        def z_techo(x, y):
            # la cota más alta de los tramos cercanos (en un cruce de tubos a distinta altura,
            # la media dejaba la tapa por debajo del techo del tubo más alto)
            d, ii = tt.query(np.column_stack([np.ravel(x), np.ravel(y)]), k=min(8, len(tunnel_pts)))
            d, ii = np.atleast_2d(d), np.atleast_2d(ii)
            z = np.where(d < d[:, :1] + 6.0, tunnel_pts[ii, 2], -1e9)
            return z.max(1) + alto

        Z0 = self.Z.copy()
        nx, ny = len(self.gx), len(self.gy)
        if not hasattr(self, "quitar1"):
            self.quitar1 = np.zeros((ny - 1, nx - 1), bool)
            self.quitar2 = np.zeros((ny - 1, nx - 1), bool)
        bx0, by0, bx1, by1 = huella.bounds
        i0 = max(0, int((bx0 - self.gx[0]) / self.res) - 1)
        i1 = min(nx - 2, int((bx1 - self.gx[0]) / self.res) + 1)
        j0 = max(0, int((by0 - self.gy[0]) / self.res) - 1)
        j1 = min(ny - 2, int((by1 - self.gy[0]) / self.res) + 1)
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        I, J = I.ravel(), J.ravel()
        xa, xb = self.gx[I], self.gx[I + 1]
        ya, yb = self.gy[J], self.gy[J + 1]
        shapely.prepare(huella)
        tapa_tri, fuera_tri = [], []
        for k, (pa, pb, pc) in enumerate((((xa, ya), (xb, ya), (xb, yb)),      # (a, b, c)
                                          ((xa, ya), (xb, yb), (xa, yb)))):    # (a, c, d)
            coords = np.stack([np.column_stack(pa), np.column_stack(pb), np.column_stack(pc), np.column_stack(pa)], 1)
            tri = shapely.polygons(coords)
            toca = shapely.intersects(huella, tri)
            (self.quitar1 if k == 0 else self.quitar2)[J[toca], I[toca]] = True
            dentro = toca & shapely.contains(huella, tri)
            borde = toca & ~dentro
            tapa_tri.append(coords[dentro, :3])
            for g in tri[borde]:
                for part, lista in ((g.intersection(huella), "tapa"), (g.difference(huella), "fuera")):
                    for pp in iter_polys(part):
                        if pp.area > 1e-4:
                            v, f = triangulate_polygon(pp, 1e9)
                            if len(f):
                                (tapa_tri if lista == "tapa" else fuera_tri).append(v[f])
        tapa = np.concatenate([t.reshape(-1, 3, 2) for t in tapa_tri if len(t)]) if tapa_tri else np.zeros((0, 3, 2))
        fuera = np.concatenate([t.reshape(-1, 3, 2) for t in fuera_tri if len(t)]) if fuera_tri else np.zeros((0, 3, 2))
        # alturas: la de la malla original (exacta) y, en la tapa, nunca por debajo del techo + tierra
        if len(tapa):
            v = tapa.reshape(-1, 2)
            z = np.maximum(self.z_plano(Z0, v[:, 0], v[:, 1]), z_techo(v[:, 0], v[:, 1]) + 0.3 + tierra)
            self.parches.append((np.column_stack([v, z]), np.arange(len(v)).reshape(-1, 3)))
        if len(fuera):
            v = fuera.reshape(-1, 2)
            self.parches.append((np.column_stack([v, self.z_plano(Z0, v[:, 0], v[:, 1])]),
                                 np.arange(len(v)).reshape(-1, 3)))
        # muro de contención en la línea de los muros (y frontal sobre la abertura en las bocas)
        bocas = [LineString(b[:, :2]) for b in bocas if len(b) >= 2]
        boca_u = unary_union(bocas).buffer(0.5) if bocas else None
        for pg in iter_polys(huella):
            for ring in [pg.exterior] + list(pg.interiors):
                r = np.asarray(shapely.segmentize(ring, 1.0).coords)[:, :2]
                if len(r) < 2:
                    continue
                zb = self.z_plano(Z0, r[:, 0], r[:, 1])
                zt = np.maximum(zb, z_techo(r[:, 0], r[:, 1]) + 0.3 + tierra)
                if boca_u is not None:
                    en_boca = shapely.contains_xy(boca_u, r[:, 0], r[:, 1])
                    # en la boca el muro empieza en el techo: debajo está la abertura
                    zb = np.where(en_boca, np.maximum(zb, z_techo(r[:, 0], r[:, 1])), zb)
                alt = zt - zb
                n = len(r)
                pos = np.vstack([np.column_stack([r, zt]), np.column_stack([r, zb])])
                k = np.arange(n - 1)
                ok = (alt[:-1] > 0.05) | (alt[1:] > 0.05)
                k = k[ok]
                if not len(k):
                    continue
                f = np.concatenate([np.column_stack([k, k + n, k + n + 1]), np.column_stack([k, k + n + 1, k + 1])])
                f = np.concatenate([f, f[:, ::-1]])          # las dos caras
                d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(r, axis=0), axis=1))])
                uv = np.vstack([np.column_stack([d / 3.0, zt / 3.0]), np.column_stack([d / 3.0, zb / 3.0])])
                scene.add("tunel", "muro", pos, uv, f, phys="WALL")
        # la altura de consulta (árboles, edificios) sobre la tapa, la de la tapa
        GX, GY = np.meshgrid(self.gx, self.gy)
        sub = shapely.contains_xy(huella, GX, GY)
        if sub.any():
            self.Z[sub] = np.maximum(self.Z[sub], z_techo(GX[sub], GY[sub]) + 0.3 + tierra)

    def cunetas(self, net, ocupado=None, evitar=None):
        """Cunetas a los dos lados de los caminos sin asfalto: una zanja en V de CUNETA_ANCHO m pegada a la orilla.

        La rejilla del terreno (2-4 m) no puede tallar una zanja de menos de 2 m, así que la cuneta es una franja de
        malla propia, fina, y el terreno se recorta por donde pasa: sus triángulos se cortan con exactitud (como en
        las bocas de túnel) y la franja se mete un poco por debajo del corte, sin rendijas. Lleva la textura y la
        física del terreno. `ocupado`: todo lo construido (calzadas, cruces, banquetas); `evitar`: agua y edificios.
        Llamar con el terreno ya tallado y antes de build_mesh. Devuelve los metros de cuneta."""
        from rasterio import features
        from rasterio.transform import Affine
        from .roads import _svals, iter_polys, triangulate_polygon
        if not hasattr(self, "parches"):
            self.parches = []
        prohibido = [g for g in (ocupado.buffer(0.25) if ocupado is not None else None, evitar) if g is not None]
        prohibido = unary_union(prohibido) if prohibido else None
        if prohibido is not None:
            shapely.prepare(prohibido)
        xmin, xmax, ymin, ymax = self.gx[0], self.gx[-1], self.gy[0], self.gy[-1]
        franjas, cuadros, metros = [], [], 0.0
        for e in net.edges:
            if e.perfil is None or e.urban or e.bridge or e.tunnel or e.cls in C.VEREDAS:   # (una senda no lleva cuneta)
                continue
            for a, b in net.ranges(e):
                sv = _svals(e, a, b)
                sv = np.unique(np.concatenate([sv[::2], sv[-1:]]))
                hondo = CUNETA_HONDO * e.fundido(sv)
                for lado in (1.0, -1.0):
                    orilla = e.at(sv, lado * e.hw)
                    medio = e.at(sv, lado * (e.hw + CUNETA_FONDO))
                    corte = e.at(sv, lado * (e.hw + CUNETA_ANCHO - CUNETA_SOLAPE))
                    fuera = e.at(sv, lado * (e.hw + CUNETA_ANCHO))
                    lejos = e.at(sv, lado * (e.hw + CUNETA_ANCHO + 0.5))
                    vale = (hondo > 0.03) & (fuera[:, 0] > xmin + 1) & (fuera[:, 0] < xmax - 1) \
                        & (fuera[:, 1] > ymin + 1) & (fuera[:, 1] < ymax - 1)
                    if prohibido is not None:
                        for q in (medio, corte, fuera):
                            vale &= ~shapely.contains_xy(prohibido, q[:, 0], q[:, 1])
                    orilla[:, 2] -= 0.03                      # bajo la orilla de la calzada: la tapa su faldón
                    medio[:, 2] = np.minimum(self.z_plano(self.Z, medio[:, 0], medio[:, 1]), orilla[:, 2]) - hondo
                    # el borde exterior asoma un poco sobre el terreno y luego se hunde bajo él: la franja va recta
                    # entre fila y fila y el terreno no, y si acabara justo a ras quedaban rendijas por las que se
                    # veía el cielo
                    fuera[:, 2] = self.z_plano(self.Z, fuera[:, 0], fuera[:, 1]) + 0.08
                    lejos[:, 2] = self.z_plano(self.Z, lejos[:, 0], lejos[:, 1]) - 0.35
                    for i in np.flatnonzero(vale[:-1] & vale[1:]):
                        j = i + 1
                        fila = np.array([orilla[i], medio[i], fuera[i], lejos[i], orilla[j], medio[j], fuera[j], lejos[j]])
                        caras = np.array([[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6]])
                        if np.cross(fila[1] - fila[0], fila[5] - fila[0])[2] < 0:
                            caras = caras[:, ::-1]
                        franjas.append(fila[caras].reshape(-1, 3))
                        cuadros.append(shapely.Polygon([orilla[i, :2], corte[i, :2], corte[j, :2], orilla[j, :2]]))
                        metros += float(sv[j] - sv[i])
        if not franjas:
            return 0.0
        zona = unary_union(cuadros).buffer(0)
        if ocupado is not None:
            zona = zona.difference(ocupado)
        if zona.is_empty:
            return 0.0
        # triángulos de la rejilla que toca la zona: fuera los de dentro, y los del borde se cortan
        nx, ny = len(self.gx), len(self.gy)
        if not hasattr(self, "quitar1"):
            self.quitar1 = np.zeros((ny - 1, nx - 1), bool)
            self.quitar2 = np.zeros((ny - 1, nx - 1), bool)
        tr = Affine(self.gx[1] - self.gx[0], 0, self.gx[0], 0, self.gy[1] - self.gy[0], self.gy[0])
        cerca = features.rasterize([(zona, 1)], out_shape=(ny - 1, nx - 1), transform=tr, fill=0, all_touched=True)
        J, I = np.nonzero(cerca)
        xa, xb, ya, yb = self.gx[I], self.gx[I + 1], self.gy[J], self.gy[J + 1]
        shapely.prepare(zona)
        resto = []
        for k, (pa, pb, pc) in enumerate((((xa, ya), (xb, ya), (xb, yb)), ((xa, ya), (xb, yb), (xa, yb)))):
            quitar = self.quitar1 if k == 0 else self.quitar2
            coords = np.stack([np.column_stack(pa), np.column_stack(pb), np.column_stack(pc), np.column_stack(pa)], 1)
            tri = shapely.polygons(coords)
            toca = shapely.intersects(zona, tri) & ~quitar[J, I]      # (lo ya quitado, p. ej. un túnel, no se toca)
            quitar[J[toca], I[toca]] = True
            for g in tri[toca & ~shapely.contains(zona, tri)]:
                for pp in iter_polys(g.difference(zona)):
                    if pp.area > 1e-4:
                        v, f = triangulate_polygon(pp, 1e9)
                        if len(f):
                            resto.append(v[f].reshape(-1, 2))
        if resto:
            v = np.concatenate(resto)
            self.parches.append((np.column_stack([v, self.z_plano(self.Z, v[:, 0], v[:, 1])]),
                                 np.arange(len(v)).reshape(-1, 3)))
        v = np.concatenate(franjas)
        self.parches.append((v, np.arange(len(v)).reshape(-1, 3)))
        self.log(f"  cunetas: {metros / 1000:.1f} km a los lados de los caminos sin asfalto ({len(v) // 3} triángulos)")
        return metros

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
    def build_mesh(self, scene, painter, tile_m=128.0, mpp=0.5, cobertura=None):
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
                solo = getattr(self, "solo", None)    # modo en vivo: solo las teselas del centro del pedazo
                if solo is not None and not (solo[0] <= (x0 + x1) / 2 <= solo[2] and solo[1] <= (y0 + y1) / 2 <= solo[3]):
                    continue
                zona = getattr(self, "zona", None)    # tramo largo: solo las teselas que toca el corredor
                if zona is not None and not zona.intersects(shapely.box(x0, y0, x1, y1)):
                    continue
                uv = np.column_stack([(GX.ravel() - x0) / (x1 - x0), (y1 - GY.ravel()) / (y1 - y0)])
                w, h = len(xs), len(ys)
                idx = np.arange(w * h).reshape(h, w)
                a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
                c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
                faces = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
                hv = H.ravel()
                quita = hv[faces].any(1)
                if hasattr(self, "quitar1"):
                    quita |= np.concatenate([self.quitar1[j0:j1, i0:i1].ravel(), self.quitar2[j0:j1, i0:i1].ravel()])
                faces = faces[~quita]
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
                for fis, sel in fisica_del_suelo(pos, faces, cobertura):
                    usados = np.unique(sel)
                    remap = np.full(len(pos), -1)
                    remap[usados] = np.arange(len(usados))
                    scene.add("terreno", name, pos[usados], uv[usados], remap[sel], normals=nrm[usados], phys=fis)
                # tapa y franja de los túneles que caen en esta tesela, con su misma textura
                for ppos, pf in getattr(self, "parches", []):
                    cen = ppos[pf].mean(1)
                    en = (cen[:, 0] >= x0) & (cen[:, 0] <= x1) & (cen[:, 1] >= y0) & (cen[:, 1] <= y1)
                    if en.any():
                        fsel = pf[en]
                        usados = np.unique(fsel)
                        remap = np.full(len(ppos), -1)
                        remap[usados] = np.arange(len(usados))
                        q = ppos[usados]
                        quv = np.column_stack([(q[:, 0] - x0) / (x1 - x0), (y1 - q[:, 1]) / (y1 - y0)])
                        ff = remap[fsel]
                        tri = q[ff]
                        arriba = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2] > 0
                        ff[~arriba] = ff[~arriba][:, ::-1]
                        scene.add("terreno", name, q, quv, ff, phys="GRASS")
                ti += 1
        self.log(f"  terreno: {ti} teselas de {tile_m:.0f} m con textura horneada a {mpp} m/px")


CUNETA_ANCHO = 1.6        # m desde la orilla de la calzada hasta donde la cuneta vuelve al terreno
CUNETA_FONDO = 0.7        # m de la orilla al fondo de la zanja
CUNETA_HONDO = 0.25       # m de hondura (crece desde cero al salir de los cruces, como el perfil del camino)
CUNETA_SOLAPE = 0.2       # m que el terreno recortado monta sobre el borde exterior de la franja

# Física de Assetto Corsa del suelo según lo que hay en cada sitio (clases de cobertura.py). Antes todo el terreno
# era pasto: salirse a una playa, a un pedregal o a un lago se sentía igual.
FISICA_SUELO = {1: "WATER", 2: "WATER", 3: "GRASS", 4: "SAND", 5: "DIRT", 6: "GRASS", 7: "GRASS", 8: "GRASS",
                9: "MXROCA", 10: "SAND", 11: "GRASS", 12: "DIRT"}
PENDIENTE_ROCA = 38.0     # grados: más inclinado que esto el suelo se pinta de roca, y pisa como roca
PARCHE_MIN = 12           # triángulos: un parche menor dentro de una tesela no merece malla propia


def fisica_del_suelo(pos, faces, cobertura):
    """Parte los triángulos de una tesela de terreno por su física: [(clave, caras)]. Sin mapa de cobertura,
    todo pasto salvo la roca de las pendientes."""
    if not len(faces):
        return []
    tri = pos[faces]
    cen = tri.mean(1)
    claves = ["GRASS", "DIRT", "SAND", "MXROCA", "WATER"]
    k = np.zeros(len(faces), np.int64)
    if cobertura is not None:
        xmin, ymin, xmax, ymax = cobertura.rect
        col = np.floor((cen[:, 0] - xmin) / cobertura.res).astype(np.int64)
        fil = np.floor((ymax - cen[:, 1]) / cobertura.res).astype(np.int64)
        dentro = (col >= 0) & (col < cobertura.W) & (fil >= 0) & (fil < cobertura.H)
        lut = np.zeros(256, np.int64)
        for clase, fis in FISICA_SUELO.items():
            lut[clase] = claves.index(fis)
        k[dentro] = lut[cobertura.cls[fil[dentro], col[dentro]]]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    pend = np.degrees(np.arctan2(np.hypot(n[:, 0], n[:, 1]), np.abs(n[:, 2])))
    k[(pend > PENDIENTE_ROCA) & (k != claves.index("WATER"))] = claves.index("MXROCA")
    cuenta = np.bincount(k, minlength=len(claves))
    mayor = int(np.argmax(cuenta))
    for i in range(len(claves)):
        if 0 < cuenta[i] < PARCHE_MIN and i != mayor:
            k[k == i] = mayor
    return [(claves[i], faces[k == i]) for i in range(len(claves)) if (k == i).any()]


# --------------------------------------------------------------------------------------
@njit(cache=True)
def _h(a, b, seed):
    v = (a * 374761393 + b * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
    return (v & 0xFFFF) / 65535.0


@njit(cache=True)
def _hash_noise_nb(x, y, scale, seed, out):
    for k in range(x.size):
        xs = x[k] / scale
        ys = y[k] / scale
        xi = np.int64(np.floor(xs))
        yi = np.int64(np.floor(ys))
        xf = xs - xi
        yf = ys - yi
        u = xf * xf * (3 - 2 * xf)
        v = yf * yf * (3 - 2 * yf)
        out[k] = (_h(xi, yi, seed) * (1 - u) * (1 - v) + _h(xi + 1, yi, seed) * u * (1 - v)
                  + _h(xi, yi + 1, seed) * (1 - u) * v + _h(xi + 1, yi + 1, seed) * u * v)


def _hash_noise(x, y, scale, seed=0):
    """Ruido de valor continuo en coordenadas del mundo (empalma entre teselas). En numba: con
    numpy (arrays enteros intermedios por cada esquina) el horneado del terreno tardaba ~10 s más."""
    x = np.asarray(x, np.float64)
    y = np.broadcast_to(np.asarray(y, np.float64), x.shape)
    out = np.empty(x.size, np.float64)
    _hash_noise_nb(np.ascontiguousarray(x).ravel(), np.ascontiguousarray(y).ravel(), float(scale), int(seed), out)
    return out.reshape(x.shape)


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
        # orilla de terracería bajo cada calle: se calcula una vez (antes, en cada tesela: ~35 000
        # buffers en el centro de Madrid) y se busca por tesela con un índice espacial
        self._orillas = [poly.buffer(1.4 if not e.urban else 0.6) for poly, e in road_polys]
        self._orillas_idx = shapely.STRtree(self._orillas) if self._orillas else None
        self._edif_sombra = buildings_poly.buffer(0.9) if buildings_poly is not None else None
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
        cerca = sorted(int(k) for k in self._orillas_idx.query(tb)) if self._orillas_idx is not None else []
        for k in cerca:
            if not self.road_polys[k][0].intersects(tb):
                continue
            g = self._orillas[k].intersection(tb)
            for gg in _polys(g):
                d.polygon(to_px(gg.exterior.coords), fill=(120, 104, 84, 210))
        if self.buildings is not None and self.buildings.intersects(tb):
            for gg in _polys(self._edif_sombra.intersection(tb)):
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
