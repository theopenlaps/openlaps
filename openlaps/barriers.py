"""Barreras reales dibujadas en OpenStreetMap y límites del área.

Si OSM tiene la barrera, se usa su trazo exacto en vez de adivinarla por heurística:
- barrier=guard_rail: bionda metálica a 0.55-0.87 m con postes cada 2 m.
- barrier=wall / city_wall: muro de piedra (1.2 m; muralla 4 m).
- barrier=retaining_wall: muro de contención de concreto (1 m sobre el terreno).
- barrier=jersey_barrier: barrera de concreto de 0.8 m.
Todas con colisión. Además, un muro invisible en el borde del área, para que el coche no se
salga del terreno generado y atraviese el suelo.
"""
import math

import numpy as np
import shapely

from .roads import wall_strip

KINDS = {
    #                 alto  material   grosor
    "guard_rail":     (0.32, "defensa", 0.0),
    "wall":           (1.2, "muro_piedra", 0.45),
    "city_wall":      (4.0, "muro_piedra", 1.2),
    "retaining_wall": (1.0, "muro", 0.35),
    "jersey_barrier": (0.8, "muro", 0.5),
}


def _line_xyz(proj, coords, terrain, step):
    x, y = proj.to_local([c[0] for c in coords], [c[1] for c in coords])
    line = shapely.LineString(np.column_stack([x, y]))
    if line.length < 1.0:
        return None
    pts = np.asarray(shapely.segmentize(line, step).coords)[:, :2]
    z = terrain.height(pts[:, 0], pts[:, 1])
    return np.column_stack([pts, z])


def build(scene, barriers, proj, terrain, rect, road_union=None):
    """Devuelve cuántos metros de cada tipo se construyeron."""
    xmin, ymin, xmax, ymax = rect
    box = shapely.box(xmin + 1, ymin + 1, xmax - 1, ymax - 1)
    road = road_union.buffer(0.2) if road_union is not None else None
    meters = {}
    for b in barriers:
        kind = b["tags"].get("barrier")
        if kind not in KINDS:
            continue
        h, mat, thick = KINDS[kind]
        P = _line_xyz(proj, b["coords"], terrain, 1.0 if kind == "guard_rail" else 2.0)
        if P is None:
            continue
        # recortar al área y fuera de la calzada (OSM a veces dibuja la barrera encima)
        keep = shapely.contains_xy(box, P[:, 0], P[:, 1])
        if road is not None:
            keep &= ~shapely.contains_xy(road, P[:, 0], P[:, 1])
        runs, cur = [], []
        for ok, p in zip(keep, P):
            if ok:
                cur.append(p)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for run in runs:
            run = np.array(run)
            if len(run) < 2:
                continue
            L = float(np.sum(np.linalg.norm(np.diff(run[:, :2], axis=0), axis=1)))
            meters[kind] = meters.get(kind, 0.0) + L
            if kind == "guard_rail":
                from .roadside import bionda
                d = np.gradient(run[:, :2], axis=0)
                d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
                nrm = np.column_stack([-d[:, 1], d[:, 0]])
                # la onda mira hacia la calzada más cercana
                if road is not None:
                    probe = run[:, :2] + nrm * 1.5
                    if shapely.contains_xy(road, probe[:, 0], probe[:, 1]).mean() < 0.5:
                        nrm = -nrm
                bionda(scene, run, nrm)
            else:
                base = run.copy()
                base[:, 2] -= 0.5
                # dos caras (una por lado) separadas por el grosor, más la tapa
                d = np.gradient(run[:, :2], axis=0)
                d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
                nrm = np.column_stack([-d[:, 1], d[:, 0]])
                for side in (1, -1):
                    face = base.copy()
                    face[:, :2] += nrm * side * thick / 2
                    pts = face if side < 0 else face[::-1]
                    p, uv, f = wall_strip(pts, h + 0.5, u_scale=3, v_scale=3, both=False)
                    scene.add("barreras", mat, p, uv, f, phys="WALL")
                if thick > 0:
                    top_l = run.copy()
                    top_l[:, :2] += nrm * thick / 2
                    top_r = run.copy()
                    top_r[:, :2] -= nrm * thick / 2
                    top_l[:, 2] += h
                    top_r[:, 2] += h
                    n = len(run)
                    pos = np.vstack([top_l, top_r])
                    i = np.arange(n - 1)
                    faces = np.concatenate([np.column_stack([i, i + n, i + n + 1]), np.column_stack([i, i + n + 1, i + 1])])
                    tri = pos[faces]
                    up = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2]
                    faces[up < 0] = faces[up < 0][:, ::-1]
                    uv = np.column_stack([pos[:, 0] / 3, pos[:, 1] / 3])
                    scene.add("barreras", mat, pos, uv, faces)
    return meters


def boundary(scene, terrain, rect, zona=None, height=6.0, step=4.0):
    """Muro invisible (solo colisión) en el borde del área generada (o del corredor `zona` de un tramo largo)."""
    if zona is not None:
        import shapely
        for pg in (zona.geoms if hasattr(zona, "geoms") else [zona]):
            for anillo in [pg.exterior, *pg.interiors]:
                c = np.asarray(shapely.segmentize(anillo.simplify(1.0), step).coords)
                pts = np.column_stack([c, terrain.height(c[:, 0], c[:, 1]) - 2.0])
                p, uv, f = wall_strip(pts, height + 2.0, both=True)
                scene.add("col", "invisible", p, uv, f, phys="WALL")
        return
    xmin, ymin, xmax, ymax = rect
    corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
    for (x0, y0), (x1, y1) in zip(corners[:-1], corners[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        t = np.linspace(0, 1, max(2, int(L / step) + 1))
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        # medio metro hacia dentro, para que quede sobre el terreno detallado
        cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
        x += np.sign(cx - x) * 0.5 * (abs(x1 - x0) < 1e-6)
        y += np.sign(cy - y) * 0.5 * (abs(y1 - y0) < 1e-6)
        z = terrain.height(x, y) - 2.0
        pts = np.column_stack([x, y, z])
        p, uv, f = wall_strip(pts, height + 2.0, both=True)
        scene.add("col", "invisible", p, uv, f, phys="WALL")
