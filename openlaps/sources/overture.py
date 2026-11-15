"""Alturas de edificios de Overture Maps (tema buildings, ODbL 1.0).

Overture une las huellas de OSM, Microsoft y Esri y les añade altura (`height`) y plantas
(`num_floors`); en los centros urbanos de EE. UU. buena parte de las alturas vienen del LiDAR
del USGS. Aquí solo se usan para dar altura a los edificios de OSM que no la tienen y para
completar con los edificios que OSM no tiene dibujados.

Se lee el GeoParquet público de S3 (sin cuenta) con pyarrow, saltando los grupos de filas que no
tocan la caja: unos pocos MB por km². pyarrow es opcional: sin él se avisa y se sigue sin alturas.
El resultado de cada caja se guarda en la caché.
"""
import json
import os
import re
import urllib.request
from datetime import date

import numpy as np

from .cache import UA, cache_path, fetch

BUCKET = "overturemaps-us-west-2"
LISTADO = f"https://{BUCKET}.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
STAC = "https://stac.overturemaps.org/{rel}/collections.parquet"
ATRIBUCION = "Alturas de edificios: © Overture Maps Foundation, © OpenStreetMap contributors, Microsoft, Esri (ODbL 1.0)"


def _ultima_version(log):
    """Versión más reciente publicada (se consulta una vez al mes)."""
    path = cache_path("overture-release-" + date.today().strftime("%Y-%m"), ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["rel"]
    req = urllib.request.Request(LISTADO, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        xml = r.read().decode("utf-8", "replace")
    rels = sorted(re.findall(r"<Prefix>release/([0-9][^/<]+)/</Prefix>", xml))
    if not rels:
        raise RuntimeError("no se encontró ninguna versión de Overture")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rel": rels[-1]}, f)
    return rels[-1]


def download(bbox, log=print):
    """Edificios de Overture en la caja: lista de dict(outer, inners, height, floors, roof_shape,
    roof_color, facade_color, clase, fuente_altura) con coordenadas (lon, lat)."""
    try:
        import pyarrow.compute as pc
        import pyarrow.fs as pafs
        import pyarrow.parquet as pq
    except ImportError:
        log("  aviso: sin alturas de Overture (instala pyarrow: pip install pyarrow)")
        return []
    import shapely
    rel = _ultima_version(log)
    W, S, E, N = bbox.west, bbox.south, bbox.east, bbox.north
    key = json.dumps(["overture-buildings", rel, [round(v, 6) for v in (S, W, N, E)]])
    out_path = cache_path(key, ".overture.json")
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            res = json.load(f)
        log(f"  Overture {rel}: {len(res)} edificios (desde la caché)")
        return res
    idx = pq.read_table(fetch(STAC.format(rel=rel), ext=".parquet", log=log), columns=["assets", "bbox"]).to_pylist()
    files = []
    for r in idx:
        a = r.get("assets") or {}
        href = (a.get("aws") or {}).get("href", "") if isinstance(a, dict) else ""
        if "type=building/" not in href:
            continue
        b = r.get("bbox")
        xmin, ymin, xmax, ymax = (b["xmin"], b["ymin"], b["xmax"], b["ymax"]) if isinstance(b, dict) else b
        if xmin <= E and xmax >= W and ymin <= N and ymax >= S:
            files.append(BUCKET + "/" + href.split("amazonaws.com/")[1])
    s3 = pafs.S3FileSystem(anonymous=True, region="us-west-2")
    res = []
    want = ["bbox", "height", "num_floors", "roof_shape", "roof_color", "facade_color", "class", "sources",
            "is_underground", "geometry"]
    for path in files:
        f = pq.ParquetFile(path, filesystem=s3)
        md = f.metadata
        cols = {md.schema.column(i).path: i for i in range(md.num_columns)}
        grupos = []
        for g in range(md.num_row_groups):
            try:
                st = {k: md.row_group(g).column(cols[k]).statistics for k in ("bbox.xmin", "bbox.xmax", "bbox.ymin", "bbox.ymax")}
                if st["bbox.xmin"].min <= E and st["bbox.xmax"].max >= W and st["bbox.ymin"].min <= N \
                        and st["bbox.ymax"].max >= S:
                    grupos.append(g)
            except Exception:  # noqa: BLE001 - sin estadísticas: se lee el grupo
                grupos.append(g)
        if not grupos:
            continue
        names = f.schema_arrow.names
        tb = f.read_row_groups(grupos, columns=[c for c in want if c in names])
        bb = tb["bbox"]
        m = pc.and_(pc.and_(pc.less_equal(pc.struct_field(bb, "xmin"), E), pc.greater_equal(pc.struct_field(bb, "xmax"), W)),
                    pc.and_(pc.less_equal(pc.struct_field(bb, "ymin"), N), pc.greater_equal(pc.struct_field(bb, "ymax"), S)))
        tb = tb.filter(m)
        geoms = shapely.from_wkb(np.asarray(tb["geometry"].to_pylist(), dtype=object))
        cols_py = {c: tb[c].to_pylist() for c in tb.schema.names if c not in ("geometry", "bbox")}
        for k, g in enumerate(geoms):
            if g is None or g.is_empty or (cols_py.get("is_underground") or [False] * len(geoms))[k]:
                continue
            fuente = None
            for s in (cols_py.get("sources") or [None] * len(geoms))[k] or []:
                if s.get("property") and "height" in s["property"]:
                    fuente = s.get("dataset")
            for pg in getattr(g, "geoms", [g]):
                if pg.geom_type != "Polygon":
                    continue
                res.append({"outer": [tuple(c) for c in pg.exterior.coords],
                            "inners": [[tuple(c) for c in r.coords] for r in pg.interiors],
                            "height": cols_py.get("height", [None] * len(geoms))[k],
                            "floors": cols_py.get("num_floors", [None] * len(geoms))[k],
                            "roof_shape": cols_py.get("roof_shape", [None] * len(geoms))[k],
                            "roof_color": cols_py.get("roof_color", [None] * len(geoms))[k],
                            "facade_color": cols_py.get("facade_color", [None] * len(geoms))[k],
                            "clase": cols_py.get("class", [None] * len(geoms))[k],
                            "fuente_altura": fuente})
    with open(out_path + ".part", "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(out_path + ".part", out_path)
    n_h = sum(1 for r in res if r["height"])
    log(f"  Overture {rel}: {len(res)} edificios, {n_h} con altura")
    return res
