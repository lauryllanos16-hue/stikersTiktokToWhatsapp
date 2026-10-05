"""
Stickers de TikTok a WhatsApp - ventana con botones.

Para abrirla: doble clic en iniciar.bat  (o:  python app.py)

Paso 1  Descargar los stickers del chat de TikTok
Paso 2  Convertirlos y crear los archivos .wastickers
Paso 3  Abrir la carpeta para pasarlos al celular
"""
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

AQUI = Path(__file__).resolve().parent
os.chdir(AQUI)  # los scripts usan carpetas relativas
sys.path.insert(0, str(AQUI))

import convertir  # noqa: E402
import descargar  # noqa: E402
import exportar_wastickers  # noqa: E402
import validar_packs  # noqa: E402

NOMBRES_NAV = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}
CONFIG = AQUI / "config.json"
CARPETA_WASTICKERS = AQUI / "wastickers"
CARPETA_RAW = AQUI / "stickers_raw"


def abrir_carpeta(ruta: Path):
    ruta.mkdir(exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(ruta))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stickers de TikTok a WhatsApp")
        self.geometry("780x640")
        self.minsize(640, 520)

        self.cola = queue.Queue()
        self.hilo = None
        self.tarea_actual = ""
        self.evento_listo = threading.Event()
        self.evento_parar = threading.Event()

        # Conectar los scripts con la ventana
        for modulo in (descargar, convertir, exportar_wastickers, validar_packs):
            modulo.log = self.escribir_desde_hilo
        for modulo in (descargar, convertir, exportar_wastickers):
            modulo.progreso = self._progreso_desde_hilo
        descargar.esperar = self._esperar_boton
        descargar.parar = self.evento_parar.is_set

        self._construir()
        self.after(100, self._vaciar_cola)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)

    # ---------- interfaz ----------
    def _construir(self):
        pad = {"padx": 12, "pady": 6}

        paso1 = ttk.LabelFrame(self, text=" Paso 1 · Descargar stickers de TikTok ", padding=10)
        paso1.pack(fill="x", **pad)
        ttk.Label(
            paso1, wraplength=720, justify="left",
            text=("Se abre el navegador que elijas. Inicia sesión con tu cuenta secundaria, abre el chat "
                  "donde están los stickers y pulsa «Ya abrí el chat». No toques el "
                  "navegador mientras trabaja."),
        ).pack(anchor="w")
        fila_nav = ttk.Frame(paso1)
        fila_nav.pack(fill="x", pady=(8, 0))
        ttk.Label(fila_nav, text="Navegador:").pack(side="left")
        guardado = self._cargar_config().get("navegador", "chrome")
        self.var_nav = tk.StringVar(value=NOMBRES_NAV.get(guardado, "Chrome"))
        self.combo_nav = ttk.Combobox(fila_nav, textvariable=self.var_nav, width=10,
                                      state="readonly", values=list(NOMBRES_NAV.values()))
        self.combo_nav.pack(side="left", padx=8)
        self.btn_instalar = ttk.Button(fila_nav, text="Instalar Firefox",
                                       command=self._iniciar_instalacion_firefox)
        self.btn_instalar.pack(side="left")
        ttk.Label(fila_nav, text="(Firefox necesita una descarga extra la primera vez)",
                  foreground="#666666").pack(side="left", padx=8)

        fila = ttk.Frame(paso1)
        fila.pack(fill="x", pady=(8, 0))
        self.btn_descargar = ttk.Button(fila, text="Abrir TikTok y descargar",
                                        command=self._iniciar_descarga)
        self.btn_descargar.pack(side="left")
        self.btn_listo = ttk.Button(fila, text="Ya abrí el chat  ▶", state="disabled",
                                    command=self._ya_abri_el_chat)
        self.btn_listo.pack(side="left", padx=8)
        self.btn_parar = ttk.Button(fila, text="Detener", state="disabled",
                                    command=self._detener)
        self.btn_parar.pack(side="left")
        self.btn_ver_raw = ttk.Button(fila, text="Ver stickers descargados",
                                      command=lambda: abrir_carpeta(CARPETA_RAW))
        self.btn_ver_raw.pack(side="right")

        paso2 = ttk.LabelFrame(self, text=" Paso 2 · Preparar para WhatsApp ", padding=10)
        paso2.pack(fill="x", **pad)
        fila2 = ttk.Frame(paso2)
        fila2.pack(fill="x")
        ttk.Label(fila2, text="Nombre del autor:").pack(side="left")
        self.var_autor = tk.StringVar(value=self._cargar_config().get("autor", "Yo"))
        ttk.Entry(fila2, textvariable=self.var_autor, width=24).pack(side="left", padx=8)
        self.btn_convertir = ttk.Button(fila2, text="Convertir y crear archivos .wastickers",
                                        command=self._iniciar_conversion)
        self.btn_convertir.pack(side="left", padx=8)

        paso3 = ttk.LabelFrame(self, text=" Paso 3 · Pasar al celular ", padding=10)
        paso3.pack(fill="x", **pad)
        ttk.Label(
            paso3, wraplength=720, justify="left",
            text=("Copia los archivos .wastickers a tu teléfono (cable, Drive o Telegram) y "
                  "ábrelos con la app Sticker Maker para importarlos a WhatsApp."),
        ).pack(anchor="w")
        self.btn_abrir = ttk.Button(paso3, text="Abrir carpeta con los archivos .wastickers",
                                    command=lambda: abrir_carpeta(CARPETA_WASTICKERS))
        self.btn_abrir.pack(anchor="w", pady=(8, 0))

        marco_log = ttk.LabelFrame(self, text=" Progreso ", padding=6)
        marco_log.pack(fill="both", expand=True, **pad)
        self.lbl_progreso = ttk.Label(marco_log, text="")
        self.lbl_progreso.pack(fill="x")
        self.barra = ttk.Progressbar(marco_log, mode="determinate")
        self.barra.pack(fill="x", pady=(2, 6))
        self.caja = scrolledtext.ScrolledText(marco_log, height=10, state="disabled",
                                              font=("Consolas", 9), wrap="word")
        self.caja.pack(fill="both", expand=True)
        self.escribir("Listo. Empieza por el Paso 1.")

    # ---------- registro de mensajes ----------
    def escribir(self, texto: str):
        self.caja.configure(state="normal")
        self.caja.insert("end", texto + "\n")
        self.caja.see("end")
        self.caja.configure(state="disabled")

    def escribir_desde_hilo(self, *partes):
        self.cola.put(("log", " ".join(str(p) for p in partes)))

    def _vaciar_cola(self):
        try:
            while True:
                evento = self.cola.get_nowait()
                if evento[0] == "log":
                    self.escribir(evento[1])
                elif evento[0] == "esperar":
                    self.btn_listo.configure(state="normal")
                elif evento[0] == "progreso":
                    self._aplicar_progreso(evento[1], evento[2], evento[3])
                elif evento[0] == "fin":
                    self._al_terminar(evento[1], evento[2], evento[3])
        except queue.Empty:
            pass
        self.after(100, self._vaciar_cola)

    # ---------- tareas en segundo plano ----------
    def _correr(self, nombre: str, funcion, con_parar: bool):
        if self.hilo and self.hilo.is_alive():
            return
        self.tarea_actual = nombre
        self.evento_parar.clear()
        self.evento_listo.clear()
        self.btn_descargar.configure(state="disabled")
        self.btn_convertir.configure(state="disabled")
        self.btn_instalar.configure(state="disabled")
        self.combo_nav.configure(state="disabled")
        self.btn_parar.configure(state="normal" if con_parar else "disabled")
        textos = {"descargar": "Descargando stickers...",
                  "convertir": "Preparando la conversión...",
                  "instalar": "Instalando Firefox..."}
        self._iniciar_barra(nombre != "convertir", textos.get(nombre, "Trabajando..."))

        def tarea():
            ok = True
            resultado = None
            try:
                resultado = funcion()
            except SystemExit as e:
                ok = False
                self.escribir_desde_hilo(f"⚠ {e}")
            except Exception as e:
                ok = False
                (AQUI / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
                self.escribir_desde_hilo(
                    f"✖ Error: {type(e).__name__}: {e}\n"
                    f"  (detalle completo en el archivo error.log)")
            finally:
                self.cola.put(("fin", nombre, ok, resultado))

        self.hilo = threading.Thread(target=tarea, daemon=True)
        self.hilo.start()

    def _al_terminar(self, nombre: str, ok: bool, resultado=None):
        self._detener_barra()
        self.btn_descargar.configure(state="normal")
        self.btn_convertir.configure(state="normal")
        self.btn_instalar.configure(state="normal")
        self.combo_nav.configure(state="readonly")
        self.btn_listo.configure(state="disabled")
        self.btn_parar.configure(state="disabled")

        if nombre == "descargar" and ok and resultado and not self.evento_parar.is_set():
            self._traer_al_frente()
            r = resultado
            resumen = (f"Total guardados: {r['total']} "
                       f"({r['animados']} animados, {r['estaticos']} estáticos)")
            if r["nuevos"]:
                texto = (f"Descarga terminada.\n\nStickers nuevos: {r['nuevos']}\n{resumen}\n\n"
                         "Siguiente: Paso 2, «Convertir y crear archivos .wastickers».")
            else:
                texto = f"La descarga terminó, pero no encontré stickers nuevos.\n\n{resumen}"
            messagebox.showinfo("Descarga terminada", texto)
        elif nombre == "convertir" and ok:
            self._traer_al_frente()
            n = len(list(CARPETA_WASTICKERS.glob("*.wastickers")))
            if messagebox.askyesno("Listo", f"Se crearon {n} archivo(s) .wastickers.\n\n"
                                            "¿Abrir la carpeta?"):
                abrir_carpeta(CARPETA_WASTICKERS)
        elif nombre == "instalar" and ok:
            self._traer_al_frente()
            messagebox.showinfo("Firefox instalado",
                                "Firefox quedó listo. Ya puedes elegirlo en la lista de navegadores.")

    # ---------- barra de progreso y avisos ----------
    def _progreso_desde_hilo(self, actual, total, texto=""):
        self.cola.put(("progreso", actual, total, texto))

    def _iniciar_barra(self, indeterminada: bool, texto: str):
        self.barra.stop()
        self.lbl_progreso.configure(text=texto)
        if indeterminada:
            self.barra.configure(mode="indeterminate")
            self.barra.start(12)
        else:
            self.barra.configure(mode="determinate", maximum=100, value=0)

    def _detener_barra(self):
        self.barra.stop()
        self.barra.configure(mode="determinate", value=0)
        self.lbl_progreso.configure(text="")

    def _aplicar_progreso(self, actual, total, texto):
        if total:
            if str(self.barra.cget("mode")) != "determinate":
                self.barra.stop()
                self.barra.configure(mode="determinate")
            self.barra.configure(maximum=total, value=actual)
            self.lbl_progreso.configure(text=f"{texto}: {actual} de {total}")
        else:
            if str(self.barra.cget("mode")) != "indeterminate":
                self.barra.configure(mode="indeterminate")
                self.barra.start(12)
            self.lbl_progreso.configure(text=texto)

    def _traer_al_frente(self):
        """Sube la ventana por encima del navegador u otras ventanas."""
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except tk.TclError:
            pass

    # ---------- paso 1 ----------
    def _nav_elegido(self) -> str:
        for clave, nombre in NOMBRES_NAV.items():
            if nombre == self.var_nav.get():
                return clave
        return "chrome"

    def _iniciar_descarga(self):
        nav = self._nav_elegido()
        self._guardar_config({"navegador": nav})
        self.escribir(f"\n--- Descargando stickers ({NOMBRES_NAV[nav]}) ---")
        self._correr("descargar", lambda: descargar.descargar(navegador=nav), con_parar=True)

    def _iniciar_instalacion_firefox(self):
        self.escribir("\n--- Instalando Firefox para la app ---")
        self._correr("instalar", self._instalar_firefox, con_parar=False)

    @staticmethod
    def _instalar_firefox():
        """Equivale a:  python -m playwright install firefox"""
        descargar.log("Descargando Firefox (solo la primera vez, puede tardar unos minutos)...")
        banderas = 0x08000000 if sys.platform.startswith("win") else 0  # sin ventana negra
        proceso = subprocess.Popen(
            [sys.executable, "-m", "playwright", "install", "firefox"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=banderas)
        for linea in proceso.stdout:
            linea = linea.strip()
            if linea:
                descargar.log("  " + linea)
        proceso.wait()
        if proceso.returncode != 0:
            raise SystemExit("La instalación de Firefox falló. Revisa tu conexión a internet "
                             "e inténtalo de nuevo.")
        descargar.log("Firefox listo. Ya puedes elegirlo en la lista de navegadores.")

    def _esperar_boton(self):
        """Corre en el hilo de trabajo: espera a que se pulse «Ya abri el chat»."""
        self.escribir_desde_hilo("Cuando veas los mensajes del chat, pulsa «Ya abrí el chat».")
        self.cola.put(("esperar",))
        self.evento_listo.wait()
        self.evento_listo.clear()

    def _ya_abri_el_chat(self):
        self.btn_listo.configure(state="disabled")
        self.evento_listo.set()

    def _detener(self):
        self.escribir("Deteniendo...")
        self.evento_parar.set()
        self.evento_listo.set()  # por si estaba esperando el boton

    # ---------- paso 2 ----------
    def _iniciar_conversion(self):
        autor = self.var_autor.get().strip() or "Yo"
        convertir.PUBLISHER = autor
        self._guardar_config({"autor": autor})
        self.escribir("\n--- Convirtiendo ---")
        self._correr("convertir", self._convertir_todo, con_parar=False)

    @staticmethod
    def _convertir_todo():
        convertir.main()
        validar_packs.main()
        exportar_wastickers.main()

    # ---------- configuracion ----------
    @staticmethod
    def _cargar_config() -> dict:
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _guardar_config(datos: dict):
        try:
            actual = App._cargar_config()
            actual.update(datos)
            CONFIG.write_text(json.dumps(actual, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _cerrar(self):
        if self.hilo and self.hilo.is_alive():
            if not messagebox.askyesno("Salir", "Hay una tarea en curso. ¿Cerrar de todos modos?"):
                return
            self.evento_parar.set()
            self.evento_listo.set()
            self.hilo.join(timeout=4)
        self.destroy()


def main():
    try:  # texto nítido en pantallas con escala alta (Windows)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
