"""Inspecciona un .kn5 grande sin cargarlo en memoria (salta los bloques de vértices).

Imprime texturas, materiales (shader, propiedades, samplers), árbol de nodos resumido,
dummies con su matriz y estadísticas por grupo de malla. Opcionalmente vuelca las texturas.
Uso: python tools/kn5_inspect.py pista.kn5 [--texturas carpeta] [--json salida.json]
"""
import argparse
import json
import os
import re
import struct
from collections import defaultdict


class F:
    def __init__(self, path):
        self.f = open(path, "rb")

    def i(self):
        return struct.unpack("<i", self.f.read(4))[0]

    def fl(self, n=1):
        v = struct.unpack(f"<{n}f", self.f.read(4 * n))
        return v if n > 1 else v[0]

    def b(self):
        return self.f.read(1)[0]

    def s(self):
        n = self.i()
        return self.f.read(n).decode("utf-8", "replace")

    def skip(self, n):
        self.f.seek(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kn5")
    ap.add_argument("--texturas")
    ap.add_argument("--json")
    ap.add_argument("--muestras", type=int, default=0, help="vértices de muestra por malla de calzada")
    a = ap.parse_args()
    r = F(a.kn5)
    assert r.f.read(6) == b"sc6969"
    ver = r.i()
    extra = r.i() if ver > 5 else None
    print(f"versión {ver} extra {extra}")
    nt = r.i()
    texs = []
    for _ in range(nt):
        act = r.i()
        name = r.s()
        size = r.i()
        head = r.f.read(min(size, 128))
        r.skip(size - len(head))
        fmt = "PNG" if head[:4] == b"\x89PNG" else ("DDS" if head[:4] == b"DDS " else head[:4])
        info = ""
        if fmt == "DDS":
            h, w = struct.unpack_from("<II", head, 12)
            mips = struct.unpack_from("<I", head, 28)[0]
            four = head[84:88]
            info = f"{w}x{h} mips={mips} {four}"
        texs.append({"name": name, "size": size, "fmt": str(fmt), "info": info})
        if a.texturas:
            os.makedirs(a.texturas, exist_ok=True)
            pos = r.f.tell()
            r.f.seek(pos - size)
            with open(os.path.join(a.texturas, name.replace("/", "_")), "wb") as o:
                o.write(r.f.read(size))
    print(f"\n{nt} texturas; formatos:", dict((k, sum(1 for t in texs if t['fmt'] == k)) for k in {t['fmt'] for t in texs}))
    for t in texs[:400]:
        print(f"  {t['name']:<40} {t['size'] / 1e6:7.2f} MB {t['fmt']} {t['info']}")
    nm = r.i()
    mats = []
    for _ in range(nm):
        name, shader = r.s(), r.s()
        blend, atest = r.b(), r.b()
        depth = r.i()
        props = {}
        for _ in range(r.i()):
            pn = r.s()
            v = r.fl(10)
            props[pn] = [round(x, 4) for x in v if True][:1] if not any(v[1:]) else [round(x, 4) for x in v]
        samp = []
        for _ in range(r.i()):
            samp.append((r.s(), r.i(), r.s()))
        mats.append({"name": name, "shader": shader, "blend": blend, "atest": atest, "depth": depth, "props": props, "samplers": samp})
    print(f"\n{nm} materiales; shaders:", dict((k, sum(1 for m in mats if m['shader'] == k)) for k in {m['shader'] for m in mats}))
    for m in mats:
        print(f"  {m['name']:<34} {m['shader']:<22} blend={m['blend']} at={m['atest']} depth={m['depth']} "
              f"props={m['props']} samplers={[(s[0], s[2]) for s in m['samplers']]}")
    groups = defaultdict(lambda: [0, 0, 0])
    dummies = []
    meshes = []

    def node(depth, path):
        cls = r.i()
        name = r.s()
        nch = r.i()
        active = r.b()
        if cls == 1:
            m = r.fl(16)
            dummies.append((depth, name, nch, [round(x, 4) for x in m], path))
        elif cls in (2, 3):
            cast, vis, transp = r.b(), r.b(), r.b()
            if cls == 3:
                nb = r.i()
                for _ in range(nb):
                    r.s()
                    r.skip(64)
            nv = r.i()
            vsize = 44 if cls == 2 else 76
            sample = []
            if a.muestras and re.match(r"^\d", name):
                for k in range(min(a.muestras, nv)):
                    sample.append([round(x, 3) for x in struct.unpack("<11f", r.f.read(44))])
                    r.skip(vsize - 44)
                r.skip((nv - min(a.muestras, nv)) * vsize)
            else:
                r.skip(nv * vsize)
            ni = r.i()
            r.skip(ni * 2)
            mid = r.i()
            layer = r.i()
            lod_in, lod_out = r.fl(2)
            bs = r.fl(4)
            rend = r.b()
            key = re.sub(r"\d+", "#", name)
            g = groups[key]
            g[0] += 1
            g[1] += nv
            g[2] += ni // 3
            meshes.append({"name": name, "v": nv, "t": ni // 3, "mat": mats[mid]["name"] if 0 <= mid < len(mats) else mid,
                           "cast": cast, "vis": vis, "transp": transp, "layer": layer, "lod": [lod_in, lod_out],
                           "bs": [round(x, 1) for x in bs], "rend": rend, "path": path, "sample": sample})
        for _ in range(nch):
            node(depth + 1, path + "/" + name)

    node(0, "")
    print(f"\n{len(dummies)} dummies (primeros 80):")
    for d in dummies[:80]:
        print(f"  {'  ' * d[0]}{d[1]} hijos={d[2]} m={d[3]}")
    print(f"\n{len(meshes)} mallas, {sum(m['v'] for m in meshes):,} vértices, {sum(m['t'] for m in meshes):,} triángulos")
    print("grupos de mallas (nombre con dígitos -> #), ordenados por triángulos:")
    for k, (n, v, t) in sorted(groups.items(), key=lambda kv: -kv[1][2])[:120]:
        print(f"  {k:<44} n={n:<5} v={v:>10,} t={t:>10,}")
    phys = [m for m in meshes if re.match(r"^\d", m["name"])]
    print(f"\nmallas físicas: {len(phys)}")
    for m in phys[:40]:
        print(f"  {m['name']:<36} v={m['v']:<6} mat={m['mat']} vis={m['vis']} cast={m['cast']} lod={m['lod']} bs={m['bs']}")
        for s in m["sample"]:
            print("     ", s)
    if a.json:
        with open(a.json, "w") as o:
            json.dump({"textures": texs, "materials": mats, "dummies": dummies, "meshes": meshes}, o)
    print("leído hasta", r.f.tell(), "de", os.path.getsize(a.kn5))


if __name__ == "__main__":
    main()
