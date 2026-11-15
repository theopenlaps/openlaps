# Data sources and attribution

The OpenLaps **code** is MIT-licensed (see [LICENSE](LICENSE)). The repository ships no
map data: everything is downloaded when you generate a track. The **tracks you generate**,
however, are built from the open datasets below, and their licenses travel with them.

Every generated Assetto Corsa track gets an `ATRIBUCIONES.txt` file listing the sources it
used. **If you share a track, keep that file with it.**

| Data | Source | License | Required credit |
|---|---|---|---|
| Roads, buildings, land use | [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass API | ODbL 1.0 | © OpenStreetMap contributors |
| Building footprints | [Microsoft Global ML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) | ODbL 1.0 | Microsoft |
| Elevation (global) | [Copernicus DEM GLO-30](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model) | Copernicus DEM licence | © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA |
| Elevation (Mexico, optional local file) | INEGI, Continuo de Elevaciones Mexicano 3.0 | INEGI Términos de Libre Uso | Fuente: INEGI |
| Land cover | [ESA WorldCover 2021](https://esa-worldcover.org/) | CC BY 4.0 | © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium |
| Satellite imagery and water index | Copernicus Sentinel-2 L2A via [Element84 Earth Search](https://element84.com/earth-search/) (AWS) | Copernicus open licence | Contains modified Copernicus Sentinel data |
| Tree canopy height | [Meta & WRI Global Canopy Height 1 m](https://registry.opendata.aws/dataforgood-fb-forests/) (Tolan et al. 2024) | CC BY 4.0 | Meta and World Resources Institute |
| Orthophoto and elevation (Spain) | [PNOA](https://pnoa.ign.es/) and MDT05, Instituto Geográfico Nacional | CC BY 4.0 | PNOA / MDT05 cedido por © Instituto Geográfico Nacional de España (scne.es) |
| Buildings and floor counts (Spain, except Basque Country and Navarre) | [Catastro INSPIRE buildings](https://www.catastro.hacienda.gob.es/webinspire/index.html), Dirección General del Catastro | [Catastro licence](https://www.catastro.hacienda.gob.es/webinspire/documentos/Licencia.pdf) (INSPIRE files CC BY 4.0) | © Dirección General del Catastro |
| Orthophoto (France) | BD ORTHO, IGN, via Géoplateforme | Licence Ouverte / Etalab 2.0 | © IGN |
| Orthophoto (USA) | [NAIP](https://naip-usdaonline.hub.arcgis.com/), USDA Farm Service Agency, via USGS The National Map | Public domain | USDA FSA NAIP (appreciated) |
| Elevation (USA) | [USGS 3D Elevation Program (3DEP)](https://www.usgs.gov/3d-elevation-program) | Public domain | U.S. Geological Survey, 3DEP (appreciated) |
| Building heights (USA) | [Overture Maps buildings](https://docs.overturemaps.org/guides/buildings/) | ODbL 1.0 | © Overture Maps Foundation, © OpenStreetMap contributors, Microsoft, Esri |
| Photographic textures | [ambientCG](https://ambientcg.com/) | CC0 | Not required (appreciated) |

Everything else (building facades, signs, vegetation sprites, road markings) is generated
procedurally by OpenLaps.

## Not used, on purpose

Google Maps / Google Earth data is **not** used: its terms forbid downloading it or deriving
3D models from it.

## Assetto Corsa

OpenLaps is an independent project, not affiliated with or endorsed by Kunos Simulazioni or
Assetto Corsa. It does not include or redistribute any game content; it only writes track
folders in the game's open modding format.
