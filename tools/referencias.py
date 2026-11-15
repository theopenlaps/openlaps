"""Descarga fotos reales a pie de calle de una zona, con su posición y rumbo, para comparar.

Fuentes abiertas (todas con licencia libre; la de cada foto queda en el manifiesto):
  - Panoramax (api.panoramax.xyz, CC BY-SA 4.0): fotos planas y panorámicas 360°, sin token.
  - Mapillary (CC BY-SA 4.0): la más densa en ciudades; posiciones y rumbos corregidos por SfM.
    Necesita un token gratuito de cliente en la variable de entorno MAPILLARY_TOKEN.
  - Wikimedia Commons: fotos con coordenadas de cámara y rumbo en el EXIF (solo las que lo traen).
Google Street View no se usa: sus términos lo prohíben.

Uso:
  python tools/referencias.py --centro 40.41685,-3.70357 --tam 800 --nombre madrid_sol
  python tools/referencias.py --bbox 40.415,-3.716,40.423,-3.706 --nombre madrid_opera --max 80
Sale en referencias/<nombre>/: las imágenes, referencias.json (posición, rumbo, campo de visión,
tipo plana/360, autor y licencia) y hoja.jpg (miniaturas numeradas para revisarlas).
Se prefieren calles vacías: primero panorámicas y fotos a primera hora o en domingo; las nocturnas
se marcan solas con "usar": false. Las que tengan gente tapando la calle se marcan a mano
("usar": false, "motivo": "gente"); esas marcas se conservan al volver a descargar. En cada entrada se puede editar "ajuste" a mano
({"rumbo": 0, "cabeceo": 0, "dx": 0, "dy": 0, "alto": 0}) si la foto tiene mal el GPS o la brújula.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from openlaps.geo import BBox  # noqa: E402

UA = "openlaps-referencias/0.2 (+https://github.com/theopenlaps/openlaps)"


def _get(url, timeout=60, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else json.loads(data.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            if intento == 2:
                raise
            print(f"  reintento ({e})", flush=True)
            time.sleep(2 + 3 * intento)


def _preferencia(it):
    """Menor = mejor: panorámicas 360 y fotos tomadas temprano o en domingo (calles más vacías)."""
    f = str(it.get("fecha") or "")
    pts = 0.0 if it["tipo"] == "360" else 1.0
    try:
        hora = int(f[11:13])
        pts += 0.0 if 6 <= hora <= 9 else (0.3 if hora <= 11 else 0.6)
        import datetime
        if datetime.date(int(f[:4]), int(f[5:7]), int(f[8:10])).weekday() == 6:
            pts -= 0.2
    except (ValueError, IndexError):
        pts += 0.5
    pts += 0.1 if it["fuente"] == "commons" else 0.0   # suelen ser fotos turísticas o de eventos
    return pts


def _calidad(path):
    """Luz media y fracción de bordes (algo) de la foto, para descartar las nocturnas."""
    from PIL import Image
    import numpy as np
    im = Image.open(path).convert("L")
    im.thumbnail((256, 256))
    a = np.asarray(im, np.float32) / 255
    return {"luz": round(float(a.mean()), 3), "aspecto": round(im.width / im.height, 2)}


def _hoja(folder, fotos):
    from PIL import Image, ImageDraw
    W, H, cols = 300, 225, 6
    rows = (len(fotos) + cols - 1) // cols
    S = Image.new("RGB", (cols * W, max(1, rows) * H), (20, 20, 20))
    d = ImageDraw.Draw(S)
    for i, r in enumerate(fotos):
        im = Image.open(os.path.join(folder, r["archivo"])).convert("RGB")
        im.thumbnail((W - 4, H - 4))
        x, y = (i % cols) * W, (i // cols) * H
        S.paste(im, (x + 2, y + 2))
        tag = f"{i} {r['id']}" + ("" if r.get("usar", True) else f" X {r.get('motivo', '')}")
        d.rectangle([x + 2, y + 2, x + 8 + 6 * len(tag), y + 16], fill=(0, 0, 0))
        d.text((x + 5, y + 3), tag, fill=(255, 220, 0) if r.get("usar", True) else (255, 80, 80))
    S.save(os.path.join(folder, "hoja.jpg"), quality=85)


def _thin(items, min_sep):
    """Quita fotos casi iguales: misma posición (< min_sep m) y rumbo parecido (< 25°)."""
    out = []
    for it in items:
        ok = True
        for o in out:
            d = math.hypot((it["lon"] - o["lon"]) * 111320 * math.cos(math.radians(it["lat"])),
                           (it["lat"] - o["lat"]) * 111320)
            dr = abs((it["rumbo"] - o["rumbo"] + 180) % 360 - 180) if it["tipo"] == "plana" else 0
            if d < min_sep and dr < 25:
                ok = False
                break
        if ok:
            out.append(it)
    return out


# ----------------------------------------------------------------------------- Panoramax
def panoramax(bbox, limit):
    url = ("https://api.panoramax.xyz/api/search?" +
           urllib.parse.urlencode({"bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}", "limit": limit}))
    feats = _get(url, timeout=120).get("features", [])
    out = []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        io = p.get("pers:interior_orientation") or {}
        fov = io.get("field_of_view")
        tipo = "360" if fov == 360 else "plana"
        if tipo == "plana" and not fov:
            fov = _fov_desde_exif(p.get("exif") or {}, io)
        az = p.get("view:azimuth")
        if az is None:
            continue
        via = next((lk for lk in f.get("links", []) if lk.get("rel") == "via"), {})
        out.append({
            "id": "pnx_" + f["id"][:8], "fuente": "panoramax", "lat": lat, "lon": lon, "rumbo": float(az),
            "fov": fov, "tipo": tipo, "fecha": p.get("datetimetz") or p.get("datetime"),
            "autor": p.get("geovisio:producer", "?"), "licencia": p.get("license", "CC-BY-SA-4.0"),
            "pagina": f"https://api.panoramax.xyz/#focus=pic&pic={f['id']}",
            "precision_m": p.get("quality:horizontal_accuracy"),
            "instancia": via.get("instance_name"),
            "url": (f["assets"].get("sd") or f["assets"].get("hd"))["href"],
        })
    return out


def _fov_desde_exif(exif, io):
    """Campo de visión horizontal a partir de la focal equivalente de 35 mm (si viene)."""
    f35 = exif.get("Exif.Photo.FocalLengthIn35mmFilm")
    try:
        f35 = float(f35)
    except (TypeError, ValueError):
        f35 = 0
    if f35 > 0:
        w, h = (io.get("sensor_array_dimensions") or [4, 3])[:2]
        lado = 36.0 if w >= h else 24.0
        return round(math.degrees(2 * math.atan(lado / (2 * f35))), 1)
    return None


# ----------------------------------------------------------------------------- Mapillary
def mapillary(bbox, limit, token):
    fields = "id,computed_geometry,geometry,computed_compass_angle,compass_angle,camera_type,camera_parameters," \
             "thumb_2048_url,captured_at,creator,width,height"
    out, url = [], ("https://graph.mapillary.com/images?" + urllib.parse.urlencode({
        "access_token": token, "fields": fields, "limit": min(limit, 2000),
        "bbox": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}"}))
    data = _get(url, timeout=120).get("data", [])
    for d in data:
        g = d.get("computed_geometry") or d.get("geometry")
        az = d.get("computed_compass_angle", d.get("compass_angle"))
        if not g or az is None or not d.get("thumb_2048_url"):
            continue
        lon, lat = g["coordinates"][:2]
        tipo = "360" if d.get("camera_type") in ("spherical", "equirectangular") else "plana"
        fov = 360 if tipo == "360" else None
        cp = d.get("camera_parameters") or []
        if tipo == "plana" and cp and cp[0] > 0:
            # focal normalizada por el lado mayor de la imagen
            w, h = d.get("width") or 4, d.get("height") or 3
            fov = round(math.degrees(2 * math.atan(0.5 * (1 if w >= h else w / h) / cp[0])), 1)
        out.append({
            "id": f"mly_{d['id']}", "fuente": "mapillary", "lat": lat, "lon": lon, "rumbo": float(az), "fov": fov,
            "tipo": tipo, "fecha": time.strftime("%Y-%m-%d", time.gmtime((d.get("captured_at") or 0) / 1000)),
            "autor": (d.get("creator") or {}).get("username", "?"), "licencia": "CC-BY-SA-4.0",
            "pagina": f"https://www.mapillary.com/app/?pKey={d['id']}", "url": d["thumb_2048_url"],
        })
    return out


# ----------------------------------------------------------------------------- Commons
def _ratio(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        if "/" in str(v):
            a, b = str(v).split("/")[:2]
            return float(a) / float(b)
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def commons(bbox, limit):
    api = "https://commons.wikimedia.org/w/api.php?"
    q = {"action": "query", "list": "geosearch", "gsbbox": f"{bbox.north}|{bbox.west}|{bbox.south}|{bbox.east}",
         "gsnamespace": 6, "gslimit": 500, "format": "json"}
    hits = _get(api + urllib.parse.urlencode(q)).get("query", {}).get("geosearch", [])
    out = []
    for k in range(0, len(hits), 40):
        titles = "|".join(h["title"] for h in hits[k:k + 40])
        q = {"action": "query", "titles": titles, "prop": "imageinfo", "format": "json",
             "iiprop": "url|metadata|extmetadata|size", "iiurlwidth": 1600}
        pages = _get(api + urllib.parse.urlencode(q)).get("query", {}).get("pages", {})
        for pg in pages.values():
            ii = (pg.get("imageinfo") or [{}])[0]
            meta = {m["name"]: m["value"] for m in (ii.get("metadata") or []) if isinstance(m, dict)}
            az = _ratio(meta.get("GPSImgDirection"))
            if az is None:
                continue
            lat, lon = None, None
            hit = next((h for h in hits if h["title"] == pg.get("title")), None)
            if hit:
                lat, lon = hit["lat"], hit["lon"]
            f35 = _ratio(meta.get("FocalLengthIn35mmFilm")) or 0
            w, h = ii.get("width", 4), ii.get("height", 3)
            fov = round(math.degrees(2 * math.atan((36.0 if w >= h else 24.0) / (2 * f35))), 1) if f35 > 0 else None
            em = ii.get("extmetadata") or {}
            lic = (em.get("LicenseShortName") or {}).get("value", "?")
            autor = re.sub("<[^>]+>", "", (em.get("Artist") or {}).get("value", "?")).strip()
            out.append({
                "id": f"wmc_{pg.get('pageid')}", "fuente": "commons", "lat": lat, "lon": lon, "rumbo": az % 360,
                "fov": fov, "tipo": "plana", "fecha": (em.get("DateTimeOriginal") or {}).get("value", "")[:19],
                "autor": autor[:80], "licencia": lic, "pagina": ii.get("descriptionurl"),
                "url": ii.get("thumburl") or ii.get("url"),
            })
            if len(out) >= limit:
                return out
    return out


# ----------------------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--centro", help="lat,lon")
    g.add_argument("--bbox", help="sur,oeste,norte,este")
    ap.add_argument("--tam", type=float, default=800)
    ap.add_argument("--nombre", required=True)
    ap.add_argument("--fuentes", default="panoramax,mapillary,commons")
    ap.add_argument("--max", type=int, default=60, help="fotos como máximo por fuente (después de aclarar)")
    ap.add_argument("--sep", type=float, default=12.0, help="separación mínima entre fotos parecidas (m)")
    ap.add_argument("--margen", type=float, default=40.0, help="no usar fotos a menos de N m del borde")
    ap.add_argument("--salida", default="referencias")
    a = ap.parse_args()
    if a.bbox:
        bbox = BBox.parse(a.bbox)
    else:
        lat, lon = (float(v) for v in a.centro.split(","))
        bbox = BBox.around(lat, lon, a.tam)
    inner = bbox.padded(-a.margen)
    folder = os.path.join(a.salida, a.nombre)
    os.makedirs(folder, exist_ok=True)
    man_path = os.path.join(folder, "referencias.json")
    old = {}
    if os.path.exists(man_path):
        old = {r["id"]: r for r in json.load(open(man_path, encoding="utf-8"))["fotos"]}

    todas = []
    for fuente in a.fuentes.split(","):
        try:
            if fuente == "panoramax":
                items = panoramax(inner, 1000)
            elif fuente == "mapillary":
                tok = os.environ.get("MAPILLARY_TOKEN")
                if not tok:
                    print("  mapillary: falta MAPILLARY_TOKEN (token de cliente gratuito); se omite")
                    continue
                items = mapillary(inner, 2000, tok)
            elif fuente == "commons":
                items = commons(inner, 400)
            else:
                print(f"  fuente desconocida: {fuente}")
                continue
        except Exception as e:  # noqa: BLE001
            print(f"  {fuente}: error ({e})")
            continue
        items = [it for it in items if it["lat"] is not None]
        # primero las panorámicas y las más recientes
        items.sort(key=_preferencia)
        items = _thin(items, a.sep)[:a.max]
        print(f"  {fuente}: {len(items)} fotos")
        todas += items

    fotos = []
    for it in todas:
        ext = ".jpg"
        fn = it["id"] + ext
        path = os.path.join(folder, fn)
        if not os.path.exists(path):
            try:
                data = _get(it["url"], timeout=120, binary=True)
            except Exception as e:  # noqa: BLE001
                print(f"  sin imagen {it['id']}: {e}")
                continue
            with open(path, "wb") as f:
                f.write(data)
        it["archivo"] = fn
        prev = old.get(it["id"], {})
        it["ajuste"] = prev.get("ajuste", {"rumbo": 0, "cabeceo": 0, "dx": 0, "dy": 0, "alto": 0})
        it["notas"] = prev.get("notas", "")
        it.update(_calidad(path))
        if "usar" in prev:
            it["usar"] = prev["usar"]
            if prev.get("motivo"):
                it["motivo"] = prev["motivo"]
        elif it["luz"] < 0.24:
            it["usar"], it["motivo"] = False, "noche"
        elif it["tipo"] == "plana" and it["aspecto"] > 2.3:
            it["usar"], it["motivo"] = False, "panorámica plana"
        else:
            it["usar"] = True
        fotos.append(it)
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump({"nombre": a.nombre, "bbox": [bbox.south, bbox.west, bbox.north, bbox.east], "fotos": fotos},
                  f, ensure_ascii=False, indent=1)
    _hoja(folder, fotos)
    print(f"ok {len(fotos)} fotos en {folder} ({sum(1 for f in fotos if f['usar'])} útiles; revisa hoja.jpg)")


if __name__ == "__main__":
    main()
