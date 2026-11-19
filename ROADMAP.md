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
- **Repaso de la calzada** (`roads.armonizar_calzadas`): segunda pasada que lleva calles, cruces, esquinas y
  medianas a una sola superficie continua (plano local por mínimos cuadrados, en dos niveles: viaductos y suelo)
  y tapa las rendijas. Los cruces mezclan el plano de cada calle que llega (con su pendiente) en vez de promediar
  alturas. Escalones entre superficies que se pisan: San Francisco de 2228 celdas (hasta 58 cm) a 30; centro de
  Los Ángeles de 3010 a 748. En vivo, la costura entre pedazos pasa de 20 cm a 0,3 cm.
- **Trazado y terreno más suaves**: eje relajado dentro de un tubo alrededor del trazo de OSM; talud sin
  quiebres (altura ponderada entre calles vecinas, ancho suavizado y alisado fuera de la franja); relieve
  promediado por celda (sin el ruido de los MDE finos).
- **Banquetas**: altura tomada de la calzada ya repasada, bombeo del 1,5 %, vados en los pasos de peatones,
  guarnición achaflanada con física y prolongación hasta las fachadas cercanas.
- **Tráfico para el modo Traffic de CSP** (`trafico.py`): `data/traffic.json` con un carril por carril de OSM y
  sentido (a la altura de la calzada repasada), cruces con el polígono de la calzada (solo del mismo nivel; los que
  dejan menos de 18 m de calle entre ellos, fundidos: los tramos cortos se llenaban y el coche parado dentro del
  cruce de atrás lo bloqueaba todo), el carril sigue de largo donde OSM cambia el número de carriles, retornos en
  las calles sin salida, sin cambios de sentido en los cruces,
  preferencia en glorietas y para quien no tiene alto, y límite urbano si OSM no trae `maxspeed`. CSP calcula los
  giros, la preferencia y los cambios de carril. `tools/trafico_check.py` une carriles y cruces como CSP: en LA, SF
  y Tepoztlán el 100 % de los km de carril recibe coches y lleva a una salida. Se quita con `--sin-trafico`.
- **Tráfico que respeta carriles y semáforos**: CSP agrupa los carriles por el lado del polígono del cruce que
  cruzan, y un lado solo le sirve si por él entra y sale algún carril; con la envolvente del cruce y calles de un
  solo sentido, la mayoría de las entradas se quedaba sin reglas (San Francisco 74 %, Madrid 65 %, Los Ángeles
  31 %, Tepoztlán 23 %: cualquier carril giraba a cualquier lado y nadie cedía al girar a la izquierda). Ahora
  cada calle tiene su lado ("boca"; las rampas que llegan pegadas comparten uno) y las de un sentido llevan un
  carril "fantasma" de 5 m en contra que nadie puede usar: quedan sin reglas 0 %, 10 %, 7 % y 0 %. Con eso:
  **semáforos** de dos fases donde OSM los marca (54 cruces en San Francisco, 13 en Los Ángeles, 9 en Madrid), con
  poste a la vista y luces que CSP enciende, y las avenidas paralelas en verde a la vez; **flechas de carril** de
  OSM (`turn:lanes`); al seguir recto cada carril va al de enfrente o al de al lado (rampas y avenidas); y el
  carril que con las reglas de CSP no tendría por dónde seguir (dos carriles que acaban en T) se quita en vez de
  dejar coches que desaparecen. `trafico.revisar` imita a CSP lado por lado y cuenta entradas sin salida, sin
  reglas y sin luz. Probado dentro de AC en San Francisco y Los Ángeles.
- **Tráfico desde la app**: casilla «Con tráfico» al exportar para AC (genera o no los carriles) y al jugar
  (modo Traffic de CSP o práctica normal; antes se heredaba el modo de la última sesión). Las pistas ya enlazadas
  se reconocen como tales (se pueden volver a jugar), renombrar quita el enlace viejo y al abrir la app se quitan
  los enlaces a pistas borradas, que Content Manager mostraba con error.
- **Carga de Google 3D unas 2,6 veces más rápida** (zona nueva de Madrid, 500 m: de 31-33 s a 12 s): hilos fijos que no
  cierran la conexión, 32 descargas a la vez que empiezan en cuanto se elige cada tesela, árbol recorrido sin esperar
  al más lento de cada nivel, primera pasada con poco detalle (la zona entera en ~8 s) y, en AC, cada malla copiada de
  golpe. El registro da el tiempo de cada etapa (árbol, descargas, conversión) y AC el de crear las mallas.
- **Recorte de Google 3D por tipo de triángulo** (`Mundo.recortar`): las fachadas derretidas hacia la calle se llevan
  a la pared real de OpenLaps y bajan hasta el suelo (antes flotaban sin su planta baja); árboles, coches y kioscos se
  quitan enteros si llegan a la calle (sin medias copas colgando); las teselas lejanas solo pierden el suelo (antes
  quedaban trozos volando).
- **Generación el doble de rápida con los datos ya en caché** (800 m: Madrid de ~75 a 35 s, Los Ángeles de 78 a 30 s,
  Tepoztlán de 59 a 27 s): la compresión DDS de las texturas va compilada y en paralelo (25 s de 60, mismos bytes),
  las máscaras ya no se comprimen dos veces, la losa de las banquetas se arma de una vez y el resumen y el `.glb`
  comparten las mismas mallas.
- **Menos espera de datos en una zona nueva** (800 m, primera vez en la máquina: de 144-164 s a 69-75 s): OpenStreetMap
  se pide de forma escalonada (si un espejo de Overpass falla o tarda 3 s se pregunta al siguiente y al final a la API
  oficial, y gana el primero; antes un día de espejos lentos costaba 64-85 s), y las teselas de la ortofoto y las
  texturas CC0 se bajan de cuatro en cuatro.
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
| Tráfico | Semáforos con giro protegido (CSP solo da dos fases por lados alternos) y cabezas sobre la calzada; cruces fundidos muy grandes del centro de Madrid (5 siguen con la envolvente); medir atascos con muchos coches; tráfico en el modo en vivo (correrlo desde `vivo.lua` con copia propia del código de CSP); exportar `.aip` para AssettoServer (en línea) |

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
5. **IA de carreras** en circuitos y tramos (ya se escribe `fast_lane.ai`; el "AI flood" de CSP la usa como tráfico
   en circuitos de 7 km o más).
