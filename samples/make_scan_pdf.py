"""Erzeugt Beispiel-PDFs ohne Textebene für die Texterkennung (fiktive Daten):

* samples/scan.pdf            – reiner Scan (Bild einer Seite, leicht schief, mit Rauschen)
* samples/mail_mit_scan.pdf   – als PDF gedruckte Mail (echter Text) mit eingescanntem Anhang;
                                die Anhang-Seite trägt eine Kopfzeile als echten Text

Aufruf:  .venv\\Scripts\\python samples\\make_scan_pdf.py
"""
from pathlib import Path

import numpy as np
import pymupdf as fitz

here = Path(__file__).parent
A4 = dict(width=595, height=842)


def scanned_image(text: str, title: str, dpi: int = 200, angle: float = 0.6, noise: float = 14.0, seed: int = 1):
    """Rendert Text wie einen Ausdruck und macht daraus ein „gescanntes“ Graustufenbild (JPEG-Bytes)."""
    doc = fitz.open()
    page = doc.new_page(**A4)
    page.insert_text((60, 80), title, fontsize=15, fontname="hebo")
    page.insert_textbox(fitz.Rect(60, 100, 535, 800), text, fontsize=11, fontname="helv", lineheight=1.4)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).astype(np.float32)
    # leicht schief (Scherung statt Rotation – kommt ohne OpenCV aus)
    h, w = img.shape
    shift = np.tan(np.radians(angle)) * (np.arange(h) - h / 2)
    out = np.empty_like(img)
    for y in range(h):
        out[y] = np.roll(img[y], int(round(shift[y])))
    rng = np.random.default_rng(seed)
    out = np.clip(out * 0.92 + 12 + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    gray = fitz.Pixmap(fitz.csGRAY, w, h, out.tobytes(), False)
    gray.set_dpi(dpi, dpi)
    return gray.tobytes("jpeg", jpg_quality=55)


BRIEF = (
    "Sehr geehrte Damen und Herren,\n\n"
    "hiermit kündige ich meinen Mietvertrag für die Wohnung in der Gartenstraße 17,\n"
    "04109 Leipzig, fristgerecht zum 31.12.2026.\n\n"
    "Die Kaution überweisen Sie bitte auf mein Konto:\n"
    "IBAN DE75 5121 0800 1245 1261 99\n\n"
    "Für die Wohnungsübergabe erreichen Sie mich unter 0151 23456789\n"
    "oder per E-Mail an lena.fischer@example.de.\n\n"
    "Mit freundlichen Grüßen\n\n"
    "Lena Fischer\n"
    "geb. 02.05.1991"
)

AUSWEIS = (
    "Name: Kaya, Deniz\n"
    "Anschrift: Am Stadtpark 3, 90402 Nürnberg\n"
    "Geburtsdatum: 17.11.1984\n"
    "Telefon: 0911 4455667\n"
    "Steuer-ID: 57549285017\n\n"
    "Der Antragsteller bestätigt die Richtigkeit der Angaben.\n"
    "Nürnberg, 22.09.2026"
)


def scan() -> Path:
    doc = fitz.open()
    page = doc.new_page(**A4)
    page.insert_image(page.rect, stream=scanned_image(BRIEF, "Kündigung Mietvertrag"))
    doc.set_metadata({"producer": "Scanner XY", "title": ""})
    out = here / "scan.pdf"
    doc.save(out, garbage=3, deflate=True)
    return out


def mail_mit_scan() -> Path:
    doc = fitz.open()
    p1 = doc.new_page(**A4)
    p1.insert_textbox(fitz.Rect(60, 60, 535, 800), (
        "Von: Deniz Kaya <d.kaya@example.de>\n"
        "An: Bürgerservice <buergerservice@stadt.example>\n"
        "Datum: 22.09.2026 09:14\n"
        "Betreff: Antrag Anwohnerparkausweis\n"
        "Anhang: antrag_kaya.pdf\n\n"
        "Guten Tag,\n\n"
        "anbei sende ich Ihnen den ausgefüllten Antrag für einen Anwohnerparkausweis.\n"
        "Das Kennzeichen meines Fahrzeugs lautet N-DK 4711.\n\n"
        "Viele Grüße\n"
        "Deniz Kaya"
    ), fontsize=10.5, fontname="helv", lineheight=1.35)
    p2 = doc.new_page(**A4)
    p2.insert_image(fitz.Rect(30, 40, 565, 812), stream=scanned_image(AUSWEIS, "Antrag Anwohnerparkausweis", seed=2))
    # Kopf-/Fußzeile, die das Mailprogramm als echten Text druckt
    p2.insert_text((30, 28), "Anhang: antrag_kaya.pdf", fontsize=8, fontname="helv")
    p2.insert_text((280, 830), "Seite 2 von 2", fontsize=8, fontname="helv")
    out = here / "mail_mit_scan.pdf"
    doc.save(out, garbage=3, deflate=True)
    return out


if __name__ == "__main__":
    for path in (scan(), mail_mit_scan()):
        print("ok", path)
