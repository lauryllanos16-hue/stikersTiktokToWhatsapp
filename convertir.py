"""
Convierte stickers_raw/*.webp en packs para WhatsApp.

Uso:
    python convertir.py

Salida (formato compatible con la app de ejemplo WhatsApp/stickers):
    packs/contents.json
    packs/<identificador>/001.webp, 002.webp, ..., tray.png

WhatsApp no permite mezclar estaticos y animados en un mismo pack,
asi que se generan packs separados para cada tipo.
"""
import hashlib
import json
import shutil
import struct
from pathlib import Path

from PIL import Image, ImageOps

RAW = Path("stickers_raw")
OUT = Path("packs")
PUBLISHER = "Yo"
POR_PACK = 30
MIN_POR_PACK = 3
LIM_ESTATICO = 100 * 1024
LIM_ANIMADO = 500 * 1024
MAX_MS = 10_000
EMOJI = ["😀"]  # WhatsApp exige 1 a 3 emojis por sticker


# Gancho de mensajes: en la terminal imprime; la ventana grafica lo reemplaza.
log = print


def progreso(actual, total, texto=""):
    """Gancho de avance: la ventana lo reemplaza para mostrar la barra."""


_avance = {"hecho": 0, "total": 0}


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


def encuadrar(frame: Image.Image) -> Image.Image:
    """Escala para caber en 512x512 y centra sobre lienzo transparente."""
    frame = ImageOps.contain(frame.convert("RGBA"), (512, 512), Image.LANCZOS)
    lienzo = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    lienzo.paste(frame, ((512 - frame.width) // 2, (512 - frame.height) // 2), frame)
    return lienzo


def guardar_estatico(img: Image.Image, destino: Path) -> bool:
    im = encuadrar(img)
    for q in (90, 80, 70, 60, 50, 40):
        im.save(destino, "WEBP", quality=q, method=6)
        if destino.stat().st_size <= LIM_ESTATICO:
            return True
    return False


def guardar_animado(img: Image.Image, destino: Path, origen: Path) -> bool:
    reales = duraciones_webp(origen)
    frames, durs, total = [], [], 0
    for i in range(img.n_frames):
        img.seek(i)
        d = reales[i] if i < len(reales) and reales[i] > 0 else 50
        if total + d > MAX_MS:
            break
        frames.append(encuadrar(img))
        durs.append(max(d, 10))
        total += d

    # Si pesa demasiado: bajar calidad y, si hace falta, quitar frames.
    for salto in (1, 2, 3):
        fr = frames[::salto]
        du = [sum(durs[i:i + salto]) for i in range(0, len(durs), salto)]
        for q in (80, 65, 50, 40, 30, 20):
            fr[0].save(destino, "WEBP", save_all=True, append_images=fr[1:],
                       duration=du, loop=0, quality=q, method=4)
            if destino.stat().st_size <= LIM_ANIMADO:
                return True
    return False


def hacer_tray(origen: Path, destino: Path):
    img = Image.open(origen)
    img.seek(0)
    ImageOps.contain(img.convert("RGBA"), (96, 96)).save(destino, "PNG", optimize=True)
    lienzo = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    mini = ImageOps.contain(Image.open(destino).convert("RGBA"), (96, 96))
    lienzo.paste(mini, ((96 - mini.width) // 2, (96 - mini.height) // 2), mini)
    lienzo.save(destino, "PNG", optimize=True)


def crear_packs(archivos, animado: bool, packs_json: list):
    tipo = "animados" if animado else "estaticos"
    for n, inicio in enumerate(range(0, len(archivos), POR_PACK), start=1):
        grupo = archivos[inicio:inicio + POR_PACK]
        if len(grupo) < MIN_POR_PACK:
            log(f"[{tipo}] Sobran {len(grupo)} sticker(s): faltan para llegar a "
                  f"{MIN_POR_PACK}, quedan para la proxima vez.")
            continue

        ident = f"tiktok_{tipo}_{n:02d}"
        carpeta = OUT / ident
        carpeta.mkdir(parents=True, exist_ok=True)

        stickers = []
        for k, origen in enumerate(grupo, start=1):
            nombre = f"{k:03d}.webp"
            img = Image.open(origen)
            ok = guardar_animado(img, carpeta / nombre, origen) if animado \
                else guardar_estatico(img, carpeta / nombre)
            _avance["hecho"] += 1
            progreso(_avance["hecho"], _avance["total"],
                     "Convirtiendo animados" if animado else "Convirtiendo estáticos")
            if not ok:
                log(f"  ! {origen.name} no cabe en el limite, se omite")
                (carpeta / nombre).unlink(missing_ok=True)
                continue
            stickers.append({"image_file": nombre, "emojis": EMOJI})

        if len(stickers) < MIN_POR_PACK:
            log(f"[{ident}] Quedaron menos de {MIN_POR_PACK} validos, se omite el pack.")
            continue

        hacer_tray(grupo[0], carpeta / "tray.png")
        version = hashlib.md5("".join(s["image_file"] for s in stickers).encode()
                              + b"".join(o.name.encode() for o in grupo)).hexdigest()[:8]
        packs_json.append({
            "identifier": ident,
            "name": f"TikTok {tipo} {n}",
            "publisher": PUBLISHER,
            "tray_image_file": "tray.png",
            "image_data_version": version,
            "avoid_cache": False,
            "animated_sticker_pack": animado,
            "stickers": stickers,
        })
        log(f"[{ident}] {len(stickers)} stickers")
        if animado:
            conteo = []
            for st in stickers:
                with Image.open(carpeta / st["image_file"]) as im:
                    conteo.append(getattr(im, "n_frames", 1))
            en_movimiento = sum(1 for c in conteo if c > 1)
            log(f"    {en_movimiento} de {len(conteo)} tienen movimiento "
                  f"(frames por sticker: min {min(conteo)}, max {max(conteo)})")


def main():
    if not RAW.exists():
        raise SystemExit("No existe stickers_raw/. Ejecuta primero descargar.py")

    # Orden por fecha de descarga: los nuevos se agregan al final y no
    # desordenan los packs ya creados.
    todos = sorted(RAW.glob("*.webp"), key=lambda f: f.stat().st_mtime)
    estaticos, animados = [], []
    for f in todos:
        try:
            with Image.open(f) as im:
                (animados if getattr(im, "is_animated", False) else estaticos).append(f)
        except Exception:
            log(f"  ! No se pudo leer {f.name}")

    # Se regeneran los packs desde cero para no dejar archivos viejos.
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(exist_ok=True)
    packs_json = []
    # Total de stickers que se van a convertir (solo los que forman packs completos).
    total = 0
    for lista in (estaticos, animados):
        resto = len(lista) % POR_PACK
        total += len(lista) - (resto if 0 < resto < MIN_POR_PACK else 0)
    _avance["hecho"], _avance["total"] = 0, total
    crear_packs(estaticos, False, packs_json)
    crear_packs(animados, True, packs_json)

    with open(OUT / "contents.json", "w", encoding="utf-8") as fh:
        json.dump({"android_play_store_link": "", "ios_app_store_link": "",
                   "sticker_packs": packs_json}, fh, ensure_ascii=False, indent=2)
    log(f"\nListo: {len(packs_json)} pack(s) en {OUT}/")


if __name__ == "__main__":
    main()
