"""Servidor HTTP local (solo 127.0.0.1) para la interfaz, el visor, las pistas y la API.

/app/…        -> openlaps/gui/web
/viewer/…     -> viewer/ (el visor three.js de siempre)
/salida/…     -> carpeta de salida configurada
/borrador/…   -> vista previa 3D (en %APPDATA%)
POST /api/<m> -> Api.<m>(*args)       (cabecera X-OpenLaps-Token)
GET  /api/eventos?token=… -> progreso (text/event-stream)

El token aleatorio evita que una página web cualquiera abierta en el navegador pueda mandar
órdenes a este servidor: sin él no se responde, y una cabecera propia obliga a una comprobación
CORS que aquí nunca se concede.
"""
import functools
import hmac
import json
import mimetypes
import os
import queue
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("text/javascript", ".js")


class Eventos:
    """Reparte cada evento a todas las conexiones abiertas de /api/eventos."""

    def __init__(self):
        self._subs = []
        self._lock = threading.Lock()

    def emitir(self, ev):
        with self._lock:
            for q in self._subs:
                q.put(ev)

    def suscribir(self):
        q = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def baja(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


class _Manejador(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, raices, api, eventos, token, **k):
        self.raices, self.api, self.eventos, self.token = raices, api, eventos, token
        super().__init__(*a, **k)

    # ---------------------------------------------------------------- archivos
    def translate_path(self, path):
        partes = unquote(urlsplit(path).path).lstrip("/").split("/", 1)
        base = self.raices.get(partes[0])
        if base is None:
            return ""
        # normpath y no resolve(): el Python de la Microsoft Store redirige %APPDATA% a otra carpeta
        # y resolve() devolvería esa ruta, que ya no estaría «dentro» de la base. normpath quita los ..
        base = Path(os.path.abspath(base() if callable(base) else base))
        p = Path(os.path.normpath(base / (partes[1] if len(partes) > 1 else "")))
        return str(p) if p == base or p.is_relative_to(base) else ""

    def send_head(self):
        if not self.translate_path(self.path):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        # las pistas se regeneran con el mismo nombre: nada de caché
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- API
    def _token_ok(self, valor):
        return hmac.compare_digest(str(valor or ""), self.token)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/api/eventos":
            return self._eventos(parse_qs(u.query).get("token", [""])[0])
        return super().do_GET()

    def do_POST(self):
        u = urlsplit(self.path)
        metodo = u.path.removeprefix("/api/")
        if not u.path.startswith("/api/") or not self._token_ok(self.headers.get("X-OpenLaps-Token")):
            return self._json(403, {"error": "prohibido"})
        f = getattr(self.api, metodo, None)
        if metodo.startswith("_") or not callable(f):
            return self._json(404, {"error": f"no existe {metodo}"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(n) or b"[]") if n else []
            res = f(*(args if isinstance(args, list) else [args]))
        except Exception as e:  # noqa: BLE001 - que la interfaz vea el error en vez de colgarse
            return self._json(500, {"error": f"{e.__class__.__name__}: {e}"})
        return self._json(200, res)

    def _json(self, code, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _eventos(self, token):
        if not self._token_ok(token):
            return self._json(403, {"error": "prohibido"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        q = self.eventos.suscribir()
        try:
            self.wfile.write(b": hola\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    msg = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    msg = ": sigo\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.eventos.baja(q)


def iniciar(raices, api, eventos, token, puerto=0):
    srv = ThreadingHTTPServer(("127.0.0.1", puerto), functools.partial(
        _Manejador, raices=raices, api=api, eventos=eventos, token=token))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
