"""Ventana sin marco con la barra de título dibujada por la propia app, pero con el comportamiento
nativo de Windows: arrastrar, Snap Layouts, doble clic para maximizar, bordes para redimensionar,
sombra y esquinas redondeadas.

pywebview mueve las ventanas sin marco desde JavaScript (sin Snap ni redimensionado). Aquí, al pulsar
sobre la barra o un borde, se le cede el ratón al sistema con WM_NCLBUTTONDOWN, como hacen Tauri o
VS Code: desde ese momento Windows mueve o redimensiona la ventana como cualquier otra.
"""
import ctypes

_u32 = ctypes.windll.user32
_dwm = ctypes.windll.dwmapi
WM_NCLBUTTONDOWN = 0x00A1
HT = {"arrastrar": 2, "w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}


class Marco:
    def __init__(self, ventana):
        self.ventana = ventana

    @property
    def _form(self):
        return self.ventana.native

    def _en_ui(self, f):
        """Ejecuta f en el hilo de la ventana (WinForms no admite otro)."""
        from System import Action
        form = self._form
        if form is None:
            return None
        res = []
        form.Invoke(Action(lambda: res.append(f(form))))
        return res[0] if res else None

    def preparar(self):
        """Al mostrarse: esquinas redondeadas, borde del color de la app y límites al maximizar."""
        def f(form):
            hwnd = ctypes.c_void_p(form.Handle.ToInt64())
            for attr, valor in ((33, 2),               # DWMWA_WINDOW_CORNER_PREFERENCE = redondeadas
                                (34, 0x00463F3A)):     # DWMWA_BORDER_COLOR (0x00BBGGRR) = #3a3f46
                v = ctypes.c_int(valor)
                _dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
            self._limites(form)
            form.Move += lambda s, e: self._limites(s)
        self._en_ui(f)

    @staticmethod
    def _limites(form):
        # una ventana sin marco maximizada taparía la barra de tareas: se limita al área de trabajo
        from System.Drawing import Rectangle
        from System.Windows.Forms import Screen
        pantalla = Screen.FromHandle(form.Handle)
        wa, b = pantalla.WorkingArea, pantalla.Bounds
        form.MaximizedBounds = Rectangle(wa.X - b.X, wa.Y - b.Y, wa.Width, wa.Height)

    def estado(self):
        def f(form):
            from System.Windows.Forms import FormWindowState
            return {"marco": True, "maximizada": form.WindowState == FormWindowState.Maximized}
        return self._en_ui(f) or {"marco": False}

    def accion(self, que):
        if que == "cerrar":
            self.ventana.destroy()
            return {"ok": True}

        def f(form):
            from System.Windows.Forms import FormWindowState
            if que == "minimizar":
                form.WindowState = FormWindowState.Minimized
            elif que == "maximizar":
                self._limites(form)
                form.WindowState = (FormWindowState.Normal if form.WindowState == FormWindowState.Maximized
                                    else FormWindowState.Maximized)
            elif que in HT:
                _u32.ReleaseCapture()
                _u32.PostMessageW(ctypes.c_void_p(form.Handle.ToInt64()), WM_NCLBUTTONDOWN, HT[que], 0)
            return {"marco": True, "maximizada": form.WindowState == FormWindowState.Maximized}
        return self._en_ui(f) or {"ok": False}
