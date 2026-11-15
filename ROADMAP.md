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

## Pendiente

| Tema | Qué falta |
|---|---|
| Alzado | Restricciones de curva vertical por velocidad de proyecto |
| Rayas | Leer `lanes`, `overtaking`, `lane_markings` y `turn:lanes` de OSM; flechas y marcas de glorieta |
| Coches aparcados | `parking:lane` / `parking:both` de OSM → coches estáticos con colisión y ancho útil real |
| Glorietas | Isleta central y peralte |
| Cunetas | Sección en V modelada y vegetación de orilla |
| Edificios | Naves industriales variadas, bajos comerciales (`shop`), techos texturizados con la ortofoto |
| Terreno | Revisar el especular del shader multicapa (el suelo brilla demasiado); textura de talud estirada en terraplenes |
| Horizonte | Ortofoto también en el horizonte |
| Señales | Posición real desde OSM (`traffic_sign`) |

Para revisar un tramo: `tools/comparar_ruta.py --referencia` pone las vistas del conductor cada
N metros al lado de fotogramas de la carretera real.

## Ideas

1. **Aportes de la comunidad**: un archivo de *overrides* por pista (GeoJSON) para corregir un
   edificio (fotos de fachada, color, alturas), mover una señal o marcar un carril, y que cualquiera
   lo pueda compartir.
2. **Editor web local** (Leaflet + OSM, sin servidor externo): dibujar zona o ruta y generar.
3. **Más países**: añadir un perfil en `regions.py` con su norma de rayas, estilo de techos y sus
   servicios abiertos de ortofoto y relieve (Países Bajos PDOK, Portugal DGT, Suiza swisstopo…).
4. **Áreas grandes**: troceo con LOD por distancia y teselas de terreno más gruesas lejos de las vías.
5. **IA de tráfico** en circuitos y tramos (ya se escribe `fast_lane.ai`).
