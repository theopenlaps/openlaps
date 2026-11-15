"""Edificios del Catastro español (servicio INSPIRE de la Dirección General del Catastro).

Cada edificio viene troceado en partes (`BU:BuildingPart`) con su huella exacta y su número
real de plantas sobre rasante: el cuerpo principal de 6 plantas, el patio cubierto de 1, el
ático retranqueado de 7... En los cascos urbanos OSM casi nunca trae `building:levels` (en el
centro de Madrid, menos del 25 % de los edificios), así que esta es la fuente de alturas.

Cubre toda España salvo el País Vasco y Navarra (tienen catastros forales propios).
Licencia: la de uso de la información catastral de la DG del Catastro (reutilización libre
citando la fuente); los ficheros INSPIRE se publican como CC BY 4.0.
"""
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pyproj import Transformer

from .cache import fetch

WFS = "https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx"
TILE = 500.0          # m por petición (el servicio corta las respuestas muy grandes)

_PART = re.compile(r"<bu-ext2d:BuildingPart gml:id=\"([^\"]+)\">(.*?)</bu-ext2d:BuildingPart>", re.S)
_FLOORS = re.compile(r"<bu-ext2d:numberOfFloorsAboveGround>(\d+)<")
_PATCH = re.compile(r"<gml:PolygonPatch>(.*?)</gml:PolygonPatch>", re.S)
_EXT = re.compile(r"<gml:exterior>.*?<gml:posList[^>]*>([^<]+)</gml:posList>", re.S)
_INT = re.compile(r"<gml:interior>.*?<gml:posList[^>]*>([^<]+)</gml:posList>", re.S)


def _epsg(lon):
    # Península y Baleares en ETRS89 / UTM 30; Canarias en REGCAN95 / UTM 28
    return 25828 if lon < -12.0 else 25830


def _ring(txt, inv):
    v = np.array(txt.split(), float).reshape(-1, 2)
    lon, lat = inv.transform(v[:, 0], v[:, 1])
    return list(zip(lon.tolist(), lat.tolist()))


def parse(path, inv):
    """Lee un GML de partes de edificio. Devuelve lista de dict(outer, inners, floors, ref)."""
    with open(path, encoding="latin-1") as f:
        s = f.read()
    out = []
    for gid, body in _PART.findall(s):
        m = _FLOORS.search(body)
        floors = int(m.group(1)) if m else 0
        ref = gid.split(".")[-1].split("_part")[0]
        for patch in _PATCH.findall(body):
            e = _EXT.search(patch)
            if not e:
                continue
            out.append({"outer": _ring(e.group(1), inv), "inners": [_ring(t, inv) for t in _INT.findall(patch)],
                        "floors": floors, "ref": ref, "id": gid.split(".")[-1]})
    return out


def download(bbox, log=print):
    """Partes de edificio del Catastro dentro de la caja (lat/lon). Lista vacía si no hay servicio."""
    epsg = _epsg((bbox.west + bbox.east) / 2)
    fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    xs, ys = fwd.transform([bbox.west, bbox.east, bbox.west, bbox.east], [bbox.south, bbox.south, bbox.north, bbox.north])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    nx, ny = max(1, math.ceil((x1 - x0) / TILE)), max(1, math.ceil((y1 - y0) / TILE))
    urls = []
    for i in range(nx):
        for j in range(ny):
            bx0, by0 = x0 + i * (x1 - x0) / nx, y0 + j * (y1 - y0) / ny
            bx1, by1 = x0 + (i + 1) * (x1 - x0) / nx, y0 + (j + 1) * (y1 - y0) / ny
            urls.append(f"{WFS}?service=wfs&version=2&request=getfeature&typeNames=BU:BuildingPart"
                        f"&srsName=EPSG::{epsg}&bbox={bx0:.1f},{by0:.1f},{bx1:.1f},{by1:.1f}")

    def bajar(url):
        try:
            path = fetch(url, ext=".catastro.gml", timeout=180, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: Catastro no responde ({e})")
            return None
        with open(path, "rb") as f:
            head = f.read(600)
        if b"FeatureCollection" not in head:
            # página de error o excepción del servicio: no se guarda en la caché
            os.remove(path)
            return None
        return path

    # el servicio tarda ~25 s por tesela de 500 m: de dos en dos (con más corta conexiones)
    with ThreadPoolExecutor(max_workers=2) as ex:
        paths = list(ex.map(bajar, urls))
    parts, seen = [], set()
    fallos = sum(1 for p in paths if p is None)
    for path in paths:
        if path is None:
            continue
        for p in parse(path, inv):
            if p["id"] in seen:       # las partes que cruzan el borde de dos teselas
                continue
            seen.add(p["id"])
            parts.append(p)
    if parts:
        n = np.array([p["floors"] for p in parts])
        log(f"  Catastro: {len(parts)} partes de edificio ({nx * ny} teselas), plantas mediana "
            f"{int(np.median(n[n > 0])) if (n > 0).any() else 0}, máx {int(n.max())}")
    elif fallos:
        log("  aviso: sin edificios del Catastro")
    return parts
