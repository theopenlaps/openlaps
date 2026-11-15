# OpenLaps

[English](README.md) · **Español**

Generador **abierto** de pistas 3D conducibles a partir de cualquier lugar real: eliges una
zona (o una ruta) y un algoritmo determinista (sin IA generativa) convierte datos abiertos en
una pista que se puede manejar. Exporta un `.glb` para verlo en el navegador o en el celular y
una carpeta de pista completa para **Assetto Corsa** (KN5, mapa, línea de IA, cámaras,
superficies y configuración de Custom Shaders Patch).

Cada país tiene un **perfil** (`openlaps/regions.py`) con sus normas de señalamiento, su estilo
de construcción, su vegetación y las mejores fuentes abiertas de ortofoto y relieve que existen allí.

| Región | Rayas | Techos | Ortofoto | Relieve |
|---|---|---|---|---|
| México (`mx`) | centro amarilla (NOM-034) | azotea plana con tinaco | Sentinel-2, 10 m | Copernicus 30 m o INEGI CEM (`--dem`) |
| España (`es`) | blancas, Norma 8.2-IC | teja a cuatro aguas; **plantas reales del Catastro** | **PNOA, 25 cm** | **MDT05 del IGN, 5 m** |
| Francia (`fr`) | blancas | teja / pizarra | **BD ORTHO, 20 cm** | Copernicus 30 m |
| Estados Unidos (`us`) | doble amarilla central (MUTCD), señales en mph | tela asfáltica; azotea plana desde 4 plantas; torres de vidrio; **alturas de Overture** | **NAIP, 60 cm** | **3DEP del USGS, 1 m** |
| Europa (`eu`) y genérico | blancas | inclinados | Sentinel-2 | Copernicus 30 m |

La región se detecta sola por coordenadas; `--region es` (o `mx`, `fr`, `eu`, `generic`) la fuerza.

## Uso rápido

Requiere Python 3.11 o superior.

```bash
git clone https://github.com/theopenlaps/openlaps.git
cd openlaps
python -m venv .venv
.venv/Scripts/python -m pip install -e .        # Linux/macOS: .venv/bin/python
```

```bash
# un pueblo (modo libre)
openlaps --centro 18.9853,-99.0995 --tam 800 --nombre tepoztlan --ac
openlaps --centro 40.1405,-3.4226 --tam 800 --nombre chinchon --ac      # España: PNOA + MDT05

# un tramo de rally por carretera de montaña (Espinazo del Diablo, MEX-40)
openlaps --bbox 23.6285,-105.8465,23.6415,-105.8270 --modo tramo --ruta "23.6332889,-105.8425131;23.6351057,-105.8303128" --nombre espinazo --ac

# con el relieve del INEGI (mejor que Copernicus en México)
openlaps --centro 19.43,-99.13 --tam 800 --dem CEM_V3_..._R15.tif
```

También funciona `python -m openlaps`. `openlaps --help` muestra todas las opciones.

Sale en `salida/`: `<nombre>.glb`, `<nombre>_cenital.jpg` (vista aérea horneada),
`<nombre>_cobertura.jpg` (qué es cada sitio: agua, mar, bosque, roca, arena..., y dónde OSM y el
satélite no coinciden sobre el agua), `<nombre>.json` (estadísticas) y, con `--ac`, la carpeta de pista `salida/assetto_corsa/<nombre>/`.
Para instalarla y probarla en el juego ver [docs/assetto-corsa.md](docs/assetto-corsa.md).

Las descargas se guardan en `~/.cache/openlaps` (se cambia con `OPENLAPS_CACHE`): datos de OSM y
Catastro, ortofoto, recortes de relieve, WorldCover, dosel y Sentinel-2 (estos se renuevan cada mes)
y las texturas de asfalto ya fabricadas. Las descargas van en paralelo. El centro de Madrid (800 m)
tarda ~80 s la primera vez y ~35 s las siguientes.

**Visor**: `python -m http.server` en la carpeta del proyecto y abrir
`http://localhost:8000/viewer/?glb=../salida/tepoztlan.glb`, o abrir el visor y usar
"Abrir .glb". Permite manejar (teclado o botones táctiles), con cronómetro y cadenamiento en los tramos.
Con `&cam=x,y,z&mira=x,y,z` (coordenadas del `.glb`) se abre en vista libre mirando ese punto, útil
para revisar un sitio concreto.

Opciones útiles: `--modo libre|tramo|circuito`, `--ruta "lat,lon;lat,lon"`, `--region`,
`--temporada seca|lluvias`, `--vegetacion 0.5` (menos árboles, más ligero), `--res` (malla de
terreno), `--mpp` (resolución de la textura del suelo), `--sin-orto`, `--sin-satelite`,
`--sin-dosel`, `--sin-horizonte`, `--sin-microsoft`, `--sin-catastro`, `--sin-cc0`.

## Fuentes de datos (todas abiertas)

| Dato | Fuente | Licencia |
|---|---|---|
| Calles, edificios, usos de suelo, topes, pasos | OpenStreetMap (Overpass) | ODbL |
| Huellas de edificios | Microsoft Global ML Building Footprints | ODbL |
| Relieve | Copernicus GLO-30 (automático) o INEGI CEM 3.0 / LiDAR (archivo local) | Copernicus / INEGI libre uso |
| Cobertura de suelo | ESA WorldCover 2021, 10 m | CC BY 4.0 |
| Imagen satelital e índice de agua (NDWI) | Sentinel-2 L2A (10 m) vía Element84 / AWS | Licencia abierta Copernicus |
| Árboles reales | Meta/WRI Global Canopy Height 1 m | CC BY 4.0 |
| Texturas fotográficas | ambientCG | CC0 |
| Ortofoto y relieve, España | PNOA 25 cm y MDT05 5 m (IGN) | CC BY 4.0 |
| Edificios y plantas, España | Catastro (DG del Catastro, servicio INSPIRE) | Licencia del Catastro / CC BY 4.0 |
| Ortofoto, Francia | BD ORTHO 20 cm (IGN) | Licence Ouverte 2.0 |
| Ortofoto y relieve, EE. UU. | NAIP 60 cm (USDA) y 3DEP 1 m (USGS) | Dominio público |
| Alturas de edificios, EE. UU. | Overture Maps (incluye LiDAR del USGS); requiere `pyarrow` | ODbL 1.0 |

El texto exacto de cada crédito está en [ATTRIBUTION.md](ATTRIBUTION.md). Cada pista generada
incluye un `ATRIBUCIONES.txt` con las fuentes que usó: **consérvalo si compartes la pista.**

Google Maps / Google Earth no se usan: sus términos prohíben descargar sus datos o derivar
modelos de ellos. En México no hay ortofoto abierta de alta resolución equivalente al PNOA
español; por eso allí la base es Sentinel-2 (10 m) más detalle fotográfico CC0 repetido cada
2 m con el shader multicapa.

## Cómo funciona el algoritmo

1. **Descarga y proyección**: el área se pasa a UTM recentrado (metros; x este, y norte, z
   altura real sobre el nivel del mar).
2. **Relieve**: si es un modelo de superficie, una *apertura morfológica* (mínimo y luego máximo
   en ~70 m) quita copas y techos, solo donde WorldCover dice bosque o ciudad.
3. **Grafo de calles**: las vías de OSM se parten en los nodos compartidos (cruces) y se vuelven
   a unir las que solo se tocan de dos en dos con los mismos atributos. Ancho por carriles,
   `width` o clase de vía.
4. **Planta**: cada vértice se redondea con un arco de radio mínimo por clase (6 m en calles,
   250 m en autopistas) y se remuestrea cada 1.5 m.
5. **Alzado**: altura del terreno por la calle, filtros de mediana y gaussiano (sigma por clase),
   suavizado a través de los cruces y altura común en cada cruce. Puentes y túneles van en línea
   recta entre sus extremos.
6. **Peralte**: `e = v² / (127 R)` con la velocidad de proyecto, máximo 8 %, con transición suave.
7. **Cruces**: cada calle se recorta donde su orilla deja de chocar con la vecina
   (`d = h_j / sen θ + h_i / tan θ`) y el hueco se rellena con el polígono convexo de los extremos.
8. **Terreno**: malla de 2-4 m. Las manzanas urbanas se aplanan al nivel de sus calles y después
   se *talla* con un árbol k-d de puntos de la sección transversal (con su peralte real): plano
   bajo la calzada y talud que crece con el desnivel.
9. **Señalamiento horizontal** según la norma de cada país; raya central continua donde la
   distancia de visibilidad (planta y alzado, ojo a 1.08 m) no alcanza la de rebase. Topes y
   pasos peatonales desde OSM.
10. **Banquetas**: geometría de polígonos (buffer de la calzada menos la calzada, dentro de la
    mancha urbana) con guarnición.
11. **Edificios**: extrusión con forma y colores del techo de OSM cuando existen, y si no, estilo
    regional (azotea plana con tinaco en México, cuatro aguas en Europa); naves industriales e
    iglesias con campanario. En España las huellas y la altura salen del Catastro: cada parte del
    edificio con sus plantas sobre rasante (una pieza por parcela y altura), heredando de OSM los
    colores y el tipo; las iglesias se quedan con la huella de OSM.
12. **Agua**: lagos, embalses y ríos de OSM (también los dibujados como línea, con su ancho), el
    mar a partir de la línea de costa y el agua que ve el satélite (NDWI de Sentinel-2) aunque OSM
    no la tenga. El agua quieta tiene la cota de su orilla; la de los ríos sigue el fondo del valle.
    El cauce se excava y la orilla se rebaja hasta el agua; donde una calle sin puente cruza, no hay
    agua. Los arroyos intermitentes son cauce seco en temporada seca.
13. **Vegetación**: copas individuales como máximos locales de la altura de dosel, más relleno
    del dosel cerrado; especie según cobertura y altitud. Aclareo junto a la calle.
14. **Detalles**: orillas difuminadas, huellas de rodada, arbotantes con luz nocturna, señales,
    defensas metálicas (las reales de OSM `barrier=guard_rail`, heurística en terraplenes altos),
    fantasmas, troncos y postes con colisión, muro invisible en el borde del área y horizonte de
    5 km con imagen satelital.
15. **Modo tramo o circuito**: los puntos se pegan a la calle más cercana y la ruta se calcula
    con Dijkstra respetando los sentidos únicos. Lleva pórticos de salida y meta, vallas en las
    calles laterales, parrilla, línea de IA y cámaras de TV.

Todo es determinista: la misma entrada siempre da la misma pista.

## Notas del formato de Assetto Corsa

`tools/kn5_inspect.py` lee un KN5 completo (aunque pese más de 1 GB) sin cargarlo en memoria:

- KN5 versión 5. `tools/kn5_check.py` recorre el archivo hasta el último byte.
- Los dummies `AC_START_*` usan filas X, Y, Z y traslación, con **Z hacia adelante**.
- `fast_lane.ai`: versión 7, puntos de 20 bytes, extras de 18 floats (velocidad, gas=1, radio,
  ancho izquierdo y derecho, direction=1, normal, longitud del segmento, adelante) y un entero 0
  al final.
- La exportación usa texturas DDS DXT1/DXT5 con mipmaps, mapas normales, terreno multicapa
  (`ksMultilayer_fresnel_nm`: base aérea, máscara y 4 detalles), mallas troceadas por celdas
  espaciales con distancia LOD por tipo, colisiones con material invisible, `ext_config.ini`
  (GrassFX, RainFX, luces nocturnas, sustitución por `ksTree`) y `cameras.ini`.

## Estado

Validado: el pipeline completo en un pueblo (Tepoztlán), en una carretera de montaña (Espinazo
del Diablo) y en España (Chinchón); las pistas cargan y se manejan en Assetto Corsa con CSP; la
estructura del KN5 (`tools/kn5_check.py`); la calidad del codificador DDS (37-40 dB PSNR).

Pendientes conocidos: transición suave de ancho cuando OSM cambia de atributos, muros de
contención, más mobiliario urbano, fachadas con atlas de puertas y ventanas por piso y LOD de
árboles lejanos. Ver [ROADMAP.md](ROADMAP.md).

## Herramientas

- `tools/render.py`: renderizador por CPU (numba) para revisar un `.glb` sin GPU.
  En ARM usar `NUMBA_CPU_NAME=generic`.
- `tools/defectos.py`: busca defectos geométricos en un `.glb` (losas de calzada o banqueta torcidas,
  escalones en la calzada) y saca renders de cerca de los peores sitios.
- `tools/cobertura.py`: qué es cada sitio de una zona (agua, mar, bosque, roca, arena...) sin
  generar la pista, con las discrepancias de agua entre OSM y el satélite; para elegir zonas de
  naturaleza.
- `tools/referencias.py`: descarga fotos reales a pie de calle de una zona con su posición y rumbo
  (Panoramax, Mapillary con `MAPILLARY_TOKEN`, Wikimedia Commons), prefiriendo panorámicas 360° y
  calles vacías; las nocturnas se descartan solas y `hoja.jpg` sirve para marcar a mano las demás.
- `tools/comparar_fotos.py`: renderiza el `.glb` desde la pose de cada foto (corrige rumbo, cabeceo,
  zoom y unos metros de GPS), y saca un panel real / modelo / mezcla / bordes por foto, con IoU del
  cielo y diferencia de cornisa, más un `index.html` con cortinilla.
- `tools/comparar_ruta.py`: vistas del conductor cada N metros a lo largo de un tramo, en una
  hoja (con `--referencia`, al lado de capturas de la carretera real).
- `tools/probar_ac.ps1`: enlaza la pista en Assetto Corsa, arranca una práctica y revisa el log.
- `tools/kn5_check.py`: verifica que un KN5 sea consistente hasta el último byte.
- `tools/kn5_inspect.py`: inventario de texturas, materiales, dummies y mallas de un KN5.

## Contribuir

Issues y pull requests son bienvenidos, en español o en inglés. Ver [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

Código: [MIT](LICENSE). Las pistas generadas contienen datos con las licencias que aparecen en
[ATTRIBUTION.md](ATTRIBUTION.md).

OpenLaps es un proyecto independiente, sin relación ni respaldo de Kunos Simulazioni ni de
Assetto Corsa, y no incluye contenido del juego.
