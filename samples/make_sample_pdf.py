"""Erzeugt samples/beispiel.pdf aus samples/beispiel.txt (fiktive Daten)."""
from pathlib import Path

import pymupdf as fitz

here = Path(__file__).parent
text = (here / "beispiel.txt").read_text(encoding="utf-8").replace("–", "-")  # Helvetica ohne Gedankenstrich
doc = fitz.open()
page = doc.new_page(width=595, height=842)  # A4
rect = fitz.Rect(60, 60, 535, 800)
page.insert_textbox(rect, text, fontsize=10.5, fontname="helv", lineheight=1.35)
page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(60, 60, 200, 75), "uri": "mailto:m.bergmann@example.de"})
doc.set_metadata({"author": "Jonas Weber", "title": "Personalakte Bergmann"})
doc.save(here / "beispiel.pdf")
print("ok", here / "beispiel.pdf")
