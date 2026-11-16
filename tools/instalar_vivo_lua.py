"""Reinstala el script del tramo en vivo (openlaps/vivo_ac.lua) en una pista semilla ya generada, sin
regenerarla: conserva la lista de pedazos horneados y la dirección del servidor.

    python tools/instalar_vivo_lua.py [carpeta de la pista ...]
"""
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(AQUI, "..", "openlaps", "vivo_ac.lua")
AC = r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa"
pistas = sys.argv[1:] or [os.path.join(AQUI, "..", "salida", "assetto_corsa", "openlaps_vivo"),
                          os.path.join(AC, "content", "tracks", "openlaps_vivo")]
for pista in pistas:
    f = os.path.join(pista, "extension", "vivo.lua")
    if not os.path.exists(f):
        continue
    viejo = open(f, encoding="utf-8").read()
    url = re.search(r"^local URL = '([^']*)'", viejo, re.M).group(1)
    horn = re.search(r"^local HORNEADOS = \{ (.*) \}", viejo, re.M).group(1)
    L = re.search(r"^local L = ([\d.]+)", viejo, re.M).group(1)
    geo = json.load(open(os.path.join(pista, "openlaps_geo.json"), encoding="utf-8"))
    nuevo = open(PLANTILLA, encoding="utf-8").read().replace("__URL__", url).replace("__HORNEADOS__", horn) \
        .replace("__L__", L).replace("__LAT__", repr(geo["lat0"])).replace("__LON__", repr(geo["lon0"]))
    open(f, "w", encoding="utf-8").write(nuevo)
    print("instalado:", f)
