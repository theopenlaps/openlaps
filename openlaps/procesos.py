"""Procesos de trabajo que no se quedan huérfanos.

Los procesos de un `ProcessPoolExecutor` esperan trabajo por una tubería. En Windows, si al proceso que los creó lo
cierran de golpe (la app al detener el modo en vivo, o al cerrarse), se quedan esperando para siempre: cada uno
reserva alrededor de 1 GB y, tras unas cuantas sesiones, el equipo se queda sin memoria (Assetto Corsa ni arranca).
`morir_con_el_padre` se pone de iniciador de esos procesos: en cuanto el padre deja de existir, salen.
"""
import os
import sys
import threading
import time


VARIABLE_APP = "OPENLAPS_PID_APP"     # la app pone aquí su PID; lo heredan los servicios que lanza


def morir_con_la_app():
    """Para los servicios que lanza la app (modo en vivo, Google 3D): si la app se cierra de golpe, salen con ella
    en vez de quedarse sirviendo a nadie. Lanzados a mano desde una terminal no cambia nada."""
    pid = os.environ.get(VARIABLE_APP, "")
    if pid.isdigit():
        morir_con_el_padre(int(pid))


def morir_con_el_padre(padre=None):
    """Lanza un hilo que termina este proceso cuando muere el que lo creó (o el de ese PID). No hace nada más."""
    padre = padre or os.getppid()

    def vigilar():
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            h = k32.OpenProcess(0x00100000, False, padre)      # SYNCHRONIZE
            if not h:
                return                                           # sin permiso para vigilarlo: como antes
            k32.WaitForSingleObject(h, 0xFFFFFFFF)
        else:
            while True:
                try:
                    os.kill(padre, 0)
                except OSError:
                    break
                time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=vigilar, daemon=True, name="vigila-padre").start()
