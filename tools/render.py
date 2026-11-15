"""Renderizador por software (CPU) para revisar un .glb sin GPU ni navegador.

Uso: python tools/render.py escena.glb salida.png --cam ex,ey,ez --mira tx,ty,tz [--ancho 960]
     python tools/render.py escena.glb salida.png --salida-coche   (cámara en AC_START_0)
Coordenadas glTF: x este, y arriba, z sur.
"""
import argparse
import io
import json
import struct

import numpy as np
from numba import njit
from PIL import Image


def load_glb(path):
    data = open(path, "rb").read()
    jl = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + jl])
    bo = 20 + jl + 8
    binb = data[bo:]

    def acc(i):
        a = js["accessors"][i]
        v = js["bufferViews"][a["bufferView"]]
        dt = {5126: np.float32, 5123: np.uint16, 5125: np.uint32}[a["componentType"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[a["type"]]
        arr = np.frombuffer(binb, dt, a["count"] * n, v["byteOffset"])
        return arr.reshape(-1, n) if n > 1 else arr

    texs = []
    for img in js["images"]:
        v = js["bufferViews"][img["bufferView"]]
        im = Image.open(io.BytesIO(binb[v["byteOffset"]:v["byteOffset"] + v["byteLength"]])).convert("RGBA")
        texs.append(np.asarray(im).astype(np.float32) / 255.0)
    meshes = []
    for node in js["nodes"]:
        if "mesh" not in node:
            continue
        pr = js["meshes"][node["mesh"]]["primitives"][0]
        mat = js["materials"][pr["material"]]
        ti = js["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
        meshes.append({"name": node["name"], "pos": acc(pr["attributes"]["POSITION"]).astype(np.float64),
                       "nrm": acc(pr["attributes"]["NORMAL"]).astype(np.float64),
                       "uv": acc(pr["attributes"]["TEXCOORD_0"]).astype(np.float64),
                       "idx": acc(pr["indices"]).astype(np.int64).reshape(-1, 3),
                       "tex": ti, "alpha": mat.get("alphaMode") == "MASK", "blend": mat.get("alphaMode") == "BLEND"})
    dummies = {n["name"]: n for n in js["nodes"] if "mesh" not in n}
    return meshes, texs, dummies, js.get("extras", {})


@njit(cache=True)
def raster(sx, sy, sw, uvw, shade, idx, tex, alpha, zbuf, cbuf, W, H, blend=False):
    th, tw = tex.shape[0], tex.shape[1]
    for t in range(idx.shape[0]):
        a, b, c = idx[t, 0], idx[t, 1], idx[t, 2]
        if sw[a] <= 0 or sw[b] <= 0 or sw[c] <= 0:
            continue
        x0, y0, x1, y1, x2, y2 = sx[a], sy[a], sx[b], sy[b], sx[c], sy[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        minx = max(0, int(min(x0, min(x1, x2))))
        maxx = min(W - 1, int(max(x0, max(x1, x2))) + 1)
        miny = max(0, int(min(y0, min(y1, y2))))
        maxy = min(H - 1, int(max(y0, max(y1, y2))) + 1)
        if minx > maxx or miny > maxy:
            continue
        if (maxx - minx) * (maxy - miny) > W * H:
            continue
        for py in range(miny, maxy + 1):
            for px in range(minx, maxx + 1):
                fx, fy = px + 0.5, py + 0.5
                w0 = ((x1 - fx) * (y2 - fy) - (x2 - fx) * (y1 - fy)) / area
                w1 = ((x2 - fx) * (y0 - fy) - (x0 - fx) * (y2 - fy)) / area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                iw = w0 / sw[a] + w1 / sw[b] + w2 / sw[c]
                z = 1.0 / iw
                if z >= zbuf[py, px]:
                    continue
                u = (w0 * uvw[a, 0] / sw[a] + w1 * uvw[b, 0] / sw[b] + w2 * uvw[c, 0] / sw[c]) * z
                v = (w0 * uvw[a, 1] / sw[a] + w1 * uvw[b, 1] / sw[b] + w2 * uvw[c, 1] / sw[c]) * z
                tu = int((u - np.floor(u)) * tw) % tw
                tv = int((v - np.floor(v)) * th) % th
                col = tex[tv, tu]
                if alpha and col[3] < 0.5:
                    continue
                s = w0 * shade[a] + w1 * shade[b] + w2 * shade[c]
                if blend:
                    k = col[3]
                    cbuf[py, px, 0] = cbuf[py, px, 0] * (1 - k) + col[0] * s * k
                    cbuf[py, px, 1] = cbuf[py, px, 1] * (1 - k) + col[1] * s * k
                    cbuf[py, px, 2] = cbuf[py, px, 2] * (1 - k) + col[2] * s * k
                    continue
                zbuf[py, px] = z
                cbuf[py, px, 0] = col[0] * s
                cbuf[py, px, 1] = col[1] * s
                cbuf[py, px, 2] = col[2] * s


def render(meshes, texs, eye, target, W=960, H=540, fov=60.0, skip=()):
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, [0, 1, 0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    fl = (W / 2) / np.tan(np.radians(fov) / 2)
    zbuf = np.full((H, W), 1e18)
    sky = np.linspace(0.55, 0.85, H)[:, None]
    cbuf = np.dstack([sky * 0.75 * np.ones((H, W)), sky * 0.85 * np.ones((H, W)), np.ones((H, W)) * 0.95])
    sun = np.array([0.4, 0.8, 0.35])
    sun /= np.linalg.norm(sun)
    for m in sorted(meshes, key=lambda m: m["blend"]):
        if any(k in m["name"] for k in skip):
            continue
        d = m["pos"] - eye
        cx, cy, cz = d @ r, d @ u, d @ f
        if cz.max() < 0.1:
            continue
        sx = W / 2 + fl * cx / np.maximum(cz, 1e-6)
        sy = H / 2 - fl * cy / np.maximum(cz, 1e-6)
        shade = 0.45 + 0.65 * np.clip(m["nrm"] @ sun, 0, 1)
        if m["alpha"]:
            shade = np.full(len(shade), 0.95)
        raster(sx, sy, cz, m["uv"], shade, m["idx"], texs[m["tex"]], m["alpha"], zbuf, cbuf, W, H, m["blend"])
    # niebla de distancia
    fog = np.clip((zbuf - 900) / 9000, 0, 0.45)[..., None]
    fog[zbuf > 1e17] = 0
    cbuf = cbuf * (1 - fog) + np.array([0.75, 0.82, 0.92]) * fog
    return Image.fromarray((np.clip(cbuf, 0, 1) * 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--cam")
    ap.add_argument("--mira")
    ap.add_argument("--salida-coche", action="store_true")
    ap.add_argument("--dummy", default="AC_START_0")
    ap.add_argument("--ancho", type=int, default=960)
    ap.add_argument("--alto", type=int, default=540)
    ap.add_argument("--fov", type=float, default=60)
    a = ap.parse_args()
    meshes, texs, dummies, extras = load_glb(a.glb)
    if a.salida_coche:
        dn = dummies[a.dummy]
        p = np.array(dn["translation"])
        fw = np.array(dn["extras"]["forward"])
        eye = p + np.array([0, 1.6, 0]) - fw * 6
        target = p + fw * 25 + np.array([0, 0.5, 0])
    else:
        eye = [float(v) for v in a.cam.split(",")]
        target = [float(v) for v in a.mira.split(",")]
    img = render(meshes, texs, eye, target, a.ancho, a.alto, a.fov)
    img.save(a.out)
    print("ok", a.out)


if __name__ == "__main__":
    main()
