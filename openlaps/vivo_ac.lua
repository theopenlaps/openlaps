-- Script de pista del tramo en vivo de OpenLaps (lo escribe `python -m openlaps.vivo --semilla-ac`).
-- Le dice al servidor dónde está el coche y hacia dónde va; el servidor genera los pedazos de 256 m de alrededor
-- y este script crea los que ya están listos: primero su física (physics.addTrackSurface, una llamada por tipo
-- de superficie) y luego, poco a poco, sus mallas (createMesh con los materiales de la biblioteca que trae el
-- .kn5; el suelo con su textura horneada, decodificada en memoria). Suelta los pedazos lejanos de a poco.
-- Si el coche va a entrar en un pedazo cuya física aún no está, lo frena hasta que llegue.

local URL = '__URL__'
local HORNEADOS = { __HORNEADOS__ }   -- pedazos que ya vienen en el .kn5
local L = __L__
local ORIGEN = { __LAT__, __LON__ }   -- el servidor tiene que ser de este mismo lugar (si no, sus pedazos caerían mal)
local otroLugar = false
local zonaLista = nil      -- fracción ya generada alrededor (precalentado), la da el servidor
local R_CARGA, R_SOLTAR = 430, 640    -- m (distancia del coche al borde del pedazo), más lejos cuanto más rápido
local MAX_DESCARGAS = 3

web.timeouts(nil, nil, nil, nil, 30)

local raiz = ac.findNodes('trackRoot:yes'):createNode('openlaps_vivo', false)

-- biblioteca: una malla diminuta por material (escondida bajo tierra en el .kn5). createMesh con el nombre del
-- material no trae sus texturas (sale negro): se copia el material entero de la malla de la biblioteca.
local biblioteca = {}
do
  local refs = ac.findMeshes('biblioteca_?')
  for k = 1, refs:size() do
    local r = refs:at(k)
    local m = r:materialName(1)
    if m and not biblioteca[m] then biblioteca[m] = r end
  end
end
local pedazos = {}       -- k -> { estado = 'bajando'|'armando'|'listo', mallas = {}, fisica = {}, ... }
local listos = {}        -- k -> true (lo que el servidor ya tiene)
local basura = {}        -- cosas por soltar (ac.Disposable o mallas), una por cuadro
local descargas = 0
local pidiendo, tFoco = false, -10
local espera = false
local seguro, tSeguro, tRescate = nil, 0, -10   -- último sitio con suelo (para no caer si falta un pedazo)
local texturas = {}      -- nombre -> imagen decodificada (compartida por las mallas del mismo suelo)
local diag = { mallas = 0, fallos = 0, error = '' }   -- se manda al servidor (sale en su consola)

local function clave(i, j) return i .. '_' .. j end

local function pedazoDe(x, z)
  return math.floor(x / L + 0.5), math.floor(-z / L + 0.5)
end

local function distancia(i, j, x, z)
  local y = -z
  local x0, x1, y0, y1 = (i - 0.5) * L, (i + 0.5) * L, (j - 0.5) * L, (j + 0.5) * L
  local dx = math.max(x0 - x, 0, x - x1)
  local dy = math.max(y0 - y, 0, y - y1)
  return math.sqrt(dx * dx + dy * dy)
end

-- La física de un pedazo, una vez creada, no se quita nunca: quitar superficies de física creadas por script
-- (el ac.Disposable de physics.addTrackSurface) da errores de Embree ("invalid geometry ID") y acabó tumbando el
-- juego al alejarse. Se guardan aquí (también para que no las recoja la recolección de basura) y, si se vuelve a
-- un pedazo, se reutiliza su física y solo se crean las mallas.
local fisicaHecha = {}     -- k -> lista de ac.Disposable

local function tieneFisica(k)
  if HORNEADOS[k] or fisicaHecha[k] then return true end
  local p = pedazos[k]
  return p ~= nil and p.fisicaLista == true
end

-- ---------------------------------------------------------------- servidor
local function pedirFoco()
  pidiendo = true
  local c = ac.getCar(0)
  local q = ''
  if c then
    q = string.format('?c=ac&x=%.1f&z=%.1f&vx=%.2f&vz=%.2f', c.position.x, c.position.z, c.velocity.x, c.velocity.z)
    local n, nf = 0, 0
    for _, p in pairs(pedazos) do
      n = n + 1
      if p.fisicaLista then nf = nf + 1 end
    end
    q = q .. string.format('&ac=%d,%d,%d,%d,%s,permiso=%s', n, nf, diag.mallas, diag.fallos, espera and 'espera' or '-',
      tostring(physics.allowed()))
    if diag.error ~= '' then q = q .. '&err=' .. diag.error:gsub('[^%w%.%-_ :]', ''):gsub(' ', '+'):sub(1, 160) end
  end
  web.get(URL .. '/vivo/foco' .. q, function (err, r)
    pidiendo = false
    if err or not r or r.status ~= 200 then return end
    local ok, e = pcall(JSON.parse, r.body)
    if not ok or not e then return end
    listos = {}
    otroLugar = e.origen ~= nil and (math.abs(e.origen.lat - ORIGEN[1]) > 1e-5 or math.abs(e.origen.lon - ORIGEN[2]) > 1e-5)
    if otroLugar then return end
    zonaLista = e.zona_lista
    for _, p in ipairs(e.listos or {}) do listos[clave(p[1], p[2])] = true end
  end)
end

local function bajar(k)
  pedazos[k] = { estado = 'bajando', mallas = {}, fisica = {} }
  descargas = descargas + 1
  web.get(URL .. '/vivo/p/p_' .. k .. '.olv', function (err, r)
    descargas = descargas - 1
    local p = pedazos[k]
    if not p then return end
    if err or not r or r.status ~= 200 then pedazos[k] = nil return end
    local datos = r.body
    if datos:sub(1, 4) ~= 'OLV1' then pedazos[k] = nil return end
    local n, at = string.unpack('<I4', datos, 5)
    p.estado, p.datos, p.n, p.at, p.k = 'armando', datos, n, at, 0
    p.fv, p.fi = {}, {}       -- física acumulada por tipo de superficie: vértices (vec3) e índices
  end)
end

-- ---------------------------------------------------------------- armar
-- El armado de un pedazo es una corrutina: lee mallas hasta gastar su presupuesto del cuadro y cede (también a
-- mitad de una malla grande), así a 100 km/h el juego no da tirones. La física se crea en cuanto se han leído las
-- mallas que la tienen (el servidor las pone primero), sin esperar a edificios ni árboles.
local PRESUPUESTO = 0.004      -- s por cuadro
local reloj = os.preciseClock or os.clock
local limite = 0

local function ceder()
  if reloj() > limite then coroutine.yield() end
end

local function crearFisica(p)
  if p.fisicaLista then return end
  if fisicaHecha[p.clave] then             -- ya estaba de una visita anterior
    p.fv, p.fi, p.fisicaLista = nil, nil, true
    return
  end
  -- una superficie por tipo (cada llamada reconstruye el árbol de choques: pocas y juntas)
  for fis, v in pairs(p.fv) do
    if #p.fi[fis] > 0 then
      local ok, d = pcall(physics.addTrackSurface, v, p.fi[fis], fis)
      if ok and d then p.fisica[#p.fisica + 1] = d
      else diag.fallos, diag.error = diag.fallos + 1, 'addTrackSurface ' .. fis .. ': ' .. tostring(d) end
    end
  end
  p.fv, p.fi = nil, nil
  p.fisicaLista = true
  fisicaHecha[p.clave] = p.fisica
end

local function leerMalla(p)
  local g = p.datos
  local nombre, mat, fis, tex, nv, ni, at = string.unpack('<s2s2s2s2I4I4', g, p.at)
  if fis == '' then crearFisica(p) end      -- ya pasaron todas las mallas con física
  local visible = mat ~= 'invisible'
  local vb = visible and ac.VertexBuffer(nv) or nil
  local fv, base = nil, 0
  if fis ~= '' and not p.fisicaLista and not fisicaHecha[p.clave] then
    p.fv[fis] = p.fv[fis] or {}
    p.fi[fis] = p.fi[fis] or {}
    fv = p.fv[fis]
    base = #fv
  end
  for i = 1, nv do
    local x, y, z, nx, ny, nz, u, v
    x, y, z, nx, ny, nz, u, v, at = string.unpack('<ffffffff', g, at)
    if vb then vb:set(i, ac.MeshVertex.new(vec3(x, y, z), vec3(nx, ny, nz), vec2(u, v))) end
    if fv then fv[base + i] = vec3(x, y, z) end
    if i % 2048 == 0 then ceder() end
  end
  local ib = visible and ac.IndicesBuffer(ni) or nil
  local fi = fv and p.fi[fis] or nil
  local nfi = fi and #fi or 0
  local bloque = '<' .. string.rep('H', 48)
  local i = 1
  while i <= ni do
    if ni - i + 1 >= 48 then
      local vals = { string.unpack(bloque, g, at) }
      at = vals[49]
      for q = 1, 48 do
        if ib then ib:set(i + q - 1, vals[q]) end
        -- física: mismo orden que la malla (normal hacia +y, como pide addTrackSurface; al revés el coche cae)
        if fi then fi[nfi + i + q - 1] = vals[q] + base end
      end
      i = i + 48
      if i % 6144 < 48 then ceder() end
    else
      local val
      val, at = string.unpack('<H', g, at)
      if ib then ib:set(i, val) end
      if fi then fi[nfi + i] = val + base end
      i = i + 1
    end
  end
  p.at = at
  if not visible then return end
  local esSuelo = mat:sub(1, 3) == 'tv_'
  local malla = raiz:createMesh('vivo_' .. p.clave .. '_' .. nombre, esSuelo and 'tv_base' or mat, vb, ib, false, true)
  if not malla then
    diag.fallos = diag.fallos + 1
    diag.error = 'createMesh nil con ' .. mat
    return
  end
  diag.mallas = diag.mallas + 1
  p.mallas[#p.mallas + 1] = malla
  -- los colores exactos (fachada_c8a7f6e, isleta_cb4b4a0…) que no estén en la biblioteca: su material base
  local fuente = biblioteca[esSuelo and 'tv_base' or mat] or biblioteca[mat:match('^(.-)_c%x+$') or ''] or
    biblioteca['banqueta']
  if fuente then malla:assignMaterialFrom(fuente) else diag.error = 'sin material ' .. mat end
  if esSuelo and tex ~= '' then
    malla:ensureUniqueMaterials()
    local function poner(img)
      if not img or malla:size() == 0 then return end
      local tam = ui.imageSize(img)
      malla:setMaterialTexture('txDiffuse', {
        textureSize = tam, background = false,
        callback = function () display.image({ image = img, pos = vec2(0, 0), size = tam }) end
      })
    end
    if texturas[tex] then poner(texturas[tex]) else
      web.get(URL .. '/vivo/p/' .. tex, function (err, r)
        if err or not r or r.status ~= 200 then return end
        local img = ui.decodeImage(r.body)
        texturas[tex] = img
        poner(img)
      end)
    end
  end
  ceder()
end

local function armar(p)
  if not p.co then
    p.co = coroutine.create(function ()
      while p.k < p.n do
        local ok, e = pcall(leerMalla, p)
        if not ok then
          ac.log('vivo: error en ' .. p.clave .. ': ' .. tostring(e))
          diag.fallos, diag.error = diag.fallos + 1, tostring(e)
          break
        end
        p.k = p.k + 1
      end
      crearFisica(p)
      p.datos = nil
      p.estado = 'listo'
    end)
  end
  limite = reloj() + PRESUPUESTO
  local ok, e = coroutine.resume(p.co)
  if not ok then
    diag.fallos, diag.error = diag.fallos + 1, tostring(e)
    crearFisica(p)
    p.estado = 'listo'
  end
end

local function soltar(k)
  local p = pedazos[k]
  if not p then return end
  for _, m in ipairs(p.mallas) do basura[#basura + 1] = m end
  pedazos[k] = nil
end


-- ---------------------------------------------------------------- piloto automático (opcional)
-- Para probar sin manos: si el servidor tiene una ruta de prueba activa (python -m openlaps.vivo ... --piloto-ac
-- 4000, o /vivo/piloto?metros=4000&kmh=100), el script conduce: volante, acelerador, freno y primera marcha al
-- arrancar (luego el cambio automático de AC). Baja la velocidad antes de cada curva. Todo dentro de este script.
local piloto = nil            -- { id, puntos = {{x, z}, ...}, kmh, i, fase, t, signo, r0 }
local idPiloto, pidiendoPiloto, tPiloto = 0, false, -10

local function angulo(a) return (a + math.pi) % (2 * math.pi) - math.pi end

local function pedirPiloto()
  pidiendoPiloto = true
  web.get(URL .. '/vivo/piloto?tengo=' .. idPiloto, function (err, r)
    pidiendoPiloto = false
    if err or not r or r.status ~= 200 then return end
    local ok, e = pcall(JSON.parse, r.body)
    if not ok or not e or e.id == idPiloto then return end
    idPiloto = e.id
    if e.id == 0 or not e.puntos then
      piloto = nil
      physics.overrideSteering(0, math.nan)
      return
    end
    piloto = { puntos = e.puntos, kmh = e.kmh or 50, i = 1, fase = 'arranque', t = 0, signo = 1 }
    ac.log('vivo: piloto con ruta de ' .. (#e.puntos * 2) .. ' m')
  end)
end

local function soltarMandos()
  physics.overrideSteering(0, math.nan)
  physics.forceUserThrottleFor(0, 0)
end

local function conducir(c, dt, esperando)
  local P = piloto
  local v = c.velocity
  local rapidez = math.sqrt(v.x * v.x + v.z * v.z)
  local rumbo = math.atan2(v.x, -v.z)
  P.t = P.t + dt
  local function mandos(gas, freno, giro)
    if esperando then gas = 0 end
    if gas > 0 then physics.forceUserThrottleFor(0.15, gas) else physics.forceUserThrottleFor(0, 0) end
    if freno > 0 then physics.forceUserBrakesFor(0.15, freno) end
    physics.overrideSteering(0, giro)
  end
  if P.fase == 'arranque' then
    -- primera y acelerar recto hasta moverse
    if P.t < 0.1 then physics.engageGear(0, 1) end
    mandos(0.5, 0, 0)
    if rapidez > 3 then P.fase, P.t, P.r0 = 'calibrar', 0, rumbo end
    return
  end
  if P.fase == 'calibrar' then
    -- volante positivo un momento: ¿hacia qué lado gira?
    mandos(0.3, 0, 0.4)
    if P.t > 0.8 then
      P.signo = angulo(rumbo - P.r0) > 0 and 1 or -1
      P.fase = 'seguir'
      ac.log('vivo: piloto, volante positivo = ' .. (P.signo > 0 and 'derecha' or 'izquierda'))
    end
    return
  end
  local R = P.puntos
  local n = #R
  local x, z = c.position.x, c.position.z
  local function d2(k) local dx, dz = R[k][1] - x, R[k][2] - z return dx * dx + dz * dz end
  local mejor, md = P.i, math.huge
  for k = P.i, math.min(n, P.i + 60) do
    local d = d2(k)
    if d < md then mejor, md = k, d end
  end
  P.i = mejor
  if P.fase == 'parar' or md > 40 * 40 or P.i >= n - 2 then
    if P.fase ~= 'parar' then
      ac.log('vivo: piloto, ' .. (md > 1600 and 'me salí de la ruta' or 'fin de la ruta'))
      P.fase, P.t = 'parar', 0
    end
    mandos(0, 0.8, 0)
    if P.t > 3 then soltarMandos() piloto = nil end
    return
  end
  -- mirar más lejos cuanto más rápido (puntos cada 2 m)
  local j = math.min(n, P.i + math.floor((10 + rapidez * 0.8) / 2))
  local err = angulo(math.atan2(R[j][1] - x, -(R[j][2] - z)) - rumbo)
  local giro = math.max(-1, math.min(1, P.signo * err * (rapidez < 15 and 1.6 or 1.0)))
  -- velocidad: la máxima con la que se toma cada curva de lo que viene (3.5 m/s² de lateral), contando la
  -- distancia para frenar hasta ella (4 m/s²)
  local function rumboRuta(k)
    local a, b = R[math.min(n, k)], R[math.min(n, k + 8)]
    return math.atan2(b[1] - a[1], -(b[2] - a[2]))
  end
  local objetivo = P.kmh / 3.6
  local alcance = math.min(n - P.i, math.floor((rapidez * rapidez / 8 + 40) / 2))
  for k = 0, alcance, 4 do
    local da = math.abs(angulo(rumboRuta(P.i + k + 8) - rumboRuta(P.i + k)))
    local vCurva = math.sqrt(3.5 * 16 / math.max(da, 1e-3))
    objetivo = math.min(objetivo, math.sqrt(vCurva * vCurva + 2 * 4 * k * 2))
  end
  if math.abs(err) > 0.6 then objetivo = math.min(objetivo, 6) end
  objetivo = math.max(4, objetivo)
  local gas = math.max(0, math.min(1, (objetivo - rapidez) * 0.3))
  local freno = math.max(0, math.min(0.8, (rapidez - objetivo - 1.5) * 0.25))
  mandos(gas, freno, giro)
end

-- ---------------------------------------------------------------- ciclo
function script.update(dt)
  local t = os.clock()
  if not pidiendo and t - tFoco > 0.5 then
    tFoco = t
    pedirFoco()
  end
  local c = ac.getCar(0)
  if not c then return end
  local x, z = c.position.x, c.position.z
  local v = c.velocity
  local rapidez = math.sqrt(v.x * v.x + v.z * v.z)
  local ax4, az4 = x + v.x * 4, z + v.z * 4          -- dónde estará en ~4 s: lo de delante va primero
  local function urgencia(k)
    local i, j = k:match('^(-?%d+)_(-?%d+)$')
    i, j = tonumber(i), tonumber(j)
    return math.min(distancia(i, j, x, z), distancia(i, j, ax4, az4) * 0.8)
  end

  -- pedir los listos cercanos, el más urgente primero
  if descargas < MAX_DESCARGAS then
    local mejor, md = nil, R_CARGA + rapidez * 8
    for k in pairs(listos) do
      if not HORNEADOS[k] and not pedazos[k] then
        local d = urgencia(k)
        if d < md then mejor, md = k, d end
      end
    end
    if mejor then bajar(mejor) pedazos[mejor].clave = mejor end
  end

  -- armar el que esté a medias (uno a la vez, el más urgente; el que ya empezó sigue si no hay uno bastante más
  -- urgente, para no dejar varios a medias)
  local actual, ad = nil, math.huge
  for k, p in pairs(pedazos) do
    if p.estado == 'armando' then
      local d = urgencia(k) - (p.co and 60 or 0)
      if d < ad then actual, ad = p, d end
    end
  end
  if actual then armar(actual) end

  -- soltar lejanos
  for k, p in pairs(pedazos) do
    if p.estado ~= 'bajando' and not (p.co and p.estado == 'armando') then
      local i, j = k:match('^(-?%d+)_(-?%d+)$')
      if distancia(tonumber(i), tonumber(j), x, z) > R_SOLTAR + rapidez * 8 then soltar(k) end
    end
  end
  if #basura > 0 then
    local b = table.remove(basura)
    if type(b) == 'function' or (type(b) == 'table' and getmetatable(b) and getmetatable(b).__call) then pcall(b)
    elseif b and b.dispose then pcall(b.dispose, b) else pcall(b) end
  end

  -- esperar al terreno: si en lo que se recorre en 1.5 s más la frenada no hay física, frenar. La frenada se
  -- calcula para parar antes del pedazo que falta y se impone con la velocidad (el acelerador del jugador o del
  -- piloto automático no pueden con ella); pegado al borde, el coche queda parado.
  local alcance = rapidez * 1.5 + rapidez * rapidez / 14 + 8
  local falta = nil
  espera = false
  if rapidez > 0.5 then
    for d = 0, alcance, 5 do
      local t = d / rapidez
      local i2, j2 = pedazoDe(x + v.x * t, z + v.z * t)
      if not tieneFisica(clave(i2, j2)) then falta = d break end
    end
  end
  local i1, j1 = pedazoDe(x, z)
  local aqui = tieneFisica(clave(i1, j1))
  espera = falta ~= nil or not aqui
  if falta and rapidez > 0.3 then
    local margen = math.max(0.5, falta - 12)
    local frenada = math.max(4, rapidez * rapidez / (2 * margen) * 1.3)
    local nueva = falta < 15 and 0 or math.max(0, rapidez - frenada * dt)
    physics.forceUserBrakesFor(0.2, 1)
    physics.setCarVelocity(0, v * (nueva / rapidez))
  end
  -- red de seguridad: si aun así el coche está en un pedazo sin física (se caería y AC lo mandaría a boxes), se
  -- le devuelve parado al último sitio seguro
  tSeguro = tSeguro + dt
  if aqui and not falta and tSeguro > 0.5 and math.abs(v.y) < 3 then
    tSeguro = 0
    local dir = rapidez > 1 and vec3(v.x, 0, v.z):normalize() or (seguro and seguro.dir or c.look)
    seguro = { pos = c.position:clone(), dir = dir }
  elseif seguro and os.clock() - tRescate > 2 and (not aqui
      -- o cayendo por un hueco de la física aunque el pedazo exista: muy por debajo del último sitio con suelo
      or (c.position.y < seguro.pos.y - 6 and v.y < -6)) then
    tRescate = os.clock()
    -- setCarPosition deja el coche mirando a -dir (medido en AC): se le pasa al revés
    physics.setCarPosition(0, seguro.pos + vec3(0, 0.3, 0), -seguro.dir)
    physics.setCarVelocity(0, vec3(0, 0, 0))
  end

  -- piloto automático
  if not pidiendoPiloto and t - tPiloto > 2 then
    tPiloto = t
    pedirPiloto()
  end
  if piloto then conducir(c, dt, espera) end
end

function script.draw3D()
  local s = ac.getSim()
  if otroLugar then
    render.debugText(s.cameraPosition + s.cameraLook * 3 + s.cameraUp * 0.6,
      'el servidor del tramo en vivo es de otro lugar: arrancalo con el mismo --centro que esta pista',
      rgbm(1, 0.5, 0.3, 0.9), 0.8)
    return
  end
  if not espera then
    -- parado: cuánto falta de precalentar alrededor (con todo listo se puede ir a 100 en cualquier dirección)
    local c = ac.getCar(0)
    if zonaLista and zonaLista < 0.97 and c and c.speedKmh < 3 then
      render.debugText(s.cameraPosition + s.cameraLook * 3 + s.cameraUp * 0.6,
        string.format('preparando la zona: %d %%', math.floor(zonaLista * 100)), rgbm(0.7, 0.9, 1, 0.8), 0.8)
    end
    return
  end
  render.debugText(s.cameraPosition + s.cameraLook * 3 + s.cameraUp * 0.6, 'generando el terreno de delante...',
    rgbm(1, 0.85, 0.2, 0.9), 0.9)
end
