"""Carpeta de pista para Assetto Corsa (2014): KN5, ui, mapa, superficies, IA, cámaras y CSP.

Estructura del KN5 (verificable con tools/kn5_check.py): versión 5, texturas DDS embebidas,
materiales con propiedades de 10 floats, árbol de nodos con dummies AC_* cuyas filas de la
matriz son los ejes X, Y, Z (Z = hacia adelante) y la traslación.
"""
import io
import json
import os
import struct

import numpy as np
from PIL import Image, ImageDraw

from . import CREDIT, URL, __version__, dds, regions, textures
from .export_glb import to_yup, mesh_name


def _s(b, txt):
    d = txt.encode("utf-8")
    b.write(struct.pack("<i", len(d)))
    b.write(d)


def _prop(b, name, v):
    _s(b, name)
    vals = list(v) if isinstance(v, (list, tuple)) else [v]
    if len(vals) == 10:
        b.write(struct.pack("<10f", *vals))
        return
    a = vals[0] if len(vals) == 1 else 0.0
    v2 = vals if len(vals) == 2 else [0, 0]
    v3 = vals if len(vals) == 3 else [0, 0, 0]
    v4 = vals if len(vals) == 4 else [0, 0, 0, 0]
    b.write(struct.pack("<f2f3f4f", a, *v2, *v3, *v4))


def _tangents(pos, uv, faces, nrm):
    t = np.zeros_like(pos)
    p0, p1, p2 = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    u0, u1, u2 = uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = u1 - u0, u2 - u0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    ft = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    for k in range(3):
        np.add.at(t, faces[:, k], ft)
    t -= nrm * (t * nrm).sum(1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    fallback = np.cross(nrm, [0, 0, 1.0])
    bad = ln[:, 0] < 1e-9
    t[bad] = fallback[bad]
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    ln[ln < 1e-9] = 1
    return t / ln


def _dummy_matrix(pos_ac, fwd_ac):
    """Filas = ejes X, Y, Z del dummy y traslación; Z apunta hacia donde mira el coche."""
    f = np.array(fwd_ac, float)
    f[1] = 0
    f /= max(np.linalg.norm(f), 1e-9)
    up = np.array([0, 1.0, 0])
    xax = np.cross(up, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = xax, up, f
    m[3, :3] = pos_ac
    return m


def _dds_name(n):
    return n.rsplit(".", 1)[0] + ".dds"


def _image(scene, name):
    if name in scene.images:
        return scene.images[name]
    return textures.tex_image(scene.textures[name])


SHADER = {"plain": "ksPerPixel", "blend": "ksPerPixel", "invisible": "ksPerPixel", "at": "ksPerPixelAT",
          "nm": "ksPerPixelNM", "multilayer": "ksMultilayer_fresnel_nm"}


def write_kn5(scene, path, log=print):
    meshes = scene.meshes()
    used = {}
    for m in scene.materials.values():
        for t in [m["texture"], m["normal"], m["extra"].get("mask"), m["extra"].get("detail_nm")] + list(m["extra"].get("details", [])):
            if t:
                used[t] = True
    b = io.BytesIO()
    b.write(b"sc6969")
    b.write(struct.pack("<i", 5))
    names = sorted(used)
    b.write(struct.pack("<i", len(names)))
    total_tex = 0
    for t in names:
        img = _image(scene, t)
        alpha = img.mode == "RGBA" and not t.endswith(("_mask.png", "mascara_r.png"))
        data = dds.encode(img, alpha=None if alpha else False)
        if t.endswith(("_mask.png", "mascara_r.png")):
            data = dds.encode(img, alpha=True)
        total_tex += len(data)
        b.write(struct.pack("<i", 1))
        _s(b, _dds_name(t))
        b.write(struct.pack("<i", len(data)))
        b.write(data)
    mat_names = list(scene.materials)
    b.write(struct.pack("<i", len(mat_names)))
    for name in mat_names:
        m = scene.materials[name]
        kind = m["kind"]
        _s(b, name)
        _s(b, SHADER[kind])
        # depthMode: 0 = normal, 1 = lee pero no escribe profundidad. Las calcomanías
        # semitransparentes (orillas, desgaste) no deben escribirla: si lo hacen, tapan o
        # parpadean contra lo que se dibuja después en el mismo lugar.
        depth_mode = 1 if m["blend"] else 0
        b.write(struct.pack("<BBi", 1 if m["blend"] else 0, 1 if kind == "at" else 0, depth_mode))
        amb = m["ambient"] if m["ambient"] is not None else 0.45
        dif = m["diffuse"] if m["diffuse"] is not None else 0.5
        spe = m["spec_exp"] if m["spec_exp"] is not None else 20.0
        props = [("ksAmbient", amb), ("ksDiffuse", dif), ("ksSpecular", m["specular"]),
                 ("ksSpecularEXP", spe), ("ksEmissive", [m["emissive"]] * 3)]
        if kind == "at":
            props.append(("ksAlphaRef", 0.4))
        if kind == "multilayer":
            mult = m["extra"].get("mult", 16.0)
            mr, mg, mb, ma = mult if isinstance(mult, (list, tuple)) else (mult,) * 4
            props += [("multR", mr), ("multG", mg), ("multB", mb), ("multA", ma)]
            props += list(m["extra"].get("props", {}).items())
        b.write(struct.pack("<i", len(props)))
        for pn, pv in props:
            _prop(b, pn, pv)
        samplers = [("txDiffuse", 0, m["texture"])]
        if kind == "nm":
            samplers.append(("txNormal", 1, m["normal"]))
        if kind == "multilayer":
            ex = m["extra"]
            samplers.append(("txMask", 1, ex["mask"]))
            for k, (ch, d) in enumerate(zip("RGBA", ex["details"])):
                samplers.append((f"txDetail{ch}", 2 + k, d))
            samplers.append(("txDetailNM", 6, ex["detail_nm"]))
        b.write(struct.pack("<i", len(samplers)))
        for sn, slot, tn in samplers:
            _s(b, sn)
            b.write(struct.pack("<i", slot))
            _s(b, _dds_name(tn))
    mat_id = {n: i for i, n in enumerate(mat_names)}
    b.write(struct.pack("<i", 1))
    _s(b, "openlaps")
    b.write(struct.pack("<i", len(meshes) + len(scene.dummies)))
    b.write(struct.pack("<B", 1))
    b.write(struct.pack("<16f", *np.eye(4).ravel()))
    for name, p, fwd in scene.dummies:
        pa = to_yup(p[None])[0]
        fa = to_yup(np.array([[fwd[0], fwd[1], 0.0]]))[0]
        b.write(struct.pack("<i", 1))
        _s(b, name)
        b.write(struct.pack("<i", 0))
        b.write(struct.pack("<B", 1))
        b.write(struct.pack("<16f", *_dummy_matrix(pa, fa).ravel()))
    for m in meshes:
        pos = to_yup(m["pos"])
        nrm = to_yup(m["nrm"])
        uv = m["uv"]
        tan = _tangents(pos, uv, m["faces"], nrm)
        mat = scene.materials[m["material"]]
        b.write(struct.pack("<i", 2))
        _s(b, mesh_name(m))
        b.write(struct.pack("<i", 0))
        b.write(struct.pack("<B", 1))
        cast = m["shadow"] and mat["shadows"] and mat["kind"] != "invisible"
        b.write(struct.pack("<BBB", 1 if cast else 0, 1, 0))
        vert = np.hstack([pos, nrm, uv, tan]).astype(np.float32)
        b.write(struct.pack("<i", len(vert)))
        b.write(vert.tobytes())
        idx = m["faces"].astype(np.uint16).ravel()
        b.write(struct.pack("<i", len(idx)))
        b.write(idx.tobytes())
        b.write(struct.pack("<i", mat_id[m["material"]]))
        b.write(struct.pack("<i", 0))
        b.write(struct.pack("<ff", 0.0, float(m["lod"])))
        c = (pos.min(0) + pos.max(0)) / 2
        r = float(np.linalg.norm(pos - c, axis=1).max())
        b.write(struct.pack("<3ff", *c, r))
        b.write(struct.pack("<B", 1))
    with open(path, "wb") as f:
        f.write(b.getvalue())
    log(f"  KN5: {len(names)} texturas DDS ({total_tex / 1e6:.0f} MB), {len(mat_names)} materiales, {len(meshes)} mallas")


SURFACE_DEFS = [
    # KEY, FRICTION, DAMPING, WAV, WAV_PITCH, FF, DIRT, VALID, SIN_H, SIN_L, PIT, VIB_GAIN, VIB_LEN
    ("ROAD", 0.98, 0, "", 0, "NULL", 0, 1, 0, 0, 0, 0, 0),
    ("PITLANE", 0.97, 0, "", 0, "NULL", 0, 1, 0, 0, 1, 0, 0),
    ("PAVEMENT", 0.9, 0, "", 0, "NULL", 0.05, 1, 0, 0, 0, 0.1, 0.5),
    ("KERB", 0.92, 0, "kerb.wav", 1, "1", 0, 1, 0, 0, 0, 0.4, 1),
    ("GRASS", 0.6, 0, "grass.wav", 0, "NULL", 1, 0, 0.03, 0.5, 0, 0.2, 0.6),
    ("SAND", 0.55, 0.3, "sand.wav", 0, "NULL", 1, 0, 0.05, 0.5, 0, 0, 0),
    ("DIRT", 0.72, 0.05, "sand.wav", 0, "NULL", 0.8, 0, 0.04, 0.6, 0, 0.3, 0.7),
    ("GRAVEL", 0.62, 0.2, "sand.wav", 0, "NULL", 1, 0, 0.05, 0.5, 0, 0, 0),
    ("WATER", 0.25, 1, "", 0, "NULL", 0, 0, 0, 0, 0, 0, 0),
    ("WALL", 0.8, 0, "", 0, "NULL", 0, 0, 0, 0, 0, 0, 0),
    ("LOOSE", 0.8, 0, "sand.wav", 0, "NULL", 0.3, 1, 0, 0, 0, 0.15, 0.4),
    ("MXCOBBLE", 0.9, 0, "", 0, "NULL", 0, 1, 0.006, 0.25, 0, 0.6, 0.3),
]


def surfaces_ini():
    out = []
    for i, (k, fr, dm, wav, wp, ff, dirt, valid, sh, sl, pit, vg, vl) in enumerate(SURFACE_DEFS):
        out.append(f"[SURFACE_{i}]\nKEY={k}\nFRICTION={fr}\nDAMPING={dm}\nWAV={wav}\nWAV_PITCH={wp}\nFF_EFFECT={ff}\n"
                   f"DIRT_ADDITIVE={dirt}\nBLACK_FLAG_TIME=0\nIS_VALID_TRACK={valid}\nSIN_HEIGHT={sh}\nSIN_LENGTH={sl}\n"
                   f"IS_PITLANE={pit}\nVIBRATION_GAIN={vg}\nVIBRATION_LENGTH={vl}\n")
    return "\n".join(out)


def _ai_bytes(poly, hw):
    """fast_lane.ai versión 7: puntos de 20 bytes y extras de 18 floats por punto."""
    P = to_yup(poly)
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    L = np.concatenate([[0], np.cumsum(seg)])
    seg_next = np.concatenate([seg, [seg[-1] if len(seg) else 1.0]])
    n = len(P)
    F = np.gradient(P, axis=0)
    F[:, 1] = 0
    F /= np.maximum(np.linalg.norm(F, axis=1, keepdims=True), 1e-9)
    h = np.unwrap(np.arctan2(F[:, 2], F[:, 0]))
    kappa = np.abs(np.gradient(h, np.maximum(L, 1e-6)))
    R = 1.0 / np.maximum(kappa, 1e-4)
    speed = np.minimum(np.sqrt(9.0 * R), 40.0)
    hw = np.broadcast_to(np.asarray(hw, float), (n,))
    b = io.BytesIO()
    b.write(struct.pack("<4i", 7, n, 0, 0))
    for i in range(n):
        b.write(struct.pack("<4fi", *P[i], L[i], i))
    b.write(struct.pack("<i", n))
    for i in range(n):
        b.write(struct.pack("<18f", speed[i], 1.0, 0.0, 0.0, min(R[i], 5000.0), hw[i], hw[i], 0.0, 1.0,
                            0.0, 1.0, 0.0, seg_next[i], F[i, 0], 0.0, F[i, 2], 0.0, 0.0))
    b.write(struct.pack("<i", 0))
    return b.getvalue()


def cameras_ini(cams):
    out = [f"[HEADER]\nVERSION=3\nCAMERA_COUNT={len(cams)}\nSET_NAME=openlaps TV\n"]
    for i, (pos, look, a, z) in enumerate(cams):
        p = to_yup(np.asarray(pos, float)[None])[0]
        q = to_yup(np.asarray(look, float)[None])[0]
        f = q - p
        f /= max(np.linalg.norm(f), 1e-9)
        r = np.cross(f, [0, 1, 0])
        r /= max(np.linalg.norm(r), 1e-9)
        u = np.cross(r, f)
        fmt = lambda v: " ,".join(f"{x:.5f}" for x in v)  # noqa: E731
        out.append(f"[CAMERA_{i}]\nNAME=mx_cam_{i:02d}\nPOSITION={p[0]:.3f} ,{p[1]:.3f} ,{p[2]:.3f}\nFORWARD={fmt(f)}\n"
                   f"UP={fmt(u)}\nMIN_FOV=8\nMAX_FOV=40\nIN_POINT={a:.5f}\nOUT_POINT={z:.5f}\nSHADOW_SPLIT0=50\n"
                   "SHADOW_SPLIT1=100\nSHADOW_SPLIT2=380\nNEAR_PLANE=1\nFAR_PLANE=35000\nMIN_EXPOSURE=0.2\n"
                   "MAX_EXPOSURE=0.45\nDOF_FACTOR=1\nDOF_RANGE=250\nDOF_FOCUS=20\nDOF_MANUAL=0\nSPLINE=\n"
                   "SPLINE_ROTATION=0\nFOV_GAMMA=0.5\nSPLINE_ANIMATION_LENGTH=10\nIS_FIXED=0\n")
    return "\n".join(out)


def ext_config(scene, lat, lon):
    mats = list(scene.materials)
    terr = [m for m in mats if m.startswith("terreno_")]
    roads = [m for m in mats if m.startswith("asfalto")] + [m for m in ("concreto", "empedrado", "adoquin", "terraceria", "banqueta",
                         "guarnicion", "guarnicion_amarilla", "tope", "raya_blanca", "raya_amarilla",
                         "agua", "puente", "muro") if m in mats]
    fac = [m for m in mats if m.startswith(("fachada_", "iglesia", "azotea_"))]
    veg = [m for m in mats if m.startswith("veg_")]
    j = ", ".join
    out = [f"[BASIC]\nLATITUDE={lat:.4f}\nLONGITUDE={lon:.4f}\n",
           "[LIGHTING]\nLIT_MULT=1\nSPECULAR_MULT=1\nCAR_LIGHTS_LIT_MULT=1\nBOUNCED_LIGHT_MULT=1\n",
           f"[GRASS_FX]\nGRASS_MATERIALS={j(terr + (['isleta'] if 'isleta' in mats else []))}\nOCCLUDING_MATERIALS={j(roads + fac + ['orilla'])}\n"
           "MASK_BLUR=1\nMASK_MAIN_THRESHOLD=-1\nMASK_RED_THRESHOLD=0\nMASK_MIN_LUMINANCE=0\nMASK_MAX_LUMINANCE=0.9\n"
           "SHAPE_SIZE=1.1\nSHAPE_TIDY=0.3\nSHAPE_CUT=0\nSHAPE_WIDTH=1\n",
           f"[RAIN_FX]\nPUDDLES_MATERIALS={j([m for m in mats if m.startswith('asfalto')] + ['concreto'])}\nPUDDLES_AMOUNT=0.6\nSOAKING_MATERIALS={j(['banqueta', 'muro', 'puente'] + fac)}\n"
           f"ROUGH_MATERIALS={j(terr + ['terraceria', 'empedrado', 'adoquin', 'orilla'])}\nSMOOTH_MATERIALS=defensa, tinaco\n"
           "LINES_MATERIALS=raya_blanca, raya_amarilla\n",
           f"[SHADER_REPLACEMENT_0]\nMATERIALS={j(veg)}\nSHADER=ksTree\nPROP_0=ksAlphaRef, 0.35\n",
           f"[MATERIAL_ADJUSTMENT_0]\nMATERIALS={j([m for m in fac if m.startswith(('fachada_', 'iglesia'))])}\n"
           "KEY_0=ksEmissive\nVALUE_0=0.12, 0.10, 0.07\nCONDITION=NIGHT_SMOOTH\n",
           "[MATERIAL_ADJUSTMENT_1]\nMATERIALS=lampara\nKEY_0=ksEmissive\nVALUE_0=6, 5, 3.5\nCONDITION=NIGHT_SMOOTH\n",
           f"[MATERIAL_ADJUSTMENT_2]\nMATERIALS={j(terr)}\nKEY_0=ksEmissive\nVALUE_0=0.0, 0.0, 0.0\n"]
    for i, (pos, d, col, inten, rng) in enumerate(scene.lights):
        p = to_yup(np.asarray(pos, float)[None])[0]
        out.append(f"[LIGHT_{i}]\nDESCRIPTION=arbotante\nPOSITION={p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}\n"
                   f"COLOR={col[0]}, {col[1]}, {col[2]}, {inten}\nRANGE={rng}\nRANGE_GRADIENT_OFFSET=0.2\nSPOT=150\n"
                   "SPOT_SHARPNESS=0.5\nDIRECTION=0, -1, 0\nFADE_AT=600\nFADE_SMOOTH=200\nCONDITION=NIGHT_SMOOTH\n")
    return "\n".join(out)


def write_track(scene, out_dir, track_id, info, map_lines, route=None, log=print):
    root = os.path.join(out_dir, track_id)
    for sub in ("", "ui", "data", "ai", "extension"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    write_kn5(scene, os.path.join(root, f"{track_id}.kn5"), log=log)
    w = lambda rel, txt: open(os.path.join(root, rel), "w", newline="\r\n", encoding="utf-8").write(txt)  # noqa: E731
    w("models.ini", f"[MODEL_0]\nFILE={track_id}.kn5\nPOSITION=0,0,0\nROTATION=0,0,0\n")
    w(os.path.join("data", "surfaces.ini"), surfaces_ini())
    w(os.path.join("data", "lighting.ini"), "[LIGHTING]\nSUN_PITCH_ANGLE=45\nSUN_HEADING_ANGLE=-40\n")
    w(os.path.join("extension", "ext_config.ini"), ext_config(scene, info["lat"], info["lon"]))
    if scene.cameras:
        w(os.path.join("data", "cameras.ini"), cameras_ini(scene.cameras))
    # mapa
    xs = np.concatenate([l[:, 0] for l in map_lines]) if map_lines else np.array([0, 1])
    ys = np.concatenate([l[:, 1] for l in map_lines]) if map_lines else np.array([0, 1])
    xmin, xmax, ymin, ymax = xs.min(), xs.max(), ys.min(), ys.max()
    scale = max(1.0, max(xmax - xmin, ymax - ymin) / 1560.0)
    margin = 20
    W = int((xmax - xmin) / scale) + 2 * margin
    H = int((ymax - ymin) / scale) + 2 * margin
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    tp = lambda c: [((x - xmin) / scale + margin, (ymax - y) / scale + margin) for x, y in c[:, :2]]  # noqa: E731
    for l in map_lines:
        d.line(tp(l), fill=(255, 255, 255, 160), width=3)
    if route is not None:
        d.line(tp(route), fill=(255, 255, 255, 255), width=8)
    img.save(os.path.join(root, "map.png"))
    w(os.path.join("data", "map.ini"), f"[PARAMETERS]\nWIDTH={W}\nHEIGHT={H}\nMARGIN={margin}\nSCALE_FACTOR={scale:.5f}\n"
      f"MAX_SIZE=1600\nX_OFFSET={-xmin + margin * scale:.2f}\nZ_OFFSET={ymax + margin * scale:.2f}\nDRAWING_SIZE=10\n")
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(out)
    if route is not None:
        od.line(tp(route), fill=(255, 255, 255, 255), width=12)
    else:
        for l in map_lines:
            od.line(tp(l), fill=(255, 255, 255, 255), width=6)
    out.thumbnail((512, 512))
    out.save(os.path.join(root, "ui", "outline.png"))
    if info.get("preview_png"):
        with open(os.path.join(root, "ui", "preview.png"), "wb") as f:
            f.write(info["preview_png"])
    ui = {"name": info["name"], "description": f"{info['description']} {CREDIT}", "tags": info.get("tags", []),
          "geotags": [f"{info['lat']:.5f}", f"{info['lon']:.5f}"], "country": regions.active().country,
          "city": info.get("city", ""), "length": str(int(info.get("length", 0))), "width": str(info.get("width", "")),
          "pitboxes": str(info.get("pitboxes", 8)), "run": info.get("run", "A-B"),
          "author": "openlaps", "version": __version__, "url": URL}
    with open(os.path.join(root, "ui", "ui_track.json"), "w", encoding="utf-8") as f:
        json.dump(ui, f, ensure_ascii=False, indent=2)
    if route is not None and info.get("ai_hw") is not None:
        with open(os.path.join(root, "ai", "fast_lane.ai"), "wb") as f:
            f.write(_ai_bytes(route, info["ai_hw"]))
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(route[:, :2], axis=0), axis=1))])
        k = max(3, int(np.searchsorted(s, 45.0)))
        with open(os.path.join(root, "ai", "pit_lane.ai"), "wb") as f:
            f.write(_ai_bytes(route[:k], np.asarray(info["ai_hw"])[:k] if np.ndim(info["ai_hw"]) else info["ai_hw"]))
    with open(os.path.join(root, "ATRIBUCIONES.txt"), "w", encoding="utf-8") as f:
        f.write(info["attribution"])
    log(f"  pista de Assetto Corsa: {root}")
    return root
