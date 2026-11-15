<#
Compila la versión ligera de OpenLaps para Windows: un OpenLaps.exe de pocos KB más el código
(~1 MB). La primera vez que se abre descarga Python y las librerías (ver lanzador\OpenLaps.cs).

  powershell -ExecutionPolicy Bypass -File packaging\compilar_ligero.ps1

Sale en dist\OpenLaps\ y dist\OpenLaps-<versión>-windows.zip. Solo usa herramientas que ya trae
Windows (csc.exe de .NET Framework 4); para regenerar el icono hace falta Pillow (packaging\icono.py).
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$version = (Select-String -Path "$repo\openlaps\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$dist = Join-Path $repo "dist"
$dest = Join-Path $dist "OpenLaps"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "No encuentro csc.exe de .NET Framework 4 en $csc" }
$ico = Join-Path $repo "openlaps\gui\openlaps.ico"
if (-not (Test-Path $ico)) { throw "Falta $ico (genéralo con: python packaging\icono.py)" }

Write-Host "OpenLaps $version -> $dest"
# se vacía el contenido y no la carpeta: si OpenLaps está abierto desde aquí, la carpeta es su
# directorio de trabajo y Windows no deja borrarla
New-Item -ItemType Directory -Force $dest | Out-Null
Get-ChildItem -Force $dest | Remove-Item -Recurse -Force

# 1. lanzador
$fw = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
& $csc /nologo /optimize+ /target:winexe /platform:x64 "/out:$dest\OpenLaps.exe" "/win32icon:$ico" `
    "/reference:$fw\System.Windows.Forms.dll" "/reference:$fw\System.Drawing.dll" `
    "/reference:$fw\System.IO.Compression.dll" "/reference:$fw\System.IO.Compression.FileSystem.dll" `
    "$repo\packaging\lanzador\OpenLaps.cs"
if ($LASTEXITCODE -ne 0) { throw "csc falló" }

# 2. código: el paquete openlaps (sin cachés), el visor y lo que se reparte con él
robocopy "$repo\openlaps" "$dest\openlaps" /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy "$repo\viewer" "$dest\viewer" /E /NFL /NDL /NJH /NJS /NP | Out-Null
Copy-Item "$repo\packaging\requisitos-app.txt", "$repo\LICENSE", "$repo\ATTRIBUTION.md", "$repo\README.md", "$repo\README.es.md" $dest

# 3. zip
$zip = Join-Path $dist "OpenLaps-$version-windows.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path $dest -DestinationPath $zip -CompressionLevel Optimal
$exeKb = [math]::Round((Get-Item "$dest\OpenLaps.exe").Length / 1KB)
$zipKb = [math]::Round((Get-Item $zip).Length / 1KB)
Write-Host "Listo: OpenLaps.exe $exeKb KB, zip $zipKb KB"
Write-Host $zip
