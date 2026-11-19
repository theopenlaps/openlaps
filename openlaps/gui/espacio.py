"""Cuánto disco ocupa todo lo de OpenLaps y cómo recuperarlo.

Mide las pistas, las zonas del modo en vivo, lo que hay copiado dentro de Assetto Corsa, la caché de descargas, el
Python del ejecutable y los datos de la ventana; borra una pista o una zona con todo lo suyo, y quita lo que ya no
sirve (pedazos de una versión anterior, descargas a medias, la caché del instalador). La caché de descargas tiene
un tope: al pasarlo se borra lo que lleva más tiempo sin usarse.
"""
import json
import os
import shutil
import time
from pathlib import Path

from . import ajustes, assetto
from .textos import t


def _local():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OpenLaps"


def _cache_dir():
    from ..sources import cache
    return Path(cache.CACHE_DIR)


def tam(ruta):
    """Bytes de un archivo o de una carpeta entera (sin seguir enlaces: una pista enlazada en AC no cuenta doble)."""
    p = Path(ruta)
    try:
        if not os.path.lexists(p):
            return 0
        if p.is_file():
            return p.stat().st_size
    except OSError:
        return 0
    total, pila = 0, [str(p)]
    while pila:
        try:
            with os.scandir(pila.pop()) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if assetto._destino_de_enlace(e.path) is None:      # un enlace (junction) no se recorre
                                pila.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _archivos_pista(sal, n):
    return [sal / f"{n}{suf}" for suf in (".glb", ".json", "_cenital.jpg", "_cobertura.jpg")]


def _en_ac(ac, nombre):
    """Carpeta de esa pista dentro de AC si es una copia de verdad hecha por OpenLaps (no un enlace), o None."""
    if not ac:
        return None
    d = Path(ac) / "content" / "tracks" / nombre
    if d.is_dir() and assetto._destino_de_enlace(d) is None and \
            ((d / "openlaps_geo.json").exists() or (d / "extension" / "vivo.lua").exists()):
        return d
    return None


def tam_pista(sal, n):
    return sum(tam(f) for f in _archivos_pista(sal, n)) + tam(sal / "assetto_corsa" / n)


def tam_vivo(sal, n, ac=None):
    d = _en_ac(ac, f"{n}_vivo")
    return tam(sal / "vivo" / n) + tam(sal / "assetto_corsa" / f"{n}_vivo") + (tam(d) if d else 0)


def medir(a=None):
    """Todo lo que ocupa OpenLaps en este equipo: {"total", "partes": [{clave, bytes, ruta, n}], "tope_cache"}."""
    a = a or ajustes.cargar()
    sal = ajustes.salida(a)
    ac = assetto.detectar(a.get("ac"))
    pistas = [g.stem for g in sal.glob("*.glb")] if sal.is_dir() else []
    b_pistas = sum(tam_pista(sal, n) for n in pistas)
    zonas = [d.name for d in (sal / "vivo").iterdir() if d.is_dir()] if (sal / "vivo").is_dir() else []
    b_vivo = sum(tam(sal / "vivo" / n) for n in zonas)
    # pistas de AC del modo en vivo (la de la carpeta de salida y su copia dentro del juego) y restos sin .glb
    ac_dir = sal / "assetto_corsa"
    otras = [d for d in ac_dir.iterdir() if d.is_dir() and d.name not in pistas] if ac_dir.is_dir() else []
    b_semillas = sum(tam(d) for d in otras)
    copias = []
    if ac:
        try:
            copias = [d for d in (Path(ac) / "content" / "tracks").iterdir() if _en_ac(ac, d.name)]
        except OSError:
            copias = []
    b_copias = sum(tam(d) for d in copias)
    resto = tam(sal) - b_pistas - b_vivo - b_semillas if sal.is_dir() else 0
    cache = _cache_dir()
    local = _local()
    partes = [
        {"clave": "pistas", "bytes": b_pistas, "ruta": str(sal), "n": len(pistas)},
        {"clave": "vivo", "bytes": b_vivo, "ruta": str(sal / "vivo"), "n": len(zonas)},
        {"clave": "vivo_ac", "bytes": b_semillas + b_copias, "ruta": str(ac_dir), "n": len(otras) + len(copias)},
        {"clave": "cache", "bytes": tam(cache), "ruta": str(cache), "vaciable": True},
        {"clave": "programa", "bytes": tam(local / "runtime") + tam(ajustes.RAIZ / "openlaps") + tam(ajustes.VISOR),
         "ruta": str(local if (local / "runtime").is_dir() else ajustes.RAIZ)},
        {"clave": "ventana", "bytes": tam(ajustes.DATOS), "ruta": str(ajustes.DATOS)},
    ]
    if resto > 5e6:
        partes.insert(3, {"clave": "otros", "bytes": resto, "ruta": str(sal)})
    return {"total": sum(p["bytes"] for p in partes), "partes": partes,
            "tope_cache": float(a.get("cache_max_gb") or 0)}


# ------------------------------------------------------------------ borrar
def _rm(ruta):
    """Borra un archivo o una carpeta. Devuelve los bytes liberados; RuntimeError si Windows no deja."""
    p = Path(ruta)
    if not os.path.lexists(p):
        return 0
    n = tam(p)
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    except OSError as e:
        raise RuntimeError(t("borrar_en_uso", n=p.name)) from e
    return n


def borrar_pista(ac, sal, nombre):
    """Borra una pista generada: su .glb y fichas, su carpeta de Assetto Corsa y el enlace en el juego."""
    sal = Path(sal)
    origen = sal / "assetto_corsa" / nombre
    if ac and origen.is_dir() and assetto.estado(ac, origen) == "enlazada" \
            and not assetto._quitar_enlace(Path(ac) / "content" / "tracks" / nombre):
        raise RuntimeError(t("ac_en_uso", n=nombre))
    libres = _rm(origen)
    for f in _archivos_pista(sal, nombre):
        libres += _rm(f)
    assetto.limpiar_rotos(ac)
    return libres


def borrar_vivo(ac, sal, nombre):
    """Borra una zona del modo en vivo: sus pedazos, su pista de AC y la copia de esa pista dentro del juego."""
    sal = Path(sal)
    libres = 0
    copia = _en_ac(ac, f"{nombre}_vivo")
    if copia:
        libres += _rm(copia)
    libres += _rm(sal / "assetto_corsa" / f"{nombre}_vivo")
    libres += _rm(sal / "vivo" / nombre)
    return libres


# ------------------------------------------------------------------ limpieza
def limpiar_obsoletos(a=None, log=None):
    """Quita lo que ya no sirve y nadie va a pedir: pedazos del modo en vivo de una versión anterior (la app los
    regeneraría al abrir la zona, pero los de una zona que no se vuelve a abrir se quedaban para siempre), descargas
    a medias, índices de un formato anterior y la caché del instalador. Devuelve los bytes liberados."""
    from .. import vivo
    a = a or ajustes.cargar()
    libres = 0

    def quitar(p):
        nonlocal libres
        try:
            n = tam(p)
            if Path(p).is_dir():
                shutil.rmtree(p)
            else:
                os.remove(p)
            libres += n
        except OSError:
            pass

    raiz = ajustes.salida(a) / "vivo"
    if raiz.is_dir():
        for d in raiz.iterdir():
            meta = d / "vivo.json"
            if not d.is_dir() or not meta.exists():
                continue
            try:
                version = json.loads(meta.read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError):
                continue
            if version != vivo.VERSION:
                for f in d.iterdir():
                    if f.is_file() and f.name.startswith(("p_", "tv_")):
                        quitar(f)
            # una zona que se quedó sin nada (ni pedazos, ni opciones para reanudarla, ni pista de AC): fuera
            try:
                vacia = {f.name for f in d.iterdir()} <= {"vivo.json"}
            except OSError:
                vacia = False
            if vacia and not (raiz.parent / "assetto_corsa" / f"{d.name}_vivo").exists():
                quitar(d)
    cache = _cache_dir()
    if cache.is_dir():
        hace_un_dia = time.time() - 86400
        for f in cache.iterdir():
            try:
                viejo = f.stat().st_mtime < hace_un_dia
            except OSError:
                continue
            if (f.name.endswith(".part") and viejo) or \
                    (f.name.endswith(".idx.sqlite") and ".v2." not in f.name):       # índice de un formato anterior
                quitar(f)
    inst = _local() / "runtime" / "cache"      # lo que bajó el instalador: ya está instalado en runtime/env
    if inst.is_dir() and (_local() / "runtime" / "env").is_dir():
        quitar(inst)
    if log and libres:
        log(f"limpieza: {libres / 1e6:.0f} MB de archivos que ya no servían")
    return libres


def podar_cache(max_gb, protegido_s=3600.0):
    """Deja la caché de descargas por debajo de `max_gb` borrando lo que lleva más tiempo sin usarse (lo de la
    última hora no se toca: puede ser de una generación en curso). 0 = sin tope. Devuelve los bytes liberados."""
    cache = _cache_dir()
    if not max_gb or max_gb <= 0 or not cache.is_dir():
        return 0
    arch = []
    for r, _ds, fs in os.walk(cache):
        for f in fs:
            p = os.path.join(r, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            arch.append((max(st.st_atime, st.st_mtime), st.st_size, p))
    total = sum(x[1] for x in arch)
    tope = max_gb * 1e9
    libres = 0
    ahora = time.time()
    for usado, n, p in sorted(arch):
        if total - libres <= tope or ahora - usado < protegido_s:
            break
        try:
            os.remove(p)
            libres += n
        except OSError:
            pass
    return libres


def vaciar_cache():
    """Borra toda la caché de descargas (se vuelve a bajar lo que haga falta)."""
    cache = _cache_dir()
    libres = 0
    if cache.is_dir():
        for f in cache.iterdir():
            try:
                n = tam(f)
                if f.is_dir():
                    shutil.rmtree(f)
                else:
                    f.unlink()
                libres += n
            except OSError:
                pass
    return libres


def mantenimiento(log=None):
    """Lo que la app hace sola al abrirse (en segundo plano): limpiar lo obsoleto y aplicar el tope de la caché."""
    a = ajustes.cargar()
    libres = limpiar_obsoletos(a, log=log)
    libres += podar_cache(float(a.get("cache_max_gb") or 0))
    return libres
