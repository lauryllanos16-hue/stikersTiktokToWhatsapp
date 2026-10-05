"""
Convierte los packs de packs/ en archivos .wastickers, que se pueden
importar en el telefono con la app Sticker Maker (sin Android Studio).

Uso:
    python exportar_wastickers.py

Salida:
    wastickers/<nombre del pack>.wastickers

Formato (igual al que genera el proyecto sticker-convert):
    cover.png, title.txt, author.txt
    estaticos -> NNN.png  (512x512, maximo 100 KB)
    animados  -> NNN.webp (512x512, maximo 500 KB)
"""
import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

PACKS = Path("packs")
OUT = Path("wastickers")
LIM_PNG = 100 * 1024


# Gancho de mensajes: en la terminal imprime; la ventana grafica lo reemplaza.
log = print


def progreso(actual, total, texto=""):
    """Gancho de avance: la ventana lo reemplaza para mostrar la barra."""


def transparencia_intacta(original: Image.Image, datos_png: bytes) -> bool:
    """Comprueba que el PNG conserva la transparencia del original."""
    resultado = Image.open(io.BytesIO(datos_png)).convert("RGBA").getchannel("A")
    diferencia = ImageChops.difference(original.getchannel("A"), resultado)
    return ImageStat.Stat(diferencia).mean[0] < 2


def webp_a_png(ruta: Path):
    """WebP estatico -> PNG de 512x512 que pese menos de 100 KB (o None)."""
    with Image.open(ruta) as im:
        im = im.convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    if buf.tell() <= LIM_PNG:
        return buf.getvalue()
    # Si pesa demasiado, se reduce la paleta de colores poco a poco,
    # descartando los intentos que dañen la transparencia.
    for colores in (256, 192, 128, 96, 64, 48, 32):
        q = im.quantize(colors=colores, method=Image.Quantize.FASTOCTREE)
        buf = io.BytesIO()
        q.save(buf, "PNG", optimize=True)
        if buf.tell() <= LIM_PNG and transparencia_intacta(im, buf.getvalue()):
            return buf.getvalue()
    return None


def nombre_seguro(texto: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", texto).strip()


def main():
    contenido = PACKS / "contents.json"
    if not contenido.exists():
        raise SystemExit("No existe packs/contents.json. Ejecuta primero convertir.py")
    datos = json.loads(contenido.read_text(encoding="utf-8"))

    OUT.mkdir(exist_ok=True)
    for viejo in OUT.glob("*.wastickers"):
        viejo.unlink()
    generados = 0
    total = sum(len(p["stickers"]) for p in datos["sticker_packs"])
    hecho = 0
    for p in datos["sticker_packs"]:
        carpeta = PACKS / p["identifier"]
        animado = bool(p.get("animated_sticker_pack", False))
        destino = OUT / f"{nombre_seguro(p['name'])}.wastickers"

        incluidos = 0
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(carpeta / p["tray_image_file"], "cover.png")
            z.writestr("author.txt", p["publisher"] + "\n")
            z.writestr("title.txt", p["name"] + "\n")
            for s in p["stickers"]:
                hecho += 1
                progreso(hecho, total, "Empaquetando para WhatsApp")
                f = carpeta / s["image_file"]
                if animado:
                    z.write(f, f"{f.stem}.webp")
                    incluidos += 1
                else:
                    datos_png = webp_a_png(f)
                    if datos_png is None:
                        log(f"  ! {f.name} no cabe en 100 KB como PNG, se omite")
                        continue
                    z.writestr(f"{f.stem}.png", datos_png)
                    incluidos += 1

        if incluidos < 3:
            destino.unlink(missing_ok=True)
            log(f"[{p['identifier']}] quedaron menos de 3 stickers, no se genera el archivo")
            continue
        generados += 1
        log(f"[{p['identifier']}] {destino.name}: {incluidos} stickers "
              f"({destino.stat().st_size // 1024} KB)")

    if generados == 0:
        raise SystemExit("No se genero ningun archivo: hacen falta al menos 3 stickers "
                         "del mismo tipo (estaticos o animados).")
    log(f"\nListo. Archivos en la carpeta: {OUT}/")


if __name__ == "__main__":
    main()
