"""
Descarga los stickers de un chat de TikTok Web.

Se usa desde la ventana (app.py) o por consola:
    python descargar.py
    python descargar.py --rondas 100 --paciencia 8

Mensaje de corte (opcional): si escribes en el chat un texto poco comun despues
de cada tanda de stickers, la app sube por el chat solo hasta ese mensaje y
descarga unicamente lo que mandaste despues.

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
URL_MENSAJES = "https://www.tiktok.com/messages"

# Se ejecuta dentro de la pagina. Busca el contenedor que se desplaza (el que se mueve
# con la rueda en el punto indicado) para no confundir el mensaje de corte con la vista
# previa de la lista de chats. Devuelve las imagenes del chat: todas si no hay corte a
# la vista, o solo las posteriores al corte mas reciente si lo hay.
JS_ESCANEO = """
([marca, x, y]) => {
  let el = document.elementFromPoint(x, y);
  let cont = null;
  while (el && el !== document.body) {
    const st = getComputedStyle(el);
    if (/(auto|scroll)/.test(st.overflowY) && el.scrollHeight > el.clientHeight + 4) {
      cont = el; break;
    }
    el = el.parentElement;
  }
  const raiz = cont || document.body;
  const buscada = (marca || "").trim().toLowerCase();
  let ultimo = null;
  if (buscada) {
    const it = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = it.nextNode())) {
      if (n.nodeValue && n.nodeValue.toLowerCase().includes(buscada)) ultimo = n;
    }
  }
  const urls = [];
  for (const im of raiz.querySelectorAll("img")) {
    const u = im.currentSrc || im.src;
    if (!u) continue;
    if (ultimo && !(ultimo.compareDocumentPosition(im) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
    urls.push(u);
  }
  return {marca: !!ultimo, contenedor: !!cont, altura: raiz.scrollHeight, urls: urls};
}
"""


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


def descargar(rondas: int = 60, paciencia: int = 6, navegador: str = "chrome",
              marca: str = ""):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Falta instalar Playwright. En PowerShell ejecuta:\n"
            "    python -m pip install playwright pillow")
        return

    RAW.mkdir(exist_ok=True)
    (RAW / "_descartados").mkdir(exist_ok=True)
    marca = (marca or "").strip()
    estado = {"nuevos": 0, "resp_total": 0, "resp_imagen": 0,
              "dom_stickers": 0, "marca_hallada": False}
    en_memoria = {}  # con mensaje de corte, lo que llega por red se retiene hasta ubicarlo
    claves_aceptadas = set()  # stickers que SI estan despues del corte
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
                if marca:
                    en_memoria[urlsplit(resp.url).path] = (resp.url, cuerpo)
                else:
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
                page.goto(URL_MENSAJES)

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
                        if "/messages" in pg.url:
                            return pg
                    return abiertas[-1]

                def pausa(ms):
                    """Espera dejando que Playwright procese las respuestas de red."""
                    try:
                        pestana_activa().wait_for_timeout(ms)
                    except Exception:
                        time.sleep(ms / 1000)

                def recolectar_dom(verbose=False):
                    """Lee el chat. Devuelve (corte_a_la_vista, altura_del_chat)."""
                    pg = pestana_activa()
                    tam = pg.viewport_size or {"width": 1280, "height": 800}
                    argumento = [marca, int(tam["width"] * 0.65), int(tam["height"] * 0.5)]
                    urls, visible, altura = [], False, 0
                    for fr in pg.frames:
                        try:
                            datos = fr.evaluate(JS_ESCANEO, argumento)
                        except Exception:
                            continue
                        urls += datos["urls"]
                        visible = visible or datos["marca"]
                        altura = max(altura, datos["altura"])
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
                        estado["dom_stickers"] += 1
                        claves_aceptadas.add(clave(u))
                        try:
                            if ruta in en_memoria:
                                guardar(u, en_memoria[ruta][1])
                            else:
                                resp = ctx.request.get(u)
                                if resp.ok:
                                    cuerpo = resp.body()
                                    anotar(u, resp.headers.get("content-type", ""),
                                           len(cuerpo), True)
                                    guardar(u, cuerpo)
                            # otras versiones del mismo sticker (p. ej. la animada)
                            k = clave(u)
                            for u2, cuerpo2 in list(en_memoria.values()):
                                if clave(u2) == k:
                                    guardar(u2, cuerpo2)
                        except Exception as e:
                            dbg.write(f"ERROR_DOM\t{u[:100]}\t{e}\n")
                            dbg.flush()
                    return visible, altura

                log("Buscando stickers...")
                if marca:
                    log(f"Mensaje de corte: «{marca}». Me detengo al llegar a él.")
                visible, altura_prev = recolectar_dom(verbose=True)
                if marca and visible:
                    estado["marca_hallada"] = True
                    log("El mensaje de corte ya está a la vista: solo tomo lo que viene después.")

                pg = pestana_activa()
                pg.bring_to_front()
                tam = pg.viewport_size or {"width": 1280, "height": 800}
                pg.mouse.move(int(tam["width"] * 0.65), int(tam["height"] * 0.5))

                sin_nuevos = 0
                for i in range(1, rondas + 1):
                    if estado["marca_hallada"]:
                        break
                    if parar():
                        log("Detenido por el usuario.")
                        break
                    antes = estado["nuevos"]
                    pestana_activa().mouse.wheel(0, -1500)
                    time.sleep(random.uniform(1.2, 2.8))
                    visible, altura = recolectar_dom()
                    hubo_cambio = estado["nuevos"] > antes or altura != altura_prev
                    altura_prev = altura
                    sin_nuevos = 0 if hubo_cambio else sin_nuevos + 1
                    log(f"Ronda {i}: {estado['nuevos']} stickers nuevos en total")
                    progreso(None, None,
                             f"Buscando stickers · ronda {i} · {estado['nuevos']} nuevos")
                    if marca and visible:
                        pausa(800)  # deja terminar de cargar lo ultimo
                        recolectar_dom()
                        estado["marca_hallada"] = True
                        log("Llegué al mensaje de corte, termino.")
                        break
                    if sin_nuevos >= paciencia:
                        log("No aparecen más mensajes, terminé.")
                        break

                if marca and en_memoria:
                    pausa(500)
                    # Versiones que llegaron por red despues de leer el chat (p. ej. la
                    # animada): se guardan solo si pertenecen a un sticker posterior al corte.
                    for url, cuerpo in list(en_memoria.values()):
                        if clave(url) in claves_aceptadas:
                            guardar(url, cuerpo)

                if marca and not estado["marca_hallada"] and not parar():
                    log(f"⚠ No encontré el mensaje de corte «{marca}» en este chat. "
                        "Se descargó todo lo que se pudo cargar.")
                if marca and estado["dom_stickers"] == 0 and en_memoria:
                    log("⚠ No pude ubicar los stickers dentro del chat para separar los "
                        "nuevos de los viejos. Guardo todo lo que cargó (lo ya descargado "
                        "se omite solo).")
                    for url, cuerpo in list(en_memoria.values()):
                        guardar(url, cuerpo)
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
            "estaticos": len(archivos) - animados,
            "marca": marca, "marca_hallada": estado["marca_hallada"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rondas", type=int, default=60)
    ap.add_argument("--paciencia", type=int, default=6)
    ap.add_argument("--navegador", choices=list(NAVEGADORES), default="chrome")
    ap.add_argument("--marca", default="", help="mensaje de corte: texto del chat donde detenerse")
    args = ap.parse_args()
    descargar(args.rondas, args.paciencia, args.navegador, args.marca)


if __name__ == "__main__":
    main()
