"""openlaps: genera una pista 3D conducible a partir de cualquier lugar real, con datos abiertos.

Ejemplos:
  python -m openlaps --centro 18.9853,-99.0995 --tam 1000 --nombre tepoztlan --ac
  python -m openlaps --bbox 23.62,-105.55,23.66,-105.50 --modo tramo \
      --ruta "23.655,-105.545;23.628,-105.505" --nombre espinazo --ac
  python -m openlaps --centro 19.43,-99.13 --tam 800 --dem cem_cdmx.tif   # MDE del INEGI
"""
import argparse
import sys

from .pipeline import run


def parser():
    p = argparse.ArgumentParser(prog="openlaps", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--centro", help="lat,lon del centro del área")
    g.add_argument("--bbox", help="sur,oeste,norte,este")
    p.add_argument("--tam", type=float, default=1000, help="lado del área en metros (con --centro)")
    p.add_argument("--modo", choices=["libre", "tramo", "circuito"], default="libre")
    p.add_argument("--ruta", help='puntos "lat,lon;lat,lon;..." para tramo o circuito')
    p.add_argument("--dem", help="GeoTIFF local de elevación (p. ej. INEGI CEM 3.0 o LiDAR)")
    p.add_argument("--dem-es-superficie", action="store_true", help="el MDE local incluye árboles/techos")
    p.add_argument("--temporada", choices=["seca", "lluvias"], default="seca")
    p.add_argument("--res", type=float, help="resolución de la malla de terreno en m (auto: 2-4)")
    p.add_argument("--tesela", type=float, default=128.0, help="tamaño de tesela de terreno (m)")
    p.add_argument("--mpp", type=float, help="metros por pixel de la textura del suelo (auto: 0.25 con ortofoto, 0.5 sin)")
    p.add_argument("--region", default="auto", help="perfil regional: auto, mx, es, fr, eu, generic")
    p.add_argument("--sin-orto", action="store_true", help="no usar la ortofoto nacional aunque exista")
    p.add_argument("--vegetacion", type=float, default=1.0, help="multiplicador de densidad de vegetación")
    p.add_argument("--sin-microsoft", action="store_true", help="no usar huellas de edificios de Microsoft")
    p.add_argument("--sin-satelite", action="store_true", help="no usar imagen Sentinel-2 en el suelo")
    p.add_argument("--sin-dosel", action="store_true", help="no usar altura de dosel (árboles reales)")
    p.add_argument("--sin-horizonte", action="store_true", help="no generar el relieve lejano")
    p.add_argument("--tuneles", choices=["principales", "todos", "ninguno"], default="principales",
                   help="qué túneles se construyen: principales = vías primarias para arriba (por defecto; los urbanos "
                        "menores bajan en trinchera y su boca salía en medio de la calle), todos, ninguno")
    p.add_argument("--sin-catastro", action="store_true", help="España: no usar edificios ni plantas del Catastro")
    p.add_argument("--sin-overture", action="store_true", help="EE. UU.: no usar alturas de Overture Maps")
    p.add_argument("--sin-cc0", action="store_true", help="no descargar texturas fotográficas CC0 (ambientCG)")
    p.add_argument("--ac", action="store_true", help="exportar también la carpeta de Assetto Corsa (KN5)")
    p.add_argument("--nombre", default="pista", help="identificador (sin espacios)")
    p.add_argument("--titulo", help="nombre visible en el juego")
    p.add_argument("--salida", default="salida", help="carpeta de salida")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if a.modo != "libre" and not a.ruta:
        p.error("--modo tramo/circuito necesita --ruta")
    run(a)


if __name__ == "__main__":
    sys.exit(main())
