"""Relieve y cobertura de suelo.

- Copernicus DEM GLO-30 (© DLR/Airbus, distribuido por la ESA/Copernicus): modelo de SUPERFICIE de
  30 m, así que incluye copas de árboles y techos. Se corrige después (terrain.dsm_to_dtm).
- INEGI CEM 3.0 / modelos LiDAR (Términos de Libre Uso de la Información del INEGI): modelo del
  TERRENO, mejor para México; se le pasa al programa como archivo local (--dem archivo.tif).
- ESA WorldCover 10 m 2021 (© ESA WorldCover project, CC BY 4.0): tipo de cobertura de suelo.
"""
import math
import os

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from pyproj import Transformer
from scipy.ndimage import map_coordinates

from .cache import UA

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")


class Raster:
    """Raster en memoria con su transformación, muestreable en coordenadas locales."""

    def __init__(self, data, transform, crs, nodata=None):
        self.data = data.astype(np.float32) if data.dtype != np.uint8 else data
        self.transform = transform
        self.crs = crs
        self.nodata = nodata

    def sample(self, proj, x, y, order=1, mode="nearest"):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        lon, lat = proj.to_lonlat(x.ravel(), y.ravel())
        if str(self.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
            tr = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
            lon, lat = tr.transform(lon, lat)
        inv = ~self.transform
        col, row = inv * (lon, lat)
        # centro de pixel -> índice
        coords = np.vstack([np.asarray(row) - 0.5, np.asarray(col) - 0.5])
        if self.data.ndim == 3:
            return np.stack([map_coordinates(b.astype(np.float32), coords, order=order, mode=mode).reshape(x.shape)
                             for b in self.data], -1)
        out = map_coordinates(self.data.astype(np.float32), coords, order=order, mode=mode)
        return out.reshape(x.shape)


def _copernicus_url(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"
    return f"/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"


def _worldcover_url(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return ("/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_Map.tif")


def _read_tiles(urls, bbox, fill, log):
    datasets = []
    for u in urls:
        try:
            datasets.append(rasterio.open(u))
        except Exception as e:  # noqa: BLE001 - p.ej. teselas de mar que no existen
            log(f"  (sin tesela {u.rsplit('/', 1)[-1]}: {e.__class__.__name__})")
    if not datasets:
        return None
    arr, tr = merge(datasets, bounds=(bbox.west, bbox.south, bbox.east, bbox.north), nodata=fill)
    crs = datasets[0].crs
    for d in datasets:
        d.close()
    return Raster(arr[0], tr, crs, nodata=fill)


def copernicus_dem(bbox, log=print):
    urls = []
    for la in range(math.floor(bbox.south), math.floor(bbox.north) + 1):
        for lo in range(math.floor(bbox.west), math.floor(bbox.east) + 1):
            urls.append(_copernicus_url(la, lo))
    r = _read_tiles(urls, bbox, 0.0, log)
    if r is None:
        raise RuntimeError("No hay relieve Copernicus para esta zona")
    return r


def worldcover(bbox, log=print):
    urls = []
    for la in range(math.floor(bbox.south / 3) * 3, math.floor(bbox.north / 3) * 3 + 1, 3):
        for lo in range(math.floor(bbox.west / 3) * 3, math.floor(bbox.east / 3) * 3 + 1, 3):
            urls.append(_worldcover_url(la, lo))
    r = _read_tiles(urls, bbox, 0, log)
    if r is not None:
        r.data = r.data.astype(np.uint8)
    return r


def local_raster(path, bbox):
    """Lee un GeoTIFF local (p.ej. INEGI CEM 3.0 o un MDE LiDAR) recortado al área."""
    with rasterio.open(path) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, bbox.west, bbox.south, bbox.east, bbox.north)
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
        arr = ds.read(1, window=win, masked=True)
        tr = ds.window_transform(win)
        fill = float(np.nanmedian(arr.compressed())) if arr.count() else 0.0
        data = arr.filled(fill).astype(np.float32)
        return Raster(data, tr, ds.crs, nodata=ds.nodata)


# --------------------------------------------------------------------------------------
# Sentinel-2 (Copernicus, libre con atribución) y altura de dosel (Meta/WRI, CC BY 4.0)
# --------------------------------------------------------------------------------------
STAC = "https://earth-search.aws.element84.com/v1/search"
S2_YEARS = set()   # años de las escenas usadas en esta ejecución


def sentinel2(bbox, season="seca", log=print):
    """Imagen en color real de 10 m sin nubes (TCI de Sentinel-2 L2A en COG público).

    Busca escenas de la temporada pedida (seca: nov-abr; lluvias: jul-oct) de los dos últimos
    años, ordenadas por nubosidad, y mezcla hasta tres para tapar huecos de borde de tesela.
    """
    import json
    import urllib.request
    from datetime import date
    y = date.today().year
    ranges = ([(f"{y - 1}-11-01", f"{y}-04-30"), (f"{y - 2}-11-01", f"{y - 1}-04-30")] if season == "seca"
              else [(f"{y - 1}-07-01", f"{y - 1}-10-31"), (f"{y - 2}-07-01", f"{y - 2}-10-31")])
    feats = []
    for a, b in ranges:
        body = {"collections": ["sentinel-2-l2a"], "bbox": [bbox.west, bbox.south, bbox.east, bbox.north],
                "datetime": f"{a}T00:00:00Z/{b}T23:59:59Z", "query": {"eo:cloud_cover": {"lt": 10}},
                "limit": 8, "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]}
        req = urllib.request.Request(STAC, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                feats += json.load(r).get("features", [])
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: búsqueda Sentinel-2 falló ({e})")
        if len(feats) >= 3:
            break
    if not feats:
        return None
    acc = None
    used = []
    for f in feats[:6]:
        href = f["assets"].get("visual", {}).get("href")
        if not href:
            continue
        try:
            with rasterio.open("/vsicurl/" + href) as ds:
                b = transform_bounds("EPSG:4326", ds.crs, bbox.west, bbox.south, bbox.east, bbox.north)
                win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
                arr = ds.read(window=win, boundless=True, fill_value=0)
                tr = ds.window_transform(win)
                crs = ds.crs
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: no se pudo leer {f['id']}: {e.__class__.__name__}")
            continue
        if acc is None:
            acc, acc_tr, acc_crs = arr.astype(np.float32), tr, crs
            used.append(f["id"])
        elif crs == acc_crs and arr.shape == acc.shape:
            hole = (acc.sum(0) == 0) & (arr.sum(0) > 0)
            if hole.any():
                acc[:, hole] = arr[:, hole]
                used.append(f["id"])
        if acc is not None and not (acc.sum(0) == 0).any():
            break
    if acc is None:
        return None
    log(f"  Sentinel-2: {', '.join(used)}")
    # año de cada escena (S2A_30TVK_20260427_...), para la atribución que pide Copernicus
    S2_YEARS.update(s.split("_")[2][:4] for s in used if s.count("_") >= 2)
    return Raster(acc, acc_tr, acc_crs)


def _qk(lat, lon, z=9):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    s = math.sin(math.radians(lat))
    yy = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    q = ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1)
        q += str((1 if x & m else 0) + (2 if yy & m else 0))
    return q


def canopy_height(bbox, log=print):
    """Altura de dosel de 1 m (Tolan et al. 2024, Meta / WRI), en metros."""
    keys = sorted({_qk(la, lo) for la in (bbox.south, bbox.north) for lo in (bbox.west, bbox.east)})
    urls = ["/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/"
            f"{k}.tif" for k in keys]
    dss = []
    for u in urls:
        try:
            dss.append(rasterio.open(u))
        except Exception as e:  # noqa: BLE001
            log(f"  (sin dosel para {u.rsplit('/', 1)[-1]}: {e.__class__.__name__})")
    if not dss:
        return None
    b = transform_bounds("EPSG:4326", dss[0].crs, bbox.west, bbox.south, bbox.east, bbox.north)
    arr, tr = merge(dss, bounds=b, nodata=0)
    crs = dss[0].crs
    for d in dss:
        d.close()
    log(f"  altura de dosel: {arr.shape[2]}x{arr.shape[1]} px, máx {arr.max():.0f} m")
    return Raster(arr[0].astype(np.float32), tr, crs)


# --------------------------------------------------------------------------------------
# Datos nacionales de alta resolución (ver regions.py): ortofoto por WMS y relieve por WCS
# --------------------------------------------------------------------------------------
ORTHO_WMS = {
    # IGN España, PNOA máxima actualidad (25 cm), CC BY 4.0 scne.es
    "pnoa": ("https://www.ign.es/wms-inspire/pnoa-ma", "OI.OrthoimageCoverage", 4096),
    # IGN Francia, BD ORTHO (20 cm), Licence Ouverte Etalab 2.0
    "geopf": ("https://data.geopf.fr/wms-r", "HR.ORTHOIMAGERY.ORTHOPHOTOS", 4096),
}


def ortho(bbox, source, mpp=0.25, log=print):
    """Ortofoto RGB del área pedida a `mpp` metros por pixel (aprox.), por teselas WMS en EPSG:3857.

    Devuelve un Raster (3 bandas uint8) o None si el servicio no responde.
    """
    from PIL import Image
    import rasterio.transform
    from .cache import fetch
    url, layer, maxpx = ORTHO_WMS[source]
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = tr.transform(bbox.west, bbox.south)
    x1, y1 = tr.transform(bbox.east, bbox.north)
    k = 1.0 / math.cos(math.radians((bbox.south + bbox.north) / 2))   # escala de Mercator
    res = mpp * k
    W = int(math.ceil((x1 - x0) / res))
    H = int(math.ceil((y1 - y0) / res))
    step = min(2048, maxpx)
    out = np.zeros((3, H, W), np.uint8)
    n_ok = 0
    for r0 in range(0, H, step):
        for c0 in range(0, W, step):
            w, h = min(step, W - c0), min(step, H - r0)
            bx0, bx1 = x0 + c0 * res, x0 + (c0 + w) * res
            by1, by0 = y1 - r0 * res, y1 - (r0 + h) * res
            q = (f"{url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&STYLES=&CRS=EPSG:3857"
                 f"&BBOX={bx0:.2f},{by0:.2f},{bx1:.2f},{by1:.2f}&WIDTH={w}&HEIGHT={h}&FORMAT=image/jpeg")
            try:
                path = fetch(q, ext=".jpg", log=log)
                img = Image.open(path).convert("RGB")
                if img.size != (w, h):
                    raise ValueError(f"tamaño {img.size}")
                out[:, r0:r0 + h, c0:c0 + w] = np.asarray(img).transpose(2, 0, 1)
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: tesela de ortofoto fallida ({e.__class__.__name__}: {e})")
    if n_ok == 0:
        return None
    log(f"  ortofoto {source}: {W}x{H} px (~{mpp:.2f} m/px)")
    return Raster(out, rasterio.transform.from_origin(x0, y1, res, res), "EPSG:3857")


def mdt05(bbox, log=print):
    """Relieve del TERRENO de España a 5 m (IGN MDT05, CC BY 4.0 scne.es), por WCS 2.0."""
    from .cache import fetch
    q = ("https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
         f"&CoverageId=Elevacion4258_5&SUBSET=Lat({bbox.south:.6f},{bbox.north:.6f})"
         f"&SUBSET=Long({bbox.west:.6f},{bbox.east:.6f})&FORMAT=image/tiff")
    path = fetch(q, ext=".tif", log=log)
    with rasterio.open(path) as ds:
        arr = ds.read(1, masked=True)
        if arr.count() == 0:
            raise RuntimeError("MDT05 vacío en esta zona")
        fill = float(np.nanmedian(arr.compressed()))
        data = arr.filled(fill).astype(np.float32)
        r = Raster(data, ds.transform, ds.crs, nodata=ds.nodata)
    log(f"  MDT05 (IGN): {data.shape[1]}x{data.shape[0]} px, {data.min():.0f}-{data.max():.0f} m")
    return r
