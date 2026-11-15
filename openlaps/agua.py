"""Agua en 3D: nivel de cada lámina, cauce excavado en el terreno y malla de agua.

- Agua quieta (lagos, embalses, charcas, mar): una sola cota por masa de agua, la de su orilla.
- Agua que corre (ríos, canales, arroyos): la lámina sigue el fondo del valle. Se toma el mínimo
  del terreno en una ventana de ~30 m alrededor (el fondo del cauce) y se suaviza, así baja con
  el río sin escalones y nunca queda por encima de las orillas.
- El terreno bajo el agua se hunde (más hondo cuanto más lejos de la orilla) y en una franja de
  la orilla se baja hasta casi el nivel del agua, para que no quede un escalón ni el agua flote.
- Donde una calle sin puente cruza el agua (vado, alcantarilla) no se excava ni se pone agua.
"""
import numpy as np
import shapely
from scipy.ndimage import distance_transform_edt, gaussian_filter, map_coordinates, minimum_filter
from shapely.ops import unary_union

from .roads import iter_polys, triangulate_polygon

BAJO_LAMINA = 0.25      # la lámina de un río queda este tanto por debajo del fondo suavizado del valle
MAX_HUNDIDO = 1.2       # ...pero nunca más de esto bajo el terreno original (arroyos a media ladera)
ORILLA = 4.0            # m de franja de orilla que se rebaja hasta el agua


class Agua:
    def __init__(self, cob, terr, calles_sin_puente=None, log=print):
        self.terr = terr
        excl = calles_sin_puente.buffer(1.0) if calles_sin_puente is not None and not calles_sin_puente.is_empty else None
        self.quieta, self.corriente = [], []
        for lst, out in ((cob.quieta, self.quieta), (cob.corriente, self.corriente)):
            for g in lst:
                if excl is not None and g.intersects(excl):
                    g = g.difference(excl)
                out += [p for p in iter_polys(g) if p.area >= 4.0]
        res = float(terr.gx[1] - terr.gx[0])
        self.res = res
        # fondo del valle: mínimo en ~30 m y suavizado
        k = max(3, int(round(30.0 / res)) | 1)
        self.fondo = gaussian_filter(minimum_filter(terr.Z0, size=k), 4.0 / res)
        # cota de cada masa de agua quieta: la orilla (percentil bajo del terreno en su contorno)
        self.nivel = []
        for g in self.quieta:
            ring = shapely.segmentize(g.exterior, 3.0)
            c = np.asarray(ring.coords)
            zb = terr.z(c[:, 0], c[:, 1])
            zi = terr.z(*np.asarray(g.representative_point().coords[0]))
            self.nivel.append(float(min(np.percentile(zb, 10), float(zi) + 0.5)) - 0.15)
        n = len(self.quieta) + len(self.corriente)
        if n:
            log(f"  agua: {len(self.quieta)} láminas quietas y {len(self.corriente)} cauces con corriente")

    def lamina(self, x, y):
        """Cota de la lámina de un río en (x, y)."""
        gx, gy = self.terr.gx, self.terr.gy
        c = (np.asarray(x) - gx[0]) / (gx[1] - gx[0])
        r = (np.asarray(y) - gy[0]) / (gy[1] - gy[0])
        f = map_coordinates(self.fondo, [np.ravel(r), np.ravel(c)], order=1, mode="nearest").reshape(np.shape(x))
        return np.maximum(f - BAJO_LAMINA, self.terr.z(x, y) - MAX_HUNDIDO)

    def tallar(self):
        """Hunde el terreno bajo el agua y rebaja la orilla. Llamar antes de terrain.build_mesh."""
        if not (self.quieta or self.corriente):
            return
        terr = self.terr
        GX, GY = np.meshgrid(terr.gx, terr.gy)
        W = np.full(terr.Z.shape, np.nan)
        for g, z in zip(self.quieta, self.nivel):
            m = shapely.contains_xy(g, GX, GY)
            W[m] = z
        if self.corriente:
            u = unary_union(self.corriente)
            shapely.prepare(u)
            m = shapely.contains_xy(u, GX, GY) & np.isnan(W)
            W[m] = np.maximum(self.fondo[m] - BAJO_LAMINA, terr.Z0[m] - MAX_HUNDIDO)
        dentro = ~np.isnan(W)
        if not dentro.any():
            return
        dist_in = distance_transform_edt(dentro) * self.res
        prof = np.clip(0.35 + 0.3 * dist_in, 0.35, 2.5)
        Z = terr.Z
        Z = np.where(dentro, np.minimum(Z, W - prof), Z)
        dist_out, idx = distance_transform_edt(~dentro, return_indices=True)
        dist_out = dist_out * self.res
        Wn = W[idx[0], idx[1]]
        orilla = ~dentro & (dist_out <= ORILLA)
        Z = np.where(orilla, np.minimum(Z, Wn + 0.12 + 0.45 * dist_out), Z)
        terr.Z = Z

    def construir(self, scene):
        n = 0
        for g, z in zip(self.quieta, self.nivel):
            verts, faces = triangulate_polygon(g, 8.0)
            if len(faces):
                scene.add("agua", "agua", np.column_stack([verts, np.full(len(verts), z)]), verts / 8, faces)
                n += len(faces)
        for g in self.corriente:
            verts, faces = triangulate_polygon(g, 4.0)
            if len(faces):
                scene.add("agua", "agua", np.column_stack([verts, self.lamina(verts[:, 0], verts[:, 1])]),
                          verts / 8, faces)
                n += len(faces)
        return n
