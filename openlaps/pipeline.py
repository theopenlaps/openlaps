"""Orquestador: de unas coordenadas a una escena 3D completa."""
import io
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import config as C
from . import textures, roads, terrain as T, buildings as B, vegetation as V, extras as X, barriers as BR
from . import cobertura as COB, agua as AG
from . import roadside as RS
from .geo import BBox, LocalProjection
from .mesh import Scene
from .sources import osm as OSM, rasters, msbuildings, catastro, overture
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


def run(args, log=print, proj=None, scene=None, osm_data=None, pedazo=None, cat_data=None, extension=None):
    """Genera la escena y la exporta. Modo en vivo (`pedazo` = (xmin, ymin, xmax, ymax), el centro que se conserva):
    usa la proyección `proj` ya fijada (origen común a todos los pedazos), la escena con los materiales ya
    registrados y los datos de OSM ya descargados, no pone muros de límite, salidas ni horizonte y devuelve
    la escena sin exportar nada."""
    t0 = time.time()
    if proj is not None:
        bbox = proj.bbox()
    elif args.bbox:
        bbox = BBox.parse(args.bbox)
    else:
        lat, lon = (float(v) for v in args.centro.split(","))
        bbox = BBox.around(lat, lon, args.tam)
    if proj is None:
        proj = LocalProjection(bbox)
    R = regions.set_active(regions.detect(proj.lat0, proj.lon0) if args.region == "auto" else args.region)
    log(f"Región: {R.name} (rayas {R.center_mat.replace('raya_', '')}, techos {R.roof}"
        f"{', ortofoto ' + R.ortho if R.ortho else ''}{', relieve ' + R.dem if R.dem else ''})")
    rect = (proj.xmin, proj.ymin, proj.xmax, proj.ymax)
    area_km2 = (proj.xmax - proj.xmin) * (proj.ymax - proj.ymin) / 1e6
    res = args.res or (2.0 if area_km2 <= 2.5 else (3.0 if area_km2 <= 8 else 4.0))
    log(f"Área: {area_km2:.2f} km² centrada en {proj.lat0:.5f}, {proj.lon0:.5f} (UTM EPSG:{proj.epsg})")

    # ------------------------------------------------------------ datos
    # Todas las descargas van en paralelo: son independientes y casi todo el tiempo es espera de
    # red (Overpass, COG remotos, WMS, WFS). Una tras otra, la primera generación de una zona
    # pasaba más de un minuto solo esperando.
    log("1/7 Descargando datos (en paralelo)…")
    pool = ThreadPoolExecutor(max_workers=8)

    def intento(nombre, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin {nombre} ({e})")
            return None

    # relieve alrededor del área: 300 m, o hasta donde llegue un túnel o un puente que la cruza (modo en vivo:
    # todos los pedazos que lo tocan tienen que calcular el mismo perfil)
    pad_relieve = 300.0
    if extension is not None:
        ex0, ey0, ex1, ey1 = extension
        pad_relieve = min(1600.0, max(300.0, proj.xmin - ex0, proj.ymin - ey0, ex1 - proj.xmax, ey1 - proj.ymax) + 80)
    big = bbox.padded(pad_relieve + 150)

    def relieve():
        if args.dem:
            log(f"  MDE local: {args.dem} ({'superficie' if args.dem_es_superficie else 'terreno'})")
            return rasters.local_raster(args.dem, big), not args.dem_es_superficie
        if R.dem == "mdt05":
            try:
                return rasters.mdt05(big, log=log), True
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: sin MDT05 ({e}); se usa Copernicus")
        if R.dem == "3dep":
            try:
                return rasters.dem_3dep(big, log=log), True
            except Exception as e:  # noqa: BLE001
                log(f"  aviso: sin 3DEP ({e}); se usa Copernicus")
        d = rasters.copernicus_dem(big, log=log)
        log("  Copernicus GLO-30 (superficie, se corrige a terreno)")
        return d, False

    usar_orto = bool(R.ortho) and not args.sin_orto
    f_osm = pool.submit(lambda: osm_data if osm_data is not None else OSM.parse(OSM.download(bbox.padded(200), log=log)))
    f_dem = pool.submit(relieve)
    f_lc = pool.submit(rasters.worldcover, big, log=log)
    f_orto = pool.submit(intento, "ortofoto", rasters.ortho, bbox.padded(60), R.ortho, mpp=R.ortho_mpp, log=log)         if usar_orto else None
    f_s2 = pool.submit(intento, "Sentinel-2", rasters.sentinel2, bbox.padded(60), args.temporada, log=log)         if not args.sin_satelite and not usar_orto else None
    f_ndwi = pool.submit(intento, "índice de agua NDWI", rasters.sentinel2_ndwi, bbox.padded(60), args.temporada,
                         log=log) if not args.sin_satelite else None
    f_chm = pool.submit(intento, "altura de dosel", rasters.canopy_height, bbox, log=log) if not args.sin_dosel else None
    f_ms = pool.submit(intento, "edificios de Microsoft", msbuildings.download, bbox, log=log)         if not args.sin_microsoft else None
    if cat_data is not None:        # modo en vivo: el servicio ya lo bajó por teselas
        f_cat = pool.submit(lambda: cat_data)
    else:
        f_cat = pool.submit(intento, "Catastro", catastro.download, bbox, log=log)             if R.cadastre == "catastro" and not getattr(args, "sin_catastro", False) else None
    f_ov = pool.submit(intento, "alturas de Overture", overture.download, bbox, log=log) \
        if R.heights == "overture" and not getattr(args, "sin_overture", False) else None
    # el relieve y la imagen del horizonte (5 km) se usan al final, pero se piden ya
    far = bbox.padded(5000)
    con_horizonte = not args.sin_horizonte and pedazo is None
    f_far_dem = pool.submit(rasters.copernicus_dem, far, log=log) if con_horizonte else None
    f_far_s2 = pool.submit(intento, "Sentinel-2 del horizonte", rasters.sentinel2, far, args.temporada, log=log)         if con_horizonte and not args.sin_satelite else None

    # mientras llegan los datos: texturas y materiales (cálculo puro, no depende de la zona salvo
    # por el perfil regional, que ya está elegido)
    if scene is None:
        scene = preparar_escena(args, log)

    osm = f_osm.result()
    log(f"  {len(osm['ways'])} vías, {len(osm['areas'])} áreas, {len(osm['nodes'])} nodos sueltos")
    log("2/7 Relieve y cobertura de suelo…")
    dem, dem_is_dtm = f_dem.result()
    lc = f_lc.result()
    orto = f_orto.result() if f_orto else None
    s2 = f_s2.result() if f_s2 else None
    if s2 is None and usar_orto and orto is None and not args.sin_satelite:
        s2 = intento("Sentinel-2", rasters.sentinel2, bbox.padded(60), args.temporada, log=log)
    mpp = args.mpp or (min(0.25, R.ortho_mpp) if orto is not None else 0.5)
    ndwi = f_ndwi.result() if f_ndwi else None
    chm = f_chm.result() if f_chm else None
    ms = (f_ms.result() if f_ms else None) or []
    cat = (f_cat.result() if f_cat else None) or []
    ov = (f_ov.result() if f_ov else None) or []

    terr = T.Terrain(proj, dem, lc, dem_is_dtm, res, pad=pad_relieve, log=log)

    # ------------------------------------------------------------ zona urbana
    log("3/7 Edificios y zonas urbanas…")
    blds = B.collect(osm, ms, proj, rect, log=log, cat_parts=cat, alturas=ov)
    urban_parts = [b["poly"].buffer(22) for b in blds]
    areas = []
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
    # qué es cada sitio (agua, bosque, roca...): OSM + WorldCover + índice de agua de Sentinel-2
    cob = COB.Cobertura(osm, proj, rect, lc, ndwi, args.temporada, log=log)
    water_poly = cob.agua
    if urban_poly is not None:
        shapely.prepare(urban_poly)
    urban_fn = (lambda x, y: shapely.contains_xy(urban_poly, x, y)) if urban_poly is not None else None

    # ------------------------------------------------------------ carreteras
    log("4/7 Red de carreteras…")
    net = roads.RoadNetwork(osm, proj, terr.z, urban_fn, rect, log=log, tuneles=getattr(args, "tuneles", "principales"))
    blds_poly_pre = unary_union([b["poly"] for b in blds]) if blds else None
    has_gr = any(b["tags"].get("barrier") == "guard_rail" for b in osm.get("barriers", []))
    rails = []
    for r in osm.get("rails", []):
        x, y = proj.to_local([c[0] for c in r["coords"]], [c[1] for c in r["coords"]])
        rails.append({"tags": r["tags"], "xy": np.column_stack([x, y])})
    plazas = poligonos_plaza(osm, proj)
    rd = roads.build_roads(net, scene, blds_poly_pre, water_poly, urban_poly, log=log, osm_guardrails=has_gr,
                           ortho=orto, proj=proj, rails=rails,
                           plazas=unary_union([p for p, _ in plazas]) if plazas else None)
    roads.armonizar_calzadas(scene, log=log)
    keep_out = rd["ground_union"]          # sobre un túnel sí puede haber edificios
    if rd["sidewalks"] is not None and keep_out is not None:
        keep_out = unary_union([keep_out, rd["sidewalks"]])
    blds = B.drop_on_roads(blds, keep_out)
    # un edificio encima de un puente (huella del Catastro u OSM que cae bajo el tablero) salía plantado en medio
    # de la calzada del puente
    puentes = [p for p, e in rd["road_polys"] if e.bridge]
    if puentes:
        pu = unary_union(puentes)
        shapely.prepare(pu)
        antes = len(blds)
        blds = [b for b in blds if not shapely.intersects(pu, b["poly"])
                or b["poly"].intersection(pu).area < 0.2 * b["poly"].area]
        if len(blds) < antes:
            log(f"  {antes - len(blds)} edificios encima de puentes quitados")
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
    elif pedazo is None:
        _place_free(scene, net, log=log)

    # ------------------------------------------------------------ terreno
    log("5/7 Terreno: tallado y textura…")
    terr.build_grid()
    terr.flatten_urban(rd["carve"], urban_fn)
    terr.Z_pre = terr.Z.copy()          # relieve antes de tallar las calles (altura de las plazas)
    terr.carve(rd["carve"], rd["tunnel"])
    terr.bajo_calles(rd["carve"], unary_union([p for p, e in rd["road_polys"] if not (e.bridge or e.tunnel)])
                     if rd["road_polys"] else None)
    terr.cubrir_tuneles(rd.get("tunnel_poly"), rd.get("tunnel_bocas", []), rd["tunnel"], scene)
    sin_puente = [p for p, e in rd["road_polys"] if not e.bridge]
    agua = AG.Agua(cob, terr, unary_union(sin_puente) if sin_puente else None, log=log)
    agua.tallar()
    paths = []
    for w in osm["ways"]:
        # las veredas en túnel (pasillos y accesos del metro) van bajo tierra: no se pintan en la superficie
        if w["tags"].get("highway") in C.PATHS and w["tags"].get("tunnel") in (None, "no") \
                and int(float(w["tags"].get("layer", "0") or 0)) >= 0:
            x, y = proj.to_local([c[0] for c in w["coords"]], [c[1] for c in w["coords"]])
            paths.append((np.column_stack([x, y]), 1.3))
    paths += [(c, w) for c, w in cob.secos]     # cauces secos (ramblas, arroyos intermitentes)
    painter = T.GroundPainter(lc, proj, args.temporada, rd["road_polys"], paths, areas, blds_poly, water_poly, s2=s2,
                              ortho=orto)
    if pedazo is not None:
        terr.solo = pedazo
    terr.build_mesh(scene, painter, tile_m=args.tesela, mpp=mpp)
    plazas_peatonales(scene, plazas, terr, rd["road_polys"], blds_poly, log=log)
    agua.construir(scene)

    m = BR.build(scene, osm.get("barriers", []), proj, terr, rect, rd["road_union"])
    if m:
        log("  barreras de OSM: " + ", ".join(f"{k} {v:.0f} m" for k, v in m.items()))
    if pedazo is None:
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
    roads.seguir_realce(scene, log=log)
    log(f"  detalles: orillas en {ne} tramos, desgaste en {nw}, {nl} arbotantes, señales: "
        + ", ".join(f"{k} {v}" for k, v in sg.items()))
    excl = [rd["road_union"].buffer(2.0)] if rd["road_union"] is not None else []
    if rd["sidewalks"] is not None:
        excl.append(rd["sidewalks"].buffer(0.5))
    if blds_poly is not None:
        excl.append(blds_poly.buffer(1.5))
    if water_poly is not None:
        excl.append(water_poly)
    if rd.get("tunnel_poly") is not None:      # ni árboles sobre la tapa del túnel ni en sus bocas
        excl.append(rd["tunnel_poly"].buffer(2.0))
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
    if con_horizonte:
        try:
            dem_far = f_far_dem.result()
            s2_far = f_far_s2.result() if f_far_s2 else None
            nf = X.horizon(scene, proj, dem_far, s2_far, rect, terrain=terr)
            log(f"  horizonte: {nf:,} triángulos hasta 5 km")
        except Exception as e:  # noqa: BLE001
            log(f"  aviso: sin horizonte ({e})")

    if pedazo is not None:
        pool.shutdown(wait=False, cancel_futures=True)
        log(f"  pedazo listo en {time.time() - t0:.1f} s")
        return {"scene": scene, "proj": proj, "net": net, "terr": terr}

    # ------------------------------------------------------------ exportar
    log("7/7 Exportando…")
    os.makedirs(args.salida, exist_ok=True)
    name = args.nombre
    st = scene.stats()
    log(f"  escena: {st['mallas']} mallas, {st['vertices']:,} vértices, {st['triangulos']:,} triángulos, "
        f"{st['texturas']} texturas")
    prev = T.mosaic(terr.tiles, proj, 2.0)
    prev.save(os.path.join(args.salida, f"{name}_cenital.jpg"), quality=88)
    cob.panel(orto if orto is not None else s2, f"{name}: qué es cada sitio").save(
        os.path.join(args.salida, f"{name}_cobertura.jpg"), quality=88)
    extras = {"nombre": name, "centro": [proj.lat0, proj.lon0], "modo": args.modo}
    if route is not None:
        extras["ruta"] = export_glb.to_yup(route).round(2).tolist()
    jx = [(net.node_xy[n][0], net.node_xy[n][1], net.node_z[n], len(j["inc"])) for n, j in net.junctions.items() if net.node_inside(n) and not j.get("codo")]
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
        info = {"name": args.titulo or name, "lat": proj.lat0, "lon": proj.lon0, "epsg": proj.epsg,
                "attribution": ATTRIBUTION.format(
                    s2_years=f" ({', '.join(sorted(rasters.S2_YEARS))})" if rasters.S2_YEARS else "") + "".join("- " + a + chr(10) for a in R.attribution),
                "description": f"Generada con openlaps a partir de datos abiertos. {area_km2:.2f} km².",
                "tags": [t for t in (R.code, "openlaps", args.modo) if t], "length": length or rd["stats"]["km"] * 1000, "run": info_run,
                "pitboxes": 8, "preview_png": bio.getvalue(),
                "ai_hw": route_hw, "width": int(round(float(np.median(route_hw)) * 2)) if route_hw is not None else "",
                "closed": args.modo == "circuito"}
        export_ac.write_track(scene, os.path.join(args.salida, "assetto_corsa"), name, info, map_lines, route, log=log)
    with open(os.path.join(args.salida, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"area_km2": area_km2, "stats": st, "carreteras": rd["stats"], "cobertura": cob.stats,
                   "segundos": round(time.time() - t0, 1),
                   "centro": [proj.lat0, proj.lon0], "modo": args.modo}, f, ensure_ascii=False, indent=2)
    pool.shutdown(wait=False, cancel_futures=True)
    log(f"Listo en {time.time() - t0:.0f} s")
    return glb


def precargar(args, proj, log=print):
    """Modo en vivo: pide por adelantado los rásteres y huellas que `run` va a pedir para esta proyección, así el
    pedazo los encuentra en la caché de disco. Tiene que pedir exactamente lo mismo que el bloque de descargas
    de `run` (mismas cajas y argumentos): si cambia uno, cambia el otro. OSM y Catastro van aparte."""
    R = regions.set_active(regions.detect(proj.lat0, proj.lon0) if args.region == "auto" else args.region)
    bbox = proj.bbox()
    big = bbox.padded(450)
    tareas = []
    if not args.dem:
        if R.dem == "mdt05":
            tareas.append((rasters.mdt05, (big,), {}))
        elif R.dem == "3dep":
            tareas.append((rasters.dem_3dep, (big,), {}))
        else:
            tareas.append((rasters.copernicus_dem, (big,), {}))
    tareas.append((rasters.worldcover, (big,), {}))
    usar_orto = bool(R.ortho) and not args.sin_orto
    if usar_orto:
        tareas.append((rasters.ortho, (bbox.padded(60), R.ortho), {"mpp": R.ortho_mpp}))
    elif not args.sin_satelite:
        tareas.append((rasters.sentinel2, (bbox.padded(60), args.temporada), {}))
    if not args.sin_satelite:
        tareas.append((rasters.sentinel2_ndwi, (bbox.padded(60), args.temporada), {}))
    if not args.sin_dosel:
        tareas.append((rasters.canopy_height, (bbox,), {}))
    if not args.sin_microsoft:
        tareas.append((msbuildings.download, (bbox,), {}))
    if R.heights == "overture" and not getattr(args, "sin_overture", False):
        tareas.append((overture.download, (bbox,), {}))
    with ThreadPoolExecutor(max_workers=len(tareas)) as ex:
        futs = [ex.submit(fn, *a, log=lambda *m: None, **kw) for fn, a, kw in tareas]
        for f in futs:
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - el pedazo lo volverá a intentar y avisará
                log(f"  precarga: {e}")


def preparar_escena(args, log=print):
    """Escena vacía con todos los materiales y texturas registrados (no depende de la zona salvo por el perfil
    regional, que ya debe estar elegido). El modo en vivo la prepara una vez y la copia para cada pedazo."""
    scene = Scene()
    cc0_tex = {}
    if not args.sin_cc0:
        from .sources import cc0 as CC0
        cc0_tex = CC0.load_all(1024, log=log)
    textures.register_all(scene, args.temporada, cc0_tex)
    RS.register_materials(scene)
    return scene


# --------------------------------------------------------------------------------------
def poligonos_plaza(osm, proj):
    """Plazas y áreas peatonales (highway=pedestrian + area=yes) como (polígono, material)."""
    polis = []
    for a in osm["areas"]:
        t = a["tags"]
        if t.get("highway") != "pedestrian" and t.get("area:highway") != "pedestrian":
            continue
        x, y = proj.to_local([c[0] for c in a["outer"]], [c[1] for c in a["outer"]])
        huecos = []
        for r in a.get("inners", []):
            hx, hy = proj.to_local([c[0] for c in r], [c[1] for c in r])
            huecos.append(list(zip(hx, hy)))
        try:
            pg = Polygon(list(zip(x, y)), huecos).buffer(0)
        except Exception:  # noqa: BLE001
            continue
        if pg.is_empty:
            continue
        mat = C.SURFACE_MAP.get(t.get("surface"), "banqueta")
        if mat in ("asfalto", "terraceria"):
            mat = "banqueta"
        polis.append((pg, mat))
    return polis


def plazas_peatonales(scene, polis, terr, road_polys, blds_poly, log=print, paso=2.0):
    """Plazas y áreas peatonales: una losa pavimentada que sigue el relieve. Sin esto quedaban como terreno con la
    ortofoto, que de cerca parece un campo de tierra.

    - Triangulada con el contorno exacto (earcut): con Delaunay y descarte por centroide el borde salía en dientes.
    - Su altura es la del relieve antes de tallar las calles (`terr.Z_pre`): con la del terreno tallado se hundía
      junto a cada calle y la banqueta de al lado dejaba ver un hueco con su bordillo estirado.
    - Lleva un faldón de 0.6 m todo alrededor (tapa el hueco con el terreno o la calle de al lado) y su propia
      física (va unos centímetros por encima del terreno)."""
    from .roads import triangulate_polygon
    if not polis:
        return
    quitar = unary_union([p for p, e in road_polys if not (e.bridge or e.tunnel)]
                         + ([blds_poly.buffer(0.2)] if blds_poly is not None else []))
    Zp = getattr(terr, "Z_pre", None)

    def altura(x, y):
        if Zp is None:
            return terr.height(x, y)
        from scipy.ndimage import map_coordinates
        c = (np.asarray(x) - terr.gx[0]) / (terr.gx[1] - terr.gx[0])
        r = (np.asarray(y) - terr.gy[0]) / (terr.gy[1] - terr.gy[0])
        return np.maximum(map_coordinates(Zp, [r, c], order=1, mode="nearest"), terr.height(x, y))

    # el relieve no casa con la calle o la banqueta que toca la plaza (su perfil va suavizado): quedaba un escalón
    # de hasta 28 cm en el borde. Corrección = (altura de lo que toca - relieve) en el borde, que se desvanece hacia
    # dentro (a ~15 m ya casi no cuenta)
    # (el contorno de la losa de verdad: la plaza de OSM menos las calles, que es donde toca con ellas)
    contorno = unary_union([pg.difference(quitar).boundary for pg, _ in polis]).buffer(1.0)
    ref = [p.pos for k, ps in scene.pieces.items() if k[0] in ("calzada", "banqueta") and k[2] for p in ps]
    corr = None
    if ref:
        ref = np.vstack(ref)
        ref = ref[shapely.contains_xy(contorno, ref[:, 0], ref[:, 1])]
        if len(ref):
            from scipy.spatial import cKDTree
            delta = ref[:, 2] - (altura(ref[:, 0], ref[:, 1]) + 0.04)
            ok = np.abs(delta) < 1.5                      # un puente o un túnel cerca no cuenta
            ref, delta = ref[ok], delta[ok]
            if len(ref):
                arbol = cKDTree(ref[:, :2])

                def corr(x, y):
                    d, ii = arbol.query(np.c_[x, y], k=min(8, len(ref)), distance_upper_bound=30.0)
                    d, ii = np.atleast_2d(d), np.atleast_2d(ii)
                    w = np.where(np.isfinite(d), 1.0 / (d ** 2 + 0.5), 0.0)
                    dd = delta[np.minimum(ii, len(delta) - 1)]
                    c = (w * dd).sum(1) / (w.sum(1) + 1.0 / 225.0)
                    # pegada a una calle, nunca por debajo de ella (un borde que baja es un golpe al entrar)
                    pegado = np.where(d < 1.5, dd, -np.inf).max(1)
                    return np.maximum(c, pegado)

    def altura_plaza(x, y):
        z = altura(x, y) + 0.04
        if corr is None:
            return z
        # bajar hacia una calle más baja, sí, pero nunca por debajo del terreno (asomaba entre los triángulos)
        return np.maximum(z + corr(x, y), terr.height(x, y) + 0.04)

    n_tri, area = 0, 0.0
    for pg, mat in polis:
        g = pg.difference(quitar).buffer(-0.05).buffer(0.05)
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.area < 4.0:
                continue
            try:
                v, f = triangulate_polygon(p, paso)
            except Exception:  # noqa: BLE001
                continue
            if not len(f):
                continue
            # puntos interiores para que la losa siga el relieve (earcut solo usa el contorno)
            x0, y0, x1, y1 = p.bounds
            gx, gy = np.meshgrid(np.arange(x0 + paso / 2, x1, paso), np.arange(y0 + paso / 2, y1, paso))
            interior = shapely.contains_xy(p.buffer(-paso * 0.5), gx.ravel(), gy.ravel())
            if interior.any():
                from scipy.spatial import Delaunay
                pts = np.vstack([v, np.c_[gx.ravel()[interior], gy.ravel()[interior]]])
                tri = Delaunay(pts).simplices
                cen = pts[tri].mean(axis=1)
                tri = tri[shapely.contains_xy(p, cen[:, 0], cen[:, 1])]
                # si Delaunay se come parte del contorno (polígonos muy cóncavos), earcut tal cual
                if abs(_area_tris(pts, tri) - p.area) < 0.01 * p.area + 0.5:
                    v, f = pts, tri
            z = altura_plaza(v[:, 0], v[:, 1])
            pos = np.c_[v, z]
            cara = np.cross(pos[f[:, 1]] - pos[f[:, 0]], pos[f[:, 2]] - pos[f[:, 0]])
            f = f.copy()
            f[cara[:, 2] < 0] = f[cara[:, 2] < 0][:, ::-1]
            escala = 3.0 if mat == "banqueta" else 2.0
            scene.add("banqueta", mat, pos, v / escala, f, phys="PAVEMENT")
            # faldón: del borde de la losa 0.6 m hacia abajo
            for anillo in [p.exterior, *p.interiors]:
                c = np.asarray(shapely.segmentize(anillo, paso).coords)[:, :2]
                zc = altura_plaza(c[:, 0], c[:, 1])
                n = len(c)
                top = np.c_[c, zc]
                bot = np.c_[c, zc - 0.6]
                fp = np.vstack([top, bot])
                i = np.arange(n - 1)
                caras = np.concatenate([np.c_[i, i + n, i + 1], np.c_[i + 1, i + n, i + n + 1]])
                caras2 = caras[:, ::-1]
                d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(c, axis=0), axis=1))]
                uv = np.r_[np.c_[d / escala, np.zeros(n)], np.c_[d / escala, np.full(n, 0.6 / escala)]]
                scene.add("banqueta", mat, fp, uv, np.vstack([caras, caras2]))
            n_tri += len(f)
            area += p.area
    log(f"  plazas peatonales: {len(polis)} áreas, {area:,.0f} m² pavimentados ({n_tri:,} triángulos)")


def _area_tris(pts, tri):
    a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
    u, w = b - a, c - a
    return float(np.abs(u[:, 0] * w[:, 1] - u[:, 1] * w[:, 0]).sum() / 2)


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
    """Salidas del modo libre en un buen sitio: un tramo recto y largo de una calle importante, pero cerca del
    centro de la zona (antes ganaba el tramo más largo de toda el área y la salida podía quedar en un borde, en
    medio de la nada)."""
    best, score = None, -1e18
    for e in net.edges:
        if e.tunnel or e.bridge:  # una salida bajo tierra hace que AC deje el coche en la calle de encima
            continue
        for a, b in net.ranges(e):
            if b - a <= 40:
                continue
            m = e.at(np.array([(a + b) / 2]), 0.0)[0]
            centro = math.hypot(m[0], m[1])          # el origen local es el centro de la zona
            sc = min(b - a, 150.0) * (1 + e.prio) - centro * 0.35
            if sc > score:
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
