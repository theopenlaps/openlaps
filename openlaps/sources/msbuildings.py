"""Huellas de edificios de Microsoft (Global ML Building Footprints, ODbL).

Rellena los huecos de OSM: en la mayoría de México OSM tiene pocas casas dibujadas.
"""
import csv
import gzip
import json
import math
import os
import re
import sqlite3

from .cache import fetch

INDEX_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
_FIRST = re.compile(r"\[\[\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)")
_CELDA = 200.0   # 1/200° ≈ 550 m por celda en el índice espacial local


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


def _asegurar_indice(gz_path, log, size=""):
    """Construye una sola vez un índice SQLite espacial sobre la tesela .csv.gz (después cada consulta tarda
    milisegundos en vez de volver a descomprimir 130 MB por pedazo)."""
    idx_path = gz_path + ".v2.idx.sqlite"
    if os.path.exists(idx_path):
        return idx_path
    log(f"  edificios Microsoft: indexando tesela {size} (solo la primera vez)")
    tmp = f"{idx_path}.{os.getpid()}.part"
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE b (cx INTEGER, cy INTEGER, lon REAL, lat REAL, linea TEXT)")
        lote = []
        with gzip.open(gz_path, "rt") as f:
            for line in f:
                m = _FIRST.search(line)
                if not m or '"Polygon"' not in line:
                    continue
                lon, lat = float(m.group(1)), float(m.group(2))
                lote.append((int(math.floor(lon * _CELDA)), int(math.floor(lat * _CELDA)), lon, lat, line))
                if len(lote) >= 40000:
                    con.executemany("INSERT INTO b VALUES (?,?,?,?,?)", lote)
                    lote.clear()
        if lote:
            con.executemany("INSERT INTO b VALUES (?,?,?,?,?)", lote)
        con.execute("CREATE INDEX idx_celda ON b (cx, cy)")
        con.commit()
    except Exception:
        con.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    con.close()
    try:
        os.replace(tmp, idx_path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
    return idx_path


def download(bbox, log=print):
    keys = {_quadkey(la, lo) for la in (bbox.south, bbox.north) for lo in (bbox.west, bbox.east)}
    idx = fetch(INDEX_URL, ext=".csv", log=log)
    urls = []
    with open(idx, newline="") as f:
        for row in csv.DictReader(f):
            if row["QuadKey"] in keys:
                urls.append((row["Url"], row["Size"]))
    polys = []
    cx0, cx1 = int(math.floor(bbox.west * _CELDA)), int(math.floor(bbox.east * _CELDA))
    cy0, cy1 = int(math.floor(bbox.south * _CELDA)), int(math.floor(bbox.north * _CELDA))
    for url, size in urls:
        path = fetch(url, ext=".csv.gz", log=log)
        try:
            sq = _asegurar_indice(path, log, size)
            with sqlite3.connect(sq, timeout=30) as con:
                rows = con.execute(
                    "SELECT linea FROM b WHERE cx BETWEEN ? AND ? AND cy BETWEEN ? AND ? "
                    "AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
                    (cx0, cx1, cy0, cy1, bbox.west, bbox.east, bbox.south, bbox.north),
                ).fetchall()
            for (line,) in rows:
                feat = json.loads(line)
                g = feat["geometry"]
                if g["type"] != "Polygon":
                    continue
                h = feat.get("properties", {}).get("height", -1)
                polys.append({"outer": [tuple(c) for c in g["coordinates"][0]],
                              "height": h if h and h > 0 else None})
            continue
        except Exception:  # noqa: BLE001 - respaldo: lectura lineal del .csv.gz
            pass
        log(f"  edificios Microsoft: tesela {size}")
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
