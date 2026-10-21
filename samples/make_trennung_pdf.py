"""Erzeugt samples/trennung.pdf – Namen mit Silbentrennung am Zeilenende (fiktive Daten).

Die Zeilen werden einzeln gesetzt, damit die Trennungen genau so im PDF stehen, wie ein
Textprogramm sie beim Blocksatz erzeugt („Bergen-⏎thaler“). Enthalten sind auch echte Bindestriche
am Zeilenende (Doppelname „Schneider-⏎Kowalczyk“), die nicht zusammengezogen werden dürfen.

Aufruf:  .venv\\Scripts\\python samples\\make_trennung_pdf.py
"""
from pathlib import Path

import pymupdf as fitz

here = Path(__file__).parent

LINES = [
    "Bescheid über die Kostenerstattung",
    "",
    "Sehr geehrte Damen und Herren,",
    "",
    "wir bestätigen den Eingang des Antrags von Herrn Maximilian Bergen-",
    "thaler aus Hamburg vom 2. Oktober 2026. Die Unterlagen von Frau Katharina Wester-",
    "mann liegen ebenfalls vor. Ansprechpartnerin im Fachbereich ist Frau Anna Schneider-",
    "Kowalczyk aus Leipzig. Die Erstattung an Herrn Bergenthaler erfolgt in den nächsten",
    "Wochen; Rückfragen beantwortet Herr Jonas Lichten-",
    "berger.",
    "",
    "Mit freundlichen Grüßen",
    "Ihre Servicestelle",
]


def main() -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for i, line in enumerate(LINES):
        if line:
            page.insert_text((60, y), line, fontsize=15 if i == 0 else 10.5,
                             fontname="hebo" if i == 0 else "helv")
        y += 22 if i == 0 else 15
    target = here / "trennung.pdf"
    doc.set_metadata({"title": "Beispiel Silbentrennung", "author": "pii-redact (Beispiel)"})
    doc.save(target, garbage=4, deflate=True)
    doc.close()
    return target


if __name__ == "__main__":
    print(main())
