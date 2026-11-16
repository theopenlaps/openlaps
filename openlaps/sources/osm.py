"""Descarga y lectura de OpenStreetMap vía Overpass (© OpenStreetMap contributors, ODbL)."""
import json
import os
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .cache import UA, cache_path, post_form

ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# en vivo, los espejos que contestan más rápido por cada tesela pequeña
ENDPOINTS_VIVO = [("https://maps.mail.ru/osm/tools/overpass/api/interpreter", 20),
                  ("https://overpass-api.de/api/interpreter", 25)]


def query(bbox):
    b = bbox.overpass()
    return f"""[out:json][timeout:180];
(
  way["highway"]({b});
  way["railway"~"^(tram|light_rail)$"]({b});
  way["building"]({b});
  relation["building"]["type"="multipolygon"]({b});
  way["landuse"]({b});
  way["natural"]({b});
  relation["natural"="water"]({b});
  way["waterway"]({b});
  way["amenity"="parking"]({b});
  way["leisure"]({b});
  node["traffic_calming"]({b});
  node["highway"~"crossing|traffic_signals|stop|give_way|milestone|bus_stop"]({b});
  node["place"~"^(city|town|village|hamlet|suburb)$"]({b});
  node["power"~"^(pole|tower)$"]({b});
  way["power"~"^(line|minor_line)$"]({b});
  node["natural"="tree"]({b});
  node["barrier"]({b});
  way["barrier"~"^(guard_rail|wall|retaining_wall|city_wall|jersey_barrier|fence)$"]({b});
);
out geom qt;"""


def _respuesta_valida(data):
    if "elements" not in data:
        raise RuntimeError("respuesta sin elementos")
    rem = data.get("remark", "")
    if "error" in rem.lower():                 # Overpass corta la consulta y devuelve lo que llevaba
        raise RuntimeError(f"Overpass incompleto: {rem[:120]}")
    return data


def _leer_cache_osm(bbox, q):
    enc = urllib.parse.urlencode({"data": q})
    rutas = [cache_path(ep + "?" + enc, ".osm.json") for ep in ENDPOINTS] + [
        cache_path(f"osmapi:{bbox.overpass()}", ".osm.json")
    ]
    for path in rutas:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return _respuesta_valida(json.load(f))
            except Exception:  # noqa: BLE001
                pass
    return None


def _post_overpass(ep, q, timeout, retries=1):
    path = post_form(ep, {"data": q}, ext=".osm.json", log=lambda *a: None, retries=retries, timeout=timeout)
    try:
        with open(path, encoding="utf-8") as f:
            return _respuesta_valida(json.load(f))
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _descargar_osm(bbox, espejos, log, timeout_api=30):
    q = query(bbox)
    cached = _leer_cache_osm(bbox, q)
    if cached is not None:
        return cached
    last = None
    for ep, espera in espejos:
        try:
            return _post_overpass(ep, q, timeout=espera, retries=1)
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  Overpass {ep.split('/')[2]} falló: {str(e)[:100]}")
    try:
        return download_api(bbox, log=log, timeout=timeout_api)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Overpass no respondió ({last}; API OSM: {e})") from e


def download(bbox, log=print, timeout=20):
    """Descarga OSM de Overpass con espera acotada y respaldo automático en la API oficial de OSM."""
    return _descargar_osm(bbox, [(ep, timeout) for ep in ENDPOINTS], log=log, timeout_api=max(30, timeout))


# ------------------------------------------------------------------ descarga rápida (modo en vivo)
API = "https://api.openstreetmap.org/api/0.6/map"


def download_fast(bbox, log=print, timeout=30):
    """Para el modo en vivo: un espejo de Overpass tras otro con espera corta y, si ninguno contesta,
    la API oficial de OpenStreetMap."""
    return _descargar_osm(bbox, ENDPOINTS_VIVO, log=log, timeout_api=timeout)


def download_api(bbox, log=print, timeout=30):
    """API oficial de OSM (/api/0.6/map, XML): devuelve los elementos con geometría como lo haría Overpass
    con `out geom`, filtrados a lo que pide `query`. Si la zona tiene demasiados nodos, la parte en cuatro."""
    path = cache_path(f"osmapi:{bbox.overpass()}", ".osm.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    url = f"{API}?bbox={bbox.west:.6f},{bbox.south:.6f},{bbox.east:.6f},{bbox.north:.6f}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            xml = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 400 and (bbox.north - bbox.south) > 0.0008:   # más de 50 000 nodos: en cuatro
            from ..geo import BBox
            ml, mo = (bbox.south + bbox.north) / 2, (bbox.west + bbox.east) / 2
            partes = [BBox(bbox.south, bbox.west, ml, mo), BBox(bbox.south, mo, ml, bbox.east),
                      BBox(ml, bbox.west, bbox.north, mo), BBox(ml, mo, bbox.north, bbox.east)]
            vistos, els = set(), []
            for b in partes:
                for el in download_api(b, log=log, timeout=timeout)["elements"]:
                    k = (el["type"], el["id"])
                    if k not in vistos:
                        vistos.add(k)
                        els.append(el)
            data = {"elements": els}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        raise
    data = {"elements": _api_a_overpass(xml)}
    log(f"  OSM desde la API oficial: {len(data['elements'])} elementos")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _quiere(tipo, t):
    """Lo mismo que selecciona `query` (para filtrar lo que trae la API, que es todo)."""
    if tipo == "node":
        return (t.get("traffic_calming") or t.get("highway") in ("crossing", "traffic_signals", "stop", "give_way",
                                                                   "milestone", "bus_stop")
                or t.get("place") in ("city", "town", "village", "hamlet", "suburb")
                or t.get("power") in ("pole", "tower") or t.get("natural") == "tree" or "barrier" in t)
    if tipo == "way":
        return ("highway" in t or t.get("railway") in ("tram", "light_rail") or "building" in t or "landuse" in t
                or "natural" in t or "waterway" in t or t.get("amenity") == "parking" or "leisure" in t
                or t.get("power") in ("line", "minor_line")
                or t.get("barrier") in ("guard_rail", "wall", "retaining_wall", "city_wall", "jersey_barrier", "fence"))
    return (t.get("type") == "multipolygon" and "building" in t) or t.get("natural") == "water"


def _api_a_overpass(xml):
    root = ET.fromstring(xml)
    tags = lambda e: {k.get("k"): k.get("v") for k in e.findall("tag")}  # noqa: E731
    nodos, els, vias = {}, [], {}
    for n in root.iter("node"):
        i = int(n.get("id"))
        nodos[i] = (float(n.get("lon")), float(n.get("lat")))
        t = tags(n)
        if t and _quiere("node", t):
            els.append({"type": "node", "id": i, "lat": nodos[i][1], "lon": nodos[i][0], "tags": t})
    for w in root.iter("way"):
        refs = [int(nd.get("ref")) for nd in w.findall("nd")]
        geom = [{"lon": nodos[r][0], "lat": nodos[r][1]} if r in nodos else None for r in refs]
        vias[int(w.get("id"))] = geom
        t = tags(w)
        if _quiere("way", t) and all(geom):
            els.append({"type": "way", "id": int(w.get("id")), "nodes": refs, "geometry": geom, "tags": t})
    for r in root.iter("relation"):
        t = tags(r)
        if not _quiere("relation", t):
            continue
        miembros = []
        for m in r.findall("member"):
            if m.get("type") != "way":
                continue
            g = vias.get(int(m.get("ref")))
            if g and all(g):
                miembros.append({"type": "way", "ref": int(m.get("ref")), "role": m.get("role"), "geometry": g})
        if miembros:
            els.append({"type": "relation", "id": int(r.get("id")), "members": miembros, "tags": t})
    return els


def _join_rings(segments):
    """Une tramos de vía (listas de (lon,lat)) en anillos cerrados."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def parse(data):
    """Devuelve dict con listas de elementos ya separados por tipo.

    ways: lista de dict(id, tags, nodes=[ids], coords=[(lon,lat)])
    areas: lista de dict(tags, outer=[(lon,lat)...], inners=[[...]])
    nodes: lista de dict(id, tags, lon, lat)
    barriers: lista de dict(tags, coords) con guardarraíles, muros y barreras dibujados en OSM
    coast: lista de líneas de costa [(lon,lat)...] (natural=coastline: tierra a la izquierda, mar a la derecha)
    """
    ways, areas, nodes, barriers, power, coast, rails = [], [], [], [], [], [], []
    for el in data["elements"]:
        t = el.get("type")
        tags = el.get("tags", {})
        if t == "node":
            nodes.append({"id": el["id"], "tags": tags, "lon": el["lon"], "lat": el["lat"]})
        elif t == "way" and "geometry" in el:
            coords = [(g["lon"], g["lat"]) for g in el["geometry"] if g]
            if len(coords) < 2:
                continue
            w = {"id": el["id"], "tags": tags, "nodes": el.get("nodes", []), "coords": coords}
            closed = len(coords) >= 4 and coords[0] == coords[-1]
            is_area = closed and ("building" in tags or "landuse" in tags or tags.get("area") == "yes"
                                  or tags.get("natural") in ("water", "wood", "scrub", "grassland", "bare_rock", "sand", "wetland")
                                  or tags.get("amenity") == "parking" or "leisure" in tags)
            if "highway" in tags and tags.get("area") != "yes":
                ways.append(w)
            elif "barrier" in tags:
                barriers.append({"tags": tags, "coords": coords})
            elif tags.get("railway") in ("tram", "light_rail"):
                rails.append({"tags": tags, "coords": coords})
            elif tags.get("natural") == "coastline":
                coast.append(coords)
            elif tags.get("power") in ("line", "minor_line"):
                power.append({"tags": tags, "coords": coords, "nodes": el.get("nodes", [])})
            elif is_area:
                areas.append({"tags": tags, "outer": coords, "inners": []})
            elif "waterway" in tags:
                ways.append(w)
        elif t == "relation" and "members" in el:
            outers = [[(g["lon"], g["lat"]) for g in m.get("geometry", []) if g]
                      for m in el["members"] if m.get("type") == "way" and m.get("role") in ("outer", "")]
            inners = [[(g["lon"], g["lat"]) for g in m.get("geometry", []) if g]
                      for m in el["members"] if m.get("type") == "way" and m.get("role") == "inner"]
            inner_rings = _join_rings(inners)
            for ring in _join_rings(outers):
                areas.append({"tags": tags, "outer": ring, "inners": inner_rings})
    return {"ways": ways, "areas": areas, "nodes": nodes, "barriers": barriers, "power": power, "coast": coast, "rails": rails}
