"""Erzeugt Beispiel-PDFs mit Zusatzinhalten (fiktive Daten):

* samples/kommentare.pdf – Kommentare, Markierungen, Stempel, Dateianhang, Lesezeichen,
  Metadaten (inkl. XMP) und Links
* samples/formular.pdf   – ausgefülltes Formular (Textfelder, Kontrollkästchen, Auswahlliste)

Aufruf:  .venv\\Scripts\\python samples\\make_extras_pdf.py
"""
from pathlib import Path

import pymupdf as fitz

here = Path(__file__).parent
A4 = dict(width=595, height=842)


def text_page(doc: fitz.Document, title: str, body: str) -> fitz.Page:
    page = doc.new_page(**A4)
    page.insert_text((60, 80), title, fontsize=15, fontname="hebo")
    page.insert_textbox(fitz.Rect(60, 100, 535, 800), body, fontsize=10.5, fontname="helv", lineheight=1.35)
    return page


def find(page: fitz.Page, needle: str) -> fitz.Rect:
    hits = page.search_for(needle)
    assert hits, needle
    return hits[0]


def kommentare() -> Path:
    doc = fitz.open()
    text_page(doc, "Gesprächsnotiz Personalgespräch", (
        "Datum: 18.08.2026\n"
        "Teilnehmende: Sabine Krause (Teamleitung), Thomas Albrecht (Mitarbeiter)\n\n"
        "Herr Albrecht bittet um eine Reduzierung seiner Arbeitszeit auf 30 Stunden ab dem 01.11.2026.\n"
        "Die Teamleitung unterstützt den Antrag. Eine Vertretung für die Kundenbetreuung wird bis\n"
        "Ende Oktober geregelt.\n\n"
        "Rückfragen bitte an t.albrecht@example.de oder telefonisch unter 040 55512345.\n\n"
        "Nächste Schritte\n"
        "1. Antrag an die Personalabteilung weiterleiten\n"
        "2. Vertretungsplan erstellen\n"
        "3. Gespräch nach drei Monaten wiederholen"
    ))
    text_page(doc, "Anlage: Vertretungsplan", (
        "Kundenbetreuung Nord: Vertretung durch Frau Meral Yilmaz\n"
        "Kundenbetreuung Süd: unverändert\n\n"
        "Der Plan gilt ab dem 01.11.2026 und wird quartalsweise überprüft."
    ))
    p1, p2 = doc[0], doc[1]  # neue Seiten machen ältere Seitenobjekte ungültig

    # --- Kommentare und Markierungen (Inhalte + Autor enthalten personenbezogene Daten)
    note = p1.add_text_annot((470, 118), "Hat Herr Albrecht die Elternzeit schon beantragt? Privat: 0172 9876543")
    note.set_info(title="Sabine Krause", subject="Rückfrage")
    note.update()

    hl = p1.add_highlight_annot(find(p1, "30 Stunden"))
    hl.set_info(content="Mit Betriebsrat abstimmen - Ansprechpartner Klaus Brenner", title="Sabine Krause")
    hl.update()

    ul = p1.add_underline_annot(find(p1, "t.albrecht@example.de"))
    ul.set_info(content="Private Adresse: thomas.albrecht@mail.example", title="Personalabteilung")
    ul.update()

    free = p1.add_freetext_annot(fitz.Rect(330, 420, 535, 470),
                                 "Vermerk: Gespräch mit Frau Krause am 20.08. telefonisch bestätigt",
                                 fontsize=9, text_color=(0.7, 0, 0), fill_color=(1, 1, 0.8))
    free.set_info(title="Jonas Weber")
    free.update()

    stamp = p1.add_stamp_annot(fitz.Rect(400, 700, 535, 740), stamp=fitz.STAMP_Confidential)
    stamp.set_info(title="Jonas Weber")
    stamp.update()

    att = p2.add_file_annot((500, 110), b"Krankmeldung Thomas Albrecht, 03.-07.08.2026",
                            "krankmeldung_albrecht.txt", desc="Krankmeldung")
    att.set_info(title="Sabine Krause")
    att.update()

    note2 = p2.add_text_annot((470, 118), "Frau Yilmaz ist ab 15.12. im Urlaub - Ersatz klären")
    note2.set_info(title="Thomas Albrecht")
    note2.update()

    # --- Links, Lesezeichen, Anhang, Metadaten
    p1.insert_link({"kind": fitz.LINK_URI, "from": find(p1, "t.albrecht@example.de"),
                    "uri": "mailto:t.albrecht@example.de"})
    doc.set_toc([
        [1, "Gesprächsnotiz Thomas Albrecht", 1],
        [2, "Antrag Teilzeit (30 Std.)", 1],
        [1, "Vertretung durch Meral Yilmaz", 2],
    ])
    doc.embfile_add("gespraechsprotokoll.txt", b"Protokoll: Sabine Krause / Thomas Albrecht, 18.08.2026",
                    desc="Originalprotokoll")
    doc.set_metadata({"author": "Sabine Krause", "title": "Personalgespräch Thomas Albrecht",
                      "subject": "Teilzeitantrag", "keywords": "Albrecht; Teilzeit; vertraulich"})
    doc.set_xml_metadata(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq>'
        "<rdf:li>Sabine Krause</rdf:li></rdf:Seq></dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    out = here / "kommentare.pdf"
    doc.save(out, garbage=3, deflate=True)
    return out


def formular() -> Path:
    doc = fitz.open()
    page = doc.new_page(**A4)
    page.insert_text((60, 80), "Antrag auf Dienstreise", fontsize=15, fontname="hebo")
    page.insert_text((60, 100), "Bitte vollständig ausfüllen und an die Reisestelle senden.", fontsize=10, fontname="helv")

    def field(y: float, label: str, name: str, value: str, width: float = 300) -> None:
        page.insert_text((60, y + 13), label, fontsize=10, fontname="helv")
        w = fitz.Widget()
        w.field_name, w.field_type = name, fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(210, y, 210 + width, y + 18)
        w.field_value, w.text_fontsize = value, 10
        w.border_color, w.fill_color = (0.5, 0.5, 0.5), (0.95, 0.97, 1)
        page.add_widget(w)

    field(130, "Name, Vorname", "name", "Albrecht, Thomas")
    field(160, "Personalnummer", "persnr", "P-204711", 120)
    field(190, "Abteilung", "abteilung", "Vertrieb Nord")
    field(220, "Telefon", "telefon", "040 55512345", 160)
    field(250, "Reiseziel", "ziel", "München, Messe Bauma")
    field(280, "Zeitraum", "zeitraum", "14.10.2026 bis 16.10.2026", 200)
    field(310, "Kostenstelle", "kst", "4711", 80)
    field(340, "IBAN (Erstattung)", "iban", "DE02 1203 0000 0000 2020 51")

    page.insert_text((60, 393), "Verkehrsmittel", fontsize=10, fontname="helv")
    combo = fitz.Widget()
    combo.field_name, combo.field_type = "verkehrsmittel", fitz.PDF_WIDGET_TYPE_COMBOBOX
    combo.choice_values = ["Bahn", "Flugzeug", "Dienstwagen", "Privat-Pkw"]
    combo.rect, combo.field_value, combo.text_fontsize = fitz.Rect(210, 380, 360, 398), "Bahn", 10
    page.add_widget(combo)

    page.insert_text((60, 423), "Hotel wird benötigt", fontsize=10, fontname="helv")
    box = fitz.Widget()
    box.field_name, box.field_type = "hotel", fitz.PDF_WIDGET_TYPE_CHECKBOX
    box.rect, box.field_value = fitz.Rect(210, 410, 224, 424), True
    page.add_widget(box)

    page.insert_text((60, 470), "Genehmigt durch (Name, Datum)", fontsize=10, fontname="helv")
    w = fitz.Widget()
    w.field_name, w.field_type = "genehmigt", fitz.PDF_WIDGET_TYPE_TEXT
    w.rect, w.field_value, w.text_fontsize = fitz.Rect(210, 457, 510, 475), "Sabine Krause, 02.10.2026", 10
    page.add_widget(w)

    doc.set_metadata({"author": "Thomas Albrecht", "title": "Dienstreiseantrag Albrecht"})
    out = here / "formular.pdf"
    doc.save(out, garbage=3, deflate=True)
    return out


if __name__ == "__main__":
    for path in (kommentare(), formular()):
        print("ok", path)
