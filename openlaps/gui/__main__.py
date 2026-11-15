import sys

if sys.stdout is None or sys.stderr is None:
    # con pythonw (el lanzador OpenLaps.exe) no hay consola: los errores van a un archivo
    from .ajustes import DATOS
    DATOS.mkdir(parents=True, exist_ok=True)
    _log = open(DATOS / "openlaps.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stderr = _log

from .app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
