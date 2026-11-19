"""Contenedor de geometría de la escena (coordenadas locales: x este, y norte, z arriba).

Las mallas se trocean por celdas espaciales (200 m por defecto) además de por número de
vértices: así el juego descarta por distancia y por frustum lo que no se ve, como hace
cualquier pista bien construida.
"""
from collections import defaultdict

import numpy as np

MAX_VERTS = 65000  # Assetto Corsa usa índices de 16 bits por malla

# distancia máxima de dibujo (lodOut, en m) y sombra por grupo de malla; 0 = siempre
GROUP_LOD = {
    "vegetacion": 520, "vegetacion_lejos": 0, "senales": 600, "mobiliario": 650, "rayas": 0,
    "desgaste": 350, "bordes": 600, "topes": 400, "defensa": 900, "edificios": 2500,
    "edificios_det": 900, "col": 900, "portico": 800, "vallas": 600, "horizonte": 0, "barreras": 900, "isleta": 900, "tendido": 1800, "vallas_malla": 350,
}
# tamaño de celda del troceo espacial por grupo (m); el terreno ya viene en teselas
GROUP_CELL = {"horizonte": 2600.0, "terreno": 1e9, "vegetacion": 160.0}
NO_SHADOW = {"rayas", "desgaste", "bordes", "col", "horizonte", "vegetacion_lejos"}


def vertex_normals(pos, faces):
    n = np.zeros_like(pos)
    if len(faces) == 0:
        n[:, 2] = 1
        return n
    a, b, c = pos[faces[:, 0]], pos[faces[:, 1]], pos[faces[:, 2]]
    fn = np.cross(b - a, c - a)
    for k in range(3):
        np.add.at(n, faces[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    bad = ln[:, 0] < 1e-12
    n[bad] = (0, 0, 1)
    ln[bad] = 1
    return n / ln


class Piece:
    __slots__ = ("pos", "uv", "faces", "nrm")

    def __init__(self, pos, uv, faces, nrm):
        self.pos, self.uv, self.faces, self.nrm = pos, uv, faces, nrm


class Scene:
    def __init__(self):
        self.pieces = defaultdict(list)      # (grupo, material, física) -> [Piece]
        self.materials = {}                  # nombre -> dict
        self.textures = {}                   # nombre -> bytes PNG (o imagen PIL perezosa)
        self.images = {}                     # nombre -> imagen PIL (para DDS)
        self.dummies = []                    # (nombre, posición xyz, adelante xyz)
        self.lights = []                     # luces CSP: (pos xyz, dir xyz, color rgb, intensidad, rango)
        self.cameras = []                    # cámaras de TV: (pos, mira, in, out)
        self.meta = {}

    # -- materiales -------------------------------------------------------------
    def material(self, name, texture, alpha=False, specular=0.05, emissive=0.0, shadows=True,
                 kind=None, normal=None, blend=False, ambient=None, diffuse=None, spec_exp=None,
                 extra=None):
        """kind: plain | nm | multilayer | at | blend | invisible (shader de Assetto Corsa)."""
        if name not in self.materials:
            if kind is None:
                kind = "at" if alpha else ("blend" if blend else ("nm" if normal else "plain"))
            self.materials[name] = {"texture": texture, "alpha": alpha, "specular": specular,
                                    "emissive": emissive, "shadows": shadows, "kind": kind,
                                    "normal": normal, "blend": blend or kind in ("blend", "invisible"),
                                    "ambient": ambient, "diffuse": diffuse, "spec_exp": spec_exp,
                                    "extra": extra or {}}
        return name

    # -- geometría ----------------------------------------------------------------
    def add(self, group, material, pos, uv, faces, normals=None, phys=None):
        pos = np.asarray(pos, np.float64).reshape(-1, 3)
        if len(pos) == 0:
            return
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        faces = np.asarray(faces, np.int64).reshape(-1, 3)
        if len(faces) == 0:
            return
        nrm = vertex_normals(pos, faces) if normals is None else np.asarray(normals, np.float64).reshape(-1, 3)
        self.pieces[(group, material, phys)].append(Piece(pos, uv, faces, nrm))

    def dummy(self, name, pos, forward):
        self.dummies.append((name, np.asarray(pos, float), np.asarray(forward, float)))

    # -- salida: troceo espacial y por vértices ---------------------------------
    def meshes(self, cell=200.0):
        out = []
        keyf = lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")  # noqa: E731
        for (group, material, phys), pieces in sorted(self.pieces.items(), key=keyf):
            cells = defaultdict(list)
            gcell = GROUP_CELL.get(group, cell)
            for p in pieces:
                cen = p.pos[p.faces].mean(1)[:, :2]
                ci = np.floor(cen / gcell).astype(np.int64)
                keys = ci[:, 0] * 100003 + ci[:, 1]
                for k in np.unique(keys):
                    sel = keys == k
                    if sel.all():
                        cells[(int(ci[sel][0, 0]), int(ci[sel][0, 1]))].append(p)
                    else:
                        f = p.faces[sel]
                        used, inv = np.unique(f.ravel(), return_inverse=True)
                        cells[(int(ci[sel][0, 0]), int(ci[sel][0, 1]))].append(
                            Piece(p.pos[used], p.uv[used], inv.reshape(-1, 3), p.nrm[used]))
            for (cx, cy), plist in sorted(cells.items()):
                base = f"{group}_{material}_c{cx}_{cy}"
                chunk, nv, part = [], 0, 0
                for p in plist + [None]:
                    if p is None or (nv + len(p.pos) > MAX_VERTS and chunk):
                        if chunk:
                            out.append(_merge(base + (f"+{part}" if part else ""), group, material, phys, chunk))
                            part += 1
                        chunk, nv = [], 0
                        if p is None:
                            break
                    if len(p.pos) > MAX_VERTS:
                        for sub in _split_piece(p):
                            out.append(_merge(base + f"+{part}", group, material, phys, [sub]))
                            part += 1
                        continue
                    chunk.append(p)
                    nv += len(p.pos)
        return out

    def stats(self, ms=None):
        ms = self.meshes() if ms is None else ms
        return {"mallas": len(ms), "vertices": int(sum(len(m["pos"]) for m in ms)),
                "triangulos": int(sum(len(m["faces"]) for m in ms)),
                "materiales": len(self.materials), "texturas": len(self.textures)}


def _merge(name, group, material, phys, pieces):
    pos, uv, nrm, faces, off = [], [], [], [], 0
    for p in pieces:
        pos.append(p.pos)
        uv.append(p.uv)
        nrm.append(p.nrm)
        faces.append(p.faces + off)
        off += len(p.pos)
    return {"name": name, "group": group, "material": material, "phys": phys,
            "pos": np.vstack(pos), "uv": np.vstack(uv), "nrm": np.vstack(nrm), "faces": np.vstack(faces),
            "lod": GROUP_LOD.get(group, 0), "shadow": group not in NO_SHADOW}


def _split_piece(p):
    """Parte una pieza enorme en trozos con menos de MAX_VERTS vértices."""
    faces = p.faces
    start = 0
    while start < len(faces):
        end = min(len(faces), start + MAX_VERTS // 3)
        f = faces[start:end]
        used, inv = np.unique(f.ravel(), return_inverse=True)
        yield Piece(p.pos[used], p.uv[used], inv.reshape(-1, 3), p.nrm[used])
        start = end
