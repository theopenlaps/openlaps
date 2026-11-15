# Contributing to OpenLaps

Thanks for your interest! OpenLaps is at an early stage and changes fast, so issues with
concrete examples are just as valuable as code. *Contribuciones en español también son bienvenidas.*

## Reporting a problem with a track

Open an issue with:

- the exact `openlaps ...` command you ran (coordinates, size, mode, route);
- what looks wrong, with a screenshot, ideally next to a photo or street view of the real place;
- if it is an Assetto Corsa problem, the relevant part of `Documents\Assetto Corsa\logs\log.txt`
  and `custom_shaders_patch.log` (see [docs/assetto-corsa.md](docs/assetto-corsa.md)).

If the problem is wrong map data (a missing building, wrong road width, a road that doesn't
exist), the best fix is often in [OpenStreetMap](https://www.openstreetmap.org) itself, which
benefits everyone.

## Development setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check openlaps tools tests
```

Tests run offline. CI runs them on Linux and Windows with Python 3.11 and 3.12.

## Guidelines

- **Open data only.** Every dataset must have a licence that allows deriving and sharing 3D
  models, and its credit must be added to [ATTRIBUTION.md](ATTRIBUTION.md) and to the
  `ATRIBUCIONES.txt` written with each track. No Google Maps/Earth or other closed sources.
- **Deterministic output.** Randomness must come from seeds derived from the geometry, so the
  same input always produces the same track.
- **No generated tracks or game content in the repo.** Outputs (`salida*/`) are ignored; do not
  commit Assetto Corsa content.
- **New country?** Add a profile in `openlaps/regions.py` (road-marking rules, roof style,
  species, open orthophoto/elevation services) and its attribution lines.
- Keep pull requests focused. Code comments and CLI options are currently in Spanish; English
  is fine in new code and discussions.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
