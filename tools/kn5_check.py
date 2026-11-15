"""Lee un .kn5 completo y verifica que la estructura sea consistente hasta el último byte."""
import struct
import sys

import numpy as np


class R:
    def __init__(self, data):
        self.d, self.o = data, 0

    def take(self, n):
        b = self.d[self.o:self.o + n]
        if len(b) != n:
            raise EOFError(f"fin inesperado en {self.o}")
        self.o += n
        return b

    def i(self):
        return struct.unpack("<i", self.take(4))[0]

    def f(self, n=1):
        v = struct.unpack(f"<{n}f", self.take(4 * n))
        return v if n > 1 else v[0]

    def b(self):
        return self.take(1)[0]

    def s(self):
        return self.take(self.i()).decode("utf-8")


def check(path):
    r = R(open(path, "rb").read())
    assert r.take(6) == b"sc6969", "firma"
    ver = r.i()
    if ver > 5:
        r.i()
    nt = r.i()
    tex = []
    for _ in range(nt):
        r.i()
        name = r.s()
        size = r.i()
        data = r.take(size)
        assert data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"DDS ", f"textura {name}: formato desconocido"
        tex.append(name)
    nm = r.i()
    mats = []
    for _ in range(nm):
        name, shader = r.s(), r.s()
        r.b(), r.b(), r.i()
        for _ in range(r.i()):
            r.s()
            r.f(10)
        for _ in range(r.i()):
            r.s()
            r.i()
            t = r.s()
            assert t in tex, f"material {name} usa textura inexistente {t}"
        mats.append((name, shader))
    stats = {"mallas": 0, "dummies": 0, "vertices": 0, "tris": 0, "fisicas": {}}

    def node(depth=0):
        cls = r.i()
        name = r.s()
        nch = r.i()
        r.b()
        if cls == 1:
            m = np.array(r.f(16)).reshape(4, 4)
            if name.startswith("AC_"):
                stats["dummies"] += 1
                stats.setdefault("dummy_list", []).append((name, m[3, :3].round(1).tolist()))
        elif cls == 2:
            r.b(), r.b(), r.b()
            nv = r.i()
            v = np.frombuffer(r.take(nv * 44), np.float32).reshape(nv, 11)
            ni = r.i()
            idx = np.frombuffer(r.take(ni * 2), np.uint16)
            assert idx.max() < nv, f"{name}: índice fuera de rango"
            assert np.isfinite(v).all(), f"{name}: NaN/inf en vértices"
            mid = r.i()
            assert 0 <= mid < len(mats), f"{name}: material {mid}"
            r.i()
            r.f(2)
            r.f(4)
            r.b()
            stats["mallas"] += 1
            stats["vertices"] += nv
            stats["tris"] += ni // 3
            if name[0].isdigit():
                key = name[1:].split("_")[0]
                stats["fisicas"][key] = stats["fisicas"].get(key, 0) + 1
        else:
            raise ValueError(f"clase de nodo desconocida {cls}")
        for _ in range(nch):
            node(depth + 1)

    node()
    assert r.o == len(r.d), f"sobran {len(r.d) - r.o} bytes"
    print(f"KN5 OK: versión {ver}, {nt} texturas, {nm} materiales, {stats['mallas']} mallas, "
          f"{stats['vertices']:,} vértices, {stats['tris']:,} triángulos")
    print("  mallas físicas por superficie:", stats["fisicas"])
    print("  dummies:", ", ".join(n for n, _ in stats.get("dummy_list", [])))


if __name__ == "__main__":
    check(sys.argv[1])
