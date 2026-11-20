"""Edificios de Google 3D (opcional): instalar/quitar el script sin tocar lo demás y leer la calzada del .glb."""
import numpy as np

from openlaps import google3d as G


def test_instalar_y_quitar_respeta_el_ini(tmp_path):
    ext = tmp_path / "extension"
    ext.mkdir()
    base = "[BASIC]\nLATITUDE=40.4\n\n[SCRIPT_0]\nSCRIPT=otro.lua\n"
    (ext / "ext_config.ini").write_text(base, encoding="utf-8")
    n = G.instalar(str(tmp_path), 8800)
    assert n == 1
    ini = (ext / "ext_config.ini").read_text(encoding="utf-8")
    assert "SCRIPT=google3d_ac.lua" in ini and "SCRIPT=otro.lua" in ini
    lua = (ext / G.LUA).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8800" in lua and "8772" not in lua
    G.instalar(str(tmp_path), 8801)                      # reinstalar no duplica
    assert (ext / "ext_config.ini").read_text(encoding="utf-8").count("google3d_ac.lua") == 1
    assert G.quitar(str(tmp_path))
    assert not (ext / G.LUA).exists()
    assert (ext / "ext_config.ini").read_text(encoding="utf-8").strip() == base.strip()
    assert not G.quitar(str(tmp_path))                   # apagado: no hay nada que quitar


def test_calles_desde_glb(tmp_path):
    from openlaps import export_glb
    from openlaps.mesh import Scene
    sc = Scene()
    from PIL import Image
    sc.images["gris"] = Image.new("RGB", (4, 4), (80, 80, 80))
    sc.textures["gris"] = None
    sc.material("asfalto", "gris")
    pos = np.array([[0, 0, 650.0], [20, 0, 650.0], [20, 8, 651.0], [0, 8, 651.0]])
    sc.add("calzada", "asfalto", pos, pos[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]), phys="ROAD")
    ruta = str(tmp_path / "p.glb")
    export_glb.write(sc, ruta, {})
    c = G.Calles(ruta)
    # glTF: x este, y arriba, z sur (z = -norte)
    assert c.en(c.via, np.array([10.0]), np.array([-4.0]))[0]
    assert not c.en(c.via, np.array([10.0]), np.array([-30.0]))[0]
    assert c.en(c.franja, np.array([10.0]), np.array([-10.0]))[0]
    assert abs(float(c.suelo(np.array([10.0]), np.array([-4.0]))[0]) - 650.5) < 0.2
    assert abs(float(c.suelo(np.array([10.0]), np.array([-40.0]))[0]) - 651.0) < 0.2   # lejos: la calzada más cercana


def test_recorte_respeta_edificios_y_quita_flotantes_grandes(tmp_path):
    from PIL import Image
    from openlaps import export_glb
    from openlaps.mesh import Scene
    sc = Scene()
    sc.images["gris"] = Image.new("RGB", (4, 4), (80, 80, 80))
    sc.textures["gris"] = None
    sc.material("asfalto", "gris")
    sc.material("fachada", "gris")
    # calzada en x=[0,20], y=[0,8] (en AC: z=[-8, 0]); edificio justo al lado en y=[9, 18] (z=[-18, -9])
    pos = np.array([[0, 0, 650.0], [20, 0, 650.0], [20, 8, 650.0], [0, 8, 650.0]])
    sc.add("calzada", "asfalto", pos, pos[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]), phys="ROAD")
    ed = np.array([[2, 9, 650.0], [18, 9, 650.0], [18, 18, 650.0], [2, 18, 650.0]])
    sc.add("edificios", "fachada", ed, ed[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]))
    ruta = str(tmp_path / "e.glb")
    export_glb.write(sc, ruta, {})
    c = G.Calles(ruta, avisar=False)
    assert c.tiene_edif()
    # dentro del edificio (z=-11) no corta franja ni limpieza aunque esté a 3 m de la calzada
    assert not c.en(c.franja, np.array([10.0]), np.array([-11.0]))[0]
    assert not c.en(c.limpieza, np.array([10.0]), np.array([-11.0]))[0]
    # frente a la calle fuera de edificio (x=10, z=-9? no, z=-8..0): en z=1.5 sí entra en franja/limpieza
    assert c.en(c.franja, np.array([10.0]), np.array([1.5]))[0]

    # probar sin_flotantes: componente grande flotando sobre la calle (fuera de edificio) se quita;
    # componente flotando que toca el edificio se conserva; componente apoyado en suelo se conserva
    m = object.__new__(G.Mundo)
    m.calles = c
    p = np.array([
        # 0..3: flotante grande (6x6 = 36 m²) sobre la calle en x=[7..13], z=[-1..2], altura 655 m
        [7, 655, -1], [13, 655, -1], [13, 655, 2], [7, 655, 2],
        # 4..7: flotante que toca edificio en x=[7..13], z=[-11..-9], altura 658 m
        [7, 658, -11], [13, 658, -11], [13, 658, -8], [7, 658, -8],
        # 8..11: apoya en el suelo en x=[7..13], z=[1..3], altura 650..655 m
        [7, 650, 1], [13, 650, 1], [13, 655, 3], [7, 655, 3],
    ], float)
    tris = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7], [8, 9, 10], [8, 10, 11]])
    res = m.sin_flotantes(p, tris)
    kept = {int(t[0]) for t in res}
    assert 0 not in kept    # flotante grande sobre la calle eliminado
    assert 4 in kept        # toca edificio: conservado
    assert 8 in kept        # apoya en el suelo: conservado


def test_recortar_lleva_fachadas_a_la_pared_y_quita_objetos_enteros(tmp_path):
    from PIL import Image
    from openlaps import export_glb
    from openlaps.mesh import Scene
    sc = Scene()
    sc.images["gris"] = Image.new("RGB", (4, 4), (80, 80, 80))
    sc.textures["gris"] = None
    sc.material("asfalto", "gris")
    sc.material("fachada", "gris")
    # calzada x=[0,20], norte=[0,8] (AC z=[-8,0]); edificio con fachada en norte=12 (AC z=-12) y techo hasta 20
    pos = np.array([[0, 0, 650.0], [20, 0, 650.0], [20, 8, 650.0], [0, 8, 650.0]])
    sc.add("calzada", "asfalto", pos, pos[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]), phys="ROAD")
    pared = np.array([[2, 12, 650.0], [18, 12, 650.0], [18, 12, 665.0], [2, 12, 665.0]])
    techo = np.array([[2, 12, 665.0], [18, 12, 665.0], [18, 20, 665.0], [2, 20, 665.0]])
    sc.add("edificios", "fachada", np.r_[pared, techo], np.zeros((8, 2)),
           np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]))
    ruta = str(tmp_path / "f.glb")
    export_glb.write(sc, ruta, {})
    c = G.Calles(ruta, avisar=False)
    _, d = c.pared(np.array([[10.0, -10.0]]))
    assert abs(d[0] - 2.0) < 0.05
    m = object.__new__(G.Mundo)
    m.calles = c
    p = np.array([
        [8, 650.2, -5], [12, 650.2, -5], [10, 650.2, -2],        # 0-2: suelo de Google sobre la calzada
        [8, 660, -12.5], [12, 660, -12.5], [10, 650.7, -10],       # 3-5: fachada derretida 2 m hacia la calle
        [9, 654, 1], [11, 654, 1], [10, 656, 2.5],               # 6-8: árbol junto a la calle
        [9, 654, 2.5], [11, 654, 2.5], [10, 656, 4.5],           # 9-11: el mismo árbol, más lejos de la calle
        [9, 654, 15], [11, 654, 15], [10, 656, 17],              # 12-14: árbol lejos de la calle
    ], float)
    tris = np.arange(15).reshape(-1, 3)
    res = m.recortar(p, tris)
    kept = {int(t[0]) for t in res}
    assert kept == {3, 12}                       # suelo y árbol entero fuera; fachada y árbol lejano se quedan
    assert abs(p[5, 2] + 12.0) < 0.05            # el vértice derretido vuelve a la pared...
    assert p[5, 1] < 650.0                       # ...y baja hasta el suelo (sin hueco por debajo)
    assert p[3, 2] == -12.5                      # lo que ya estaba dentro de la planta no se mueve


def test_seleccionar_detalle_cerca_y_padres():
    class FakeGoogle:
        raiz_url = "https://t/root.json"
        def url(self, uri, base): return "https://t/" + uri
        def subarbol(self, url):
            return {
                "boundingVolume": {"box": [0, 0, 0, 300, 0, 0, 0, 300, 0, 0, 0, 300]},
                "geometricError": 100.0,
                "children": [
                    {
                        "boundingVolume": {"box": [20, 0, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]},
                        "geometricError": 1.2,  # con 0.03 a 50 m bastaría (1.5), pero con error_cerca=0.01 a 50 m (0.5) baja al hijo
                        "content": {"uri": "padre.glb"},
                        "children": [{
                            "boundingVolume": {"box": [20, 0, 0, 5, 0, 0, 0, 5, 0, 0, 0, 5]},
                            "geometricError": 0.2,
                            "content": {"uri": "hijo.glb"},
                        }],
                    },
                    {
                        "boundingVolume": {"box": [400, 0, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]},
                        "geometricError": 4.0,  # lejos (~240 m): con error=0.03 basta (7.2)
                        "content": {"uri": "lejos.glb"},
                        "children": [{
                            "boundingVolume": {"box": [400, 0, 0, 5, 0, 0, 0, 5, 0, 0, 0, 5]},
                            "geometricError": 0.2,
                            "content": {"uri": "lejos_hijo.glb"},
                        }],
                    },
                ],
            }

    padres = {}
    urls = G.seleccionar(FakeGoogle(), np.array([150.0, 0, 0]), 500.0, 0.03, 100,
                         error_cerca=0.01, radio_cerca=150.0, foco_actual=np.array([-20.0, 0, 0]), padres=padres)
    assert "https://t/hijo.glb" in urls
    assert "https://t/padre.glb" not in urls
    assert "https://t/lejos.glb" in urls
    assert G.tid("https://t/padre.glb") in padres[G.tid("https://t/hijo.glb")]


def test_marco_hilos():
    import concurrent.futures
    m = G.Marco(40.4168, -3.7038, 32630)
    ecef = G.Marco.ecef(40.4168, -3.7038, 650.0).reshape(1, 3)
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda _: (m.ac(ecef), m.lonlat(0.0, 0.0)), range(8)))
    for ac_p, (lon, lat) in res:
        assert abs(ac_p[0, 0]) < 1.0 and abs(ac_p[0, 2]) < 1.0
        assert abs(lon - (-3.7038)) < 1e-5 and abs(lat - 40.4168) < 1e-5


def test_desfase_local_suave_y_sin_falso_50(tmp_path):
    m = G.Marco(19.4328, -99.1329, 32614)
    m.registrar_ancla(np.zeros(20), np.zeros(20), np.full(20, -20.0))
    m.registrar_ancla(np.full(20, -900.0), np.full(20, 300.0), np.full(20, -10.0))
    assert float(m.desfase_en(0.0, 0.0)) < -18.5
    assert float(m.desfase_en(-900.0, 300.0)) > -11.5
    # en un tramo de edificios sin muestras cercanas no inventa 50 m
    from PIL import Image
    from openlaps import export_glb
    from openlaps.mesh import Scene
    sc = Scene()
    sc.images["g"] = Image.new("RGB", (4, 4), (80, 80, 80))
    sc.textures["g"] = None
    sc.material("asfalto", "g")
    pos = np.array([[0, 0, 2230.0], [20, 0, 2230.0], [20, 8, 2230.0], [0, 8, 2230.0]])
    sc.add("calzada", "asfalto", pos, pos[:, :2] / 3, np.array([[0, 1, 2], [0, 2, 3]]), phys="ROAD")
    ruta = str(tmp_path / "m.glb")
    export_glb.write(sc, ruta, {})
    mundo = object.__new__(G.Mundo)
    mundo.calles = G.Calles(ruta, avisar=False)
    mundo.marco = m
    ecef_lejos = G.Marco.ecef(19.50, -99.20, 2230.0).reshape(1, 3)
    assert mundo.medir_desfase([(ecef_lejos, np.zeros((1, 2)), np.array([[0, 0, 0]]), None)], (0.0, 0.0)) is None


def test_instalar_incluye_interruptor_ac(tmp_path):
    (tmp_path / "extension").mkdir()
    G.instalar(str(tmp_path), 8772)
    lua = (tmp_path / "extension" / G.LUA).read_text(encoding="utf-8")
    assert "openlaps_config" in lua and "aplicarVisible" in lua



def test_reponer_teselas_que_se_quedaron_sin_malla(monkeypatch):
    """Una tesela elegida que perdió su malla (descartada por una corrección de altura posterior) se rehace en la
    misma pasada: antes quedaba un rectángulo sin edificios hasta que el coche se movía 60 m."""
    import collections
    import concurrent.futures
    import threading
    import types
    m = object.__new__(G.Mundo)
    m.cerrojo = threading.RLock()
    m.teselas, m.mallas, m.centro_malla, m.tid_meta = collections.OrderedDict(), {}, {}, {}
    m.en_proceso, m._bajando, m.vigentes_set, m.inicial = set(), {}, set(), 7
    m.args = types.SimpleNamespace(cache=100)
    monkeypatch.setattr(G, "tid", lambda u: u.rsplit("/", 1)[-1])
    monkeypatch.setattr(G, "primitivas", lambda datos: [datos])
    pedidas = []

    def bajar(u):
        pedidas.append(u)
        f = concurrent.futures.Future()
        f.set_result(u)
        m._bajando[G.tid(u)] = f
        return f

    m._bajar = bajar
    m.convertir = lambda prims: ([{"geo": b"x", "tex": None, "centro": (0.0, 0.0)}], None)
    m.teselas["a"] = ["7_a_0"]                      # esta sigue con su malla
    urls = ["http://g/a", "http://g/b", "http://g/c"]
    assert m._reponer(urls) == 2
    assert pedidas == ["http://g/b", "http://g/c"] and list(m.teselas) == ["a", "b", "c"]
    assert m.teselas["b"] == ["7_b_0"] and "7_c_0" in m.mallas and not m.en_proceso and not m._bajando
    assert m._reponer(urls) == 0                    # nada que reponer: no vuelve a bajar nada
    assert len(pedidas) == 2


def test_faldones_bajan_al_suelo_los_bordes_en_el_aire():
    """Un trozo de malla de Google que acaba 3 m sobre el suelo se prolonga hasta el suelo; el que ya lo toca, el que
    está muy alto (un techo) y lo que cae sobre la calzada se quedan como están."""
    class Calles:
        rodada, junto_edif = "rodada", "junto_edif"

        def suelo(self, x, z):
            return np.zeros(len(np.atleast_1d(x)))

        def en(self, mascara, x, z):
            x = np.atleast_1d(x)
            return x > 100 if mascara == "rodada" else x > -50      # la calzada, lejos; edificios cerca salvo en x < -50

    m = object.__new__(G.Mundo)
    m.calles = Calles()
    m._cargados_mask = lambda xs, zs: np.ones(len(xs), bool)

    def pared(x0, y0, y1):                           # un cuadrado vertical de 2 m de ancho, de y0 a y1
        p = np.array([[x0, y0, 0], [x0 + 2, y0, 0], [x0 + 2, y1, 0], [x0, y1, 0]], float)
        return p, np.array([[0, 1, 2], [0, 2, 3]])

    p, tris = pared(0.0, 3.0, 6.0)
    uv = np.c_[p[:, 0] / 2, (p[:, 1] - 3.0) / 3.0]          # la textura sube 1/3 por metro de pared
    p2, uv2, t2 = m.faldones(p.copy(), uv, tris)
    nuevos, uv_n = p2[len(p):], uv2[len(p):]
    assert len(t2) > len(tris) and len(uv2) == len(p2) and set(np.round(nuevos[:, 0])) == {0.0, 2.0}
    assert np.isclose(p2[t2].reshape(-1, 3)[:, 1].min(), -0.3)
    # bajo la arista de abajo (3,3 m hasta el suelo): dos franjas de 1,65 m. La primera sube por la textura lo mismo
    # que baja (la pared de encima, reflejada) y la segunda vuelve al borde: nada de un solo color estirado
    medio = np.isclose(nuevos[:, 1], 3.0 - 1.65) & np.isclose(uv_n[:, 1], 1.65 / 3.0)
    abajo = np.isclose(nuevos[:, 1], -0.3) & np.isclose(uv_n[:, 1], 0.0)
    assert medio.sum() == 2 and abajo.sum() >= 2
    assert set(np.round(uv_n[medio, 0], 3)) == {0.0, 1.0}  # y a lo ancho, la misma columna de textura que arriba
    # el sentido del faldón es el de la cara de la que cuelga (misma normal, hacia +z o -z igual que la pared)
    n0 = np.cross(p[tris[0, 1]] - p[tris[0, 0]], p[tris[0, 2]] - p[tris[0, 0]])
    nf = np.cross(p2[t2[len(tris), 1]] - p2[t2[len(tris), 0]], p2[t2[len(tris), 2]] - p2[t2[len(tris), 0]])
    assert n0 @ nf > 0
    # está muy alto o cae sobre la calzada: sin faldón; y la arista que ya toca el suelo tampoco lo lleva
    for x0, y0, y1 in ((0.0, 0.1, 3.0), (0.0, 12.0, 15.0), (200.0, 3.0, 6.0), (-80.0, 3.0, 6.0)):
        q, tq = pared(x0, y0, y1)
        q2, _, tq2 = m.faldones(q.copy(), np.zeros((4, 2)), tq)
        if x0 > 100 or x0 < -50 or y0 > G.FALDON_MAX:      # calzada, lejos de todo edificio (un árbol), o un techo
            assert len(tq2) == len(tq) and len(q2) == len(q)
        else:                                       # la arista de abajo ya toca el suelo: de ella no cuelga nada
            assert len(tq2) < len(t2)


def test_faldones_solo_de_caras_verticales():
    """De una cara tumbada (un resto de copa de árbol, un toldo) no cuelga faldón aunque esté en el aire."""
    class Calles:
        rodada, junto_edif = "rodada", "junto_edif"

        def suelo(self, x, z):
            return np.zeros(len(np.atleast_1d(x)))

        def en(self, mascara, x, z):
            return np.full(len(np.atleast_1d(x)), mascara == "junto_edif")

    m = object.__new__(G.Mundo)
    m.calles = Calles()
    m._cargados_mask = lambda xs, zs: np.ones(len(xs), bool)
    p = np.array([[0, 4, 0], [2, 4, 0], [2, 4, 2], [0, 4, 2]], float)       # un cuadrado horizontal a 4 m
    tris = np.array([[0, 1, 2], [0, 2, 3]])
    p2, _, t2 = m.faldones(p.copy(), np.zeros((4, 2)), tris)
    assert len(p2) == 4 and len(t2) == 2
