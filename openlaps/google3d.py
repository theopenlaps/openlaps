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
import gzip
import hashlib
import http.client
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
        self._local_http = threading.local()
        ep = json.loads(self._get(f"https://api.cesium.com/v1/assets/{ACTIVO_GOOGLE}/endpoint?access_token={token}"))
        self.raiz_url = ep["options"]["url"]
        self.llave = urllib.parse.parse_qs(urllib.parse.urlparse(self.raiz_url).query)["key"][0]
        self.sesion = None
        self.pedidas = 0
        self.subarboles = {}
        self._sub_vuelo = {}
        self._lock = threading.Lock()

    def _conn(self, scheme, netloc):
        conns = getattr(self._local_http, "conns", None)
        if conns is None:
            conns = {}
            self._local_http.conns = conns
        k = (scheme, netloc)
        c = conns.get(k)
        if c is None:
            c = (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(netloc, timeout=30)
            conns[k] = c
        return c

    def _get(self, url):
        """GET con conexiones persistentes (HTTP keep-alive) y compresión gzip para reutilizar TLS."""
        for intento in range(2):
            u = urllib.parse.urlparse(url)
            ruta = (u.path or "/") + ("?" + u.query if u.query else "")
            c = self._conn(u.scheme, u.netloc)
            try:
                c.request("GET", ruta, headers={"User-Agent": "OpenLaps", "Accept-Encoding": "gzip",
                                                "Connection": "keep-alive"})
                r = c.getresponse()
                datos = r.read()
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.getheader("Location")
                    if loc:
                        url = urllib.parse.urljoin(url, loc)
                        continue
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status} en {u.netloc}{u.path}")
                if r.getheader("Content-Encoding", "").lower() == "gzip":
                    datos = gzip.decompress(datos)
                return datos
            except (http.client.HTTPException, OSError, EOFError):
                c.close()
                conns = getattr(self._local_http, "conns", None)
                if conns is not None:
                    conns.pop((u.scheme, u.netloc), None)
                if intento == 1:
                    raise

    def url(self, uri, base):
        u = urllib.parse.urlparse(urllib.parse.urljoin(base, uri))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        q["key"] = self.llave
        with self._lock:
            if "session" in q:
                self.sesion = q["session"]
            elif self.sesion:
                q["session"] = self.sesion
        return u._replace(query=urllib.parse.urlencode(q)).geturl()

    def get(self, url):
        with self._lock:
            self.pedidas += 1
        return self._get(url)

    def subarbol(self, url):
        """Raíz de un subárbol JSON, guardada en memoria por su ruta (al moverse se vuelven a usar). Google da rutas
        opacas que cambian cada vez que se pide el root.json: por eso también se guarda la raíz."""
        ruta = urllib.parse.urlparse(url).path
        ev = None
        soy_dueno = False
        with self._lock:
            if ruta in self.subarboles:
                return self.subarboles[ruta]
            ev = self._sub_vuelo.get(ruta)
            if ev is None:
                ev = threading.Event()
                self._sub_vuelo[ruta] = ev
                soy_dueno = True
        if not soy_dueno:
            ev.wait(30)
            with self._lock:
                if ruta in self.subarboles:
                    return self.subarboles[ruta]
        try:
            raiz = json.loads(self.get(url))["root"]
            with self._lock:
                self.subarboles[ruta] = raiz
            return raiz
        finally:
            if soy_dueno:
                with self._lock:
                    self._sub_vuelo.pop(ruta, None)
                ev.set()


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


def seleccionar(google, centro, radio, error_por_metro, max_teselas, error_cerca=0.01, radio_cerca=150.0,
                foco_actual=None, padres=None):
    """Teselas glb a dibujar alrededor de `centro` (ECEF): como Cesium, se baja de nivel mientras el error
    geométrico sea grande para su distancia (refinamiento REPLACE). En los primeros `radio_cerca` m usa un error más
    fino (`error_cerca`). Por niveles (ordenados por cercanía), con peticiones en paralelo."""
    elegidas = []
    frente = [(google.subarbol(google.raiz_url), google.raiz_url, ())]

    def expandir(item):
        tile, base, anc = item
        d = distancia(tile["boundingVolume"], centro)
        d_foco = distancia(tile["boundingVolume"], foco_actual) if foco_actual is not None else d
        if min(d, d_foco) > radio:
            return [], []
        uri = tile.get("content", {}).get("uri")
        if uri and ".json" in uri:
            url = google.url(uri, base)
            return [], [(google.subarbol(url), url, anc)]
        hijos = tile.get("children", [])
        d_lod = min(d, d_foco)
        tol = max(0.2, d_lod * min(error_por_metro, error_cerca)) if d_lod < radio_cerca \
            else max(0.5, d_lod * error_por_metro)
        if uri and ".glb" in uri:
            u = google.url(uri, base)
            if tile["geometricError"] <= tol or not hijos:
                return [(u, d_lod, anc)], []
            anc = anc + (tid(u),)
        return [], [(h, base, anc) for h in hijos]

    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        while frente and len(elegidas) < max_teselas:
            nuevos = []
            for sel, sig in ex.map(expandir, frente):
                elegidas += sel
                nuevos += sig
            # si hay muchas ramas abiertas, prioriza las más cercanas al coche
            if len(nuevos) > max_teselas:
                nuevos.sort(key=lambda it: distancia(it[0]["boundingVolume"], foco_actual if foco_actual is not None else centro))
            frente = nuevos
    # las más cercanas primero: son las que se ven y las que se publican antes
    elegidas.sort(key=lambda e: e[1])
    elegidas = elegidas[:max_teselas]
    if padres is not None:
        for u, _, anc in elegidas:
            padres[tid(u)] = frozenset(anc)
    return [u for u, _, _ in elegidas]


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
    ondulación del geoide más corrección local suave cuando el MDE de superficie varía por manzanas, p. ej. en México).
    Cada hilo tiene sus propios transformadores de pyproj."""

    def __init__(self, lat0, lon0, epsg):
        self._epsg = epsg
        self._local = threading.local()
        _, a_utm, _ = self._trans()
        self.x0, self.y0 = a_utm.transform(lon0, lat0)
        self.desfase = None
        self._ancla_lock = threading.Lock()
        self.anclas = {}          # (ix, iz) cada 128 m -> (mx, mz, desfase_local)
        self._ancla_arr = None    # tupla atómica (ax, az, ad) para lectura sin cerrojo

    def _trans(self):
        t = getattr(self._local, "t", None)
        if t is None:
            t = (
                Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True),
                Transformer.from_crs("EPSG:4326", f"EPSG:{self._epsg}", always_xy=True),
                Transformer.from_crs(f"EPSG:{self._epsg}", "EPSG:4326", always_xy=True),
            )
            self._local.t = t
        return t

    def registrar_ancla(self, xs, zs, difs):
        """Guarda un ancla local `(mx, mz, desfase)` a partir de muestras sobre la calle y actualiza la
        superficie suave de alineación."""
        if len(difs) == 0:
            return
        mx, mz = float(np.median(xs)), float(np.median(zs))
        val = float(np.percentile(difs, 25))
        celda = (int(round(mx / 128.0)), int(round(mz / 128.0)))
        with self._ancla_lock:
            if len(self.anclas) >= 3:
                med = float(np.median([v[2] for v in self.anclas.values()]))
                if abs(val - med) > 35.0:
                    return
            self.anclas[celda] = (mx, mz, val)
            vals = list(self.anclas.values())
            ax = np.array([v[0] for v in vals], float)
            az = np.array([v[1] for v in vals], float)
            ad = np.array([v[2] for v in vals], float)
            self._ancla_arr = (ax, az, ad)
            self.desfase = float(np.median(ad))

    def desfase_en(self, x, z):
        """Desfase interpolado suavemente en `(x, z)` para que todas las teselas vecinas empalmen sin escalón."""
        arr = self._ancla_arr
        if arr is None:
            v = self.desfase or 0.0
            return np.full_like(np.asarray(x, float), v, dtype=float) if np.ndim(x) else v
        ax, az, ad = arr
        if len(ad) == 1:
            v = float(ad[0])
            return np.full_like(np.asarray(x, float), v, dtype=float) if np.ndim(x) else v
        x_arr = np.atleast_1d(np.asarray(x, float))
        z_arr = np.atleast_1d(np.asarray(z, float))
        r0_2 = 90.0 * 90.0
        out = np.empty(len(x_arr), float)
        for s in range(0, len(x_arr), 8192):
            dx = x_arr[s:s + 8192, None] - ax[None, :]
            dz = z_arr[s:s + 8192, None] - az[None, :]
            w = 1.0 / (dx * dx + dz * dz + r0_2)
            out[s:s + 8192] = (w @ ad) / w.sum(axis=1)
        return out if np.ndim(x) else float(out[0])

    def ac(self, ecef, desfase=True):
        a_geo, a_utm, _ = self._trans()
        lon, lat, h = a_geo.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
        e, n = a_utm.transform(lon, lat)
        dx, dz = e - self.x0, -(n - self.y0)
        dh = self.desfase_en(dx, dz) if desfase else 0.0
        return np.c_[dx, h - dh, dz]

    def lonlat(self, x, z):
        _, _, a_ll = self._trans()
        return a_ll.transform(self.x0 + x, self.y0 - z)

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
        def mascara(lista):
            m = np.zeros((nx, nz), bool)
            if lista:
                _rasterizar(np.concatenate(lista), self.x0, self.z0, nx, nz, CELDA, np.full((nx, nz), -1e9), m)
            return m
        # donde se quita TODO lo de Google: la calzada encogida 2 m y lejos de las plantas de los edificios. La
        # malla de Google es una cáscara hueca y las fachadas caen a menudo sobre la banqueta: recortar ahí (o
        # junto a un edificio) se llevaba la fachada y quedaban techos y astillas sueltas.
        # 3 transformadas de distancia (rápidas) en vez de 5 dilataciones/erosiones de núcleo grande
        calz = mascara(tris_calz)
        edif = mascara(tris_edif)
        d_cubre, (ii, jj) = ndimage.distance_transform_edt(~cubre, return_indices=True)
        d_edif = ndimage.distance_transform_edt(~edif) if edif.any() else None
        edif_protec = (d_edif <= (1.0 / CELDA)) if d_edif is not None else edif
        d_calz = ndimage.distance_transform_edt(calz) if calz.any() else None
        via_calz = (d_calz > (2.0 / CELDA)) if d_calz is not None else calz
        self.edif = edif
        self.cubre = cubre
        self.via = (via_calz & ~(d_edif <= (1.5 / CELDA))) if d_edif is not None else via_calz
        # alrededor (banquetas incluidas): solo lo que queda entero por debajo de 2 m (coches, gente, suelo),
        # sin meterse nunca en las plantas de los edificios de OpenLaps (si no, les corta los bajos)
        self.franja = (d_cubre <= (FRANJA / CELDA)) & ~self.via & ~edif_protec
        self.limpieza = (d_cubre <= ((FRANJA + 2.0) / CELDA)) & ~edif
        # altura del suelo en toda la rejilla: la de la calzada más cercana
        self.suelo_r = alto[ii, jj]
        self.nx, self.nz = nx, nz
        if avisar:
            log(f"calzada de OpenLaps: {cubre.sum() * CELDA * CELDA:,.0f} m²")

    def tiene_edif(self):
        return bool(self.edif.any())

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
        self._lock = threading.Lock()

    def tiene_edif(self):
        with self._lock:
            return any(c.tiene_edif() for c in self.pedazos.values())

    def cargar_cerca(self, x, z, radio):
        L = self.L
        n = int(math.ceil(radio / L)) + 1
        ci, cj = int(math.floor(x / L + 0.5)), int(math.floor(-z / L + 0.5))
        faltan = []
        with self._lock:
            for i in range(ci - n, ci + n + 1):
                for j in range(cj - n, cj + n + 1):
                    if (i, j) not in self.pedazos:
                        f = os.path.join(self.carpeta, f"p_{i}_{j}.glb")
                        if os.path.isfile(f):
                            faltan.append((i, j, f))
        cargados = []
        if len(faltan) > 1:
            def _leer(item):
                i, j, f = item
                try:
                    return (i, j), Calles(f, avisar=False)
                except (SystemExit, OSError, ValueError, KeyError):
                    return None
            with concurrent.futures.ThreadPoolExecutor(min(8, len(faltan))) as ex:
                cargados = [r for r in ex.map(_leer, faltan) if r is not None]
        elif faltan:
            i, j, f = faltan[0]
            try:
                cargados.append(((i, j), Calles(f, avisar=False)))
            except (SystemExit, OSError, ValueError, KeyError):
                pass
        nuevos_k = []
        if cargados:
            with self._lock:
                for k, c in cargados:
                    if k not in self.pedazos:
                        self.pedazos[k] = c
                        self.materiales |= c.materiales
                        nuevos_k.append(k)
                total = len(self.pedazos)
            if nuevos_k:
                log(f"calzada: {len(nuevos_k)} pedazos nuevos ({total} en total)")
        return nuevos_k

    def faltantes_en(self, x, z, margen=80.0):
        """Claves de pedazos alrededor de `(x, z)` que aún no se han cargado."""
        ci0, cj0 = self._claves(np.array([x - margen, x + margen]), np.array([z - margen, z + margen]))
        with self._lock:
            return {(i, j) for i in range(int(ci0.min()), int(ci0.max()) + 1)
                    for j in range(int(cj0.min()), int(cj0.max()) + 1) if (i, j) not in self.pedazos}

    def _claves(self, x, z):
        x, z = np.asarray(x, float), np.asarray(z, float)
        return np.floor(x / self.L + 0.5).astype(np.int64), np.floor(-z / self.L + 0.5).astype(np.int64)

    def _consulta(self, que, x, z, mascara=None):
        x, z = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(z, float))
        out = np.zeros(len(x), bool) if que == "en" else np.full(len(x), np.nan)
        with self._lock:
            pedazos = dict(self.pedazos)
        if not pedazos:
            return out if que == "en" else np.zeros(len(x))
        ki, kj = self._claves(x, z)
        claves = list(pedazos)
        cent = np.array([(i * self.L, -j * self.L) for i, j in claves])
        for a, b in set(zip(ki.tolist(), kj.tolist())):
            sel = (ki == a) & (kj == b)
            c = pedazos.get((a, b))
            if c is None:
                if que == "en":
                    continue
                # sin pedazo: la altura del pedazo cargado más cercano (fuera de su rejilla se recorta al borde)
                k = int(np.argmin(np.hypot(cent[:, 0] - a * self.L, cent[:, 1] + b * self.L)))
                c = pedazos[claves[k]]
            if que == "en":
                out[sel] = c.en(getattr(c, mascara), x[sel], z[sel])
            else:
                out[sel] = c.suelo(x[sel], z[sel])
        return out

    # misma interfaz que Calles (las máscaras se piden por nombre)
    via, franja, limpieza, edif, cubre = "via", "franja", "limpieza", "edif", "cubre"

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
        self.tid_meta = {}                           # tid -> (cx, cz, d_usado, set_pedazos_faltantes)
        self.padres_de = {}                          # tid -> frozenset(tids ancestros)
        self.completo_tids = []                      # tids del último conjunto entero
        self.tapa = {}                               # tid_viejo -> [tids nuevos que lo sustituyen]
        self.vigentes_set = set()
        self.en_proceso = set()                      # tids bajándose/convirtiéndose (evita trabajo doble entre hilos)
        self.bucle_activo = False
        # versiones numeradas desde la hora de arranque: si se reinicia, AC no confunde la nueva v1 con la anterior
        self.version, self.publicadas = int(time.time()) % 10_000_000 * 100, []
        self.inicial = self.version
        self.foco, self.centro = self.calles.salida, None
        self.cerrojo = threading.Lock()
        self.despertar = threading.Event()
        self.despertar.set()
        self.error = None
        self._vivo_url = getattr(args, "vivo_url", None) or ("http://127.0.0.1:8790" if getattr(args, "vivo", None) else None)
        self._t_camino, self._zona_tam = 0.0, (1200.0, 1200.0)
        self._hechas_zona = set()

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
            # los pedazos de alrededor van llegando: cada 15 s se revisan los nuevos y se invalidan solo las
            # teselas que se convirtieron antes de que existiera su calle
            self.t_revisado = time.time()
            if self.calles.cargar_cerca(x, z, self.args.radio):
                self.despertar.set()

    def _ecef_de(self, x, z):
        lon, lat = self.marco.lonlat(x, z)
        d = self.marco.desfase_en(x, z) if self.marco.desfase is not None else 0.0
        h = float(np.ravel(self.calles.suelo(x, z))[0]) + float(d) + 1.5
        return self.marco.ecef(lat, lon, h)

    def _reemplazos(self, viejos, vigentes):
        reemp = {}
        for t_old in viejos:
            desc = [t for t in vigentes if t_old in self.padres_de.get(t, ())]
            if desc:
                reemp[t_old] = desc
                continue
            anc = [t for t in vigentes if t in self.padres_de.get(t_old, ())]
            if anc:
                reemp[t_old] = anc
        return reemp

    def _invalidar(self, nuevos_pedazos=()):
        """Quita de la caché las teselas que ya quedaron desfasadas (> 1,5 m frente a nuevas anclas) o cuyo pedazo
        de calle acaba de llegar."""
        nset = set(nuevos_pedazos)
        borrar = []
        with self.cerrojo:
            for t, (cx, cz, d_u, falt) in self.tid_meta.items():
                if (nset and (falt & nset)) or (self.marco.desfase is not None
                                               and abs(float(self.marco.desfase_en(cx, cz)) - d_u) > 1.5):
                    borrar.append(t)
            for t in borrar:
                self.tid_meta.pop(t, None)
                for i in self.teselas.pop(t, ()):
                    self.mallas.pop(i, None)
                    self.centro_malla.pop(i, None)
        return len(borrar)

    def _guardar_teselas(self, grupo, convertidas):
        with self.cerrojo:
            for u, res in zip(grupo, convertidas):
                mallas, meta = res if isinstance(res, tuple) else (res, None)
                t = tid(u)
                if t in self.teselas:
                    continue
                ids = []
                for k, m in enumerate(mallas):
                    i = f"{self.inicial}_{t}_{k}"
                    self.mallas[i] = m
                    self.centro_malla[i] = m.pop("centro")
                    ids.append(i)
                self.teselas[t] = ids
                if meta is not None:
                    self.tid_meta[t] = meta
            while len(self.teselas) > self.args.cache:
                t, ids = next(iter(self.teselas.items()))
                if t in self.vigentes_set:
                    break
                self.teselas.popitem(last=False)
                self.tid_meta.pop(t, None)
                for i in ids:
                    self.mallas.pop(i, None)
                    self.centro_malla.pop(i, None)

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
        fx, fz = self.foco
        if isinstance(self.calles, CallesVivo):
            nuevos_p = self.calles.cargar_cerca(x, z, self.args.radio)
            nuevos_p += self.calles.cargar_cerca(fx, fz, 250.0)
            if nuevos_p:
                self._invalidar(nuevos_p)
        padres = {}
        err_cerca = getattr(self.args, "error_cerca", 0.01)
        urls = seleccionar(self.google, self._ecef_de(x, z), self.args.radio, self.args.error, self.args.max,
                           error_cerca=err_cerca, radio_cerca=150.0, foco_actual=self._ecef_de(fx, fz), padres=padres)
        vigentes = list(dict.fromkeys(tid(u) for u in urls))
        with self.cerrojo:
            self.padres_de.update(padres)
            self.vigentes_set = set(vigentes)
            nuevas = [u for u in urls if tid(u) not in self.teselas]
            viejos = [t for t in self.completo_tids if t not in self.vigentes_set]
            reemp = self._reemplazos(viejos, vigentes)
            self.tapa = reemp

        # por tandas, de la más cercana a la más lejana, publicando cada tanda; los viejos que ya tienen todos sus
        # sustitutos listos dejan de publicarse al instante (nunca coexiste una tesela burda encima de sus hijas)
        TANDA = 24
        self.bucle_activo = True
        try:
            pend_sin_desfase = []
            for k0 in range(0, max(len(nuevas), 1), TANDA):
                candidatas = nuevas[k0:k0 + TANDA]
                grupo = []
                with self.cerrojo:
                    for u in candidatas:
                        t = tid(u)
                        if t not in self.teselas and t not in self.en_proceso:
                            self.en_proceso.add(t)
                            grupo.append(u)
                if grupo:
                    try:
                        with concurrent.futures.ThreadPoolExecutor(16) as ex:
                            crudas = list(ex.map(self.google.get, grupo))
                        prims = [primitivas(d) for d in crudas]
                        n_anc_antes = len(self.marco.anclas)
                        if self.marco.desfase is None and prims:
                            d0 = self.medir_desfase([p for ps in prims for p in ps], (x, z))
                            if d0 is not None:
                                self.marco.desfase = d0
                                log(f"desfase de altura Google - OpenLaps: {self.marco.desfase:.2f} m")
                        if self.marco.desfase is None:
                            pend_sin_desfase.extend(zip(grupo, prims))
                        else:
                            if pend_sin_desfase:
                                gs, ps = zip(*pend_sin_desfase)
                                pend_sin_desfase.clear()
                                self._guardar_teselas(gs, [self.convertir(p) for p in ps])
                            convertidas = [self.convertir(p) for p in prims]
                            self._guardar_teselas(grupo, convertidas)
                            if len(self.marco.anclas) > n_anc_antes:
                                self._invalidar()
                    finally:
                        with self.cerrojo:
                            for u in grupo:
                                self.en_proceso.discard(tid(u))
                ultima = k0 + TANDA >= len(nuevas)
                if ultima and pend_sin_desfase:
                    if self.marco.desfase is None:
                        # ninguna tesela tocó calle aún (p. ej. pedazos aún generándose): usar 0 provisional sin fijar ancla
                        self.marco.desfase = 0.0
                    gs, ps = zip(*pend_sin_desfase)
                    pend_sin_desfase.clear()
                    self._guardar_teselas(gs, [self.convertir(p) for p in ps])
                    if not self.marco.anclas:
                        self.marco.desfase = None    # que la próxima tanda real sí calibre el desfase
                with self.cerrojo:
                    listas = [i for t in vigentes if t in self.teselas for i in self.teselas[t]]
                    if ultima:
                        self.publicadas = listas
                        self.completo_tids = vigentes
                    else:
                        anteriores = []
                        for t_old in viejos:
                            rep = reemp.get(t_old)
                            if rep and all(r in self.teselas for r in rep):
                                continue
                            for i in self.teselas.get(t_old, ()):
                                if math.hypot(*np.subtract(self.centro_malla.get(i, (1e9, 1e9)), (x, z))) < self.args.radio:
                                    anteriores.append(i)
                        self.publicadas = list(dict.fromkeys(listas + anteriores))
                    self.version += 1
                if not ultima and math.hypot(*np.subtract(self.prevista(), (x, z))) > 2 * UMBRAL_MOVIDO:
                    break                    # el coche ya se fue lejos: mejor empezar otra selección desde donde está
            with self.cerrojo:
                for t in vigentes:
                    if t in self.teselas:
                        self.teselas.move_to_end(t)
                while len(self.teselas) > self.args.cache:      # memoria: fuera las más viejas que ya no se usan
                    t, ids = next(iter(self.teselas.items()))
                    if t in self.vigentes_set:
                        break
                    self.teselas.popitem(last=False)
                    self.tid_meta.pop(t, None)
                    for i in ids:
                        self.mallas.pop(i, None)
                        self.centro_malla.pop(i, None)
        finally:
            self.bucle_activo = False
        if math.hypot(*np.subtract(self.prevista(), (x, z))) > UMBRAL_MOVIDO:
            self.despertar.set()
        log(f"v{self.version - self.inicial}: {len(vigentes)} teselas ({len(nuevas)} nuevas, "
            f"{self.google.pedidas} peticiones), {len(self.publicadas)} mallas, {time.time() - t0:.1f} s")

    def _camino_vivo(self, fx, fz, vx, vz):
        """Puntos previstos sobre las calles de OSM (desde el servidor en vivo) y tamaño de la zona preparada."""
        if not self._vivo_url:
            return [], self._zona_tam
        try:
            u = f"{self._vivo_url}/vivo/camino?x={fx:.1f}&z={fz:.1f}&vx={vx:.2f}&vz={vz:.2f}"
            with urllib.request.urlopen(u, timeout=1.5) as r:
                d = json.loads(r.read().decode("utf-8"))
            if d.get("zona_tam"):
                self._zona_tam = tuple(float(v) for v in d["zona_tam"])
            return d.get("camino") or [], self._zona_tam
        except Exception:  # noqa: BLE001
            return [], self._zona_tam

    def _precargar_punto(self, tx, tz, radio=240.0):
        if self.bucle_activo:
            return 0
        if isinstance(self.calles, CallesVivo):
            nuevos_p = self.calles.cargar_cerca(tx, tz, radio + 60.0)
            if nuevos_p:
                self._invalidar(nuevos_p)
        padres = {}
        err_cerca = getattr(self.args, "error_cerca", 0.01)
        urls = seleccionar(self.google, self._ecef_de(tx, tz), radio, self.args.error, 400,
                           error_cerca=err_cerca, radio_cerca=radio, padres=padres)
        with self.cerrojo:
            self.padres_de.update(padres)
            pend = [u for u in urls if tid(u) not in self.teselas and tid(u) not in self.en_proceso]
        if not pend:
            return 0
        hechas = 0
        for k0 in range(0, len(pend), 12):
            if self.bucle_activo:
                break
            grupo = []
            with self.cerrojo:
                if len(self.teselas) >= int(self.args.cache * 0.92):
                    break
                for u in pend[k0:k0 + 12]:
                    t = tid(u)
                    if t not in self.teselas and t not in self.en_proceso:
                        self.en_proceso.add(t)
                        grupo.append(u)
            if not grupo:
                continue
            try:
                with concurrent.futures.ThreadPoolExecutor(8) as ex:
                    crudas = list(ex.map(self.google.get, grupo))
                convertidas = [self.convertir(primitivas(d)) for d in crudas]
                self._guardar_teselas(grupo, convertidas)
                hechas += len(grupo)
            finally:
                with self.cerrojo:
                    for u in grupo:
                        self.en_proceso.discard(tid(u))
            time.sleep(0.02)
        return hechas

    def precarga_bucle(self):
        """Hilo en segundo plano: precarga con detalle fino 300-600 m por delante al conducir, o la zona entera
        (1200x1200 m) con el coche parado."""
        while self.marco.desfase is None:
            time.sleep(0.5)
        while True:
            try:
                if self.bucle_activo:
                    time.sleep(0.25)
                    continue
                fx, fz = self.foco
                vx, vz = getattr(self, "vel", (0.0, 0.0))
                rap = math.hypot(vx, vz)
                hechas = 0
                if rap >= 2.0:
                    camino, _ = self._camino_vivo(fx, fz, vx, vz)
                    blancos = []
                    if len(camino) >= 2:
                        for dist in (320.0, 480.0, 600.0):
                            cerca = min(camino, key=lambda p: abs((p[2] if len(p) > 2 else math.hypot(p[0] - fx, p[1] - fz)) - dist))
                            s = cerca[2] if len(cerca) > 2 else math.hypot(cerca[0] - fx, cerca[1] - fz)
                            if 220.0 <= s <= 700.0:
                                blancos.append((cerca[0], cerca[1]))
                    if not blancos:
                        ux, uz = vx / rap, vz / rap
                        blancos = [(fx + ux * 350.0, fz + uz * 350.0), (fx + ux * 550.0, fz + uz * 550.0)]
                    vistos = set()
                    for bx, bz in blancos:
                        cl = (round(bx / 60), round(bz / 60))
                        if cl in vistos:
                            continue
                        vistos.add(cl)
                        hechas += self._precargar_punto(bx, bz, radio=240.0)
                        if math.hypot(self.foco[0] - fx, self.foco[1] - fz) > 80.0 or self.bucle_activo:
                            break
                else:
                    w, h = self._zona_tam
                    cx0, cz0 = (0.0, 0.0) if getattr(self.args, "vivo", None) else self.calles.salida
                    paso = 240.0
                    xs = np.arange(cx0 - w / 2 + paso / 2, cx0 + w / 2, paso)
                    zs = np.arange(cz0 - h / 2 + paso / 2, cz0 + h / 2, paso)
                    pts = sorted([(float(a), float(b)) for a in xs for b in zs],
                                 key=lambda p: math.hypot(p[0] - fx, p[1] - fz))
                    for bx, bz in pts:
                        if math.hypot(*getattr(self, "vel", (0.0, 0.0))) >= 2.0 or self.bucle_activo:
                            break
                        k = (round(bx), round(bz))
                        if k in self._hechas_zona:
                            continue
                        n = self._precargar_punto(bx, bz, radio=220.0)
                        hechas += n
                        if not self.bucle_activo:
                            self._hechas_zona.add(k)
                        if len(self.teselas) >= int(self.args.cache * 0.92):
                            break
                if hechas:
                    log(f"precarga: +{hechas} teselas en caché ({len(self.teselas)}/{self.args.cache})")
                else:
                    time.sleep(1.0)
            except Exception as e:  # noqa: BLE001
                log(f"precarga: {e!r}")
                time.sleep(2.0)

    def _cargados_mask(self, xs, zs):
        """En modo en vivo: qué puntos caen en pedazos ya generados (fuera de ellos la altura es extrapolada)."""
        cl = self.calles
        if not isinstance(cl, CallesVivo):
            return np.ones(len(xs), bool)
        ki, kj = cl._claves(xs, zs)
        with cl._lock:
            peds = set(cl.pedazos)
        return np.array([(int(a), int(b)) in peds for a, b in zip(ki, kj)], bool)

    def medir_desfase(self, prims, centro, radio=250.0):
        """Altura de la superficie de Google menos la de la calzada de OpenLaps (percentil 25: coches y árboles
        solo pueden subirla). Registra también anclas locales y devuelve None si aún no toca ninguna calle."""
        difs_cerca, difs_todo, difs_plaza = [], [], []
        cl = self.calles
        for pos_ecef, _, tris, _ in prims:
            p = self.marco.ac(pos_ecef, desfase=False)
            c = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3
            en_ped = self._cargados_mask(c[:, 0], c[:, 2])
            if not en_ped.any():
                continue
            en_via = en_ped & cl.en(cl.via, c[:, 0], c[:, 2])
            if en_via.any():
                sv = cl.suelo(c[en_via, 0], c[en_via, 2])
                d = c[en_via, 1] - sv
                if en_via.sum() >= 15:
                    self.marco.registrar_ancla(c[en_via, 0], c[en_via, 2], d)
                difs_todo.extend(d.tolist())
                m_cerca = np.hypot(c[en_via, 0] - centro[0], c[en_via, 2] - centro[1]) < radio
                if m_cerca.any():
                    difs_cerca.extend(d[m_cerca].tolist())
            else:
                en_plaza = en_ped & cl.en(cl.cubre, c[:, 0], c[:, 2]) & ~cl.en(cl.edif, c[:, 0], c[:, 2])
                if en_plaza.any():
                    sv = cl.suelo(c[en_plaza, 0], c[en_plaza, 2])
                    d = c[en_plaza, 1] - sv
                    if en_plaza.sum() >= 30 and not self.marco.anclas:
                        self.marco.registrar_ancla(c[en_plaza, 0], c[en_plaza, 2], d)
                    difs_plaza.extend(d.tolist())
        eleg = difs_cerca or difs_todo or difs_plaza
        return float(np.percentile(eleg, 25)) if eleg else None

    def convertir(self, prims):
        """Mallas empacadas de una tesela: en coordenadas de AC (alineadas suavemente a la altura local de las
        calles) y sin lo que cae sobre la calle de OpenLaps."""
        salida = []
        cl = self.calles
        pts_raw = []
        for pos_ecef, uv, tris, jpeg in prims:
            p = self.marco.ac(pos_ecef, desfase=False)
            c = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3
            en_ped = self._cargados_mask(c[:, 0], c[:, 2])
            if en_ped.any():
                en_via = en_ped & cl.en(cl.via, c[:, 0], c[:, 2])
                if en_via.sum() >= 15:
                    d = c[en_via, 1] - cl.suelo(c[en_via, 0], c[en_via, 2])
                    self.marco.registrar_ancla(c[en_via, 0], c[en_via, 2], d)
            pts_raw.append((p, uv, tris, jpeg))

        cxs, czs = [], []
        for p_raw, uv, tris, jpeg in pts_raw:
            p = p_raw.copy()
            p[:, 1] -= self.marco.desfase_en(p[:, 0], p[:, 2])
            c = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3
            cxs.append(float(c[:, 0].mean()))
            czs.append(float(c[:, 2].mean()))
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
                if len(p) >= len(tris) * 2:
                    # soldar vértices duplicados (mallas sin indexar) para aliviar AC
                    _, idx_u, inv = np.unique(np.c_[np.round(p, 3), np.round(uv, 4)], axis=0,
                                              return_index=True, return_inverse=True)
                    p, uv, tris = p[idx_u], uv[idx_u], inv[tris]
                cen = p[np.unique(tris)][:, [0, 2]].mean(axis=0)
                salida += [{"geo": empacar(pp, uu, tt), "tex": jpeg, "centro": (float(cen[0]), float(cen[1]))}
                           for pp, uu, tt in partir(p, uv, tris)]
        if cxs:
            mx, mz = float(np.mean(cxs)), float(np.mean(czs))
            falt = cl.faltantes_en(mx, mz, 90.0) if isinstance(cl, CallesVivo) else set()
            meta = (mx, mz, float(self.marco.desfase_en(mx, mz)), falt)
        else:
            meta = None
        return salida, meta

    def sin_flotantes(self, p, tris, alto_min=2.5, area_max=20.0):
        """Quita los pedazos sueltos que dejan los recortes cerca de la calle: componentes chicos (y grandes si no
        tocan ninguna planta de edificio de OpenLaps) que ya no tocan el suelo (copas de árboles, toldos, trozos de
        fachada sueltos)."""
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
        v_edif = self.calles.en(self.calles.edif, p[usados, 0], p[usados, 2])
        frac_edif = np.bincount(lab[usados], v_edif.astype(float), lab.max() + 1) / np.maximum(cuenta, 1)
        tope_area = 200.0 if self.calles.tiene_edif() else area_max
        cand = comps[(area_c[comps] < tope_area) & (frac_edif[comps] < 0.4)]
        cand = cand[self.calles.en(self.calles.limpieza, cx[cand], cz[cand])] if len(cand) else cand
        if not len(cand):
            return tris
        fuera = cand[bajo[cand] > self.calles.suelo(cx[cand], cz[cand]) + alto_min]
        return tris[~np.isin(comp_t, fuera)]

    def lista(self):
        with self.cerrojo:
            return {"listo": self.version > self.inicial, "version": self.version, "material": self.material,
                    "creditos": CREDITOS, "error": self.error, "tapa": self.tapa,
                    "mallas": [{"id": i, "tex": self.mallas[i]["tex"] is not None} for i in self.publicadas]}

    def malla(self, i):
        with self.cerrojo:
            return self.mallas.get(i)


def servir(args, mundo):
    class Manejador(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            partes = u.path.strip("/").split("/")
            if partes == ["lista"]:
                q = urllib.parse.parse_qs(u.query)
                if "x" in q and "z" in q:  # dónde está el coche (coordenadas de la pista)
                    mundo.mover_foco(float(q["x"][0]), float(q["z"][0]))
                cuerpo, tipo = json.dumps(mundo.lista()).encode(), "application/json"
            elif len(partes) == 2 and partes[0] == "malla" and mundo.malla(partes[1]):
                m = mundo.malla(partes[1])
                geo = m["geo"]
                cuerpo = struct.pack("<I", len(geo)) + geo + (m["tex"] or b"")
                tipo = "application/octet-stream"
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
    p.add_argument("--error-cerca", type=float, default=0.01, help="error geométrico en los primeros 150 m")
    p.add_argument("--max", type=int, default=1200, help="máximo de teselas a la vez")
    p.add_argument("--cache", type=int, default=3000, help="teselas convertidas que se guardan en memoria")
    p.add_argument("--vivo", help="modo en vivo: carpeta de los pedazos (salida/vivo/<nombre>) en lugar de --glb")
    p.add_argument("--vivo-url", help="URL del servidor openlaps.vivo (para la ruta prevista al precargar)")
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
    threading.Thread(target=mundo.precarga_bucle, daemon=True).start()
    servir(a, mundo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
