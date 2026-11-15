"""Huellas de edificios de Microsoft (Global ML Building Footprints, ODbL).

Rellena los huecos de OSM: en la mayoría de México OSM tiene pocas casas dibujadas.
"""
import csv
import gzip
import json
import math
import re

from .cache import fetch

INDEX_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
_FIRST = re.compile(r"\[\[\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)")


def _quadkey(lat, lon, z=9):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    s = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    q = ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        q += str((1 if x & m else 0) + (2 if y & m else 0))
    return q


def download(bbox, log=print):
    keys = {_quadkey(la, lo) for la in (bbox.south, bbox.north) for lo in (bbox.west, bbox.east)}
    idx = fetch(INDEX_URL, ext=".csv", log=log)
    urls = []
    with open(idx, newline="") as f:
        for row in csv.DictReader(f):
            if row["QuadKey"] in keys:
                urls.append((row["Url"], row["Size"]))
    polys = []
    for url, size in urls:
        log(f"  edificios Microsoft: tesela {size}")
        path = fetch(url, ext=".csv.gz", log=log)
        with gzip.open(path, "rt") as f:
            for line in f:
                m = _FIRST.search(line)
                if not m:
                    continue
                lon, lat = float(m.group(1)), float(m.group(2))
                if not (bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north):
                    continue
                feat = json.loads(line)
                g = feat["geometry"]
                if g["type"] != "Polygon":
                    continue
                h = feat.get("properties", {}).get("height", -1)
                polys.append({"outer": [tuple(c) for c in g["coordinates"][0]],
                              "height": h if h and h > 0 else None})
    return polys
