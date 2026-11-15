// Lanzador ligero de OpenLaps para Windows.
//
// Pesa unos KB: la primera vez descarga uv (verificado con SHA-256), con él un Python 3.12
// propio y las librerías de requisitos-app.txt, todo en %LOCALAPPDATA%\OpenLaps\runtime.
// Las siguientes veces solo abre la app (pythonw -m openlaps.gui).
//
//   OpenLaps.exe            abre la app (instala lo que falte)
//   OpenLaps.exe --reparar  borra el entorno y lo vuelve a instalar
//
// Se compila con el csc.exe de .NET Framework 4 que ya trae Windows (packaging\compilar_ligero.ps1).
using System;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: System.Reflection.AssemblyTitle("OpenLaps")]
[assembly: System.Reflection.AssemblyProduct("OpenLaps")]
[assembly: System.Reflection.AssemblyDescription("Generador abierto de pistas 3D a partir de datos abiertos")]

static class Lanzador
{
    // uv fijado a una versión concreta; la suma es la que publica astral-sh en la release
    const string UvVersion = "0.13.6";
    const string UvSha256 = "a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f";
    const string PythonVersion = "3.12";
    const string WebView2Url = "https://developer.microsoft.com/microsoft-edge/webview2/";

    static readonly bool Es = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "es";
    static string T(string es, string en) { return Es ? es : en; }

    static string Base { get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'); } }
    static string Runtime { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenLaps", "runtime"); } }
    static string Env { get { return Path.Combine(Runtime, "env"); } }
    static string Requisitos { get { return Path.Combine(Base, "requisitos-app.txt"); } }
    static string Sello { get { return Path.Combine(Env, ".openlaps-instalado"); } }

    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool reparar = Array.Exists(args, a => a == "--reparar" || a == "--repair");

        if (!File.Exists(Path.Combine(Base, "openlaps", "gui", "app.py")) || !File.Exists(Requisitos))
        {
            Error(T("Faltan archivos del programa junto a OpenLaps.exe. Vuelve a descomprimir el .zip completo.",
                    "Program files are missing next to OpenLaps.exe. Extract the whole .zip again."));
            return 2;
        }
        if (!HayWebView2())
        {
            var r = MessageBox.Show(T("OpenLaps necesita Microsoft Edge WebView2 (viene con Windows 11). ¿Abrir la página de descarga?",
                                      "OpenLaps needs Microsoft Edge WebView2 (included with Windows 11). Open the download page?"),
                                    "OpenLaps", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (r == DialogResult.Yes) Process.Start(WebView2Url);
            return 3;
        }
        if (reparar || !Instalado())
        {
            var f = new Instalador(reparar);
            Application.Run(f);
            if (!f.Ok) return 1;
        }
        return Abrir() ? 0 : 1;
    }

    static string HuellaRequisitos()
    {
        using (var sha = SHA256.Create())
        {
            var txt = File.ReadAllText(Requisitos) + "|" + PythonVersion + "|" + UvVersion;
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(txt))).Replace("-", "");
        }
    }

    static bool Instalado()
    {
        try
        {
            return File.Exists(Path.Combine(Env, "Scripts", "pythonw.exe")) && File.Exists(Sello)
                && File.ReadAllText(Sello).Trim() == HuellaRequisitos();
        }
        catch (IOException) { return false; }
    }

    static bool HayWebView2()
    {
        const string id = @"\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
        foreach (var k in new[] { @"HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node" + id, @"HKEY_LOCAL_MACHINE\SOFTWARE" + id, @"HKEY_CURRENT_USER\Software" + id })
        {
            var v = Registry.GetValue(k, "pv", null) as string;
            if (!string.IsNullOrEmpty(v) && v != "0.0.0.0") return true;
        }
        return false;
    }

    static bool Abrir()
    {
        var psi = new ProcessStartInfo(Path.Combine(Env, "Scripts", "pythonw.exe"), "-m openlaps.gui")
        {
            WorkingDirectory = Base,
            UseShellExecute = false,
        };
        psi.EnvironmentVariables["PYTHONPATH"] = Base;
        psi.EnvironmentVariables["OPENLAPS_LANZADOR"] = Path.Combine(Base, "OpenLaps.exe");
        try { Process.Start(psi); return true; }
        catch (Exception e)
        {
            Error(T("No se pudo abrir OpenLaps: ", "Couldn't open OpenLaps: ") + e.Message
                + "\n\n" + T("Prueba con OpenLaps.exe --reparar", "Try OpenLaps.exe --repair"));
            return false;
        }
    }

    static void Error(string msg) { MessageBox.Show(msg, "OpenLaps", MessageBoxButtons.OK, MessageBoxIcon.Error); }

    // ------------------------------------------------------------------ ventana de instalación
    // Dibujada a mano con la misma estética que la app (asfalto oscuro y amarillo): barra de título
    // oscura, icono y nombre, lista de pasos con su estado, barra de progreso con porcentaje y el
    // registro técnico escondido tras «Ver detalles».
    sealed class Instalador : Form
    {
        [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int valor, int tam);

        static readonly Color Fondo = Color.FromArgb(21, 23, 26), Panel2 = Color.FromArgb(31, 34, 38),
            Linea = Color.FromArgb(58, 63, 70), Texto = Color.FromArgb(236, 234, 228), Texto2 = Color.FromArgb(169, 173, 179),
            Texto3 = Color.FromArgb(125, 131, 139), Amarillo = Color.FromArgb(232, 178, 30), Rojo = Color.FromArgb(226, 96, 79);

        public bool Ok;
        readonly bool reparar;
        readonly string[] nombres;
        readonly string[] detalles = new string[4];
        readonly double[] pesos = { 0.04, 0.12, 0.78, 0.06 };   // parte de la barra de cada paso
        int pasoActual = -1;
        double avancePaso;          // 0-1 dentro del paso actual
        bool fallo;
        string error = "";
        int fase;                   // animación
        int total, descargados;     // paquetes de uv
        readonly Lienzo lienzo = new Lienzo();
        readonly TextBox registro = new TextBox();
        readonly Boton cancelar = new Boton();
        readonly LinkLabel detallesLink = new LinkLabel();
        readonly System.Windows.Forms.Timer reloj = new System.Windows.Forms.Timer { Interval = 33 };
        readonly Icon icono;
        volatile bool cancelado;
        Process actual;

        sealed class Lienzo : Control
        {
            public Lienzo() { SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.ResizeRedraw, true); }
        }

        sealed class Boton : Button
        {
            public Boton()
            {
                FlatStyle = FlatStyle.Flat;
                FlatAppearance.BorderColor = Color.FromArgb(75, 82, 91);
                FlatAppearance.MouseOverBackColor = Color.FromArgb(41, 45, 50);
                FlatAppearance.MouseDownBackColor = Color.FromArgb(50, 55, 61);
                BackColor = Color.FromArgb(31, 34, 38);
                ForeColor = Color.FromArgb(236, 234, 228);
                Cursor = Cursors.Hand;
            }
        }

        public Instalador(bool reparar)
        {
            this.reparar = reparar;
            nombres = new[] {
                T("Instalador", "Installer"),
                "Python " + PythonVersion,
                T("Librerías del generador", "Generator libraries"),
                T("Comprobación", "Check"),
            };
            Text = "OpenLaps";
            try { icono = Icon.ExtractAssociatedIcon(Application.ExecutablePath); Icon = icono; } catch { }
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(520, 352);
            BackColor = Fondo;
            ForeColor = Texto;
            Font = new Font("Segoe UI", 9.75f);

            lienzo.SetBounds(0, 0, 520, 300);
            lienzo.BackColor = Fondo;
            lienzo.Paint += Pintar;

            detallesLink.Text = T("Ver detalles", "Show details");
            detallesLink.LinkColor = Texto3;
            detallesLink.ActiveLinkColor = Amarillo;
            detallesLink.LinkBehavior = LinkBehavior.HoverUnderline;
            detallesLink.SetBounds(28, 314, 200, 22);
            detallesLink.LinkClicked += delegate { AlternarDetalles(); };

            cancelar.Text = T("Cancelar", "Cancel");
            cancelar.SetBounds(392, 308, 100, 32);
            cancelar.Click += delegate { if (fallo) Close(); else Cancelar(); };

            registro.Multiline = true;
            registro.ReadOnly = true;
            registro.ScrollBars = ScrollBars.Vertical;
            registro.BackColor = Color.FromArgb(14, 16, 18);
            registro.ForeColor = Texto2;
            registro.BorderStyle = BorderStyle.None;
            registro.Font = new Font("Consolas", 8.5f);
            registro.SetBounds(28, 352, 464, 150);
            registro.Visible = false;

            Controls.AddRange(new Control[] { lienzo, detallesLink, cancelar, registro });
            reloj.Tick += delegate { fase++; lienzo.Invalidate(); };
            FormClosing += delegate (object s, FormClosingEventArgs e) { reloj.Stop(); if (!Ok) Cancelar(); };
            Shown += delegate { reloj.Start(); new Thread(Trabajar) { IsBackground = true }.Start(); };
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            int si = 1;   // barra de título oscura (Windows 10 20H1+ y Windows 11)
            try { if (DwmSetWindowAttribute(Handle, 20, ref si, 4) != 0) DwmSetWindowAttribute(Handle, 19, ref si, 4); } catch { }
        }

        void AlternarDetalles()
        {
            registro.Visible = !registro.Visible;
            ClientSize = new Size(520, registro.Visible ? 516 : 352);
            detallesLink.Text = registro.Visible ? T("Ocultar detalles", "Hide details") : T("Ver detalles", "Show details");
        }

        double Progreso()
        {
            double p = 0;
            for (int i = 0; i < pesos.Length; i++)
                if (i < pasoActual) p += pesos[i];
                else if (i == pasoActual) p += pesos[i] * Math.Max(0, Math.Min(1, avancePaso));
            return Ok ? 1 : p;
        }

        void Pintar(object sender, PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            using (var fTitulo = new Font("Segoe UI Semibold", 17f))
            using (var fSub = new Font("Segoe UI", 9.75f))
            using (var fPaso = new Font("Segoe UI", 10f))
            using (var fPasoAct = new Font("Segoe UI Semibold", 10f))
            using (var fChico = new Font("Segoe UI", 8.75f))
            using (var bTexto = new SolidBrush(Texto))
            using (var bTexto2 = new SolidBrush(Texto2))
            using (var bTexto3 = new SolidBrush(Texto3))
            using (var bAmarillo = new SolidBrush(Amarillo))
            using (var bRojo = new SolidBrush(Rojo))
            using (var pLinea = new Pen(Linea, 1.5f))
            using (var pAmarillo = new Pen(Amarillo, 2.2f))
            {
                // cabecera
                if (icono != null) g.DrawIcon(new Icon(icono, 48, 48), new Rectangle(28, 26, 44, 44));
                g.DrawString("OpenLaps", fTitulo, bTexto, 84, 22);
                g.DrawString(fallo ? T("No se pudo terminar la preparación", "Setup couldn't finish")
                                   : reparar ? T("Reinstalando lo necesario", "Reinstalling what's needed")
                                             : T("Preparando el programa · solo la primera vez", "Getting ready · first run only"),
                             fSub, fallo ? bRojo : bTexto2, 86, 54);

                // pasos
                int y = 100;
                for (int i = 0; i < nombres.Length; i++)
                {
                    var c = new Rectangle(30, y + 2, 16, 16);
                    bool hecho = i < pasoActual || Ok, actualP = i == pasoActual && !Ok;
                    if (hecho)
                    {
                        g.FillEllipse(bAmarillo, c);
                        using (var pCheck = new Pen(Color.FromArgb(26, 21, 0), 2f))
                            g.DrawLines(pCheck, new[] { new PointF(c.X + 4.2f, c.Y + 8.4f), new PointF(c.X + 7f, c.Y + 11.2f), new PointF(c.X + 12f, c.Y + 5.4f) });
                    }
                    else if (actualP && fallo)
                    {
                        g.FillEllipse(bRojo, c);
                        g.DrawString("!", fPasoAct, bTexto, c.X + 4.5f, c.Y - 2);
                    }
                    else if (actualP)
                    {
                        g.DrawEllipse(pLinea, c);
                        g.DrawArc(pAmarillo, c, (fase * 9) % 360, 100);
                    }
                    else g.DrawEllipse(pLinea, c);
                    g.DrawString(nombres[i], actualP ? fPasoAct : fPaso, hecho || actualP ? bTexto : bTexto3, 58, y);
                    if (!string.IsNullOrEmpty(detalles[i]))
                    {
                        var sz = g.MeasureString(detalles[i], fChico);
                        g.DrawString(detalles[i], fChico, bTexto3, 492 - sz.Width, y + 2);
                    }
                    y += 30;
                }

                // barra de progreso
                var barra = new Rectangle(28, 236, 464, 8);
                using (var bFondo = new SolidBrush(Panel2)) Redondeado(g, bFondo, barra, 4);
                double p = Progreso();
                if (p > 0)
                {
                    var lleno = new Rectangle(barra.X, barra.Y, Math.Max(8, (int)(barra.Width * p)), barra.Height);
                    Redondeado(g, fallo ? bRojo : bAmarillo, lleno, 4);
                    if (!fallo && !Ok)
                    {
                        // brillo que recorre la parte llena: se nota que sigue trabajando
                        int x = lleno.X + (fase * 6) % Math.Max(1, lleno.Width + 80) - 80;
                        var brillo = new Rectangle(x, lleno.Y, 80, lleno.Height);
                        using (var lg = new System.Drawing.Drawing2D.LinearGradientBrush(brillo, Color.FromArgb(0, 255, 255, 255), Color.FromArgb(0, 255, 255, 255), 0f))
                        {
                            var cb = new System.Drawing.Drawing2D.ColorBlend(3);
                            cb.Colors = new[] { Color.FromArgb(0, 255, 255, 255), Color.FromArgb(110, 255, 255, 255), Color.FromArgb(0, 255, 255, 255) };
                            cb.Positions = new[] { 0f, 0.5f, 1f };
                            lg.InterpolationColors = cb;
                            g.SetClip(lleno);
                            g.FillRectangle(lg, brillo);
                            g.ResetClip();
                        }
                    }
                }
                string pct = ((int)Math.Round(p * 100)) + " %";
                string estado = fallo ? error : T("Descarga única de ~250 MB en tu carpeta de usuario; no toca el resto del sistema.",
                                                  "One-time ~250 MB download into your user folder; nothing else on the system is touched.");
                g.DrawString(pct, fPasoAct, bTexto, 28, 252);
                g.DrawString(estado, fChico, fallo ? bRojo : bTexto3, new RectangleF(76, 254, 418, 40));
            }
        }

        static void Redondeado(Graphics g, Brush b, Rectangle r, int radio)
        {
            using (var path = new System.Drawing.Drawing2D.GraphicsPath())
            {
                int d = radio * 2;
                path.AddArc(r.X, r.Y, d, d, 180, 90);
                path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
                path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
                path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
                path.CloseFigure();
                g.FillPath(b, path);
            }
        }

        void Cancelar()
        {
            cancelado = true;
            try { if (actual != null && !actual.HasExited) actual.Kill(); } catch { }
        }

        void Ui(Action a) { if (IsHandleCreated && !IsDisposed) BeginInvoke(a); }
        void Paso(int i) { Ui(() => { pasoActual = i; avancePaso = 0; }); Log("== " + nombres[i]); }
        void Log(string s) { Ui(() => { registro.AppendText(s + Environment.NewLine); }); }

        // lee la salida de uv para mover la barra dentro del paso de librerías
        static readonly System.Text.RegularExpressions.Regex ReResuelto = new System.Text.RegularExpressions.Regex(@"Resolved (\d+) packages");
        static readonly System.Text.RegularExpressions.Regex ReBajando = new System.Text.RegularExpressions.Regex(@"Downloading (\S+) \(([\d.]+\s*\w+)\)");
        void Interpretar(string linea)
        {
            var m = ReResuelto.Match(linea);
            if (m.Success) { int n = int.Parse(m.Groups[1].Value); Ui(() => { total = n; }); return; }
            m = ReBajando.Match(linea);
            if (m.Success)
            {
                string nombre = m.Groups[1].Value, tam = m.Groups[2].Value.Replace("MiB", " MB").Replace("KiB", " KB");
                Ui(() =>
                {
                    if (pasoActual >= 0) detalles[pasoActual] = nombre + " · " + tam;
                    if (pasoActual == 2 && total > 0) avancePaso = Math.Min(0.9, ++descargados / (double)total);
                    else avancePaso = Math.Min(0.9, avancePaso + 0.3);
                });
                return;
            }
            if (linea.Contains("Prepared ") || linea.Contains("Installed ")) Ui(() => { avancePaso = Math.Max(avancePaso, 0.95); });
        }

        void Trabajar()
        {
            try
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | (SecurityProtocolType)12288; // TLS 1.2 y 1.3
                Directory.CreateDirectory(Runtime);
                if (reparar && Directory.Exists(Env)) Directory.Delete(Env, true);
                Paso(0);
                string uv = Uv();
                Ui(() => detalles[0] = "uv " + UvVersion);
                Paso(1);
                // Python propio, sin tocar nada fuera de esta carpeta (--no-bin: nada en ~\.localin;
                // --no-registry: nada en el registro). uv 0.13 en Windows instala Python completo pero a
                // veces falla al crear su «enlace de versión menor»: se tolera si el python.exe arranca.
                // El entorno se crea con la ruta exacta (pedir «3.12» tropieza con ese enlace y con los
                // Python de la Microsoft Store).
                string py = null;
                for (int intento = 0; intento < 2 && py == null; intento++)
                {
                    Ejecutar(uv, "python install " + PythonVersion + " --no-bin --no-registry" + (intento > 0 ? " --reinstall" : ""), false);
                    try
                    {
                        string cand = BuscarPython();
                        Ejecutar(cand, "-c \"import sys; print(sys.version)\"");
                        py = cand;
                    }
                    catch (OperationCanceledException) { throw; }
                    catch (Exception e1) { Log(e1.Message); if (intento > 0) throw; }
                }
                if (Directory.Exists(Env)) Directory.Delete(Env, true);
                Ejecutar(uv, "venv \"" + Env + "\" --python \"" + py + "\"");
                Ui(() => detalles[1] = "");
                Paso(2);
                Ejecutar(uv, "pip install --python \"" + Path.Combine(Env, "Scripts", "python.exe") + "\" -r \"" + Requisitos + "\"");
                Ui(() => detalles[2] = total > 0 ? total + T(" paquetes", " packages") : "");
                Paso(3);
                Ejecutar(Path.Combine(Env, "Scripts", "python.exe"), "-c \"import numpy, scipy, shapely, rasterio, pyproj, numba, webview; print('ok')\"");
                File.WriteAllText(Sello, HuellaRequisitos());
                Ui(() => { Ok = true; lienzo.Invalidate(); });
                Thread.Sleep(450);
                Ui(Close);
            }
            catch (Exception e)
            {
                if (cancelado) { Ui(Close); return; }
                Log(e.Message);
                Ui(() =>
                {
                    fallo = true;
                    error = T("Revisa tu conexión a internet y vuelve a abrir OpenLaps. En «Ver detalles» está el motivo.",
                              "Check your internet connection and open OpenLaps again. «Show details» has the reason.");
                    cancelar.Text = T("Cerrar", "Close");
                    if (!registro.Visible) AlternarDetalles();
                    lienzo.Invalidate();
                });
            }
        }

        static string BuscarPython()
        {
            string raiz = Path.Combine(Runtime, "python");
            if (Directory.Exists(raiz))
            {
                var dirs = Directory.GetDirectories(raiz, "cpython-" + PythonVersion + ".*-windows-x86_64-none");
                Array.Sort(dirs);
                for (int i = dirs.Length - 1; i >= 0; i--)
                {
                    string exe = Path.Combine(dirs[i], "python.exe");
                    if (File.Exists(exe)) return exe;
                }
            }
            throw new Exception(T("No encuentro el Python instalado en ", "Can't find the installed Python in ") + raiz);
        }

        string Uv()
        {
            string dir = Path.Combine(Runtime, "uv-" + UvVersion), exe = Path.Combine(dir, "uv.exe");
            if (File.Exists(exe)) return exe;
            Log("uv " + UvVersion + ": " + T("descargando", "downloading"));
            string url = "https://github.com/astral-sh/uv/releases/download/" + UvVersion + "/uv-x86_64-pc-windows-msvc.zip";
            string zip = Path.Combine(Runtime, "uv.zip");
            using (var wc = new WebClient())
            {
                wc.Headers.Add("User-Agent", "OpenLaps-launcher");
                wc.DownloadFile(url, zip);
            }
            string suma;
            using (var sha = SHA256.Create())
            using (var f = File.OpenRead(zip))
                suma = BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
            if (suma != UvSha256)
            {
                File.Delete(zip);
                throw new Exception(T("La descarga de uv no coincide con la suma esperada; no se usa.", "The uv download doesn't match the expected checksum; it won't be used."));
            }
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            ZipFile.ExtractToDirectory(zip, dir);
            File.Delete(zip);
            if (!File.Exists(exe)) throw new Exception("uv.exe?");
            return exe;
        }

        void Ejecutar(string exe, string argumentos) { Ejecutar(exe, argumentos, true); }

        int Ejecutar(string exe, string argumentos, bool exigir)
        {
            if (cancelado) throw new OperationCanceledException();
            var psi = new ProcessStartInfo(exe, argumentos)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["UV_PYTHON_INSTALL_DIR"] = Path.Combine(Runtime, "python");
            psi.EnvironmentVariables["UV_CACHE_DIR"] = Path.Combine(Runtime, "cache");
            psi.EnvironmentVariables["UV_NO_PROGRESS"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            using (var p = new Process { StartInfo = psi })
            {
                p.OutputDataReceived += (s, e) => { if (e.Data != null) { Log(e.Data); Interpretar(e.Data); } };
                p.ErrorDataReceived += (s, e) => { if (e.Data != null) { Log(e.Data); Interpretar(e.Data); } };
                actual = p;
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                p.WaitForExit();
                actual = null;
                if (cancelado) throw new OperationCanceledException();
                if (exigir && p.ExitCode != 0) throw new Exception(Path.GetFileName(exe) + " -> " + p.ExitCode);
                return p.ExitCode;
            }
        }
    }
}
