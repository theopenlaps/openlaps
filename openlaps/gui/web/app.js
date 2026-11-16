"use strict";
/* Interfaz de OpenLaps: mapa (Leaflet), panel normal, asistente, vista previa y progreso.
   Habla con Python por /api/<método> (POST con token) y recibe el progreso por /api/eventos (SSE). */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const M_POR_GRADO = 111320;
const AMARILLO = "#e8b21e";
const FUENTES = ["orto", "satelite", "dosel", "horizonte", "microsoft", "catastro", "overture", "cc0"];
const ORTO = { pnoa: "PNOA 25 cm", geopf: "BD ORTHO 20 cm", naip: "NAIP 60 cm", "": "Sentinel-2 10 m" };
const RELIEVE = { mdt05: "MDT05 5 m", "3dep": "3DEP 1 m", "": "Copernicus 30 m" };
const NO_CONDUCIBLE = new Set(["footway", "path", "steps", "cycleway", "pedestrian", "bridleway", "corridor",
  "proposed", "construction", "elevator", "platform", "via_ferrata", "raceway", "bus_stop"]);

// ------------------------------------------------------------------ estado
const S = {
  modo: "libre", centro: null, ancho: 800, alto: 800, ruta: [],
  calidad: "normal", nombre: "", nombreAuto: "", ac: true, jugar: false,
  maxKm2: 24, csp: false, vivo: null, vivoModo: false,
  temporada: "seca", region: "auto", vegetacion: 1, usar: Object.fromEntries(FUENTES.map((k) => [k, true])), dem: null,
  lugar: "", lugarElegido: false, lugarEsPunto: false, regionInfo: null, osm: null,
  asistente: false, paso: 1,
  trabajo: null, trabajoId: null, t0: 0, registro: [], ultimaPista: null, borradorGlb: null,
  bor: null, borClave: null, borError: "", borRegistro: [], autoBorrador: true, verOsm: true,
  pistas: [], ajustes: {}, acRuta: null, salida: "", version: "",
};

async function api(metodo, ...args) {
  try {
    const r = await fetch(`/api/${metodo}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-OpenLaps-Token": TOKEN }, body: JSON.stringify(args),
    });
    const d = await r.json().catch(() => ({ error: t("err.respuesta", { s: r.status }) }));
    return d === null ? null : d;
  } catch (e) {
    return { error: t("err.conexion") };
  }
}

function slug(t) {
  return (t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^\x00-\x7f]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}
const nombreFinal = () => slug(S.nombre) || S.nombreAuto || "pista";
const fmt = (n, d = 0) => Number(n).toLocaleString(LOCALE[IDIOMA], { maximumFractionDigits: d, minimumFractionDigits: d });
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function aviso(texto, { mal = false, ms = 5000, accion = null } = {}) {
  const el = $("#aviso");
  el.className = "aviso" + (mal ? " mal" : "");
  el.textContent = texto;
  if (accion) {
    const b = document.createElement("button");
    b.className = "enlace";
    b.style.marginLeft = "10px";
    b.textContent = accion.texto;
    b.onclick = () => { el.hidden = true; accion.fn(); };
    el.append(b);
  }
  el.hidden = false;
  clearTimeout(aviso.t);
  if (ms) aviso.t = setTimeout(() => { el.hidden = true; }, ms);
}

// ------------------------------------------------------------------ mapa
const map = L.map("mapa", { zoomControl: false, preferCanvas: true, worldCopyJump: true }).setView([35, 0], 3);
L.control.zoom({ position: "bottomright" }).addTo(map);
map.attributionControl.setPrefix(false);
const CAPAS = {
  mapa: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }),
  satelite: L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg", {
    maxZoom: 19, maxNativeZoom: 15,
    attribution: 'Sentinel-2 cloudless 2016 © <a href="https://s2maps.eu">EOX IT Services</a> (datos modificados de Copernicus Sentinel)' }),
};
let capaActual = null;
function ponerCapa(nombre) {
  if (!CAPAS[nombre]) nombre = "mapa";
  if (capaActual) map.removeLayer(CAPAS[capaActual]);
  CAPAS[nombre].addTo(map);
  capaActual = nombre;
  map.getContainer().classList.toggle("mapa-oscuro", nombre === "mapa");
  $$("[data-capa]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.capa === nombre)));
}

const svg = L.svg({ padding: 0.5 });
map.createPane("osm").style.zIndex = 390;   // entre el mapa y la zona; se atenúa mientras carga otra
const capaOSM = L.layerGroup().addTo(map);
const Z = { rect: null, centro: null, esquina: null, etiqueta: null };
const R = { marcas: [], linea: null };
let arrastre = null;

// la zona es un rectángulo de ancho × alto (m) centrado en S.centro
function caja(centro = S.centro, ancho = S.ancho, alto = S.alto) {
  const [lat, lon] = centro;
  const dlat = alto / 2 / M_POR_GRADO, dlon = ancho / 2 / (M_POR_GRADO * Math.cos((lat * Math.PI) / 180));
  return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]];
}
const kmZona = () => (S.ancho * S.alto) / 1e6;
// la zona no pasa de maxKm2 (24); para más, el modo en vivo (casilla): prepara esta zona y lo demás se genera
// mientras se conduce
const MIN_VIVO_KM2 = 2;   // el modo en vivo se puede activar a partir de 2 km² de zona
const esVivo = () => S.modo === "libre" && !!S.centro && !!S.vivoModo && kmZona() >= MIN_VIVO_KM2 - 1e-9;
const enElMaximo = () => kmZona() >= S.maxKm2 * 0.98;   // el recorte redondea a 50 m: se queda un poco por debajo
const textoZona = () => esVivo() ? t("zona.area_vivo", { km: fmt(kmZona(), 2) })
  : enElMaximo() ? t("zona.area_max", { km: fmt(kmZona(), 2), max: fmt(S.maxKm2) }) : t("zona.area", { km: fmt(kmZona(), 2) });
// mientras se genera la zona (pista o modo en vivo) no se puede cambiar: aviso y que cancele antes
function bloqueo() {
  if (S.trabajo) return "bloq.zona";
  if (S.vivo && S.vivo.activo) return "bloq.vivo";
  return null;
}
function avisarBloqueo() {
  const b = bloqueo();
  if (b) aviso(t(b), { mal: true, ms: 6000 });
  return !!b;
}
function aplicarBloqueo() {
  const b = !!bloqueo();
  document.body.classList.toggle("zona-bloqueada", b);
  for (const m of [Z.centro, Z.esquina, ...R.marcas]) if (m && m.dragging) (b ? m.dragging.disable() : m.dragging.enable());
  for (const el of $$('[data-campo="ancho"], [data-campo="alto"], [data-campo="vivoModo"], [data-grupo="modo"] button, [data-accion="deshacer-punto"], [data-accion="borrar-ruta"]')) el.disabled = b;
  // por debajo de 2 km² no hay modo en vivo (y si se achica la zona, se desmarca)
  const pocaZona = kmZona() < MIN_VIVO_KM2 - 1e-9;
  if (pocaZona && S.vivoModo && !b) S.vivoModo = false;
  for (const el of $$('[data-campo="vivoModo"]')) {
    el.disabled = b || pocaZona;
    el.checked = !!S.vivoModo && !pocaZona;
    el.closest("label").title = pocaZona ? t("campo.vivo_min", { min: MIN_VIVO_KM2 }) : "";
    el.closest("label").classList.toggle("apagada", pocaZona);
  }
}
// que el rectángulo no pase del máximo: si se agranda un lado, se recorta el otro (o los dos, a escala)
function limitarZona(cambio) {
  const max = S.maxKm2 * 1e6;
  if (S.ancho * S.alto <= max) return;
  if (cambio === "ancho") S.alto = Math.max(200, Math.floor(max / S.ancho / 50) * 50);
  else if (cambio === "alto") S.ancho = Math.max(200, Math.floor(max / S.alto / 50) * 50);
  else {
    const f = Math.sqrt(max / (S.ancho * S.alto));
    S.ancho = Math.floor((S.ancho * f) / 50) * 50;
    S.alto = Math.floor((S.alto * f) / 50) * 50;
  }
}

function icono(clase, html = "", tam = 18) {
  return L.divIcon({ className: "", html: `<div class="${clase}">${html}</div>`, iconSize: [tam, tam], iconAnchor: [tam / 2, tam / 2] });
}

function dibujarZona(moviendo = null) {
  const visible = S.modo === "libre" && S.centro;
  if (!visible) {
    for (const k of Object.keys(Z)) if (Z[k]) { map.removeLayer(Z[k]); Z[k] = null; }
    return;
  }
  const b = caja();
  const sur = [b[0][0], S.centro[1]];
  if (!Z.rect) {
    Z.rect = L.rectangle(b, { renderer: svg, color: AMARILLO, weight: 2, dashArray: "7 5", fillColor: AMARILLO, fillOpacity: 0.07, bubblingMouseEvents: false })
      .addTo(map);
    Z.rect.on("mousedown", (e) => {
      if (e.originalEvent.button !== 0) return;
      if (avisarBloqueo()) { L.DomEvent.stop(e); return; }
      arrastre = { ini: e.latlng, centro: [...S.centro] };
      map.dragging.disable();
      L.DomEvent.stop(e);
    });
    Z.centro = L.marker(S.centro, { draggable: true, icon: icono("asa centro", "", 26), title: t("zona.mover"), zIndexOffset: 500 }).addTo(map);
    Z.centro.on("drag", (e) => { S.centro = [e.latlng.lat, e.latlng.lng]; dibujarZona("centro"); });
    Z.centro.on("dragend", cambioZona);
    Z.esquina = L.marker(b[1], { draggable: true, icon: icono("asa"), title: t("zona.tamano"), zIndexOffset: 600 }).addTo(map);
    Z.esquina.on("drag", (e) => {
      // ancho y alto por separado: un rectángulo como lo quiera el usuario
      const dy = Math.abs(e.latlng.lat - S.centro[0]) * M_POR_GRADO;
      const dx = Math.abs(e.latlng.lng - S.centro[1]) * M_POR_GRADO * Math.cos((S.centro[0] * Math.PI) / 180);
      S.ancho = Math.min(8000, Math.max(200, Math.round((2 * dx) / 50) * 50));
      S.alto = Math.min(8000, Math.max(200, Math.round((2 * dy) / 50) * 50));
      limitarZona();
      dibujarZona("esquina");
      sync();
    });
    Z.esquina.on("dragend", () => { Z.esquina.setLatLng(caja()[1]); cambioZona(); });
    Z.etiqueta = L.marker(sur, { interactive: false, icon: L.divIcon({ className: "", html: "", iconSize: [0, 0] }) }).addTo(map);
  }
  Z.rect.setBounds(b);
  if (moviendo !== "centro") Z.centro.setLatLng(S.centro);
  if (moviendo !== "esquina") Z.esquina.setLatLng(b[1]);
  Z.etiqueta.setLatLng(sur);
  Z.etiqueta.setIcon(L.divIcon({ className: "", html: `<span class="etiqueta-tam${esVivo() ? " vivo" : ""}">${fmt(S.ancho)} × ${fmt(S.alto)} m · ${fmt(kmZona(), 2)} km²${esVivo() ? ` · ${t("vivo.insignia")}` : ""}</span>`, iconSize: [0, 0] }));
  Z.rect.setStyle({ dashArray: esVivo() ? "2 6" : "7 5" });
}

map.on("mousemove", (e) => {
  if (!arrastre) return;
  S.centro = [arrastre.centro[0] + e.latlng.lat - arrastre.ini.lat, arrastre.centro[1] + e.latlng.lng - arrastre.ini.lng];
  dibujarZona();
});
function finArrastre() {
  if (!arrastre) return;
  arrastre = null;
  map.dragging.enable();
  cambioZona();
}
map.on("mouseup", finArrastre);
document.addEventListener("mouseup", finArrastre);

function dibujarRuta() {
  R.marcas.forEach((m) => map.removeLayer(m));
  R.marcas = [];
  if (R.linea) { map.removeLayer(R.linea); R.linea = null; }
  if (S.modo === "libre") return;
  const n = S.ruta.length;
  S.ruta.forEach((p, i) => {
    const circ = S.modo === "circuito";
    const ini = i === 0, fin = i === n - 1 && n > 1 && !circ;
    const texto = ini ? (circ ? "S/M" : "S") : fin ? "M" : String(i);
    const m = L.marker(p, { draggable: true, icon: icono(`punto${ini ? " ini" : fin ? " fin" : ""}`, texto, 26),
      title: t("ruta.title"), zIndexOffset: 700 }).addTo(map);
    m.on("drag", (e) => { S.ruta[i] = [e.latlng.lat, e.latlng.lng]; actualizarLinea(); });
    m.on("dragend", () => { cambioZona(); sync(); });
    m.on("contextmenu", () => { if (avisarBloqueo()) return; S.ruta.splice(i, 1); dibujarRuta(); cambioZona(); sync(); });
    R.marcas.push(m);
  });
  actualizarLinea();
}
function actualizarLinea() {
  const pts = S.modo === "circuito" && S.ruta.length > 2 ? [...S.ruta, S.ruta[0]] : S.ruta;
  if (!R.linea) R.linea = L.polyline([], { renderer: svg, color: AMARILLO, weight: 3, dashArray: "2 7", lineCap: "round", interactive: false }).addTo(map);
  R.linea.setLatLngs(pts);
}

function modoClic() {
  if (S.asistente && S.paso === 1) return "lugar";
  if (S.asistente && S.paso === 2) return null;
  return S.modo === "libre" ? "zona" : "ruta";
}
map.on("click", (e) => {
  const p = [e.latlng.lat, e.latlng.lng];
  const m = modoClic();
  if (m && avisarBloqueo()) return;
  if (m === "lugar" || m === "zona") {
    S.centro = p;
    if (m === "lugar") avanzarSolo(1);
    if (m === "lugar" || !S.lugarElegido) {
      S.lugar = t("lugar.punto", { lat: p[0].toFixed(4), lon: p[1].toFixed(4) });
      S.lugarEsPunto = true;
      S.lugarElegido = true;
      if (!S.nombre && !S.nombreAuto) S.nombreAuto = "zona";
    }
    if (m === "lugar" && S.modo !== "libre") S.ruta = [];
    dibujarZona();
    cambioZona();
  } else if (m === "ruta") {
    S.ruta.push(p);
    S.lugarElegido = true;
    if (!S.centro) S.centro = p;
    dibujarRuta();
    cambioZona();
  }
  sync();
});

function cambiarModo(m) {
  if (m !== S.modo && avisarBloqueo()) return;
  S.modo = m;
  map.getContainer().style.cursor = m === "libre" ? "" : "crosshair";
  dibujarZona();
  dibujarRuta();
  cambioZona();
  sync();
}

function irA(lat, lon, nombre, cajaBusqueda) {
  if (avisarBloqueo()) return;
  S.lugar = nombre;
  S.lugarEsPunto = false;
  S.lugarElegido = true;
  S.osm = null;          // otro sitio: las cifras de la zona anterior ya no valen
  S.nombreAuto = slug(nombre) || "zona";
  let zoom = 15;
  if (cajaBusqueda && cajaBusqueda.length === 4) {
    const [s, n, w, e] = cajaBusqueda;
    zoom = Math.max(12, Math.min(16, map.getBoundsZoom([[s, w], [n, e]])));
  }
  map.flyTo([lat, lon], zoom, { duration: 0.8 });
  S.centro = [lat, lon];
  avanzarSolo(1);
  if (S.modo !== "libre") { S.ruta = []; dibujarRuta(); }
  dibujarZona();
  cambioZona();
  sync();
}

// ------------------------------------------------------------------ zona -> vista previa, región
function zonaActual() {
  if (S.modo === "libre") return S.centro ? caja() : null;
  if (!S.ruta.length) return null;
  const lats = S.ruta.map((p) => p[0]), lons = S.ruta.map((p) => p[1]);
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2;
  const dlat = 200 / M_POR_GRADO, dlon = 200 / (M_POR_GRADO * Math.cos((lat0 * Math.PI) / 180));
  return [[Math.min(...lats) - dlat, Math.min(...lons) - dlon], [Math.max(...lats) + dlat, Math.max(...lons) + dlon]];
}
function areaKm2(b) {
  if (!b) return 0;
  const lat0 = (b[0][0] + b[1][0]) / 2;
  return ((b[1][0] - b[0][0]) * M_POR_GRADO * (b[1][1] - b[0][1]) * M_POR_GRADO * Math.cos((lat0 * Math.PI) / 180)) / 1e6;
}
function distancia(a, b) {
  const r = Math.PI / 180, dlat = (b[0] - a[0]) * r, dlon = (b[1] - a[1]) * r;
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dlon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}
function largoRuta() {
  let d = 0;
  for (let i = 1; i < S.ruta.length; i++) d += distancia(S.ruta[i - 1], S.ruta[i]);
  if (S.modo === "circuito" && S.ruta.length > 2) d += distancia(S.ruta[S.ruta.length - 1], S.ruta[0]);
  return d;
}

let tZona = null;
function cambioZona() {
  clearTimeout(tZona);
  tZona = setTimeout(() => {
    cargarOSM();
    pedirRegion();
    guardarVista();
    programarBorrador();
  }, 650);
  sync();
}

async function pedirRegion() {
  const p = S.modo === "libre" ? S.centro : S.ruta[0];
  if (!p) return;
  const r = await api("region", p[0], p[1]);
  if (r && !r.error) { S.regionInfo = r; sync(); }
}

let tVista = null;
function guardarVista() {
  clearTimeout(tVista);
  tVista = setTimeout(() => {
    const c = map.getCenter();
    api("guardar_ajustes", { vista: { lat: c.lat, lon: c.lng, zoom: map.getZoom(), centro: S.centro, ancho: S.ancho, alto: S.alto, lugar: S.lugar, nombreAuto: S.nombreAuto } });
  }, 800);
}
map.on("moveend", guardarVista);

let osmPedido = 0;
const osmCache = new Map();
async function cargarOSM() {
  const b = zonaActual();
  const id = ++osmPedido;
  if (!b || (S.modo !== "libre" && S.ruta.length < 2)) {
    capaOSM.clearLayers();
    S.osm = null;
    sync();
    return;
  }
  const km2 = areaKm2(b);
  if (km2 > 9) { capaOSM.clearLayers(); S.osm = { error: t("osm.grande") }; sync(); return; }
  const zona = { modo: S.modo, centro: S.centro, ancho: S.ancho, alto: S.alto, ruta: S.ruta };
  const clave = JSON.stringify(zona);
  let datos = osmCache.get(clave);
  if (!datos) {
    S.osm = S.osm && S.osm.km !== undefined ? { ...S.osm, cargando: true } : { cargando: true };
    map.getPane("osm").style.opacity = 0.3;
    sync();
    datos = await api("vista_osm", zona);
    if (datos && datos.superado) return;            // llegó otra zona mientras tanto
    if (!datos || datos.error) {
      if (id === osmPedido) { S.osm = { error: datos?.error || t("osm.error") }; sync(); }
      return;
    }
    osmCache.set(clave, datos);
    if (osmCache.size > 12) osmCache.delete(osmCache.keys().next().value);
  }
  if (id === osmPedido) pintarOSM(datos, km2 <= 3, b);
}

function pintarOSM(datos, conEdif, b) {
  capaOSM.clearLayers();
  map.getPane("osm").style.opacity = 1;
  let km = 0, edificios = 0, agua = false;
  const usos = new Map();
  const ver = S.verOsm;
  // la consulta cubre una caja algo mayor (rejilla de caché): las cifras cuentan solo lo de dentro
  const dentro = (p) => p[0] >= b[0][0] && p[0] <= b[1][0] && p[1] >= b[0][1] && p[1] <= b[1][1];
  for (const el of datos.vias || []) {
    const pts = el.g;
    const t = el.t || {};
    if (t.building) {
      if (dentro(pts[0])) edificios++;
      if (ver && conEdif) L.polygon(pts, { stroke: false, fillColor: "#9aa0a8", fillOpacity: 0.45, interactive: false, pane: "osm" }).addTo(capaOSM);
    } else if (t.natural === "water" || t.waterway) {
      agua = true;
      if (ver) (t.waterway ? L.polyline(pts, { color: "#4a8fd6", weight: 4, opacity: 0.8, interactive: false, pane: "osm" })
        : L.polygon(pts, { stroke: false, fillColor: "#4a8fd6", fillOpacity: 0.5, interactive: false, pane: "osm" })).addTo(capaOSM);
    } else if (t.highway) {
      const cls = t.highway.replace("_link", "");
      const conducible = !NO_CONDUCIBLE.has(cls) && t.area !== "yes";
      if (conducible) {
        for (let i = 1; i < pts.length; i++) if (dentro(pts[i])) km += distancia(pts[i - 1], pts[i]) / 1000;
        (el.n || []).forEach((n, i) => { if (pts[i] && dentro(pts[i])) usos.set(n, (usos.get(n) || 0) + 1); });
      }
      if (!ver) continue;
      const mayor = ["motorway", "trunk", "primary"].includes(cls), media = ["secondary", "tertiary"].includes(cls);
      const estilo = !conducible ? { color: "#7d838b", weight: 1, opacity: 0.6, dashArray: "3 4" }
        : mayor ? { color: AMARILLO, weight: 4.5, opacity: 0.95 }
          : media ? { color: "#f1eee7", weight: 3.5, opacity: 0.95 } : { color: "#d8d3c8", weight: 2.2, opacity: 0.85 };
      L.polyline(pts, { ...estilo, interactive: false, pane: "osm" }).addTo(capaOSM);
    }
  }
  let cruces = 0;
  usos.forEach((v) => { if (v >= 2) cruces++; });
  S.osm = { km, edificios, cruces, agua, conEdif };
  sync();
}

// ------------------------------------------------------------------ estimaciones
function estimacion() {
  const b = zonaActual();
  const km2 = areaKm2(b);
  const f = { rapida: 0.6, normal: 1, detallada: 1.8 }[S.calidad] || 1;
  const seg = (20 + 60 * km2) * f * (S.ac ? 1.15 : 1);
  const mb = 55 * km2 * ({ rapida: 0.7, normal: 1, detallada: 1.6 }[S.calidad] || 1);
  return { km2, seg, mb };
}
const tiempoTexto = (s) => (s < 60 ? `~${Math.max(10, Math.round(s / 10) * 10)} s` : `~${Math.round(s / 60)} min`);

function resumenHTML() {
  const r = S.regionInfo;
  const filas = [];
  const zona = zonaActual();
  if (!zona) return `<dt>${t("res.area")}</dt><dd>${t("zona.sin_elegir")}</dd>`;
  const e = estimacion();
  if (r) {
    filas.push([t("res.pais"), t(`region.${S.region === "auto" ? r.codigo : S.region}`)]);
    filas.push([t("res.orto"), S.usar.orto ? ORTO[r.orto] || r.orto : ORTO[""]]);
    filas.push([t("res.relieve"), S.dem ? t("res.local") : RELIEVE[r.relieve] || r.relieve]);
  }
  if (S.modo === "libre") filas.push([t("res.area"), `${fmt(S.ancho)} × ${fmt(S.alto)} m · ${fmt(kmZona(), 2)} km²`]);
  if (esVivo()) filas.push([t("res.modo"), t("res.modo_vivo")]);
  else filas.push([t("res.ruta"), S.ruta.length > 1 ? t("res.linea_recta", { km: fmt(largoRuta() / 1000, 1) }) : t("res.sin_puntos")]);
  filas.push([t("res.tiempo"), `${tiempoTexto(e.seg)} ${t("res.aprox")}`]);
  filas.push([t("res.tamano"), `~${fmt(Math.max(5, e.mb))} MB`]);
  return filas.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("");
}

// ------------------------------------------------------------------ sincronizar interfaz
function sync() {
  for (const el of $$("[data-campo]")) {
    const k = el.dataset.campo;
    if (el.type === "checkbox") el.checked = !!S[k];
    else if (document.activeElement !== el) el.value = S[k] ?? "";
    if (k === "nombre") el.placeholder = S.nombreAuto || "mi_pista";
  }
  for (const el of $$("[data-usar]")) el.checked = !!S.usar[el.dataset.usar];
  for (const g of $$("[data-grupo]")) {
    for (const b of $$("button[data-valor]", g)) b.setAttribute("aria-pressed", String(S[g.dataset.grupo] === b.dataset.valor));
  }
  for (const el of $$("[data-solo]")) el.hidden = el.dataset.solo === "libre" ? S.modo !== "libre" : S.modo === "libre";
  const nombre = nombreFinal();
  const existe = S.pistas.some((p) => p.nombre === nombre);
  const textos = {
    ancho: `${fmt(S.ancho)} m`,
    alto: `${fmt(S.alto)} m`,
    "zona-area": S.centro ? textoZona() : "",
    vegetacion: `${Math.round(S.vegetacion * 100)} %`,
    ruta: S.ruta.length ? (S.ruta.length > 1 ? `${t("ruta.puntos", { n: S.ruta.length })} · ${fmt(largoRuta() / 1000, 1)} km` : t("ruta.punto")) : "",
    "ayuda-ruta": t(S.modo === "circuito" ? "ruta.ayuda_circuito" : "ruta.ayuda_tramo"),
    lugar: S.lugarElegido ? S.lugar : "",
    dem: S.dem ? S.dem.split(/[\\/]/).pop() : "",
    "nombre-aviso": existe ? t("nombre.reemplaza", { n: nombre }) : slug(S.nombre) && slug(S.nombre) !== S.nombre ? t("nombre.como", { n: nombre }) : "",
  };
  for (const el of $$("[data-mostrar]")) {
    el.textContent = textos[el.dataset.mostrar] ?? "";
    if (el.dataset.mostrar === "nombre-aviso") el.classList.toggle("aviso-texto", existe);
  }
  for (const el of $$("[data-mostrar-si]")) el.hidden = !S[el.dataset.mostrarSi];
  for (const el of $$(".zona-area")) el.classList.toggle("vivo", esVivo());
  $("#b-generar").textContent = t(esVivo() ? "accion.vivo" : "accion.generar");
  aplicarBloqueo();
  const html = resumenHTML();
  $$("[data-resumen]").forEach((el) => { el.innerHTML = html; });
  syncVista();
  syncVista3D();
  syncAsistente();
}

function syncVista() {
  const d = $("#vista-datos");
  const o = S.osm;
  d.classList.toggle("actualizando", !!(o && o.cargando));
  if (!zonaActual()) d.textContent = t(S.modo === "libre" ? "osm.elige" : "osm.salida");
  else if (!o) d.textContent = "";
  else if (o.cargando && o.km === undefined) d.textContent = t("osm.cargando");
  else if (o.error) d.textContent = o.error;
  else {
    d.innerHTML = `<div class="cifras">
      <div class="cifra"><b>${fmt(o.km, 1)}</b><span>${t("osm.km")}</span></div>
      <div class="cifra"><b>${fmt(o.cruces)}</b><span>${t("osm.cruces")}</span></div>
      <div class="cifra"><b>${fmt(o.edificios)}</b><span>${t("osm.edificios")}</span></div></div>`;
  }
}

function syncAsistente() {
  if (!S.asistente) return;
  $$("[data-paso]").forEach((s) => { s.hidden = Number(s.dataset.paso) !== S.paso; });
  $$("[data-paso-ind]").forEach((li) => {
    const n = Number(li.dataset.pasoInd);
    li.className = n === S.paso ? "actual" : n < S.paso ? "hecho" : "";
  });
  const atras = $('[data-accion="paso-atras"]');
  atras.style.visibility = S.paso === 1 ? "hidden" : "visible";
  $('[data-accion="paso-siguiente"]').textContent = t(S.paso === 4 ? (esVivo() ? "asis.vivo" : "asis.generar") : "asis.siguiente");
}

// ------------------------------------------------------------------ asistente
function abrirAsistente() {
  S.asistente = true;
  S.paso = 1;
  $("#panel-asistente").hidden = false;
  $("#panel-experto").hidden = true;
  $("[data-error-paso]").hidden = true;
  sync();
  setTimeout(() => $('#panel-asistente [data-buscador] input')?.focus(), 50);
}
function cerrarAsistente() {
  S.asistente = false;
  $("#panel-asistente").hidden = true;
  $("#panel-experto").hidden = false;
  if (!S.ajustes.asistente_visto) { S.ajustes.asistente_visto = true; api("guardar_ajustes", { asistente_visto: true }); }
  sync();
}
function errorPaso(t) {
  const el = $("[data-error-paso]");
  el.textContent = t;
  el.hidden = !t;
}
function validarZona() {
  if (S.modo === "libre" && !S.centro) return t("err.zona");
  if (S.modo === "tramo" && S.ruta.length < 2) return t("err.tramo");
  if (S.modo === "circuito" && S.ruta.length < 3) return t("err.circuito");
  // una zona grande no es error: pasa al modo en vivo; una ruta enorme sí
  if (S.modo !== "libre" && areaKm2(zonaActual()) > 12) return t("err.grande");
  return "";
}
// asistente: al elegir el sitio (paso 1) o el tipo (paso 2) se pasa solo al siguiente
let tAvance = null;
function avanzarSolo(desde) {
  if (!S.asistente || S.paso !== desde) return;
  clearTimeout(tAvance);
  tAvance = setTimeout(() => {
    if (!S.asistente || S.paso !== desde) return;
    errorPaso("");
    S.paso = desde + 1;
    if (S.paso === 3 && S.modo === "libre" && S.centro) map.flyToBounds(caja(), { padding: [80, 80], duration: 0.6 });
    sync();
  }, desde === 1 ? 900 : 350);
}
function pasoSiguiente() {
  if (S.paso === 1 && !S.lugarElegido) return errorPaso(t("err.lugar"));
  if (S.paso === 3) { const e = validarZona(); if (e) return errorPaso(e); }
  errorPaso("");
  if (S.paso === 4) { generar().then((ok) => { if (ok) cerrarAsistente(); }); return; }
  clearTimeout(tAvance);
  S.paso++;
  if (S.paso === 3 && S.modo === "libre" && S.centro) map.flyToBounds(caja(), { padding: [80, 80], duration: 0.6 });
  sync();
}

// ------------------------------------------------------------------ búsqueda
function prepararBuscador(raiz) {
  const input = $("input", raiz), lista = $(".resultados", raiz);
  let t = null, res = [], sel = -1, pedido = 0;
  const pintar = () => {
    lista.innerHTML = "";
    if (!res.length) { lista.hidden = true; return; }
    res.forEach((r, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", String(i === sel));
      if (r.vacio) { li.className = "nada"; li.textContent = r.nombre; }
      else {
        li.innerHTML = `${escapar(r.nombre)}<small>${escapar(r.detalle || "")}</small>`;
        li.addEventListener("mousedown", (e) => { e.preventDefault(); elegir(i); });
      }
      lista.append(li);
    });
    lista.hidden = false;
  };
  const elegir = (i) => {
    const r = res[i];
    if (!r || r.vacio) return;
    input.value = r.nombre;
    res = []; pintar();
    input.blur();
    errorPaso("");
    irA(r.lat, r.lon, r.nombre, r.caja);
  };
  const buscar = async () => {
    const q = input.value.trim();
    if (q.length < 2) { res = []; pintar(); return; }
    const id = ++pedido;
    const r = await api("buscar", q);
    if (id !== pedido) return;
    if (r && r.error) res = [{ vacio: true, nombre: r.error }];
    else res = r && r.length ? r : [{ vacio: true, nombre: t("buscar.nada") }];
    sel = res[0]?.vacio ? -1 : 0;
    pintar();
  };
  input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(buscar, 450); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!res.length) return;
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + res.length) % res.length;
      pintar();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (res.length && sel >= 0) elegir(sel);
      else { clearTimeout(t); buscar(); }
    } else if (e.key === "Escape") { res = []; pintar(); }
  });
  input.addEventListener("blur", () => setTimeout(() => { res = []; pintar(); }, 150));
}
const escapar = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ------------------------------------------------------------------ generación
function opciones() {
  return {
    modo: S.modo, centro: S.centro, ancho: S.ancho, alto: S.alto, ruta: S.ruta, calidad: S.calidad, nombre: nombreFinal(),
    vivo: esVivo(),
    titulo: S.lugar && !S.lugarEsPunto ? S.lugar : "", ac: S.ac,
    temporada: S.temporada, region: S.region, vegetacion: S.vegetacion,
    sin: FUENTES.filter((k) => !S.usar[k]), dem: S.dem,
  };
}

// ------------------------------------------------------------------ generar (pista completa)
async function generar() {
  if (S.trabajo) { aviso(t("err.ocupado")); return false; }
  const e = validarZona();
  if (e) { aviso(e, { mal: true }); return false; }
  if (esVivo()) return iniciarVivo();
  clearTimeout(tBorrador);
  S.bor = null;                       // Python cancela el borrador que estuviera en marcha
  // el trabajo se marca antes de la llamada: los primeros eventos pueden llegar antes que la respuesta
  S.trabajo = nombreFinal();
  S.trabajoId = null;
  S.registro = [];
  S.t0 = Date.now();
  $("#resultado").hidden = true;
  mostrarProgreso(`«${S.trabajo}»: ${t("prog.preparando")}`, 2);
  syncVista3D();
  const r = await api("generar", opciones());
  if (!r || r.error) {
    ocultarProgreso();
    aviso(r?.error || t("err.empezar"), { mal: true, ms: 8000 });
    return false;
  }
  S.trabajo = r.nombre;
  S.trabajoId = r.id;
  S.registro.unshift(`> ${r.comando}`);
  return true;
}

function mostrarProgreso(titulo, pct) {
  aplicarBloqueo();
  $("#acc-listo").hidden = true;
  $("#acc-progreso").hidden = false;
  $("#prog-titulo").textContent = titulo;
  $("#prog-barra").style.width = `${pct}%`;
}
function ocultarProgreso() {
  $("#acc-listo").hidden = false;
  $("#acc-progreso").hidden = true;
  S.trabajo = null;
  S.trabajoId = null;
  aplicarBloqueo();
  programarBorrador();
}
setInterval(() => { if (S.trabajo) $("#prog-tiempo").textContent = mmss((Date.now() - S.t0) / 1000); }, 500);

function manejarEvento(ev) {
  if (ev.tipo === "borrador") return eventoBorrador(ev);
  if (ev.tipo === "vivo") return eventoVivo(ev);
  if (!S.trabajo) return;
  if (S.trabajoId == null) {
    if (ev.estado !== "inicio") return;
    S.trabajoId = ev.id;
  }
  if (ev.id !== S.trabajoId) return;       // de un trabajo anterior
  if (ev.linea) {
    S.registro.push(ev.linea);
    if (!$("#modal-registro").hidden) pintarRegistro();
  }
  const pre = `«${S.trabajo}»`;
  if (ev.estado === "linea" || ev.estado === "inicio") {
    const p = ev.paso || 0;
    const pct = p ? ((p - 0.5) / ev.pasos) * 100 : 3;
    mostrarProgreso(p ? `${pre}: ${p}/${ev.pasos} ${t(`paso.${p}`)}` : `${pre}: ${t(ev.precarga ? "prog.precarga" : "prog.preparando")}`, pct);
  } else if (ev.estado === "listo") {
    $("#prog-barra").style.width = "100%";
    ocultarProgreso();
    mostrarResultado(ev.pista);
    refrescarPistas();
    if (S.jugar && ev.pista && ev.pista.ac) jugarAC(ev.pista.nombre, $("#res-mensaje"));
  } else if (ev.estado === "error") {
    ocultarProgreso();
    aviso(ev.error || t("err.fallo"), { mal: true, ms: 0, accion: { texto: t("accion.ver_registro"), fn: () => verRegistro() } });
  } else if (ev.estado === "cancelado") {
    ocultarProgreso();
    aviso(t("prog.cancelado"));
  }
}

// ------------------------------------------------------------------ borrador 3D automático
// Al dejar quieta la zona (1,5 s, y lo que falte de la descarga de OSM) se rehace el borrador.
// Un borrador nuevo sustituye al que estuviera en marcha; «Generar pista» lo cancela.
const claveZona = () => JSON.stringify({ modo: S.modo, centro: S.centro, ancho: S.ancho, alto: S.alto, ruta: S.ruta });
let tBorrador = null;
const borradorPosible = () => !validarZona() && areaKm2(zonaActual()) <= 3;

function programarBorrador() {
  clearTimeout(tBorrador);
  syncVista3D();
  if (!S.autoBorrador || S.trabajo || !borradorPosible()) return;
  const k = claveZona();
  if (S.bor ? S.bor.clave === k : S.borClave === k) return;   // ya está hecho o haciéndose
  tBorrador = setTimeout(() => lanzarBorrador(true), 1500);
}

async function lanzarBorrador(auto) {
  if (S.trabajo) { if (!auto) aviso(t("err.ocupado")); return; }
  const e = validarZona();
  if (e) { if (!auto) aviso(e, { mal: true }); return; }
  const bor = { clave: claveZona(), id: null, paso: 0, precarga: false, registro: [] };
  S.bor = bor;
  S.borError = "";
  syncVista3D();
  const r = await api("borrador", opciones());
  if (S.bor !== bor) return;               // ya lo sustituyó otro
  if (!r || r.error) {
    S.bor = null;
    if (!r?.ocupado) S.borError = r?.error || t("err.empezar");
    syncVista3D();
    return;
  }
  bor.id = r.id;
  bor.registro.unshift(`> ${r.comando}`);
}

function eventoBorrador(ev) {
  const bor = S.bor;
  if (!bor) return;
  if (bor.id == null) {
    if (ev.estado !== "inicio") return;
    bor.id = ev.id;
  }
  if (ev.id !== bor.id) return;
  if (ev.linea) bor.registro.push(ev.linea);
  if (ev.estado === "linea" || ev.estado === "inicio") {
    bor.paso = ev.paso || 0;
    bor.precarga = !!ev.precarga;
    syncVista3D();
  } else if (ev.estado === "listo") {
    S.bor = null;
    S.borClave = bor.clave;
    S.borRegistro = bor.registro;
    api("guardar_ajustes", { borrador_zona: bor.clave });
    mostrarBorrador(ev.glb);
    programarBorrador();                   // por si la zona cambió mientras se hacía
  } else if (ev.estado === "error") {
    S.bor = null;
    S.borError = ev.error || t("err.fallo");
    S.borRegistro = bor.registro;
    syncVista3D();
  } else if (ev.estado === "cancelado") {
    S.bor = null;
    syncVista3D();
  }
}

function syncVista3D() {
  const hay = !!S.borradorGlb, alDia = S.borClave === claveZona();
  let texto = "", pct = null, boton = false, mal = false;
  if (S.bor) {
    const p = S.bor.paso;
    texto = p ? `${t("vista.actualizando")} · ${p}/7 ${t(`paso.${p}`)}` : t(S.bor.precarga ? "vista.descargando" : "vista.actualizando");
    pct = p ? ((p - 0.5) / 7) * 100 : 3;
  } else if (S.borError) {
    texto = S.borError;
    mal = true;
    boton = true;
  } else if (hay && !alDia) {
    texto = t("vista.desfasada");
    boton = !S.autoBorrador || !borradorPosible() || !!S.trabajo;
  }
  const est = $("#vista-estado");
  est.hidden = !texto || (!hay && !S.bor && !mal);
  est.classList.toggle("mal", mal);
  $("#ve-texto").textContent = texto;
  $("#ve-prog").hidden = pct === null;
  $("#ve-barra").style.width = `${pct || 0}%`;
  $("#ve-boton").hidden = !boton || !!S.trabajo || !!validarZona();
  $("#ve-registro").hidden = !mal;
  $("#vista-iframe").classList.toggle("desfasada", hay && (!alDia || !!S.bor));
  // sin ningún borrador todavía: explicación y, si hace falta, el botón manual
  const vacia = $("#vista-vacia");
  vacia.hidden = hay || !!S.bor || mal;
  const sinZona = !!validarZona(), grande = !sinZona && !borradorPosible();
  $("#vista-vacia-texto").textContent = sinZona ? t("vista.elige") : grande ? t("vista.grande")
    : S.autoBorrador ? t("vista.espera") : t("vista.vacia");
  $("#b-borrador").hidden = sinZona || (S.autoBorrador && !grande) || !!S.trabajo;
}

function conectarEventos() {
  const es = new EventSource(`/api/eventos?token=${encodeURIComponent(TOKEN)}`);
  es.onmessage = (m) => { try { manejarEvento(JSON.parse(m.data)); } catch (e) { console.error(e); } };
}

function pintarRegistro(lineas = S.registro) {
  const pre = $("#registro");
  pre.textContent = lineas.join("\n") || t("registro.vacio");
  pre.scrollTop = pre.scrollHeight;
}
function verRegistro(lineas) { $("#modal-registro").hidden = false; pintarRegistro(lineas); }

function urlVisor(glb, extra = "") {
  return `/viewer/index.html?glb=${encodeURIComponent(glb)}&lang=${IDIOMA}${extra}`;
}
function mostrarBorrador(glb) {
  S.borradorGlb = glb;
  const f = $("#vista-iframe");
  f.src = urlVisor(glb, "&vista=libre&incrustado=1");
  f.hidden = false;
  $("#b-ampliar").hidden = false;
  syncVista3D();
}
function abrirVisor(glb) {
  const f = $("#visor-iframe");
  f.src = urlVisor(glb);
  $("#visor-completo").hidden = false;
  f.onload = () => f.contentWindow?.focus();
}

function mostrarResultado(p) {
  if (!p) { aviso(t("err.sin_archivo"), { mal: true }); return; }
  S.ultimaPista = p;
  $("#res-img").src = p.cenital || "";
  $("#res-img").hidden = !p.cenital;
  $("#res-titulo").textContent = p.nombre;
  const datos = [p.area_km2 ? t("dato.km2", { v: fmt(p.area_km2, 2) }) : "", p.km ? t("dato.km_calles", { v: fmt(p.km, 1) }) : "",
    t("dato.mb", { v: fmt(p.mb, 1) }), p.segundos ? t("dato.s", { v: Math.round(p.segundos) }) : ""];
  $("#res-datos").textContent = datos.filter(Boolean).join(" · ");
  $("#res-jugar").hidden = !p.ac || !S.acRuta;
  const b = $("#res-ac");
  b.hidden = !p.ac;
  b.textContent = t(p.ac_estado === "enlazada" ? "res.ya_ac" : "res.instalar");
  $("#res-mensaje").textContent = "";
  $("#resultado").hidden = false;
}

async function jugarAC(nombre, salidaMsg) {
  const r = await api("jugar_ac", nombre);
  const txt = r?.error || r?.mensaje || "";
  if (salidaMsg) { salidaMsg.textContent = txt; salidaMsg.className = `ayuda ${r?.error ? "res-mal" : "res-ok"}`; }
  else aviso(txt, { mal: !!r?.error, ms: 8000 });
  refrescarPistas();
}

// ------------------------------------------------------------------ modo en vivo
// Zona de más de 5 km²: Python arranca `openlaps.vivo` con la zona (a escala, 5 km²) para prepararla entera, crea e
// instala la pista semilla de AC y sirve los pedazos. Aquí se enseña el progreso (se le pregunta directamente al
// servicio) y se puede conducir en el visor o abrir AC.
let tVivo = null;
async function iniciarVivo() {
  if (S.vivo && S.vivo.activo) { aviso(t("err.ocupado")); return false; }
  clearTimeout(tBorrador);
  S.bor = null;
  const r = await api("vivo_iniciar", opciones());
  if (!r || r.error) { aviso(r?.error || t("err.empezar"), { mal: true, ms: 8000 }); return false; }
  S.vivo = { activo: true, puerto: r.puerto, url: r.url, nombre: r.nombre, pistaAc: r.pista_ac, zona: r.zona,
    ac: r.ac, csp: r.csp, semilla: r.pista_ac ? "haciendo" : null, estado: null, registro: [`> openlaps.vivo :${r.puerto}`] };
  if (r.aviso) aviso(r.aviso, { ms: 10000 });
  $("#resultado").hidden = true;
  $("#vivo-panel").hidden = false;
  pintarVivo();
  clearInterval(tVivo);
  tVivo = setInterval(sondearVivo, 2000);
  return true;
}
async function sondearVivo() {
  const v = S.vivo;
  if (!v || !v.activo) return;
  try {
    v.estado = await (await fetch(`http://127.0.0.1:${v.puerto}/vivo/estado`)).json();
  } catch { v.estado = null; }
  pintarVivo();
}
function eventoVivo(ev) {
  const v = S.vivo;
  if (!v) return;
  if (ev.linea) v.registro = [...v.registro, ev.linea].slice(-500);
  if (ev.estado === "semilla") v.semilla = "lista";
  else if (ev.estado === "semilla_error") v.semilla = "error";
  else if (ev.estado === "fin") {
    v.activo = false;
    clearInterval(tVivo);
    if (!v.detenido) aviso(t("vivo.fin"), { mal: ev.codigo !== 0, accion: { texto: t("accion.ver_registro"), fn: () => verRegistro(v.registro) } });
  }
  pintarVivo();
}
function pintarVivo() {
  const v = S.vivo;
  // el panel del modo en vivo ocupa el sitio de la vista previa 3D
  $("#vista").hidden = !!v;
  aplicarBloqueo();
  if (!v) { $("#vivo-panel").hidden = true; return; }
  $("#vivo-titulo").textContent = v.nombre;
  $("#vivo-texto").textContent = v.activo ? t("vivo.texto", { zona: `${fmt(v.zona[0])} × ${fmt(v.zona[1])} m` }) : t("vivo.detenido");
  const e = v.estado;
  const pct = e ? Math.round(e.zona_lista * 100) : 0;
  $("#vivo-barra").style.width = `${v.activo ? Math.max(2, pct) : 0}%`;
  $("#vivo-zona").textContent = !v.activo ? "" : !e ? t("vivo.arrancando")
    : e.t_medio ? t("vivo.zona", { pct, n: e.listos.length, s: fmt(e.t_medio, 0) }) : t("vivo.zona_espera");
  const p = v.pistaAc;
  $("#vivo-ac").textContent = !v.ac ? t("vivo.ac_no") : !p ? "" : v.semilla === "lista" ? (v.csp ? t("vivo.ac_lista", { p }) : t("vivo.ac_csp"))
    : v.semilla === "error" ? t("vivo.ac_error") : t("vivo.ac_haciendo", { p });
  $("#vivo-jugar").hidden = !(v.activo && v.semilla === "lista" && v.ac);
  $('[data-accion="vivo-conducir"]').hidden = !v.activo || !e;
}

async function instalarAC(nombre, salidaMsg) {
  const r = await api("instalar_ac", nombre);
  if (r.error) {
    if (salidaMsg) { salidaMsg.textContent = r.error; salidaMsg.className = "ayuda res-mal"; }
    else aviso(r.error, { mal: true, ms: 8000 });
    return;
  }
  if (salidaMsg) { salidaMsg.textContent = r.mensaje; salidaMsg.className = "ayuda res-ok"; }
  else aviso(r.mensaje, { ms: 8000 });
  refrescarPistas();
}

// ------------------------------------------------------------------ mis pistas, ajustes
async function refrescarPistas() {
  const r = await api("pistas");
  if (Array.isArray(r)) S.pistas = r;
  pintarPistas();
  sync();
}
function pintarPistas() {
  const n = $("#n-pistas");
  n.textContent = S.pistas.length;
  n.hidden = !S.pistas.length;
  const ul = $("#lista-pistas");
  ul.innerHTML = "";
  if (!S.pistas.length) {
    ul.innerHTML = `<li class="vacia">${t("pistas.vacia")}</li>`;
    return;
  }
  for (const p of S.pistas) {
    const li = document.createElement("li");
    const meta = [p.fecha, t("dato.mb", { v: fmt(p.mb, 1) }), t(`modo_nombre.${p.modo}`)].join(" · ");
    li.innerHTML = `${p.cenital ? `<img src="${p.cenital}" alt="" loading="lazy">` : `<div class="sin-img"></div>`}
      <div><b title="${escapar(p.nombre)}">${escapar(p.nombre)}</b><small>${escapar(meta)}</small>
      <div class="fila-botones">
        <button class="boton chico" data-p="manejar">${t("pistas.conducir")}</button>
        ${p.centro ? `<button class="boton chico" data-p="mapa">${t("pistas.mapa")}</button>` : ""}
        <button class="boton chico" data-p="carpeta">${t("pistas.carpeta")}</button>
        ${p.ac ? `<button class="boton chico" data-p="ac">${t(p.ac_estado === "enlazada" ? "pistas.en_ac" : "pistas.instalar")}</button>` : ""}
      </div></div>`;
    li.addEventListener("click", (e) => {
      const a = e.target.closest("[data-p]")?.dataset.p;
      if (a === "manejar") abrirVisor(p.glb);
      else if (a === "carpeta") api("abrir_carpeta", p.nombre);
      else if (a === "ac") instalarAC(p.nombre);
      else if (a === "mapa") { cerrarCajones(); map.flyTo(p.centro, 15, { duration: 0.8 }); }
    });
    ul.append(li);
  }
}
function abrirCajon(id) {
  const c = $(id);
  const abrir = c.hidden;
  cerrarCajones();
  c.hidden = !abrir;
}
function cerrarCajones() { $$(".cajon").forEach((c) => { c.hidden = true; }); }
function pintarAjustes() {
  $("#aj-salida").textContent = S.salida;
  $("#aj-ac").textContent = S.acRuta || t("aj.ac_no");
  $("#aj-idioma").value = IDIOMA;
  $("#aj-version").textContent = S.version;
}
async function recargarEstado() {
  const r = await api("iniciar");
  if (!r || r.error) return null;
  S.ajustes = r.ajustes;
  S.acRuta = r.ac;
  S.salida = r.salida;
  S.version = r.version;
  S.pistas = r.pistas || [];
  pintarAjustes();
  pintarPistas();
  return r;
}

// ------------------------------------------------------------------ acciones
const ACCIONES = {
  generar: () => generar(),
  borrador: () => lanzarBorrador(false),
  "registro-borrador": () => verRegistro(S.borRegistro || []),
  cancelar: () => api("cancelar"),
  "ver-registro": () => verRegistro(),
  "cerrar-registro": () => { $("#modal-registro").hidden = true; },
  "deshacer-punto": () => { if (avisarBloqueo()) return; S.ruta.pop(); dibujarRuta(); cambioZona(); },
  "borrar-ruta": () => { if (avisarBloqueo()) return; S.ruta = []; dibujarRuta(); cambioZona(); },
  "paso-siguiente": pasoSiguiente,
  "paso-atras": () => { errorPaso(""); S.paso = Math.max(1, S.paso - 1); sync(); },
  "cerrar-asistente": cerrarAsistente,
  "abrir-asistente": () => { cerrarCajones(); abrirAsistente(); },
  "ampliar-borrador": () => S.borradorGlb && abrirVisor(S.borradorGlb),
  "cerrar-visor": () => { $("#visor-completo").hidden = true; $("#visor-iframe").src = "about:blank"; },
  "cerrar-resultado": () => { $("#resultado").hidden = true; },
  "manejar-resultado": () => S.ultimaPista && abrirVisor(S.ultimaPista.glb),
  "instalar-resultado": () => S.ultimaPista && instalarAC(S.ultimaPista.nombre, $("#res-mensaje")),
  "jugar-resultado": () => S.ultimaPista && jugarAC(S.ultimaPista.nombre, $("#res-mensaje")),
  "vivo-conducir": () => {
    if (!S.vivo) return;
    const f = $("#visor-iframe");
    f.src = `${S.vivo.url}&lang=${IDIOMA}`;
    $("#visor-completo").hidden = false;
    f.onload = () => f.contentWindow?.focus();
  },
  "vivo-jugar": async () => {
    const r = await api("vivo_jugar");
    const m = $("#vivo-mensaje");
    m.textContent = r?.error || r?.mensaje || "";
    m.className = `ayuda ${r?.error ? "res-mal" : "res-ok"}`;
  },
  "vivo-detener": async () => {
    if (S.vivo) S.vivo.detenido = true;
    await api("vivo_detener");
    clearInterval(tVivo);
    S.vivo = null;
    pintarVivo();
    aviso(t("vivo.detenido"));
  },
  "vivo-registro": () => S.vivo && verRegistro(S.vivo.registro),
  "carpeta-resultado": () => S.ultimaPista && api("abrir_carpeta", S.ultimaPista.nombre),
  "cerrar-cajon": cerrarCajones,
  "abrir-salida": () => api("abrir_carpeta"),
  "cambiar-salida": async () => {
    const r = await api("elegir_carpeta", "salida");
    if (r?.error) return aviso(r.error, { mal: true });
    if (r?.ok) { await recargarEstado(); sync(); aviso(t("ok.salida")); }
  },
  "cambiar-ac": async () => {
    const r = await api("elegir_carpeta", "ac");
    if (r?.error) return aviso(r.error, { mal: true });
    if (r?.ok) { await recargarEstado(); aviso(t("ok.ac")); }
  },
  "elegir-dem": async () => {
    const r = await api("elegir_dem");
    if (r?.error) return aviso(r.error, { mal: true });
    if (typeof r === "string") { S.dem = r; sync(); }
  },
  "quitar-dem": () => { S.dem = null; sync(); },
};

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (a && /^https?:/.test(a.href) && !a.href.startsWith(location.origin)) {
    e.preventDefault();
    api("abrir_enlace", a.href);
    return;
  }
  const enl = e.target.closest("[data-enlace]");
  if (enl) return api("abrir_enlace", enl.dataset.enlace);
  const acc = e.target.closest("[data-accion]");
  if (acc && ACCIONES[acc.dataset.accion]) return ACCIONES[acc.dataset.accion]();
  const ir = e.target.closest("[data-ir]");
  if (ir) {
    const [lat, lon, ...n] = ir.dataset.ir.split(",");
    errorPaso("");
    return irA(Number(lat), Number(lon), n.join(","));
  }
  const capa = e.target.closest("[data-capa]");
  if (capa) { ponerCapa(capa.dataset.capa); api("guardar_ajustes", { capa: capa.dataset.capa }); return; }
  const op = e.target.closest("[data-grupo] button[data-valor]");
  if (op) {
    const g = op.closest("[data-grupo]").dataset.grupo;
    if (g === "modo") { cambiarModo(op.dataset.valor); avanzarSolo(2); }
    else {
      S[g] = op.dataset.valor;
      if (g === "calidad") api("guardar_ajustes", { calidad: S.calidad });
      sync();
    }
  }
});

document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset.campo) {
    const k = el.dataset.campo;
    S[k] = el.type === "checkbox" ? el.checked : el.type === "range" ? Number(el.value) : el.value;
    if (k === "ancho" || k === "alto") {
      limitarZona(k);
      dibujarZona();
      cambioZona();
      // que se vea el rectángulo entero mientras se cambia su tamaño
      if (S.centro && !map.getBounds().contains(L.latLngBounds(caja()))) map.fitBounds(caja(), { padding: [60, 60], animate: false });
    }
    if (k === "ac") api("guardar_ajustes", { exportar_ac: S.ac });
    if (k === "jugar") api("guardar_ajustes", { jugar_al_terminar: S.jugar });
    if (k === "vivoModo") dibujarZona();
    if (k === "region") pedirRegion();
    sync();
  } else if (el.dataset.usar) {
    S.usar[el.dataset.usar] = el.checked;
    sync();
  }
});
$("#b-osm").addEventListener("click", () => {
  S.verOsm = !S.verOsm;
  $("#b-osm").setAttribute("aria-pressed", String(S.verOsm));
  api("guardar_ajustes", { ver_osm: S.verOsm });
  cargarOSM();
});
$("#auto-3d").addEventListener("change", (e) => {
  S.autoBorrador = e.target.checked;
  api("guardar_ajustes", { borrador_auto: S.autoBorrador });
  if (!S.autoBorrador) { clearTimeout(tBorrador); if (S.bor) { S.bor = null; api("cancelar", "borrador"); } }
  programarBorrador();
});
$("#aj-idioma").addEventListener("change", (e) => cambiarIdioma(e.target.value, true));

function cambiarIdioma(idioma, guardar) {
  aplicarIdioma(idioma);
  if (guardar) api("guardar_ajustes", { idioma: IDIOMA });
  if (Z.centro) { Z.centro.options.title = t("zona.mover"); Z.esquina.options.title = t("zona.tamano"); }
  pintarAjustes();
  pintarPistas();
  pintarMarco();
  if (S.ultimaPista && !$("#resultado").hidden) mostrarResultado(S.ultimaPista);
  sync();
}

$("#b-guiado").addEventListener("click", () => (S.asistente ? cerrarAsistente() : abrirAsistente()));
$("#b-pistas").addEventListener("click", () => { abrirCajon("#cajon-pistas"); refrescarPistas(); });
$("#b-ajustes").addEventListener("click", () => abrirCajon("#cajon-ajustes"));

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
    e.preventDefault();
    (S.asistente && S.paso === 1 ? $("#panel-asistente [data-buscador] input") : $(".top [data-buscador] input")).focus();
  } else if (e.key === "Escape") {
    if (!$("#visor-completo").hidden) ACCIONES["cerrar-visor"]();
    else if (!$("#modal-registro").hidden) ACCIONES["cerrar-registro"]();
    else cerrarCajones();
  }
});

// ------------------------------------------------------------------ barra de título propia
// En la ventana de OpenLaps (sin marco) la barra superior hace de barra de título: al pulsarla o
// pulsar un borde, Python le cede el ratón a Windows, que mueve o redimensiona como siempre (con Snap).
let marco = null;
async function iniciarMarco() {
  const e = await api("ventana_estado");
  if (!e || !e.marco) return;
  marco = e;
  document.body.classList.add("con-marco");
  $(".ventana-ctl").hidden = false;
  $(".bordes").hidden = false;
  pintarMarco();
  let tr = null;
  window.addEventListener("resize", () => {
    clearTimeout(tr);
    tr = setTimeout(async () => { const r = await api("ventana_estado"); if (r && r.marco) { marco = r; pintarMarco(); } }, 120);
  });
}
function pintarMarco() {
  if (!marco) return;
  document.body.classList.toggle("maximizada", !!marco.maximizada);
  const b = $('[data-ventana="maximizar"]');
  const k = marco.maximizada ? "ventana.restaurar" : "ventana.maximizar";
  b.title = t(k);
  b.setAttribute("aria-label", t(k));
}
async function accionVentana(que) {
  const r = await api("ventana_accion", que);
  if (r && r.marco) { marco = r; pintarMarco(); }
}
let ultimoClic = 0;
$(".top").addEventListener("mousedown", (e) => {
  if (!marco || e.button !== 0) return;
  if (e.target.closest("button, input, select, a, ul, [data-buscador], .ventana-ctl")) return;
  e.preventDefault();
  const ahora = Date.now();
  if (ahora - ultimoClic < 380) { ultimoClic = 0; accionVentana("maximizar"); return; }
  ultimoClic = ahora;
  accionVentana("arrastrar");
});
$(".ventana-ctl").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ventana]");
  if (b) accionVentana(b.dataset.ventana);
});
$(".bordes").addEventListener("mousedown", (e) => {
  const b = e.target.closest("[data-borde]");
  if (b && e.button === 0) { e.preventDefault(); accionVentana(b.dataset.borde); }
});

// ------------------------------------------------------------------ arranque
(async function arrancar() {
  aplicarIdioma(idiomaInicial(null));
  $$("[data-buscador]").forEach(prepararBuscador);
  iniciarMarco();
  conectarEventos();
  const r = await recargarEstado();
  const a = S.ajustes || {};
  cambiarIdioma(idiomaInicial(a.idioma), !!r && !a.idioma);
  ponerCapa(a.capa || "mapa");
  S.calidad = a.calidad || "normal";
  S.ac = a.exportar_ac !== false;
  S.jugar = !!a.jugar_al_terminar;
  S.maxKm2 = r?.max_km2 || 24;
  S.csp = !!r?.csp;
  S.autoBorrador = a.borrador_auto !== false;
  S.verOsm = a.ver_osm !== false;
  $("#auto-3d").checked = S.autoBorrador;
  $("#b-osm").setAttribute("aria-pressed", String(S.verOsm));
  const v = a.vista;
  if (v && Number.isFinite(v.lat)) {
    // se abre mirando la zona elegida (si la hay), no donde quedó el mapa
    map.setView(Array.isArray(v.centro) ? v.centro : [v.lat, v.lon], v.zoom || 14);
    if (Array.isArray(v.centro)) {
      S.centro = v.centro;
      S.ancho = v.ancho || v.tam || 800;
      S.alto = v.alto || v.tam || 800;
      S.lugar = v.lugar || "";
      S.lugarElegido = true;
      S.nombreAuto = v.nombreAuto || "";
    }
  }
  dibujarZona();
  // el último borrador es de esta misma zona: se enseña tal cual en vez de rehacerlo
  if (S.centro && a.borrador_zona && a.borrador_zona === claveZona()) {
    S.borClave = a.borrador_zona;
    mostrarBorrador(`/borrador/borrador.glb?t=${Date.now()}`);
  }
  sync();
  if (S.centro) cambioZona();
  // el modo en vivo sigue en marcha (la ventana se recargó): se recupera su panel y la zona queda bloqueada
  const ev = await api("vivo_estado");
  if (ev && ev.activo) {
    S.vivo = { activo: true, puerto: ev.puerto, url: ev.url, nombre: ev.nombre, pistaAc: ev.pista_ac, zona: ev.zona || [0, 0],
      ac: ev.ac, csp: ev.csp, semilla: ev.semilla, estado: null, registro: ev.registro || [] };
    if (ev.centro) { S.centro = ev.centro; S.ancho = ev.zona[0]; S.alto = ev.zona[1]; S.modo = "libre"; S.vivoModo = true; dibujarZona(); }
    $("#vivo-panel").hidden = false;
    pintarVivo();
    clearInterval(tVivo);
    tVivo = setInterval(sondearVivo, 2000);
    sync();
  }
  if (!r) aviso(t("err.arranque"), { mal: true, ms: 0 });
  else if (!a.asistente_visto) abrirAsistente();
})();
