"""Vegetación según la ecología de México.

Especie por cobertura de suelo (WorldCover), altitud y latitud:
- Bosque > 2300 m: pino / oyamel; 1000-2300 m: encino con algo de pino; < 1000 m: selva
  (tropical) y palmas cerca de la costa.
- Matorral: en el norte árido y el altiplano, cactus órgano, magueyes y arbustos.
- Pastizal y cultivo: árboles sueltos y magueyes en las orillas.
- Zona urbana: árboles de patio y algunas palmas.
Los puntos salen de una rejilla con desplazamiento aleatorio (parecido a Poisson) y se
descartan los que caen en calles, banquetas, edificios o agua.
"""
import math

import numpy as np
import shapely

from . import config as C

SPECIES = {  # altura media (m), ancho relativo
    "encino": (9.0, 0.95), "tropical": (11.0, 1.0), "pino": (17.0, 0.55), "palma": (10.0, 0.75),
    "organo": (4.5, 0.6), "maguey": (1.6, 1.3), "arbusto": (2.2, 1.2), "olivo": (4.2, 1.35),
}


def _choose(cls, elev, lat, r):
    from .regions import map_species, active
    k = _choose_mx(cls, elev, lat, r) if active().code == "mx" else _choose_eu(cls, elev, lat, r)
    # fuera de México las palmas solo en el sur (Andalucía, Levante, Canarias)
    if k == "palma" and active().code != "mx" and lat > 38.6:
        k = "encino"
    return map_species(k)


def _choose_eu(cls, elev, lat, r):
    """Europa mediterránea / atlántica: pino y encina (o roble) en el monte, plátanos de sombra
    y otros caducifolios en la ciudad, matorral bajo; palmas solo en el sur (ver _choose)."""
    if cls in (C.WC_TREE, C.WC_MANGROVE):
        if elev > 1400:
            return "pino" if r < 0.8 else "encino"
        return "pino" if r < 0.4 else ("encino" if r < 0.85 else "tropical")
    if cls == C.WC_SHRUB:
        return "arbusto" if r < 0.8 else "encino"
    if cls in (C.WC_GRASS, C.WC_CROP):
        # árboles sueltos en campos: olivos en el centro y sur, encinas más al norte
        if lat < 41.8:
            return "olivo" if r < 0.75 else "encino"
        return "encino" if r < 0.7 else "arbusto"
    if cls == C.WC_BUILT:
        return "tropical" if r < 0.55 else ("encino" if r < 0.85 else ("palma" if lat < 38.6 else "pino"))
    return "arbusto"


def _choose_mx(cls, elev, lat, r):
    arid = lat > 22.0 or elev > 1700
    if cls == C.WC_TREE or cls == C.WC_MANGROVE:
        if elev > 2300:
            return "pino" if r < 0.75 else "encino"
        if elev > 1000:
            return "encino" if r < 0.7 else "pino"
        return "palma" if (elev < 150 and r < 0.25) else "tropical"
    if cls == C.WC_SHRUB:
        if arid:
            return "arbusto" if r < 0.45 else ("maguey" if r < 0.75 else "organo")
        return "arbusto" if r < 0.75 else "tropical"
    if cls in (C.WC_GRASS, C.WC_CROP):
        if r < 0.35:
            return "maguey" if arid else "arbusto"
        return "encino" if elev > 1000 else "tropical"
    if cls == C.WC_BUILT:
        return "palma" if r < 0.25 else ("encino" if elev > 1000 else "tropical")
    return "arbusto"


# separación media entre plantas (m) por clase; None = nada
SPACING = {C.WC_TREE: 6.5, C.WC_MANGROVE: 6.0, C.WC_SHRUB: 7.5, C.WC_GRASS: 22.0,
           C.WC_CROP: 35.0, C.WC_BUILT: 26.0, C.WC_WETLAND: 14.0, C.WC_BARE: 40.0}


def trees_from_canopy(chm, proj, step=1.5):
    """Copas individuales = máximos locales de la altura de dosel suavizada.

    Ventana de búsqueda de ~7.5 m (5 px a 1.5 m); se descartan copas de menos de 3 m. Los
    arbustos (1-3 m) se toman por muestreo disperso. Devuelve (x, y, altura) de cada planta.
    """
    from scipy.ndimage import gaussian_filter, maximum_filter
    xs = np.arange(proj.xmin + 1, proj.xmax - 1, step)
    ys = np.arange(proj.ymin + 1, proj.ymax - 1, step)
    GX, GY = np.meshgrid(xs, ys)
    H = chm.sample(proj, GX, GY, order=1)
    Hs = gaussian_filter(H, 1.0)
    # copas: máximos locales de más de 3.5 m; en campo abierto (cultivo/pasto) también desde
    # 2.5 m, para no perder olivos y árboles sueltos que el suavizado del modelo rebaja
    peaks = (Hs == maximum_filter(Hs, size=3)) & (Hs > 2.5)
    peaks &= (Hs > 3.5) | (maximum_filter(Hs, size=7) == Hs)
    rng = np.random.default_rng(11)
    shrub = (Hs > 1.0) & (Hs <= 3.0) & (rng.random(Hs.shape) < 0.035)
    # relleno del dosel cerrado: el modelo de 1 m suaviza las copas juntas y quedan pocos
    # máximos; se añaden plantas con una densidad acorde al tamaño de copa (~0.3 H + 2 m)
    crown = 0.3 * Hs + 2.0
    p_fill = np.clip((step / crown) ** 2 * 0.9, 0, 1) * (Hs > 5.0)
    near_peak = maximum_filter(peaks.astype(np.uint8), size=3) > 0
    fill = (rng.random(Hs.shape) < p_fill) & ~near_peak
    sel = peaks | shrub | fill
    jx = rng.uniform(-0.5, 0.5, sel.sum()) * step
    jy = rng.uniform(-0.5, 0.5, sel.sum()) * step
    return np.column_stack([GX[sel] + jx, GY[sel] + jy, Hs[sel]])


def build(scene, terrain, landcover, proj, exclude, osm_trees, density=1.0, road_pts=None, chm=None, log=print):
    rng = np.random.default_rng(42)
    p = proj
    if chm is not None:
        return _build_from_canopy(scene, terrain, landcover, proj, exclude, osm_trees, density, road_pts, chm, log)
    base = 6.0 / math.sqrt(max(density, 0.05))
    xs = np.arange(p.xmin + 2, p.xmax - 2, base)
    ys = np.arange(p.ymin + 2, p.ymax - 2, base)
    GX, GY = np.meshgrid(xs, ys)
    X = GX.ravel() + rng.uniform(-0.45, 0.45, GX.size) * base
    Y = GY.ravel() + rng.uniform(-0.45, 0.45, GY.size) * base
    cls = landcover.sample(p, X, Y, order=0).astype(int) if landcover is not None else np.full(X.shape, C.WC_GRASS)
    sp = np.array([SPACING.get(c, 0) or 0 for c in cls], float) / math.sqrt(max(density, 0.05))
    prob = np.where(sp > 0, (base / np.maximum(sp, 1e-6)) ** 2, 0.0)
    if road_pts is not None and len(road_pts):
        # aclareo por distancia a la calle: denso donde se ve, ralo al fondo
        from scipy.spatial import cKDTree
        dist, _ = cKDTree(road_pts[::3]).query(np.column_stack([X, Y]), distance_upper_bound=2000)
        dist = np.where(np.isfinite(dist), dist, 2000)
        prob = prob * np.clip(1.0 - (dist - 120.0) / 350.0, 0.22, 1.0)
    keep = rng.random(X.size) < prob
    X, Y, cls = X[keep], Y[keep], cls[keep]
    if exclude is not None and len(X):
        bad = shapely.contains_xy(exclude, X, Y)
        X, Y, cls = X[~bad], Y[~bad], cls[~bad]
    # árboles sueltos mapeados en OSM
    if osm_trees is not None and len(osm_trees):
        X = np.concatenate([X, osm_trees[:, 0]])
        Y = np.concatenate([Y, osm_trees[:, 1]])
        cls = np.concatenate([cls, np.full(len(osm_trees), C.WC_BUILT)])
    if len(X) == 0:
        log("  vegetación: nada")
        return
    Z = terrain.height(X, Y)
    lat = p.lat0
    counts = {}
    groups = {}
    for x, y, z, c in zip(X, Y, Z, cls):
        r = rng.random()
        kind = _choose(int(c), float(z), lat, r)
        groups.setdefault(kind, []).append((x, y, z))
        counts[kind] = counts.get(kind, 0) + 1
    for kind, pts in groups.items():
        pts = np.array(pts)
        hmean, wrel = SPECIES[kind]
        H = hmean * rng.uniform(0.7, 1.3, len(pts))
        _emit(scene, kind, pts, H, rng, wd=H * wrel)
    log("  vegetación: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))


def _build_from_canopy(scene, terrain, landcover, proj, exclude, osm_trees, density, road_pts, chm, log):
    rng = np.random.default_rng(42)
    T = trees_from_canopy(chm, proj)
    if osm_trees is not None and len(osm_trees):
        T = np.vstack([T, np.column_stack([osm_trees, np.full(len(osm_trees), 8.0)])])
    X, Y, Hc = T[:, 0], T[:, 1], T[:, 2]
    keep = np.ones(len(X), bool)
    if exclude is not None and len(X):
        keep &= ~shapely.contains_xy(exclude, X, Y)
    dist = np.full(len(X), 0.0)
    if road_pts is not None and len(road_pts):
        from scipy.spatial import cKDTree
        dist, _ = cKDTree(road_pts[::3]).query(np.column_stack([X, Y]), distance_upper_bound=3000)
        dist = np.where(np.isfinite(dist), dist, 3000)
        prob = np.clip(1.0 - (dist - 150.0) / 400.0, 0.25, 1.0) * min(1.0, density)
        keep &= rng.random(len(X)) < prob
    X, Y, Hc, dist = X[keep], Y[keep], Hc[keep], dist[keep]
    cls = landcover.sample(proj, X, Y, order=0).astype(int) if landcover is not None else np.full(len(X), C.WC_TREE)
    Z = terrain.height(X, Y)
    groups, counts = {}, {}
    for x, y, z, h, c in zip(X, Y, Z, Hc, cls):
        r = rng.random()
        if h <= 3.0:
            open_land = c in (C.WC_SHRUB, C.WC_GRASS, C.WC_BARE, C.WC_CROP)
            from .regions import map_species
            kind = map_species("maguey" if open_land and (proj.lat0 > 22 or z > 1700) and r < 0.4 else "arbusto")
        else:
            kind = _choose(C.WC_TREE if c in (C.WC_BUILT, C.WC_GRASS, C.WC_CROP, C.WC_SHRUB) and h > 6 else int(c), float(z), proj.lat0, r)
            if kind in ("maguey", "arbusto") and h > 5:
                kind = "encino" if z > 1000 else "tropical"
        groups.setdefault(kind, []).append((x, y, z, h))
        counts[kind] = counts.get(kind, 0) + 1
    for kind, pts in groups.items():
        pts = np.array(pts)
        _emit(scene, kind, pts[:, :3], pts[:, 3] * rng.uniform(0.92, 1.08, len(pts)), rng)
    log("  vegetación (altura de dosel real): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    # troncos con colisión cerca de las calles (te puedes estrellar con un árbol)
    near = (dist < 25) & (Hc > 4.0)
    return np.column_stack([X[near], Y[near], Z[near]])


def _emit(scene, kind, pts, H, rng, wd=None):
    """Planos cruzados con el sprite de la especie (variante al azar por planta).

    Árboles grandes: 3 planos a 60°, para que se vean llenos desde cualquier ángulo; plantas
    bajas: 2. Las normales apuntan hacia afuera del eje del árbol mezcladas con "arriba", así la
    copa tiene un lado iluminado y otro en sombra en lugar de un color plano.
    """
    from .textures import VEG_VARIANTS
    hmean, wrel = SPECIES[kind]
    n = len(pts)
    if n == 0:
        return
    pts = np.asarray(pts, float)
    H = np.asarray(H, float)
    if wd is None:
        Wd = H * wrel * (0.55 if kind in ("encino", "tropical") else 1.0) + (2.0 if kind in ("encino", "tropical") else 0.0)
    else:
        Wd = np.asarray(wd, float)
    big = kind in ("encino", "tropical", "pino", "palma")
    angles = (0.0, math.pi / 3, 2 * math.pi / 3) if big else (0.0, math.pi / 2)
    yaw = rng.uniform(0, math.pi, n)
    variant = rng.integers(0, VEG_VARIANTS, n)
    nq = len(angles) * 2
    pos = np.zeros((n, nq, 4, 3))
    uv = np.zeros((n, nq, 4, 2))
    nrm = np.zeros((n, nq, 4, 3))
    for k, ang in enumerate(angles):
        c, s_ = np.cos(yaw + ang), np.sin(yaw + ang)
        dx, dy = c * Wd / 2, s_ * Wd / 2
        for face in range(2):
            q = k * 2 + face
            sign = 1 if face == 0 else -1
            xs4 = np.stack([pts[:, 0] - dx * sign, pts[:, 0] + dx * sign, pts[:, 0] + dx * sign, pts[:, 0] - dx * sign], 1)
            ys4 = np.stack([pts[:, 1] - dy * sign, pts[:, 1] + dy * sign, pts[:, 1] + dy * sign, pts[:, 1] - dy * sign], 1)
            zs4 = np.stack([pts[:, 2] - 0.2, pts[:, 2] - 0.2, pts[:, 2] + H, pts[:, 2] + H], 1)
            pos[:, q] = np.stack([xs4, ys4, zs4], -1)
            uv[:, q] = np.array([(0, 1), (1, 1), (1, 0), (0, 0)])
            # normal: hacia afuera (izquierda/derecha del plano) + arriba (más arriba en la punta)
            ox = np.stack([-c * sign, c * sign, c * sign, -c * sign], 1)
            oy = np.stack([-s_ * sign, s_ * sign, s_ * sign, -s_ * sign], 1)
            up = np.array([0.45, 0.45, 1.0, 1.0])[None, :].repeat(n, 0)
            v = np.stack([ox * 0.75, oy * 0.75, up], -1)
            nrm[:, q] = v / np.linalg.norm(v, axis=-1, keepdims=True)
    quad = np.array([[0, 1, 2], [0, 2, 3]])
    for vi in range(VEG_VARIANTS):
        sel = variant == vi
        m = int(sel.sum())
        if m == 0:
            continue
        P = pos[sel].reshape(-1, 3)
        U = uv[sel].reshape(-1, 2)
        N = nrm[sel].reshape(-1, 3)
        base_i = np.arange(m * nq)[:, None] * 4
        faces = (base_i[:, None, :] + quad[None, :, :]).reshape(-1, 3)
        scene.add("vegetacion", f"veg_{kind}_{vi}", P, U, faces, normals=N)
