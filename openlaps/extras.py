"""Detalles que hacen que una pista generada se sienta "hecha a mano".

- Orillas: franja de terracería semitransparente que funde el asfalto con el terreno.
- Desgaste: huellas de rodada más oscuras en cada carril.
- Arbotantes (postes de luz) en calles urbanas, con su luz para la noche (CSP).
- Señalamiento vertical NOM-034: ALTO (SR-6) en las calles secundarias que llegan a una
  principal, velocidad máxima (SR-9) y curva peligrosa (SP-6/SP-7) antes de curvas cerradas.
- Colisiones invisibles para troncos, postes y señales.
- Cámaras de TV a lo largo del recorrido.
- Horizonte: relieve lejano de baja resolución con imagen satelital, hasta ~5 km.
"""
import math

import numpy as np

from . import config as C
from . import textures
from .roads import strip, box_mesh, _svals, _clean_runs, DZ_DESGASTE


def _yaw(e, s):
    t = e.frame(s)
    return math.atan2(t[1], t[0])


# ------------------------------------------------------------------ orillas y desgaste
def regions_active():
    from .regions import active
    return active()


def edges_and_wear(net, scene, terrain=None, road_union=None):
    """Orillas difuminadas y huellas de rodada.

    La orilla nace pegada a la calzada y su borde exterior se apoya sobre el terreno ya tallado
    (más un margen), en lugar de seguir el plano de la calle: si siguiera el plano, el talud lo
    cruzaría y las dos superficies parpadearían donde se cortan.
    """
    n_edge = n_wear = 0
    import shapely
    other = road_union.buffer(-0.15) if road_union is not None else None
    road_any = road_union.buffer(0.05) if road_union is not None else None
    if other is not None:
        shapely.prepare(other)
        shapely.prepare(road_any)
    for e in net.edges:
        if e.tunnel:
            continue
        for a, b in net.ranges(e):
            sv = _svals(e, a, b)
            if len(sv) < 2:
                continue
            if not e.sidewalk and not e.bridge and e.mat in ("asfalto", "concreto", "empedrado", "adoquin"):
                w = (0.6 if regions_active().code != "mx" else 1.1) if not e.urban else 0.6
                for offs, outer, us, u0 in (([e.hw, e.hw + w], 1, w, e.hw), ([-e.hw - w, -e.hw], 0, -w, -e.hw)):
                    p, uv, f = strip(e, sv, offs, dz=-0.01, u_scale=us, v_scale=4.0, u0=u0)
                    k = np.arange(outer, len(p), 2)
                    if terrain is not None:
                        zt = terrain.height(p[k, 0], p[k, 1]) + 0.06
                        p[k, 2] = np.maximum(p[k, 2] - 0.05, zt)
                    if other is not None:
                        # fuera los trozos de orilla que caen sobre otra calzada (en cruces y
                        # ramales se veían como franjas de tierra encima del asfalto): se prueba
                        # el vértice exterior y también el centro de cada triángulo
                        bad_v = np.zeros(len(p), bool)
                        bad_v[k] = shapely.contains_xy(other, p[k, 0], p[k, 1])
                        cen = p[f].mean(1)
                        bad_f = shapely.contains_xy(road_any, cen[:, 0], cen[:, 1])
                        f = f[~(bad_v[f].any(axis=1) | bad_f)]
                        if len(f) == 0:
                            continue
                    scene.add("bordes", "orilla", p, uv, f)
                n_edge += 1
            if e.mat == "asfalto" and e.hw >= 2.0:
                n_wear += _asphalt_marks(scene, e, a, b)
            if False:   # rodaduras: ahora van en la textura macro
                carriage = min(e.lanes * e.lane_w, 2 * e.hw)
                half = carriage / 2
                for k in range(e.lanes):
                    c = -half + (k + 0.5) * (carriage / e.lanes)
                    for d in (-0.85, 0.85):
                        o = c + d
                        p, uv, f = strip(e, sv, [o - 0.35, o + 0.35], dz=DZ_DESGASTE, u_scale=0.7, v_scale=8.0, u0=o - 0.35)
                        scene.add("desgaste", "desgaste", p, uv, f)
                n_wear += 1
    return n_edge, n_wear


def _asphalt_marks(scene, e, a, b):
    """Grietas y bacheos sueltos: separación, posición, tamaño, giro y forma al azar (semilla por
    calle), pocos y tenues. Calcomanía semitransparente sobre la calzada, bajo las rayas."""
    rng = np.random.default_rng(e.id * 7919 + 13)
    s = a + rng.uniform(5, 30)
    pos, uv, faces = [], [], []
    while s < b - 3:
        cell = int(rng.integers(0, 8))
        L = rng.uniform(1.2, 3.2) if cell < 6 else rng.uniform(1.0, 2.2)
        W = L * (rng.uniform(0.5, 1.0) if cell < 6 else rng.uniform(0.7, 1.2))
        o = rng.uniform(-e.hw + 0.4 + W / 2, e.hw - 0.4 - W / 2) if e.hw * 2 > W + 1 else 0.0
        rot = rng.normal(0, 0.25)
        cu, cv = (cell % 4) / 4.0, (cell // 4) / 2.0
        corners = []
        for du, dv in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
            ls, lo = dv * L, du * W
            rs = ls * math.cos(rot) - lo * math.sin(rot)
            ro = ls * math.sin(rot) + lo * math.cos(rot)
            corners.append(e.at(s + rs, o + ro) + np.array([0, 0, DZ_DESGASTE]))
        base = len(pos)
        pos += corners
        uv += [(cu, cv + 0.5), (cu + 0.25, cv + 0.5), (cu + 0.25, cv), (cu, cv)]
        faces += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
        s += rng.uniform(14, 55)
    if not faces:
        return 0
    pos, faces = np.array(pos), np.array(faces)
    tri = pos[faces]
    up = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2]
    faces[up < 0] = faces[up < 0][:, ::-1]
    scene.add("desgaste", "asfalto_marcas", pos, np.array(uv, float), faces)
    return 1


# ------------------------------------------------------------------ arbotantes
def street_lamps(net, scene, spacing=32.0, max_lamps=700):
    n = 0
    for e in net.edges:
        if not e.sidewalk or e.bridge or e.tunnel:
            continue
        side = 1 if (e.id % 2) else -1
        for a, b in net.ranges(e, e.s0 + 4, e.s1 - 4):
            for s in np.arange(a + spacing / 2, b, spacing):
                if n >= max_lamps:
                    return n
                o = side * (e.hw + 0.45)
                base = e.at(s, o)
                yaw = _yaw(e, s)
                p, uv, f = box_mesh((base[0], base[1], base[2] - 0.3), (0.16, 0.16, 8.3), yaw)
                scene.add("mobiliario", "poste", p, uv, f)
                p, uv, f = box_mesh((base[0], base[1], base[2] - 0.3), (0.35, 0.35, 2.4), yaw)
                scene.add("col", "invisible", p, uv, f, phys="WALL")
                arm = e.at(s, o - side * 1.6)
                mid = (base + arm) / 2
                p, uv, f = box_mesh((mid[0], mid[1], base[2] + 7.8), (0.1, 1.7, 0.1), yaw)
                scene.add("mobiliario", "poste", p, uv, f)
                p, uv, f = box_mesh((arm[0], arm[1], base[2] + 7.62), (0.35, 0.65, 0.18), yaw)
                scene.add("mobiliario", "lampara", p, uv, f)
                scene.lights.append(((arm[0], arm[1], base[2] + 7.55), (0, 0, -1), (255, 214, 170), 6, 28))
                n += 1
            side = -side
    return n


# ------------------------------------------------------------------ señales
def _sign(scene, e, s, side, toward, mat, h=2.0, size=0.75, travel_sign=1, aspect=1.0, post=True, extra_o=0.9):
    """Señal en la abscisa s, del lado `side` (+ izquierda del eje), mirando al conductor.

    aspect: ancho/alto de la placa (carteles rectangulares); post=False para una segunda placa
    en el mismo poste (sin repetir poste ni colisión, que parpadearían)."""
    o = side * (e.hw + extra_o)
    base = e.at(s, o)
    t = e.frame(s) * travel_sign            # dirección en que circula quien la debe ver
    yaw = math.atan2(t[1], t[0])
    if post:
        p, uv, f = box_mesh((base[0], base[1], base[2] - 0.4), (0.07, 0.07, h + 0.4 + size * 0.5), yaw)
        scene.add("senales", "poste", p, uv, f)
        p, uv, f = box_mesh((base[0], base[1], base[2] - 0.4), (0.3, 0.3, 2.4), yaw)
        scene.add("col", "invisible", p, uv, f, phys="WALL")
    nx, ny = -t[1], t[0]                    # izquierda del conductor
    c = np.array([base[0] - t[0] * 0.06, base[1] - t[1] * 0.06, base[2] + h])
    hs = size / 2
    ws = hs * aspect
    a = c + [-nx * ws, -ny * ws, -hs]       # abajo a la derecha del conductor
    b = c + [nx * ws, ny * ws, -hs]
    q = c + [nx * ws, ny * ws, hs]
    d = c + [-nx * ws, -ny * ws, hs]
    pos = np.array([b, a, d, q])            # cara hacia -t (hacia el conductor que viene)
    uv = np.array([(0, 1), (1, 1), (1, 0), (0, 0)])
    faces = np.array([(0, 1, 2), (0, 2, 3)])
    nrm = np.tile([-t[0], -t[1], 0.0], (4, 1))
    scene.add("senales", mat, pos, uv, faces, normals=nrm)
    back = pos[[1, 0, 3, 2]] + np.array([t[0], t[1], 0]) * 0.01
    scene.add("senales", "poste", back, uv, faces, normals=-nrm)


def signs(net, scene):
    counts = {"alto": 0, "curva": 0, "velocidad": 0}
    # ALTO en las calles secundarias que llegan a una de mayor jerarquía
    for nid, j in net.junctions.items():
        if not net.node_inside(nid) or j.get("codo"):
            continue
        top = max(e.prio for e, _ in j["inc"])
        for e, end in j["inc"]:
            if top - e.prio < 2 or e.length < 20:
                continue
            if e.oneway and end == 0:
                continue  # el tráfico sale del cruce por esta calle
            s = (e.s0 + 1.5) if end == 0 else (e.s1 - 1.5)
            if not any(a <= s <= b for a, b in e.runs):
                continue
            # quien llega al nodo circula en -T si el nodo está al inicio; su derecha es +N
            side = 1 if end == 0 else -1
            _sign(scene, e, s, side, None, "senal_alto", travel_sign=-1 if end == 0 else 1)
            counts["alto"] += 1
    # curvas peligrosas y velocidad en carretera
    for e in net.edges:
        if e.urban or e.cls not in C.RURAL_CLASSES or e.length < 120:
            continue
        h = np.unwrap(np.arctan2(e.T[:, 1], e.T[:, 0]))
        from scipy.ndimage import gaussian_filter1d
        kap = gaussian_filter1d(np.gradient(h, e.s), 6, mode="nearest")
        sharp = _clean_runs(np.abs(kap) > 1 / 120.0, e.s, 15.0, 30.0)
        i = 0
        while i < len(sharp):
            if not sharp[i]:
                i += 1
                continue
            j = i
            while j < len(sharp) and sharp[j]:
                j += 1
            s_in, s_out = e.s[i], e.s[j - 1]
            left = np.mean(kap[i:j]) > 0
            # velocidad aconsejada en la curva (radio mínimo): si es menor que la de la vía, va
            # debajo de la señal de curva junto con la placa de distancia, como en la real
            Rmin = 1.0 / max(float(np.max(np.abs(kap[i:j]))), 1e-4)
            v_adv = int(min(e.speed, max(30, round(math.sqrt(Rmin * 127 * 0.22) / 10) * 10)))
            dist = 150 if e.length > 400 else 70
            for sp, side, ts, mat in ((s_in - dist, -1, 1, "senal_curva_izq" if left else "senal_curva_der"),
                                      (s_out + dist, 1, -1, "senal_curva_der" if left else "senal_curva_izq")):
                if ts == -1 and e.oneway:
                    continue
                if not any(a <= sp <= b for a, b in net.ranges(e)):
                    continue
                if v_adv < e.speed:
                    _sign(scene, e, sp, side, None, mat, h=2.45, travel_sign=ts)
                    _sign(scene, e, sp, side, None, textures.speed_material(scene, v_adv), h=1.72, size=0.6,
                          travel_sign=ts, post=False)
                    _sign(scene, e, sp, side, None, textures.plate_material(scene, f"{dist} m"), h=1.28, size=0.2,
                          aspect=2.4, travel_sign=ts, post=False)
                else:
                    _sign(scene, e, sp, side, None, mat, travel_sign=ts)
                counts["curva"] += 1
            i = j
        if e.length > 400 and not e.tags.get("maxspeed"):
            v = int(round(e.speed / 10.0) * 10)
            mat = textures.speed_material(scene, v)
            for sp, side, ts in ((45.0, -1, 1), (e.length - 45.0, 1, -1)):
                if ts == -1 and e.oneway:
                    continue
                if any(a <= sp <= b for a, b in net.ranges(e)):
                    _sign(scene, e, sp, side, None, mat, travel_sign=ts)
                    counts["velocidad"] += 1
    # límite de velocidad real (etiqueta maxspeed de OSM) al principio de cada calle, en ambos
    # sentidos; sin etiqueta no se inventa nada
    inc = net.incident()
    for e in net.edges:
        ms = _num_speed(e.tags.get("maxspeed"))
        if ms is None or e.length < 60:
            continue
        mat = textures.speed_material(scene, ms)
        for sp, side, ts, nid in ((e.s0 + 12.0, -1, 1, e.a), (e.s1 - 12.0, 1, -1, e.b)):
            if ts == -1 and e.oneway:
                continue
            # solo donde el límite cambia: si otra calle del mismo nodo ya tiene ese límite, es
            # continuación de la misma vía y la señal sobra
            others = [_num_speed(o.tags.get("maxspeed")) for o, _ in inc.get(nid, []) if o is not e]
            if ms in others:
                continue
            if any(a <= sp <= b for a, b in net.ranges(e)):
                _sign(scene, e, sp, side, None, mat, travel_sign=ts)
                counts["velocidad"] += 1
    return counts


def _num_speed(v):
    if not v:
        return None
    import re
    m = re.match(r"\s*(\d+)\s*(mph)?", str(v))
    if not m:
        return None
    n = int(m.group(1))
    if m.group(2):
        n = int(round(n * 1.609344))     # exacto: la señal en mph se vuelve a calcular de aquí
    return n if 10 <= n <= 130 else None


# ------------------------------------------------------------------ colisiones de troncos
def trunk_colliders(scene, pts):
    for x, y, z in pts:
        p, uv, f = box_mesh((x, y, z - 0.5), (0.5, 0.5, 4.0))
        scene.add("col", "invisible", p, uv, f, phys="WALL")
    return len(pts)


# ------------------------------------------------------------------ cámaras de TV
def tv_cameras(scene, route, spacing=220.0):
    if route is None or len(route) < 3:
        return 0
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(route[:, :2], axis=0), axis=1))])
    L = s[-1]
    marks = np.arange(0, L, spacing)
    for k, sm in enumerate(marks):
        i = int(np.searchsorted(s, sm))
        i = min(i, len(route) - 1)
        j = int(np.searchsorted(s, min(L, sm + spacing * 0.5)))
        j = min(j, len(route) - 1)
        t = route[min(len(route) - 1, i + 3), :2] - route[max(0, i - 3), :2]
        t /= max(np.linalg.norm(t), 1e-6)
        side = 1 if k % 2 == 0 else -1
        cam = route[i] + np.array([-t[1] * 14 * side, t[0] * 14 * side, 6.0])
        look = route[j]
        scene.cameras.append((cam, look, sm / L, min(1.0, (sm + spacing) / L)))
    return len(marks)


# ------------------------------------------------------------------ horizonte
def horizon(scene, proj, dem_far, img_far, rect, far_m=5000.0, step=40.0, terrain=None):
    """Relieve lejano (sin física) alrededor del área, con textura satelital o de cobertura.

    El horizonte nunca debe asomar por encima del terreno detallado: el MDE lejano es de
    superficie (incluye árboles y techos) y el detallado ya está corregido a terreno y tallado,
    así que en la franja donde se enciman el horizonte se pone claramente por debajo.
    """
    from PIL import Image
    xmin, ymin, xmax, ymax = rect
    X0, Y0, X1, Y1 = xmin - far_m, ymin - far_m, xmax + far_m, ymax + far_m
    xs = np.arange(X0, X1 + step, step)
    ys = np.arange(Y0, Y1 + step, step)
    GX, GY = np.meshgrid(xs, ys)
    Z = dem_far.sample(proj, GX, GY, order=1)
    from scipy.ndimage import gaussian_filter
    Z = gaussian_filter(Z, 0.7)
    Z = Z - 1.5
    inner = (GX > xmin) & (GX < xmax) & (GY > ymin) & (GY < ymax)
    if terrain is not None:
        # cerca del área: transición al terreno corregido (mismo nivel que la orilla del detallado)
        dx = np.maximum(np.maximum(xmin - GX, GX - xmax), 0)
        dy = np.maximum(np.maximum(ymin - GY, GY - ymax), 0)
        dout = np.hypot(dx, dy)
        near = dout < 280.0
        if near.any():
            gx, gy = GX[near], GY[near]
            # el terreno detallado del borde no es el relieve original (aplanado urbano, tallado de
            # calles): esa diferencia se mide en el punto de borde más cercano y se propaga hacia
            # fuera desvaneciéndose en 150 m, para que el horizonte empalme sin zanja ni escalón
            cx, cy = np.clip(gx, xmin + 0.5, xmax - 0.5), np.clip(gy, ymin + 0.5, ymax - 0.5)
            dev = terrain.height(cx, cy) - terrain.z(cx, cy)
            fall = np.clip(1 - dout[near] / 150.0, 0, 1) ** 2
            zt = terrain.z(gx, gy) + dev * fall - 0.6
            t = np.clip(dout[near] / 280.0, 0, 1)
            t = t * t * (3 - 2 * t)
            Z[near] = zt * (1 - t) + Z[near] * t
        # dentro del área: 1 m bajo el terreno final, siguiéndolo. Antes estos vértices se hundían
        # 4-25 m y las celdas que cruzaban el borde formaban una zanja de laderas casi verticales,
        # en sombra: de lejos se veía como una franja negra en el horizonte.
        if inner.any():
            Z[inner] = terrain.height(GX[inner], GY[inner]) - 1.0
    # las celdas completamente dentro del área (con un paso de margen) se descartan abajo
    inside = (GX > xmin + step) & (GX < xmax - step) & (GY > ymin + step) & (GY < ymax - step)
    pos = np.column_stack([GX.ravel(), GY.ravel(), Z.ravel()])
    uv = np.column_stack([(GX.ravel() - X0) / (X1 - X0), (Y1 - GY.ravel()) / (Y1 - Y0)])
    h, w = Z.shape
    idx = np.arange(w * h).reshape(h, w)
    a, b = idx[:-1, :-1], idx[:-1, 1:]
    c, d = idx[1:, 1:], idx[1:, :-1]
    cell_in = inside[:-1, :-1] & inside[1:, 1:] & inside[:-1, 1:] & inside[1:, :-1]
    keep = ~cell_in
    faces = np.concatenate([np.column_stack([a[keep], b[keep], c[keep]]), np.column_stack([a[keep], c[keep], d[keep]])])
    W = H = 2048
    px = X0 + (np.arange(W) + 0.5) * (X1 - X0) / W
    py = Y1 - (np.arange(H) + 0.5) * (Y1 - Y0) / H
    PX, PY = np.meshgrid(px, py)
    if img_far is not None:
        col = img_far.sample(proj, PX, PY, order=1)
        col = np.clip(col * 1.05, 0, 255)
        # huecos sin dato (borde de escena, nubes enmascaradas) salían negros: se rellenan con el
        # píxel válido más cercano
        from scipy.ndimage import distance_transform_edt
        bad = col.sum(-1) < 20
        if bad.any() and (~bad).any():
            idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
            col = col[idx[0], idx[1]]
    else:
        col = np.full(PX.shape + (3,), (120, 118, 90), np.float32)
    img = Image.fromarray(col.astype(np.uint8), "RGB")
    scene.textures["horizonte.png"] = textures.png(img)
    scene.images["horizonte.png"] = img
    scene.material("horizonte", "horizonte.png", ambient=0.445, diffuse=0.4, specular=0.0, spec_exp=1.0, shadows=False)
    scene.add("horizonte", "horizonte", pos, uv, faces)
    return len(faces)
