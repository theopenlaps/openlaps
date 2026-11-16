"""Exportador glTF binario (.glb) escrito a mano, sin dependencias."""
import json
import struct

import numpy as np

from . import CREDIT, __version__


def to_yup(p):
    """Local (x este, y norte, z arriba) -> glTF/KN5 (x, y arriba, z sur)."""
    return np.column_stack([p[:, 0], p[:, 2], -p[:, 1]])


def _compact(scene, name):
    """Texturas opacas en JPEG (5-10 veces más ligeras); las que tienen transparencia en PNG."""
    import io as _io
    from .textures import tex_bytes, tex_image
    img = scene.images.get(name)
    if img is None:
        img = tex_image(scene.textures[name])
    if img.mode == "RGBA" and np.asarray(img)[..., 3].min() < 250:
        return tex_bytes(scene.textures[name]), "image/png"
    b = _io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=84, optimize=True)
    return b.getvalue(), "image/jpeg"


def mesh_name(m):
    return f"1{m['phys']}_{m['name']}" if m["phys"] else m["name"]


def write(scene, path, extras=None, solo_usados=False, tex_cache=None, meshes=None):
    """solo_usados: solo los materiales que usa alguna malla (los pedazos del modo en vivo); tex_cache: dict
    nombre -> (bytes, mime) para no recomprimir en cada pedazo las texturas que se repiten."""
    meshes = scene.meshes() if meshes is None else meshes
    usados = {m["material"] for m in meshes} if solo_usados else None
    materiales = {k: v for k, v in scene.materials.items() if usados is None or k in usados}
    bin_parts, views, accessors = [], [], []
    offset = 0

    def add_view(data, target=None):
        nonlocal offset
        pad = (-len(data)) % 4
        data = data + b"\0" * pad
        v = {"buffer": 0, "byteOffset": offset, "byteLength": len(data) - pad}
        if target:
            v["target"] = target
        views.append(v)
        bin_parts.append(data)
        offset += len(data)
        return len(views) - 1

    def add_acc(arr, ctype, typ, target=None, minmax=False):
        vi = add_view(arr.tobytes(), target)
        a = {"bufferView": vi, "componentType": ctype, "count": int(len(arr)), "type": typ}
        if minmax:
            a["min"] = arr.min(0).tolist()
            a["max"] = arr.max(0).tolist()
        accessors.append(a)
        return len(accessors) - 1

    # texturas e imágenes
    tex_names = sorted({m["texture"] for m in materiales.values()})
    images, textures = [], []
    tex_index = {}
    for t in tex_names:
        if tex_cache is not None and t in tex_cache:
            data, mime = tex_cache[t]
        else:
            data, mime = _compact(scene, t)
            if tex_cache is not None:
                tex_cache[t] = (data, mime)
        vi = add_view(data)
        images.append({"bufferView": vi, "mimeType": mime, "name": t})
        textures.append({"source": len(images) - 1, "sampler": 0})
        tex_index[t] = len(textures) - 1
    mats, mat_index = [], {}
    for name, m in materiales.items():
        g = {"name": name, "pbrMetallicRoughness": {
            "baseColorTexture": {"index": tex_index[m["texture"]]},
            "metallicFactor": 0.0, "roughnessFactor": max(0.2, 1.0 - m["specular"])}}
        if m["alpha"]:
            g["alphaMode"] = "MASK"
            g["alphaCutoff"] = 0.5
        elif m.get("blend"):
            g["alphaMode"] = "BLEND"
        if m["emissive"]:
            g["emissiveFactor"] = [m["emissive"]] * 3
            g["emissiveTexture"] = {"index": tex_index[m["texture"]]}
        mats.append(g)
        mat_index[name] = len(mats) - 1

    gl_meshes, nodes = [], []
    for m in meshes:
        if scene.materials[m["material"]].get("kind") == "invisible":
            continue  # colisiones: solo existen en la física del juego
        pos = to_yup(m["pos"]).astype(np.float32)
        nrm = to_yup(m["nrm"]).astype(np.float32)
        uv = m["uv"].astype(np.float32)
        idx = m["faces"].astype(np.uint16 if len(pos) < 65535 else np.uint32).ravel()
        prim = {"attributes": {
            "POSITION": add_acc(pos, 5126, "VEC3", 34962, True),
            "NORMAL": add_acc(nrm, 5126, "VEC3", 34962),
            "TEXCOORD_0": add_acc(uv, 5126, "VEC2", 34962)},
            "indices": add_acc(idx, 5123 if idx.dtype == np.uint16 else 5125, "SCALAR", 34963),
            "material": mat_index[m["material"]]}
        gl_meshes.append({"name": mesh_name(m), "primitives": [prim]})
        nodes.append({"name": mesh_name(m), "mesh": len(gl_meshes) - 1})
    for name, p, fwd in scene.dummies:
        q = to_yup(p[None])[0]
        nodes.append({"name": name, "translation": q.tolist(),
                      "extras": {"forward": to_yup(np.array([[fwd[0], fwd[1], 0.0]]))[0].tolist()}})
    gltf = {
        "asset": {"version": "2.0", "generator": f"openlaps {__version__}", "copyright": CREDIT},
        "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": gl_meshes, "materials": mats,
        "textures": textures, "images": images,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": offset}],
        "extras": extras or {},
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    binblob = b"".join(bin_parts)
    total = 12 + 8 + len(js) + 8 + len(binblob)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(binblob), 0x004E4942))
        f.write(binblob)
    return total
