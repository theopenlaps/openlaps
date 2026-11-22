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

- **Muchas menos llamadas de dibujo en Assetto Corsa** (Madrid, 4 km²: de 8 488 a 1 571 mallas; a la vista, de
  1 700 a 476 de mediana y de 5 134 a 1 137 en el peor 5 %; en sombras, de 1 125 a 198): AC dibuja cada malla aparte y
  sin oclusión, así que en una ciudad grande mandaba el número de mallas, no los triángulos. La celda de troceo ya no
  es fija (`mesh.Scene.meshes`): se parte de celdas de 1,6 km y solo se subdividen, hasta 200 m, las que pasan de
  6 000 triángulos; cada color de fachada, las señales o los árboles salen en pocas mallas grandes, con el `lodOut`
  alargado en lo que crece la malla para que nada desaparezca antes. El suelo a ras (calzada, banquetas) ya no
  proyecta sombra y el pretil va en la misma malla que el muro. Misma geometría y mismos materiales.
  `tools/rendimiento_ac.py` lo mide sobre cualquier `.kn5`.
- **Unión entre pedazos del modo en vivo**: el corte es exacto (`vivo.partir`: los triángulos que cruzan el borde se
  parten por él; antes se repartían enteros por su centroide y el borde quedaba dentado y distinto en cada vecino),
  cada pedazo nuevo se cose a los vecinos ya generados (`vivo.coser`: calzada con calzada, banqueta con banqueta y
  suelo con suelo, repartiendo la diferencia en 10 m) y el suelo lleva un faldón en el borde. En el centro de Madrid:
  escalón típico de la calzada de 6 a 0,8 cm, del suelo de 43 a 10 cm como mucho, sin solapes.
  `tools/costuras.py` lo mide sobre una carpeta de pedazos. Dos vecinos no se generan a la vez (salvo urgencia).
- **Modo en vivo en la app, a la par de una pista normal**: las zonas preparadas salen en «Mis pistas» y se reanudan
  sin dibujarlas otra vez; al detener, el panel se queda con «Reanudar», carpeta y mapa; se ve la zona, la calidad y
  el tiempo en marcha, y se dice lo que en vivo aún no hay (tráfico). «Conducir» pasa a llamarse «Vista previa 3D».
- **Tráfico en el modo en vivo, primera versión** (`trafico_vivo.py`, `vivo_ac.lua`): el servidor calcula solo la red
  de calles (sin mallas, `pipeline.red_de_calles`) de una ventana de 1,6 km alrededor del coche y saca sus carriles
  y cruces (7-9 s en el centro de Madrid, en un proceso aparte); el script de la pista corre la misma simulación
  del modo Traffic de CSP desde una copia que la app deja en `extension/trafico` (un script de pista no puede leer
  la carpeta de la herramienta; los modelos de coche van por enlace duro). Al alejarse 300 m del centro pide la
  ventana siguiente y pasa a ella los coches que van por un carril (siguen 86-112 de 120; el cambio para el juego
  0,2-0,3 s). Los carriles se pegan a la calzada con rayos contra la física de los pedazos. Se apaga con la casilla
  «Con tráfico» o dentro del juego (ajustes de OpenLaps, con el número de coches). Sin semáforos todavía.
- **El tráfico lo lleva siempre el script de la pista** (también en una pista normal, con su `traffic.json` y sus
  semáforos; el modo Traffic de CSP queda de respaldo): así los coches cercanos van pegados al suelo con un rayo
  por cuadro (en un giro CSP traza una Bézier en 3D entre los dos carriles y la pendiente de los extremos sacaba el
  coche del asfalto) y la velocidad del tráfico se ajusta dentro del juego. `vivo.refrescar_lua` pone al día el
  script de las pistas ya generadas al abrirlas desde la app. Botón «Restablecer valores por defecto» en los
  parámetros de generación dentro del juego.
- **Google 3D, edificios sin planta en OpenLaps**: un grupo grande, alto y con caras ordenadas lejos de toda pared de
  OpenLaps ya no se quita como «objeto» ni pierde la fachada que pisa la banqueta (en Ciudad de México, lo quitado
  como objeto baja un 40 %). También se quita lo alto y suelto que cae donde OpenLaps ya tiene sus árboles.
- **Google 3D, manzanas enteras sin edificios**: al corregirse la altura local en más de 1,5 m se descartaban las
  teselas ya hechas de esa zona y no se rehacían hasta que el coche se movía 60 m; en Ciudad de México, 30 de 252
  teselas quedaban sin malla (rectángulos vacíos). `_reponer` las rehace en la misma pasada: las plantas cubiertas
  por Google pasan del 95,6 al 99,5 %. Y **faldones**: donde la malla termina en el aire (el relieve de Google y el
  de OpenLaps difieren varios metros dentro de una manzana) se prolonga hasta el suelo, sin hueco por debajo.
  El faldón continúa la fachada: va en franjas de hasta 2,5 m que repiten, reflejada, la pared de encima (el mapa
  de textura del triángulo del que cuelga dice cuánto cambia por metro; no se lee ninguna imagen), en vez de estirar
  el color del borde en rayas. Unos 4 ms por tesela.
- **Procesos que no se quedan huérfanos** (`procesos.py`): los procesos de trabajo del modo en vivo salen cuando
  muere quien los creó, y los servicios que lanza la app, cuando se cierra la app. Antes, tras cerrarla de golpe,
  quedaban esperando para siempre con 1 GB reservado cada uno: tras unas sesiones Assetto Corsa ya no arrancaba.

- **Espacio en disco bajo control** (`gui/espacio.py`): Ajustes enseña cuánto ocupa todo lo de OpenLaps (pistas,
  zonas en vivo y sus pistas de AC, caché de descargas, programa y datos de la ventana); «Mis pistas» dice cuánto
  pesa cada una y deja borrarla con todo lo suyo (carpeta de AC y enlace en el juego incluidos), también las zonas en
  vivo. Al abrirse, la app quita sola lo que ya no sirve (pedazos de una versión anterior, descargas a medias, índices
  de un formato viejo, la caché del instalador) y aplica el tope de la caché de descargas (10 GB por defecto; se va
  primero lo que lleva más tiempo sin usarse).

- **Subregiones** (`regions.SUBREGIONES`): dentro de un país no se construye igual en todas partes. Cada subregión es
  el perfil del país con unos pocos campos cambiados (colores de fachada y de tejado, forma del techo, plantas
  típicas, especies): en México, gran ciudad de bloque y concreto, pueblo colonial de colores, pueblo corriente,
  norte y sureste; en España, norte, sur, Mediterráneo y Canarias; sur de Francia; suroeste, Pacífico y sureste de
  EE. UU. Se elige sola por coordenadas (cajas aproximadas) o a mano (`--subregion`, «Estilo de la zona» en la app,
  con la muestra de colores). Siempre 8 fachadas: no añade materiales, texturas ni mallas a la pista.

- **Caminos sin asfalto, fase 1 (rally y offroad)**: la terracería ya no es una sola superficie. Grava, tierra,
  arena y pasto de OSM llevan cada uno su física (`MXGRAVA`, `MXTIERRA`, `MXARENA`, `MXPASTO`, y `LOOSE` para lo
  compactado), todas pista válida, con fricción por debajo de 0.9 y el tipo de superficie de CSP
  (`_EXT_SURFACE_TYPE=GRAVEL`/`SAND`/`GRASS`, firmeza y ruido Perlin). Y se deduce la terracería que OSM no
  etiqueta (`RoadNetwork._superficies`): `tracktype`, `smoothness`, el mismo camino partido en varias vías, el
  ramal de una brecha y, en México, los caminos sin clasificar fuera de poblado; nunca una vía con señales de
  asfalto. `--terraceria auto|osm|rural` («Caminos de tierra» en la app). En la Sierra de Lobos una zona de
  1.5 km pasa de 0 a 4.7 km de terracería; Tepoztlán no cambia. El color de Sentinel-2 se probó y se descartó:
  a 10 m separa asfalto de tierra en Guanajuato pero no en Finlandia. Falta probarlo en el juego, y siguen
  pendientes la geometría de camino (fase 2), el terreno conducible (fase 3) y los tramos largos (fase 4).

- **Caminos sin asfalto, fase 2 (geometría)**: un camino de tierra ya no es una cinta lisa. Cada uno lleva su
  perfil según la física (`roads.PERFIL_CAMINO`, `Edge._camino`): roderas donde pisan las ruedas (2 a 5 cm, un
  par si es de un carril y dos si caben dos coches), bombeo, baches largos (ondas de 7 a 47 m, 1 a 3.5 cm) y
  ancho irregular (±6 a ±14 %, salvo que OSM dé el ancho). Sale todo de `Edge.at`, así que la calzada, sus
  orillas, el corte del terreno y la línea de la IA ven la misma superficie; se apaga en 8 m hacia los cruces y
  en las horquillas, y las ondas dependen de la posición en el mundo, no de cómo se parta la vía (dos pedazos
  del modo en vivo dan la misma superficie). La malla gana un vértice en cada rodera: +2.6 % de triángulos en
  Ouninpohja (15 km de caminos), sin costuras nuevas de más de 9 cm. Las roderas son geometría, no dibujo: la
  textura sigue siendo la de siempre. Sin cunetas todavía: con el terreno a 2-4 m no se pueden tallar (fase 3).

- **Caminos sin asfalto, fase 3 (offroad)**: el suelo fuera del camino ya no es todo pasto. Cada triángulo del
  terreno toma su física del mapa de cobertura (`terrain.fisica_del_suelo`): pasto, tierra suelta bajo el bosque
  y en lo urbano, arena en playas y cauces secos, roca (`MXROCA`, también en pendientes de más de 38°) y agua
  en lagos y ríos, con el tipo de superficie de CSP; sigue sin contar como pista. `--veredas` («Veredas
  conducibles» en la app) mete las sendas de monte de OSM (`path`, `bridleway`) como brechas de tierra de
  2.6 m: 41 km en 1.5 km de La Pedriza. Y vados (`RoadNetwork.vadear`): donde un camino de tierra cruza un
  arroyo sin puente (hasta 25 m, o `ford=yes`), la calzada baja con rampas de 9 m y el agua pasa 12 cm por
  encima, en vez de cortarse como bajo lo asfaltado; en Landmannalaugar queda a 10-22 cm. El agua no frena
  al coche en el vado (la física es la del camino). Sin hacer: malla de terreno más fina junto a los caminos y
  cunetas (pide una malla adaptativa), y las teselas de terreno pasan a 2-3 mallas cada una (+6 % de mallas en
  el KN5 de Tepoztlán).

- **Repaso de las fases 1 a 3 de los caminos sin asfalto**: (1) cunetas (`Terrain.cunetas`, `--sin-cunetas`): zanja
  en V de 1.6 m y 25 cm a cada lado de los caminos de tierra fuera de poblado, como franja de malla propia; el
  terreno se recorta con exactitud por donde pasa (igual que en las bocas de túnel) y la franja se hunde bajo el
  corte, sin rendijas; 8 km en Ouninpohja, +2.7 % de triángulos. (2) El vado frena: el trozo bajo el agua lleva la
  física `MXVADO`; el camino baja a buscar la lámina del arroyo (antes el agua subía hasta el camino y quedaba un
  lomo), con rampas del 10 % como mucho, y se vadean cruces de hasta 80 m. (3) Las roderas se ven: una huella
  oscura (`rodera`) encima de cada una. (4) El perfil se apaga también donde otra calzada corre pegada al camino.
  (5) `Terrain.z_plano` usaba el paso nominal de la rejilla y no el real: las tapas de túnel (y ahora las cunetas)
  tomaban la altura de un punto corrido hasta 1 m. (6) **Relieve de 30 m corrido**: `rasters._read_tiles`
  recortaba Copernicus y WorldCover empezando en la esquina pedida y no en un borde de pixel, así que cada
  descarga salía desplazada hasta 15 m; dos pedazos vecinos del modo en vivo veían relieves distintos (en un
  bosque de Finlandia, 19 cm de salto en la calzada y 75 cm en el terreno; ahora 0 y 0.6 cm). Afecta a toda zona
  sin relieve nacional (México entero); la caché de esos dos rásteres se renueva sola. Queda: un salto de 7 cm
  en el relleno de un cruce de la Sierra de Lobos entre una brecha ancha y un camino de servicio.

- **Caminos sin asfalto, fase 4 (modo rally)**: tramos largos. Un tramo o circuito en una zona de más de 24 km²
  se genera como **corredor** de 220 m a cada lado de la ruta (`--corredor auto|metros|0`,
  `pipeline.ancho_corredor`): la ruta se calcula antes que nada y a ese polígono se acotan las calles
  (`RoadNetwork.acotar`), las teselas de terreno, los edificios, el agua, la vegetación y lo demás de OSM; el
  resto del rectángulo lo cubre el relieve lejano, y el muro invisible va por el borde del corredor. Un tramo de
  12 km en la Sierra de Lobos: 4.7 km² de 30, 3.5 min, KN5 con 91 MB de texturas. En zonas así no se piden
  ortofoto ni altura de dosel (no caben), y el terreno va a 4 m. **Notas de copiloto** (`openlaps/notas.py`):
  de la curvatura del eje salen las curvas (lado, grado 1-6 por radio, larga, se cierra/abre, horquilla), las
  rasantes y saltos, los cambios de superficie y los vados, con la distancia entre una y otra; van a `NOTAS.txt`
  (libro de ruta), `data/notas.json` y `extension/notas.lua`, que las canta con los mensajes del sistema de AC
  unos 4 s antes (un script de pista de CSP no puede dibujar un HUD propio: comprobado en las definiciones de
  `extension/internal/lua-sdk`). 161 notas en ese tramo. Sin probar en el juego. Sin hacer: cintas y público, voz
  del copiloto, y exportar las notas al formato de otros copilotos (no hay especificación pública que seguir).

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
