"""
Descarga los stickers de un chat de TikTok Web.

Se usa desde la ventana (app.py) o por consola:
    python descargar.py
    python descargar.py --rondas 100 --paciencia 8

Funciona con Chrome, Edge o Firefox. Escucha todas las pestanas, detecta stickers por tipo (WebP) o por
nombre, prefiere siempre la version animada y respeta lo que muevas a
stickers_raw/_descartados. Registro completo en debug_imagenes.txt.
"""
import argparse
import hashlib
import random
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

RAW = Path("stickers_raw")
PERFIL = Path("perfil_tiktok")
DEBUG = Path("debug_imagenes.txt")
MIN_BYTES = 3000


# --- Ganchos: la ventana grafica los reemplaza; por consola usan print/input ---
log = print


def progreso(actual, total, texto=""):
    """Gancho de avance: la ventana lo reemplaza para mostrar la barra."""


def esperar():
    input("Pulsa Enter cuando veas los mensajes del chat... ")


def parar() -> bool:
    return False


def es_sticker(url: str, ctype: str = "") -> bool:
    u = url.lower()
    path = urlsplit(u).path
    if "sticker" in u or "dhq7zx" in u:
        return True
    if "webp" in ctype.lower():
        return True
    return path.endswith((".webp", ".awebp"))


def es_animado(cuerpo: bytes) -> bool:
    """Un WebP animado lleva un bloque 'ANIM' al inicio del archivo."""
    return b"ANIM" in cuerpo[:300]


def clave(url: str) -> str:
    path = urlsplit(url).path
    m = re.search(r"/([0-9a-f]{32})~", path)
    if m:
        return m.group(1)
    return hashlib.md5(path.encode()).hexdigest()


NAVEGADORES = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}


def perfil_de(navegador: str) -> Path:
    """Cada navegador guarda su sesion en su propia carpeta."""
    return PERFIL if navegador == "chrome" else Path(f"{PERFIL}_{navegador}")


def abrir_navegador(p, navegador: str):
    comunes = {"headless": False, "viewport": {"width": 1280, "height": 800}}
    ruta = str(perfil_de(navegador))
    try:
        if navegador == "firefox":
            # En Firefox no hay CDP: se desactiva la cache con preferencias.
            return p.firefox.launch_persistent_context(
                ruta,
                firefox_user_prefs={"browser.cache.disk.enable": False,
                                    "browser.cache.memory.enable": False},
                **comunes)
        canal = "msedge" if navegador == "edge" else "chrome"
        return p.chromium.launch_persistent_context(
            ruta, channel=canal,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
            **comunes)
    except Exception as e:
        msg = str(e)
        if navegador == "firefox" and ("playwright install" in msg
                                       or "Executable doesn't exist" in msg):
            raise RuntimeError(
                "Firefox todavía no está descargado para la app. Pulsa «Instalar "
                "Firefox» (o ejecuta: python -m playwright install firefox) y "
                "vuelve a intentar.") from None
        if navegador != "firefox" and ("is not found" in msg
                                       or "distribution" in msg.lower()
                                       or "Executable doesn't exist" in msg):
            raise RuntimeError(
                f"No encuentro {NAVEGADORES[navegador]} instalado en este equipo. "
                "Elige otro navegador de la lista.") from None
        raise


def descargar(rondas: int = 60, paciencia: int = 6, navegador: str = "chrome"):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Falta instalar Playwright. En PowerShell ejecuta:\n"
            "    python -m pip install playwright pillow")
        return

    RAW.mkdir(exist_ok=True)
    (RAW / "_descartados").mkdir(exist_ok=True)
    estado = {"nuevos": 0, "resp_total": 0, "resp_imagen": 0}
    visto_debug = set()
    procesadas = set()
    dbg = open(DEBUG, "w", encoding="utf-8")

    def anotar(url, ctype, tam, guardado):
        sp = urlsplit(url)
        k = (sp.netloc, sp.path)
        if k in visto_debug:
            return
        visto_debug.add(k)
        dbg.write(f"{'GUARDADO' if guardado else 'ignorado'}\t{ctype}\t{tam}\t"
                  f"{sp.netloc}{sp.path[:90]}\n")
        dbg.flush()

    def guardar(url: str, cuerpo: bytes) -> bool:
        if len(cuerpo) < MIN_BYTES:
            return False
        destino = RAW / f"{clave(url)}.webp"
        if (RAW / "_descartados" / destino.name).exists():
            return False
        if destino.exists():
            previo = destino.read_bytes()
            nuevo_ani, previo_ani = es_animado(cuerpo), es_animado(previo)
            # Gana la animada; si ambas son del mismo tipo, la mas pesada.
            mejor = (nuevo_ani and not previo_ani) or \
                    (nuevo_ani == previo_ani and len(cuerpo) > len(previo))
            if mejor:
                destino.write_bytes(cuerpo)
                if nuevo_ani and not previo_ani:
                    log(f"  ~ {destino.name} reemplazado por la versión animada")
            return False
        destino.write_bytes(cuerpo)
        estado["nuevos"] += 1
        log(f"  + {destino.name} ({len(cuerpo) // 1024} KB)")
        return True

    def al_responder(resp):
        estado["resp_total"] += 1
        try:
            ctype = resp.headers.get("content-type", "")
            if not (ctype.startswith("image") or es_sticker(resp.url)):
                return
            estado["resp_imagen"] += 1
            if resp.status != 200:
                return
            cuerpo = resp.body()
            ok = es_sticker(resp.url, ctype)
            anotar(resp.url, ctype, len(cuerpo), ok)
            if ok:
                guardar(resp.url, cuerpo)
        except Exception as e:
            dbg.write(f"ERROR\t{resp.url[:100]}\t{e}\n")
            dbg.flush()

    try:
        with sync_playwright() as p:
            try:
                ctx = abrir_navegador(p, navegador)
            except RuntimeError as e:
                log(str(e))
                return
            try:
                # Escucha a nivel de contexto: cubre todas las pestanas.
                ctx.on("response", al_responder)

                def desactivar_cache(pg):
                    try:
                        ctx.new_cdp_session(pg).send(
                            "Network.setCacheDisabled", {"cacheDisabled": True})
                    except Exception:
                        pass

                for pg in ctx.pages:
                    desactivar_cache(pg)
                ctx.on("page", desactivar_cache)

                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("https://www.tiktok.com/messages")

                log(f"Se abrió {NAVEGADORES[navegador]} con TikTok.")
                log("  1) Inicia sesión si hace falta (cuenta secundaria).")
                log("  2) Abre el chat donde están los stickers.")
                log("     (Si ya estaba abierto, entra a otro chat y vuelve a este.)")
                esperar()
                if parar():
                    log("Cancelado.")
                    return

                def pestana_activa():
                    abiertas = [pg for pg in ctx.pages if not pg.is_closed()]
                    if not abiertas:
                        raise RuntimeError("Se cerró el navegador")
                    for pg in abiertas:
                        if "tiktok.com/messages" in pg.url:
                            return pg
                    return abiertas[-1]

                def recolectar_dom(verbose=False):
                    pg = pestana_activa()
                    urls = []
                    for fr in pg.frames:
                        try:
                            urls += fr.eval_on_selector_all(
                                "img", "els => els.map(e => e.currentSrc || e.src)")
                        except Exception:
                            pass
                    urls = [u for u in urls if u and u.startswith("http")]
                    candidatos = {u for u in urls if es_sticker(u)}
                    if verbose:
                        log(f"  En pantalla: {len(urls)} imágenes, "
                            f"{len(candidatos)} parecen stickers "
                            f"(respuestas vistas: {estado['resp_total']})")
                    for u in candidatos:
                        ruta = urlsplit(u).path
                        if ruta in procesadas:
                            continue
                        procesadas.add(ruta)
                        try:
                            r = ctx.request.get(u)
                            if r.ok:
                                cuerpo = r.body()
                                anotar(u, r.headers.get("content-type", ""),
                                       len(cuerpo), True)
                                guardar(u, cuerpo)
                        except Exception as e:
                            dbg.write(f"ERROR_DOM\t{u[:100]}\t{e}\n")
                            dbg.flush()

                log("Buscando stickers...")
                recolectar_dom(verbose=True)

                pg = pestana_activa()
                pg.bring_to_front()
                ancho, alto = pg.viewport_size["width"], pg.viewport_size["height"]
                pg.mouse.move(int(ancho * 0.65), int(alto * 0.5))

                sin_nuevos = 0
                for i in range(1, rondas + 1):
                    if parar():
                        log("Detenido por el usuario.")
                        break
                    antes = estado["nuevos"]
                    pestana_activa().mouse.wheel(0, -1500)
                    time.sleep(random.uniform(1.2, 2.8))
                    recolectar_dom()
                    sin_nuevos = 0 if estado["nuevos"] > antes else sin_nuevos + 1
                    log(f"Ronda {i}: {estado['nuevos']} stickers nuevos en total")
                    progreso(None, None,
                             f"Buscando stickers · ronda {i} · {estado['nuevos']} nuevos")
                    if sin_nuevos >= paciencia:
                        log("No aparecen más stickers, terminé.")
                        break
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        dbg.close()

    archivos = list(RAW.glob("*.webp"))
    animados = sum(1 for f in archivos if es_animado(f.read_bytes()))
    log(f"\nListo. Nuevos: {estado['nuevos']}. Total en {RAW}/: {len(archivos)} "
        f"({animados} animados, {len(archivos) - animados} estaticos)")
    return {"nuevos": estado["nuevos"], "total": len(archivos), "animados": animados,
            "estaticos": len(archivos) - animados}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rondas", type=int, default=60)
    ap.add_argument("--paciencia", type=int, default=6)
    ap.add_argument("--navegador", choices=list(NAVEGADORES), default="chrome")
    args = ap.parse_args()
    descargar(args.rondas, args.paciencia, args.navegador)


if __name__ == "__main__":
    main()
