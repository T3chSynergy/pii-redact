"""Erzeugt samples/unterschrift.pdf (fiktive Daten) zum Üben der freien Bereichs-Schwärzung:

* Brief mit einer als Vektorgrafik gezeichneten Unterschrift,
* einem Passfoto (Rasterbild) und
* einer handschriftlichen Notiz (ebenfalls Vektorgrafik) – alles ohne Text, also von der
  automatischen Erkennung nicht erfassbar.

Aufruf:  .venv\\Scripts\\python samples\\make_signature_pdf.py
"""
import math
from pathlib import Path

import numpy as np
import pymupdf as fitz

here = Path(__file__).parent


def scribble(page: fitz.Page, x: float, y: float, width: float, seed: int, height: float = 18) -> None:
    """Eine „Unterschrift“ bzw. Handschrift aus Bézierkurven."""
    rng = np.random.default_rng(seed)
    shape = page.new_shape()
    pts = [fitz.Point(x + width * i / 14, y + rng.uniform(-height, height) * math.sin(i)) for i in range(15)]
    for a, b in zip(pts, pts[1:]):
        c1 = fitz.Point(a.x + rng.uniform(0, 8), a.y - rng.uniform(4, 14))
        c2 = fitz.Point(b.x - rng.uniform(0, 8), b.y + rng.uniform(4, 14))
        shape.draw_bezier(a, c1, c2, b)
    shape.finish(color=(0.05, 0.1, 0.45), width=1.3, closePath=False)
    shape.commit()


def portrait() -> fitz.Pixmap:
    """Kleines Platzhalter-„Passfoto“ (Kopf und Schultern als Graustufenbild)."""
    h, w = 160, 120
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), 215, np.uint8)
    img[((xx - 60) / 28) ** 2 + ((yy - 62) / 36) ** 2 < 1] = 150          # Kopf
    img[(((xx - 60) / 58) ** 2 + ((yy - 175) / 60) ** 2 < 1)] = 90        # Schultern
    return fitz.Pixmap(fitz.csGRAY, w, h, img.tobytes(), False)


def main() -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Bewerbung als Sachbearbeiterin", fontsize=15, fontname="hebo")
    page.insert_textbox(fitz.Rect(60, 105, 400, 330), (
        "Sehr geehrte Damen und Herren,\n\n"
        "hiermit bewerbe ich mich auf die ausgeschriebene Stelle in der\n"
        "Personalabteilung. Meine Unterlagen finden Sie in der Anlage.\n\n"
        "Über eine Einladung zu einem Gespräch freue ich mich.\n\n"
        "Mit freundlichen Grüßen"
    ), fontsize=10.5, fontname="helv", lineheight=1.4)
    page.insert_image(fitz.Rect(440, 100, 530, 220), pixmap=portrait())      # Passfoto
    scribble(page, 62, 360, 150, seed=3)                                      # Unterschrift
    page.draw_line(fitz.Point(60, 385), fitz.Point(260, 385), color=(0, 0, 0), width=0.5)
    page.insert_text((60, 398), "Carla Neumann", fontsize=9, fontname="helv")
    # handschriftliche Randnotiz
    page.insert_text((60, 470), "Eingangsvermerk:", fontsize=9, fontname="helv")
    scribble(page, 150, 468, 110, seed=11, height=6)
    doc.set_metadata({"author": "Carla Neumann", "title": "Bewerbung"})
    out = here / "unterschrift.pdf"
    doc.save(out, garbage=3, deflate=True)
    return out


if __name__ == "__main__":
    print("ok", main())
