# Hoja de ruta de openlaps

Objetivo: pistas reconocibles y conducibles de cualquier lugar real, siendo un proyecto
**abierto**: código libre, solo datos abiertos y resultados reproducibles.

## Hecho

- **Perfil de carretera suave**: suavizado por calle completa a través de los cruces (quiebres
  > 2 %: de 58 a 26 en Chinchón).
- **Guardarraíles reales**: si OSM tiene `barrier=guard_rail`, se usa su trazo; la heurística solo
  se aplica en terraplenes > 6 m.
- **Muro invisible** en el borde del área generada.
- **Edificios con datos de OSM**: color de fachada y techo y forma del techo; techos a cuatro aguas
  en Europa.
- **Todo determinista** (semillas por geometría); árboles desde la altura de dosel real
  (Meta/WRI 1 m).
- **Horizonte de 5 km** con imagen satelital.
- Carreteras rurales que salían "urbanas" (WorldCover marca las vías como construido): se filtra
  con una apertura morfológica.
- Raya central por visibilidad de rebase que solo bloquea el terreno real.
- Hitos de arista españoles (blancos con franja negra) cada 50 m en recta y 20 m en curva.
- Orillas que invadían otra calzada en los cruces, bifurcaciones con escalón, edificios que
  cerraban calles, banquetas "nevadas" y rampas: corregidos.
- **Alturas reales en España** con el Catastro (plantas de cada parte del edificio). En el centro
  de Madrid OSM no traía plantas en el 76-84 % de los edificios y salían de 1-3 plantas en vez
  de 5-7; comparado con 156 vistas de panorámicas 360°, el error de cielo visible bajó de 16,4 a
  13,6 puntos.
- **Comparación con fotos reales**: `tools/referencias.py` + `tools/comparar_fotos.py`.
- **Agua y cobertura del suelo**: ríos dibujados como línea, mar desde la costa, agua del satélite
  (NDWI de Sentinel-2), lámina de río que sigue el valle, cauce excavado y cauces secos; mapa
  `<nombre>_cobertura.jpg` y `tools/cobertura.py` para ver qué es cada sitio.
- **Estados Unidos**: perfil `us` (MUTCD, mph, tela asfáltica, torres) con NAIP, 3DEP y alturas de
  Overture; estacionamiento a ambos lados en ciudad; vías de tranvía y cable car en la calzada.
- **Túneles** sobre la huella conjunta (sin muros cruzando la calzada ni huecos al cielo), bajo
  tierra en todo su recorrido; nodos intermedios de túneles y viaductos interpolados. Terreno
  como en los juegos: se quitan los triángulos que tocan la huella y se ponen una tapa recortada
  en la línea de los muros, la franja exterior a su altura exacta y muros de contención y frontal
  donde hay desnivel (sin conos ni pendientes bruscas en las bocas).
- **Acuerdos verticales** por velocidad de proyecto (rasante sin baches).
- Comparación con 360°: orientación automática por la silueta (algunas cámaras guardan el rumbo
  girado 90°).

## Pendiente

| Tema | Qué falta |
|---|---|
| Rayas | Leer `lanes`, `overtaking`, `lane_markings` y `turn:lanes` de OSM; flechas y marcas de glorieta |
| Coches aparcados | El ancho ya los cuenta (`parking:*` y, en EE. UU., por defecto): faltan los coches estáticos con colisión |
| Glorietas | Isleta central y peralte |
| Cunetas | Sección en V modelada y vegetación de orilla |
| Edificios | Naves industriales variadas, bajos comerciales (`shop`), techos texturizados con la ortofoto |
| Fachadas urbanas | En Madrid todas salen iguales (beige con contraventanas verdes): variedad de colores, balcones de hierro, planta baja comercial sin ventanas de vivienda |
| Arbolado urbano | Faltan los árboles de alineación (plátanos en Bailén, Ferraz): son la mayor parte del cielo que sobra en las comparaciones |
| Agua | Cascadas y presas (salto de lámina), orillas con vegetación de ribera y playas de arena modeladas, oleaje en el mar |
| Terreno natural | Paredes de roca casi verticales (cañones) con la ortofoto estirada: textura de roca proyectada en lateral |
| Plazas | El pavimento de las plazas peatonales sale pixelado (ortofoto estirada): textura de granito/adoquín; monumentos y fuentes desde OSM |
| Terreno | Revisar el especular del shader multicapa (el suelo brilla demasiado); textura de talud estirada en terraplenes |
| Horizonte | Ortofoto también en el horizonte |
| Señales | Posición real desde OSM (`traffic_sign`) |

Para revisar un tramo: `tools/comparar_ruta.py --referencia` pone las vistas del conductor cada
N metros al lado de fotogramas de la carretera real. Para una zona urbana: `tools/referencias.py`
baja fotos y panorámicas reales (Panoramax, Mapillary, Commons) y `tools/comparar_fotos.py` las
pone 1 a 1 junto al modelo visto desde la misma pose.

## Ideas

1. **Aportes de la comunidad**: un archivo de *overrides* por pista (GeoJSON) para corregir un
   edificio (fotos de fachada, color, alturas), mover una señal o marcar un carril, y que cualquiera
   lo pueda compartir.
2. **Editor web local** (Leaflet + OSM, sin servidor externo): dibujar zona o ruta y generar.
3. **Más países**: añadir un perfil en `regions.py` con su norma de rayas, estilo de techos y sus
   servicios abiertos de ortofoto y relieve (Países Bajos PDOK, Portugal DGT, Suiza swisstopo…).
4. **Áreas grandes**: troceo con LOD por distancia y teselas de terreno más gruesas lejos de las vías.
5. **IA de tráfico** en circuitos y tramos (ya se escribe `fast_lane.ai`).
