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
    except OSError:
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


def _destino_de_enlace(p):
    try:
        return Path(os.readlink(p)).resolve()
    except OSError:
        return None


def estado(ac, origen):
    """'no' | 'enlazada' (se actualiza sola al regenerar) | 'otra' (hay otra pista con ese nombre)."""
    if not ac:
        return "sin_ac"
    dst = Path(ac) / "content" / "tracks" / Path(origen).name
    if not os.path.lexists(dst):
        return "no"
    destino = _destino_de_enlace(dst)
    return "enlazada" if destino and destino == Path(origen).resolve() else "otra"


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


def jugar(ac, pista):
    """Abre una práctica de AC en esa pista, sin launcher (como el botón Drive): escribe TRACK en race.ini
    (guarda una copia la primera vez) con el coche que ya tenía elegido, o el Abarth 500 si no hay ninguno, y
    arranca acs.exe. Steam tiene que estar abierto."""
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
    ri.write_text(txt, encoding="latin-1")
    appid = ac / "steam_appid.txt"
    if not appid.exists():
        try:
            appid.write_text("244210", encoding="ascii")
        except OSError:
            pass
    subprocess.Popen([str(ac / "acs.exe")], cwd=str(ac), creationflags=_NO_VENTANA)
    return t("ac_jugando", p=pista)
