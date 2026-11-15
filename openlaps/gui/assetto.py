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
