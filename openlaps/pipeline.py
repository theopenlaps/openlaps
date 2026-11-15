"""Orquestador: de unas coordenadas a una escena 3D completa."""
import io
import json
import math
import os
import time

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import config as C
from . import textures, roads, terrain as T, buildings as B, vegetation as V, extras as X, barriers as BR
from . import roadside as RS
from .geo import BBox, LocalProjection
from .mesh import Scene
from .sources import osm as OSM, rasters, msbuildings
from . import export_glb, export_ac, regions

ATTRIBUTION = """Atribuciones de datos de esta pista
- Calles, edificios y usos de suelo: (c) OpenStreetMap contributors, ODbL 1.0
- Huellas de edificios: Microsoft Global ML Building Footprints, ODbL 1.0
- Relieve: Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 y (c) Airbus Defence and Space GmbH
  2014-2018, distribuido por la ESA/Copernicus  -- o --  INEGI, Continuo de Elevaciones Mexicano
  (Términos de Libre Uso de la Información del INEGI), si se usó un MDE local
- Cobertura de suelo: (c) ESA WorldCover project 2021, CC BY 4.0
- Imagen satelital: contiene datos modificados de Copernicus Sentinel{s2_years}, vía Element84 / AWS
- Altura de dosel: Meta y World Resources Institute, Global Canopy Height 1 m (Tolan et al. 2024), CC BY 4.0
- Texturas fotográficas: ambientCG.com (CC0, dominio público); el resto, generadas por algoritmo (openlaps)
"""

AREA_COLORS = {
    "farmland": (128, 110, 74), "meadow": (110, 130, 70), "grass": (104, 138, 66),
    "orchard": (90, 112, 60), "cemetery": (120, 120, 100), "pitch": (70, 130, 60),
    "parking": (92, 92, 92), "sand": (196, 176, 130), "bare_rock": (130, 122, 112),
    "park": (90, 134, 62), "playground": (160, 130, 100),
}


def run(args, log=print):
    t0 = time.time()
    if args.bbox:
        bbox = BBox.parse(args.bbox)
    else:
        lat, lon = (float(v) for v in args.centro.split(","))
        bbox = BBox.around(lat, lon, args.tam)
    proj = LocalProjection(bbox)
    R = regions.set_active(regions.detect(proj.lat0, proj.lon0) if args.region == "auto" else args.region)
    log(f"Región: {R.name} (rayas {R.center_mat.replace('raya_', '')}, techos {R.roof}"
        f"{', ortofoto ' + R.ortho if R.ortho else ''}{', relieve ' + R.dem if R.dem else ''})")
    rect = (proj.xmin, proj.ymin, proj.xmax, proj.ymax)
    area_km2 = (proj.xmax - proj.xmin) * (proj.ymax - proj.ymin) / 1e6
    res = args.res or (2.0 if area_km2 <= 2.5 else (3.0 if area_km2 <= 8 else 4.0))
    log(f"Área: {area_km2:.2f} km² centrada en {proj.lat0:.5f}, {proj.lon0:.5f} (UTM EPSG:{proj.epsg})")

    # ------------------------------------------------------------ datos
    log("1/7 Descargando OpenStreetMap…")
    osm = OSM.parse(OSM.download(bbox.padded(200), log=log))
    log(f"  {len(osm['ways'])} vías, {len(osm['areas'])} áreas, {len(osm['nodes'])} nodos sueltos")
    log("2/7 Relieve y cobertura de suelo…")
    big = bbox.padded(450)
    if args.dem:
        dem = rasters.local_raster(args.dem, big)
        dem_is_dtm = not args.dem_es_superficie
        log(f"  MDE local: {args.dem} ({'terreno' if dem_is_dtm else 'superficie'})")
    else:
        dem = None
        if R.dem == "mdt05":
            try:
                dem = rasters.mdt05(big, log=log)
                dem_is_dtm = True
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: sin MDT05 ({e}); se usa Copernicus")
        if dem is None:
            dem = rasters.copernicus_dem(big, log=log)
            dem_is_dtm = False
            log("  Copernicus GLO-30 (superficie, se corrige a terreno)")
    lc = rasters.worldcover(big, log=log)
    s2 = chm = orto = None
    if R.ortho and not args.sin_orto:
        try:
            orto = rasters.ortho(bbox.padded(60), R.ortho, mpp=0.25, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin ortofoto ({e})")
    mpp = args.mpp or (0.25 if orto is not None else 0.5)
    if not args.sin_satelite and orto is None:
        try:
            s2 = rasters.sentinel2(bbox.padded(60), args.temporada, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin Sentinel-2 ({e})")
    if not args.sin_dosel:
        try:
            chm = rasters.canopy_height(bbox, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin altura de dosel ({e})")
    ms = []
    if not args.sin_microsoft:
        try:
            ms = msbuildings.download(bbox, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin edificios de Microsoft ({e})")

    terr = T.Terrain(proj, dem, lc, dem_is_dtm, res, log=log)

    # ------------------------------------------------------------ zona urbana
    log("3/7 Edificios y zonas urbanas…")
    blds = B.collect(osm, ms, proj, rect, log=log)
    urban_parts = [b["poly"].buffer(22) for b in blds]
    water_parts, areas = [], []
    for a in osm["areas"]:
        t = a["tags"]
        x, y = proj.to_local([c[0] for c in a["outer"]], [c[1] for c in a["outer"]])
        if len(x) < 4:
            continue
        try:
            pg = Polygon(list(zip(x, y))).buffer(0)
        except Exception:  # noqa: BLE001
            continue
        if pg.is_empty:
            continue
        lu = t.get("landuse")
        if lu in ("residential", "commercial", "retail"):
            urban_parts.append(pg)
        if t.get("natural") == "water" or lu in ("reservoir", "basin"):
            water_parts.append(pg)
        key = lu or t.get("leisure") or t.get("natural") or ("parking" if t.get("amenity") == "parking" else None)
        if key in AREA_COLORS:
            areas.append((pg, AREA_COLORS[key]))
    if lc is not None:
        # celdas construidas de WorldCover como respaldo
        gx = np.arange(proj.xmin, proj.xmax, 20.0)
        gy = np.arange(proj.ymin, proj.ymax, 20.0)
        GX, GY = np.meshgrid(gx, gy)
        cls = lc.sample(proj, GX, GY, order=0)
        m = cls == C.WC_BUILT
        # WorldCover marca como "construido" también las carreteras (líneas de 1-2 celdas): sin
        # esta apertura morfológica, toda carretera rural quedaba "urbana" (banquetas, farolas,
        # raya continua y sin pivotes). La apertura conserva solo manchas reales de pueblo/polígono.
        from scipy.ndimage import binary_opening
        m = binary_opening(m, structure=np.ones((3, 3), bool))
        if m.any():
            pts = shapely.points(GX[m], GY[m])
            urban_parts.append(unary_union(shapely.buffer(pts, 18.0, quad_segs=2)))
    urban_poly = unary_union(urban_parts).buffer(-8).buffer(8) if urban_parts else None
    water_poly = unary_union(water_parts) if water_parts else None
    if urban_poly is not None:
        shapely.prepare(urban_poly)
    urban_fn = (lambda x, y: shapely.contains_xy(urban_poly, x, y)) if urban_poly is not None else None

    # ------------------------------------------------------------ carreteras
    log("4/7 Red de carreteras…")
    net = roads.RoadNetwork(osm, proj, terr.z, urban_fn, rect, log=log)
    scene = Scene()
    cc0_tex = {}
    if not args.sin_cc0:
        from .sources import cc0 as CC0
        cc0_tex = CC0.load_all(1024, log=log)
    textures.register_all(scene, args.temporada, cc0_tex)
    RS.register_materials(scene)
    blds_poly_pre = unary_union([b["poly"] for b in blds]) if blds else None
    has_gr = any(b["tags"].get("barrier") == "guard_rail" for b in osm.get("barriers", []))
    rd = roads.build_roads(net, scene, blds_poly_pre, water_poly, urban_poly, log=log, osm_guardrails=has_gr,
                           ortho=orto, proj=proj)
    keep_out = rd["road_union"]
    if rd["sidewalks"] is not None and keep_out is not None:
        keep_out = unary_union([keep_out, rd["sidewalks"]])
    blds = B.drop_on_roads(blds, keep_out)
    blds_poly = unary_union([b["poly"] for b in blds]) if blds else None

    # ------------------------------------------------------------ modo de juego
    route = route_hw = None
    info_run, length = "free roam", 0.0
    if args.modo in ("tramo", "circuito"):
        wps = []
        for tok in args.ruta.split(";"):
            la, lo = (float(v) for v in tok.split(","))
            x, y = proj.to_local([lo], [la])
            wps.append((float(x[0]), float(y[0])))
        route, route_hw, legs = roads.route(net, wps, closed=args.modo == "circuito")
        length = float(np.sum(np.linalg.norm(np.diff(route[:, :2], axis=0), axis=1)))
        log(f"  ruta de {args.modo}: {length / 1000:.2f} km por {len(legs)} tramos de calle")
        _place_stage(scene, net, route, route_hw, legs, closed=args.modo == "circuito", log=log)
        X.tv_cameras(scene, route)
        info_run = "clockwise" if args.modo == "circuito" else "A-B"
    else:
        _place_free(scene, net, log=log)

    # ------------------------------------------------------------ terreno
    log("5/7 Terreno: tallado y textura…")
    terr.build_grid()
    terr.flatten_urban(rd["carve"], urban_fn)
    terr.carve(rd["carve"], rd["tunnel"])
    paths = []
    for w in osm["ways"]:
        if w["tags"].get("highway") in C.PATHS:
            x, y = proj.to_local([c[0] for c in w["coords"]], [c[1] for c in w["coords"]])
            paths.append((np.column_stack([x, y]), 1.3))
    painter = T.GroundPainter(lc, proj, args.temporada, rd["road_polys"], paths, areas, blds_poly, water_poly, s2=s2,
                              ortho=orto)
    terr.build_mesh(scene, painter, tile_m=args.tesela, mpp=mpp)
    if water_poly is not None:
        for pg in roads.iter_polys(water_poly.intersection(shapely.geometry.box(*rect))):
            verts, faces = roads.triangulate_polygon(pg, 8.0)
            if len(faces):
                z = float(np.percentile(terr.height(verts[:, 0], verts[:, 1]), 10)) + 0.1
                scene.add("agua", "agua", np.column_stack([verts, np.full(len(verts), z)]), verts / 8, faces)

    m = BR.build(scene, osm.get("barriers", []), proj, terr, rect, rd["road_union"])
    if m:
        log("  barreras de OSM: " + ", ".join(f"{k} {v:.0f} m" for k, v in m.items()))
    BR.boundary(scene, terr, rect)
    ns = RS.power_lines(scene, osm, proj, terr, rect)
    fm = RS.fences(scene, osm, proj, terr, rect, rd["road_union"])
    if ns or fm:
        log(f"  tendido eléctrico: {ns} apoyos; vallas de malla: {fm:.0f} m")

    # ------------------------------------------------------------ edificios y vegetación
    log("6/7 Edificios y vegetación…")
    B.build(scene, blds, terr, log=log)
    ne, nw = X.edges_and_wear(net, scene, terr, rd["road_union"])
    nl = X.street_lamps(net, scene)
    sg = X.signs(net, scene)
    RS.place_signs(net, scene, osm, proj, log=log)
    log(f"  detalles: orillas en {ne} tramos, desgaste en {nw}, {nl} arbotantes, señales: "
        + ", ".join(f"{k} {v}" for k, v in sg.items()))
    excl = [rd["road_union"].buffer(2.0)] if rd["road_union"] is not None else []
    if rd["sidewalks"] is not None:
        excl.append(rd["sidewalks"].buffer(0.5))
    if blds_poly is not None:
        excl.append(blds_poly.buffer(1.5))
    if water_poly is not None:
        excl.append(water_poly)
    exclude = unary_union(excl) if excl else None
    if exclude is not None:
        shapely.prepare(exclude)
    trees = [n for n in osm["nodes"] if n["tags"].get("natural") == "tree"]
    osm_trees = None
    if trees:
        x, y = proj.to_local([n["lon"] for n in trees], [n["lat"] for n in trees])
        osm_trees = np.column_stack([x, y])
        inside = (x > proj.xmin) & (x < proj.xmax) & (y > proj.ymin) & (y < proj.ymax)
        osm_trees = osm_trees[inside]
    trunks = V.build(scene, terr, lc, proj, exclude, osm_trees, density=args.vegetacion,
                     road_pts=rd["carve"][:, :2] if len(rd["carve"]) else None, chm=chm, log=log)
    if trunks is not None and len(trunks):
        log(f"  {X.trunk_colliders(scene, trunks)} troncos con colisión junto a la calle")
    if not args.sin_horizonte:
        try:
            far = bbox.padded(5000)
            dem_far = rasters.copernicus_dem(far, log=log)
            s2_far = rasters.sentinel2(far, args.temporada, log=log) if not args.sin_satelite else None
            nf = X.horizon(scene, proj, dem_far, s2_far, rect, terrain=terr)
            log(f"  horizonte: {nf:,} triángulos hasta 5 km")
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin horizonte ({e})")

    # ------------------------------------------------------------ exportar
    log("7/7 Exportando…")
    os.makedirs(args.salida, exist_ok=True)
    name = args.nombre
    st = scene.stats()
    log(f"  escena: {st['mallas']} mallas, {st['vertices']:,} vértices, {st['triangulos']:,} triángulos, "
        f"{st['texturas']} texturas")
    prev = T.mosaic(terr.tiles, proj, 2.0)
    prev.save(os.path.join(args.salida, f"{name}_cenital.jpg"), quality=88)
    extras = {"nombre": name, "centro": [proj.lat0, proj.lon0], "modo": args.modo}
    if route is not None:
        extras["ruta"] = export_glb.to_yup(route).round(2).tolist()
    jx = [(net.node_xy[n][0], net.node_xy[n][1], net.node_z[n], len(j["inc"])) for n, j in net.junctions.items() if net.node_inside(n)]
    extras["cruces"] = [[round(float(x), 2), round(float(z), 2), round(float(-y), 2), k] for x, y, z, k in jx]
    glb = os.path.join(args.salida, f"{name}.glb")
    size = export_glb.write(scene, glb, extras)
    log(f"  {glb} ({size / 1e6:.1f} MB)")
    if args.ac:
        thumb = prev.copy()
        thumb.thumbnail((512, 512))
        bio = io.BytesIO()
        thumb.save(bio, "PNG")
        map_lines = [e.at(np.arange(a, b, 2.0), 0.0)[:, :2] for e in net.edges for a, b in e.runs if b - a > 4]
        info = {"name": args.titulo or name, "lat": proj.lat0, "lon": proj.lon0,
                "attribution": ATTRIBUTION.format(
                    s2_years=f" ({', '.join(sorted(rasters.S2_YEARS))})" if rasters.S2_YEARS else "") + "".join("- " + a + chr(10) for a in R.attribution),
                "description": f"Generada con openlaps a partir de datos abiertos. {area_km2:.2f} km².",
                "tags": [t for t in (R.code, "openlaps", args.modo) if t], "length": length or rd["stats"]["km"] * 1000, "run": info_run,
                "pitboxes": 8, "preview_png": bio.getvalue(),
                "ai_hw": route_hw, "width": int(round(float(np.median(route_hw)) * 2)) if route_hw is not None else "",
                "closed": args.modo == "circuito"}
        export_ac.write_track(scene, os.path.join(args.salida, "assetto_corsa"), name, info, map_lines, route, log=log)
    with open(os.path.join(args.salida, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"area_km2": area_km2, "stats": st, "carreteras": rd["stats"], "segundos": round(time.time() - t0, 1),
                   "centro": [proj.lat0, proj.lon0], "modo": args.modo}, f, ensure_ascii=False, indent=2)
    log(f"Listo en {time.time() - t0:.0f} s")
    return glb


# --------------------------------------------------------------------------------------
def _frame(poly, i):
    j0, j1 = max(0, i - 2), min(len(poly) - 1, i + 2)
    t = poly[j1, :2] - poly[j0, :2]
    t /= max(np.linalg.norm(t), 1e-9)
    return t, np.array([-t[1], t[0]])


def _at_dist(poly, d):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(poly[:, :2], axis=0), axis=1))])
    return int(np.clip(np.searchsorted(s, d), 0, len(poly) - 1))


def _gate(scene, p, t, n, hw, mat):
    for side in (-1, 1):
        c = p[:2] + n * side * (hw + 0.8)
        pp, uv, f = roads.box_mesh((c[0], c[1], p[2] - 0.3), (0.35, 0.35, 6.3), math.atan2(t[1], t[0]))
        scene.add("portico", "poste", pp, uv, f, phys="WALL")
    a = p[:2] + n * (hw + 0.8)
    b = p[:2] - n * (hw + 0.8)
    z0, z1 = p[2] + 4.6, p[2] + 6.0
    pos = np.array([[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]])
    uv = np.array([(0, 1), (1, 1), (1, 0), (0, 0)])
    scene.add("portico", mat, pos, uv, [(0, 1, 2), (0, 2, 3)])
    pos2 = pos[[1, 0, 3, 2]]
    scene.add("portico", mat, pos2, uv, [(0, 1, 2), (0, 2, 3)])


def _place_grid(scene, poly, hw, i_line, back=True, count=12, prefix="AC_START"):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(poly[:, :2], axis=0), axis=1))])
    for k in range(count):
        d = s[i_line] - (8.0 + 7.0 * k) * (1 if back else -1)
        i = int(np.clip(np.searchsorted(s, d), 0, len(poly) - 1))
        t, n = _frame(poly, i)
        lat = (hw[i] * 0.45) * (1 if k % 2 == 0 else -1)
        pos = poly[i].copy()
        pos[:2] += n * lat
        pos[2] += 0.3
        scene.dummy(f"{prefix}_{k}", pos, t)


def _place_stage(scene, net, route, route_hw, legs, closed, log=print):
    L = float(np.sum(np.linalg.norm(np.diff(route[:, :2], axis=0), axis=1)))
    i_start = _at_dist(route, min(60.0, L * 0.1))
    i_end = _at_dist(route, max(L - 30.0, L * 0.9)) if not closed else i_start
    t, n = _frame(route, i_start)
    hw = route_hw
    p = route[i_start]
    if closed:
        for side, nm in ((1, "AC_TIME_0_L"), (-1, "AC_TIME_0_R")):
            scene.dummy(nm, p + np.r_[n * side * (hw[i_start] + 1), 0.5], t)
        _gate(scene, p, t, n, hw[i_start], "lona_meta")
    else:
        for side, nm in ((1, "AC_AB_START_L"), (-1, "AC_AB_START_R")):
            scene.dummy(nm, p + np.r_[n * side * (hw[i_start] + 1), 0.5], t)
        _gate(scene, p, t, n, hw[i_start], "lona_salida")
        q = route[i_end]
        t2, n2 = _frame(route, i_end)
        for side, nm in ((1, "AC_AB_FINISH_L"), (-1, "AC_AB_FINISH_R")):
            scene.dummy(nm, q + np.r_[n2 * side * (hw[i_end] + 1), 0.5], t2)
        _gate(scene, q, t2, n2, hw[i_end], "lona_meta")
    _place_grid(scene, route, hw, i_start, count=12, prefix="AC_START")
    _place_grid(scene, route, hw, i_start, count=8, prefix="AC_PIT")
    hp = route[max(0, i_start - 3)].copy()
    hp[2] += 0.3
    scene.dummy("AC_HOTLAP_START_0", hp, t)
    # cerrar las calles laterales con vallas, como en un tramo de rally de verdad
    on_route = {id(e) for e, _, _ in legs}
    nodes_on = set()
    for e, s0, s1 in legs:
        lo, hi = min(s0, s1), max(s0, s1)
        if lo < 1.0:
            nodes_on.add(e.a)
        if hi > e.length - 1.0:
            nodes_on.add(e.b)
    inc = net.incident()
    closed_n = 0
    for nid in nodes_on:
        for e, end in inc.get(nid, []):
            if id(e) in on_route:
                continue
            s = (e.s0 + 1.5) if end == 0 else (e.s1 - 1.5)
            if not (0 < s < e.length) or not any(a <= s <= b for a, b in e.runs):
                continue
            a = e.at(s, e.hw)
            b = e.at(s, -e.hw)
            pts = np.vstack([a, b])
            for k in range(2):
                pp, uv, f = roads.wall_strip(pts if k == 0 else pts[::-1], 1.0, u_scale=1, v_scale=1, both=False)
                scene.add("vallas", "valla", pp, uv, f, phys="WALL")
            closed_n += 1
    log(f"  salida, meta y {closed_n} calles laterales cerradas con vallas")


def _place_free(scene, net, log=print):
    best, score = None, -1
    for e in net.edges:
        for a, b in net.ranges(e):
            sc = (b - a) * (1 + e.prio)
            if sc > score and b - a > 40:
                best, score = (e, a, b), sc
    if best is None:
        for e in net.edges:
            for a, b in net.ranges(e):
                if b - a > score:
                    best, score = (e, a, b), b - a
    if best is None:
        log("  aviso: no hay calles dentro del área para poner salidas")
        return
    e, a, b = best
    sv = np.arange(a + 2, b - 2, 1.0)
    poly = e.at(sv, 0.0)
    hw = np.full(len(poly), e.hw)
    i_line = len(poly) - 1 if len(poly) < 120 else 110
    _place_grid(scene, poly, hw, i_line, count=12, prefix="AC_START")
    _place_grid(scene, poly, hw, i_line, count=8, prefix="AC_PIT")
    t, n = _frame(poly, i_line)
    p = poly[i_line].copy()
    p[2] += 0.3
    scene.dummy("AC_HOTLAP_START_0", p, t)
    for side, nm in ((1, "AC_TIME_0_L"), (-1, "AC_TIME_0_R")):
        scene.dummy(nm, poly[i_line] + np.r_[n * side * (e.hw + 1), 0.5], t)
    log(f"  salidas en {e.tags.get('name', e.cls)} ({b - a:.0f} m)")
