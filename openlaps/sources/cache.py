"""Descargas HTTP con caché en disco (para no volver a pedir lo mismo)."""
import hashlib
import os
import time
import urllib.parse
import urllib.request

from .. import URL, __version__

UA = f"openlaps/{__version__} (+{URL})"
CACHE_DIR = os.environ.get("OPENLAPS_CACHE") or os.path.expanduser("~/.cache/openlaps")


def cache_path(key, ext=""):
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.sha1(key.encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, h + ext)


def fetch(url, data=None, ext="", retries=3, timeout=300, log=print):
    key = url + ("?" + data if data else "")
    path = cache_path(key, ext)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    body = data.encode() if data else None
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(path + ".part", "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(path + ".part", path)
            return path
        except Exception as e:  # noqa: BLE001 - reintento genérico de red
            last = e
            log(f"  aviso: fallo descargando ({e}); reintento {attempt + 1}/{retries}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


def post_form(url, fields, **kw):
    return fetch(url, data=urllib.parse.urlencode(fields), **kw)
