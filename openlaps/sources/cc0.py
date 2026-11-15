"""Texturas fotográficas CC0 (dominio público) de ambientCG.

Se descarga la versión 1K en JPG, se guarda en caché y se usan el color y el mapa normal en
convención DirectX (la que usa Assetto Corsa). Si no hay internet, el programa sigue con las
texturas procedurales.
"""
import io
import zipfile

from PIL import Image

from .cache import fetch

# ranura de material -> id de ambientCG
ASSETS = {
    "asfalto": "Asphalt031",
    "empedrado": "PavingStones119",
    "adoquin": "PavingStones046",
    "concreto": "Concrete048",
    "banqueta": "Concrete034",
    "aplanado": "Plaster001",
    "terraceria": "Ground103",
    "hojarasca": "Ground078",
    "pasto": "Ground037",
    "roca": "Rock051",
}


def load(asset_id, size=1024, log=print):
    """Devuelve (color, normal_dx) como imágenes PIL, o None si falla."""
    url = f"https://ambientcg.com/get?file={asset_id}_1K-JPG.zip"
    try:
        path = fetch(url, ext=".zip", log=log, retries=2)
        z = zipfile.ZipFile(path)
        names = z.namelist()
        col = next(n for n in names if n.endswith("_Color.jpg"))
        nrm = next((n for n in names if n.endswith("_NormalDX.jpg")), None)
        c = Image.open(io.BytesIO(z.read(col))).convert("RGB")
        n = Image.open(io.BytesIO(z.read(nrm))).convert("RGB") if nrm else None
        if size and c.size[0] != size:
            c = c.resize((size, size), Image.LANCZOS)
            n = n.resize((size, size), Image.LANCZOS) if n else None
        return c, n
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: textura CC0 {asset_id} no disponible ({e.__class__.__name__})")
        return None


def load_all(size=1024, log=print):
    out = {}
    for slot, aid in ASSETS.items():
        r = load(aid, size, log)
        if r:
            out[slot] = r
    log(f"  texturas CC0 de ambientCG: {len(out)}/{len(ASSETS)}")
    return out


def load_cutout(asset_id, log=print):
    """Recorte con transparencia (hojas, ramas, agujas): devuelve imagen RGBA o None.

    Usa el PNG de 1K (trae _Opacity.png); el color se multiplica por la opacidad en el canal alfa.
    """
    url = f"https://ambientcg.com/get?file={asset_id}_1K-PNG.zip"
    try:
        path = fetch(url, ext=".zip", log=log, retries=2)
        z = zipfile.ZipFile(path)
        names = z.namelist()
        col = next(n for n in names if n.endswith("_Color.png"))
        opa = next((n for n in names if n.endswith("_Opacity.png")), None)
        c = Image.open(io.BytesIO(z.read(col))).convert("RGB")
        if opa:
            a = Image.open(io.BytesIO(z.read(opa))).convert("L").resize(c.size)
        else:
            a = Image.new("L", c.size, 255)
        c.putalpha(a)
        return c
    except Exception as e:  # noqa: BLE001
        log(f"  aviso: recorte CC0 {asset_id} no disponible ({e.__class__.__name__})")
        return None
