"""Descarga y lectura de OpenStreetMap vía Overpass (© OpenStreetMap contributors, ODbL)."""
import json

from .cache import post_form

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


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


def download(bbox, log=print):
    q = query(bbox)
    last = None
    for ep in ENDPOINTS:
        try:
            path = post_form(ep, {"data": q}, ext=".osm.json", log=log, retries=2)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if "elements" not in data:
                raise RuntimeError("respuesta sin elementos")
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  Overpass {ep} falló: {e}")
    raise RuntimeError(f"Overpass no respondió: {last}")


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
