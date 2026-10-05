"""
Comprueba los packs de packs/ contra las mismas reglas que aplica la app
oficial de WhatsApp (StickerPackValidator), para detectar fallos en el PC.

Uso:
    python validar_packs.py
"""
import json
import struct
from pathlib import Path

from PIL import Image

OUT = Path("packs")
KB = 1024


# Gancho de mensajes: en la terminal imprime; la ventana grafica lo reemplaza.
log = print


def duraciones_webp(ruta) -> list:
    """Duracion (ms) de cada frame de un WebP animado, leida de los bloques ANMF."""
    datos = Path(ruta).read_bytes()
    pos, durs = 12, []
    while pos + 8 <= len(datos):
        etiqueta = datos[pos:pos + 4]
        tam = struct.unpack("<I", datos[pos + 4:pos + 8])[0]
        if etiqueta == b"ANMF":
            durs.append(int.from_bytes(datos[pos + 20:pos + 23], "little"))
        pos += 8 + tam + (tam & 1)
    return durs


def revisar_pack(p: dict) -> list:
    errores = []
    carpeta = OUT / p["identifier"]
    animado = bool(p.get("animated_sticker_pack", False))

    tray = carpeta / p["tray_image_file"]
    if not tray.exists():
        errores.append("falta el icono tray")
    else:
        if tray.stat().st_size > 50 * KB:
            errores.append(f"tray pesa {tray.stat().st_size // KB} KB (max 50)")
        with Image.open(tray) as im:
            if not (24 <= im.width <= 512 and 24 <= im.height <= 512):
                errores.append(f"tray mide {im.width}x{im.height} (debe estar entre 24 y 512)")

    n = len(p["stickers"])
    if not (3 <= n <= 30):
        errores.append(f"tiene {n} stickers (debe tener de 3 a 30)")

    for s in p["stickers"]:
        nombre = s["image_file"]
        f = carpeta / nombre
        if not (1 <= len(s["emojis"]) <= 3):
            errores.append(f"{nombre}: debe tener de 1 a 3 emojis")
        if not f.exists():
            errores.append(f"{nombre}: no existe")
            continue
        tam = f.stat().st_size
        limite = 500 if animado else 100
        if tam > limite * KB:
            errores.append(f"{nombre}: pesa {tam // KB} KB (max {limite})")
        with Image.open(f) as im:
            if im.size != (512, 512):
                errores.append(f"{nombre}: mide {im.size[0]}x{im.size[1]} (debe ser 512x512)")
            frames = getattr(im, "n_frames", 1)
            if animado:
                if frames <= 1:
                    errores.append(f"{nombre}: el pack es animado pero este sticker no tiene movimiento")
                else:
                    durs = duraciones_webp(f)
                    total = sum(durs)
                    if len(durs) != frames:
                        errores.append(f"{nombre}: no pude leer las duraciones de los frames")
                    elif min(durs) < 8:
                        errores.append(f"{nombre}: un frame dura {min(durs)} ms (min 8)")
                    if total > 10_000:
                        errores.append(f"{nombre}: dura {total} ms (max 10000)")
            elif frames > 1:
                errores.append(f"{nombre}: el pack es estatico pero este sticker tiene movimiento")
    return errores


def main():
    contenido = OUT / "contents.json"
    if not contenido.exists():
        raise SystemExit("No existe packs/contents.json. Ejecuta primero convertir.py")
    datos = json.loads(contenido.read_text(encoding="utf-8"))
    todo_ok = True
    for p in datos["sticker_packs"]:
        errores = revisar_pack(p)
        if errores:
            todo_ok = False
            log(f"[{p['identifier']}] PROBLEMAS:")
            for e in errores:
                log(f"   - {e}")
        else:
            log(f"[{p['identifier']}] OK ({len(p['stickers'])} stickers)")
    log("\nTodo listo para la app." if todo_ok else "\nCorrige lo anterior antes de usar la app.")


if __name__ == "__main__":
    main()
