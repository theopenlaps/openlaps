"""Tráfico para Assetto Corsa: carriles, cruces y reglas para la herramienta de tráfico de CSP.

Custom Shaders Patch trae una herramienta de tráfico (Traffic Planner, de x4fab, dominio público) y un modo de
juego "Traffic" que llenan la pista de coches sencillos (cientos o miles). Leen `data/traffic.json`:

- carriles: polilíneas 3D con sentido, con un "rol" (prioridad y límite de velocidad);
- cruces: polígonos. Un carril que entra al polígono y otro que sale de él quedan unidos; los giros, quién pasa
  primero y qué carril puede girar adónde los calcula CSP solo;
- zonas (no se usan).

Aquí solo hay que dar la geometría bien puesta, a partir de la red de calles ya construida:

- un carril por cada carril de OSM y sentido, a la altura de la calzada ya repasada;
- cada carril empieza y termina DENTRO de un cruce (o en el borde del área, donde aparecen y desaparecen
  los coches): CSP solo une carriles a través de cruces;
- donde se juntan solo dos calles con los mismos carriles, el carril sigue de largo, sin cruce;
- calles sin salida de doble sentido: un cruce pequeño al final para dar la vuelta;
- sin cambios de sentido en los cruces normales, preferencia para quien va dentro de una glorieta y menos
  prioridad para quien llega a un alto o a un ceda el paso;
- el polígono de cada cruce tiene un lado ("boca") por calle: CSP agrupa los carriles por el lado del polígono
  que cruzan, y de ahí saca quién puede girar desde qué carril, quién cede al girar a la izquierda y qué lados
  tienen verde. Un lado solo cuenta si por él entra y sale algún carril, así que a las calles de un solo
  sentido se les pone un carril "fantasma" de 5 m en contra, al que ninguna trayectoria puede ir;
- semáforos (de dos fases, por lados alternos) donde OSM los marca, y flechas de carril (`turn:lanes`).

`revisar` reproduce cómo CSP une carriles y cruces, para comprobar el resultado sin abrir el juego.
"""
import math

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import Polygon
from shapely.ops import unary_union

from . import roads

# vías por las que circula el tráfico (sin servicio, caminos ni peatonales: estacionamientos y accesos)
CLASES = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
          "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "road"}
PASO = 4.0           # separación de los puntos de un carril (m); CSP los vuelve a muestrear cada 3 m
DENTRO = 2.0         # cuánto se mete un carril en el cruce desde el corte de la calle (m)
VUELTA_MIN = 25.0    # largo mínimo de una calle sin salida para ponerle un retorno (m)
JUNTOS = 18.0        # dos cruces con menos calle libre que esto entre ellos se funden en uno (m)
CRUCE_Y = 5.0        # CSP: TrafficConfig.intersectionYThreshold (más el desnivel del propio polígono)
ETIQUETA = "traffic-planner-ready"   # el modo Traffic solo admite pistas con esta etiqueta
GLORIETA = ("roundabout", "circular")
MARGEN_BOCA = 0.5    # cuánto sobresale la boca de un cruce a cada lado de la calzada (m)
FANTASMA = "~"       # los carriles fantasma se llaman así (ver arriba): no llevan coches
FANT_FUERA = 3.5     # lo que asoma un carril fantasma fuera del cruce (m): CSP no pone coches a menos de 5 m
VERDE_S = (12.0, 16.0)   # duración de cada fase de un semáforo (s): calles normales, avenidas
ALTURA_LUCES = (3.3, 3.0, 2.7)   # altura de la luz roja, la ámbar y la verde sobre la calzada (m)
# límite en ciudad cuando OSM no trae maxspeed (el de la clase es el de carretera: 80 km/h en una avenida del centro)
URBANO_KMH = {"trunk": 70, "primary": 60, "secondary": 50, "tertiary": 50, "unclassified": 40, "road": 40,
              "residential": 40, "living_street": 20, "primary_link": 40, "secondary_link": 40, "tertiary_link": 40}


def _velocidad(e):
    v = float(e.speed)
    if e.urban and "maxspeed" not in e.tags and e.cls in URBANO_KMH:
        v = min(v, URBANO_KMH[e.cls])
    return int(round(v))


def _sentidos(e):
    """(carriles en el sentido del trazo, carriles en contra, ancho que ocupan, carriles totales)."""
    t = e.tags
    n = int(e.lanes)
    if e.oneway:
        f, b = (0, n) if t.get("oneway") == "-1" else (n, 0)
    else:
        f, b = roads._num(t.get("lanes:forward")), roads._num(t.get("lanes:backward"))
        if f is None and b is None:
            f = b = max(1, n // 2)
        elif f is None:
            f = n - b
        elif b is None:
            b = n - f
        f, b = int(max(1, f)), int(max(1, b))
    acot = 2 * e.shoulder if (not e.urban and "width" not in t) else 0.0
    W = float(np.clip(2 * e.hw - e.parking - acot, 2.4 * (f + b), 2 * e.hw))
    return f, b, W, max(n, f + b)


def _desplazamiento(e, k, de_ida):
    """Desplazamiento lateral (izquierda +) del carril k (0 = el de más adentro) de un sentido de la calle
    (de_ida = en el sentido del trazo). Se circula por la derecha."""
    f, b, W, n = _sentidos(e)
    lw = W / n
    if de_ida:
        return -W / 2 + (f - k - 0.5) * lw
    return W / 2 - (b - k - 0.5) * lw


class _Pieza:
    """Tramo de calle dentro del área: arista, abscisas [a, b] y nodo de cada extremo (None = borde)."""
    __slots__ = ("e", "a", "b", "na", "nb", "f", "r")

    def __init__(self, e, a, b, na, nb):
        self.e, self.a, self.b, self.na, self.nb = e, a, b, na, nb
        self.f, self.r = _sentidos(e)[:2]

    def nodo(self, lado):
        return self.na if lado == 0 else self.nb

    def s(self, lado):
        return self.a if lado == 0 else self.b

    def cuenta(self, al_reves):
        """(carriles en el sentido de recorrido, carriles en contra)."""
        return (self.r, self.f) if al_reves else (self.f, self.r)


class _Brazo:
    """Calle que llega a un cruce: tramo, lado, corte, boca (extremos A y B), sentido hacia fuera y carriles."""
    __slots__ = ("p", "lado", "s", "A", "B", "mid", "t", "fase", "lado_poli", "entradas", "salidas")

    def __init__(self, p, lado, s):
        e = p.e
        self.p, self.lado, self.s = p, lado, float(s)
        h = e.hw + MARGEN_BOCA
        self.A, self.B = e.at(s, h)[:2], e.at(s, -h)[:2]
        self.mid = (self.A + self.B) / 2
        t = e.frame(s) * (1.0 if lado == 0 else -1.0)           # alejándose del cruce
        self.t = t / max(float(np.linalg.norm(t)), 1e-9)
        self.fase, self.lado_poli = 0, 0
        self.entradas, self.salidas = [], []                    # carriles que entran al cruce / salen de él

    def llegan(self):
        """Carriles que entran al cruce por esta calle."""
        return self.p.f if self.lado == 1 else self.p.r


class _Cruce:
    def __init__(self, poly, tipo, nodos=()):
        self.poly = poly           # Polygon (planta)
        self.casco = poly          # envolvente convexa: lo que queda dentro no lleva carriles
        self.brazos = None         # [_Brazo] si el polígono tiene una boca por calle
        self.semaforo = None       # duración de la fase (s) si lleva semáforo
        self.tipo = tipo           # "cruce" | "union" (cambia el número de carriles) | "retorno" (sin salida)
        self.nodos = set(nodos)
        self.cortes = {}           # (id(pieza), lado) -> abscisa donde la calle entra al cruce
        self.entradas = []         # (carril, dirección 2D, cadena, extremo de la cadena, arista)
        self.salidas = []
        self.glorieta = False


def _poligono(pts):
    pts = np.asarray(pts, float)
    if len(pts) < 3:
        return None
    hull = roads.convex_hull_2d(pts)
    if len(hull) < 3:
        return None
    pg = Polygon(hull)
    return pg if pg.is_valid and pg.area > 1.0 else None


def _dentro_del_cruce(e, s, lado, poly, o):
    """Abscisa a la que el carril (desplazamiento o) ya está dentro del cruce, buscando desde el corte de la
    calle hacia el nodo. None si no entra."""
    sgn = -1.0 if lado == 0 else 1.0       # hacia el nodo
    fin = 0.0 if lado == 0 else e.length
    lo, hi = min(fin, s), max(fin, s)
    for d in (DENTRO, 1.0, 3.5, 5.0, 0.5, 7.0, 9.0):
        si = float(np.clip(s + sgn * d, lo, hi))
        if shapely.contains_xy(poly, *e.at(si, o)[:2]):
            return si
    if shapely.contains_xy(poly, *e.at(fin, o)[:2]):
        return fin
    return None


def _cruz(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _fases(brazos):
    """Reparte las calles de un cruce entre las dos fases del semáforo: la principal y las que siguen su eje
    (a menos de 45°) en la 0, las que la cruzan en la 1. Devuelve la fase que debe caer en los lados pares del
    polígono (así las avenidas paralelas de toda la zona tienen verde a la vez), o None si alguna fase se queda
    sin calles que entren (ahí un semáforo no hace nada)."""
    pr = max(brazos, key=lambda b: (b.p.e.prio, b.p.e.hw, b.llegan()))
    for b in brazos:
        b.fase = 0 if abs(float(b.t @ pr.t)) >= 0.7071 else 1
    if not all(any(b.fase == f and b.llegan() > 0 for b in brazos) for f in (0, 1)):
        return None
    return 0 if abs(pr.t[0]) >= abs(pr.t[1]) else 1


def _bocas_en_estrella(brazos):
    """Bocas en el corte de cada calle, perpendiculares a ella, ordenadas alrededor del centro: [(calles de la
    boca, extremo 1, extremo 2)] en el orden del polígono."""
    cen = np.mean([b.mid for b in brazos], axis=0)
    orden = sorted(brazos, key=lambda b: math.atan2(b.mid[1] - cen[1], b.mid[0] - cen[0]))
    return [([b], *((b.A, b.B) if _cruz(b.A - cen, b.B - cen) >= 0 else (b.B, b.A))) for b in orden]


def _sale_del_casco(e, p, lado, o, casco, s0):
    """Abscisa en la que la línea de la calle (desplazamiento o) sale de la envolvente del cruce, buscando
    alrededor del corte s0. None si no la cruza."""
    afuera = 1.0 if lado == 0 else -1.0
    ss = np.unique(np.clip(s0 + afuera * np.arange(-12.0, 60.01, 0.5), p.a, p.b))
    if afuera < 0:
        ss = ss[::-1]                                   # siempre de dentro hacia fuera
    q = e.at(ss, o)
    ins = shapely.contains_xy(casco, q[:, 0], q[:, 1])
    if not ins.any() or ins[-1]:
        return None
    i = int(np.flatnonzero(ins)[-1])
    lo, hi = ss[i], ss[i + 1]
    for _ in range(12):
        m = (lo + hi) / 2
        if shapely.contains_xy(casco, *e.at(m, o)[:2]):
            lo = m
        else:
            hi = m
    return float(lo)


def _bocas_en_casco(c, brazos):
    """Para los cruces en los que las bocas en estrella se pisan (rampas que se juntan casi paralelas, vías de
    giro pegadas a la esquina): la boca de cada calle es el trozo del borde de la envolvente por el que pasan sus
    carriles, y las calles cuyos trozos se pisan comparten boca. Recoloca el corte y la boca de cada calle.
    Devuelve las bocas como _bocas_en_estrella, o None."""
    borde = c.casco.exterior
    largo = borde.length
    arcos = []
    for b in brazos:
        e = b.p.e
        W = _sentidos(e)[2]
        us, pts = [], []
        for o in (W / 2 - 0.3, -(W / 2 - 0.3)):
            s = _sale_del_casco(e, b.p, b.lado, o, c.casco, b.s)
            if s is None:
                return None
            pts.append(e.at(s, o)[:2])
            us.append(borde.project(shapely.Point(pts[-1])))
        s_c = _sale_del_casco(e, b.p, b.lado, 0.0, c.casco, b.s)
        if s_c is None:
            return None
        u0, du = us[0], (us[1] - us[0]) % largo
        if du > largo / 2:
            u0, du = us[1], largo - du
        if du > largo / 2 - 1.0:
            return None
        b.s, b.A, b.B = s_c, pts[0], pts[1]
        b.mid = (b.A + b.B) / 2
        t = e.frame(s_c) * (1.0 if b.lado == 0 else -1.0)
        b.t = t / max(float(np.linalg.norm(t)), 1e-9)
        arcos.append([u0, du, [b]])
    arcos.sort(key=lambda x: x[0])
    grupos = [arcos[0]]
    for u0, du, bs in arcos[1:]:
        g = grupos[-1]
        if u0 <= g[0] + g[1] + 0.3:                      # se pisa con el anterior: misma boca
            g[1] = max(g[1], u0 + du - g[0])
            g[2] += bs
        else:
            grupos.append([u0, du, bs])
    if len(grupos) > 1 and grupos[0][0] + largo <= grupos[-1][0] + grupos[-1][1] + 0.3:     # el último da la vuelta
        g = grupos.pop()
        grupos[0] = [g[0] - largo, max(g[1], grupos[0][0] + grupos[0][1] + largo - g[0]), g[2] + grupos[0][2]]
    if len(grupos) < 2 or any(g[1] > largo / 2 for g in grupos):
        return None

    def punto(u):
        q = borde.interpolate(u % largo)
        return np.array([q.x, q.y])
    return [(bs, punto(u0), punto(u0 + du)) for u0, du, bs in grupos]


def _poner_bocas(c, brazos, semaforo):
    """Cambia el polígono del cruce por uno con un lado ("boca") por calle, en orden alrededor del centro; entre
    dos bocas queda una esquina. Con semáforo, las esquinas entre calles de distinta fase se parten en dos para
    que las bocas de una fase caigan en lados pares y las de la otra en impares (CSP da el verde por paridad).
    Devuelve None, o el motivo si no sale un polígono en el que cada calle entre por su boca (se queda la
    envolvente)."""
    if len(brazos) < 2:
        return "menos de dos calles"
    motivo = _con_bocas(c, _bocas_en_estrella(brazos), semaforo)
    if motivo is None:
        return None
    bocas = _bocas_en_casco(c, brazos)
    if bocas is None:
        return motivo
    motivo = _con_bocas(c, bocas, semaforo)
    if motivo is None:
        for b in brazos:
            c.cortes[(id(b.p), b.lado)] = b.s          # los carriles entran por la boca nueva
    return motivo


def _con_bocas(c, bocas, semaforo):
    """Arma y comprueba el polígono con esas bocas [(calles, extremo 1, extremo 2)]; None o el motivo."""
    brazos = [b for bs, _, _ in bocas for b in bs]
    par = _fases(brazos) if semaforo else None
    if par is not None and any(len({b.fase for b in bs}) > 1 for bs, _, _ in bocas):
        par = None                                      # dos fases por la misma boca: sin semáforo
    anillo, lados = [], []
    for i, (bs, a1, a2) in enumerate(bocas):
        lados.append(len(anillo) + 1)                  # lado i de CSP: del punto i al i+1, contando desde 1
        anillo += [a1, a2]
        if par is not None and i < len(bocas) - 1 and bocas[i + 1][0][0].fase != bs[0].fase:
            anillo.append((a2 + bocas[i + 1][1]) / 2)
    if par is not None and lados[next(i for i, x in enumerate(bocas) if x[0][0].fase == par)] % 2:
        anillo = [anillo[-1]] + anillo[:-1]
        lados = [k + 1 for k in lados]
    pg = Polygon(anillo)
    if not pg.is_valid or pg.area < 1.0:
        return "las bocas se cruzan"
    borde = pg.exterior
    for bs, a1, a2 in bocas:
        boca = shapely.LineString([a1, a2])
        for b in bs:
            e = b.p.e
            W = _sentidos(e)[2]
            afuera = 1.0 if b.lado == 0 else -1.0
            for o in (-W / 2 + 0.8, W / 2 - 0.8):
                si = _dentro_del_cruce(e, b.s, b.lado, pg, o)
                if si is None:
                    return "un carril no entra"
                q_in, q_out = e.at(si, o)[:2], e.at(b.s + afuera * 1.0, o)[:2]
                paso = shapely.LineString([q_in, q_out])
                corte = paso.intersection(borde)
                if shapely.contains_xy(pg, *q_out) or corte.geom_type != "Point" or paso.distance(boca) > 1e-6:
                    return "un carril no pasa por su boca"
    c.poly, c.anillo, c.brazos = pg, np.array(anillo), brazos
    c.brazo_de = {(id(b.p), b.lado): b for b in brazos}
    for (bs, _, _), k in zip(bocas, lados):
        for b in bs:
            b.lado_poli = k
    if par is not None:
        c.semaforo = VERDE_S[1] if max(b.p.f + b.p.r for b in brazos) >= 4 else VERDE_S[0]
    return None


def _nodo_al_llegar(net, p, rev, prueba, hasta=40.0):
    """¿Algún nodo de los últimos metros antes del cruce cumple `prueba(etiquetas)`? (rev: el recorrido acaba en
    el principio del tramo)."""
    nodos = p.e.nodes if rev else p.e.nodes[::-1]       # desde el extremo donde termina el recorrido
    x0 = np.array(net.node_xy.get(nodos[0], (0.0, 0.0)))
    for n in nodos[:12]:
        xy = net.node_xy.get(n)
        if xy is None or float(np.linalg.norm(np.array(xy) - x0)) > hasta:
            break
        t = net.node_tags.get(n) or {}
        if t and prueba(t):
            return True
    return False


def _es_semaforo(t):
    return t.get("highway") == "traffic_signals"


def _es_paso_con_semaforo(t):
    return t.get("highway") == "crossing" and (t.get("crossing") == "traffic_signals"
                                               or t.get("crossing:signals") == "yes")


def _con_semaforo(net, c, brazos):
    """¿OSM marca un semáforo en el cruce? En el nodo, en la línea de alto de alguna calle que llega (a menos
    de 50 m) o en sus pasos de peatones (a menos de 30 m)."""
    if c.glorieta or c.tipo != "cruce":
        return False
    if any(_es_semaforo(net.node_tags.get(n) or {}) for n in c.nodos):
        return True
    return any(_nodo_al_llegar(net, b.p, b.lado == 0, _es_semaforo, 50.0)
               or _nodo_al_llegar(net, b.p, b.lado == 0, _es_paso_con_semaforo, 30.0)
               for b in brazos if b.llegan() > 0)


_FLECHA = {"through": "T", "left": "L", "sharp_left": "L", "slight_left": "LT", "right": "R", "sharp_right": "R",
           "slight_right": "RT", "reverse": "LU", "merge_to_left": "T", "merge_to_right": "T"}


def _flechas_de(b):
    """Giros que permite cada carril que entra por la calle (del de más adentro al de más afuera) según
    `turn:lanes` de OSM: conjuntos de letras (T recto, L izquierda, R derecha, U vuelta) o None si no dice nada."""
    e, n = b.p.e, len(b.entradas)
    de_ida = b.lado == 1
    tag = e.tags.get("turn:lanes:forward" if de_ida else "turn:lanes:backward")
    if tag is None and e.oneway and de_ida == (e.tags.get("oneway") != "-1"):
        tag = e.tags.get("turn:lanes")
    if not tag or not n:
        return None
    partes = tag.split("|")
    m = len(partes)
    out = []
    for k in range(n):
        mios = [x for i, x in enumerate(partes) if (round(i * (n - 1) / (m - 1)) if m > 1 and n > 1 else 0) == k]
        letras, libre = set(), False
        for x in mios:
            for tok in x.split(";"):
                if tok in _FLECHA:
                    letras |= set(_FLECHA[tok])
                else:
                    libre = True               # "none", vacío o algo desconocido: sin restricción
        out.append(None if libre or not letras else letras)
    return out if any(x is not None for x in out) else None


def _giro(d_e, d_s):
    """(ángulo en grados, izquierda +; letras con las que casa) de la trayectoria que entra con d_e y sale con d_s."""
    ang = math.degrees(math.atan2(_cruz(d_e, d_s), float(d_e @ d_s)))
    a = abs(ang)
    lado = "L" if ang > 0 else "R"
    letras = {"T"} if a <= 30 else {"T", lado} if a <= 60 else {lado} if a <= 150 else {lado, "U"}
    return ang, letras


def _prohibir_por_flechas(c, vetadas):
    """Trayectorias que las flechas de carril de OSM no permiten: [[carril que entra, carril que sale]].

    Con cuidado de no dejar un carril sin salida (el coche desaparecería a la vista): no se aplica a un carril
    si, contando las reglas propias de CSP (solo el carril del lado del giro puede girar a una calle de dos o más
    carriles), se queda sin ninguna; ni a una calle entera si deja otra a la que ya no se pueda ir desde ella."""
    d_ent = {x[0]: x[1] for x in c.entradas}
    d_sal = {x[0]: x[1] for x in c.salidas}
    brazo_sal = {lid: b for b in c.brazos for lid in b.salidas}
    out = []
    for b in c.brazos:
        fl = _flechas_de(b)
        if fl is None:
            continue
        n = len(b.entradas)
        mias, alcanza_antes, alcanza = [], set(), set()
        for k, lid_e in enumerate(b.entradas):
            quitar, quedan = [], []
            for lid_s, d_s in d_sal.items():
                if (lid_e, lid_s) in vetadas:
                    continue
                bs = brazo_sal.get(lid_s)
                alcanza_antes.add(id(bs))
                ang, letras = _giro(d_ent[lid_e], d_s)
                if fl[k] is not None and not (fl[k] & letras):
                    quitar.append(lid_s)
                    continue
                de_csp = (n >= 2 and abs(ang) > 60 and bs is not None and len(bs.salidas) >= 2
                          and k != (0 if ang > 0 else n - 1))
                if not de_csp:
                    quedan.append(lid_s)
            if not quedan:
                quitar, quedan = [], [s for s in d_sal if (lid_e, s) not in vetadas]
            mias += [[lid_e, s] for s in quitar]
            alcanza |= {id(brazo_sal.get(s)) for s in quedan}
        if alcanza_antes <= alcanza:
            out += mias
    return out


def construir(net, scene=None, log=print):
    """Datos del traffic.json en el formato del editor de la herramienta de CSP, con coordenadas locales
    (x este, y norte, z arriba; `a_ac` las pasa a las de AC). None si no hay calles con tráfico.

    Si con las reglas de CSP algún carril llega a un cruce y no puede seguir por ninguno (dos carriles que
    acaban en una T: CSP solo deja girar al del lado del giro; el coche del otro desaparecería ahí), se quita
    ese carril y se vuelve a armar."""
    quitar = set()
    for _ in range(4):
        datos, texto = _construir(net, scene, quitar)
        if datos is None:
            return None
        por_calle = {}
        for ln in datos["lanes"]:
            if "_k" in ln:
                por_calle[ln["_k"][:2]] = por_calle.get(ln["_k"][:2], 0) + 1
        nuevos = set()
        for k in sorted(_sin_salida(datos) - quitar):
            if por_calle[k[:2]] > 1:                   # nunca el último carril de una calle
                por_calle[k[:2]] -= 1
                nuevos.add(k)
        if not nuevos:
            break
        quitar |= nuevos
    log(texto + (f"; {len(quitar)} carriles quitados por no tener por dónde seguir" if quitar else ""))
    return datos


def _sin_salida(datos):
    """Carriles (su clave `_k`) que llegan a un cruce y, tal como CSP los une, no pueden seguir por ninguno."""
    L = datos["lanes"]
    out = set()
    for K in enlaces_csp(a_ac(datos)):
        sal = [s["carril"] for s in K["sal"] if "_k" in L[s["carril"]]]
        for x in K["ent"]:
            ln = L[x["carril"]]
            if "_k" in ln and not any((x["carril"], s) not in K["veto"] for s in sal):
                out.add(ln["_k"])
    return out


def _recto_sin_cruzarse(c, por_id, vetadas):
    """Al seguir recto (rampas que se juntan o se separan, avenidas de varios carriles) cada carril sigue por el
    que tiene enfrente o por el de al lado: {(carril que entra, carril que sale)} que quedarían cruzados. Cada
    carril que entra conserva el más cercano de cada calle, y cada uno que sale, quien le llega más de frente."""
    por_calle_e, por_calle_s = {}, {}
    for x in c.entradas:
        por_calle_e.setdefault((x[2], x[3]), []).append(x)
    for x in c.salidas:
        por_calle_s.setdefault((x[2], x[3]), []).append(x)
    out = set()
    for es in por_calle_e.values():
        for ss in por_calle_s.values():
            if len(es) * len(ss) < 2:
                continue
            lat = {}
            for lid_e, d_e, *_ in es:
                p_e = por_id[lid_e]["points"][-1, :2]
                for lid_s, d_s, *_ in ss:
                    if (lid_e, lid_s) in vetadas or abs(_giro(d_e, d_s)[0]) > 30:
                        continue
                    lat[(lid_e, lid_s)] = abs(_cruz(d_e, por_id[lid_s]["points"][0, :2] - p_e))
            if len(lat) < 2:
                continue
            tope = min(lat.values()) + 4.0
            min_e, min_s = {}, {}
            for (a, b), v in lat.items():
                min_e[a] = min(min_e.get(a, 1e9), v)
                min_s[b] = min(min_s.get(b, 1e9), v)
            out |= {k for k, v in lat.items() if v > tope and v > min_e[k[0]] + 0.5 and v > min_s[k[1]] + 0.5}
    return out


def _construir(net, scene, quitar):
    """Una pasada de `construir` sin los carriles de `quitar` {(cadena, de ida, carril)}: (datos, resumen)."""
    piezas = []
    for e in net.edges:
        if e.cls not in CLASES or e.P is None:
            continue
        for a, b in e.runs:
            if b - a < 1.0:
                continue
            na = e.a if a <= 1e-6 and net.node_inside(e.a) else None
            nb = e.b if b >= e.length - 1e-6 and net.node_inside(e.b) else None
            piezas.append(_Pieza(e, a, b, na, nb))
    if not piezas:
        return None, ""
    extremos = {}
    for p in piezas:
        for lado in (0, 1):
            n = p.nodo(lado)
            if n is not None:
                extremos.setdefault(n, []).append((p, lado))

    # ---- cruces de la red (fundidos como la calzada) donde llegan 3 o más extremos de calle
    grupos, cascos = roads._agrupar_cruces(net)
    cruces, de_nodo = [], {}
    for nid, lst in extremos.items():
        if len(lst) < 3 or nid in de_nodo:
            continue
        # solo los del mismo nivel: en un enlace de autopistas la calzada funde cruces que se pisan en planta
        # aunque estén a 10 m de altura uno de otro, y CSP no uniría ningún carril a un polígono así
        z0 = net.node_z.get(nid, 0.0)
        miembros = [m for m in grupos.get(nid, [nid])
                    if m == nid or (m not in de_nodo and abs(net.node_z.get(m, z0) - z0) < CRUCE_Y - 1.0)]
        pgs = [cascos[m] for m in miembros if m in cascos]
        u = unary_union(pgs) if pgs else None
        pg = _poligono(np.array(u.convex_hull.exterior.coords)) if u is not None and not u.is_empty else None
        if pg is None:
            continue
        c = _Cruce(pg, "cruce", miembros)
        for m in miembros:
            de_nodo[m] = c
            for p, lado in extremos.get(m, []):
                c.cortes[(id(p), lado)] = p.e.s0 if lado == 0 else p.e.s1
        cruces.append(c)

    # ---- nodos con dos calles: el carril sigue si los carriles casan (si no, un cruce pequeño); calles sin
    # salida de doble sentido: retorno
    paso = {}
    for nid, lst in extremos.items():
        if nid in de_nodo:
            continue
        if len(lst) == 2:
            (p1, l1), (p2, l2) = lst
            # el carril sigue de largo si hay carriles en los mismos sentidos (con el número menor del tramo: un
            # cruce pequeño cada vez que OSM cambia el número de carriles dejaba tramos cortísimos que se llenaban)
            llega, sale = p1.cuenta(al_reves=(l1 == 0)), p2.cuenta(al_reves=(l2 == 1))
            if (llega[0] > 0) == (sale[0] > 0) and (llega[1] > 0) == (sale[1] > 0):
                paso[nid] = lst
                continue
            cortes, pts = {}, []
            for p, lado in lst:
                d = min(3.0, 0.3 * (p.b - p.a))
                s = p.s(lado) + (d if lado == 0 else -d)
                cortes[(id(p), lado)] = s
                pts += [p.e.at(s, p.e.hw)[:2], p.e.at(s, -p.e.hw)[:2]]
            pg = _poligono(pts)
            if pg is not None:
                c = _Cruce(pg, "union", [nid])
                c.cortes = cortes
                de_nodo[nid] = c
                cruces.append(c)
        elif len(lst) == 1:
            p, lado = lst[0]
            if p.f and p.r and p.b - p.a >= VUELTA_MIN:
                e = p.e
                s_fin = p.s(lado)
                lu = min(12.0, 0.4 * (p.b - p.a))
                s_lejos = s_fin + (lu if lado == 0 else -lu)
                t = e.frame(s_fin) * (-1.0 if lado == 0 else 1.0)        # hacia fuera de la calle
                t = t / max(float(np.linalg.norm(t)), 1e-9)
                w = e.hw + 0.3
                pg = _poligono([e.at(s_lejos, w)[:2], e.at(s_lejos, -w)[:2],
                                e.at(s_fin, w)[:2] + 1.5 * t, e.at(s_fin, -w)[:2] + 1.5 * t])
                if pg is not None:
                    c = _Cruce(pg, "retorno", [nid])
                    c.cortes[(id(p), lado)] = s_lejos
                    de_nodo[nid] = c
                    cruces.append(c)
    for c in cruces:
        c.glorieta = any(p.e.tags.get("junction") in GLORIETA for n in c.nodos for p, _ in extremos.get(n, []))
        c.z = float(np.mean([net.node_z.get(n, 0.0) for n in c.nodos]))

    # ---- cadenas: tramos unidos por nodos de paso, en orden de recorrido [(pieza, al_reves)]
    def siguiente(p, rev):
        lado = 0 if rev else 1
        n = p.nodo(lado)
        if n is None or n not in paso:
            return None
        (p1, l1), (p2, l2) = paso[n]
        q, lq = (p2, l2) if (p1 is p and l1 == lado) else (p1, l1)
        return q, lq == 1

    vistas, cadenas = set(), []
    for p in piezas:
        if id(p) in vistas:
            continue
        cad, lazo = [(p, False)], False
        vistas.add(id(p))
        cur = (p, False)
        while (nx := siguiente(*cur)) is not None:
            if nx[0] is p:
                lazo = True
                break
            if id(nx[0]) in vistas:
                break
            cad.append(nx)
            vistas.add(id(nx[0]))
            cur = nx
        if not lazo:
            atras, cur = [], (p, True)
            while (nx := siguiente(*cur)) is not None and id(nx[0]) not in vistas:
                atras.append((nx[0], not nx[1]))
                vistas.add(id(nx[0]))
                cur = nx
            cad = atras[::-1] + cad
        cadenas.append((cad, lazo))

    # ---- cruces muy juntos (del mismo nivel) pasan a ser uno: el tramo entre ellos, donde caben uno o dos coches,
    # se llenaba enseguida y el coche que esperaba dentro del cruce de atrás lo bloqueaba (CSP deja entrar a un
    # cruce aunque no haya sitio a la salida, y el que se queda parado dentro corta todas las trayectorias)
    cruces, de_nodo = _fundir_juntos(cruces, de_nodo, cadenas)
    arbol_cruces = shapely.STRtree([c.casco for c in cruces]) if cruces else None

    # ---- calles que quedan enteras dentro de un cruce (entre las dos calzadas de un bulevar): sin carriles
    internas, cadena_de = set(), {}
    for ci, (cad, lazo) in enumerate(cadenas):
        if lazo:
            continue
        (p0, r0), (p1, r1) = cad[0], cad[-1]
        l0, l1 = (1 if r0 else 0), (0 if r1 else 1)
        cadena_de[(id(p0), l0)] = cadena_de[(id(p1), l1)] = ci
        c0, c1 = de_nodo.get(p0.nodo(l0)), de_nodo.get(p1.nodo(l1))
        if c0 is None or c0 is not c1 or c0.tipo != "cruce":
            continue
        dentro = True
        for p, _ in cad:
            W = _sentidos(p.e)[2]
            ss = np.linspace(p.a, p.b, max(2, int((p.b - p.a) / PASO) + 1))
            for o in (-W / 2, 0.0, W / 2):
                q = p.e.at(ss, o)
                if not (shapely.contains_xy(c0.casco, q[:, 0], q[:, 1]).all() and np.all(np.abs(q[:, 2] - c0.z) < CRUCE_Y)):
                    dentro = False
                    break
            if not dentro:
                break
        if dentro:
            internas.add(ci)

    # ---- polígono de cada cruce con una boca por calle (y semáforo donde OSM lo marca)
    pieza_de = {id(p): p for p in piezas}
    n_bocas = 0
    for c in cruces:
        if c.tipo != "cruce":
            continue
        brazos = [_Brazo(pieza_de[pid], lado, s) for (pid, lado), s in c.cortes.items()
                  if cadena_de.get((pid, lado)) not in internas and (pid, lado) in cadena_de]
        c.sin_bocas = _poner_bocas(c, brazos, _con_semaforo(net, c, brazos))
        n_bocas += c.sin_bocas is None
    sin_bocas = sum(c.tipo == "cruce" for c in cruces) - n_bocas

    def tapado(pts):
        """¿El carril cae entero dentro de un cruce de su nivel? (CSP no lo uniría a nada)."""
        if arbol_cruces is None:
            return False
        caja = shapely.box(pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
        for k in arbol_cruces.query(caja, predicate="intersects"):
            c = cruces[int(k)]
            if shapely.contains_xy(c.casco, pts[:, 0], pts[:, 1]).all() and np.all(np.abs(pts[:, 2] - c.z) < CRUCE_Y):
                return True
        return False

    # ---- carriles
    roles, rol_de = [], {}

    def rol(e):
        clave = (int(e.prio), _velocidad(e))
        if clave not in rol_de:
            roles.append({"name": f"{e.cls} {clave[1]} km/h", "priority": clave[0], "speedLimit": clave[1]})
            rol_de[clave] = len(roles)       # índice desde 1 (Lua)
        return rol_de[clave]

    def extremo(p, rev, inicio):
        """Cruce en el que empieza (inicio) o termina el recorrido de la pieza, y su lado."""
        lado = (1 if rev else 0) if inicio else (0 if rev else 1)
        c = de_nodo.get(p.nodo(lado))
        return (c, lado) if c is not None and (id(p), lado) in c.cortes else (None, lado)

    carriles, fallos = [], 0
    for ci, (cad, lazo) in enumerate(cadenas):
        if ci in internas:
            continue
        for de_ida in (True, False):
            recorrido = cad if de_ida else [(p, not rev) for p, rev in cad[::-1]]
            (p0, rev0), (p1, rev1) = recorrido[0], recorrido[-1]
            c_ini, l_ini0 = (None, 0) if lazo else extremo(p0, rev0, True)
            c_fin, l_fin1 = (None, 0) if lazo else extremo(p1, rev1, False)
            n_carr = min(p.cuenta(rev)[0] for p, rev in recorrido)
            for k in range(n_carr):
                if (ci, de_ida, k) in quitar:
                    continue
                tramos, une_ini, une_fin = [], False, False
                for i, (p, rev) in enumerate(recorrido):
                    e = p.e
                    o = _desplazamiento(e, k, de_ida=not rev)
                    s_ini, s_fin = (p.b, p.a) if rev else (p.a, p.b)
                    if i == 0 and c_ini is not None:
                        si = _dentro_del_cruce(e, c_ini.cortes[(id(p), l_ini0)], l_ini0, c_ini.poly, o)
                        if si is not None:
                            s_ini, une_ini = si, True
                        else:
                            fallos += 1
                    if i == len(recorrido) - 1 and c_fin is not None:
                        si = _dentro_del_cruce(e, c_fin.cortes[(id(p), l_fin1)], l_fin1, c_fin.poly, o)
                        if si is not None:
                            s_fin, une_fin = si, True
                        else:
                            fallos += 1
                    m = max(2, int(math.ceil(abs(s_fin - s_ini) / PASO)) + 1)
                    tramos.append(e.at(np.linspace(s_ini, s_fin, m), o))
                pts = tramos[0]
                for t in tramos[1:]:
                    # en el nodo de paso, el punto medio entre el final de una calle y el principio de la otra
                    pts = np.vstack([pts[:-1], ((pts[-1] + t[0]) / 2)[None], t[1:]])
                if lazo:
                    pts = pts[:-1]
                if len(tramos) > 1 and len(pts) > 4:
                    # donde cambia el ancho de la calle el carril saltaba de lado: se reparte en ~15 m
                    pts = pts.copy()
                    pts[1:-1, :2] = gaussian_filter1d(pts[:, :2], 1.6, axis=0, mode="wrap" if lazo else "nearest")[1:-1]
                L = float(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1).sum())
                if len(pts) < 2 or L < 4.0:
                    continue
                # un carril entero dentro de un cruce (el tramo entre las dos calzadas de un bulevar) no se une
                if tapado(pts):
                    continue
                if scene is not None:
                    pts = pts.copy()
                    pts[:, 2] += roads.realce_en(scene, pts, banda=1.6)
                lid = len(carriles) + 1
                e0 = p0.e
                carriles.append({"name": f"{e0.tags.get('name') or e0.cls} #{lid}", "id": lid, "loop": bool(lazo),
                                 "role": rol(e0), "priorityOffset": 0, "points": pts, "_k": (ci, de_ida, k),
                                 "params": {"allowUTurns": False, "allowLaneChanges": n_carr > 1}})
                d_ini = pts[1, :2] - pts[0, :2]
                d_fin = pts[-1, :2] - pts[-2, :2]
                d_ini = d_ini / max(float(np.linalg.norm(d_ini)), 1e-9)
                d_fin = d_fin / max(float(np.linalg.norm(d_fin)), 1e-9)
                if une_ini:
                    c_ini.salidas.append((lid, d_ini, ci, 0 if de_ida else 1, e0))
                    if c_ini.brazos:
                        c_ini.brazo_de[(id(p0), l_ini0)].salidas.append(lid)
                if une_fin:
                    c_fin.entradas.append((lid, d_fin, ci, 1 if de_ida else 0, p1.e, (p1, rev1)))
                    if c_fin.brazos:
                        c_fin.brazo_de[(id(p1), l_fin1)].entradas.append(lid)

    # ---- carriles fantasma: en cada calle por la que solo se entra al cruce (o solo se sale), uno de 5 m en
    # contra, para que CSP no descarte ese lado del polígono (sin él no aplica ahí ni sus reglas de carril ni el
    # semáforo). Ninguna trayectoria puede usarlos y son demasiado cortos para que aparezca un coche en ellos.
    n_reales = len(carriles)
    for c in cruces:
        c.fant_ent, c.fant_sal = [], []
        if not c.brazos or not c.entradas or not c.salidas:
            continue
        d0 = c.entradas[0][1]
        if len(c.salidas) == 1 and all(float(x[1] @ d0) > 0.5 for x in c.entradas):
            c.semaforo = None          # incorporación: CSP la trata aparte (cremallera) si solo hay una salida
            continue
        por_lado = {}
        for b in c.brazos:
            por_lado.setdefault(b.lado_poli, []).append(b)
        for bs in por_lado.values():
            entran, salen = any(b.entradas for b in bs), any(b.salidas for b in bs)
            if entran == salen:
                continue
            sale = entran                                 # falta un carril que salga
            b = max((b for b in bs if b.entradas or b.salidas), key=lambda b: len(b.entradas) + len(b.salidas))
            e = b.p.e
            reales_de_ida = (b.lado == 1) if sale else (b.lado == 0)
            o = (_sentidos(e)[2] / 2 - 0.8) * (1.0 if reales_de_ida else -1.0)    # a la izquierda de los reales
            s_in = _dentro_del_cruce(e, b.s, b.lado, c.poly, o)
            s_out = float(np.clip(b.s + (FANT_FUERA if b.lado == 0 else -FANT_FUERA), b.p.a, b.p.b))
            if s_in is None or abs(s_out - b.s) < 1.5 or abs(s_in - b.s) < 0.3:
                continue
            pts = e.at(np.array([s_in, b.s, s_out] if sale else [s_out, b.s, s_in]), o)
            if scene is not None:
                pts[:, 2] += roads.realce_en(scene, pts, banda=1.6)
            lid = len(carriles) + 1
            carriles.append({"name": f"{FANTASMA}{lid}", "id": lid, "loop": False, "role": rol(e),
                             "priorityOffset": 0, "points": pts,
                             "params": {"allowUTurns": False, "allowLaneChanges": False}})
            (c.fant_sal if sale else c.fant_ent).append(lid)

    # ---- cruces: polígono, giros prohibidos y prioridad de entrada
    por_id = {c["id"]: c for c in carriles}
    cruces_out, n_semaforos = [], 0
    for c in cruces:
        if not c.entradas or not c.salidas:
            continue
        prohib, prio = set(), []
        if c.tipo != "retorno":
            # sin cambios de sentido: volver por la misma calle o girar más de ~150°, salvo que no haya otra salida
            for lid_e, d_e, cad_e, ext_e, *_ in c.entradas:
                malos = [lid_s for lid_s, d_s, cad_s, ext_s, _ in c.salidas
                         if (cad_s == cad_e and ext_s == ext_e) or float(d_e @ d_s) < -0.85]
                if len(malos) < len(c.salidas):
                    prohib |= {(lid_e, s) for s in malos}
        if c.tipo == "cruce":
            prohib |= _recto_sin_cruzarse(c, por_id, prohib)
            if c.brazos:
                prohib |= {(a, b) for a, b in _prohibir_por_flechas(c, prohib)}
        # nadie va a un carril fantasma ni sale de uno
        prohib |= {(x[0], f) for f in c.fant_sal for x in c.entradas}
        prohib |= {(f, s) for f in c.fant_ent for s in [x[0] for x in c.salidas] + c.fant_sal}
        for lid_e, _, _, _, e1, (p_fin, rev_fin) in c.entradas:
            off = 0
            if c.glorieta and e1.tags.get("junction") not in GLORIETA:
                off -= 5                     # en la glorieta pasa primero quien ya está dentro
            if _alto_al_llegar(net, p_fin, rev_fin):
                off -= 5
            if off:
                prio.append({"lane": lid_e, "offset": off})
        # todos los vértices a la misma altura (la media de los carriles que llegan y salen): así CSP solo admite
        # carriles a ±5 m y no une el cruce con un puente que pase por encima
        zs = [por_id[x[0]]["points"][-1, 2] for x in c.entradas] + [por_id[x[0]]["points"][0, 2] for x in c.salidas]
        ring = c.anillo if c.brazos else np.array(c.poly.exterior.coords)[:-1]
        n = len(cruces_out) + 1
        z_c = float(np.mean(zs))
        it = {"name": f"{c.tipo.capitalize()} #{n}", "id": n,
              "points": np.column_stack([ring, np.full(len(ring), z_c)])}
        luz = _semaforo(c, len(ring), z_c) if c.semaforo and c.brazos else None
        if luz:
            it["trafficLight"] = luz
            n_semaforos += 1
        if prohib:
            it["disallowedTrajectories"] = [list(x) for x in sorted(prohib)]
        if prio:
            it["entryPriorityOffsets"] = prio
        cruces_out.append(it)
    km = sum(float(np.linalg.norm(np.diff(c["points"][:, :2], axis=0), axis=1).sum())
             for c in carriles[:n_reales]) / 1000
    if not n_reales:
        return None, ""
    datos = {"lanes": carriles, "intersections": cruces_out, "areas": [], "rules": {"laneRoles": roles}}
    return datos, (f"  tráfico: {n_reales} carriles ({km:.1f} km), {len(cruces_out)} cruces"
                   + (f" ({n_semaforos} con semáforo)" if n_semaforos else "")
                   + (f", {fallos} extremos de carril que no entran a su cruce" if fallos else "")
                   + (f", {sin_bocas} cruces sin un lado por calle" if sin_bocas else "")
                   + f"; en el modo Traffic, unos {coches_recomendados(datos)} coches")


def _semaforo(c, n_lados, z):
    """Semáforo de CSP para el cruce: programa de dos fases (lados pares / impares) y, por cada calle que entra,
    las luces (círculos que CSP dibuja encendidos: roja, ámbar y verde) a la derecha de quien llega y, si la
    calle es ancha o de un solo sentido, también a la izquierda. None si alguna fase se queda sin carriles."""
    con = [b for b in c.brazos if b.entradas]
    if {b.lado_poli % 2 for b in con} != {0, 1}:
        return None
    emis = [None] * n_lados
    postes = []
    for b in con:
        e = b.p.e
        afuera = 1.0 if b.lado == 0 else -1.0
        items = []
        lados = (1.0, -1.0) if (len(b.entradas) >= 3 or not b.salidas) else (1.0,)
        for sg in lados:                                 # +: a la derecha de quien llega (circula en -t)
            base = e.at(b.s + afuera * 0.6, sg * afuera * (e.hw + 0.9))
            postes.append((base, b.t.copy()))
            for h in ALTURA_LUCES:                       # roja, ámbar, verde
                items.append({"pos": np.array([base[0] + b.t[0] * 0.17, base[1] + b.t[1] * 0.17, base[2] + h]),
                              "dir": np.array([b.t[0], b.t[1], 0.0]), "radius": 0.1})
        if emis[b.lado_poli - 1] is None:
            emis[b.lado_poli - 1] = {"mode": 2, "roles": [{"items": []}], "virtual": {"items": []}}
        emis[b.lado_poli - 1]["virtual"]["items"] += items
    emis = [s or {} for s in emis]
    return {"program": "Basic", "params": {"duration": float(c.semaforo)}, "emissive": emis, "postes": postes}


def postes_de_semaforo(datos):
    """[(pie xyz, dirección 2D hacia quien llega)] de los semáforos, para poner el poste y la caja en la pista."""
    return [p for it in datos["intersections"] for p in (it.get("trafficLight") or {}).get("postes", [])]


def poner_postes(scene, datos):
    """Poste y caja de cada semáforo en la pista (las luces las dibuja CSP encima). Devuelve cuántos."""
    postes = postes_de_semaforo(datos)
    for pie, t in postes:
        yaw = math.atan2(t[1], t[0])
        p, uv, f = roads.box_mesh((pie[0], pie[1], pie[2] - 0.3), (0.12, 0.12, 2.8), yaw)
        scene.add("senales", "poste", p, uv, f, phys="WALL")
        p, uv, f = roads.box_mesh((pie[0], pie[1], pie[2] + ALTURA_LUCES[2] - 0.25), (0.24, 0.34, 1.1), yaw)
        scene.add("senales", "poste", p, uv, f)
    return len(postes)


def _fundir_juntos(cruces, de_nodo, cadenas, libre_min=JUNTOS, ext_max=90.0):
    """Une los cruces (del mismo nivel) entre los que una calle deja menos de `libre_min` m fuera de ellos, sin
    pasar de `ext_max` m de largo. Devuelve la nueva lista de cruces y el nuevo nodo -> cruce."""
    idx = {id(c): i for i, c in enumerate(cruces)}
    padre = list(range(len(cruces)))
    forma = {i: c.poly for i, c in enumerate(cruces)}

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i
    for cad, lazo in cadenas:
        if lazo:
            continue
        (p0, r0), (p1, r1) = cad[0], cad[-1]
        l0, l1 = (1 if r0 else 0), (0 if r1 else 1)
        c0, c1 = de_nodo.get(p0.nodo(l0)), de_nodo.get(p1.nodo(l1))
        if c0 is None or c1 is None or c0 is c1 or c0.tipo != "cruce" or c1.tipo != "cruce":
            continue
        if (id(p0), l0) not in c0.cortes or (id(p1), l1) not in c1.cortes or abs(c0.z - c1.z) > 3.0:
            continue
        libre = sum(p.b - p.a for p, _ in cad) - abs(c0.cortes[(id(p0), l0)] - p0.s(l0)) \
            - abs(c1.cortes[(id(p1), l1)] - p1.s(l1))
        if libre >= libre_min:
            continue
        a, b = raiz(idx[id(c0)]), raiz(idx[id(c1)])
        if a == b:
            continue
        u = unary_union([forma[a], forma[b]]).convex_hull
        caja = np.array(shapely.oriented_envelope(u).exterior.coords)
        if max(np.linalg.norm(np.diff(caja, axis=0), axis=1)) > ext_max:
            continue
        padre[b] = a
        forma[a] = u
    grupos = {}
    for i in range(len(cruces)):
        grupos.setdefault(raiz(i), []).append(cruces[i])
    nuevos, de_nodo2 = [], dict(de_nodo)
    for r, miembros in grupos.items():
        if len(miembros) == 1:
            nuevos.append(miembros[0])
            continue
        pg = _poligono(np.array(forma[r].exterior.coords))
        if pg is None:
            nuevos += miembros
            continue
        c = _Cruce(pg, "cruce", set().union(*(m.nodos for m in miembros)))
        for m in miembros:
            c.cortes.update(m.cortes)
        c.glorieta = any(m.glorieta for m in miembros)
        c.z = float(np.mean([m.z for m in miembros]))
        for n in c.nodos:
            de_nodo2[n] = c
        nuevos.append(c)
    return nuevos, de_nodo2


def _alto_al_llegar(net, p, rev, hasta=40.0):
    """¿Hay un alto o un ceda el paso (no de todos los sentidos) en los últimos metros antes del cruce?"""
    return _nodo_al_llegar(net, p, rev, lambda t: t.get("highway") in ("stop", "give_way")
                           and t.get("stop") != "all", hasta)


def coches_recomendados(datos, metros_por_coche=150.0):
    """Coches para el modo Traffic (su selector arranca en 1000, pensado para mapas de cientos de km): en un área
    de 1 km los coches no salen nunca (solo desaparecen en el borde) y con uno cada ~40 m de carril los cruces,
    sin semáforos, acaban atascados."""
    km = sum(float(np.linalg.norm(np.diff(np.asarray(c["points"])[:, :2], axis=0), axis=1).sum())
             for c in datos["lanes"] if not c["name"].startswith(FANTASMA)) / 1000
    return int(max(50, round(km * 1000 / metros_por_coche / 50) * 50))


def a_ac(datos):
    """Pasa a coordenadas de AC (x este, y altura, z sur) y a listas para JSON."""
    def conv(P):
        P = np.asarray(P, float)
        return np.round(np.column_stack([P[:, 0], P[:, 2], -P[:, 1]]), 2).tolist()

    def cruce(c):
        c = {**c, "points": conv(c["points"])}
        luz = c.get("trafficLight")
        if luz:
            emis = [{**s, "virtual": {"items": [{"pos": conv([i["pos"]])[0], "dir": conv([i["dir"]])[0],
                                                 "radius": i["radius"]} for i in s["virtual"]["items"]]}}
                    if s else s for s in luz["emissive"]]
            c["trafficLight"] = {"program": luz["program"], "params": luz["params"], "emissive": emis}
        return c
    return {"lanes": [{**{k: v for k, v in c.items() if k[0] != "_"}, "points": conv(c["points"])}
                      for c in datos["lanes"]],
            "intersections": [cruce(c) for c in datos["intersections"]],
            "areas": datos["areas"], "rules": datos["rules"]}


def _corte(p_fuera, p_dentro, anillo):
    """(lado desde 1, punto) donde el segmento corta el polígono, el corte más cercano al punto de fuera
    (FlatPolyShape:intersect de CSP). (0, p_dentro) si no lo corta."""
    n = len(anillo)
    a, b = anillo, np.roll(anillo, -1, axis=0)
    r = p_dentro - p_fuera
    s = b - a
    den = r[0] * s[:, 1] - r[1] * s[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        q = a - p_fuera
        u = (q[:, 0] * s[:, 1] - q[:, 1] * s[:, 0]) / den
        v = (q[:, 0] * r[1] - q[:, 1] * r[0]) / den
    ok = (np.abs(den) > 1e-12) & (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    if not ok.any():
        return 0, p_dentro
    k = int(np.argmin(np.where(ok, u, np.inf)))
    return k % n + 1, p_fuera + r * u[k]


def enlaces_csp(datos_ac):
    """Une carriles y cruces como CSP (TrafficIntersection:link y :finalizeLinks) y devuelve, por cruce:

    - ent / sal: enlaces {carril (índice), lado, pos, dir} de los carriles que entran (no empiezan dentro) y de
      los que salen (no acaban dentro);
    - lados: lados del polígono por los que entra y sale algún carril (los demás CSP los descarta: a sus
      carriles no les aplica reglas de carril ni semáforo), con sus entradas y salidas ordenadas como CSP;
    - veto: {(carril que entra, carril que sale)} incompatibles (los del archivo y los que CSP deduce: sin
      vueltas en U cruzando otro carril y sin girar más que desde el carril del lado del giro);
    - dentro: carriles enteros dentro del polígono; toques: [(carril, primer punto dentro, toca el principio,
      toca el final)]."""
    lanes, inters = datos_ac["lanes"], datos_ac["intersections"]
    P3 = [np.asarray(ln["points"], float) for ln in lanes]
    cajas = shapely.box([p[:, 0].min() for p in P3], [p[:, 2].min() for p in P3],
                        [p[:, 0].max() for p in P3], [p[:, 2].max() for p in P3]) if P3 else []
    arbol = shapely.STRtree(cajas) if len(P3) else None
    id_a_idx = {ln["id"]: i for i, ln in enumerate(lanes)}
    out = []
    for it in inters:
        Q = np.asarray(it["points"], float)
        anillo = Q[:, [0, 2]]
        pg = Polygon(anillo)
        yc, yt = (Q[:, 1].max() + Q[:, 1].min()) / 2, CRUCE_Y + Q[:, 1].max() - Q[:, 1].min()
        ent, sal, dentro, toques = [], [], [], []
        for li in (sorted(int(k) for k in arbol.query(pg.envelope)) if arbol is not None else []):
            P = P3[li]
            ins = shapely.contains_xy(pg, P[:, 0], P[:, 2]) & (np.abs(P[:, 1] - yc) <= yt)
            if not ins.any():
                continue
            if ins.all():
                dentro.append(li)
                continue
            d = np.diff(np.r_[0, ins.astype(int), 0])
            for i0, i1 in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1):
                toques.append((li, int(i0), bool(i0 == 0), bool(i1 == len(P) - 1)))
                if i0 > 0:
                    lado, pos = _corte(P[i0 - 1, [0, 2]], P[i0, [0, 2]], anillo)
                    v = P[i0, [0, 2]] - P[i0 - 1, [0, 2]]
                    ent.append({"carril": li, "lado": lado, "pos": pos, "dir": v / max(np.linalg.norm(v), 1e-9)})
                if i1 < len(P) - 1:
                    lado, pos = _corte(P[i1 + 1, [0, 2]], P[i1, [0, 2]], anillo)
                    v = P[i1 + 1, [0, 2]] - P[i1, [0, 2]]
                    sal.append({"carril": li, "lado": lado, "pos": pos, "dir": v / max(np.linalg.norm(v), 1e-9)})
        lados = {}
        for k in sorted({x["lado"] for x in ent} & {x["lado"] for x in sal}):
            es, ss = [x for x in ent if x["lado"] == k], [x for x in sal if x["lado"] == k]
            ce, cs = np.mean([x["pos"] for x in es], axis=0), np.mean([x["pos"] for x in ss], axis=0)
            de, ds = np.sum([x["dir"] for x in es], axis=0), np.sum([x["dir"] for x in ss], axis=0)
            es.sort(key=lambda x: float(np.sum((x["pos"] - cs) ** 2)))
            ss.sort(key=lambda x: float(np.sum((x["pos"] - ce) ** 2)))
            lados[k] = {"ent": es, "sal": ss, "centro_sal": cs, "dir_ent": de / max(np.linalg.norm(de), 1e-9),
                        "dir_sal": ds / max(np.linalg.norm(ds), 1e-9)}
        veto = {(id_a_idx[a], id_a_idx[b]) for a, b in it.get("disallowedTrajectories", [])
                if a in id_a_idx and b in id_a_idx}
        for L in lados.values():
            es = L["ent"]
            if len(es) < 2:
                continue
            for i, x in enumerate(es):
                if i > 0:
                    veto |= {(x["carril"], s["carril"]) for s in L["sal"]}
                elif (len(L["sal"]) > 1 and np.linalg.norm(x["pos"] - L["sal"][0]["pos"]) < 6
                      and (x["carril"], L["sal"][1]["carril"]) not in veto):
                    veto.add((x["carril"], L["sal"][0]["carril"]))
            for M in lados.values():
                if len(M["sal"]) < 2 or float(L["dir_ent"] @ M["dir_sal"]) >= 0.5:
                    continue
                d0 = np.sum((es[0]["pos"] - M["centro_sal"]) ** 2)
                d1 = np.sum((es[-1]["pos"] - M["centro_sal"]) ** 2)
                i = 0 if d0 < d1 else len(es) - 1
                if not any((es[i]["carril"], s["carril"]) not in veto for s in M["sal"]):
                    continue
                veto |= {(x["carril"], s["carril"]) for j, x in enumerate(es) if j != i for s in M["sal"]}
        out.append({"ent": ent, "sal": sal, "lados": lados, "veto": veto, "dentro": dentro, "toques": toques})
    return out


def revisar(datos_ac):
    """Une carriles y cruces como CSP (ver enlaces_csp) y mide el resultado, sin abrir el juego.

    - dentro_de_un_cruce: carriles enteros dentro de un polígono (CSP no los une a nada);
    - toques_de_mas: carriles que pasan por un cruce que no es el suyo (o más de dos);
    - cruces_sin_entrada / cruces_sin_salida / cruces_vacios;
    - entradas_sin_salida: carriles que llegan a un cruce y no pueden seguir por ningún otro (el coche
      desaparecería ahí, a la vista);
    - entradas_sin_reglas: fracción de los carriles que entran a un cruce por un lado que CSP descarta (por él no
      sale ningún carril): no les aplica las reglas de carril, ni quién cede al girar, ni el semáforo. Solo
      cuentan los cruces con carriles por tres lados o más (donde solo se juntan dos calles no hay nada que reglar);
    - semaforos / entradas_sin_luz: cruces con semáforo y carriles que entran a ellos sin luz;
    - conexo: de los carriles que van de cruce a cruce, fracción de km dentro de la mayor componente
      fuertemente conexa (de donde un coche puede ir a cualquier otro carril de ella y volver), contando los
      giros prohibidos. Los que entran o salen por el borde del área no cuentan: ahí aparecen y desaparecen;
    - util: fracción de km de carril a la que llegan coches y desde la que pueden seguir hasta una salida del
      área o volver a la red principal (en un recorte, las autopistas de un sentido no vuelven: no son conexas,
      pero sí útiles).

    Los carriles fantasma (ver arriba) solo cuentan para decidir qué lados descarta CSP."""
    lanes, inters = datos_ac["lanes"], datos_ac["intersections"]
    real = np.array([not ln["name"].startswith(FANTASMA) for ln in lanes], bool)
    largos = np.array([float(np.linalg.norm(np.diff(np.asarray(ln["points"], float)[:, [0, 2]], axis=0), axis=1).sum())
                       for ln in lanes])
    info = enlaces_csp(datos_ac)
    n = len(lanes)
    toques = [[] for _ in range(n)]
    dentro = sum(1 for K in info for li in K["dentro"] if real[li])
    sin_ent = sin_sal = vacios = muertas = sin_reglas = n_ent = semaforos = sin_luz = 0
    ar = []
    empieza_en_cruce, acaba_en_cruce = np.zeros(n, bool), np.zeros(n, bool)
    for it, K in zip(inters, info):
        for li, i0, s0, s1 in K["toques"]:
            toques[li].append((i0, s0, s1))
            empieza_en_cruce[li] |= s0
            acaba_en_cruce[li] |= s1
        ent = [x for x in K["ent"] if real[x["carril"]]]
        sal = [x for x in K["sal"] if real[x["carril"]]]
        sin_ent += bool(sal) and not ent
        sin_sal += bool(ent) and not sal
        vacios += not ent and not sal
        luz = bool(it.get("trafficLight"))
        semaforos += luz
        varios = len({x["lado"] for x in ent + sal}) >= 3
        for x in ent:
            sigue = [s["carril"] for s in sal if (x["carril"], s["carril"]) not in K["veto"]]
            muertas += not sigue
            ar += [(x["carril"], s) for s in sigue]
            n_ent += 1
            if x["lado"] not in K["lados"]:
                sin_reglas += varios
                sin_luz += luz
    extra = sum(1 for li in range(n) if real[li]
                and (len(toques[li]) > 2 or any(not (s0 or s1) for _, s0, s1 in toques[li])))
    out = {"carriles": int(real.sum()), "cruces": len(inters), "dentro_de_un_cruce": dentro, "toques_de_mas": extra,
           "cruces_sin_entrada": int(sin_ent), "cruces_sin_salida": int(sin_sal), "cruces_vacios": int(vacios),
           "entradas_sin_salida": int(muertas), "entradas_sin_reglas": round(sin_reglas / max(n_ent, 1), 3),
           "semaforos": int(semaforos), "entradas_sin_luz": int(sin_luz), "conexo": 0.0, "util": 0.0}
    if ar:
        a = np.array(ar)
        g = coo_matrix((np.ones(len(a)), (a[:, 0], a[:, 1])), shape=(n, n))
        _, comp = connected_components(g, directed=True, connection="strong")
        interior = real & empieza_en_cruce & acaba_en_cruce        # los demás tocan el borde del área
        peso = {}
        for li in np.flatnonzero(interior):
            peso[comp[li]] = peso.get(comp[li], 0.0) + largos[li]
        total = float(largos[interior].sum())
        out["conexo"] = round(float(max(peso.values()) / total), 3) if peso and total else 0.0
        # útil: un coche puede llegar al carril (desde el borde o desde la red principal) y, al acabarlo, seguir
        # hasta una salida o volver a la red principal. Lo que no, es una trampa o un carril que nunca se usa.
        grande = max(peso, key=peso.get) if peso else -1
        nucleo = np.flatnonzero((comp == grande) & interior) if grande >= 0 else np.zeros(0, int)
        ida = _alcanzables(g.tocsr(), list(np.flatnonzero(real & ~empieza_en_cruce)) + list(nucleo))
        vuelta = _alcanzables(g.T.tocsr(), list(np.flatnonzero(real & ~acaba_en_cruce)) + list(nucleo))
        out["util"] = round(float(largos[real & ida & vuelta].sum()) / max(float(largos[real].sum()), 1e-9), 3)
    return out


def _alcanzables(g, origen):
    """Vértices a los que se llega desde alguno de `origen` en el grafo dirigido g (CSR)."""
    vis = np.zeros(g.shape[0], bool)
    pila = list(dict.fromkeys(int(v) for v in origen))
    vis[pila] = True
    while pila:
        u = pila.pop()
        for v in g.indices[g.indptr[u]:g.indptr[u + 1]]:
            if not vis[v]:
                vis[v] = True
                pila.append(int(v))
    return vis
