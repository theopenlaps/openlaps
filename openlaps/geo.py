"""Proyección a un sistema local en metros (x = este, y = norte, z = altura)."""
import math
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer


@dataclass
class BBox:
    south: float
    west: float
    north: float
    east: float

    @classmethod
    def around(cls, lat, lon, width_m, height_m=None):
        height_m = height_m or width_m
        dlat = height_m / 2 / 111_320.0
        dlon = width_m / 2 / (111_320.0 * math.cos(math.radians(lat)))
        return cls(lat - dlat, lon - dlon, lat + dlat, lon + dlon)

    @classmethod
    def parse(cls, s):
        a = [float(v) for v in s.split(",")]
        return cls(*a)

    def padded(self, meters):
        lat = (self.south + self.north) / 2
        dlat = meters / 111_320.0
        dlon = meters / (111_320.0 * math.cos(math.radians(lat)))
        return BBox(self.south - dlat, self.west - dlon, self.north + dlat, self.east + dlon)

    @property
    def center(self):
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    def overpass(self):
        return f"{self.south:.6f},{self.west:.6f},{self.north:.6f},{self.east:.6f}"


class LocalProjection:
    """UTM de la zona que toca, recentrada en el centro del área."""

    def __init__(self, bbox: BBox):
        lat0, lon0 = bbox.center
        zone = int((lon0 + 180) // 6) + 1
        epsg = (32600 if lat0 >= 0 else 32700) + zone
        self.epsg = epsg
        self._fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        self._inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        ox, oy = self._fwd.transform(lon0, lat0)
        self.ox, self.oy = ox, oy
        self.lat0, self.lon0 = lat0, lon0
        x0, y0 = self.to_local(np.array([bbox.west, bbox.east]), np.array([bbox.south, bbox.north]))
        # límites del área en coordenadas locales
        self.xmin, self.xmax = float(min(x0)), float(max(x0))
        self.ymin, self.ymax = float(min(y0)), float(max(y0))

    @classmethod
    def marco(cls, lat0, lon0, rect):
        """Proyección con el origen fijo en (lat0, lon0) y límites `rect` = (xmin, ymin, xmax, ymax) en metros.
        La usa el modo en vivo: todos los pedazos comparten el mismo origen, así encajan sin moverlos."""
        p = cls(BBox.around(lat0, lon0, 10.0))
        p.xmin, p.ymin, p.xmax, p.ymax = (float(v) for v in rect)
        return p

    def bbox(self):
        """Caja geográfica (lat/lon) que contiene el rectángulo local."""
        lon, lat = self.to_lonlat(np.array([self.xmin, self.xmax, self.xmin, self.xmax]),
                                  np.array([self.ymin, self.ymin, self.ymax, self.ymax]))
        return BBox(float(lat.min()), float(lon.min()), float(lat.max()), float(lon.max()))

    def to_local(self, lon, lat):
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(x) - self.ox, np.asarray(y) - self.oy

    def to_lonlat(self, x, y):
        lon, lat = self._inv.transform(np.asarray(x, float) + self.ox, np.asarray(y, float) + self.oy)
        return np.asarray(lon), np.asarray(lat)
