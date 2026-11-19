"""Ventana principal: pywebview (WebView2) + API que la interfaz llama como window.pywebview.api.*"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, URL
from .. import regions, vivo
from . import ajustes, assetto, servidor
from . import espacio
from .envivo import EnVivo, _puerto_libre, esperar_puerto
from .google3d import Google3D
from .generador import MAX_KM2, Trabajo, argumentos, nombre_valido
from .precarga import Precarga, _clave, caja_de
from .textos import idioma, t

_UA = f"OpenLaps/{__version__} (+{URL})"
_COORDS = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


class Api:
    """Todo lo público aquí lo puede llamar la interfaz (devuelve promesas en JS)."""

    def __init__(self, eventos):
        self._eventos = eventos
        self._ventana = None    # None en modo navegador (sin diálogos nativos)
        self._marco = None      # barra de título propia (solo en la ventana)
        self._trabajo = None
        self._precarga = Precarga()
        self._n = 0             # id de trabajo: la interfaz descarta los eventos de uno ya sustituido
        self._vivo = EnVivo(self._emitir)
        self._vivo_ac = EnVivo(lambda ev: None)
        self._vivo_ac_lock = threading.Lock()
        self._puerto_control_ac = None
        self._g3d = Google3D()
        self._rev_cache = {}

    # ------------------------------------------------------------------ estado
    def iniciar(self):
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        assetto.limpiar_rotos(ac)      # enlaces a pistas que ya no existen: Content Manager las da por rotas
        return {"version": __version__, "ajustes": a, "salida": str(ajustes.salida(a)),
                "ac": ac, "csp": assetto.csp(ac), "max_km2": MAX_KM2, "pistas": self.pistas(),
                "vivo": self._vivo.estado()}

    def ventana_estado(self):
        return self._marco.estado() if self._marco else {"marco": False}

    def ventana_accion(self, que):
        return self._marco.accion(str(que)) if self._marco else {"marco": False}

    def guardar_ajustes(self, cambios):
        res = ajustes.guardar(cambios or {})
        if any(str(k).startswith("vivo_") for k in (cambios or {})):
            self._notificar_vivo_config(ajustes.vivo_params(res))
        return res

    def _lugar_cerca(self, lat, lon):
        k = (round(lat, 2), round(lon, 2), idioma())
        if k in self._rev_cache:
            return self._rev_cache[k]
        res = ("", "")
        try:
            q = urllib.parse.urlencode({"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "format": "jsonv2",
                                        "zoom": 10, "addressdetails": 1, "accept-language": idioma()})
            req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}", headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=3.5) as r:
                addr = json.load(r).get("address") or {}
            zona = (addr.get("city") or addr.get("town") or addr.get("state") or addr.get("county")
                    or addr.get("village") or addr.get("municipality") or "")
            res = (addr.get("country") or "", zona)
        except Exception:  # noqa: BLE001
            pass
        if len(self._rev_cache) > 64:
            self._rev_cache.pop(next(iter(self._rev_cache)))
        self._rev_cache[k] = res
        return res

    def region(self, lat, lon, elegida=None):
        """Perfil de ese punto. `elegida`: el país elegido a mano en la interfaz (sus subregiones son las que se
        ofrecen; la automática se calcula dentro de él)."""
        la, lo = float(lat), float(lon)
        r = regions.detect(la, lo)
        pais_nom, zona = self._lugar_cerca(la, lo)
        pais = r.name if r.code not in ("eu", "generic") else (pais_nom or "")
        base = regions.REGIONS.get(elegida) or r
        en = idioma() == "en"
        auto = regions.detect_sub(base, la, lo)
        return {"codigo": r.code, "nombre": r.name, "orto": r.ortho or "", "relieve": r.dem or "",
                "pais": pais, "zona": zona, "sub": auto.sub,
                "subs": [{"codigo": s.sub, "nombre": s.sub_name_en if en else s.sub_name,
                          "colores": ["#%02x%02x%02x" % tuple(c) for c in s.facade_colors]}
                         for s in regions.subregiones(base.code)],
                "colores": ["#%02x%02x%02x" % tuple(c) for c in base.facade_colors]}

    # ------------------------------------------------------------------ vista previa de OSM
    def vista_osm(self, op):
        """Calles, edificios y agua de la zona para dibujarlos en el mapa. Es la misma descarga de
        Overpass que hará el generador (ver precarga.py): mientras el usuario mira el mapa ya se
        está bajando lo que luego necesita el borrador."""
        try:
            k = self._precarga.pedir(caja_de(op))
            datos = self._precarga.esperar(k)
        except (ValueError, KeyError, TypeError):
            return {"vias": []}
        except Exception as e:  # noqa: BLE001
            return {"error": t("osm_fallo", e=e.__class__.__name__)}
        if datos is None:
            return {"superado": True}
        claves = ("highway", "building", "natural", "waterway", "area")
        vias = []
        for el in datos.get("elements", []):
            if el.get("type") != "way" or "geometry" not in el:
                continue
            tags = {k: v for k, v in (el.get("tags") or {}).items() if k in claves}
            if not tags or (tags.get("natural") and tags["natural"] != "water"):
                continue
            vias.append({"t": tags, "g": [[round(g["lat"], 6), round(g["lon"], 6)] for g in el["geometry"] if g],
                         "n": el.get("nodes", []) if "highway" in tags else []})
        return {"vias": vias}

    # ------------------------------------------------------------------ búsqueda
    def buscar(self, texto):
        texto = (texto or "").strip()
        m = _COORDS.match(texto)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return [{"nombre": f"{lat:.5f}, {lon:.5f}", "detalle": t("coordenadas"), "lat": lat, "lon": lon}]
        if len(texto) < 2:
            return []
        q = urllib.parse.urlencode({"q": texto, "format": "jsonv2", "limit": 6, "addressdetails": 1,
                                    "accept-language": idioma()})
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}", headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                datos = json.load(r)
        except OSError as e:
            return {"error": t("buscar_fallo", e=e.__class__.__name__)}
        res = []
        for d in datos:
            partes = d.get("display_name", "").split(", ")
            addr = d.get("address") or {}
            zona = (addr.get("city") or addr.get("town") or addr.get("state") or addr.get("county")
                    or addr.get("village") or addr.get("municipality") or partes[0])
            res.append({"nombre": partes[0], "detalle": ", ".join(partes[1:4]),
                        "pais": addr.get("country") or "", "zona": zona,
                        "lat": float(d["lat"]), "lon": float(d["lon"]),
                        "caja": [float(v) for v in d.get("boundingbox", [])] or None})
        return res

    # ------------------------------------------------------------------ generación
    def generar(self, op):
        return self._lanzar("generar", op)

    def borrador(self, op):
        return self._lanzar("borrador", dict(op, calidad="borrador"))

    def cancelar(self, tipo=None):
        """Cancela el trabajo en curso; con tipo, solo si es de ese tipo («borrador»)."""
        if self._trabajo and self._trabajo.activo and (tipo is None or self._trabajo.tipo == tipo):
            self._trabajo.cancelar()
        return True

    def _lanzar(self, tipo, op):
        actual = self._trabajo
        if actual and actual.activo:
            if actual.tipo == "generar":
                return {"error": t("ocupado"), "ocupado": True}
            actual.cancelar()    # un borrador nuevo (o generar) sustituye al borrador en curso
        try:
            if tipo == "borrador":
                ajustes.BORRADOR.mkdir(parents=True, exist_ok=True)
                argv = argumentos(op, ajustes.BORRADOR, nombre="borrador")
                nombre = "borrador"
            else:
                salida = ajustes.salida()
                salida.mkdir(parents=True, exist_ok=True)
                nombre = nombre_valido(op.get("nombre"))
                argv = argumentos(op, salida, nombre=nombre)
        except (ValueError, KeyError, TypeError) as e:
            return {"error": str(e) or t("faltan_datos")}
        except OSError as e:
            return {"error": t("carpeta_salida", e=e)}

        self._n += 1
        ident = self._n

        def al_evento(ev):
            ev["id"] = ident
            if ev.get("estado") == "listo":
                if tipo == "borrador":
                    ev["glb"] = f"/borrador/borrador.glb?t={int(time.time())}"
                else:
                    ev["pista"] = self._pista(ajustes.salida() / f"{nombre}.glb")
            self._emitir(ev)

        antes = None
        trabajo_ref = [None]
        try:
            k = _clave(caja_de(op))
            if self._precarga.descargando(k):
                antes = lambda: self._precarga.esperar(k, cancelar=lambda: bool(trabajo_ref[0] and trabajo_ref[0].cancelado))  # noqa: E731
        except (ValueError, KeyError, TypeError):
            pass
        self._trabajo = Trabajo(tipo, argv, al_evento, antes=antes, prioridad_baja=tipo == "borrador")
        trabajo_ref[0] = self._trabajo
        try:
            self._trabajo.iniciar()
        except OSError as e:
            return {"error": t("no_inicia", e=e)}
        return {"ok": True, "id": ident, "nombre": nombre, "comando": "openlaps " + " ".join(argv)}

    def _emitir(self, ev):
        if ev.get("tipo") == "vivo" and ev.get("estado") == "semilla":
            self._precalentar_google()
        self._eventos.emitir(ev)

    def _precalentar_google(self):
        a = ajustes.cargar()
        token = str(a.get("token_cesium") or "").strip()
        if not a.get("google3d") or not token or not self._vivo.activo or not self._vivo.nombre:
            return
        ac = assetto.detectar(a.get("ac"))
        if not ac:
            return
        nombre = f"{self._vivo.nombre}_vivo"
        pista = Path(ac) / "content" / "tracks" / nombre
        vivo_dir = ajustes.salida() / "vivo" / self._vivo.nombre
        vivo_url = f"http://127.0.0.1:{self._vivo.puerto}" if self._vivo.puerto else None
        threading.Thread(
            target=lambda: self._g3d.preparar(pista, None, True, token, vivo=vivo_dir, vivo_url=vivo_url),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------ pistas
    def pistas(self):
        sal = ajustes.salida()
        if not sal.is_dir():
            return []
        glbs = sorted(sal.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)
        return self._zonas_vivo() + [p for p in (self._pista(g) for g in glbs) if p]

    def _zonas_vivo(self):
        """Zonas del modo en vivo preparadas desde la app (salida/vivo/<nombre>/openlaps_app.json): salen en «Mis
        pistas» como las demás, para volver a abrirlas sin dibujar la zona otra vez."""
        out = []
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        sal = ajustes.salida(a)
        # también las de antes de que la app las apuntara y las pistas de AC que quedaron sin sus pedazos: no se
        # pueden reanudar (no se sabe con qué opciones se hicieron), pero sí ver cuánto ocupan y borrarlas
        nombres = {d.name for d in (sal / "vivo").iterdir() if d.is_dir()} if (sal / "vivo").is_dir() else set()
        if (sal / "assetto_corsa").is_dir():
            nombres |= {d.name[:-5] for d in (sal / "assetto_corsa").iterdir() if d.is_dir() and d.name.endswith("_vivo")}
        for n in sorted(nombres):
            carpeta = sal / "vivo" / n
            f = carpeta / "openlaps_app.json"
            d, centro = {}, None
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                centro = [float(v) for v in d["op"]["centro"]]
            except (OSError, ValueError, KeyError, TypeError):
                d = {}
                try:
                    v = json.loads((carpeta / "vivo.json").read_text(encoding="utf-8"))
                    centro = [float(v["lat0"]), float(v["lon0"])]
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            semilla = sal / "assetto_corsa" / f"{n}_vivo"
            try:
                st = (f if f.exists() else carpeta if carpeta.is_dir() else semilla).stat()
            except OSError:
                continue
            out.append({
                "nombre": n, "vivo": True, "glb": None, "cenital": None, "mb": None, "modo": "libre",
                "reanudable": bool(d.get("op")),
                "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"), "_t": st.st_mtime,
                "centro": centro, "zona": d.get("zona"),
                "pedazos": sum(1 for _ in carpeta.glob("p_*.glb")) if carpeta.is_dir() else 0,
                "activo": bool(self._vivo.activo and self._vivo.nombre == n),
                "ac": (semilla / f"{n}_vivo.kn5").exists(),
                "ac_estado": "enlazada" if ac and os.path.lexists(Path(ac) / "content" / "tracks" / f"{n}_vivo") else "no",
                "trafico": False, "mb": round(espacio.tam_vivo(ajustes.salida(a), n, ac) / 1e6),
            })
        out.sort(key=lambda p: p.pop("_t"), reverse=True)
        return out

    # ------------------------------------------------------------------ espacio en disco
    def espacio(self):
        return espacio.medir()

    def _ocupado(self):
        return bool(self._trabajo and self._trabajo.activo and self._trabajo.tipo == "generar")

    def borrar_pista(self, nombre):
        """Borra una pista generada (también de Assetto Corsa)."""
        nombre = nombre_valido(nombre)
        if self._ocupado():
            return {"error": t("borrar_ocupado")}
        if assetto.jugando():
            return {"error": t("borrar_en_uso", n=nombre)}
        a = ajustes.cargar()
        try:
            libres = espacio.borrar_pista(assetto.detectar(a.get("ac")), ajustes.salida(a), nombre)
        except RuntimeError as e:
            return {"error": str(e)}
        return {"ok": True, "mensaje": t("borrada", n=nombre, mb=round(libres / 1e6)), "pistas": self.pistas()}

    def borrar_vivo(self, nombre):
        """Borra una zona del modo en vivo (pedazos y pista de AC)."""
        nombre = nombre_valido(nombre)
        if (self._vivo.activo and self._vivo.nombre == nombre) or self._vivo_ac.activo:
            return {"error": t("borrar_ocupado")}
        if assetto.jugando():
            return {"error": t("borrar_en_uso", n=nombre)}
        a = ajustes.cargar()
        try:
            libres = espacio.borrar_vivo(assetto.detectar(a.get("ac")), ajustes.salida(a), nombre)
        except RuntimeError as e:
            return {"error": str(e)}
        return {"ok": True, "mensaje": t("borrada", n=nombre, mb=round(libres / 1e6)), "pistas": self.pistas()}

    def vaciar_cache(self):
        if self._ocupado() or self._vivo.activo or self._vivo_ac.activo:
            return {"error": t("borrar_ocupado")}
        self.cancelar("borrador")
        libres = espacio.vaciar_cache()
        return {"ok": True, "mensaje": t("liberado", mb=round(libres / 1e6)), "espacio": espacio.medir()}

    def limpiar_espacio(self):
        """Lo mismo que la app hace sola al abrirse: quitar lo obsoleto y aplicar el tope de la caché."""
        libres = espacio.mantenimiento()
        return {"ok": True, "mensaje": t("liberado", mb=round(libres / 1e6)), "espacio": espacio.medir()}

    def abrir_ruta_espacio(self, clave):
        for p in espacio.medir()["partes"]:
            if p["clave"] == clave and os.path.isdir(p["ruta"]):
                os.startfile(p["ruta"])  # noqa: S606
                return True
        return False

    def abrir_carpeta_vivo(self, nombre):
        d = ajustes.salida() / "vivo" / nombre_valido(nombre)
        os.startfile(d if d.is_dir() else ajustes.salida())  # noqa: S606
        return True

    def vivo_reanudar(self, nombre):
        """Vuelve a arrancar el modo en vivo de una zona ya preparada (los pedazos hechos se aprovechan)."""
        f = ajustes.salida() / "vivo" / nombre_valido(nombre) / "openlaps_app.json"
        try:
            op = json.loads(f.read_text(encoding="utf-8"))["op"]
        except (OSError, ValueError, KeyError):
            return {"error": t("vivo_sin_zona")}
        r = self.vivo_iniciar(op)
        if r.get("ok"):
            r["op"] = op
        return r

    def _pista(self, glb):
        glb = Path(glb)
        if not glb.exists():
            return None
        n = glb.stem
        info = {}
        try:
            info = json.loads(glb.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        ac_dir = glb.parent / "assetto_corsa" / n
        ac = (ac_dir / f"{n}.kn5").exists()
        st = glb.stat()
        a = ajustes.cargar()
        return {
            "nombre": n, "glb": f"/salida/{urllib.parse.quote(glb.name)}?t={int(st.st_mtime)}",
            "cenital": f"/salida/{urllib.parse.quote(n)}_cenital.jpg?t={int(st.st_mtime)}" if (glb.parent / f"{n}_cenital.jpg").exists() else None,
            "mb": round(espacio.tam_pista(glb.parent, n) / 1e6, 1), "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
            "area_km2": info.get("area_km2"), "segundos": info.get("segundos"), "modo": info.get("modo", "libre"),
            "centro": info.get("centro"), "km": (info.get("carreteras") or {}).get("km"),
            "ac": ac, "ac_estado": assetto.estado(assetto.detectar(a.get("ac")), ac_dir) if ac else "no",
            "trafico": ac and assetto.tiene_trafico(ac_dir),
        }

    def abrir_carpeta(self, nombre=None):
        sal = ajustes.salida()
        sal.mkdir(parents=True, exist_ok=True)
        f = sal / f"{nombre}.glb" if nombre else None
        if f and f.exists():
            subprocess.Popen(["explorer", "/select,", str(f)])
        else:
            os.startfile(sal)  # noqa: S606
        return True

    def instalar_ac(self, nombre):
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        if not ac:
            return {"error": t("ac_no")}
        nombre = nombre_valido(nombre)
        origen = ajustes.salida() / "assetto_corsa" / nombre
        if assetto.estado(ac, origen) == "otra":
            return {"error": t("ac_otra", n=nombre), "ac_otra": True, "nombre": nombre}
        try:
            return {"ok": True, "mensaje": assetto.instalar(ac, origen)}
        except RuntimeError as e:
            return {"error": str(e)}

    def _buscar_geo_pista(self, pista):
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        cand = []
        raw = str(pista or "").strip()
        for nm in [nombre_valido(raw) if raw else "", raw]:
            if nm and nm not in cand:
                cand.append(nm)
        for nm in cand:
            rutas = [ajustes.salida(a) / "assetto_corsa" / nm / "openlaps_geo.json"]
            if ac:
                rutas.append(Path(ac) / "content" / "tracks" / nm / "openlaps_geo.json")
            for f in rutas:
                if f.is_file():
                    try:
                        return json.loads(f.read_text(encoding="utf-8")), nm
                    except (OSError, ValueError):
                        pass
        return None, cand[0] if cand else "vivo"

    def _aplicar_vivo_overrides(self, query):
        claves = {"lado": "vivo_lado", "margen": "vivo_margen", "intervalo": "vivo_intervalo",
                  "trabajadores": "vivo_trabajadores", "radio": "vivo_radio", "radio_fondo": "vivo_radio_fondo",
                  "pad_osm": "vivo_pad_osm", "tesela_osm": "vivo_tesela_osm",
                  "r_carga": "vivo_r_carga", "r_soltar": "vivo_r_soltar"}
        cambios = {}
        for k, ak in claves.items():
            if k in query and query[k] != "":
                try:
                    cambios[ak] = float(query[k])
                except ValueError:
                    pass
        if cambios:
            base = dict(ajustes.cargar(), **cambios)
            vp = ajustes.vivo_params(base)
            ajustes.guardar({ak: vp[k] for k, ak in claves.items()})
            return vp
        return ajustes.vivo_params()

    def _notificar_vivo_config(self, vp):
        q = urllib.parse.urlencode({"intervalo": vp["intervalo"], "radio": vp["radio"],
                                    "radio_fondo": vp["radio_fondo"]})
        for inst in (self._vivo_ac, self._vivo):
            if inst.activo and inst.puerto:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{inst.puerto}/vivo/config?{q}", timeout=1.5).close()
                except Exception:  # noqa: BLE001
                    pass

    def _parametros_vivo_ac(self, query=None):
        vp = self._aplicar_vivo_overrides(query or {})
        if query:
            self._notificar_vivo_config(vp)
        return 200, {"ok": True, **vp}

    def _activar_vivo_ac(self, pista, lat=None, lon=None, query=None):
        with self._vivo_ac_lock:
            geo, nombre = self._buscar_geo_pista(pista)
            if not geo:
                return 404, {"ok": False, "error": "No se encontro openlaps_geo.json para esta pista"}
            try:
                if "lat0" in geo and "lon0" in geo:
                    lat0, lon0 = float(geo["lat0"]), float(geo["lon0"])
                else:
                    c = geo.get("centro") or {}
                    lat0, lon0 = float(c["lat"]), float(c["lon"])
            except (KeyError, TypeError, ValueError):
                return 400, {"ok": False, "error": "openlaps_geo.json sin centro valido"}
            if lat is not None and lon is not None:
                try:
                    if abs(lat0 - float(lat)) > 1e-4 or abs(lon0 - float(lon)) > 1e-4:
                        return 409, {"ok": False, "error": "Las coordenadas de la pista no coinciden con OpenLaps"}
                except (TypeError, ValueError):
                    return 400, {"ok": False, "error": "Coordenadas invalidas"}
            rect = geo.get("rect")
            if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
                return 400, {"ok": False, "error": "Regenera esta pista en OpenLaps para usar el modo en vivo"}
            try:
                rect = [float(v) for v in rect]
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": "Rectangulo de pista invalido"}
            vp = self._aplicar_vivo_overrides(query or {})
            a_cfg = ajustes.cargar()
            if self._vivo_ac.activo:
                info = self._vivo_ac.info
                ic = info.get("centro") or []
                if (info.get("nombre") == nombre and len(ic) == 2
                        and abs(ic[0] - lat0) < 1e-5 and abs(ic[1] - lon0) < 1e-5
                        and info.get("params") == vp):
                    if esperar_puerto(info["puerto"], timeout=10.0, proc=self._vivo_ac.proc):
                        return 200, {"ok": True, "url": f"http://127.0.0.1:{info['puerto']}",
                                     "L": vp["lado"], **vp}
            op = dict(geo.get("vivo_opc") or {})
            op["centro"] = [lat0, lon0]
            op["nombre"] = nombre
            r = self._vivo_ac.iniciar(op, ajustes.salida(), semilla=False, excluir=rect, params=a_cfg)
            if not esperar_puerto(r["puerto"], timeout=15.0, proc=self._vivo_ac.proc):
                self._vivo_ac.detener()
                return 500, {"ok": False, "error": "El servidor de modo en vivo no pudo arrancar"}
            return 200, {"ok": True, "url": f"http://127.0.0.1:{r['puerto']}", "L": vp["lado"], **vp}

    def jugar_ac(self, nombre):
        """Instala la pista (si hace falta) y abre Assetto Corsa en ella."""
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        if not ac:
            return {"error": t("ac_no")}
        nombre = nombre_valido(nombre)
        origen = ajustes.salida() / "assetto_corsa" / nombre
        if assetto.estado(ac, origen) == "otra":
            return {"error": t("ac_otra", n=nombre), "ac_otra": True, "nombre": nombre}
        try:
            if assetto.estado(ac, origen) != "enlazada":
                assetto.instalar(ac, origen)
            vp = ajustes.vivo_params(a)
            ctrl = f"http://127.0.0.1:{self._puerto_control_ac or vivo.PUERTO_CONTROL_AC}"
            vivo.actualizar_control_lua(str(origen), control_url=ctrl, pista=nombre, params=vp)
            # edificios de Google 3D (opcional): con la opción apagada se quita el script y la pista queda tal cual
            aviso = self._g3d.preparar(origen, ajustes.salida() / f"{nombre}.glb", bool(a.get("google3d")),
                                       str(a.get("token_cesium") or "").strip())
            # con tráfico: el modo Traffic de CSP, si la pista lo trae y la opción está puesta; si no, práctica
            con_trafico = bool(a.get("trafico", True)) and assetto.tiene_trafico(origen) and assetto.csp(ac)
            r = {"ok": True, "mensaje": assetto.jugar(ac, nombre, trafico=con_trafico), "trafico": con_trafico}
            if aviso:
                r["aviso"] = t(aviso)
            return r
        except RuntimeError as e:
            return {"error": str(e)}

    def renombrar_pista(self, viejo, nuevo):
        """Renombra una pista ya generada sin volver a generarla."""
        viejo = nombre_valido(viejo)
        nuevo_s = nombre_valido(nuevo)
        if not (str(nuevo or "").strip()) or viejo == nuevo_s:
            return {"ok": True, "nombre": viejo, "pista": self._pista(ajustes.salida() / f"{viejo}.glb")}
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        try:
            assetto.renombrar(ac, ajustes.salida(), viejo, nuevo_s)
        except RuntimeError as e:
            return {"error": str(e)}
        return {"ok": True, "nombre": nuevo_s, "mensaje": t("renombrada", n=nuevo_s),
                "pista": self._pista(ajustes.salida() / f"{nuevo_s}.glb")}

    # ------------------------------------------------------------------ modo en vivo
    def vivo_iniciar(self, op):
        """Zona de más de 5 km²: se preparan 5 km² (la zona a escala) y lo demás se genera mientras se conduce."""
        if self._trabajo and self._trabajo.activo and self._trabajo.tipo == "generar":
            return {"error": t("ocupado")}
        self.cancelar("borrador")
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        try:
            r = self._vivo.iniciar(op, ajustes.salida(), ac=ac if op.get("ac", True) else None,
                                   semilla=bool(op.get("ac", True)), params=a)
        except (ValueError, KeyError, TypeError) as e:
            return {"error": str(e) or t("faltan_datos")}
        except OSError as e:
            return {"error": t("no_inicia", e=e)}
        r.update(ok=True, csp=assetto.csp(ac), ac=bool(ac))
        try:        # para «Mis pistas»: con qué opciones se preparó esta zona
            d = ajustes.salida() / "vivo" / r["nombre"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "openlaps_app.json").write_text(json.dumps({"op": op, "zona": r.get("zona")}, ensure_ascii=False),
                                                 encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass
        if op.get("ac", True) and ac and not assetto.csp(ac):
            r["aviso"] = t("vivo_sin_csp")
        return r

    def vivo_estado(self):
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        return dict(self._vivo.estado(), ac=bool(ac), csp=assetto.csp(ac))

    def vivo_detener(self):
        self._g3d.detener()
        return self._vivo.detener()

    def google3d_estado(self):
        return self._g3d.estado()

    def detener_todo(self):
        self.cancelar()
        self._vivo.detener()
        self._vivo_ac.detener()
        self._g3d.detener()

    def vivo_jugar(self):
        """Abre AC en la pista semilla del modo en vivo (ya instalada por el servicio)."""
        a = ajustes.cargar()
        ac = assetto.detectar(a.get("ac"))
        if not ac:
            return {"error": t("ac_no")}
        if not self._vivo.nombre:
            return {"error": t("faltan_datos")}
        try:
            nombre = f"{self._vivo.nombre}_vivo"
            ctrl = f"http://127.0.0.1:{self._puerto_control_ac or vivo.PUERTO_CONTROL_AC}"
            vivo.actualizar_control_lua(str(Path(ac) / "content" / "tracks" / nombre), control_url=ctrl,
                                        pista=nombre, params=ajustes.vivo_params(a))
            # edificios de Google 3D (opcional): sobre la semilla ya instalada en AC, con los pedazos como calzada
            aviso = self._g3d.preparar(Path(ac) / "content" / "tracks" / nombre, None, bool(a.get("google3d")),
                                       str(a.get("token_cesium") or "").strip(),
                                       vivo=ajustes.salida() / "vivo" / self._vivo.nombre,
                                       vivo_url=f"http://127.0.0.1:{self._vivo.puerto}" if self._vivo.puerto else None)
            r = {"ok": True, "mensaje": assetto.jugar(ac, nombre, trafico=False)}    # en vivo aún no hay tráfico
            if aviso:
                r["aviso"] = t(aviso)
            return r
        except RuntimeError as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------ diálogos
    def elegir_carpeta(self, tipo):
        import webview
        if not self._ventana:
            return {"error": t("solo_ventana")}
        r = self._ventana.create_file_dialog(webview.FileDialog.FOLDER)
        if not r:
            return None
        carpeta = r[0] if isinstance(r, (list, tuple)) else r
        if tipo == "ac":
            if not assetto.es_ac(carpeta):
                return {"error": t("no_acs")}
            ajustes.guardar({"ac": carpeta})
        elif tipo == "salida":
            ajustes.guardar({"salida": carpeta})
        return {"ok": True, "carpeta": carpeta}

    def elegir_dem(self):
        import webview
        if not self._ventana:
            return {"error": t("solo_ventana")}
        r = self._ventana.create_file_dialog(webview.FileDialog.OPEN, file_types=(t("dem_tipo"),))
        return (r[0] if isinstance(r, (list, tuple)) else r) if r else None

    def abrir_enlace(self, url):
        if str(url).startswith(("https://", "http://")):
            webbrowser.open(url)
        return True


def _iniciar_control_ac(api, puerto=None):
    """Servidor local para que el script de Assetto Corsa active el modo en vivo al pulsar en CSP."""
    class ControlAC(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, codigo, obj):
            cuerpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                if u.path == "/activar":
                    codigo, obj = api._activar_vivo_ac(q.get("pista"), q.get("lat"), q.get("lon"), q)
                    return self._json(codigo, obj)
                if u.path == "/parametros":
                    codigo, obj = api._parametros_vivo_ac(q)
                    return self._json(codigo, obj)
                return self._json(404, {"ok": False, "error": "no existe"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001
                try:
                    self._json(500, {"ok": False, "error": str(e)})
                except (BrokenPipeError, ConnectionResetError):
                    pass

    preferido = vivo.PUERTO_CONTROL_AC if puerto is None else puerto
    for candidato in (preferido, _puerto_libre(vivo.PUERTO_CONTROL_AC)):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", candidato), ControlAC)
            break
        except OSError:
            continue
    else:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ControlAC)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_port


def main(argv=None):
    p = argparse.ArgumentParser(prog="openlaps-gui", description="Interfaz de escritorio de openlaps")
    p.add_argument("--navegador", action="store_true", help="abrir en el navegador en vez de en una ventana propia")
    p.add_argument("--puerto", type=int, default=0, help="puerto local (0 = uno libre)")
    p.add_argument("--no-abrir", action="store_true", help="con --navegador: solo mostrar la dirección")
    a = p.parse_args(argv)

    eventos = servidor.Eventos()
    api = Api(eventos)
    srv_ctrl, puerto_ctrl = _iniciar_control_ac(api)
    api._puerto_control_ac = puerto_ctrl
    token = secrets.token_urlsafe(24)
    srv, puerto = servidor.iniciar({
        "app": ajustes.WEB, "viewer": ajustes.VISOR,
        "salida": lambda: ajustes.salida(), "borrador": ajustes.BORRADOR,
    }, api, eventos, token, a.puerto)
    url = f"http://127.0.0.1:{puerto}/app/index.html?token={token}"
    ajustes.DATOS.mkdir(parents=True, exist_ok=True)
    # en segundo plano: quitar lo que ya no sirve (pedazos de otra versión, descargas a medias) y el tope de la caché
    threading.Thread(target=lambda: espacio.mantenimiento(log=print), daemon=True).start()

    if a.navegador:
        print(f"OpenLaps en {url}  (Ctrl+C para salir)", flush=True)
        if not a.no_abrir:
            webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        api.detener_todo()
        srv_ctrl.shutdown()
        srv_ctrl.server_close()
        srv.shutdown()
        return 0

    try:
        import webview
    except ImportError:
        print("Falta pywebview (python -m pip install pywebview). Mientras tanto: --navegador", file=sys.stderr)
        srv_ctrl.shutdown()
        srv_ctrl.server_close()
        srv.shutdown()
        return 1
    try:
        # icono y grupo propios en la barra de tareas (si no, sale agrupado con Python)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenLaps.OpenLaps")
    except (AttributeError, OSError):
        pass
    from .marco import Marco
    ventana = webview.create_window(
        "OpenLaps", url, width=1320, height=860, min_size=(980, 640), frameless=True, easy_drag=False,
        shadow=True, background_color="#15171a", text_select=False)
    api._ventana = ventana
    api._marco = Marco(ventana)
    ventana.events.closing += lambda: api.detener_todo()
    ventana.events.shown += lambda: api._marco.preparar()
    webview.start(gui="edgechromium", debug=bool(os.environ.get("OPENLAPS_DEBUG")),
                  private_mode=False, storage_path=str(ajustes.DATOS / "webview"),
                  icon=str(Path(__file__).with_name("openlaps.ico")))
    srv_ctrl.shutdown()
    srv_ctrl.server_close()
    srv.shutdown()
    return 0
