"""Edificios de Google 3D en Assetto Corsa (opcional): la malla fotogramétrica de Google, vía Cesium ion, dibujada
DENTRO de AC mientras conduces, sobre una pista hecha con OpenLaps.

Este proceso pide las teselas alrededor del coche, las pasa a coordenadas de la pista y las sirve desde la memoria a
un script de pista de CSP (`google3d_ac.lua`), que crea las mallas en vivo con `createMesh`.

Reglas que se respetan (condiciones de Google y de Cesium ion):
- solo en memoria: ni las teselas ni las texturas se escriben en disco; al cerrarlo no queda nada;
- la malla de Google es solo visual: la física sigue siendo la pista de OpenLaps y de ella no se deriva nada;
- sobre la calzada y las banquetas de OpenLaps se quitan los triángulos de Google al dibujar;
- los créditos de Google y Cesium se muestran en AC.

Hace falta un token de Cesium ion (cuenta gratis, solo correo: https://ion.cesium.com/signup) con el activo
"Google Photorealistic 3D Tiles" añadido a la cuenta.

    python -m openlaps.google3d <carpeta de la pista de AC> --glb salida/<nombre>.glb --token <token>
        [--radio 500] [--puerto 8772]
    python -m openlaps.google3d <pista semilla del modo en vivo> --vivo salida/vivo/<nombre> --token <token>
    python -m openlaps.google3d <carpeta de la pista de AC> --instalar [--puerto 8772]   # pone el script en AC
    python -m openlaps.google3d <carpeta de la pista de AC> --quitar                      # lo quita
"""

import argparse
import collections
import concurrent.futures
import hashlib
import http.server
import json
import math
import os
import re
import struct
import threading
import time
import urllib.parse
import urllib.request

import numpy as np
from numba import njit
from pyproj import Transformer
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ACTIVO_GOOGLE = 2275207   # Google Photorealistic 3D Tiles en Cesium ion
MAX_VERTICES = 65535      # AC: índices de 16 bits
CREDITOS = "Google · Cesium ion · Calles: OpenLaps (OpenStreetMap ODbL)"
PUERTO = 8772
LUA = "google3d_ac.lua"
MARCA = "; OpenLaps: edificios de Google 3D (opcional)"
UMBRAL_MOVIDO = 60.0   # m que se mueve el coche antes de recalcular las teselas
FRANJA = 3.5           # m alrededor de la calzada donde se quitan coches y suelo de Google (no fachadas)
CELDA = 0.5            # rejilla de la calzada de OpenLaps (m)


def log(texto):
    print(time.strftime("%H:%M:%S"), texto, flush=True)


# ============================================================ instalar el script en la pista de AC
def instalar(carpeta_pista, puerto=PUERTO):
    """Copia el script de pista a `extension\\` y lo registra en `ext_config.ini` como [SCRIPT_n]."""
    ext = os.path.join(carpeta_pista, "extension")
    os.makedirs(ext, exist_ok=True)
    fuente = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), LUA), encoding="utf-8").read()
    with open(os.path.join(ext, LUA), "w", encoding="utf-8", newline="\n") as f:
        f.write(fuente.replace("http://127.0.0.1:8772", f"http://127.0.0.1:{int(puerto)}"))
    ini = os.path.join(ext, "ext_config.ini")
    texto = _sin_registro(open(ini, encoding="utf-8").read() if os.path.isfile(ini) else "")
    usados = {int(n) for n in re.findall(r"^\[SCRIPT_(\d+)\]", texto, re.M)}
    n = next(i for i in range(100) if i not in usados)
    texto = texto.rstrip("\n") + f"\n\n{MARCA}\n[SCRIPT_{n}]\nSCRIPT={LUA}\n"
    open(ini, "w", encoding="utf-8").write(texto)
    return n


def quitar(carpeta_pista):
    """Deja la pista como la generó OpenLaps (sin el script). Devuelve si había algo que quitar."""
    ext = os.path.join(carpeta_pista, "extension")
    ini = os.path.join(ext, "ext_config.ini")
    habia = False
    if os.path.isfile(ini):
        texto = open(ini, encoding="utf-8").read()
        nuevo = _sin_registro(texto)
        if nuevo != texto:
            open(ini, "w", encoding="utf-8").write(nuevo)
            habia = True
    if os.path.isfile(os.path.join(ext, LUA)):
        os.remove(os.path.join(ext, LUA))
        habia = True
    return habia


def instalado(carpeta_pista):
    return os.path.isfile(os.path.join(carpeta_pista, "extension", LUA))


def _sin_registro(texto):
    return re.sub(r"\n*" + re.escape(MARCA) + r"\n\[SCRIPT_\d+\]\nSCRIPT=" + re.escape(LUA) + r"\n?", "\n", texto)


# ============================================================ Cesium ion → Google 3D Tiles
class Google:
    def __init__(self, token):
        ep = json.loads(self._get(f"https://api.cesium.com/v1/assets/{ACTIVO_GOOGLE}/endpoint?access_token={token}"))
        self.raiz_url = ep["options"]["url"]
        self.llave = urllib.parse.parse_qs(urllib.parse.urlparse(self.raiz_url).query)["key"][0]
        self.sesion = None
        self.pedidas = 0
        self.subarboles = {}

    @staticmethod
    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "OpenLaps"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def url(self, uri, base):
        u = urllib.parse.urlparse(urllib.parse.urljoin(base, uri))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        q["key"] = self.llave
        if "session" in q:
            self.sesion = q["session"]
        elif self.sesion:
            q["session"] = self.sesion
        return u._replace(query=urllib.parse.urlencode(q)).geturl()

    def get(self, url):
        self.pedidas += 1
        return self._get(url)

    def subarbol(self, url):
        """Raíz de un subárbol JSON, guardada en memoria por su ruta (al moverse se vuelven a usar). Google da rutas
        opacas que cambian cada vez que se pide el root.json: por eso también se guarda la raíz."""
        ruta = urllib.parse.urlparse(url).path
        if ruta not in self.subarboles:
            self.subarboles[ruta] = json.loads(self.get(url))["root"]
        return self.subarboles[ruta]


def distancia(bv, punto):
    """Distancia (m) del punto a la caja orientada de 3D Tiles (0 si está dentro)."""
    b = np.array(bv["box"], float)
    c, ejes = b[:3], b[3:].reshape(3, 3)
    d = punto - c
    fuera = 0.0
    for a in ejes:
        largo = float(np.linalg.norm(a))
        if largo == 0:
            continue
        exceso = abs(float(d @ a) / largo) - largo
        if exceso > 0:
            fuera += exceso * exceso
    return math.sqrt(fuera)


def seleccionar(google, centro, radio, error_por_metro, max_teselas):
    """Teselas glb a dibujar alrededor de `centro` (ECEF): como Cesium, se baja de nivel mientras el error
    geométrico sea grande para su distancia (refinamiento REPLACE). Por niveles, con peticiones en paralelo."""
    elegidas = []
    frente = [(google.subarbol(google.raiz_url), google.raiz_url)]

    def expandir(item):
        tile, base = item
        d = distancia(tile["boundingVolume"], centro)
        if d > radio:
            return [], []
        uri = tile.get("content", {}).get("uri")
        if uri and ".json" in uri:
            url = google.url(uri, base)
            return [], [(google.subarbol(url), url)]
        hijos = tile.get("children", [])
        suficiente = tile["geometricError"] <= max(0.5, d * error_por_metro)
        if uri and ".glb" in uri and (suficiente or not hijos):
            return [(google.url(uri, base), d)], []
        return [], [(h, base) for h in hijos]

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        while frente and len(elegidas) < max_teselas:
            nuevos = []
            for sel, sig in ex.map(expandir, frente):
                elegidas += sel
                nuevos += sig
            frente = nuevos
    # las más cercanas primero: son las que se ven y las que se publican antes
    return [u for u, _ in sorted(elegidas[:max_teselas], key=lambda e: e[1])]


# ============================================================ glb de Google → mallas en coordenadas de AC
def leer_glb(datos):
    largo = struct.unpack_from("<I", datos, 12)[0]
    gltf = json.loads(datos[20:20 + largo])
    return gltf, memoryview(datos)[20 + largo + 8:]


def accesor(gltf, binario, i):
    a = gltf["accessors"][i]
    v = gltf["bufferViews"][a["bufferView"]]
    tipo = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[a["componentType"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    inicio = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    elem = n * np.dtype(tipo).itemsize
    paso = v.get("byteStride") or elem
    if paso == elem:
        arr = np.frombuffer(binario, dtype=tipo, count=a["count"] * n, offset=inicio).reshape(-1, n)
    else:  # intercalado
        crudo = np.frombuffer(binario, dtype=np.uint8, count=paso * (a["count"] - 1) + elem, offset=inicio)
        arr = np.lib.stride_tricks.as_strided(crudo, shape=(a["count"], elem), strides=(paso, 1)).copy().view(tipo)
    arr = np.array(arr, dtype=np.float64 if tipo == np.float32 else tipo)
    if a.get("normalized"):
        arr = arr / np.iinfo(tipo).max
    return arr if n > 1 else arr[:, 0]


def primitivas(datos):
    """(posiciones ECEF, uv, índices, jpeg) de cada primitiva de una tesela."""
    gltf, binario = leer_glb(datos)
    salida = []

    def nodo(i, padre):
        nd = gltf["nodes"][i]
        m = np.array(nd.get("matrix", np.eye(4).flatten(order="F")), float).reshape(4, 4, order="F")
        m = padre @ m
        if "mesh" in nd:
            for prim in gltf["meshes"][nd["mesh"]]["primitives"]:
                ext = prim.get("extensions", {}).get("KHR_draco_mesh_compression")
                if ext:
                    import DracoPy
                    vista = gltf["bufferViews"][ext["bufferView"]]
                    o = vista.get("byteOffset", 0)
                    malla = DracoPy.decode(bytes(binario[o:o + vista["byteLength"]]))
                    pos, idx = np.asarray(malla.points, float), np.asarray(malla.faces).reshape(-1)
                    uv = np.asarray(malla.tex_coord, float) if malla.tex_coord is not None else np.zeros((len(pos), 2))
                else:
                    pos = accesor(gltf, binario, prim["attributes"]["POSITION"])
                    uv = accesor(gltf, binario, prim["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in prim["attributes"] \
                        else np.zeros((len(pos), 2))
                    idx = accesor(gltf, binario, prim["indices"]).astype(np.int64)
                pos = (m @ np.c_[pos, np.ones(len(pos))].T).T[:, :3]
                # 3D Tiles: el glTF va con Y arriba; a ECEF (Z arriba): (x, y, z) -> (x, -z, y)
                pos = np.c_[pos[:, 0], -pos[:, 2], pos[:, 1]]
                jpeg = None
                mat = gltf["materials"][prim["material"]] if "material" in prim else {}
                tex = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture")
                if tex is not None:
                    imagen = gltf["images"][gltf["textures"][tex["index"]]["source"]]
                    vista = gltf["bufferViews"][imagen["bufferView"]]
                    o = vista.get("byteOffset", 0)
                    jpeg = bytes(binario[o:o + vista["byteLength"]])
                salida.append((pos, uv, idx.reshape(-1, 3), jpeg))
        for h in nd.get("children", []):
            nodo(h, m)

    for i in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        nodo(i, np.eye(4))
    return salida


class Marco:
    """ECEF → coordenadas de la pista de AC (x este, y arriba, z sur), con el desfase de altura de Google (≈ la
    ondulación del geoide: Google da alturas elipsoidales y OpenLaps sobre el nivel del mar)."""

    def __init__(self, lat0, lon0, epsg):
        self.a_geo = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)
        self.a_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        self.a_ll = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        self.x0, self.y0 = self.a_utm.transform(lon0, lat0)
        self.desfase = None

    def ac(self, ecef):
        lon, lat, h = self.a_geo.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
        e, n = self.a_utm.transform(lon, lat)
        return np.c_[e - self.x0, h - (self.desfase or 0.0), -(n - self.y0)]

    def lonlat(self, x, z):
        return self.a_ll.transform(self.x0 + x, self.y0 - z)

    @staticmethod
    def ecef(lat, lon, h):
        a, e2 = 6378137.0, 6.69437999014e-3
        la, lo = math.radians(lat), math.radians(lon)
        n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
        return np.array([(n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
                         (n * (1 - e2) + h) * math.sin(la)])


def normales(pos, tris):
    caras = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    n = np.zeros_like(pos)
    for k in range(3):
        np.add.at(n, tris[:, k], caras)
    largo = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(largo > 0, largo, 1)


def empacar(pos, uv, tris):
    """Binario para el script: uint32 nv, uint32 ni, nv × (pos3, normal3, uv2) float32, ni × uint16. ECEF → (este,
    arriba, -norte) es una rotación: el orden de glTF se conserva (invertirlo deja ver las caras de adentro)."""
    usados, inv = np.unique(tris.reshape(-1), return_inverse=True)
    pos, uv, tris = pos[usados], uv[usados], inv.reshape(-1, 3)
    v = np.c_[pos, normales(pos, tris), uv].astype(np.float32)
    i = tris.reshape(-1).astype(np.uint16)
    return struct.pack("<II", len(v), len(i)) + v.tobytes() + i.tobytes()


def partir(pos, uv, tris):
    """Trozos de menos de MAX_VERTICES vértices (AC indexa con 16 bits)."""
    if len(pos) <= MAX_VERTICES:
        return [(pos, uv, tris)]
    trozos, actual, vistos = [], [], set()
    for t in tris:
        nuevos = {int(x) for x in t} - vistos
        if len(vistos) + len(nuevos) > MAX_VERTICES - 3:
            trozos.append(np.array(actual))
            actual, vistos = [], set()
            nuevos = {int(x) for x in t}
        actual.append(t)
        vistos |= nuevos
    trozos.append(np.array(actual))
    return [(pos, uv, t) for t in trozos]


# ============================================================ la calzada de OpenLaps, en rejilla
@njit(cache=True)
def _rasterizar(tris, x0, z0, nx, nz, celda, alto, cubre):
    for t in range(tris.shape[0]):
        ax, ay, az = tris[t, 0, 0], tris[t, 0, 1], tris[t, 0, 2]
        bx, by, bz = tris[t, 1, 0], tris[t, 1, 1], tris[t, 1, 2]
        cx, cy, cz = tris[t, 2, 0], tris[t, 2, 1], tris[t, 2, 2]
        d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
        if abs(d) < 1e-9:
            continue
        i0 = max(0, int((min(ax, bx, cx) - x0) / celda))
        i1 = min(nx - 1, int((max(ax, bx, cx) - x0) / celda) + 1)
        j0 = max(0, int((min(az, bz, cz) - z0) / celda))
        j1 = min(nz - 1, int((max(az, bz, cz) - z0) / celda) + 1)
        for i in range(i0, i1 + 1):
            px = x0 + (i + 0.5) * celda
            for j in range(j0, j1 + 1):
                pz = z0 + (j + 0.5) * celda
                l1 = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / d
                l2 = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / d
                l3 = 1.0 - l1 - l2
                if l1 < -0.01 or l2 < -0.01 or l3 < -0.01:
                    continue
                y = l1 * ay + l2 * by + l3 * cy
                cubre[i, j] = True
                if y > alto[i, j]:
                    alto[i, j] = y


class Calles:
    """Dónde están la calzada y las banquetas de OpenLaps y a qué altura, en una rejilla de 0,5 m (coordenadas de AC).
    Sale del .glb de la pista: en lugar de polígonos, máscaras (consultarlas es instantáneo)."""

    def __init__(self, ruta_glb, avisar=True):
        with open(ruta_glb, "rb") as f:
            datos = f.read()
        gltf, binario = leer_glb(datos)
        tris, tris_calz, tris_edif = [], [], []
        self.salida = (0.0, 0.0)
        for nd in gltf["nodes"]:
            if nd.get("name") == "AC_START_0":
                t = nd.get("translation", [0, 0, 0])
                self.salida = (float(t[0]), float(t[2]))
            if "mesh" not in nd:
                continue
            m = gltf["meshes"][nd["mesh"]]
            nombre = m.get("name") or nd.get("name") or ""
            es_calle = any(k in nombre for k in ("calzada", "banqueta", "isleta"))
            es_edif = "edificios" in nombre
            if not (es_calle or es_edif):
                continue
            tr = np.array(nd.get("translation", [0, 0, 0]), float)
            for prim in m["primitives"]:
                p = accesor(gltf, binario, prim["attributes"]["POSITION"]) + tr
                idx = accesor(gltf, binario, prim["indices"]).astype(np.int64).reshape(-1, 3)
                if es_edif:
                    tris_edif.append(p[idx])
                    continue
                tris.append(p[idx])
                if "calzada" in nombre:
                    tris_calz.append(p[idx])
        self.materiales = {mt.get("name") for mt in gltf.get("materials", [])}
        if not tris:
            raise SystemExit(f"{ruta_glb}: sin calzada (¿es una pista de OpenLaps?)")
        T = np.concatenate(tris)
        self.x0, self.z0 = T[:, :, 0].min() - 10, T[:, :, 2].min() - 10
        nx = int((T[:, :, 0].max() + 10 - self.x0) / CELDA) + 2
        nz = int((T[:, :, 2].max() + 10 - self.z0) / CELDA) + 2
        alto = np.full((nx, nz), -1e9)
        cubre = np.zeros((nx, nz), bool)
        _rasterizar(T, self.x0, self.z0, nx, nz, CELDA, alto, cubre)
        disco = lambda r: np.hypot(*np.mgrid[-r:r + 1, -r:r + 1]) <= r  # noqa: E731

        def mascara(lista):
            m = np.zeros((nx, nz), bool)
            if lista:
                _rasterizar(np.concatenate(lista), self.x0, self.z0, nx, nz, CELDA, np.full((nx, nz), -1e9), m)
            return m
        # donde se quita TODO lo de Google: la calzada encogida 2 m y lejos de las plantas de los edificios. La
        # malla de Google es una cáscara hueca y las fachadas caen a menudo sobre la banqueta: recortar ahí (o
        # junto a un edificio) se llevaba la fachada y quedaban techos y astillas sueltas
        calz = mascara(tris_calz)
        edif = mascara(tris_edif)
        self.via = ndimage.binary_erosion(calz, disco(int(2.0 / CELDA))) &             ~ndimage.binary_dilation(edif, disco(int(1.5 / CELDA)))
        # alrededor (banquetas incluidas): solo lo que queda entero por debajo de 2 m (coches, gente, suelo)
        self.franja = ndimage.binary_dilation(cubre, disco(int(FRANJA / CELDA))) & ~self.via
        self.limpieza = ndimage.binary_dilation(cubre, disco(int((FRANJA + 2.0) / CELDA)))
        # altura del suelo en toda la rejilla: la de la calzada más cercana
        _, (ii, jj) = ndimage.distance_transform_edt(~cubre, return_indices=True)
        self.suelo_r = alto[ii, jj]
        self.nx, self.nz = nx, nz
        if avisar:
            log(f"calzada de OpenLaps: {cubre.sum() * CELDA * CELDA:,.0f} m²")

    def _ij(self, x, z):
        i = np.clip(((np.asarray(x) - self.x0) / CELDA).astype(np.int64), 0, self.nx - 1)
        j = np.clip(((np.asarray(z) - self.z0) / CELDA).astype(np.int64), 0, self.nz - 1)
        return i, j

    def en(self, mascara, x, z):
        i, j = self._ij(x, z)
        return mascara[i, j]

    def suelo(self, x, z):
        i, j = self._ij(x, z)
        return self.suelo_r[i, j]


class CallesVivo:
    """Lo mismo para el modo en vivo: la calzada sale de los pedazos (p_i_j.glb) que el servicio va generando
    alrededor del coche. Se leen los cercanos al coche y se vuelven a mirar en cada actualización (van apareciendo).
    Donde aún no hay pedazo no se quita nada de Google y la altura es la del pedazo leído más cercano."""
    L = 256.0

    def __init__(self, carpeta):
        self.carpeta = carpeta
        self.pedazos = {}            # (i, j) -> Calles
        self.salida = (0.0, 0.0)
        self.materiales = set()

    def cargar_cerca(self, x, z, radio):
        L = self.L
        n = int(math.ceil(radio / L)) + 1
        ci, cj = int(math.floor(x / L + 0.5)), int(math.floor(-z / L + 0.5))
        nuevos = 0
        for i in range(ci - n, ci + n + 1):
            for j in range(cj - n, cj + n + 1):
                if (i, j) in self.pedazos:
                    continue
                f = os.path.join(self.carpeta, f"p_{i}_{j}.glb")
                if os.path.isfile(f):
                    try:
                        c = Calles(f, avisar=False)
                    except (SystemExit, OSError, ValueError, KeyError):
                        continue            # pedazo sin calles (campo, agua) o a medio escribir
                    self.pedazos[(i, j)] = c
                    self.materiales |= c.materiales
                    nuevos += 1
        if nuevos:
            log(f"calzada: {nuevos} pedazos nuevos ({len(self.pedazos)} en total)")

    def _claves(self, x, z):
        x, z = np.asarray(x, float), np.asarray(z, float)
        return np.floor(x / self.L + 0.5).astype(np.int64), np.floor(-z / self.L + 0.5).astype(np.int64)

    def _consulta(self, que, x, z, mascara=None):
        x, z = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(z, float))
        out = np.zeros(len(x), bool) if que == "en" else np.full(len(x), np.nan)
        if not self.pedazos:
            return out if que == "en" else np.zeros(len(x))
        ki, kj = self._claves(x, z)
        claves = list(self.pedazos)
        cent = np.array([(i * self.L, -j * self.L) for i, j in claves])
        for a, b in set(zip(ki.tolist(), kj.tolist())):
            sel = (ki == a) & (kj == b)
            c = self.pedazos.get((a, b))
            if c is None:
                if que == "en":
                    continue
                # sin pedazo: la altura del pedazo cargado más cercano (fuera de su rejilla se recorta al borde)
                k = int(np.argmin(np.hypot(cent[:, 0] - a * self.L, cent[:, 1] + b * self.L)))
                c = self.pedazos[claves[k]]
            if que == "en":
                out[sel] = c.en(getattr(c, mascara), x[sel], z[sel])
            else:
                out[sel] = c.suelo(x[sel], z[sel])
        return out

    # misma interfaz que Calles (las máscaras se piden por nombre)
    via, franja, limpieza = "via", "franja", "limpieza"

    def en(self, mascara, x, z):
        return self._consulta("en", x, z, mascara)

    def suelo(self, x, z):
        return self._consulta("suelo", x, z)


# ============================================================ mundo en memoria: sigue al coche
def tid(url):
    """Identificador estable de una tesela (su ruta, sin llave ni sesión)."""
    return hashlib.sha1(urllib.parse.urlparse(url).path.encode()).hexdigest()[:12]


MATERIALES_BASE = ("guarnicion", "puente", "poste", "tope", "fantasma")  # ksPerPixel opacos de OpenLaps


class Mundo:
    """Qué teselas tocan alrededor del coche y sus mallas ya convertidas. Todo en memoria."""

    def __init__(self, args):
        self.args = args
        with open(os.path.join(args.pista, "openlaps_geo.json"), encoding="utf-8") as f:
            g = json.load(f)
        self.marco = Marco(g["lat0"], g["lon0"], g.get("epsg") or (32600 if g["lat0"] >= 0 else 32700)
                           + int((g["lon0"] + 180) // 6) + 1)
        if getattr(args, "vivo", None):
            # modo en vivo: la semilla trae la biblioteca con todos los materiales de OpenLaps
            self.calles = CallesVivo(args.vivo)
            self.material = MATERIALES_BASE[0]
        else:
            self.calles = Calles(args.glb)
            self.material = next((m for m in MATERIALES_BASE if m in self.calles.materiales), None)
            if not self.material:
                raise SystemExit("la pista no tiene ningún material base conocido (" + ", ".join(MATERIALES_BASE) + ")")
        self.google = Google(args.token)
        self.teselas = collections.OrderedDict()   # tid -> [ids de malla]  (las más recientes al final)
        self.mallas = {}                             # id -> {"geo", "tex"}
        self.centro_malla = {}                       # id -> (x, z) en la pista
        self.completo = []                           # mallas del último conjunto entero
        # versiones numeradas desde la hora de arranque: si se reinicia, AC no confunde la nueva v1 con la anterior
        self.version, self.publicadas = int(time.time()) % 10_000_000 * 100, []
        self.inicial = self.version
        self.foco, self.centro = self.calles.salida, None
        self.cerrojo = threading.Lock()
        self.despertar = threading.Event()
        self.despertar.set()
        self.error = None

    def prevista(self):
        """Dónde estará el coche en ~6 s (como mucho 150 m por delante)."""
        vx, vz = getattr(self, "vel", (0.0, 0.0))
        ade = min(1.0, 150.0 / max(math.hypot(vx, vz) * 6.0, 1e-6))
        return self.foco[0] + vx * 6.0 * ade, self.foco[1] + vz * 6.0 * ade

    def mover_foco(self, x, z):
        # velocidad del coche (suavizada) para elegir las teselas donde VA a estar: entre bajar y convertir pasan
        # ~7 s y a 70 km/h lo que quedaba al lado se había elegido como "lejano" (poco detalle, derretido)
        ahora = time.time()
        if getattr(self, "t_foco", None) and ahora - self.t_foco > 0.2:
            dt = ahora - self.t_foco
            vx, vz = (x - self.foco[0]) / dt, (z - self.foco[1]) / dt
            if math.hypot(vx, vz) < 80:
                ox, oz = getattr(self, "vel", (0.0, 0.0))
                self.vel = (0.7 * ox + 0.3 * vx, 0.7 * oz + 0.3 * vz)
        self.t_foco = ahora
        self.foco = (x, z)
        px, pz = self.prevista()
        if self.centro is None or math.hypot(px - self.centro[0], pz - self.centro[1]) > UMBRAL_MOVIDO:
            self.despertar.set()
        elif isinstance(self.calles, CallesVivo) and time.time() - getattr(self, "t_revisado", 0) > 15:
            # los pedazos de alrededor van llegando: cada 15 s se vuelve a recortar con los nuevos
            # (solo para las teselas que se bajen desde ahora: volver a recortar todas costaba 30 s cada vez)
            self.t_revisado = time.time()
            self.calles.cargar_cerca(x, z, self.args.radio)

    def bucle(self):
        while True:
            self.despertar.wait()
            self.despertar.clear()
            try:
                self.actualizar(*self.prevista())
                self.error = None
            except Exception as e:  # noqa: BLE001 - que un fallo de red no tumbe el proceso
                self.error = repr(e)
                log(f"error al actualizar: {e!r}")
                time.sleep(2)
                self.despertar.set()

    def actualizar(self, x, z):
        t0 = time.time()
        self.centro = (x, z)
        if isinstance(self.calles, CallesVivo):
            self.calles.cargar_cerca(x, z, self.args.radio)
        lon, lat = self.marco.lonlat(x, z)
        h = float(np.ravel(self.calles.suelo(x, z))[0]) + (self.marco.desfase or 50.0) + 1.5
        urls = seleccionar(self.google, self.marco.ecef(lat, lon, h), self.args.radio, self.args.error, self.args.max)
        nuevas = [u for u in urls if tid(u) not in self.teselas]
        vigentes = list(dict.fromkeys(tid(u) for u in urls))
        # lo anterior que se sigue enseñando mientras carga lo nuevo: solo el último conjunto COMPLETO y solo lo que
        # sigue dentro del radio (antes se iban acumulando las tandas a medias: 2.300 mallas, viejas encima de nuevas)
        anteriores = [i for i in getattr(self, "completo", [])
                      if math.hypot(*(np.subtract(self.centro_malla.get(i, (1e9, 1e9)), (x, z)))) < self.args.radio]
        # por tandas, de la más cercana a la más lejana, publicando cada tanda: antes no salía nada hasta tener
        # las ~300 teselas (20-30 s) y a 70 km/h el coche ya iba 500 m más allá. Mientras tanto se siguen
        # publicando también las mallas anteriores (si no, AC quitaría las viejas antes de tener las nuevas)
        TANDA = 24
        for k0 in range(0, max(len(nuevas), 1), TANDA):
            grupo = nuevas[k0:k0 + TANDA]
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                crudas = list(ex.map(self.google.get, grupo))
            prims = [primitivas(d) for d in crudas]
            if self.marco.desfase is None and prims:
                self.marco.desfase = self.medir_desfase([p for ps in prims for p in ps], (x, z))
                log(f"desfase de altura Google - OpenLaps: {self.marco.desfase:.2f} m")
            # en un solo hilo: los transformadores de pyproj no se pueden compartir entre hilos
            convertidas = [self.convertir(p) for p in prims]
            ultima = k0 + TANDA >= len(nuevas)
            with self.cerrojo:
                for u, mallas in zip(grupo, convertidas):
                    ids = []
                    for k, m in enumerate(mallas):
                        i = f"{self.inicial}_{tid(u)}_{k}"
                        self.mallas[i] = m
                        self.centro_malla[i] = m.pop("centro")
                        ids.append(i)
                    self.teselas[tid(u)] = ids
                listas = [i for t in vigentes if t in self.teselas for i in self.teselas[t]]
                self.publicadas = listas if ultima else list(dict.fromkeys(listas + anteriores))
                if ultima:
                    self.completo = listas
                self.version += 1
            if not ultima and math.hypot(*np.subtract(self.prevista(), (x, z))) > 2 * UMBRAL_MOVIDO:
                break                    # el coche ya se fue lejos: mejor empezar otra selección desde donde está
        with self.cerrojo:
            for t in vigentes:
                if t in self.teselas:
                    self.teselas.move_to_end(t)
            while len(self.teselas) > self.args.cache:      # memoria: fuera las más viejas que ya no se usan
                t, ids = next(iter(self.teselas.items()))
                if t in vigentes:
                    break
                self.teselas.popitem(last=False)
                for i in ids:
                    self.mallas.pop(i, None)
                    self.centro_malla.pop(i, None)
        if math.hypot(*np.subtract(self.prevista(), (x, z))) > UMBRAL_MOVIDO:
            self.despertar.set()
        log(f"v{self.version - self.inicial}: {len(vigentes)} teselas ({len(nuevas)} nuevas, "
            f"{self.google.pedidas} peticiones), {len(self.publicadas)} mallas, {time.time() - t0:.1f} s")

    def medir_desfase(self, prims, centro, radio=150.0):
        """Altura de la superficie de Google menos la de la calzada de OpenLaps cerca del coche (percentil 30:
        coches y árboles solo pueden subirla)."""
        self.marco.desfase = 0.0
        difs = []
        for pos_ecef, _, tris, _ in prims:
            p = self.marco.ac(pos_ecef)
            c = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3
            cerca = (np.hypot(c[:, 0] - centro[0], c[:, 2] - centro[1]) < radio) & self.calles.en(self.calles.via, c[:, 0], c[:, 2])
            if cerca.any():
                difs += list(c[cerca, 1] - self.calles.suelo(c[cerca, 0], c[cerca, 2]))
        self.marco.desfase = None
        return float(np.percentile(difs, 30)) if difs else 50.0

    def convertir(self, prims):
        """Mallas empacadas de una tesela: en coordenadas de AC y sin lo que cae sobre la calle de OpenLaps."""
        salida = []
        cl = self.calles
        for pos_ecef, uv, tris, jpeg in prims:
            p = self.marco.ac(pos_ecef)
            c = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3
            # sobre la calzada y las banquetas se va todo (si no, árboles derretidos y "telarañas" entre edificios
            # hacen túneles)
            en_via = cl.en(cl.via, c[:, 0], c[:, 2])
            # franja junto a la calzada: solo lo que queda entero por debajo de 2 m (suelo, coches estacionados); la
            # base de las fachadas de Google viene deformada y si se quita quedan planos colgando
            en_franja = cl.en(cl.franja, c[:, 0], c[:, 2]) & ~en_via
            if en_franja.any():
                k = np.nonzero(en_franja)[0]
                alto = np.maximum.reduce([p[tris[k, n], 1] for n in range(3)])
                en_franja[k[alto >= cl.suelo(c[k, 0], c[k, 2]) + 2.0]] = False
            tris = tris[~(en_via | en_franja)]
            if len(tris):
                tris = self.sin_flotantes(p, tris)
            if len(tris):
                cen = p[np.unique(tris)][:, [0, 2]].mean(axis=0)
                salida += [{"geo": empacar(pp, uu, tt), "tex": jpeg, "centro": (float(cen[0]), float(cen[1]))}
                           for pp, uu, tt in partir(p, uv, tris)]
        return salida

    def sin_flotantes(self, p, tris, alto_min=2.5, area_max=20.0):
        """Quita los pedazos sueltos que dejan los recortes cerca de la calle: componentes chicos que ya no tocan
        el suelo (copas de árboles derretidas, trozos de fachada). Fachadas y techos siguen pegados a su edificio."""
        n = len(p)
        a = np.r_[tris[:, 0], tris[:, 1], tris[:, 2]]
        b = np.r_[tris[:, 1], tris[:, 2], tris[:, 0]]
        _, lab = connected_components(coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n)), directed=False)
        comp_t = lab[tris[:, 0]]
        comps = np.unique(comp_t)
        area_t = 0.5 * np.linalg.norm(np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]]), axis=1)
        area_c = np.bincount(comp_t, area_t, lab.max() + 1)
        usados = np.unique(tris)
        bajo = np.full(lab.max() + 1, np.inf)
        np.minimum.at(bajo, lab[usados], p[usados, 1])
        cuenta = np.bincount(lab[usados], minlength=lab.max() + 1)
        cx = np.bincount(lab[usados], p[usados, 0], lab.max() + 1) / np.maximum(cuenta, 1)
        cz = np.bincount(lab[usados], p[usados, 2], lab.max() + 1) / np.maximum(cuenta, 1)
        chicos = comps[area_c[comps] < area_max]
        chicos = chicos[self.calles.en(self.calles.limpieza, cx[chicos], cz[chicos])] if len(chicos) else chicos
        if not len(chicos):
            return tris
        fuera = chicos[bajo[chicos] > self.calles.suelo(cx[chicos], cz[chicos]) + alto_min]
        return tris[~np.isin(comp_t, fuera)]

    def lista(self):
        with self.cerrojo:
            return {"listo": self.version > self.inicial, "version": self.version, "material": self.material,
                    "creditos": CREDITOS, "error": self.error,
                    "mallas": [{"id": i, "tex": self.mallas[i]["tex"] is not None} for i in self.publicadas]}

    def malla(self, i):
        with self.cerrojo:
            return self.mallas.get(i)


def servir(args, mundo):
    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            partes = u.path.strip("/").split("/")
            if partes == ["lista"]:
                q = urllib.parse.parse_qs(u.query)
                if "x" in q and "z" in q:  # dónde está el coche (coordenadas de la pista)
                    mundo.mover_foco(float(q["x"][0]), float(q["z"][0]))
                cuerpo, tipo = json.dumps(mundo.lista()).encode(), "application/json"
            elif len(partes) == 2 and partes[0] in ("geo", "tex") and mundo.malla(partes[1]):
                m = mundo.malla(partes[1])
                cuerpo, tipo = (m["geo"], "application/octet-stream") if partes[0] == "geo" else (m["tex"] or b"", "image/jpeg")
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.puerto), Manejador)
    log(f"edificios de Google 3D: http://127.0.0.1:{args.puerto}/lista")
    srv.serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(prog="openlaps.google3d", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pista", help="carpeta de la pista de AC hecha con OpenLaps (con openlaps_geo.json)")
    p.add_argument("--glb", help="el .glb de la pista (por defecto, el de salida/ con el mismo nombre)")
    p.add_argument("--token", default=os.environ.get("CESIUM_ION_TOKEN"), help="token de Cesium ion")
    p.add_argument("--radio", type=float, default=500.0, help="m alrededor del coche")
    p.add_argument("--error", type=float, default=0.03, help="error geométrico aceptable por metro de distancia")
    p.add_argument("--max", type=int, default=1200, help="máximo de teselas a la vez")
    p.add_argument("--cache", type=int, default=3000, help="teselas convertidas que se guardan en memoria")
    p.add_argument("--vivo", help="modo en vivo: carpeta de los pedazos (salida/vivo/<nombre>) en lugar de --glb")
    p.add_argument("--puerto", type=int, default=PUERTO)
    p.add_argument("--instalar", action="store_true", help="solo poner el script en la pista de AC")
    p.add_argument("--quitar", action="store_true", help="solo quitar el script de la pista de AC")
    a = p.parse_args(argv)
    if a.quitar:
        print("quitado" if quitar(a.pista) else "no estaba instalado")
        return
    if a.instalar:
        print(f"instalado como [SCRIPT_{instalar(a.pista, a.puerto)}]")
        return
    if not a.token:
        raise SystemExit("falta el token de Cesium ion (--token o CESIUM_ION_TOKEN)")
    if not a.glb and not a.vivo:
        real = os.path.realpath(a.pista)
        nombre = os.path.basename(real.rstrip("\\/"))
        a.glb = os.path.join(os.path.dirname(os.path.dirname(real)), f"{nombre}.glb")
    mundo = Mundo(a)
    threading.Thread(target=mundo.bucle, daemon=True).start()
    servir(a, mundo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
