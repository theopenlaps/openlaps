# OpenLaps

**English** · [Español](README.es.md)

Open generator of drivable 3D tracks from any real place. Pick an area (or a route) and a
deterministic algorithm (no generative AI) turns open data into a track you can drive. It
exports a `.glb` you can view in the browser or on a phone, and a complete **Assetto Corsa**
track folder (KN5, map, AI line, cameras, surfaces and Custom Shaders Patch config).

Each country has a **profile** (`openlaps/regions.py`) with its road-marking rules, building
style, vegetation and the best open orthophoto and elevation sources available there.

| Region | Markings | Roofs | Orthophoto | Elevation |
|---|---|---|---|---|
| Mexico (`mx`) | yellow centre line (NOM-034) | flat roof with water tank | Sentinel-2, 10 m | Copernicus 30 m or INEGI CEM (`--dem`) |
| Spain (`es`) | white, Norma 8.2-IC | hipped tile roofs | **PNOA, 25 cm** | **IGN MDT05, 5 m** |
| France (`fr`) | white | tile / slate | **BD ORTHO, 20 cm** | Copernicus 30 m |
| Europe (`eu`) and generic | white | pitched | Sentinel-2 | Copernicus 30 m |

The region is detected from the coordinates; `--region es` (or `mx`, `fr`, `eu`, `generic`) forces it.

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/theopenlaps/openlaps.git
cd openlaps
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # Linux/macOS: .venv/bin/python
```

The command-line options are in Spanish (`--centro` = centre, `--tam` = size in metres,
`--nombre` = name, `--ruta` = route):

```bash
# a town (free roam)
openlaps --centro 18.9853,-99.0995 --tam 800 --nombre tepoztlan --ac
openlaps --centro 40.1405,-3.4226 --tam 800 --nombre chinchon --ac      # Spain: PNOA + MDT05

# a mountain rally stage (Espinazo del Diablo, MEX-40)
openlaps --bbox 23.6285,-105.8465,23.6415,-105.8270 --modo tramo --ruta "23.6332889,-105.8425131;23.6351057,-105.8303128" --nombre espinazo --ac

# with INEGI elevation (better than Copernicus in Mexico)
openlaps --centro 19.43,-99.13 --tam 800 --dem CEM_V3_..._R15.tif
```

`python -m openlaps` works too. Run `openlaps --help` for every option.

Output goes to `salida/`: `<name>.glb`, `<name>_cenital.jpg` (baked aerial view),
`<name>.json` (stats) and, with `--ac`, the track folder `salida/assetto_corsa/<name>/`.
See [docs/assetto-corsa.md](docs/assetto-corsa.md) to install and test it in the game.

Downloads are cached in `~/.cache/openlaps` (override with `OPENLAPS_CACHE`).

**Viewer**: run `python -m http.server` in the project folder and open
`http://localhost:8000/viewer/?glb=../salida/tepoztlan.glb`, or open the viewer and use
"Abrir .glb". You can drive in it (keyboard or touch buttons), with a timer and chainage on stages.

Useful options: `--modo libre|tramo|circuito` (free / stage / circuit), `--ruta "lat,lon;lat,lon"`,
`--region`, `--temporada seca|lluvias` (dry / rainy season), `--vegetacion 0.5` (fewer trees),
`--res` (terrain mesh), `--mpp` (ground texture resolution), `--sin-orto`, `--sin-satelite`,
`--sin-dosel`, `--sin-horizonte`, `--sin-microsoft`, `--sin-cc0`.

## Data sources (all open)

| Data | Source | License |
|---|---|---|
| Roads, buildings, land use, speed bumps, crossings | OpenStreetMap (Overpass) | ODbL |
| Building footprints | Microsoft Global ML Building Footprints | ODbL |
| Elevation | Copernicus GLO-30 (automatic) or INEGI CEM 3.0 / LiDAR (local file) | Copernicus / INEGI free use |
| Land cover | ESA WorldCover 2021, 10 m | CC BY 4.0 |
| Satellite imagery | Sentinel-2 L2A (10 m) via Element84 / AWS | Copernicus open licence |
| Real trees | Meta/WRI Global Canopy Height 1 m | CC BY 4.0 |
| Photographic textures | ambientCG | CC0 |
| Orthophoto and elevation, Spain | PNOA 25 cm and MDT05 5 m (IGN) | CC BY 4.0 |
| Orthophoto, France | BD ORTHO 20 cm (IGN) | Licence Ouverte 2.0 |

Exact credit lines are in [ATTRIBUTION.md](ATTRIBUTION.md). Every generated track includes an
`ATRIBUCIONES.txt` with the sources it used: **keep it when you share a track.**

Google Maps / Google Earth are not used: their terms forbid downloading their data or deriving
models from it. Mexico has no open high-resolution orthophoto comparable to Spain's PNOA, so
there the base is Sentinel-2 (10 m) plus CC0 photographic detail tiled every 2 m by the
multilayer shader.

## How the algorithm works

1. **Download and projection**: the area is projected to re-centred UTM (metres; x east,
   y north, z real height above sea level).
2. **Elevation**: on surface models, a *morphological opening* (~70 m min then max) removes
   tree canopies and roofs, only where WorldCover says forest or built-up.
3. **Road graph**: OSM ways are split at shared nodes (junctions) and re-joined where only two
   meet with the same attributes. Width from lanes, `width` or road class.
4. **Plan**: each vertex is rounded with a minimum-radius arc per class (6 m in streets, 250 m on
   motorways) and resampled every 1.5 m.
5. **Profile**: terrain height along the road, median and Gaussian filters (sigma per class),
   smoothed across junctions, with a shared height at each junction. Bridges and tunnels run
   straight between their ends.
6. **Superelevation**: `e = v² / (127 R)` with the design speed, 8 % max, with smooth transitions.
7. **Junctions**: each road is trimmed where its edge stops overlapping its neighbour
   (`d = h_j / sin θ + h_i / tan θ`) and the gap is filled with the convex polygon of the ends.
8. **Terrain**: 2-4 m mesh. Urban blocks are flattened to the level of their streets, then the
   terrain is *carved* with a k-d tree of cross-section points (with real superelevation): flat
   under the road and a slope that grows with the height difference.
9. **Road markings** per country rules; centre line solid where sight distance (plan and
   profile, eye at 1.08 m) is below overtaking distance. Speed bumps and crossings from OSM.
10. **Sidewalks**: polygon geometry (road buffer minus roadway, inside the urban area) with kerbs.
11. **Buildings**: extrusion with roof shape and colours from OSM when available, regional
    styles otherwise (flat roofs with water tanks in Mexico, hipped roofs in Europe), industrial
    sheds and churches with bell towers.
12. **Vegetation**: individual crowns from local maxima of the canopy height, plus fill for
    closed canopy; species from land cover and altitude. Thinned near the road.
13. **Details**: soft road edges, tyre tracks, street lights at night, signs, guard rails (real
    ones from OSM `barrier=guard_rail`, heuristic on high embankments), roadside posts, trunks
    and poles with collision, invisible wall at the area edge and a 5 km satellite horizon.
14. **Stage or circuit mode**: route points snap to the nearest road and the route is computed
    with Dijkstra respecting one-way streets. Adds start and finish gantries, side-street
    barriers, grid, AI line and TV cameras.

Everything is deterministic: the same input always gives the same track.

## Assetto Corsa format notes

`tools/kn5_inspect.py` reads a whole KN5 (even over 1 GB) without loading it into memory:

- KN5 version 5. `tools/kn5_check.py` walks the file to the last byte.
- `AC_START_*` dummies use X, Y, Z rows plus translation, with **Z forward**.
- `fast_lane.ai`: version 7, 20-byte points, 18-float extras (speed, gas=1, radius, left and
  right width, direction=1, normal, segment length, forward) and a trailing 0 integer.
- The export uses DXT1/DXT5 DDS textures with mipmaps, normal maps, multilayer terrain
  (`ksMultilayer_fresnel_nm`: aerial base, mask and 4 detail layers), meshes split by spatial
  cells with per-type LOD distance, invisible collision materials, `ext_config.ini` (GrassFX,
  RainFX, night lights, `ksTree` replacement) and `cameras.ini`.

## Status

Validated: the full pipeline in a town (Tepoztlán), on a mountain road (Espinazo del Diablo)
and in Spain (Chinchón); tracks load and drive in Assetto Corsa with CSP; KN5 structure
(`tools/kn5_check.py`); DDS encoder quality (37-40 dB PSNR).

Known gaps: smooth width transitions when OSM attributes change, retaining walls, more street
furniture, facades with per-floor door/window atlases, and LOD for distant trees. See
[ROADMAP.md](ROADMAP.md) (in Spanish).

## Tools

- `tools/render.py`: CPU renderer (numba) to check a `.glb` without a GPU.
  On ARM use `NUMBA_CPU_NAME=generic`.
- `tools/comparar_ruta.py`: driver views every N metres along a stage, on one sheet
  (`--referencia` puts real-road frames side by side).
- `tools/probar_ac.ps1`: links the track into Assetto Corsa, starts a practice and checks the log.
- `tools/kn5_check.py`: checks a KN5 is consistent to the last byte.
- `tools/kn5_inspect.py`: inventory of textures, materials, dummies and meshes in a KN5.

## Contributing

Issues and pull requests are welcome, in English or Spanish. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Code: [MIT](LICENSE). Generated tracks contain data under the licenses listed in
[ATTRIBUTION.md](ATTRIBUTION.md).

OpenLaps is an independent project, not affiliated with or endorsed by Kunos Simulazioni or
Assetto Corsa, and it does not include any game content.
