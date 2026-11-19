"""Buscar Assetto Corsa en las bibliotecas de Steam e instalar en él las pistas generadas."""
import os
import re
import subprocess
from pathlib import Path

from .textos import t

_NO_VENTANA = 0x08000000


def _steam():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except (ImportError, OSError):      # ImportError: fuera de Windows no hay registro (las pruebas en Linux)
        return None


def _bibliotecas(steam):
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def es_ac(carpeta):
    return bool(carpeta) and (Path(carpeta) / "acs.exe").exists()


def detectar(preferida=None):
    """Carpeta de assettocorsa o None: la elegida a mano, OPENLAPS_AC o las bibliotecas de Steam."""
    cands = [preferida, os.environ.get("OPENLAPS_AC")]
    steam = _steam()
    if steam:
        cands += [lib / "steamapps" / "common" / "assettocorsa" for lib in _bibliotecas(steam)]
    cands.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common" / "assettocorsa")
    for c in cands:
        if es_ac(c):
            return str(Path(c))
    return None


def _sin_prefijo(ruta):
    """os.readlink devuelve los enlaces de Windows como \\\\?\\C:\\…; sin quitarlo, ninguna comparación de rutas acierta."""
    s = str(ruta)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    return s[4:] if s.startswith(("\\\\?\\", "\\??\\")) else s


def _misma(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _destino_de_enlace(p):
    try:
        return Path(_sin_prefijo(os.readlink(p)))
    except (OSError, ValueError):
        return None


def _quitar_enlace(dst):
    """Quita el enlace (no lo enlazado). False si Windows no deja (carpeta en uso)."""
    for _ in range(2):
        try:
            os.rmdir(dst)
        except OSError:
            subprocess.run(["cmd", "/c", "rmdir", str(dst)], capture_output=True, creationflags=_NO_VENTANA)
        if not os.path.lexists(dst):
            return True
    return False


def limpiar_rotos(ac):
    """Quita de content/tracks los enlaces a pistas de OpenLaps cuya carpeta ya no existe (se borró o se renombró
    a mano): Content Manager las enseña con error y no deja abrirlas. Devuelve los nombres quitados."""
    quitados = []
    try:
        entradas = list((Path(ac) / "content" / "tracks").iterdir()) if ac else []
    except OSError:
        return quitados
    for p in entradas:
        d = _destino_de_enlace(p)
        if d is not None and d.parent.name == "assetto_corsa" and not d.exists() and _quitar_enlace(p):
            quitados.append(p.name)
    return quitados


def estado(ac, origen):
    """'no' | 'enlazada' (se actualiza sola al regenerar) | 'otra' (hay otra pista con ese nombre)."""
    if not ac:
        return "sin_ac"
    dst = Path(ac) / "content" / "tracks" / Path(origen).name
    if not os.path.lexists(dst):
        return "no"
    destino = _destino_de_enlace(dst)
    if destino is None:
        return "otra"
    if _misma(destino, Path(origen).resolve()) or _misma(destino.resolve(), Path(origen).resolve()):
        return "enlazada"
    # un enlace nuestro que se quedó apuntando a una carpeta que ya no existe no estorba: se quita
    if destino.parent.name == "assetto_corsa" and not destino.exists() and _quitar_enlace(dst):
        return "no"
    return "otra"


def instalar(ac, origen):
    """Enlaza (junction) la carpeta de la pista en content/tracks: no ocupa espacio y al volver a
    generar con el mismo nombre la pista del juego se actualiza. No necesita permisos de administrador."""
    origen = Path(origen)
    if not (origen / f"{origen.name}.kn5").exists():
        raise RuntimeError(t("ac_sin_kn5"))
    e = estado(ac, origen)
    if e == "enlazada":
        return t("ac_ya")
    if e == "otra":
        raise RuntimeError(t("ac_otra", n=origen.name))
    dst = Path(ac) / "content" / "tracks" / origen.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(origen.resolve())],
                       capture_output=True, text=True, creationflags=_NO_VENTANA)
    if r.returncode != 0:
        raise RuntimeError(t("ac_enlace", d=dst.parent, e=(r.stderr or r.stdout).strip()))
    return t("ac_ok")


def renombrar(ac, salida, viejo, nuevo):
    """Renombra una pista ya generada (sin volver a generarla) y actualiza su enlace en AC si lo tenía."""
    import json
    salida = Path(salida)
    if not viejo or not nuevo or viejo == nuevo:
        return
    if (salida / f"{nuevo}.glb").exists() or (salida / "assetto_corsa" / nuevo).exists() \
            or (ac and os.path.lexists(Path(ac) / "content" / "tracks" / nuevo)):
        raise RuntimeError(t("nombre_existe", n=nuevo))
    ac_src = salida / "assetto_corsa" / viejo
    enlazada = bool(ac and ac_src.is_dir() and estado(ac, ac_src) == "enlazada")
    if enlazada and not _quitar_enlace(Path(ac) / "content" / "tracks" / viejo):
        raise RuntimeError(t("ac_en_uso", n=viejo))      # si no, quedaría en AC un enlace a una carpeta que ya no existe
    for suf in (".glb", ".json", "_cenital.jpg", "_cobertura.jpg"):
        p = salida / f"{viejo}{suf}"
        if p.exists():
            p.rename(salida / f"{nuevo}{suf}")
    if ac_src.is_dir():
        ac_dst = salida / "assetto_corsa" / nuevo
        ac_src.rename(ac_dst)
        kn5 = ac_dst / f"{viejo}.kn5"
        if kn5.exists():
            kn5.rename(ac_dst / f"{nuevo}.kn5")
        ini = ac_dst / "models.ini"
        if ini.exists():
            txt = ini.read_text(encoding="utf-8", errors="replace")
            ini.write_text(txt.replace(f"FILE={viejo}.kn5", f"FILE={nuevo}.kn5"), encoding="utf-8")
        ui_f = ac_dst / "ui" / "ui_track.json"
        if ui_f.exists():
            try:
                u = json.loads(ui_f.read_text(encoding="utf-8"))
                if u.get("name") == viejo:
                    u["name"] = nuevo
                    ui_f.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                pass
        if (ac_dst / "extension" / "vivo.lua").exists():
            from .. import vivo
            vivo.actualizar_control_lua(str(ac_dst), pista=nuevo)
        if enlazada:
            instalar(ac, ac_dst)


def csp(ac):
    """¿Tiene Custom Shaders Patch? (hace falta para el modo en vivo: scripts de pista con física)."""
    return bool(ac) and (Path(ac) / "extension" / "config").is_dir() and (Path(ac) / "dwrite.dll").exists()


def _documentos_ac():
    from .ajustes import _documentos
    return _documentos() / "Assetto Corsa"


def jugando():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq acs.exe", "/NH"], capture_output=True, text=True,
                       creationflags=_NO_VENTANA)
    return "acs.exe" in (r.stdout or "").lower()


def tiene_trafico(carpeta):
    """¿La pista se generó con tráfico (carriles para el modo Traffic de CSP)?"""
    return (Path(carpeta) / "data" / "traffic.json").is_file()


def _modo(txt, trafico):
    """Pone o quita en race.ini el modo Traffic de CSP (la misma clave que escribe Content Manager). Sin esto se
    heredaba el modo de la última sesión: una pista sin tráfico se abría en modo Traffic y al revés."""
    txt = re.sub(r"(?m)^__CM_CUSTOM_MODE=.*\r?\n?", "", txt)
    if trafico:
        txt = re.sub(r"(?m)^\[RACE\]\s*$", lambda m: m.group(0).rstrip() + "\n__CM_CUSTOM_MODE=traffic", txt, count=1)
    return re.sub(r"(?ms)(^\[SESSION_0\].*?^NAME=)[^\r\n]*", lambda m: m.group(1) + ("Traffic" if trafico else "Practice"),
                  txt, count=1)


def jugar(ac, pista, trafico=None):
    """Abre una práctica de AC en esa pista, sin launcher (como el botón Drive): escribe TRACK en race.ini
    (guarda una copia la primera vez) con el coche que ya tenía elegido, o el Abarth 500 si no hay ninguno, y
    arranca acs.exe. Steam tiene que estar abierto. `trafico`: True abre el modo Traffic de CSP, False una
    práctica normal, None deja el modo que hubiera."""
    if jugando():
        raise RuntimeError(t("ac_abierto"))
    ac = Path(ac)
    ri = _documentos_ac() / "cfg" / "race.ini"
    if not ri.exists():
        raise RuntimeError(t("ac_sin_race"))
    copia = ri.with_name("race.ini.antes_openlaps")
    if not copia.exists():
        copia.write_bytes(ri.read_bytes())
    txt = ri.read_text(encoding="latin-1")
    coche = (re.search(r"(?ms)^\[RACE\].*?^MODEL=(.*?)\s*$", txt) or [None, ""])[1].strip()
    if not coche or not (ac / "content" / "cars" / coche / "skins").is_dir():
        coche = "abarth500"
        skins = sorted(p.name for p in (ac / "content" / "cars" / coche / "skins").glob("*") if p.is_dir())
        txt = re.sub(r"(?ms)^(\[RACE\].*?^)MODEL=.*?$", lambda m: m.group(1) + "MODEL=" + coche, txt, count=1)
        if skins:
            txt = re.sub(r"(?m)^SKIN=.*$", "SKIN=" + skins[0], txt, count=1)
    txt = re.sub(r"(?m)^TRACK=.*$", "TRACK=" + pista, txt, count=1)
    txt = re.sub(r"(?m)^CONFIG_TRACK=.*$", "CONFIG_TRACK=", txt, count=1)
    if trafico is not None:
        txt = _modo(txt, trafico)
    ri.write_text(txt, encoding="latin-1")
    appid = ac / "steam_appid.txt"
    if not appid.exists():
        try:
            appid.write_text("244210", encoding="ascii")
        except OSError:
            pass
    subprocess.Popen([str(ac / "acs.exe")], cwd=str(ac), creationflags=_NO_VENTANA)
    return t("ac_jugando_trafico" if trafico else "ac_jugando", p=pista)
