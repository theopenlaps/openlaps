-- Script de pista de OpenLaps (opcional): edificios de Google 3D dentro de AC, en vivo y solo en memoria.
-- Cada segundo le dice al proceso de OpenLaps (python -m openlaps.google3d, http://127.0.0.1:8772) dónde está el
-- coche; el proceso publica qué mallas tocan alrededor y este script crea las que faltan (createMesh, texturas JPEG
-- decodificadas en memoria con ui.decodeImage) y quita las que sobran cuando las nuevas ya están. Nada se escribe en
-- disco. La física no cambia: sigue siendo la pista de OpenLaps. Se ocultan sus edificios y su horizonte (solo lo
-- visual), y solo cuando ya hay mallas de Google: si el proceso no está, la pista se ve como siempre.

local URL = 'http://127.0.0.1:8772'
local PRESUPUESTO = 0.006     -- s por cuadro creando mallas (antes 3 fijas: lento con mallas chicas, tirones con grandes)
local EN_VUELO = 48           -- peticiones de mallas a la vez

web.timeouts(nil, nil, nil, nil, 12)

local raiz = ac.findNodes('trackRoot:yes'):createNode('google_teselas', false)
-- edificios y horizonte de OpenLaps: los de la pista y, en el modo en vivo, los de los pedazos que van llegando
-- (vivo_<pedazo>_edificios_...): se vuelven a buscar cada 2 s mientras haya mallas de Google
local PROPIOS = '{edificios_?, 1WALL_edificios_?, horizonte_?, vivo_?_edificios_?, vivo_?_1WALL_edificios_?, vivo_?_horizonte_?}'
local ocultos = false
local tOcultar = -10
local function ocultarPropios()
  ac.findMeshes(PROPIOS):setVisible(false)
  ocultos = true
end

local material, creditos = nil, nil
local version = 0
local deseadas = {}           -- id -> {tex = bool}  (las de la última versión)
local orden = {}              -- sus ids, de la más cercana a la más lejana (así llegan del proceso)
local creadas = {}            -- id -> malla
local encargadas = {}         -- id -> true (pedida o en cola, aún sin crear)
local cola = {}               -- {id, geo, tex} listas para crear
local enVuelo = 0
local pidiendoLista = false
local ultimaLista = -10
local avisado = false

local function pedirLista()
  pidiendoLista = true
  local p = car and car.position or vec3()
  web.get(string.format('%s/lista?x=%.1f&z=%.1f', URL, p.x, p.z), function (err, r)
    pidiendoLista = false
    if err or not r or r.status ~= 200 then return end
    local ok, datos = pcall(JSON.parse, r.body)
    if not ok or not datos or not datos.listo or datos.version == version then return end
    version, material, creditos = datos.version, datos.material, datos.creditos
    deseadas, orden = {}, {}
    for _, m in ipairs(datos.mallas) do
      deseadas[m.id] = { tex = m.tex }
      orden[#orden + 1] = m.id
    end
  end)
end

local function pedirMalla(id, conTextura)
  encargadas[id] = true
  enVuelo = enVuelo + 1
  web.get(URL .. '/geo/' .. id, function (err, r)
    if err or not r or r.status ~= 200 then enVuelo = enVuelo - 1 encargadas[id] = nil return end
    if not conTextura then
      enVuelo = enVuelo - 1
      cola[#cola + 1] = { id = id, geo = r.body }
      return
    end
    local geo = r.body
    web.get(URL .. '/tex/' .. id, function (err2, r2)
      enVuelo = enVuelo - 1
      if err2 or not r2 or r2.status ~= 200 then encargadas[id] = nil return end
      cola[#cola + 1] = { id = id, geo = geo, tex = r2.body }
    end)
  end)
end

local function crear(item)
  -- binario de openlaps.google3d: uint32 nv, uint32 ni, nv × 8 float32 (pos, normal, uv), ni × uint16
  -- (los scripts de pista no tienen ffi.cast: se lee con string.unpack)
  local g = item.geo
  local nv, ni, at = string.unpack('<I4I4', g, 1)
  local vb = ac.VertexBuffer(nv)
  for i = 1, nv do
    local x, y, z, nx, ny, nz, u, v
    x, y, z, nx, ny, nz, u, v, at = string.unpack('<ffffffff', g, at)
    vb:set(i, ac.MeshVertex.new(vec3(x, y, z), vec3(nx, ny, nz), vec2(u, v)))
  end
  local ib = ac.IndicesBuffer(ni)
  local bloque = '<' .. string.rep('H', 48)
  local i = 1
  while i <= ni do
    if ni - i + 1 >= 48 then
      local vals = { string.unpack(bloque, g, at) }
      at = vals[49]
      for k = 1, 48 do ib:set(i + k - 1, vals[k]) end
      i = i + 48
    else
      local val
      val, at = string.unpack('<H', g, at)
      ib:set(i, val)
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
    local tam = img and ui.imageSize(img) or vec2(256, 256)
    malla:setMaterialTexture('txDiffuse', {
      textureSize = tam,
      background = false,
      callback = function ()
        display.image({ image = img, pos = vec2(0, 0), size = tam })
      end
    })
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
    if not creadas[id] and not encargadas[id] then pedirMalla(id, deseadas[id].tex) end
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

  -- quitar las que sobran solo cuando ya no falta ninguna nueva (así no se abren huecos)
  local faltan = false
  for id in pairs(deseadas) do
    if not creadas[id] then faltan = true break end
  end
  if not faltan then
    local n = 0
    for id in pairs(creadas) do
      if not deseadas[id] then quitar(id) n = n + 1 if n >= 20 then break end end
    end
    if not avisado then
      avisado = true
      ac.log(string.format('google3d: versión %d completa', version))
    end
  end
end

function script.draw3D()
  -- créditos de Google y Cesium (obligatorios): una línea discreta, centrada en la parte de abajo
  if not creditos then return end
  local s = ac.getSim()
  render.debugText(s.cameraPosition + s.cameraLook * 2 - s.cameraUp * 1.02, creditos, rgbm(1, 1, 1, 0.55), 0.55)
end
