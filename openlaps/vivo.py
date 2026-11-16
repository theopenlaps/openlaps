"""Tramo en vivo: la pista se genera a pedazos alrededor del coche mientras conduces.

    python -m openlaps.vivo --centro 40.41680,-3.70380 [--trabajadores 3] [--puerto 8790] [opciones de openlaps]

Es una opción aparte: la generación normal (`python -m openlaps ...`) no cambia. Aquí:

- Todo comparte un origen fijo (el punto de partida), así cada pedazo cae en su sitio sin moverlo.
- Pedazos de 256 m: cada uno se genera con 64 m de margen (calles, cruces y relieve de alrededor) y se
  recorta a su centro; lo pequeño (árboles, postes, señales) va entero al pedazo donde cae su centro.
- OSM se baja por teselas de 512 m, por delante del coche: espejos de Overpass con espera corta y, si no
  contestan, la API oficial de OpenStreetMap. Todo queda en caché.
- Varios procesos generan a la vez, siempre el pedazo más urgente según dónde está el coche y hacia dónde va.
- Servidor HTTP: el visor (`/?vivo=1`), el estado (`/vivo/foco?x=&z=&vx=&vz=`), los pedazos (`/vivo/p/...`)
  y una ruta de prueba para el piloto automático (`/vivo/ruta`). Coordenadas como en el .glb y en Assetto
  Corsa: x = este, y = altura, z = sur.
"""
import argparse
import http.server
import json
import math
import os
import re
import shutil
import struct
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np

L = 256.0            # lado del pedazo (m)
MARGEN = 64.0        # margen con que se genera (se recorta después)
TESELA_OSM = 1024.0  # teselas de descarga de OSM (Overpass tarda casi lo mismo por 1 km² que por 0.25: menos peticiones)
TESELA_CAT = 512.0   # teselas del Catastro (el servicio corta las respuestas grandes)
PAD_OSM = 200.0      # OSM alrededor del pedazo (calles que entran, edificios del borde)
EXT_MAX = 1500.0     # hasta dónde se completa un túnel o un puente que sale del pedazo (m)
_A_PIE = {"footway", "path", "steps", "cycleway", "pedestrian", "bridleway", "corridor", "elevator", "platform"}
VERSION = 13          # súbela si cambia el formato de los pedazos: los de caché se regeneran

AQUI = os.path.dirname(os.path.abspath(__file__))
VISOR = os.path.join(os.path.dirname(AQUI), "viewer")


# categoría de cada calle para prever por dónde sigue el coche (no bajar de una avenida a una calle de servicio)
RANGO = {"motorway": 0, "motorway_link": 1, "trunk": 1, "trunk_link": 2, "primary": 2, "primary_link": 3,
         "secondary": 3, "secondary_link": 4, "tertiary": 4, "tertiary_link": 5, "unclassified": 5,
         "residential": 5, "living_street": 6, "service": 7, "track": 8}


def _estructura(el):
    """Vía de coches en túnel o en puente."""
    t = el.get("tags") or {}
    if el.get("type") != "way" or "highway" not in t or t["highway"] in _A_PIE:
        return False
    return t.get("tunnel") not in (None, "no", "building_passage") or t.get("bridge") not in (None, "no")


def rect_pedazo(i, j, pad=PAD_OSM):
    x0, y0, x1, y1 = nucleo(i, j)
    return (x0 - MARGEN - pad, y0 - MARGEN - pad, x1 + MARGEN + pad, y1 + MARGEN + pad)


def nucleo(i, j):
    return ((i - 0.5) * L, (j - 0.5) * L, (i + 0.5) * L, (j + 0.5) * L)


def pedazo_de(x, y):
    return int(math.floor(x / L + 0.5)), int(math.floor(y / L + 0.5))


# =========================================================================== trabajador (otro proceso)
_W = {}


def _iniciar(args_dict, lat0, lon0):
    from . import pipeline, regions
    args = argparse.Namespace(**args_dict)
    regions.set_active(regions.detect(lat0, lon0) if args.region == "auto" else args.region)
    _W.update(args=args, origen=(lat0, lon0), tex_cache={},
              plantilla=pipeline.preparar_escena(args, log=lambda *a: None))


def _escena(i, j, elementos, partes_cat=None, log=None, ext=None):
    """Escena del pedazo (i, j), ya recortada a su centro y con el suelo renombrado. Devuelve (escena, terreno)."""
    from . import pipeline
    from .geo import LocalProjection
    from .mesh import Scene
    from .sources import osm as OSM
    args, (lat0, lon0) = _W["args"], _W["origen"]
    x0, y0, x1, y1 = nucleo(i, j)
    proj = LocalProjection.marco(lat0, lon0, (x0 - MARGEN, y0 - MARGEN, x1 + MARGEN, y1 + MARGEN))
    pl = _W["plantilla"]
    scene = Scene()
    scene.materials, scene.textures, scene.images = dict(pl.materials), dict(pl.textures), dict(pl.images)
    r = pipeline.run(args, log=log or (lambda m: None), proj=proj, scene=scene,
                     osm_data=OSM.parse({"elements": elementos}), pedazo=(x0, y0, x1, y1), cat_data=partes_cat,
                     extension=ext)
    recortar(scene, (x0, y0, x1, y1))
    renombrar_terreno(scene, i, j)
    return scene, r["terr"]


def _iniciar_precarga(args_dict, lat0, lon0):
    _W.update(args=argparse.Namespace(**args_dict), origen=(lat0, lon0))


def _precargar(i, j):
    from . import pipeline
    from .geo import LocalProjection
    (lat0, lon0) = _W["origen"]
    x0, y0, x1, y1 = nucleo(i, j)
    proj = LocalProjection.marco(lat0, lon0, (x0 - MARGEN, y0 - MARGEN, x1 + MARGEN, y1 + MARGEN))
    pipeline.precargar(_W["args"], proj)


def _generar(i, j, elementos, carpeta, partes_cat=None, ext=None):
    """Genera el pedazo (i, j) y deja en `carpeta`: p_i_j.glb (visor), p_i_j.olv (Assetto Corsa) y las
    texturas del suelo en JPEG. Devuelve un resumen."""
    from . import export_glb
    t0 = time.time()
    log = []
    try:
        scene, _ = _escena(i, j, elementos, partes_cat, log=lambda m: log.append(f"{time.time() - t0:5.1f} {m}"),
                           ext=ext)
    except Exception:  # noqa: BLE001
        return {"i": i, "j": j, "error": traceback.format_exc(limit=6), "log": log[-12:]}
    x0, y0, x1, y1 = nucleo(i, j)
    base = os.path.join(carpeta, f"p_{i}_{j}")
    ex = {"pedazo": [i, j], "nucleo": [x0, y0, x1, y1], "version": VERSION}
    mallas = scene.meshes()
    export_glb.write(scene, base + ".glb.part", ex, solo_usados=True, tex_cache=_W["tex_cache"], meshes=mallas)
    for k in [k for k in _W["tex_cache"] if k.startswith("tv_")]:
        del _W["tex_cache"][k]
    n_tri = escribir_olv(scene, base + ".olv.part", carpeta, set(_W["plantilla"].materials), mallas)
    os.replace(base + ".olv.part", base + ".olv")
    os.replace(base + ".glb.part", base + ".glb")       # el .glb al final: su presencia marca el pedazo listo
    log.append(f"{time.time() - t0:5.1f} exportado")
    return {"i": i, "j": j, "seg": round(time.time() - t0, 1), "tri": n_tri, "log": log}


def recortar(scene, caja):
    """Deja solo lo que cae en el centro del pedazo. Las piezas pequeñas (árbol, poste, señal, caseta) van
    enteras al pedazo donde cae el centro de su caja; lo grande (calles, terreno, edificios largos) se corta
    por triángulos según su centroide. Intervalos semiabiertos: ningún triángulo cae en dos pedazos."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    from .mesh import Piece
    x0, y0, x1, y1 = caja

    def dentro(p):
        return (p[:, 0] >= x0) & (p[:, 0] < x1) & (p[:, 1] >= y0) & (p[:, 1] < y1)

    for key, piezas in list(scene.pieces.items()):
        nuevas = []
        for p in piezas:
            # atajo: casi todas las piezas caen enteras dentro o enteras fuera (el análisis por componentes,
            # que es lo caro, solo hace falta en las que cruzan el borde)
            bx0, by0 = p.pos[:, 0].min(), p.pos[:, 1].min()
            bx1, by1 = p.pos[:, 0].max(), p.pos[:, 1].max()
            if bx0 >= x0 and bx1 < x1 and by0 >= y0 and by1 < y1:
                nuevas.append(p)
                continue
            if bx1 < x0 or bx0 >= x1 or by1 < y0 or by0 >= y1:
                continue
            f = p.faces
            keep = dentro(p.pos[f].mean(1)[:, :2])
            if key[0] != "terreno":
                n = len(p.pos)
                filas = np.concatenate([f[:, 0], f[:, 1]])
                cols = np.concatenate([f[:, 1], f[:, 2]])
                g = coo_matrix((np.ones(len(filas), np.int8), (filas, cols)), shape=(n, n))
                nc, lab = connected_components(g, directed=False)
                mn = np.full((nc, 2), np.inf)
                mx = np.full((nc, 2), -np.inf)
                np.minimum.at(mn, lab, p.pos[:, :2])
                np.maximum.at(mx, lab, p.pos[:, :2])
                chico = (mx - mn).max(1) < 40.0
                cen_ok = dentro((mn + mx) / 2)
                lf = lab[f[:, 0]]
                keep = np.where(chico[lf], cen_ok[lf], keep)
            if keep.all():
                nuevas.append(p)
            elif keep.any():
                fs = f[keep]
                usados, inv = np.unique(fs.ravel(), return_inverse=True)
                nuevas.append(Piece(p.pos[usados], p.uv[usados], inv.reshape(-1, 3), p.nrm[usados]))
        if nuevas:
            scene.pieces[key] = nuevas
        else:
            del scene.pieces[key]
    scene.dummies = []


def renombrar_terreno(scene, i, j):
    """terreno_003 -> tv_<i>_<j>_003: los nombres del suelo (material y textura) no chocan entre pedazos."""
    ren = {}
    for name in list(scene.materials):
        if name.startswith("terreno_"):
            nuevo = f"tv_{i}_{j}_{name[8:]}"
            m = dict(scene.materials.pop(name))
            for k, ext in ((name + ".png", ".png"), (name + "_mask.png", "_mask.png")):
                if k in scene.textures:
                    scene.textures[nuevo + ext] = scene.textures.pop(k)
                if k in scene.images:
                    scene.images[nuevo + ext] = scene.images.pop(k)
            m["texture"] = nuevo + ".png"
            if m.get("extra", {}).get("mask"):
                m["extra"] = dict(m["extra"], mask=nuevo + "_mask.png")
            scene.materials[nuevo] = m
            ren[name] = nuevo
    for key in list(scene.pieces):
        if key[1] in ren:
            scene.pieces[(key[0], ren[key[1]], key[2])] = scene.pieces.pop(key)


def respaldo(mat, biblioteca):
    """Material de la biblioteca de la pista semilla para uno que no está en ella (las fachadas y azoteas con el
    color exacto de OSM se crean al vuelo: en AC van con la fachada genérica)."""
    if mat in biblioteca or mat.startswith("tv_"):
        return mat
    for pre, base in (("fachada", "fachada_0"), ("azotea", "azotea_concreto"), ("veg_", "veg_arbusto_0"),
                      ("isleta", "isleta")):
        if mat.startswith(pre) and base in biblioteca:
            return base
    return "banqueta"


def escribir_olv(scene, path, carpeta, biblioteca, mallas=None):
    """Formato sencillo para el script de Assetto Corsa (string.unpack en Lua):
    "OLV1", u32 n; por malla: s2 nombre, s2 material, s2 física, s2 textura propia ("" si usa la de la
    biblioteca), u32 nv, u32 ni, nv*8 float32 (pos xyz, normal xyz, uv) en ejes de AC, ni uint16."""
    from .export_glb import to_yup
    meshes = [m for m in (mallas if mallas is not None else scene.meshes())
              if scene.materials[m["material"]].get("kind") != "invisible" or m["phys"]]
    # primero lo que tiene física: el script de AC la crea en cuanto ha leído esas mallas, sin esperar al resto
    meshes.sort(key=lambda m: not m["phys"])
    partes = [b"OLV1", struct.pack("<I", len(meshes))]
    n_tri = 0
    s2 = lambda t: struct.pack("<H", len(t.encode())) + t.encode()  # noqa: E731
    for m in meshes:
        mat = respaldo(m["material"], biblioteca)
        tex = ""
        if mat.startswith("tv_"):
            tex = mat + ".jpg"
            img = scene.images.get(mat + ".png")
            if img is not None:
                img.convert("RGB").save(os.path.join(carpeta, tex), "JPEG", quality=86)
        pos = to_yup(m["pos"]).astype(np.float32)
        nrm = to_yup(m["nrm"]).astype(np.float32)
        uv = m["uv"].astype(np.float32)
        v = np.hstack([pos, nrm, uv]).astype("<f4")
        idx = m["faces"].astype("<u2").ravel()
        partes += [s2(m["name"]), s2(mat), s2(m["phys"] or ""), s2(tex),
                   struct.pack("<II", len(v), len(idx)), v.tobytes(), idx.tobytes()]
        n_tri += len(m["faces"])
    with open(path, "wb") as f:
        f.write(b"".join(partes))
    return n_tri


# =========================================================================== datos por teselas
class Teselas:
    """Descarga por teselas de 512 m (en el marco local) con caché en memoria; cada elemento guarda su caja
    lat/lon para dar a cada pedazo solo lo que le toca. Las subclases dicen cómo bajar y cómo identificar."""
    nombre = "datos"
    lado = TESELA_OSM
    paralelo = 2           # Overpass da dos huecos por IP: con más, responde 429

    def __init__(self, proj, log):
        self.proj, self.log = proj, log
        self.datos = {}          # (a, b) -> lista de (elemento, caja lat/lon)
        self.pedidas = {}        # (a, b) -> future
        self.fallos = {}         # (a, b) -> instante del último fallo
        self.red = ThreadPoolExecutor(max_workers=self.paralelo)
        self.cola = {}           # (a, b) -> urgencia (aún sin pedir)
        self.lock = threading.Lock()

    def claves(self, rect):
        x0, y0, x1, y1 = rect
        T = self.lado
        return [(a, b) for a in range(int(math.floor(x0 / T)), int(math.floor(x1 / T)) + 1)
                for b in range(int(math.floor(y0 / T)), int(math.floor(y1 / T)) + 1)]

    def pedir(self, k, urgencia=0.0):
        """Encola la tesela; se bajan primero las más urgentes (antes iban por orden de llegada y las lejanas,
        pedidas antes, tapaban a las que el coche ya necesitaba)."""
        with self.lock:
            if k in self.datos or k in self.pedidas or time.time() - self.fallos.get(k, 0) < 20:
                return
            if urgencia < self.cola.get(k, 1e18):
                self.cola[k] = urgencia
        self._despachar()

    def _despachar(self):
        with self.lock:
            while self.cola and len(self.pedidas) < self.paralelo:
                k = min(self.cola, key=self.cola.get)
                del self.cola[k]
                if k in self.datos or k in self.pedidas:
                    continue
                self.pedidas[k] = self.red.submit(self._bajar, k)

    def olvidar_cola(self):
        """Se vacía en cada paso de la planificación (las urgencias cambian con el coche)."""
        with self.lock:
            self.cola.clear()

    def _bajar(self, k):
        from .geo import LocalProjection
        a, b = k
        t0 = time.time()
        T = self.lado
        p = LocalProjection.marco(self.proj.lat0, self.proj.lon0, (a * T, b * T, (a + 1) * T, (b + 1) * T))
        try:
            lista = self.obtener(p.bbox())
        except Exception as e:  # noqa: BLE001
            self.log(f"{self.nombre} {k}: {e}")
            with self.lock:
                self.fallos[k] = time.time()
                del self.pedidas[k]
            self._despachar()
            return
        with self.lock:
            self.datos[k] = lista
            del self.pedidas[k]
        self._despachar()
        dt = time.time() - t0
        if dt > 0.5:
            self.log(f"{self.nombre} tesela {k}: {len(lista)} elementos en {dt:.1f} s")

    @staticmethod
    def caja(pts):
        xs, ys = zip(*pts)
        return min(ys), min(xs), max(ys), max(xs)

    def listas(self, rect):
        return all(k in self.datos for k in self.claves(rect))

    def elementos(self, rect):
        """Elementos (sin repetir) que tocan `rect` (coordenadas locales)."""
        from .geo import LocalProjection
        bb = LocalProjection.marco(self.proj.lat0, self.proj.lon0, rect).bbox()
        vistos, out = set(), []
        for k in self.claves(rect):
            for el, (s, w, n, e) in self.datos.get(k, []):
                if n < bb.south or s > bb.north or e < bb.west or w > bb.east:
                    continue
                kk = self.ident(el)
                if kk not in vistos:
                    vistos.add(kk)
                    out.append(el)
        return out

    def asegurar(self, rect, espera=90):
        for k in self.claves(rect):
            self.pedir(k)
        t0 = time.time()
        while not self.listas(rect) and time.time() - t0 < espera:
            for k in self.claves(rect):
                self.pedir(k)
            time.sleep(0.2)
        return self.listas(rect)


class TeselasOSM(Teselas):
    nombre = "OSM"

    def obtener(self, bbox):
        from .sources import osm as OSM
        data = OSM.download_fast(bbox, log=self.log)
        lista = []
        for el in data["elements"]:
            if el["type"] == "node":
                pts = [(el["lon"], el["lat"])]
            elif el["type"] == "way":
                pts = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
            else:
                pts = [(g["lon"], g["lat"]) for m in el.get("members", []) for g in m.get("geometry", []) if g]
            if pts:
                lista.append((el, self.caja(pts)))
        return lista

    @staticmethod
    def ident(el):
        return el["type"], el["id"]

    def del_pedazo(self, i, j):
        """Elementos del pedazo (i, j) más, completos, los túneles y puentes que lo cruzan y las calles de sus
        bocas y estribos. Devuelve (elementos, extensión, teselas que faltan). Sin esto, cada pedazo veía solo el
        trozo de túnel que le tocaba y calculaba su perfil por su cuenta: en la costura con el vecino la calzada
        saltaba hasta 9 m (el coche caía al vacío). `extensión` es el rectángulo local que ocupa lo añadido (el
        relieve tiene que llegar hasta ahí); las teselas que faltan hay que bajarlas antes de generarlo."""
        rect = rect_pedazo(i, j)
        els = self.elementos(rect)
        semillas = [el for el in els if _estructura(el)]
        if not semillas:
            return els, None, []
        cx0, cy0, cx1, cy1 = nucleo(i, j)
        tope = (cx0 - EXT_MAX, cy0 - EXT_MAX, cx1 + EXT_MAX, cy1 + EXT_MAX)
        with self.lock:
            todas = [el for k in self.claves(tope) for el, _ in self.datos.get(k, [])]
        por_nodo, por_id = {}, {}
        for el in todas:
            if el.get("type") == "way" and "highway" in (el.get("tags") or {}):
                por_id[el["id"]] = el
                for n in el.get("nodes", []):
                    por_nodo.setdefault(n, []).append(el["id"])
        vistos = {el["id"] for el in els if el.get("type") == "way"}
        cola = [el["id"] for el in semillas]
        hechas = set(cola)
        extra = []
        while cola:
            w = por_id.get(cola.pop())
            if w is None:
                continue
            for n in w.get("nodes", []):
                for o in por_nodo.get(n, []):
                    if o in vistos:
                        if o not in hechas and _estructura(por_id[o]):
                            hechas.add(o)
                            cola.append(o)
                        continue
                    vistos.add(o)
                    extra.append(por_id[o])
                    if _estructura(por_id[o]):
                        hechas.add(o)
                        cola.append(o)
        if not extra:
            return els, None, []
        lon = [g["lon"] for el in extra for g in el.get("geometry", []) if g]
        lat = [g["lat"] for el in extra for g in el.get("geometry", []) if g]
        xs, ys = self.proj.to_local(lon, lat)
        ext = (max(float(xs.min()), tope[0]), max(float(ys.min()), tope[1]),
               min(float(xs.max()), tope[2]), min(float(ys.max()), tope[3]))
        faltan = [k for k in self.claves(ext) if k not in self.datos]
        return els + extra, ext, faltan


class TeselasCatastro(Teselas):
    """Partes de edificio del Catastro (España): el servicio tarda ~25 s por tesela, así que se piden por
    delante como OSM, en una sola petición por tesela."""
    nombre = "Catastro"
    lado = TESELA_CAT
    paralelo = 4

    def obtener(self, bbox):
        from .sources import catastro
        avisos = []
        partes = catastro.download(bbox, log=avisos.append, tile=800.0)
        if not partes and any("no responde" in a or "sin edificios" in a for a in avisos):
            raise RuntimeError("el servicio no respondió")     # se reintenta más tarde (no se guarda vacío)
        return [(p, self.caja(p["outer"])) for p in partes if p["outer"]]

    @staticmethod
    def ident(p):
        return p["id"]


# =========================================================================== el servicio
class Vivo:
    def __init__(self, gen_args, lat0, lon0, carpeta, trabajadores, radio, log=print, radio_fondo=1200.0, zona=None):
        from .geo import LocalProjection
        self.args, self.carpeta, self.radio, self.log = gen_args, carpeta, radio, log
        self.radio_fondo = radio_fondo
        # zona fija que se precalienta entera (ancho, alto en m, centrada en el origen): la app la usa para "genera
        # estos km² y lo demás en vivo"
        self.zona_tam = tuple(zona) if zona else None
        self.zona = None
        if zona:
            w, h = zona
            self.zona = [k for k in self._pedazos_en((-w / 2, -h / 2, w / 2, h / 2))]
            self.zona.sort(key=lambda k: math.hypot(*nucleo(*k)[:2]))
        self.proj = LocalProjection.marco(lat0, lon0, (-1, -1, 1, 1))
        self.osm = TeselasOSM(self.proj, log)
        from . import regions
        R = regions.detect(lat0, lon0) if gen_args.region == "auto" else regions.REGIONS[gen_args.region]
        self.cat = TeselasCatastro(self.proj, log)             if R.cadastre == "catastro" and not gen_args.sin_catastro else None
        if self.cat is not None:
            # con Catastro, las huellas de Microsoft no aportan (salen "0 de Microsoft") y leer su tesela de 20 MB
            # costaba ~1.5 s por pedazo
            gen_args.sin_microsoft = True
        self.trabajadores = trabajadores
        self.listos = {}          # (i, j) -> segundos que tardó (o 0 si venía de caché)
        self.en_curso = {}        # (i, j) -> future
        self.errores = {}         # (i, j) -> (instante, mensaje)
        # un foco por cliente (el visor, AC…): x, y (norte), vx, vy, instante. Con uno solo, el visor y AC abiertos
        # a la vez se lo quitaban el uno al otro y la prioridad saltaba de un coche al otro.
        self.focos = {"inicio": (0.0, 0.0, 0.0, 0.0, time.time())}
        # ruta de prueba para el piloto automático del script de AC (id 0 = apagado)
        self.piloto = {"id": 0}
        self.caminos = {}
        self.ultimo_foco = time.time()
        self.salida = None
        self.lock = threading.Lock()
        os.makedirs(carpeta, exist_ok=True)
        meta = os.path.join(carpeta, "vivo.json")
        previo = json.load(open(meta)) if os.path.exists(meta) else {}
        if previo.get("version") != VERSION:
            for f in os.listdir(carpeta):
                if f.startswith("p_") or f.startswith("tv_"):
                    os.remove(os.path.join(carpeta, f))
        json.dump({"version": VERSION, "lat0": lat0, "lon0": lon0, "epsg": self.proj.epsg}, open(meta, "w"))
        for f in os.listdir(carpeta):
            if f.startswith("p_") and f.endswith(".glb"):
                i, j = (int(v) for v in f[2:-4].split("_"))
                self.listos[(i, j)] = 0.0
        if self.listos:
            log(f"{len(self.listos)} pedazos ya generados en {carpeta}")
        self.pool = ProcessPoolExecutor(max_workers=trabajadores, initializer=_iniciar,
                                        initargs=(vars(gen_args), lat0, lon0))
        # rásteres por adelantado, en procesos aparte (no le quitan el GIL al servidor)
        self.precargador = ProcessPoolExecutor(max_workers=2, initializer=_iniciar_precarga,
                                               initargs=(vars(gen_args), lat0, lon0))
        self.precarga, self.precarga_vuelo = {}, set()
        self.grafo, self.n_grafo, self.rehaciendo = None, -1, False
        self.tiempos = []
        threading.Thread(target=self._bucle, daemon=True).start()
        threading.Thread(target=self._buscar_salida, daemon=True).start()

    # ------------------------------------------------------------------ planificación
    @staticmethod
    def _pedazos_en(rect):
        x0, y0, x1, y1 = rect
        i0, j0 = pedazo_de(x0, y0)
        i1, j1 = pedazo_de(x1, y1)
        return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    def _fondo(self):
        """Lo que se precalienta con los procesos libres: la zona fija (si la hay) y anillos alrededor de cada coche."""
        out = self.deseados(self.radio_fondo) if self.radio_fondo > 0 else []
        if self.zona:
            vistos = {k for _, k in out}
            out = [(160.0 + n * 0.01, k) for n, k in enumerate(self.zona) if k not in vistos] + out
        return out

    def activos(self):
        """Focos de los clientes que han hablado en los últimos 5 s (si ninguno, el último que hubo)."""
        ahora = time.time()
        act = {c: f for c, f in list(self.focos.items()) if ahora - f[4] < 5.0}
        if not act and self.focos:
            c = max(self.focos, key=lambda c: self.focos[c][4])
            act = {c: self.focos[c]}
        return act

    def camino(self, foco):
        """Camino previsto del coche sobre las calles de OSM: desde el tramo de calle más cercano que va en su
        dirección, la continuación más recta en cada cruce (sin bajar de categoría si se puede). Lo largo que
        sea depende de la velocidad (~30 s de viaje). Lista de (x, y, s) con s = metros desde el coche."""
        x, y, vx, vy = foco[:4]
        v = math.hypot(vx, vy)
        if v < 2.0:
            return []
        largo = min(1200.0, max(250.0, v * 32.0))
        hx, hy = vx / v, vy / v
        pts = [(x, y, 0.0)]
        g = self.grafo
        s = 0.0
        if g is not None:
            xy, vec, clase, rejilla = g
            mejor = None
            cx, cy = int(math.floor(x / 50)), int(math.floor(y / 50))
            for i in range(cx - 1, cx + 2):
                for j in range(cy - 1, cy + 2):
                    for a, b in rejilla.get((i, j), ()):
                        (ax, ay), (bx, by) = xy[a], xy[b]
                        dx, dy = bx - ax, by - ay
                        n = math.hypot(dx, dy)
                        if n < 0.5:
                            continue
                        cos = (dx * hx + dy * hy) / n
                        if cos < 0.6:
                            continue
                        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (n * n)))
                        d = math.hypot(ax + t * dx - x, ay + t * dy - y)
                        if d < 25 and (mejor is None or d - 10 * cos < mejor[0]):
                            mejor = (d - 10 * cos, a, b)
            if mejor is not None:
                _, prev, actual = mejor
                bx, by = xy[actual]
                s = math.hypot(bx - x, by - y)
                pts.append((bx, by, s))
                rumbo = math.atan2(by - xy[prev][1], bx - xy[prev][0])
                rango = RANGO.get(clase.get((prev, actual)), 6)
                visto = {prev, actual}
                while s < largo:
                    ops = [n for n in vec.get(actual, ()) if n not in visto and n in xy]
                    if not ops:
                        break

                    def coste(n):
                        r = math.atan2(xy[n][1] - xy[actual][1], xy[n][0] - xy[actual][0])
                        giro = abs((r - rumbo + math.pi) % (2 * math.pi) - math.pi)
                        return giro + 0.35 * max(0, RANGO.get(clase.get((actual, n)), 6) - rango)

                    sig = min(ops, key=coste)
                    (ax, ay), (bx, by) = xy[actual], xy[sig]
                    r = math.atan2(by - ay, bx - ax)
                    if abs((r - rumbo + math.pi) % (2 * math.pi) - math.pi) > 1.9:
                        break          # solo quedan vueltas cerradas: no se adivina más
                    rumbo = r
                    rango = RANGO.get(clase.get((actual, sig)), rango)
                    s += math.hypot(bx - ax, by - ay)
                    pts.append((bx, by, s))
                    visto.add(sig)
                    actual = sig
        # sin calle conocida (o se acabó): recto en la dirección que lleva
        if s < largo:
            lx, ly, _ = pts[-1]
            if len(pts) >= 2:
                dx, dy = lx - pts[-2][0], ly - pts[-2][1]
                n = math.hypot(dx, dy) or 1.0
                hx, hy = dx / n, dy / n
            pts.append((lx + hx * (largo - s), ly + hy * (largo - s), largo))
        # remuestreo cada 20 m
        out = [pts[0]]
        for (ax, ay, sa), (bx, by, sb) in zip(pts, pts[1:]):
            n = max(1, int((sb - sa) // 20))
            for k in range(1, n + 1):
                t = k / n
                out.append((ax + (bx - ax) * t, ay + (by - ay) * t, sa + (sb - sa) * t))
        return out

    def deseados(self, radio, extra_camino=1.0, segundos_camino=None):
        """Pedazos que hacen falta, del más urgente al menos, juntando lo de todos los clientes: los de alrededor
        de cada coche (hasta `radio`) y los que toca su camino previsto (hasta `extra_camino` veces su largo, o
        hasta `segundos_camino` de viaje). Lo de delante pesa menos que la distancia: a 100 km/h importa lo que
        viene."""
        pr = {}
        for c, foco in self.activos().items():
            v = math.hypot(foco[2], foco[3])
            max_camino = max(200.0, v * segundos_camino) if segundos_camino else None
            self._deseados_de(foco, self.caminos.get(c, []), radio, extra_camino, max_camino, pr)
        return sorted((v, k) for k, v in pr.items())

    def _deseados_de(self, foco, cam, radio, extra_camino, max_camino, pr):
        x, y = foco[0], foco[1]
        if extra_camino > 1 and len(cam) >= 2:
            # para los datos: el camino alargado en línea recta desde su final
            (ax, ay, sa), (bx, by, sb) = cam[-2], cam[-1]
            n = math.hypot(bx - ax, by - ay) or 1.0
            extra = sb * (extra_camino - 1)
            cam = cam + [(bx + (bx - ax) / n * e, by + (by - ay) / n * e, sb + e) for e in np.arange(20, extra, 20)]
        r = int(math.ceil(radio / L)) + 1
        ci, cj = pedazo_de(x, y)
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                x0, y0, x1, y1 = nucleo(i, j)
                d = math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
                if d <= radio and d < pr.get((i, j), 1e9):
                    pr[(i, j)] = d
        for px, py, s in cam:
            if max_camino is not None and s > max_camino:
                break
            pi, pj = pedazo_de(px, py)
            for i in (pi - 1, pi, pi + 1):
                for j in (pj - 1, pj, pj + 1):
                    x0, y0, x1, y1 = nucleo(i, j)
                    d = math.hypot(max(x0 - px, 0, px - x1), max(y0 - py, 0, py - y1))
                    if d <= 90:
                        v = s * 0.7 + d * 1.5
                        if v < pr.get((i, j), 1e9):
                            pr[(i, j)] = v

    def _bucle(self):
        while True:
            try:
                self._paso()
            except Exception:  # noqa: BLE001
                self.log(traceback.format_exc())
            time.sleep(0.25)

    def _grafo_al_dia(self):
        """Rehace (en otro hilo) el grafo de calles cuando llegan teselas nuevas de OSM."""
        n = len(self.osm.datos)
        if n == self.n_grafo or self.rehaciendo or n == 0:
            return
        self.rehaciendo = True

        def rehacer():
            try:
                claves = list(self.osm.datos)
                T = self.osm.lado
                a0, a1 = min(a for a, _ in claves), max(a for a, _ in claves)
                b0, b1 = min(b for _, b in claves), max(b for _, b in claves)
                xy, vec, clase = self._vias((a0 * T, b0 * T, (a1 + 1) * T, (b1 + 1) * T))
                rejilla = {}
                for a, vs in vec.items():
                    for b in vs:
                        (ax, ay), (bx, by) = xy[a], xy[b]
                        n_ = max(1, int(math.hypot(bx - ax, by - ay) // 25))
                        celdas = {(int(math.floor((ax + (bx - ax) * k / n_) / 50)),
                                   int(math.floor((ay + (by - ay) * k / n_) / 50))) for k in range(n_ + 1)}
                        for c in celdas:
                            rejilla.setdefault(c, []).append((a, b))
                self.grafo = (xy, vec, clase, rejilla)
                self.n_grafo = len(claves)
            finally:
                self.rehaciendo = False
        threading.Thread(target=rehacer, daemon=True).start()

    def _paso(self):
        # terminados
        for k, fut in list(self.en_curso.items()):
            if not fut.done():
                continue
            del self.en_curso[k]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001  (p. ej. el proceso murió)
                r = {"error": repr(e), "log": []}
            if r.get("error"):
                self.errores[k] = (time.time(), r["error"])
                self.log(f"pedazo {k}: ERROR\n{r['error']}\n" + "\n".join(r.get("log", [])))
            else:
                with self.lock:
                    self.listos[k] = r["seg"]
                self.tiempos.append(r["seg"])
                self.log(f"pedazo {k}: {r['seg']:.1f} s, {r['tri']:,} triángulos")
        self._grafo_al_dia()
        act = self.activos()
        self.caminos = {c: self.camino(f) for c, f in act.items()}
        for c in [c for c in self.focos if c not in act and time.time() - self.focos[c][4] > 60]:
            del self.focos[c]
        # datos por delante: OSM, Catastro y rásteres de lo que se va a generar y de bastante más allá, cada
        # tesela con la urgencia del pedazo más urgente que la necesita
        self.osm.olvidar_cola()
        if self.cat is not None:
            self.cat.olvidar_cola()
        # (incluye el "fondo": lo que se precalienta alrededor de cada coche con los procesos libres)
        datos = self.deseados(max(self.radio + 300, self.radio_fondo + 200), extra_camino=2.5)
        if self.zona:
            datos += [(200.0 + n, k) for n, k in enumerate(self.zona)]
        for pr, k in datos:
            x0, y0, x1, y1 = nucleo(*k)
            for t in self.osm.claves((x0 - MARGEN - PAD_OSM, y0 - MARGEN - PAD_OSM, x1 + MARGEN + PAD_OSM,
                                      y1 + MARGEN + PAD_OSM)):
                self.osm.pedir(t, pr)
            if self.cat is not None:
                for t in self.cat.claves((x0 - MARGEN, y0 - MARGEN, x1 + MARGEN, y1 + MARGEN)):
                    self.cat.pedir(t, pr)
            if k not in self.listos and k not in self.precarga and len(self.precarga_vuelo) < 3:
                self.precarga[k] = self.precargador.submit(_precargar, k[0], k[1])
                self.precarga_vuelo.add(k)
        for k in [k for k in self.precarga_vuelo if self.precarga[k].done()]:
            self.precarga_vuelo.discard(k)
        # generar: el más urgente que ya tenga sus datos
        # para generar, el camino previsto solo cuenta hasta ~20 s de viaje: más allá la previsión falla a menudo
        # (si giras, eso lejano ya no sirve) y ocupaba los procesos mientras faltaba el pedazo de al lado
        urgentes = self.deseados(self.radio, segundos_camino=20.0)
        ya = {k for _, k in urgentes}
        # fondo: con los procesos que sobren, anillos alrededor de cada coche hasta radio_fondo, para que a 100 km/h
        # haya ya generado en cualquier dirección (lo urgente siempre va antes y tiene un proceso reservado)
        fondo = [(max(pr, 151.0), k) for pr, k in self._fondo() if k not in ya]
        for pr, k in urgentes + fondo:
            if len(self.en_curso) >= self.trabajadores:
                break
            # siempre queda un proceso libre para lo cercano (a menos de 150 m del coche)
            if pr > 150 and len(self.en_curso) >= self.trabajadores - 1:
                break
            if k in self.listos or k in self.en_curso or time.time() - self.errores.get(k, (0,))[0] < 60:
                continue
            if k in self.precarga_vuelo:
                continue            # que termine de bajar sus rásteres (dos procesos no deben bajar lo mismo)
            x0, y0, x1, y1 = nucleo(*k)
            rect = rect_pedazo(*k)
            if not self.osm.listas(rect):
                continue
            els, ext, faltan = self.osm.del_pedazo(*k)
            if faltan:                  # un túnel o un puente sigue por teselas aún sin bajar
                for t in faltan:
                    self.osm.pedir(t, pr)
                continue
            partes = None
            if self.cat is not None:
                rc = (x0 - MARGEN, y0 - MARGEN, x1 + MARGEN, y1 + MARGEN)
                # sin Catastro se genera igual (con las alturas de OSM) si su tesela falló, o si el pedazo ya es
                # urgente (el coche está a menos de 120 m): mejor edificios menos exactos que el coche parado
                if not self.cat.listas(rc) and pr > 120 and any(time.time() - self.cat.fallos.get(t, 0) > 20
                                                                for t in self.cat.claves(rc)):
                    continue
                if not self.cat.listas(rc):
                    self.log(f"pedazo {k}: sin esperar al Catastro (urgente)")
                partes = self.cat.elementos(rc)
            self.en_curso[k] = self.pool.submit(_generar, k[0], k[1], els, self.carpeta, partes, ext)

    # ------------------------------------------------------------------ salida y ruta
    def _vias(self, rect, sin_tuneles=False):
        """Grafo de calles conducibles de OSM en `rect`: posiciones locales y vecinos. `sin_tuneles`: para la ruta de
        prueba del piloto automático (en un túnel no hay nada que ver ni que probar)."""
        from . import config as C
        xy, vec, clase = {}, {}, {}
        for el in self.osm.elementos(rect):
            t = el.get("tags", {})
            if el["type"] != "way" or t.get("highway") not in C.DRIVABLE or t.get("area") == "yes":
                continue
            if C.cerrada_a_coches(t):
                continue
            # túneles: solo los que de verdad se construyen (misma regla que roads.RoadNetwork)
            if t.get("tunnel") not in (None, "no", "building_passage"):
                if sin_tuneles:
                    continue
                modo = getattr(self.args, "tuneles", "principales")
                omitido = (t.get("highway") in C.TUNNEL_SKIP_CLASSES or t.get("service") in C.TUNNEL_SKIP_SERVICE
                           or modo == "ninguno"
                           or (modo == "principales" and t.get("highway") not in C.TUNNEL_MAIN_CLASSES))
                if omitido:
                    continue
            if t.get("highway") in ("service", "track") and t.get("service") in ("parking_aisle", "driveway"):
                continue
            g = el.get("geometry") or []
            if len(g) != len(el.get("nodes", [])) or not all(g):
                continue
            x, y = self.proj.to_local([p["lon"] for p in g], [p["lat"] for p in g])
            for n, a, b in zip(el["nodes"], x, y):
                xy[n] = (float(a), float(b))
            ow = t.get("oneway") in ("yes", "1") or t.get("junction") == "roundabout"
            for a, b in zip(el["nodes"], el["nodes"][1:]):
                if t.get("oneway") == "-1":
                    a, b = b, a
                vec.setdefault(a, set()).add(b)
                clase[(a, b)] = t.get("highway")
                if not ow and t.get("oneway") != "-1":
                    vec.setdefault(b, set()).add(a)
                    clase[(b, a)] = t.get("highway")
        return xy, vec, clase

    def _buscar_salida(self):
        """Salida en un buen sitio, no en medio de la nada: entre las calles a menos de ~250 m del origen (dentro
        de lo que se hornea en la pista de AC), la que mejor puntúa por categoría (mejor una avenida que una calle
        de servicio), por tramo recto por delante (para arrancar sin chocar), por edificios alrededor y por
        cercanía al centro; nunca en un puente ni en un túnel."""
        rect = (-450, -450, 450, 450)
        if not self.osm.asegurar(rect, espera=600):
            self.log("sin OSM alrededor del origen: no hay salida")
            return
        xy, vec, clase = self._vias(rect)
        puentes = set()
        edificios = []
        for el in self.osm.elementos(rect):
            t = el.get("tags", {})
            g = el.get("geometry") or []
            if el["type"] != "way" or not g or not all(g):
                continue
            if "building" in t:
                x, y = self.proj.to_local([p["lon"] for p in g], [p["lat"] for p in g])
                edificios.append((float(np.mean(x)), float(np.mean(y))))
            elif "highway" in t and t.get("bridge") not in (None, "no"):
                puentes.update(el.get("nodes", []))
        ed = np.array(edificios) if edificios else np.zeros((0, 2))

        def recto(a, b, maximo=80.0):
            """Metros casi rectos por delante desde a->b siguiendo la continuación más recta."""
            total, prev, act = 0.0, a, b
            rumbo = math.atan2(xy[b][1] - xy[a][1], xy[b][0] - xy[a][0])
            while total < maximo:
                total += math.hypot(xy[act][0] - xy[prev][0], xy[act][1] - xy[prev][1])
                ops = [n for n in vec.get(act, ()) if n != prev and n in xy]
                if not ops:
                    break
                sig = min(ops, key=lambda n: abs((math.atan2(xy[n][1] - xy[act][1], xy[n][0] - xy[act][0]) - rumbo
                                                   + math.pi) % (2 * math.pi) - math.pi))
                giro = abs((math.atan2(xy[sig][1] - xy[act][1], xy[sig][0] - xy[act][0]) - rumbo + math.pi)
                           % (2 * math.pi) - math.pi)
                if giro > 0.35:
                    break
                rumbo = math.atan2(xy[sig][1] - xy[act][1], xy[sig][0] - xy[act][0])
                prev, act = act, sig
            return min(total, maximo)

        best = None
        for a, vs in vec.items():
            for b in vs:
                if a in puentes or b in puentes:
                    continue
                (ax, ay), (bx, by) = xy[a], xy[b]
                dx, dy = bx - ax, by - ay
                l2 = dx * dx + dy * dy
                if l2 < 25:
                    continue
                t = max(0.15, min(0.85, (-ax * dx - ay * dy) / l2))
                px, py = ax + t * dx, ay + t * dy
                d = math.hypot(px, py)
                if d > 250:
                    continue
                cat = RANGO.get(clase.get((a, b)), 6)
                if cat >= 7:            # servicio, pistas de tierra: solo si no hay otra cosa
                    cat += 4
                n_ed = int(np.sum(np.hypot(ed[:, 0] - px, ed[:, 1] - py) < 80)) if len(ed) else 0
                score = d * 0.6 + cat * 30 - recto(a, b) * 0.8 - min(n_ed, 25) * 3
                if best is None or score < best[0]:
                    best = (score, px, py, dx, dy, d, clase.get((a, b)), n_ed)
        if best is None:
            self.log("no hay calles cerca del origen")
            return
        _, px, py, dx, dy, d, cl, n_ed = best
        n = math.hypot(dx, dy)
        self.salida = {"x": round(px, 2), "z": round(-py, 2), "fx": round(dx / n, 4), "fz": round(-dy / n, 4)}
        self.log(f"salida a {d:.0f} m del origen, en una calle {cl} con {n_ed} edificios cerca")

    def ruta(self, metros=2500.0, espera=120.0):
        """Ruta de prueba desde la salida: en cada cruce, la continuación más recta (castiga las calles
        pequeñas). Va bajando las teselas de OSM que necesita. Puntos cada 2 m, en ejes del .glb (x, z)."""
        while self.salida is None:
            time.sleep(0.3)
        s = self.salida
        x, y = s["x"], -s["z"]
        rumbo = math.atan2(-s["fz"], s["fx"])
        puntos = [(x, y)]
        visto = set()
        largo = 0.0
        actual = None
        malas = {"service": 0.6, "track": 0.9, "living_street": 0.4, "unclassified": 0.1}
        rango = None            # categoría de la calle por la que va: no bajar de ella si se puede
        while largo < metros:
            rect = (x - 560, y - 560, x + 560, y + 560)
            if not self.osm.asegurar(rect, espera=espera):
                break               # sin datos todavía más allá: se devuelve lo que hay (se puede volver a pedir)
            xy, vec, clase = self._vias(rect, sin_tuneles=True)
            if actual is None:
                actual = min(xy, key=lambda n: math.hypot(xy[n][0] - x, xy[n][1] - y)
                             - 3.0 * (math.cos(math.atan2(xy[n][1] - y, xy[n][0] - x) - rumbo) > 0))
            cx, cy = x, y
            atascado = False
            while largo < metros:
                ops = [n for n in vec.get(actual, ()) if n not in visto and n in xy]
                if not ops:
                    atascado = True
                    break

                def coste(n):
                    r = math.atan2(xy[n][1] - xy[actual][1], xy[n][0] - xy[actual][0])
                    c = clase.get((actual, n))
                    baja = 0.35 * max(0, RANGO.get(c, 6) - rango) if rango is not None else 0.0
                    return abs((r - rumbo + math.pi) % (2 * math.pi) - math.pi) + malas.get(c, 0) + baja

                sig = min(ops, key=coste)
                rango = RANGO.get(clase.get((actual, sig)), rango)
                (ax, ay), (bx, by) = xy[actual], xy[sig]
                rumbo = math.atan2(by - ay, bx - ax)
                largo += math.hypot(bx - ax, by - ay)
                puntos.append((bx, by))
                visto.add(actual)
                actual = sig
                x, y = bx, by
                if abs(x - cx) > 400 or abs(y - cy) > 400:
                    break           # cerca del borde de lo bajado: bajar más y seguir
            if atascado:
                break
        pts = np.array(puntos)
        seg = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
        sv = np.arange(0, seg[-1], 2.0)
        fx, fy = np.interp(sv, seg, pts[:, 0]), np.interp(sv, seg, pts[:, 1])
        return [[round(float(a), 2), round(float(-b), 2)] for a, b in zip(fx, fy)]

    def activar_piloto(self, metros, kmh):
        puntos = self.ruta(metros, espera=300)
        self.piloto = {"id": int(time.time() * 1000) % 1_000_000_000, "kmh": kmh, "puntos": puntos}
        self.log(f"piloto automático de AC: ruta de {len(puntos) * 2} m a {kmh:.0f} km/h")

    def camino_ac(self, x, z, vx, vz):
        """Puntos previstos por delante del coche en coordenadas de AC ([x, z, metros_desde_coche])."""
        pil = self.piloto
        if pil.get("id") and pil.get("puntos"):
            pts = pil["puntos"]
            cerca = min(range(len(pts)), key=lambda i: (pts[i][0] - x) ** 2 + (pts[i][1] - z) ** 2)
            out, s = [], 0.0
            px0, pz0 = x, z
            for px, pz in pts[cerca::10]:
                s += math.hypot(px - px0, pz - pz0)
                out.append([round(px, 1), round(pz, 1), round(s, 1)])
                px0, pz0 = px, pz
                if s >= 1200.0:
                    break
            return out
        if math.hypot(vx, vz) < 2.0:
            return []
        return [[round(px, 1), round(-py, 1), round(s, 1)]
                for px, py, s in self.camino((x, -z, vx, -vz))]

    # ------------------------------------------------------------------ estado
    def estado(self):
        with self.lock:
            listos = [[i, j] for (i, j) in self.listos]
        fondo = [k for _, k in self.deseados(self.radio_fondo)] if not self.zona else self.zona
        zona = sum(1 for k in fondo if k in self.listos) / max(1, len(fondo))
        return {"origen": {"lat": self.proj.lat0, "lon": self.proj.lon0, "epsg": self.proj.epsg}, "L": L,
                "zona_lista": round(zona, 2),
                "zona_tam": list(self.zona_tam) if self.zona_tam else None,
                "listos": listos, "en_curso": [list(k) for k in self.en_curso],
                "osm_pendientes": len(self.osm.pedidas), "salida": self.salida,
                "t_medio": round(float(np.mean(self.tiempos[-10:])), 1) if self.tiempos else None,
                "errores": len(self.errores),
                "clientes": len(self.activos()),
                "camino": [[round(px, 1), round(-py, 1)] for cam in self.caminos.values() for px, py, _ in cam[::5]]}


# =========================================================================== pista semilla de Assetto Corsa
def semilla_ac(vivo, nombre, carpeta_ac, url, radio=1, log=print):
    """Pista de AC para el modo en vivo: los pedazos alrededor de la salida ya horneados en el .kn5 (así el
    coche arranca sobre suelo firme), una "biblioteca" con todos los materiales y texturas (mallas diminutas
    escondidas bajo tierra) para que el script cree los demás pedazos con ellos, y el script vivo.lua."""
    from . import export_ac, pipeline
    t0 = time.time()
    lat0, lon0 = vivo.proj.lat0, vivo.proj.lon0
    # junto a la carpeta de los pedazos: <salida>/vivo/<nombre> -> <salida>/assetto_corsa/<pista>
    salida = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(vivo.carpeta))), "assetto_corsa")
    raiz = os.path.join(salida, nombre)
    # ya hecha para este mismo sitio y esta versión: solo se reescribe el script (la dirección del servidor puede
    # haber cambiado) y se vuelve a instalar
    geo = os.path.join(raiz, "openlaps_geo.json")
    marca = os.path.join(raiz, "extension", "vivo_version.txt")
    if os.path.exists(geo) and os.path.exists(marca) and open(marca).read().strip() == str(VERSION):
        g = json.load(open(geo, encoding="utf-8"))
        if abs(g["lat0"] - lat0) < 1e-7 and abs(g["lon0"] - lon0) < 1e-7:
            vieja = open(os.path.join(raiz, "extension", "vivo.lua"), encoding="utf-8").read()
            horneados = re.search(r"^local HORNEADOS = \{ (.*) \}", vieja, re.M).group(1)
            _escribir_lua(raiz, url, horneados, lat0, lon0)
            _instalar_semilla(raiz, carpeta_ac, nombre, log)
            log(f"pista semilla lista en {time.time() - t0:.0f} s (reutilizada)")
            return raiz
    while vivo.salida is None:
        time.sleep(0.5)
    _iniciar(vars(vivo.args), lat0, lon0)
    pl = _W["plantilla"]
    from .mesh import Scene
    scene = Scene()
    scene.materials, scene.textures, scene.images = dict(pl.materials), dict(pl.textures), dict(pl.images)
    S = vivo.salida
    sx, sy = S["x"], -S["z"]
    ci, cj = pedazo_de(sx, sy)
    horneados, terr_salida = [], None
    for i in range(ci - radio, ci + radio + 1):
        for j in range(cj - radio, cj + radio + 1):
            x0, y0, x1, y1 = nucleo(i, j)
            rect = rect_pedazo(i, j)
            vivo.osm.asegurar(rect, espera=900)
            for _ in range(4):
                els, ext, faltan = vivo.osm.del_pedazo(i, j)
                if not faltan:
                    break
                vivo.osm.asegurar(ext, espera=600)
            partes = None
            if vivo.cat is not None:
                rc = (x0 - MARGEN, y0 - MARGEN, x1 + MARGEN, y1 + MARGEN)
                vivo.cat.asegurar(rc, espera=300)
                partes = vivo.cat.elementos(rc)
            sc, terr = _escena(i, j, els, partes, ext=ext)
            for k, v in sc.materials.items():
                scene.materials.setdefault(k, v)
            for k, v in sc.textures.items():
                scene.textures.setdefault(k, v)
            for k, v in sc.images.items():
                scene.images.setdefault(k, v)
            for k, piezas in sc.pieces.items():
                scene.pieces[k].extend(piezas)
            if (i, j) == (ci, cj):
                terr_salida = terr
            horneados.append(f"{i}_{j}")
            log(f"semilla: pedazo ({i}, {j}) horneado")
    # biblioteca: un triángulo diminuto por material, 2 km bajo tierra
    scene.material("tv_base", "det_tierra.png" if "det_tierra.png" in scene.textures else next(iter(scene.textures)),
                   specular=0.0, ambient=0.4, diffuse=0.36)
    for k, m in list(scene.materials.items()):
        if k.startswith("tv_") and k != "tv_base":
            continue
        tri = np.array([[sx, sy, -2000.0], [sx + 0.01, sy, -2000.0], [sx, sy + 0.01, -2000.0]])
        scene.add("biblioteca", k, tri, np.zeros((3, 2)), [(0, 1, 2)])
    # salidas sobre la calle más cercana al origen
    fx, fy = S["fx"], -S["fz"]
    sv = np.arange(-120.0, 40.0, 1.0)
    px, py = sx + fx * sv, sy + fy * sv
    poly = np.column_stack([px, py, terr_salida.height(px, py)])
    hw = np.full(len(poly), 3.0)
    i_line = int(np.argmin(np.abs(sv)))
    pipeline._place_grid(scene, poly, hw, i_line, count=8, prefix="AC_START")
    pipeline._place_grid(scene, poly, hw, i_line, count=8, prefix="AC_PIT")
    t, n = pipeline._frame(poly, i_line)
    p = poly[i_line].copy()
    p[2] += 0.3
    scene.dummy("AC_HOTLAP_START_0", p, t)
    for side, nm in ((1, "AC_TIME_0_L"), (-1, "AC_TIME_0_R")):
        scene.dummy(nm, poly[i_line] + np.r_[n * side * 5.0, 0.5], t)
    info = {"name": f"OpenLaps en vivo: {nombre.removesuffix('_vivo')}", "lat": lat0, "lon": lon0, "epsg": vivo.proj.epsg,
            "attribution": pipeline.ATTRIBUTION.format(s2_years=""),
            "description": "Tramo en vivo: la ciudad se genera alrededor del coche mientras conduces "
                           "(necesita el servidor: python -m openlaps.vivo).",
            "tags": ["openlaps", "vivo"], "length": 0, "run": "free roam", "pitboxes": 8}
    raiz = export_ac.write_track(scene, salida, nombre, info, [], None, log=log)
    # CSP solo da la API de física a los scripts de pista si surfaces.ini es "extendido" (WAV_PITCH=extended-N en
    # la primera superficie) y trae [_SCRIPTING_PHYSICS] ALLOW_TRACK_SCRIPTS=1. Sin lo primero,
    # physics.allowed() es false y addTrackSurface responde "Not available" (probado en AC, 2026-11).
    sup = os.path.join(raiz, "data", "surfaces.ini")
    txt = open(sup, encoding="utf-8").read()
    txt = re.sub(r"WAV_PITCH=[^\r\n]*", "WAV_PITCH=extended-0", txt, count=1)
    open(sup, "w", encoding="utf-8", newline="").write(txt)
    for rel, extra in ((("data", "surfaces.ini"), "[_SCRIPTING_PHYSICS]|ALLOW_TRACK_SCRIPTS=1"),
                       (("extension", "ext_config.ini"), "[SCRIPT_0]|SCRIPT=vivo.lua")):
        with open(os.path.join(raiz, *rel), "a", encoding="utf-8", newline="") as f:
            f.write("".join(chr(13) + chr(10) + t for t in [""] + extra.split("|")) + chr(13) + chr(10))
    _escribir_lua(raiz, url, ", ".join(f"['{k}'] = true" for k in horneados), lat0, lon0)
    with open(os.path.join(raiz, "extension", "vivo_version.txt"), "w") as f:
        f.write(str(VERSION))
    _instalar_semilla(raiz, carpeta_ac, nombre, log)
    log(f"pista semilla lista en {time.time() - t0:.0f} s ({len(horneados)} pedazos horneados)")
    return raiz


def _escribir_lua(raiz, url, horneados, lat0, lon0):
    with open(os.path.join(AQUI, "vivo_ac.lua"), encoding="utf-8") as f:
        lua = f.read()
    lua = lua.replace("__URL__", url).replace("__HORNEADOS__", horneados)
    lua = lua.replace("__L__", str(L)).replace("__LAT__", repr(lat0)).replace("__LON__", repr(lon0))
    with open(os.path.join(raiz, "extension", "vivo.lua"), "w", encoding="utf-8") as f:
        f.write(lua)


def _instalar_semilla(raiz, carpeta_ac, nombre, log):
    if carpeta_ac:
        destino = os.path.join(carpeta_ac, "content", "tracks", nombre)
        shutil.copytree(raiz, destino, dirs_exist_ok=True)
        log(f"semilla instalada en {destino}")


# =========================================================================== HTTP
def servidor(vivo, puerto):
    tipos = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".glb": "model/gltf-binary",
             ".json": "application/json", ".jpg": "image/jpeg", ".png": "image/png", ".olv": "application/octet-stream"}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, cuerpo, tipo, codigo=200):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, obj):
            self._enviar(json.dumps(obj).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                if u.path == "/vivo/foco":
                    if "x" in q:
                        cliente = q.get("c") or self.client_address[0] + ("-ac" if "ac" in q else "")
                        vivo.focos[cliente] = (float(q["x"]), -float(q["z"]), float(q.get("vx", 0)),
                                               -float(q.get("vz", 0)), time.time())
                    diag = q.get("ac", "") + (" " + q["err"] if "err" in q else "")
                    if diag and "x" in q:
                        kmh = math.hypot(float(q.get("vx", 0)), float(q.get("vz", 0))) * 3.6
                        diag += f" | coche x={float(q['x']):.0f} z={float(q['z']):.0f} {kmh:.0f} km/h"
                    if diag and diag != getattr(vivo, "diag_ac", None) and time.time() - getattr(vivo, "t_diag", 0) > 4:
                        vivo.t_diag = time.time()
                        vivo.diag_ac = diag
                        vivo.log(f"AC: pedazos {diag}  (cargados, con física, mallas, fallos, espera)")
                    return self._json(vivo.estado())
                if u.path == "/vivo/piloto":
                    # ?metros=4000&kmh=100 activa una ruta de prueba nueva; ?apagar=1 la quita; ?tengo=<id> la
                    # consulta (sin repetir los puntos si el cliente ya la tiene)
                    if "apagar" in q:
                        vivo.piloto = {"id": 0}
                    elif "metros" in q:
                        vivo.activar_piloto(float(q["metros"]), float(q.get("kmh", 50)))
                    if q.get("tengo") == str(vivo.piloto["id"]):
                        return self._json({"id": vivo.piloto["id"]})
                    return self._json(vivo.piloto)
                if u.path == "/vivo/estado":
                    return self._json(vivo.estado())
                if u.path == "/vivo/camino":
                    if "x" in q and "z" in q:
                        x, z = float(q["x"]), float(q["z"])
                        vx, vz = float(q.get("vx", 0)), float(q.get("vz", 0))
                    else:
                        f = next(iter(vivo.activos().values()), (0.0, 0.0, 0.0, 0.0, 0.0))
                        x, z, vx, vz = f[0], -f[1], f[2], -f[3]
                    return self._json({"camino": vivo.camino_ac(x, z, vx, vz),
                                       "zona_tam": list(vivo.zona_tam) if vivo.zona_tam else None,
                                       "salida": vivo.salida})
                if u.path == "/vivo/ruta":
                    return self._json({"puntos": vivo.ruta(float(q.get("metros", 2500)), float(q.get("espera", 120)))})
                if u.path.startswith("/vivo/p/"):
                    nombre = os.path.basename(u.path)
                    ruta = os.path.join(vivo.carpeta, nombre)
                else:
                    rel = u.path.lstrip("/") or "index.html"
                    ruta = os.path.normpath(os.path.join(VISOR, rel))
                    if not ruta.startswith(VISOR):
                        return self._enviar(b"no", "text/plain", 403)
                if not os.path.isfile(ruta):
                    return self._enviar(b"no existe", "text/plain", 404)
                with open(ruta, "rb") as f:
                    self._enviar(f.read(), tipos.get(os.path.splitext(ruta)[1], "application/octet-stream"))
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), H)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    from .__main__ import parser as parser_openlaps
    p = argparse.ArgumentParser(prog="openlaps.vivo", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--centro", required=True, help="lat,lon del punto de partida (origen de coordenadas)")
    p.add_argument("--puerto", type=int, default=8790)
    p.add_argument("--trabajadores", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 3)))
    p.add_argument("--radio", type=float, default=300.0, help="genera los pedazos a menos de esta distancia (m)")
    p.add_argument("--radio-fondo", type=float, default=None,
                   help="con los procesos libres, precalienta hasta esta distancia alrededor de cada coche (m; "
                        "por defecto 1200, o 600 con --zona)")
    p.add_argument("--zona", metavar="ANCHO,ALTO",
                   help="precalienta entera esta zona (m, centrada en --centro) antes que lo demás")
    p.add_argument("--carpeta", help="dónde guardar los pedazos (por defecto salida/vivo/<centro>)")
    p.add_argument("--nuevo", action="store_true", help="borra los pedazos ya generados de este centro")
    p.add_argument("--piloto-ac", type=float, metavar="METROS",
                   help="ruta de prueba de este largo para el piloto automático del script de AC (conduce solo)")
    p.add_argument("--piloto-kmh", type=float, default=50.0, help="velocidad máxima del piloto automático")
    p.add_argument("--semilla-ac", action="store_true",
                   help="genera (e instala) la pista de Assetto Corsa para este centro y sigue sirviendo")
    p.add_argument("--nombre-ac", default="openlaps_vivo", help="carpeta de la pista de AC")
    p.add_argument("--ac-carpeta", default=r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa",
                   help="carpeta de Assetto Corsa donde instalar la semilla ('' para no instalar)")
    a, resto = p.parse_known_args(argv)
    lat0, lon0 = (float(v) for v in a.centro.split(","))
    gen = parser_openlaps().parse_args(["--centro", a.centro, "--tesela", "64", "--sin-horizonte"] + resto)
    carpeta = a.carpeta or os.path.join("salida", "vivo", f"{lat0:.5f}_{lon0:.5f}")
    if a.nuevo and os.path.isdir(carpeta):
        shutil.rmtree(carpeta)

    def log(m):
        print(time.strftime("%H:%M:%S"), m, flush=True)

    zona = tuple(float(v) for v in a.zona.split(",")) if a.zona else None
    vivo = Vivo(gen, lat0, lon0, carpeta, a.trabajadores, a.radio, log=log,
                radio_fondo=a.radio_fondo if a.radio_fondo is not None else (600.0 if zona else 1200.0), zona=zona)
    srv = servidor(vivo, a.puerto)
    if a.semilla_ac:
        def hacer_semilla():
            try:
                semilla_ac(vivo, a.nombre_ac, a.ac_carpeta if a.ac_carpeta and os.path.isdir(a.ac_carpeta) else None,
                           f"http://127.0.0.1:{a.puerto}", log=log)
            except Exception:  # noqa: BLE001
                log("semilla: ERROR\n" + traceback.format_exc())
        threading.Thread(target=hacer_semilla, daemon=True).start()
    if a.piloto_ac:
        threading.Thread(target=vivo.activar_piloto, args=(a.piloto_ac, a.piloto_kmh), daemon=True).start()
    log(f"tramo en vivo: http://127.0.0.1:{a.puerto}/?vivo=1  ({a.trabajadores} trabajadores, pedazos de {L:.0f} m)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        vivo.pool.shutdown(wait=False, cancel_futures=True)
        vivo.precargador.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    from openlaps.vivo import main as _main   # así los procesos hijos encuentran las funciones por su módulo
    sys.exit(_main())
