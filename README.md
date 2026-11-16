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
| Spain (`es`) | white, Norma 8.2-IC | hipped tile roofs; **real floor counts from the Catastro** | **PNOA, 25 cm** | **IGN MDT05, 5 m** |
| France (`fr`) | white | tile / slate | **BD ORTHO, 20 cm** | Copernicus 30 m |
| United States (`us`) | double yellow centre line (MUTCD), mph signs | asphalt shingles; flat roofs from 4 floors; glass towers; **Overture heights** | **NAIP, 60 cm** | **USGS 3DEP, 1 m** |
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
`<name>_cobertura.jpg` (what every spot is: water, sea, forest, rock, sand..., and where OSM and the
satellite disagree about water), `<name>.json` (stats) and, with `--ac`, the track folder `salida/assetto_corsa/<name>/`.
See [docs/assetto-corsa.md](docs/assetto-corsa.md) to install and test it in the game.

Downloads are cached in `~/.cache/openlaps` (override with `OPENLAPS_CACHE`): OSM and Catastro data,
orthophoto, elevation, WorldCover, canopy and Sentinel-2 crops (Sentinel-2 refreshes monthly) and
the generated asphalt textures. Downloads run in parallel. Central Madrid (800 m) takes ~80 s the
first time and ~35 s afterwards.

**Viewer**: run `python -m http.server` in the project folder and open
`http://localhost:8000/viewer/?glb=../salida/tepoztlan.glb`, or open the viewer and use
"Abrir .glb". You can drive in it (keyboard or touch buttons), with a timer and chainage on stages.
With `&cam=x,y,z&mira=x,y,z` (`.glb` coordinates) it opens in free view looking at that point, handy to
inspect a specific spot.

Useful options: `--modo libre|tramo|circuito` (free / stage / circuit), `--ruta "lat,lon;lat,lon"`,
`--region`, `--temporada seca|lluvias` (dry / rainy season), `--vegetacion 0.5` (fewer trees),
`--res` (terrain mesh), `--mpp` (ground texture resolution), `--sin-orto`, `--sin-satelite`,
`--sin-dosel`, `--sin-horizonte`, `--sin-microsoft`, `--sin-catastro`, `--sin-cc0`.

## Live stage (experimental)

A separate option (the normal generation is unchanged): the track is generated in 256 m pieces
around the car while you drive, with no area limit.

```bash
python -m openlaps.vivo --centro 40.41680,-3.70380
```

Open `http://127.0.0.1:8790/?vivo=1` to drive in the browser; `&auto=3000` adds an autopilot that
follows 3 km of streets (handy for hands-free testing) and `&vmax=100` sets its top speed in km/h (it
brakes on its own before each bend). With `--semilla-ac` it also builds and installs
the `openlaps_vivo` Assetto Corsa track: the pieces around the start baked into the `.kn5`, a library
with every material and the `vivo.lua` script, which creates the other pieces in-game (meshes with
`createMesh`, physics with `physics.addTrackSurface`). If the car reaches a piece that isn't there yet,
the viewer holds it and AC brakes it until it arrives. To test in AC hands-free, `--piloto-ac 4000
--piloto-kmh 100` (or `http://127.0.0.1:8790/vivo/piloto?metros=4000&kmh=100` with the game running; `?apagar=1`
removes it): the track script itself drives that route. Nothing but OpenLaps and Assetto Corsa with CSP is
needed; the server console shows where the AC car is.

- All pieces share one origin (the starting point), so each lands in place. They are generated with a
  64 m margin and cropped to their core; small things (trees, posts, signs) go whole to the piece their
  center falls in, and the terrain is sampled on a common grid, so the edges match.
- OpenStreetMap is fetched in 1 km tiles and the Spanish Cadastre in 512 m tiles, ahead of the car
  (from its position and speed). Overpass first (the mail.ru mirror and the main one, with short
  timeouts); if neither answers, the official OSM API as a last resort. Everything is cached.
- Several processes generate at once, always the most urgent piece. The server predicts where the car is
  going by following the OSM streets (the straightest continuation, without dropping to a smaller road)
  about 30 s ahead and generates those pieces first; the data (OSM, Cadastre and the rasters: terrain,
  orthophoto, canopy…) is requested even further ahead, in separate processes, so each piece finds it
  cached. A piece takes about 8-10 s (4 processes on a 12-thread machine): along Madrid's Castellana you
  can drive at ~100 km/h almost without waiting. The first time in an area, the OSM download dominates.
- Pre-warming: spare processes (one is always kept for urgent work) generate rings around each car up to
  `--radio-fondo` (1200 m by default). AC shows "preparando la zona: N %" while you are stopped and the viewer
  "area ready N %": the first time somewhere, wait until it is close to 100 % to drive at 100 km/h in any
  direction (it stays cached afterwards). If you reach a piece that isn't there yet, AC brakes hard enough to
  stop before it (even with the throttle held) and, if you still get in, puts you back, stopped, at the last
  place with ground instead of letting you fall. The server serves several cars at once (the viewer and AC,
  for example), each with its own predicted path.
- In AC, CSP only lets a track script create physics if `surfaces.ini` has
  `[_SCRIPTING_PHYSICS] ALLOW_TRACK_SCRIPTS=1` **and** is "extended" (`WAV_PITCH=extended-0` on the first
  surface); the seed track already does both. Meshes copy their material from the library with
  `assignMaterialFrom` (with `createMesh` and the material name they render black). The script reports its
  state to the server, which prints it (`AC: pedazos …`). `python tools/instalar_vivo_lua.py` reinstalls the
  script without rebuilding the seed (don't reload it while driving outside the baked area: reloading drops
  the physics for a moment).
- Pieces are kept in `salida/vivo/<center>/` (`.glb` for the viewer, `.olv` and `.jpg` for AC) and
  reused when you come back. `--nuevo` deletes them; `--radio`, `--trabajadores` and `--puerto` tune
  the service, and `openlaps` options (`--sin-catastro`, `--vegetacion`…) pass straight through.

## Desktop app (Windows)

No command line needed: a map where you pick the area (or mark a stage or circuit), an instant
street preview and a quick 3D draft before generating the full track, which installs into Assetto
Corsa with one click. In English and Spanish.

- **Lightweight build** (recommended): download `OpenLaps-<version>-windows.zip` (~330 KB), extract
  it and open `OpenLaps.exe`. On first run it downloads Python and the libraries (~250 MB, into
  `%LOCALAPPDATA%\OpenLaps`); after that it opens instantly. `OpenLaps.exe --repair` reinstalls that
  environment. Requires WebView2, which ships with Windows 11.
- **From source**: `python -m pip install -e .[gui]` and `python -m openlaps.gui`
  (`--navegador` opens it in the browser instead of its own window).
- **Build the lightweight package**: `powershell -ExecutionPolicy Bypass -File packaging\compilar_ligero.ps1`
  (uses the `csc.exe` that ships with Windows; output goes to `dist\`).

- **Your own area**: a rectangle with free width and height (drag its corner) up to 24 km². From 2 km² you
  can turn on **live mode**: that whole area is prepared and the rest is built around the car as you drive,
  with no limit; the app shows how much is ready and lets you drive in the window itself or open Assetto Corsa.
  While it is being generated the area can't be changed (cancel first).
- **Wizard**: once you pick the place it moves on to the track type (area, stage or circuit), and then to
  adjusting it.
- **Assetto Corsa in one click**: «Play in AC» installs the track and opens the game on it (with the car you
  had selected; Steam must be running). «Open in Assetto Corsa when done» does it automatically. Live mode
  inside AC needs Custom Shaders Patch (free, with Content Manager); normal tracks don't.
- **Everything installs itself**: `OpenLaps.exe` downloads Python and the libraries the first time and, if
  WebView2 is missing (Windows 10), offers to install it from Microsoft.
- **Starts in a good place**: on a main, straight street near the centre with buildings around; never on a
  bridge or in a tunnel.

While you look at the map, the app is already downloading the area data (OpenStreetMap, Cadastre,
Microsoft), which is the slow part the first time, so the draft comes out in about 25 s.

## Data sources (all open)

| Data | Source | License |
|---|---|---|
| Roads, buildings, land use, speed bumps, crossings | OpenStreetMap (Overpass) | ODbL |
| Building footprints | Microsoft Global ML Building Footprints | ODbL |
| Elevation | Copernicus GLO-30 (automatic) or INEGI CEM 3.0 / LiDAR (local file) | Copernicus / INEGI free use |
| Land cover | ESA WorldCover 2021, 10 m | CC BY 4.0 |
| Satellite imagery and water index (NDWI) | Sentinel-2 L2A (10 m) via Element84 / AWS | Copernicus open licence |
| Real trees | Meta/WRI Global Canopy Height 1 m | CC BY 4.0 |
| Photographic textures | ambientCG | CC0 |
| Orthophoto and elevation, Spain | PNOA 25 cm and MDT05 5 m (IGN) | CC BY 4.0 |
| Buildings and floor counts, Spain | Catastro (DG del Catastro, INSPIRE service) | Catastro licence / CC BY 4.0 |
| Orthophoto, France | BD ORTHO 20 cm (IGN) | Licence Ouverte 2.0 |
| Orthophoto and elevation, USA | NAIP 60 cm (USDA) and 3DEP 1 m (USGS) | Public domain |
| Building heights, USA | Overture Maps (includes USGS lidar); needs `pyarrow` | ODbL 1.0 |

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
    sheds and churches with bell towers. In Spain footprints and heights come from the Catastro:
    every building part with its floors above ground (one piece per parcel and height), inheriting
    colours and type from OSM; churches keep their OSM footprint.
12. **Water**: OSM lakes, reservoirs and rivers (also those drawn as lines, with their width), the
    sea from the coastline and water the satellite sees (Sentinel-2 NDWI) even if OSM lacks it.
    Still water sits at its shoreline level; rivers follow the valley floor. The bed is carved and
    the bank lowered to the water; where a road without a bridge crosses, there is no water.
    Intermittent streams are dry beds in the dry season.
13. **Vegetation**: individual crowns from local maxima of the canopy height, plus fill for
    closed canopy; species from land cover and altitude. Thinned near the road.
14. **Details**: soft road edges, tyre tracks, street lights at night, signs, guard rails (real
    ones from OSM `barrier=guard_rail`, heuristic on high embankments), roadside posts, trunks
    and poles with collision, invisible wall at the area edge and a 5 km satellite horizon.
15. **Stage or circuit mode**: route points snap to the nearest road and the route is computed
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
- `openlaps_geo.json`: the track's exact georeference. An AC position `(x, y, z)` is UTM
  `(x0 + x, y0 − z)` in the given EPSG, with `(x0, y0)` = UTM of `(lat0, lon0)`, and `y` is the
  height above sea level. Useful for drawing the car on a real map.

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
- `tools/defectos.py`: finds geometric defects in a `.glb` (tilted road or sidewalk slabs, steps in
  the road surface) and renders close-ups of the worst spots.
- `tools/cobertura.py`: what every spot of an area is (water, sea, forest, rock, sand...) without
  generating the track, with OSM vs satellite water mismatches; handy to scout nature areas.
- `tools/referencias.py`: downloads real street-level photos of an area with position and heading
  (Panoramax, Mapillary with `MAPILLARY_TOKEN`, Wikimedia Commons), preferring 360° panoramas and
  empty streets; night shots are dropped automatically and `hoja.jpg` helps flag the rest by hand.
- `tools/comparar_fotos.py`: renders the `.glb` from each photo's pose (refining heading, pitch,
  zoom and a few metres of GPS error) and writes a real / model / blend / edges panel per photo, with
  sky IoU and skyline difference, plus an `index.html` with a swipe slider.
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
