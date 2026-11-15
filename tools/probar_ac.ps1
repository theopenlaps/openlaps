<#
Lanza una práctica de Assetto Corsa en una pista de openlaps, sin launcher ni Content Manager.

  powershell -File tools\probar_ac.ps1 -Pista tepoztlan
  powershell -File tools\probar_ac.ps1 -Pista espinazo -Coche ks_mazda_miata -Skin 00_classic_red
  powershell -File tools\probar_ac.ps1 -Pista chinchon -AC "D:\SteamLibrary\steamapps\common\assettocorsa"

- Enlaza salida\assetto_corsa\<pista> en content\tracks si aún no está (junction).
- Escribe TRACK/MODEL/SKIN en Documents\Assetto Corsa\cfg\race.ini (respaldo único en
  race.ini.antes_openlaps) y arranca acs.exe. Necesita steam_appid.txt (244210) junto a acs.exe.
- Espera a que la pista cargue y muestra los errores del log que tienen que ver con ella.
#>
param(
    [Parameter(Mandatory = $true)][string]$Pista,
    [string]$Coche = "abarth500",
    [string]$Skin = "",
    [string]$AC = $env:OPENLAPS_AC,
    [int]$Espera = 120
)
$ErrorActionPreference = "Stop"
# carpeta de AC: -AC, la variable OPENLAPS_AC o la biblioteca de Steam por defecto
if (-not $AC) { $AC = "${env:ProgramFiles(x86)}\Steam\steamapps\common\assettocorsa" }
if (-not (Test-Path (Join-Path $AC "acs.exe"))) { throw "No encuentro acs.exe en $AC. Pasa -AC <carpeta de assettocorsa> o define OPENLAPS_AC." }
$repo = Split-Path -Parent $PSScriptRoot
$docs = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Assetto Corsa"
$ri = Join-Path $docs "cfg\race.ini"
$log = Join-Path $docs "logs\log.txt"

# 1. pista enlazada
$src = Join-Path $repo "salida\assetto_corsa\$Pista"
$dst = Join-Path $AC "content\tracks\$Pista"
if (-not (Test-Path (Join-Path $src "$Pista.kn5"))) { throw "No existe $src\$Pista.kn5: genera la pista con --nombre $Pista --ac" }
if (-not (Test-Path $dst)) { New-Item -ItemType Junction -Path $dst -Target $src | Out-Null; "Enlazada: $dst" }

# 2. coche y skin
$carDir = Join-Path $AC "content\cars\$Coche"
if (-not (Test-Path (Join-Path $carDir "skins"))) { throw "El coche $Coche no está instalado completo (sin carpeta skins)" }
if (-not $Skin) { $Skin = Get-ChildItem (Join-Path $carDir "skins") -Directory -Name | Select-Object -First 1 }

# 3. cerrar sesiones anteriores (el launcher oficial reescribe race.ini al abrirse)
Get-Process acs -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process | Where-Object { $_.Path -like "*assettocorsa\AssettoCorsa.exe" } | Stop-Process -Force
Start-Sleep -Seconds 2

# 4. race.ini
# respaldo único del race.ini original
if (-not (Test-Path "$ri.antes_openlaps")) { Copy-Item $ri "$ri.antes_openlaps" }
$t = Get-Content $ri -Raw
$t = $t -replace '(?m)^TRACK=.*$', "TRACK=$Pista" -replace '(?m)^CONFIG_TRACK=.*$', 'CONFIG_TRACK=' `
        -replace '(?m)^(\[RACE\][\s\S]*?)^MODEL=.*$', "`${1}MODEL=$Coche" -replace '(?m)^SKIN=.*$', "SKIN=$Skin" `
        -replace '(?m)^SPAWN_SET=.*$', 'SPAWN_SET=START' -replace '(?m)^CARS=.*$', 'CARS=1'
[IO.File]::WriteAllText($ri, $t, (New-Object Text.ASCIIEncoding))

# 5. lanzar
$appid = Join-Path $AC "steam_appid.txt"
if (-not (Test-Path $appid)) { Set-Content -Path $appid -Value "244210" -Encoding ascii }
$t0 = Get-Date
$p = Start-Process -FilePath (Join-Path $AC "acs.exe") -WorkingDirectory $AC -PassThru
"AC lanzado (PID $($p.Id)): $Pista con $Coche/$Skin"

# 6. esperar a que cargue
$ok = $false
for ($i = 0; $i -lt $Espera; $i += 2) {
    Start-Sleep -Seconds 2
    if ($p.HasExited) { break }
    if ((Test-Path $log) -and (Get-Item $log).LastWriteTime -gt $t0) {
        $txt = Get-Content $log -Raw
        if ($txt -match "goToSpawnPosition START") { $ok = $true; break }
    }
}
if ($p.HasExited) { "AC se cerró (código $($p.ExitCode)). Últimas líneas del log:"; Get-Content $log -Tail 25; exit 1 }
if (-not $ok) { "AC sigue cargando tras $Espera s"; exit 2 }
"Pista cargada en $([int]((Get-Date) - $t0).TotalSeconds) s"
Get-Content $log | Select-String -Pattern "content/tracks/$Pista|ERROR|CRASH" |
    Where-Object { $_.Line -notmatch "Variable type cannot be found|acnotify|NO STAT FOR" } |
    Select-Object -First 30 | ForEach-Object { "  " + $_.Line }
