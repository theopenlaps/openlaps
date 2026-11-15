# Using OpenLaps tracks in Assetto Corsa

Requirements: Assetto Corsa (PC) with [Custom Shaders Patch](https://acstuff.ru/patch/) (CSP).
[Content Manager](https://assettocorsa.club/content-manager.html) is recommended.

## Install a generated track

Generating with `--ac` writes a ready track folder to `salida/assetto_corsa/<name>/`. Either
copy it into `<assettocorsa>/content/tracks/`, or link it so that re-generating with the same
`--name` updates the track in place (Windows, PowerShell):

```powershell
openlaps --centro LAT,LON --tam 800 --nombre myplace --ac
New-Item -ItemType Junction -Path "<assettocorsa>\content\tracks\myplace" -Target "$PWD\salida\assetto_corsa\myplace"
```

`<assettocorsa>` is usually `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa`.
If the console fails on accented characters, set `$env:PYTHONIOENCODING='utf-8'` first.

## Drive it with Content Manager

1. **Drive → Quick Drive**, mode **Practice**.
2. Pick a car (a light road car such as the Abarth 500 or Mazda MX-5 works well) and search
   for `openlaps` in the track list.
3. **Go**. For point-to-point stages with AI, use **Race** or **Track Day** with 1-2 opponents.

After re-generating a track, press F5 in Content Manager (or restart it) so it re-reads the folder.

## Launch from a script (no UI)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\probar_ac.ps1 -Pista myplace -Coche abarth500
```

It links the track if needed, writes `race.ini` (keeping a one-time backup as
`race.ini.antes_openlaps`), starts `acs.exe`, waits for the track to load and prints the log
errors related to it. Close any running AC session first. If AC is not in the default Steam
folder, pass `-AC <folder>` or set the `OPENLAPS_AC` environment variable.

## Test checklist

1. **Loading**: opens without errors or crashes.
2. **Multilayer terrain** (`ksMultilayer_fresnel_nm`): the ground shows the aerial photo plus
   close-up detail; not black, white or pink.
3. **Surfaces**: normal grip on asphalt, vibration on cobblestones, loose gravel on dirt, slow grass.
4. **Collisions**: buildings, poles, trunks and fences are solid; no holes to fall out of the
   world and no invisible walls in the middle of the road.
5. **Junctions**: drive slowly through several intersections looking for steps or gaps.
6. **A-B timing** (stages): the timer starts at the start gantry and stops at the finish.
7. **AI** (stages): opponents follow the road without running wide or braking hard.
8. **CSP**: `ksTree` vegetation, rain puddles on asphalt, street lights at night.
9. **Performance**: check FPS with the AC or CSP performance app and note where it drops.
10. **Map and UI**: preview, outline and minimap in place.

## Logs to attach when reporting a problem

- `Documents\Assetto Corsa\logs\log.txt`: engine errors (textures, shaders, AI, surfaces)
- `Documents\Assetto Corsa\logs\custom_shaders_patch.log`: CSP and `ext_config.ini` errors

Include the part of the log from the last session and a screenshot (F8 in AC saves to
`Documents\Assetto Corsa\screens`).
