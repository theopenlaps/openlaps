-- Script de pista de OpenLaps (opcional): edificios de Google 3D dentro de AC, en vivo y solo en memoria.
-- Cada segundo le dice al proceso de OpenLaps (python -m openlaps.google3d, http://127.0.0.1:8772) dónde está el
-- coche; el proceso publica qué mallas tocan alrededor y este script crea las que faltan (createMesh, texturas JPEG
-- decodificadas en memoria con ui.decodeImage) y quita las que sobran cuando las nuevas ya están. Nada se escribe en
-- disco. La física no cambia: sigue siendo la pista de OpenLaps. Se ocultan sus edificios y su horizonte (solo lo
-- visual), y solo cuando ya hay mallas de Google: si el proceso no está, la pista se ve como siempre.

local URL = 'http://127.0.0.1:8772'
local PRESUPUESTO = 0.008     -- s por cuadro creando mallas
local EN_VUELO = 64           -- peticiones de mallas a la vez
local FMT_V4 = '<' .. string.rep('f', 32)
local FMT_I48 = '<' .. string.rep('H', 48)

web.timeouts(nil, nil, nil, nil, 12)

local raiz = ac.findNodes('trackRoot:yes'):createNode('google_teselas', false)
-- edificios y horizonte de OpenLaps: los de la pista y, en el modo en vivo, los de los pedazos que van llegando
-- (vivo_<pedazo>_edificios_...): se vuelven a buscar cada 2 s mientras haya mallas de Google
local PROPIOS = '{edificios_?, 1WALL_edificios_?, horizonte_?, vivo_?_edificios_?, vivo_?_1WALL_edificios_?, vivo_?_horizonte_?}'
local ocultos = false
local mostrar = true
local tOcultar = -10
local function ocultarPropios()
  ac.findMeshes(PROPIOS):setVisible(false)
  ocultos = true
end

local function aplicarVisible(vis)
  mostrar = vis
  raiz:setVisible(vis)
  if vis then
    if next(creadas) ~= nil then ocultarPropios() end
    ultimaLista = -10
  else
    ac.findMeshes(PROPIOS):setVisible(true)
    ocultos = false
  end
end

local shared = nil
if ac.connect and ac.StructItem then
  local ok, res = pcall(function ()
    return ac.connect({
      ac.StructItem.key('openlaps.config.v1'),
      vivoUi = ac.StructItem.boolean(),
      g3dActivo = ac.StructItem.boolean(),
      g3dMostrar = ac.StructItem.boolean(),
    }, true)
  end)
  if ok then
    shared = res
    shared.g3dActivo = true
    shared.g3dMostrar = mostrar
  end
end

if not (shared and shared.vivoUi) and ui and ui.addSettings then
  ui.addSettings({
    name = 'OpenLaps · Configuraciones',
    id = 'openlaps_config',
    category = 'main',
    icon = ui.Icons.AppWindow,
    size = { automatic = true }
  }, function ()
    if ui.checkbox('Edificios de Google 3D', mostrar) then
      aplicarVisible(not mostrar)
      if shared then shared.g3dMostrar = mostrar end
    end
    ui.textDisabled(mostrar and 'Visibles (se ocultan los de OpenLaps)' or 'Ocultos (usando edificios de OpenLaps)')
  end)
end

local material, creditos = nil, nil
local version = 0
local deseadas = {}           -- id -> {tex = bool}  (las de la última versión)
local deseadasPorTid = {}     -- tid -> {id1, id2, ...}
local tapa = {}               -- tid_viejo -> {tid_nuevo, ...} (para no coexistir ni abrir huecos)
local orden = {}              -- sus ids, de la más cercana a la más lejana (así llegan del proceso)
local creadas = {}            -- id -> malla
local encargadas = {}         -- id -> true (pedida o en cola, aún sin crear)
local cola = {}               -- {id, geo, tex} listas para crear
local enVuelo = 0
local pidiendoLista = false
local ultimaLista = -10
local avisado = false

local function tidDe(id)
  return id:match('^[^_]+_([^_]+)_')
end

local function pedirLista()
  pidiendoLista = true
  local p = car and car.position or vec3()
  web.get(string.format('%s/lista?x=%.1f&z=%.1f', URL, p.x, p.z), function (err, r)
    pidiendoLista = false
    if err or not r or r.status ~= 200 then return end
    local ok, datos = pcall(JSON.parse, r.body)
    if not ok or not datos or not datos.listo or datos.version == version then return end
    version, material, creditos = datos.version, datos.material, datos.creditos
    deseadas, deseadasPorTid, orden = {}, {}, {}
    for _, m in ipairs(datos.mallas) do
      deseadas[m.id] = { tex = m.tex }
      local t = tidDe(m.id)
      if t then
        local lst = deseadasPorTid[t]
        if not lst then lst = {} deseadasPorTid[t] = lst end
        lst[#lst + 1] = m.id
      end
      orden[#orden + 1] = m.id
    end
    for k, v in pairs(datos.tapa or {}) do
      tapa[k] = v
    end
  end)
end

local function pedirMalla(id)
  encargadas[id] = true
  enVuelo = enVuelo + 1
  web.get(URL .. '/malla/' .. id, function (err, r)
    enVuelo = enVuelo - 1
    if err or not r or r.status ~= 200 or #r.body < 8 then encargadas[id] = nil return end
    local lg = string.unpack('<I4', r.body, 1)
    local geo = r.body:sub(5, 4 + lg)
    local tex = #r.body > 4 + lg and r.body:sub(5 + lg) or nil
    cola[#cola + 1] = { id = id, geo = geo, tex = tex }
  end)
end

local function crear(item)
  -- binario de openlaps.google3d: uint32 nv, uint32 ni, nv × 8 float32 (pos, normal, uv), ni × uint16
  -- escribe directo en vb.raw / ib.raw (punteros FFI 0-based de GenericList) sin crear vec3/MeshVertex por vértice
  local g = item.geo
  local nv, ni, at = string.unpack('<I4I4', g, 1)
  local vb = ac.VertexBuffer(nv)
  local vr = vb.raw
  if vr then
    local i = 0
    while i + 4 <= nv do
      local x1, y1, z1, nx1, ny1, nz1, u1, v1,
            x2, y2, z2, nx2, ny2, nz2, u2, v2,
            x3, y3, z3, nx3, ny3, nz3, u3, v3,
            x4, y4, z4, nx4, ny4, nz4, u4, v4, nat = string.unpack(FMT_V4, g, at)
      at = nat
      vr[i].pos.x, vr[i].pos.y, vr[i].pos.z, vr[i].normal.x, vr[i].normal.y, vr[i].normal.z, vr[i].uv.x, vr[i].uv.y = x1, y1, z1, nx1, ny1, nz1, u1, v1
      vr[i + 1].pos.x, vr[i + 1].pos.y, vr[i + 1].pos.z, vr[i + 1].normal.x, vr[i + 1].normal.y, vr[i + 1].normal.z, vr[i + 1].uv.x, vr[i + 1].uv.y = x2, y2, z2, nx2, ny2, nz2, u2, v2
      vr[i + 2].pos.x, vr[i + 2].pos.y, vr[i + 2].pos.z, vr[i + 2].normal.x, vr[i + 2].normal.y, vr[i + 2].normal.z, vr[i + 2].uv.x, vr[i + 2].uv.y = x3, y3, z3, nx3, ny3, nz3, u3, v3
      vr[i + 3].pos.x, vr[i + 3].pos.y, vr[i + 3].pos.z, vr[i + 3].normal.x, vr[i + 3].normal.y, vr[i + 3].normal.z, vr[i + 3].uv.x, vr[i + 3].uv.y = x4, y4, z4, nx4, ny4, nz4, u4, v4
      i = i + 4
    end
    while i < nv do
      local x, y, z, nx, ny, nz, u, v, nat = string.unpack('<ffffffff', g, at)
      at = nat
      vr[i].pos.x, vr[i].pos.y, vr[i].pos.z, vr[i].normal.x, vr[i].normal.y, vr[i].normal.z, vr[i].uv.x, vr[i].uv.y = x, y, z, nx, ny, nz, u, v
      i = i + 1
    end
  else
    for i = 1, nv do
      local x, y, z, nx, ny, nz, u, v
      x, y, z, nx, ny, nz, u, v, at = string.unpack('<ffffffff', g, at)
      vb:set(i, ac.MeshVertex.new(vec3(x, y, z), vec3(nx, ny, nz), vec2(u, v)))
    end
  end
  local ib = ac.IndicesBuffer(ni)
  local ir = ib.raw
  local i = 1
  while i <= ni do
    if ni - i + 1 >= 48 then
      local vals = { string.unpack(FMT_I48, g, at) }
      at = vals[49]
      if ir then
        for k = 1, 48 do ir[i + k - 2] = vals[k] end
      else
        for k = 1, 48 do ib:set(i + k - 1, vals[k]) end
      end
      i = i + 48
    else
      local val
      val, at = string.unpack('<H', g, at)
      if ir then ir[i - 1] = val else ib:set(i, val) end
      i = i + 1
    end
  end
  local malla = raiz:createMesh('google_' .. item.id, material, vb, ib, false, true)
  if not malla then return nil end
  if not ocultos then ocultarPropios() end
  malla:ensureUniqueMaterials()
  -- la textura de Google ya trae su luz: poca iluminación de AC encima
  malla:setMaterialProperty('ksAmbient', 0.75)
  malla:setMaterialProperty('ksDiffuse', 0.35)
  malla:setMaterialProperty('ksSpecular', 0)
  if item.tex then
    local img = ui.decodeImage(item.tex)
    if img then malla:setMaterialTexture('txDiffuse', img) end
  end
  return malla
end

local function quitar(id)
  local m = creadas[id]
  if not m then return end
  m:dispose()
  creadas[id] = nil
end

function script.update(dt)
  if shared and shared.g3dMostrar ~= mostrar then
    aplicarVisible(shared.g3dMostrar)
  end
  if not mostrar then return end
  local t = os.clock()
  if not pidiendoLista and t - ultimaLista > 1 then
    ultimaLista = t
    pedirLista()
  end
  if not material then return end
  if ocultos and t - tOcultar > 2 then
    tOcultar = t
    ocultarPropios()
  end

  -- pedir las que faltan
  for _, id in ipairs(orden) do
    if enVuelo >= EN_VUELO then break end
    if not creadas[id] and not encargadas[id] then pedirMalla(id) end
  end

  -- crear de a poco (las que ya no se desean se descartan sin crear)
  local hechas = 0
  local t0 = os.clock()
  while #cola > 0 and (hechas == 0 or os.clock() - t0 < PRESUPUESTO) do
    local item = table.remove(cola, 1)
    encargadas[item.id] = nil
    if deseadas[item.id] and not creadas[item.id] then
      local ok, malla = pcall(crear, item)
      if ok and malla then creadas[item.id] = malla else ac.log('google3d: error ' .. tostring(malla)) end
      hechas = hechas + 1
    end
  end

  -- quitar las que sobran: cada tesela vieja se quita en cuanto están creadas todas sus sustitutas (sin esperar a
  -- toda la ciudad, así nunca coexisten burda y fina), o al salir del radio
  local function listaTid(t)
    local lst = deseadasPorTid[t]
    if not lst then return false end
    for _, mid in ipairs(lst) do
      if not creadas[mid] then return false end
    end
    return true
  end

  local n, faltan = 0, false
  for id in pairs(deseadas) do
    if not creadas[id] then faltan = true break end
  end
  for id in pairs(creadas) do
    if not deseadas[id] then
      local t = tidDe(id)
      local rep = t and tapa[t] or nil
      local puede = not faltan
      if not puede and rep then
        puede = true
        for _, nt in ipairs(rep) do
          if not listaTid(nt) then puede = false break end
        end
      elseif not puede and not rep then
        puede = true
      end
      if puede then
        quitar(id)
        if rep then tapa[t] = nil end
        n = n + 1
        if n >= 40 then break end
      end
    end
  end
  if not faltan and not avisado then
    avisado = true
    ac.log(string.format('google3d: versión %d completa', version))
  end
end

function script.draw3D()
  -- créditos de Google y Cesium (obligatorios): una línea discreta, centrada en la parte de abajo
  if not creditos or not mostrar then return end
  local s = ac.getSim()
  render.debugText(s.cameraPosition + s.cameraLook * 2 - s.cameraUp * 1.02, creditos, rgbm(1, 1, 1, 0.55), 0.55)
end
