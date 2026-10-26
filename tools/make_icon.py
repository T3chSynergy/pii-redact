"""Erzeugt das Programmsymbol von pii-redact aus einfachen Grundformen (Pillow).

Motiv: blaues Quadrat mit abgerundeten Ecken, darauf ein weißes Blatt mit zwei schwarzen Balken (geschwärzter
Text) und zwei grauen Linien (normaler Text). Keine Schrift, keine fremde Grafik – das Symbol ist selbst erstellt.

Ausgabe:
* ``packaging/pii-redact.ico`` – Symbol der EXE-Dateien (16, 24, 32, 48, 64, 128 und 256 Pixel)
* ``src/pii_redact/resources/icon.png`` – Fenstersymbol (256 Pixel)

Das Skript zeichnet die Dateien vom 26.09.2026 pixelgenau nach (alle sieben Größen geprüft). Bewusst ohne
Kantenglättung: das 256er-Bild besteht nur aus den fünf Farben unten, die kleineren Größen rechnet Pillow beim
Speichern der ICO-Datei herunter.

Aufruf:  python tools/make_icon.py              Dateien neu schreiben
         python tools/make_icon.py --pruefen    nur vergleichen (Rückgabe 1 bei Abweichung)
         python tools/make_icon.py --ausgabe ORDNER
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "packaging" / "pii-redact.ico"
PNG = ROOT / "src" / "pii_redact" / "resources" / "icon.png"

SIZE = 256
BLUE = (28, 126, 214, 255)     # #1C7ED6 – Hintergrund
WHITE = (255, 255, 255, 255)   # Blatt
DARK = (33, 37, 41, 255)       # #212529 – geschwärzter Text
GREY = (173, 181, 189, 255)    # #ADB5BD – normaler Text

# Formen auf dem 256er-Raster: (links, oben, rechts, unten), Endpunkte eingeschlossen
BACKGROUND = ((0, 0, 255, 255), 46)    # Rechteck, Eckenradius
PAGE = ((51, 35, 205, 221), 12)
LINES = [
    ((71, 65, 133, 87), DARK),
    ((71, 104, 185, 116), GREY),
    ((71, 133, 150, 155), DARK),
    ((71, 172, 167, 184), GREY),
]


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(BACKGROUND[0], radius=BACKGROUND[1], fill=BLUE)
    d.rounded_rectangle(PAGE[0], radius=PAGE[1], fill=WHITE)
    for box, color in LINES:
        d.rectangle(box, fill=color)
    return img


def same_image(a: Path, b: Path) -> bool:
    """Gleiche Pixel in allen Größen (ICO) bzw. im Bild (PNG) – die Dateibytes dürfen sich unterscheiden."""
    with Image.open(a) as x, Image.open(b) as y:
        sizes = sorted(x.info.get("sizes") or {x.size})
        if sizes != sorted(y.info.get("sizes") or {y.size}):
            return False
        for size in sizes:
            if x.format == "ICO":
                x.size = y.size = size
            diff = ImageChops.difference(x.convert("RGBA"), y.convert("RGBA"))
            if diff.getbbox(alpha_only=False) is not None:   # Standard prüft bei RGBA nur die Transparenz
                return False
    return True


def write(target_dir: Path | None = None) -> tuple[Path, Path]:
    ico = (target_dir / ICO.name) if target_dir else ICO
    png = (target_dir / PNG.name) if target_dir else PNG
    ico.parent.mkdir(parents=True, exist_ok=True)
    png.parent.mkdir(parents=True, exist_ok=True)
    img = draw_icon()
    img.save(ico, format="ICO")   # Pillow-Standard: 16, 24, 32, 48, 64, 128, 256 Pixel
    img.save(png, format="PNG")
    return ico, png


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Programmsymbol von pii-redact erzeugen.")
    p.add_argument("--pruefen", action="store_true", help="nur mit den vorhandenen Dateien vergleichen")
    p.add_argument("--ausgabe", type=Path, help="in diesen Ordner schreiben statt ins Projekt")
    args = p.parse_args(argv)
    if args.pruefen:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ico, png = write(Path(tmp))
            ok = same_image(ico, ICO) and same_image(png, PNG)
        print("Symbol entspricht dem Skript." if ok else "Symbol weicht vom Skript ab.")
        return 0 if ok else 1
    for f in write(args.ausgabe):
        print(f"geschrieben: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
