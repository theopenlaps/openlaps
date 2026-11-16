"""Relieve y cobertura de suelo.

- Copernicus DEM GLO-30 (© DLR/Airbus, distribuido por la ESA/Copernicus): modelo de SUPERFICIE de
  30 m, así que incluye copas de árboles y techos. Se corrige después (terrain.dsm_to_dtm).
- INEGI CEM 3.0 / modelos LiDAR (Términos de Libre Uso de la Información del INEGI): modelo del
  TERRENO, mejor para México; se le pasa al programa como archivo local (--dem archivo.tif).
- ESA WorldCover 10 m 2021 (© ESA WorldCover project, CC BY 4.0): tipo de cobertura de suelo.
"""
import functools
import json
import math
import os
from datetime import date

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from pyproj import Transformer
from scipy.ndimage import map_coordinates

from .cache import UA, cache_path

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")


_TRANSFORMERS = {}


def _transformer(epsg_src, crs_dst):
    k = (epsg_src, crs_dst)
    if k not in _TRANSFORMERS:
        _TRANSFORMERS[k] = Transformer.from_crs(f"EPSG:{epsg_src}", crs_dst, always_xy=True)
    return _TRANSFORMERS[k]


def _transformar(tr, x, y, n=33):
    """Transforma muchos puntos a otro sistema. Con más de 50 000 puntos se transforma solo una
    rejilla de n x n sobre su caja y el resto se interpola (bilineal): en las distancias de una
    pista el cambio de proyección es casi afín y el error queda en milímetros, frente a píxeles
    de 25 cm o más. Antes se transformaba cada píxel de cada tesela, uno por uno."""
    if x.size < 50000:
        return tr.transform(x, y)
    x0, x1, y0, y1 = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    gx, gy = np.linspace(x0, x1, n), np.linspace(y0, y1, n)
    GX, GY = np.meshgrid(gx, gy)
    TX, TY = tr.transform(GX, GY)
    TX, TY = np.asarray(TX, float), np.asarray(TY, float)
    c = (x - x0) / max(x1 - x0, 1e-9) * (n - 1)
    r = (y - y0) / max(y1 - y0, 1e-9) * (n - 1)
    return (map_coordinates(TX, [r, c], order=1, mode="nearest"),
            map_coordinates(TY, [r, c], order=1, mode="nearest"))


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
        # de la UTM local directamente al sistema del ráster (sin pasar por lat/lon), con el
        # transformador reutilizado: crearlo en cada llamada costaba ~5 s por pista
        lon, lat = _transformar(_transformer(proj.epsg, str(self.crs)), x.ravel() + proj.ox, y.ravel() + proj.oy)
        inv = ~self.transform
        col, row = inv * (lon, lat)
        # centro de pixel -> índice
        coords = np.vstack([np.asarray(row) - 0.5, np.asarray(col) - 0.5])
        # se interpola directamente sobre los datos (uint8 o float32) con salida en float32: antes
        # se copiaba el ráster entero a float32 en cada llamada (la ortofoto, 54 MB por banda)
        if self.data.ndim == 3:
            return np.stack([map_coordinates(b, coords, order=order, mode=mode, output=np.float32).reshape(x.shape)
                             for b in self.data], -1)
        out = map_coordinates(self.data, coords, order=order, mode=mode, output=np.float32)
        return out.reshape(x.shape)


def _cacheado(nombre, mensual=False):
    """Guarda en disco (~/.cache/openlaps) el recorte ráster que devuelve la función.

    Los COG remotos (Copernicus, WorldCover, dosel, Sentinel-2) se leían por HTTP en cada
    ejecución: ~45 s de cada generación. La clave es la caja redondeada y los argumentos; con
    `mensual`, también el mes en curso (Sentinel-2 busca las escenas más recientes)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(bbox, *args, log=print, **kw):
            key = json.dumps([nombre, [round(v, 6) for v in (bbox.south, bbox.west, bbox.north, bbox.east)],
                              list(args), kw, date.today().strftime("%Y-%m") if mensual else None])
            path = cache_path(key, ".raster.npz")
            if os.path.exists(path):
                try:
                    with np.load(path, allow_pickle=False) as z:
                        meta = json.loads(str(z["meta"]))
                        r = Raster(z["data"], rasterio.Affine(*meta["transform"]), meta["crs"], meta["nodata"])
                    S2_YEARS.update(meta.get("s2_years", []))
                    log(f"  {nombre}: desde la caché")
                    return r
                except Exception:  # noqa: BLE001 - caché corrupta: se vuelve a descargar
                    os.remove(path)
            antes = set(S2_YEARS)
            r = fn(bbox, *args, log=log, **kw)
            if r is not None:
                meta = {"transform": list(r.transform)[:6], "crs": str(r.crs), "nodata": r.nodata,
                        "s2_years": sorted(S2_YEARS - antes)}
                np.savez(path + ".part.npz", data=r.data, meta=json.dumps(meta))
                os.replace(path + ".part.npz", path)
            return r
        return wrap
    return deco


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


@_cacheado("relieve Copernicus")
def copernicus_dem(bbox, log=print):
    urls = []
    for la in range(math.floor(bbox.south), math.floor(bbox.north) + 1):
        for lo in range(math.floor(bbox.west), math.floor(bbox.east) + 1):
            urls.append(_copernicus_url(la, lo))
    r = _read_tiles(urls, bbox, 0.0, log)
    if r is None:
        raise RuntimeError("No hay relieve Copernicus para esta zona")
    return r


@_cacheado("WorldCover")
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


def _s2_items(bbox, season, log):
    """Escenas Sentinel-2 L2A de la temporada pedida, de menos a más nubes."""
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
    return feats


def _read_window(href, bbox):
    with rasterio.open("/vsicurl/" + href) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, bbox.west, bbox.south, bbox.east, bbox.north)
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        return ds.read(window=win, boundless=True, fill_value=0), ds.window_transform(win), ds.crs


@_cacheado("Sentinel-2", mensual=True)
def sentinel2(bbox, season="seca", log=print):
    """Imagen en color real de 10 m sin nubes (TCI de Sentinel-2 L2A en COG público).

    Busca escenas de la temporada pedida (seca: nov-abr; lluvias: jul-oct) de los dos últimos
    años, ordenadas por nubosidad, y mezcla hasta tres para tapar huecos de borde de tesela.
    """
    feats = _s2_items(bbox, season, log)
    if not feats:
        return None
    acc = None
    used = []
    for f in feats[:6]:
        href = f["assets"].get("visual", {}).get("href")
        if not href:
            continue
        try:
            arr, tr, crs = _read_window(href, bbox)
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


@_cacheado("NDWI Sentinel-2", mensual=True)
def sentinel2_ndwi(bbox, season="seca", log=print):
    """Índice de agua NDWI = (verde - infrarrojo) / (verde + infrarrojo) a 10 m (bandas B03 y B08).

    El agua libre da valores > 0.2; la vegetación y el suelo, negativos. Sirve para ver dónde hay
    agua de verdad aunque OSM no la tenga dibujada (o al revés: embalses bajos, ramblas secas)."""
    feats = _s2_items(bbox, season, log)
    acc, used = None, []
    for f in feats[:6]:
        g, n = f["assets"].get("green", {}).get("href"), f["assets"].get("nir", {}).get("href")
        if not g or not n:
            continue
        try:
            ga, tr, crs = _read_window(g, bbox)
            na, _, _ = _read_window(n, bbox)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: NDWI no se pudo leer {f['id']}: {e.__class__.__name__}")
            continue
        ga, na = ga[0].astype(np.float32), na[0].astype(np.float32)
        ok = (ga > 0) & (na > 0)
        nd = np.where(ok, (ga - na) / np.maximum(ga + na, 1), np.nan)
        if acc is None:
            acc, acc_tr, acc_crs = nd, tr, crs
            used.append(f["id"])
        elif crs == acc_crs and nd.shape == acc.shape:
            hole = np.isnan(acc) & ~np.isnan(nd)
            if hole.any():
                acc[hole] = nd[hole]
                used.append(f["id"])
        if acc is not None and not np.isnan(acc).any():
            break
    if acc is None:
        return None
    S2_YEARS.update(s.split("_")[2][:4] for s in used if s.count("_") >= 2)
    return Raster(np.nan_to_num(acc, nan=-1.0), acc_tr, acc_crs)


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


@_cacheado("altura de dosel")
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


# servicios ArcGIS ImageServer (exportImage): NAIP de EE. UU. (USDA, 60 cm, dominio público)
ORTHO_ARCGIS = {
    "naip": ("https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage", 4000),
}


def ortho(bbox, source, mpp=0.25, log=print):
    """Ortofoto RGB del área pedida a `mpp` metros por pixel (aprox.), por teselas WMS en EPSG:3857.

    Devuelve un Raster (3 bandas uint8) o None si el servicio no responde.
    """
    from PIL import Image
    import rasterio.transform
    from .cache import fetch
    arcgis = source in ORTHO_ARCGIS
    url, layer, maxpx = ORTHO_WMS[source] if not arcgis else (ORTHO_ARCGIS[source][0], None, ORTHO_ARCGIS[source][1])
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
            if arcgis:
                q = (f"{url}?bbox={bx0:.2f},{by0:.2f},{bx1:.2f},{by1:.2f}&bboxSR=3857&imageSR=3857&size={w},{h}"
                     f"&format=jpg&bandIds=0,1,2&compressionQuality=90&f=image")
            else:
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


def dem_3dep(bbox, mpp=1.0, log=print):
    """Relieve del TERRENO de EE. UU.: USGS 3DEP (1 m donde hay LiDAR, si no 10 m), dominio público.

    Una petición exportImage al ImageServer dinámico, que mezcla la mejor fuente disponible.
    El servicio a veces responde 502: `fetch` reintenta, y una respuesta que no es un GeoTIFF
    válido no se guarda en la caché."""
    from .cache import fetch
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = tr.transform(bbox.west, bbox.south)
    x1, y1 = tr.transform(bbox.east, bbox.north)
    k = 1.0 / math.cos(math.radians((bbox.south + bbox.north) / 2))
    W = int(min(4000, math.ceil((x1 - x0) / (mpp * k))))
    H = int(min(4000, math.ceil((y1 - y0) / (mpp * k))))
    q = ("https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
         f"?bbox={x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}&bboxSR=3857&imageSR=3857&size={W},{H}&format=tiff"
         "&pixelType=F32&interpolation=RSP_BilinearInterpolation&noData=-9999&f=image")
    path = fetch(q, ext=".tif", log=log)
    try:
        with rasterio.open(path) as ds:
            arr = ds.read(1, masked=True)
            tr_ds, crs = ds.transform, ds.crs
    except Exception:
        os.remove(path)
        raise
    arr = np.ma.masked_less(arr, -1000)
    if arr.count() == 0:
        raise RuntimeError("3DEP vacío en esta zona")
    data = arr.filled(float(np.ma.median(arr))).astype(np.float32)
    log(f"  3DEP (USGS): {W}x{H} px (~{mpp:.0f} m), {data.min():.0f}-{data.max():.0f} m")
    return Raster(data, tr_ds, crs)


def _mdt05_utm(bbox, log=print):
    """MDT05 en su cuadrícula nativa (UTM 29/30/31 ETRS89, píxeles de 5 m con centro en múltiplos de 5), pidiendo
    los bordes alineados a ella: el servicio devuelve los píxeles tal cual. En lat/lon remuestrea cada petición a su
    propia rejilla, y dos pedazos vecinos del modo en vivo (que piden cajas distintas) veían alturas que diferían
    hasta 30 cm: la calzada saltaba en la costura."""
    from pyproj import Transformer

    from .cache import fetch
    lat0, lon0 = bbox.center
    zona = int((lon0 + 180) // 6) + 1
    if zona not in (29, 30, 31) or lat0 < 35.0:          # Canarias va en otra cuadrícula
        raise RuntimeError("fuera de la península y Baleares")
    epsg = 25800 + zona
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    xs, ys = tr.transform([bbox.west, bbox.east, bbox.west, bbox.east], [bbox.south, bbox.south, bbox.north, bbox.north])
    e0 = math.floor((min(xs) + 2.5) / 5.0) * 5.0 - 2.5
    e1 = math.ceil((max(xs) - 2.5) / 5.0) * 5.0 + 2.5
    n0 = math.floor((min(ys) + 2.5) / 5.0) * 5.0 - 2.5
    n1 = math.ceil((max(ys) - 2.5) / 5.0) * 5.0 + 2.5
    q = ("https://servicios.idee.es/wcs-inspire/mdt?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
         f"&CoverageId=Elevacion{epsg}_5&SUBSET=x({e0:.1f},{e1:.1f})&SUBSET=y({n0:.1f},{n1:.1f})&FORMAT=image/tiff")
    path = fetch(q, ext=".tif", log=log)
    with rasterio.open(path) as ds:
        arr = ds.read(1, masked=True)
        if arr.count() == 0 or abs(ds.transform.a - 5.0) > 1e-6:
            raise RuntimeError("respuesta inesperada")
        arr = np.ma.masked_less(arr, -1000)
        fill = float(np.ma.median(arr))
        data = arr.filled(fill).astype(np.float32)
        r = Raster(data, ds.transform, ds.crs, nodata=ds.nodata)
    log(f"  MDT05 (IGN, UTM {zona}): {data.shape[1]}x{data.shape[0]} px, {data.min():.0f}-{data.max():.0f} m")
    return r


def mdt05(bbox, log=print):
    """Relieve del TERRENO de España a 5 m (IGN MDT05, CC BY 4.0 scne.es), por WCS 2.0."""
    from .cache import fetch
    try:
        return _mdt05_utm(bbox, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"  MDT05 en UTM: {e}; se pide en lat/lon")
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
