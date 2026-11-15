"""Compara 1 a 1 fotos reales a pie de calle con la misma vista renderizada de un .glb.

Para cada foto de referencias/<nombre>/referencias.json (ver tools/referencias.py):
  1. pone la cámara en la posición de la foto (lat/lon -> UTM local del .glb), a `alto` metros
     sobre el suelo del modelo, con su rumbo (corrigiendo la convergencia de meridianos);
  2. si es una panorámica 360°, recorta la vista en perspectiva que toque;
  3. busca el pequeño giro de rumbo y cabeceo, el zoom y el desplazamiento (±6 m) que mejor encajan
     la silueta (la brújula, la focal del EXIF y el GPS entre edificios fallan) y vuelve a renderizar.
     Ojo: la búsqueda puede compensar en parte un edificio de altura equivocada; con --sin-auto o
     --buscar-pos 0 se ve la pose tal cual;
  4. mide el parecido: IoU del cielo, diferencia de la línea de cornisa en grados (¿edificios
     demasiado bajos o altos?) y correlación de bordes;
  5. guarda un panel 2x2 por foto (real | modelo / mezcla | bordes y cornisas), una hoja con
     todas y un index.html con cortinilla para revisarlas a mano.

Uso:
  python tools/comparar_fotos.py salida/madrid_sol.glb referencias/madrid_sol
  python tools/comparar_fotos.py salida/madrid_sol.glb referencias/madrid_sol --solo pnx_37c78a1f --sin-auto
Sale en salida/comparar_<nombre>/. Rojo = bordes y cornisa de la foto; cian = del modelo.
"""
import argparse
import html
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from render import load_glb, render  # noqa: E402
from openlaps.cobertura import fuente  # noqa: E402
from openlaps.geo import BBox, LocalProjection  # noqa: E402

GROUND = ("terreno", "calzada", "banqueta", "cruce", "agua")
FOV_LARGO = 68.0     # campo de visión típico de un móvil en su lado largo, si la foto no lo trae


# ----------------------------------------------------------------------------- geometría
class Suelo:
    """Altura del suelo del modelo bajo un punto (rayo vertical contra calzada, banqueta y terreno)."""

    def __init__(self, meshes):
        self.parts = []
        for m in meshes:
            if not any(k in m["name"] for k in GROUND) or "horizonte" in m["name"]:
                continue
            p = m["pos"]
            self.parts.append((p[:, 0].min(), p[:, 0].max(), p[:, 2].min(), p[:, 2].max(), p, m["idx"]))

    def z(self, x, zs):
        best = -1e9
        for x0, x1, z0, z1, p, idx in self.parts:
            if not (x0 <= x <= x1 and z0 <= zs <= z1):
                continue
            a, b, c = p[idx[:, 0]], p[idx[:, 1]], p[idx[:, 2]]
            sel = ((np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0]) <= x) &
                   (np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0]) >= x) &
                   (np.minimum(np.minimum(a[:, 2], b[:, 2]), c[:, 2]) <= zs) &
                   (np.maximum(np.maximum(a[:, 2], b[:, 2]), c[:, 2]) >= zs))
            if not sel.any():
                continue
            a, b, c = a[sel], b[sel], c[sel]
            den = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
            ok = np.abs(den) > 1e-12
            den = np.where(ok, den, 1)
            w0 = ((b[:, 2] - c[:, 2]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (zs - c[:, 2])) / den
            w1 = ((c[:, 2] - a[:, 2]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (zs - c[:, 2])) / den
            w2 = 1 - w0 - w1
            inside = ok & (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if inside.any():
                y = (w0 * a[:, 1] + w1 * b[:, 1] + w2 * c[:, 1])[inside]
                best = max(best, float(y.max()))
        return None if best < -1e8 else best


def ejes_locales(proj, lon, lat):
    """Vectores unitarios este y norte geográficos expresados en la rejilla local (UTM)."""
    x0, y0 = proj.to_local([lon], [lat])
    x1, y1 = proj.to_local([lon], [lat + 1e-4])
    n = np.array([float(x1[0] - x0[0]), float(y1[0] - y0[0])])
    n /= np.linalg.norm(n)
    e = np.array([n[1], -n[0]])
    return np.array([float(x0[0]), float(y0[0])]), e, n


def direccion(e, n, rumbo, cabeceo=0.0):
    """Rumbo (grados desde el norte, horario) y cabeceo -> vector glTF (x este, y arriba, z sur)."""
    r = math.radians(rumbo)
    d = math.sin(r) * e + math.cos(r) * n
    c = math.radians(cabeceo)
    return np.array([d[0] * math.cos(c), math.sin(c), -d[1] * math.cos(c)])


def fov_vertical(fov_h, w, h):
    return math.degrees(2 * math.atan(math.tan(math.radians(fov_h) / 2) * h / w))


# ----------------------------------------------------------------------------- fotos
def vista_de_360(pano, rumbo_centro, rumbo, fov_h, W, H):
    """Recorta una vista en perspectiva (rumbo, fov horizontal) de una panorámica equirectangular."""
    P = np.asarray(pano.convert("RGB"), np.float32)
    Hp, Wp = P.shape[:2]
    fl = (W / 2) / math.tan(math.radians(fov_h) / 2)
    px, py = np.meshgrid(np.arange(W) + 0.5 - W / 2, H / 2 - (np.arange(H) + 0.5))
    r = math.radians(rumbo)
    f = np.array([math.sin(r), math.cos(r), 0.0])
    rt = np.array([math.cos(r), -math.sin(r), 0.0])
    d = f[None, None] * fl + rt[None, None] * px[..., None] + np.array([0, 0, 1.0])[None, None] * py[..., None]
    az = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
    el = np.degrees(np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1])))
    u = (((az - rumbo_centro) / 360.0 + 0.5) % 1.0) * Wp - 0.5
    v = (0.5 - el / 180.0) * Hp - 0.5
    out = np.dstack([ndimage.map_coordinates(P[..., k], [v, u], order=1, mode="wrap") for k in range(3)])
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


TOPE_ELEV = 60.0      # elevación máxima de la silueta que se compara al orientar las 360°


def perfil_cielo_pano(pano, centro):
    """Elevación (°) de la silueta contra el cielo en cada grado de azimut de una panorámica
    equirectangular cuyo centro mira a `centro`. NaN donde no hay cielo detectado."""
    im = pano.convert("RGB").resize((720, 360), Image.LANCZOS)
    cielo = mascara_cielo_foto(im)
    fila = cornisa(cielo)                                     # 0 = sin cielo
    elev = 90.0 - fila / 360.0 * 180.0
    elev = np.where(fila == 0, np.nan, np.minimum(elev, TOPE_ELEV))
    az = (centro + (np.arange(720) + 0.5) / 2.0 - 180.0) % 360
    out = np.full(360, np.nan)
    for b in range(360):
        sel = (np.floor(az).astype(int) == b) & ~np.isnan(elev)
        if sel.any():
            out[b] = float(np.median(elev[sel]))
    return out


def perfil_cielo_modelo(meshes, texs, eye, e, n, size=200):
    """Lo mismo en el modelo: seis vistas de 70° alrededor, mirando 25° hacia arriba."""
    E = np.array([e[0], 0.0, -e[1]])
    N = np.array([n[0], 0.0, -n[1]])
    out = [[] for _ in range(360)]
    fl = (size / 2) / math.tan(math.radians(35))
    for h in range(0, 360, 60):
        f = direccion(e, n, h, 25.0)
        _, z = render(meshes, texs, eye, eye + f * 30, size, size, 70.0, depth=True)
        cielo = z > 1e17
        r = np.cross(f, [0, 1, 0])
        r /= np.linalg.norm(r)
        u = np.cross(r, f)
        fila = cornisa(cielo)
        for c in range(0, size, 2):
            x = c + 0.5 - size / 2
            if fila[c] == 0:
                continue
            y = size / 2 - (fila[c] - 0.5) if fila[c] < size else -size
            d = f * fl + r * x + u * y
            az = math.degrees(math.atan2(d @ E, d @ N)) % 360
            el = math.degrees(math.atan2(d[1], math.hypot(d @ E, d @ N)))
            out[int(az) % 360].append(min(el, TOPE_ELEV))
    return np.array([np.median(v) if v else np.nan for v in out])


def orientar_360(pano, rumbo, meshes, texs, eye, e, n):
    """Giro (°) que hay que sumar al rumbo de una 360° para que su centro mire de verdad ahí.

    Algunas cámaras guardan como rumbo el de la marcha del coche aunque la panorámica esté
    girada (en San Francisco, ~90°): se busca el giro que mejor encaja la silueta de los
    edificios contra el cielo de la foto con la del modelo, en toda la vuelta."""
    P = perfil_cielo_pano(pano, 0.0)          # perfil con el centro a 0°: luego se desplaza
    M = perfil_cielo_modelo(meshes, texs, eye, e, n)
    mejor, mejor_err, err0 = 0, 1e9, None
    for d in range(0, 360):
        Pd = np.roll(P, int(round(rumbo + d)) % 360)          # foto colocada con centro en rumbo + d
        ok = ~np.isnan(Pd) & ~np.isnan(M)
        if ok.sum() < 60:
            continue
        err = float(np.mean(np.abs(Pd[ok] - M[ok])))
        if d == 0:
            err0 = err
        if err < mejor_err:
            mejor, mejor_err = d, err
    if mejor > 180:
        mejor -= 360
    return mejor, mejor_err, err0


def mascara_cielo_foto(img):
    """Cielo de una foto: claro, poco saturado o azulado, liso y conectado con el borde superior."""
    a = np.asarray(img.convert("RGB"), np.float32) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    v = a.max(-1)
    s = (v - a.min(-1)) / np.maximum(v, 1e-3)
    gray = a.mean(-1)
    liso = ndimage.gaussian_gradient_magnitude(gray, 1.5) < 0.035
    cand = (((b >= r - 0.03) & (v > 0.42) & (s < 0.6)) | ((v > 0.78) & (s < 0.14))) & liso
    cand = ndimage.binary_opening(cand, np.ones((3, 3)))
    lab, n = ndimage.label(cand)
    if n == 0:
        return np.zeros(cand.shape, bool)
    top = np.unique(lab[:max(2, cand.shape[0] // 40)])
    top = top[top > 0]
    m = ndimage.binary_closing(np.isin(lab, top), np.ones((5, 5)))
    # las nubes dejan huecos dentro del cielo: se rellenan (con una fila de cielo encima para que
    # también se cierren los huecos que tocan el borde superior)
    m = ndimage.binary_fill_holes(np.vstack([np.ones((1, m.shape[1]), bool), m]))[1:]
    return m


def bordes(img_or_gray):
    a = img_or_gray if isinstance(img_or_gray, np.ndarray) else np.asarray(img_or_gray.convert("L"), np.float32) / 255
    g = ndimage.gaussian_gradient_magnitude(a, 1.2)
    return g / max(float(np.percentile(g, 99)), 1e-4)


def cornisa(mask):
    """Para cada columna, fila bajo el píxel de cielo más bajo (0 = sin cielo, H = todo cielo)."""
    H = mask.shape[0]
    low = H - 1 - np.argmax(mask[::-1], axis=0)
    return np.where(mask.any(0), low + 1, 0)


def ncc(a, b):
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else 0.0


# ----------------------------------------------------------------------------- alineado
def muestrear_rotado(src, fl_src, W, H, fl, dyaw, dpitch):
    """Muestrea `src` (render ancho) como si la cámara girara dyaw (der +) y dpitch (arriba +)."""
    Hs, Ws = src.shape[:2]
    px, py = np.meshgrid(np.arange(W) + 0.5 - W / 2, H / 2 - (np.arange(H) + 0.5))
    d = np.stack([px / fl, py / fl, np.ones_like(px)], -1)
    cp, sp = math.cos(math.radians(dpitch)), math.sin(math.radians(dpitch))
    cy, sy = math.cos(math.radians(dyaw)), math.sin(math.radians(dyaw))
    y1 = d[..., 1] * cp + d[..., 2] * sp
    z1 = -d[..., 1] * sp + d[..., 2] * cp
    x2 = d[..., 0] * cy + z1 * sy
    z2 = -d[..., 0] * sy + z1 * cy
    ok = z2 > 1e-3
    u = Ws / 2 + fl_src * x2 / np.where(ok, z2, 1)
    v = Hs / 2 - fl_src * y1 / np.where(ok, z2, 1)
    ui = np.clip(u.astype(int), 0, Ws - 1)
    vi = np.clip(v.astype(int), 0, Hs - 1)
    return src[vi, ui]


def alinear(foto_cielo, foto_bordes, cielo_ancho, bordes_ancho, fl_ancho, W, H, fl, rango_yaw, rango_pitch,
            mults=(1.0,)):
    """Busca giro (rumbo, cabeceo) y zoom (multiplicador de la focal) que mejor encajan la foto con un
    render ancho ya hecho desde la misma posición. Devuelve (puntos, dyaw, dpitch, mult)."""
    hay_cielo = foto_cielo.mean() > 0.03

    def puntos(dy, dp, fm):
        rb = muestrear_rotado(bordes_ancho, fl_ancho, W, H, fl * fm, dy, dp)
        sc = 0.6 * ncc(foto_bordes, rb)
        if hay_cielo:
            rc = muestrear_rotado(cielo_ancho, fl_ancho, W, H, fl * fm, dy, dp)
            sc += (rc & foto_cielo).sum() / max((rc | foto_cielo).sum(), 1)
        # preferencia por no corregir: brújula y focal suelen acertar más o menos
        return sc - 0.002 * (abs(dy) + abs(dp)) - 0.15 * abs(math.log(fm))

    best = (-9.0, 0.0, 0.0, 1.0)
    for fm in mults:
        for dy in np.arange(-rango_yaw, rango_yaw + 1e-6, 2.0):
            for dp in np.arange(-rango_pitch, rango_pitch + 1e-6, 2.0):
                sc = puntos(dy, dp, fm)
                if sc > best[0]:
                    best = (sc, float(dy), float(dp), fm)
    _, by, bp, bm = best
    for dy in (by - 1, by, by + 1):
        for dp in (bp - 1, bp, bp + 1):
            if abs(dy) <= rango_yaw and abs(dp) <= rango_pitch:
                sc = puntos(dy, dp, bm)
                if sc > best[0]:
                    best = (sc, float(dy), float(dp), bm)
    return best


def buscar_pose(meshes, texs, suelo, eye0, e, n, rumbo, cab, foto, fcielo, fov, alto, ry, rp, radio, mults):
    """Prueba posiciones alrededor de la del GPS (rejilla de radio `radio` m) y, en cada una, el giro y
    zoom que mejor encajan. Devuelve (eye, dyaw, dpitch, mult, desplazamiento_m)."""
    W, H = foto.size
    q = 160.0 / W
    w, h = max(8, int(W * q)), max(6, int(H * q))
    fl = (W / 2) / math.tan(math.radians(fov) / 2)
    fls = fl * q
    fcs = np.asarray(Image.fromarray(fcielo.astype(np.uint8) * 255).resize((w, h))) > 127
    fbs = bordes(foto.resize((w, h)))
    fm_min = min(mults)
    fova = min(150.0, 2 * math.degrees(math.atan((w / 2) / (fls * fm_min))) + 2 * (ry + 3))
    fovv = min(150.0, 2 * math.degrees(math.atan((h / 2) / (fls * fm_min))) + 2 * (rp + 3))
    Wa = int(2 * fls * math.tan(math.radians(fova) / 2))
    Ha = int(2 * fls * math.tan(math.radians(fovv) / 2))
    fova = math.degrees(2 * math.atan((Wa / 2) / fls))
    d = direccion(e, n, rumbo, cab)
    pasos = [0.0] if radio <= 0 else list(np.arange(-radio, radio + 1e-6, radio / 2))
    best = None
    for ox in pasos:
        for oy in pasos:
            if math.hypot(ox, oy) > radio + 1e-6:
                continue
            x, ys = eye0[0] + ox, eye0[2] - oy
            gz = suelo.z(x, ys)
            if gz is None:
                continue
            eye = np.array([x, gz + alto, ys])
            ra, za = render(meshes, texs, eye, eye + d * 30, Wa, Ha, fova, depth=True)
            if (za < 2.5).mean() > 0.3:      # cámara metida en un edificio o pegada a un muro
                continue
            sc, dy, dp, fm = alinear(fcs, fbs, za > 1e17, bordes(ra), fls, w, h, fls, ry, rp, mults)
            sc -= 0.012 * math.hypot(ox, oy)
            if best is None or sc > best[0]:
                best = (sc, eye, dy, dp, fm, math.hypot(ox, oy))
    if best is None:
        return eye0, 0.0, 0.0, 1.0, 0.0
    return best[1], best[2], best[3], best[4], best[5]


# ----------------------------------------------------------------------------- panel
def panel(foto, rimg, fcielo, rcielo, titulo, lineas):
    W, H = foto.size
    fa = np.asarray(foto, np.float32)
    ra = np.asarray(rimg, np.float32)
    mezcla = Image.fromarray((fa * 0.5 + ra * 0.5).astype(np.uint8))
    eb = bordes(foto) > 0.35
    rb = bordes(rimg) > 0.35
    over = np.full((H, W, 3), 25, np.uint8)
    over[rb] = (0, 200, 220)
    over[eb] = (235, 50, 50)
    over[eb & rb] = (255, 255, 255)
    cf, cr = cornisa(fcielo), cornisa(rcielo)
    ov = Image.fromarray(over)
    d = ImageDraw.Draw(ov)
    d.font = fuente(13)
    d.line([(x, int(cf[x])) for x in range(W)], fill=(255, 120, 120), width=2)
    d.line([(x, int(cr[x])) for x in range(W)], fill=(120, 255, 255), width=2)
    top = 34
    out = Image.new("RGB", (W * 2, H * 2 + top), (16, 16, 16))
    out.paste(foto, (0, top))
    out.paste(rimg, (W, top))
    out.paste(mezcla, (0, top + H))
    out.paste(ov, (W, top + H))
    d = ImageDraw.Draw(out)
    d.font = fuente(13)
    d.text((6, 3), titulo, fill=(255, 230, 80))
    d.text((6, 18), "   ".join(lineas), fill=(220, 220, 220))
    for (x, y), t in (((4, top + 4), "REAL"), ((W + 4, top + 4), "MODELO"), ((4, top + H + 4), "MEZCLA 50%"),
                      ((W + 4, top + H + 4), "BORDES rojo=real cian=modelo")):
        d.rectangle([x - 2, y - 2, x + d.textlength(t) + 2, y + 15], fill=(0, 0, 0))
        d.text((x, y), t, fill=(255, 255, 255))
    return out


def informe_html(out_dir, filas, nombre):
    css = ("body{font-family:system-ui,sans-serif;background:#111;color:#ddd;margin:0;padding:16px}"
           ".c{margin:0 auto 40px;max-width:1100px}.sw{position:relative;width:100%;overflow:hidden}"
           ".sw img{display:block;width:100%}.sw .top{position:absolute;inset:0;clip-path:inset(0 50% 0 0)}"
           "input[type=range]{width:100%}small{color:#999}a{color:#8cf}h1{font-size:20px}"
           ".m{font-family:monospace;color:#fc6}")
    parts = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
             f"<title>Comparar {html.escape(nombre)}</title><style>{css}</style><h1>{html.escape(nombre)}: "
             f"foto real vs modelo</h1><p>Desliza para pasar de la foto real (izquierda) al modelo. "
             f"Ordenadas de peor a mejor parecido.</p>"]
    for i, r in enumerate(filas):
        parts.append(
            f"<div class=c><h3>{html.escape(r['id'])} <small>{html.escape(r['fuente'])} · rumbo {r['rumbo']:.0f}° · "
            f"{html.escape(str(r.get('fecha') or ''))[:16]}</small></h3>"
            f"<div class=sw id=s{i}><img src='{r['render']}'><img class=top src='{r['foto']}'></div>"
            f"<input type=range min=0 max=100 value=50 oninput=\"document.querySelector('#s{i} .top').style.clipPath="
            f"'inset(0 '+(100-this.value)+'% 0 0)'\">"
            f"<div class=m>{html.escape(' · '.join(r['lineas']))}</div>"
            f"<small>Foto: {html.escape(r['autor'])}, {html.escape(r['licencia'])} — "
            f"<a href='{html.escape(r.get('pagina') or '#')}'>original</a> · <a href='{r['panel']}'>panel</a></small></div>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(parts))


# ----------------------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb")
    ap.add_argument("refs", help="carpeta con referencias.json")
    ap.add_argument("--salida")
    ap.add_argument("--ancho", type=int, default=640)
    ap.add_argument("--alto", type=float, default=1.6, help="altura de la cámara sobre el suelo (m)")
    ap.add_argument("--fov360", type=float, default=80.0, help="campo de visión de las vistas sacadas de 360°")
    ap.add_argument("--vistas360", default="0,90,180,270", help="giros (°) respecto al rumbo de la panorámica")
    ap.add_argument("--sin-auto", action="store_true", help="no corregir rumbo y cabeceo automáticamente")
    ap.add_argument("--rango", default="10,6", help="búsqueda automática: ±rumbo,±cabeceo (grados)")
    ap.add_argument("--buscar-pos", type=float, default=6.0,
                    help="radio (m) para corregir la posición del GPS; 0 = usar la posición tal cual")
    ap.add_argument("--solo", help="ids separados por coma")
    ap.add_argument("--todas", action="store_true", help="incluir también las marcadas con usar=false")
    a = ap.parse_args()

    man = json.load(open(os.path.join(a.refs, "referencias.json"), encoding="utf-8"))
    nombre = man.get("nombre", os.path.basename(a.refs.rstrip("/\\")))
    out_dir = a.salida or os.path.join(os.path.dirname(a.glb) or ".", f"comparar_{nombre}")
    os.makedirs(out_dir, exist_ok=True)
    meshes, texs, dummies, extras = load_glb(a.glb)
    lat0, lon0 = extras["centro"]
    proj = LocalProjection(BBox.around(lat0, lon0, 100))
    suelo = Suelo(meshes)
    # límites del área (x este, y norte) a partir del terreno del modelo
    lim = (min(q[0] for q in suelo.parts), max(q[1] for q in suelo.parts),
           -max(q[3] for q in suelo.parts), -min(q[2] for q in suelo.parts))
    ry, rp = (float(v) for v in a.rango.split(","))
    solo = set(a.solo.split(",")) if a.solo else None

    # orientación de las panorámicas 360°: giro por foto según la silueta y, si una misma cámara
    # (autor) da casi siempre el mismo giro, se usa su mediana para todas sus fotos
    giros, por_autor = {}, {}
    if not a.sin_auto:
        for ref in man["fotos"]:
            if ref["tipo"] != "360" or (solo and ref["id"] not in solo) or (ref.get("usar") is False and not a.todas):
                continue
            pos, e, n = ejes_locales(proj, ref["lon"], ref["lat"])
            gz = suelo.z(pos[0], -pos[1])
            if gz is None:
                continue
            eye = np.array([pos[0], gz + a.alto, -pos[1]])
            src = ImageOps.exif_transpose(Image.open(os.path.join(a.refs, ref["archivo"]))).convert("RGB")
            g, e1, e0 = orientar_360(src, ref["rumbo"], meshes, texs, eye, e, n)
            if e0 is not None and e1 < 0.85 * e0:
                giros[ref["id"]] = g
                por_autor.setdefault(ref.get("autor"), []).append(g)
        def dif(x, y):
            return abs(((x - y) + 180) % 360 - 180)

        for autor, gs in por_autor.items():
            if len(gs) < 3:
                continue
            # grupo mayoritario (en una calle-cañón la silueta hacia delante y hacia atrás se
            # parece y algunas fotos salen con 180° de error: manda la mayoría)
            centro_g = max(gs, key=lambda c: sum(dif(g, c) <= 25 for g in gs))
            cerca = [g for g in gs if dif(g, centro_g) <= 25]
            med = float(np.median(cerca))
            if len(cerca) >= 0.5 * len(gs):
                med = float(np.median(cerca))
                print(f"  cámara de {autor}: panorámicas giradas {med:+.0f}° ({len(cerca)} de {len(gs)} coinciden)")
                for ref in man["fotos"]:
                    if ref.get("autor") == autor and ref["tipo"] == "360":
                        giros[ref["id"]] = med
    filas = []
    for ref in man["fotos"]:
        if solo and ref["id"] not in solo:
            continue
        if ref.get("usar") is False and not a.todas and not solo:
            continue
        aj = ref.get("ajuste") or {}
        pos, e, n = ejes_locales(proj, ref["lon"], ref["lat"])
        pos = pos + np.array([aj.get("dx", 0), aj.get("dy", 0)])
        if not (lim[0] + 5 < pos[0] < lim[1] - 5 and lim[2] + 5 < pos[1] < lim[3] - 5):
            print(f"  {ref['id']}: fuera del área del modelo")
            continue
        gz = suelo.z(pos[0], -pos[1])
        if gz is None:
            print(f"  {ref['id']}: sin suelo bajo la cámara")
            continue
        eye = np.array([pos[0], gz + a.alto + aj.get("alto", 0), -pos[1]])
        src = Image.open(os.path.join(a.refs, ref["archivo"]))
        src = ImageOps.exif_transpose(src).convert("RGB")
        centro = ref["rumbo"]
        if ref["tipo"] == "360":
            giro = giros.get(ref["id"])
            if giro:
                centro = (ref["rumbo"] + giro) % 360
            vistas = [(centro + float(v)) % 360 for v in a.vistas360.split(",")]
        else:
            vistas = [ref["rumbo"]]
        for k, rumbo in enumerate(vistas):
            rid = ref["id"] + (f"_{int(round(float(a.vistas360.split(',')[k])))}" if ref["tipo"] == "360" else "")
            if ref["tipo"] == "360":
                W, H = a.ancho, int(a.ancho * 0.75)
                fov = a.fov360
                foto = vista_de_360(src, centro, rumbo, fov, W, H)
            else:
                sw, sh = src.size
                if sw / sh > 2.3:
                    print(f"  {rid}: panorámica plana (aspecto {sw / sh:.1f}), se omite")
                    continue
                W = a.ancho
                H = int(round(W * sh / sw))
                fov = ref.get("fov")
                if not fov:
                    largo = math.radians(FOV_LARGO) / 2
                    fov = FOV_LARGO if sw >= sh else math.degrees(2 * math.atan(math.tan(largo) * sw / sh))
                foto = src.resize((W, H), Image.LANCZOS)
            rumbo += aj.get("rumbo", 0)
            cab = aj.get("cabeceo", 0)
            fl = (W / 2) / math.tan(math.radians(fov) / 2)
            fcielo = mascara_cielo_foto(foto)
            fb = bordes(foto)
            dyaw = dpitch = 0.0
            fm, desp, ojo = 1.0, 0.0, eye
            if not a.sin_auto:
                mults = (1.0,) if ref["tipo"] == "360" else (0.8, 0.9, 1.0, 1.12, 1.25)
                ojo, dyaw, dpitch, fm, desp = buscar_pose(meshes, texs, suelo, eye, e, n, rumbo, cab, foto, fcielo,
                                                          fov, a.alto + aj.get("alto", 0), ry, rp, a.buscar_pos, mults)
                if fm != 1.0:
                    fov = math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) / fm))
            d = direccion(e, n, rumbo + dyaw, cab + dpitch)
            rimg, z = render(meshes, texs, ojo, ojo + d * 30, W, H, fov, depth=True)
            rcielo = z > 1e17
            inter, union = (fcielo & rcielo).sum(), (fcielo | rcielo).sum()
            iou = inter / union if union else float("nan")
            vf = fov_vertical(fov, W, H)
            cf, cr = cornisa(fcielo), cornisa(rcielo)
            valid = ((cf > 0) | (cr > 0)) & (cf < H) & (cr < H)
            dif = float(np.median(cr[valid] - cf[valid]) * vf / H) if valid.sum() > W * 0.1 else float("nan")
            nb = ncc(bordes(rimg), fb)
            lineas = [f"cielo foto {fcielo.mean() * 100:.0f}% modelo {rcielo.mean() * 100:.0f}%",
                      f"IoU cielo {iou:.2f}" if union else "sin cielo",
                      (f"cornisa {dif:+.1f}° ({'modelo más bajo' if dif > 0 else 'modelo más alto'})"
                       if not math.isnan(dif) else ""),
                      f"bordes {nb:.2f}", f"giro auto {dyaw:+.0f}°/{dpitch:+.0f}°", f"fov {fov:.0f}°" + (f" (x{1 / fm:.2f})" if fm != 1 else ""),
                      f"movida {desp:.1f} m" if desp else "",
                      f"suelo {gz:.1f} m"]
            lineas = [x for x in lineas if x]
            titulo = f"{rid}  [{ref['fuente']}]  {ref['lat']:.6f},{ref['lon']:.6f}  rumbo {rumbo:.0f}°"
            pn = panel(foto, rimg, fcielo, rcielo, titulo, lineas)
            base = f"{rid}"
            pn.save(os.path.join(out_dir, base + "_panel.jpg"), quality=88)
            foto.save(os.path.join(out_dir, base + "_real.jpg"), quality=90)
            rimg.save(os.path.join(out_dir, base + "_modelo.jpg"), quality=90)
            sc = (0 if math.isnan(iou) else iou) + nb
            filas.append({**{k: ref.get(k) for k in ("fuente", "autor", "licencia", "pagina", "fecha")},
                          "id": rid, "rumbo": rumbo, "iou_cielo": None if math.isnan(iou) else round(float(iou), 3),
                          "cornisa_grados": None if math.isnan(dif) else round(dif, 2), "bordes": round(nb, 3),
                          "giro_auto": [dyaw, dpitch], "fov": round(fov, 1), "movida_m": round(desp, 1),
                          "giro_360": giros.get(ref["id"]), "puntos": sc, "lineas": lineas,
                          "panel": base + "_panel.jpg", "foto": base + "_real.jpg", "render": base + "_modelo.jpg"})
            print(f"  {rid}: " + " · ".join(lineas), flush=True)

    filas.sort(key=lambda r: r["puntos"])
    with open(os.path.join(out_dir, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=1)
    informe_html(out_dir, filas, nombre)
    # hoja: real | modelo por fila, para verlo todo de un vistazo
    if filas:
        cw = 400
        rows = []
        for r in filas:
            fa = Image.open(os.path.join(out_dir, r["foto"]))
            ra = Image.open(os.path.join(out_dir, r["render"]))
            h = int(cw * fa.height / fa.width)
            row = Image.new("RGB", (cw * 2, h + 14), (16, 16, 16))
            row.paste(fa.resize((cw, h)), (0, 14))
            row.paste(ra.resize((cw, h)), (cw, 14))
            ImageDraw.Draw(row).text((4, 0), r["id"] + "  " + " · ".join(r["lineas"][:3]), fill=(255, 230, 80))
            rows.append(row)
        for k in range(0, len(rows), 8):
            grp = rows[k:k + 8]
            sheet = Image.new("RGB", (cw * 2, sum(r.height for r in grp)))
            y = 0
            for r in grp:
                sheet.paste(r, (0, y))
                y += r.height
            sheet.save(os.path.join(out_dir, f"hoja_{k // 8 + 1}.jpg"), quality=85)
    print(f"ok {len(filas)} comparaciones en {out_dir} (index.html)")


if __name__ == "__main__":
    main()
