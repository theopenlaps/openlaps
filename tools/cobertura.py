"""Qué es cada sitio de una zona (agua, mar, bosque, roca, arena...), sin generar la pista.

Descarga solo lo necesario (OSM, WorldCover, imagen aérea y el índice de agua de Sentinel-2) y
dibuja el panel de diagnóstico: imagen aérea | mapa de cobertura | agua según OSM frente al
satélite (azul = coinciden; rojo = OSM dice agua y el satélite no la ve; amarillo = agua que
solo ve el satélite). Útil para elegir una zona de naturaleza y ver qué le falta a OSM antes de
generar.

  python tools/cobertura.py --centro 19.1950,-100.1330 --tam 2000 --nombre valle_de_bravo
  python tools/cobertura.py --centro 40.0790,-2.1290 --tam 1500 --nombre hoz_jucar --temporada lluvias
Sale en salida/<nombre>_cobertura.jpg y salida/<nombre>_cobertura.json.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from openlaps import regions  # noqa: E402
from openlaps.cobertura import Cobertura  # noqa: E402
from openlaps.geo import BBox, LocalProjection  # noqa: E402
from openlaps.sources import osm as OSM, rasters  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--centro", help="lat,lon")
    g.add_argument("--bbox", help="sur,oeste,norte,este")
    ap.add_argument("--tam", type=float, default=1500)
    ap.add_argument("--nombre", required=True)
    ap.add_argument("--temporada", choices=["seca", "lluvias"], default="seca")
    ap.add_argument("--salida", default="salida")
    a = ap.parse_args()
    if a.bbox:
        bbox = BBox.parse(a.bbox)
    else:
        lat, lon = (float(v) for v in a.centro.split(","))
        bbox = BBox.around(lat, lon, a.tam)
    proj = LocalProjection(bbox)
    R = regions.detect(proj.lat0, proj.lon0)
    rect = (proj.xmin, proj.ymin, proj.xmax, proj.ymax)
    osm = OSM.parse(OSM.download(bbox.padded(200)))
    lc = rasters.worldcover(bbox.padded(100))
    ndwi = rasters.sentinel2_ndwi(bbox.padded(60), a.temporada)
    img = None
    if R.ortho:
        lado = max(proj.xmax - proj.xmin, proj.ymax - proj.ymin)
        try:
            img = rasters.ortho(bbox, R.ortho, mpp=max(0.5, lado / 2000))
        except Exception as e:  # noqa: BLE001
            print(f"  aviso: sin ortofoto ({e})")
    if img is None:
        img = rasters.sentinel2(bbox.padded(60), a.temporada)
    cob = Cobertura(osm, proj, rect, lc, ndwi, a.temporada)
    os.makedirs(a.salida, exist_ok=True)
    out = os.path.join(a.salida, f"{a.nombre}_cobertura.jpg")
    cob.panel(img, f"{a.nombre} ({proj.lat0:.4f}, {proj.lon0:.4f}, temporada {a.temporada})").save(out, quality=88)
    with open(os.path.join(a.salida, f"{a.nombre}_cobertura.json"), "w", encoding="utf-8") as f:
        json.dump(cob.stats, f, ensure_ascii=False, indent=1)
    print(json.dumps(cob.stats, ensure_ascii=False))
    print("ok", out)


if __name__ == "__main__":
    main()
