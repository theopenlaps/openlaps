"""Notas de copiloto de un tramo, calculadas de la geometría de la ruta.

De la polilínea del eje (un punto por metro) salen las curvas (lado, grado del 1 al 6, larga, se cierra / abre,
horquilla), los cambios de rasante, los cambios de superficie y los vados, cada uno con su distancia desde la
salida. Se escriben como libro de ruta (NOTAS.txt), como datos (notas.json) y como script de pista de CSP
(notas.lua), que las canta con los mensajes del sistema de Assetto Corsa cuando el coche se acerca.

El grado sigue el uso corriente de 1 (muy cerrada) a 6 (casi a fondo) según el radio mínimo. No hay una escala
oficial: los radios de GRADOS son los que dan un reparto razonable en carreteras de montaña.
"""
import json

import numpy as np
from scipy.ndimage import gaussian_filter1d

# radio mínimo (m) hasta el que una curva es de cada grado; por encima del último no se canta
GRADOS = ((14.0, 1), (24.0, 2), (40.0, 3), (70.0, 4), (120.0, 5), (220.0, 6))
GIRO_MIN = 9.0            # grados: un quiebro menor no es una curva
HORQUILLA = (125.0, 22.0)  # giro mínimo (grados) y radio máximo (m)
LARGA = 70.0              # m de curva a partir de los cuales es "larga"
JUNTAS = 28.0             # m: dos notas más cerca que esto van enlazadas ("y")
RASANTE = (0.07, 0.13)    # cambio de pendiente (tanto por uno) en 30 m para "rasante" y para "salto"


def _tramos(mask):
    c = np.flatnonzero(np.diff(np.concatenate([[False], mask, [False]]).astype(np.int8)))
    return list(zip(c[::2], c[1::2] - 1))


def calcular(ruta, piezas=None, vados=None, cerrada=False):
    """Notas de la ruta: lista de {"d": metros desde la salida, "texto", "tipo", ...} en orden.

    `ruta`: N x 3 (eje, ~1 m entre puntos). `piezas`: [(arista, s0, s1)] de roads.route, para los cambios de
    superficie. `vados`: [(x, y, cota, radio)] de RoadNetwork.vadear."""
    P = np.asarray(ruta, float)
    if len(P) < 30:
        return []
    paso = np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(paso)])
    # remuestreo a 1 m exacto: la curvatura es una derivada y no perdona puntos desiguales
    u = np.arange(0.0, s[-1], 1.0)
    x, y, z = (np.interp(u, s, P[:, k]) for k in range(3))
    x, y = gaussian_filter1d(x, 3.0, mode="nearest"), gaussian_filter1d(y, 3.0, mode="nearest")
    rumbo = np.unwrap(np.arctan2(np.gradient(y), np.gradient(x)))
    curv = gaussian_filter1d(np.gradient(rumbo), 3.0, mode="nearest")          # 1/m, + a la izquierda
    notas = []
    limite = 1.0 / GRADOS[-1][0]
    for signo in (1, -1):
        m = curv * signo > limite
        tramos = _tramos(m)
        # dos trozos de la misma curva separados por un respiro corto son una sola
        unidos = []
        for a, b in tramos:
            if unidos and a - unidos[-1][1] < 18:
                unidos[-1] = (unidos[-1][0], b)
            else:
                unidos.append((a, b))
        for a, b in unidos:
            giro = abs(np.degrees(rumbo[b] - rumbo[a]))
            if giro < GIRO_MIN or b - a < 4:
                continue
            k = np.abs(curv[a:b + 1])
            radio = 1.0 / max(float(np.percentile(k, 90)), 1e-6)
            grado = next((g for r, g in GRADOS if radio <= r), None)
            if grado is None:
                continue
            lado = "izquierda" if signo > 0 else "derecha"
            largo = float(b - a)
            n = {"d": float(u[a]), "fin": float(u[b]), "tipo": "curva", "lado": lado, "grado": grado,
                 "radio": round(radio, 1), "giro": round(giro), "largo": round(largo)}
            if giro >= HORQUILLA[0] and radio <= HORQUILLA[1]:
                n["horquilla"] = True
            elif largo >= LARGA and giro >= 45:
                n["larga"] = True
            if b - a >= 24:
                t = (b - a) // 3
                ini, fin = float(k[:t].mean()), float(k[-t:].mean())
                if fin > 1.45 * ini and not n.get("horquilla"):
                    n["cierra"] = True
                elif ini > 1.45 * fin and not n.get("horquilla"):
                    n["abre"] = True
            notas.append(n)
    # cambios de rasante: la pendiente cae de golpe (se pierde de vista la carretera)
    zs = gaussian_filter1d(z, 4.0, mode="nearest")
    pend = np.gradient(zs)
    if len(pend) > 40:
        caida = pend[:-30] - pend[30:]            # cuánto baja la pendiente en los 30 m siguientes
        for a, b in _tramos(caida > RASANTE[0]):
            i = a + int(np.argmax(caida[a:b + 1]))
            notas.append({"d": float(u[i + 10]), "fin": float(u[min(i + 20, len(u) - 1)]), "tipo": "rasante",
                          "salto": bool(caida[i] > RASANTE[1])})
    # cambios de superficie
    if piezas:
        d, antes = 0.0, None
        for e, s0, s1 in piezas:
            clase = "asfalto" if e.mat in ("asfalto", "concreto") else \
                "empedrado" if e.mat in ("empedrado", "adoquin") else "tierra"
            if antes is not None and clase != antes and d > 30:
                notas.append({"d": d, "fin": d, "tipo": "superficie", "a": clase})
            antes = clase
            d += abs(s1 - s0)
    for vx, vy, _, r in vados or []:
        cerca = np.hypot(x - vx, y - vy) < r
        for a, b in _tramos(cerca):
            notas.append({"d": float(u[a]), "fin": float(u[b]), "tipo": "vado"})
    notas.sort(key=lambda n: n["d"])
    for n in notas:
        n["texto"] = _texto(n)
    if not cerrada:
        notas.append({"d": float(u[-1]), "fin": float(u[-1]), "tipo": "meta", "texto": "Meta"})
    for a, b in zip(notas[:-1], notas[1:]):
        a["hasta"] = round(max(0.0, b["d"] - a["fin"]))
    for n in notas:
        n["d"], n["fin"] = round(n["d"], 1), round(n["fin"], 1)
    return notas


def _texto(n):
    if n["tipo"] == "curva":
        if n.get("horquilla"):
            return f"Horquilla {n['lado']}"
        t = f"{n['lado'].capitalize()} {n['grado']}"
        t += " larga" if n.get("larga") else ""
        t += ", se cierra" if n.get("cierra") else ", abre" if n.get("abre") else ""
        return t
    if n["tipo"] == "rasante":
        return "Salto" if n.get("salto") else "Rasante"
    if n["tipo"] == "superficie":
        return {"asfalto": "Cambio a asfalto", "empedrado": "Cambio a empedrado", "tierra": "Cambio a tierra"}[n["a"]]
    return {"vado": "Vado", "meta": "Meta"}[n["tipo"]]


def _distancia(m):
    """La distancia como se canta: redondeada a 10 m hasta 100, a 50 m después."""
    return int(round(m / 10.0) * 10) if m < 100 else int(round(m / 50.0) * 50)


def enlace(n):
    """Lo que une una nota con la siguiente: "y" si van seguidas, o los metros que hay entre las dos."""
    h = n.get("hasta")
    if h is None:
        return ""
    return "y" if h < JUNTAS else f"{_distancia(h)}"


def libro_de_ruta(notas, titulo=""):
    lineas = [f"Notas de copiloto{' · ' + titulo if titulo else ''}", "",
              "km      nota                                  hasta la siguiente", "-" * 66]
    for n in notas:
        sig = enlace(n)
        lineas.append(f"{n['d'] / 1000:6.2f}  {n['texto']:<38}{'enlazada' if sig == 'y' else sig + ' m' if sig else ''}")
    lineas += ["", "Grados: 1 muy cerrada … 6 casi a fondo. Calculadas por OpenLaps a partir del trazado; revísalas en",
               "un reconocimiento antes de fiarte de ellas."]
    return "\n".join(lineas) + "\n"


_LUA = """-- Notas de copiloto de OpenLaps: canta cada nota con los mensajes del sistema de Assetto Corsa cuando el coche
-- se acerca (unos 4 s antes, 40 m como poco). Generado; no editar: se reescribe al generar la pista.
local notas = {
%s
}
local siguiente, ultima = 1, -1

function script.update(dt)
  local ok = pcall(function ()
    local car, sim = ac.getCar(0), ac.getSim()
    if not car or sim.isReplayActive or sim.isPaused then return end
    local pos = car.splinePosition * sim.trackLengthM
    if pos < ultima - 60 then siguiente = 1 end                  -- vuelta a la salida
    ultima = pos
    while siguiente <= #notas and notas[siguiente][1] < pos - 5 do siguiente = siguiente + 1 end
    local n = notas[siguiente]
    if n and n[1] - pos <= math.max(40, car.speedKmh / 3.6 * 4) then
      ac.setMessage(n[2], n[3], nil, 3.5)
      siguiente = siguiente + 1
    end
  end)
  if not ok then script.update = function () end end           -- un CSP sin estas funciones: sin notas y ya
end
"""


def lua(notas):
    filas = []
    for i, n in enumerate(notas):
        sig = enlace(n)
        despues = notas[i + 1]["texto"] if i + 1 < len(notas) else ""
        detalle = "" if not despues else (f"y {despues}" if sig == "y" else f"{sig} m · {despues}")
        filas.append(f"  {{{n['d']:.1f}, {json.dumps(n['texto'], ensure_ascii=False)}, "
                     f"{json.dumps(detalle, ensure_ascii=False)}}},")
    return _LUA % "\n".join(filas)
