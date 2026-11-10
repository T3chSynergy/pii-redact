"""Nicht angezeigter PDF-Inhalt: außerhalb des Seitenbereichs (Zuschnitt, neben dem Blatt) und auf
ausgeblendeten Ebenen. Er wird beim Laden entfernt; gelingt das nicht nachweislich, wird er eingeblendet."""

import pymupdf
import pytest

from pii_redact.core import Level, ReplaceMode, load_document, pdf_hidden, redact_pdf, verify_pdf
from pii_redact.core.pdf_hidden import _instructions


def _load(tmp_path, data: bytes):
    path = tmp_path / "test.pdf"
    path.write_bytes(data)
    return load_document(path, ocr=False)


def _notices(doc):
    return {n.kind: n for n in doc.notices}


def _in_file(pdf_bytes: bytes, word: str = "Bergenthaler") -> bool:
    """Steht das Wort noch in einem der Datenströme der Datei (als Text oder hexadezimal)?"""
    pdf = pymupdf.open(stream=pdf_bytes)
    data = b"".join(pdf.xref_stream(x) or b"" for x in range(1, pdf.xref_length()))
    return word.encode() in data or word.encode().hex().encode() in data.lower()


def _images(pages):
    return [p.get_pixmap(dpi=50).samples for p in pages]


def _same_inside(a: bytes, b: bytes) -> bool:
    """Gleiches Seitenbild – ohne die äußerste Bildpunktreihe (ragt ein Bild über den Rand, werden seine
    Bildpunkte außerhalb geleert; beim Vergrößern mischt die Darstellung sie in die letzte Reihe hinein)."""
    import numpy as np

    for pa, pb in zip(pymupdf.open(stream=a), pymupdf.open(stream=b), strict=True):
        xa, xb = pa.get_pixmap(dpi=50), pb.get_pixmap(dpi=50)
        if (xa.width, xa.height) != (xb.width, xb.height):
            return False
        arr = [np.frombuffer(x.samples, np.uint8).reshape(x.height, x.width, 3)[1:-1, 1:-1] for x in (xa, xb)]
        if not np.array_equal(*arr):
            return False
    return True


def _square(color=(200, 0, 0)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 400))
    pix.set_rect(pix.irect, color)
    return pix


# ---------------------------------------------------------------- außerhalb des Seitenbereichs
def _cropped_pdf(offset: bool = False, inherited: bool = False, rotation: int = 0) -> bytes:
    """Seite, von der nur die obere Hälfte angezeigt wird; dazu Inhalt neben dem Blatt."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "Sichtbar: Frau Zofia Jaeger")
    page.insert_text((72, 700), "Abgeschnitten: Herr Karl Bergenthaler")
    page.insert_text((72, 900), "Neben dem Blatt: Herr Karl Bergenthaler")
    page.draw_line((300, 300), (300, 600))                                   # ragt über die Schnittkante
    page.draw_bezier((100, 720), (150, 690), (200, 750), (250, 720))         # liegt ganz im abgeschnittenen Teil
    page.insert_image(pymupdf.Rect(400, 350, 480, 450), pixmap=_square())   # ragt über die Schnittkante
    if offset:
        pdf.xref_set_key(page.xref, "MediaBox", "[10 20 605 862]")
    holder = int(pdf.xref_get_key(page.xref, "Parent")[1].split()[0]) if inherited else page.xref
    pdf.xref_set_key(holder, "CropBox", "[10 462 605 862]" if offset else "[0 442 595 842]")
    if rotation:
        pdf = pymupdf.open(stream=pdf.tobytes())
        pdf[0].set_rotation(rotation)
    return pdf.tobytes()


@pytest.mark.parametrize("offset", [False, True], ids=["Nullpunkt", "versetzte Seite"])
@pytest.mark.parametrize("inherited", [False, True], ids=["an der Seite", "geerbt"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_content_outside_page_is_removed(tmp_path, offset, inherited, rotation):
    original = _cropped_pdf(offset, inherited, rotation)
    assert _in_file(original)

    doc = _load(tmp_path, original)
    assert "Zofia Jaeger" in doc.text and "Bergenthaler" not in doc.text
    notice = _notices(doc)["removed"]
    assert notice.level == Level.INFO and notice.pages == [0] and "außerhalb des Seitenbereichs" in notice.detail
    assert not doc.critical_notices
    assert _same_inside(doc.pdf_bytes, original)                # die Seite sieht aus wie vorher
    assert not _in_file(doc.pdf_bytes)

    out = redact_pdf(doc, [], ReplaceMode.BLACKOUT)
    assert verify_pdf(out, []) == []
    assert not _in_file(out)
    page = pymupdf.open(stream=out)[0]
    assert pdf_hidden.is_cropped(page) and page.rotation == rotation       # Zuschnitt und Drehung bleiben


def test_image_across_the_edge_loses_hidden_pixels(tmp_path):
    doc = _load(tmp_path, _cropped_pdf())
    pdf = pymupdf.open(stream=doc.pdf_bytes)
    pdf.xref_set_key(pdf[0].xref, "CropBox", "[0 0 595 842]")              # Zuschnitt zur Probe aufheben
    page = pymupdf.open(stream=pdf.tobytes())[0]
    assert page.get_text().strip() == "Sichtbar: Frau Zofia Jaeger"
    assert page.get_pixmap(clip=pymupdf.Rect(400, 352, 480, 398)).color_count() == 1     # sichtbarer Teil: rot
    assert page.get_pixmap(clip=pymupdf.Rect(400, 402, 480, 450)).pixel(10, 10) == (255, 255, 255)
    assert page.get_drawings()[0]["rect"].y1 == pytest.approx(600)        # Linie über die Kante bleibt ganz
    assert len(page.get_drawings()) == 1                                    # Kurve im abgeschnittenen Teil ist weg


def test_verify_reports_content_outside_page():
    problems = verify_pdf(_cropped_pdf(), [])
    assert any("außerhalb des sichtbaren Bereichs" in p for p in problems)


def test_ordinary_and_cropped_pages_without_hidden_content_are_untouched(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Alles sichtbar")
    page.draw_rect(pymupdf.Rect(-10, -10, 605, 60), fill=(0.9, 0.9, 0.9))     # Hintergrund mit Überstand
    page.set_cropbox(pymupdf.Rect(20, 20, 575, 822))                          # zugeschnitten, aber nichts dahinter
    data = pdf.tobytes()
    doc = _load(tmp_path, data)
    assert not doc.notices and doc.pdf_bytes == data


def test_uncrop_when_removal_fails(tmp_path, monkeypatch):
    """Lässt sich der abgeschnittene Teil nicht entfernen, wird er eingeblendet und geprüft."""
    monkeypatch.setattr(pdf_hidden, "_redact_outside", lambda page: None)
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "Sichtbar")
    page.insert_text((72, 700), "Abgeschnitten: Herr Karl Bergenthaler")
    pdf.xref_set_key(page.xref, "CropBox", "[0 442 595 842]")
    doc = _load(tmp_path, pdf.tobytes())
    assert "Karl Bergenthaler" in doc.text
    notice = _notices(doc)["cropped"]
    assert notice.critical and notice.pages == [0]
    assert not pdf_hidden.is_cropped(pymupdf.open(stream=doc.pdf_bytes)[0])


def test_content_beside_sheet_that_cannot_be_removed_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_hidden, "_redact_outside", lambda page: None)
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Sichtbar")
    page.insert_text((72, 900), "Neben dem Blatt")
    doc = _load(tmp_path, pdf.tobytes())
    notice = _notices(doc)["outside_left"]
    assert notice.critical and notice.pages == [0] and "Neben dem Blatt" not in doc.text
    problems = verify_pdf(redact_pdf(doc, [], ReplaceMode.BLACKOUT), [])
    assert any("außerhalb des sichtbaren Bereichs" in p for p in problems)


# ---------------------------------------------------------------- Ebenen
def _layered_pdf() -> bytes:
    """Ausgeblendete Ebene mit Text, Bild, Fläche, Kurve und eingebetteter Seite; dazu eine sichtbare Ebene
    und normaler Inhalt davor und danach."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Sichtbar eins")
    off = pdf.add_ocg("Intern", on=False)
    on = pdf.add_ocg("Entwurf", on=True)
    page.insert_text((72, 200), "Intern: Herr Karl Bergenthaler", oc=off, color=(1, 0, 0))
    page.insert_image(pymupdf.Rect(300, 180, 380, 260), pixmap=_square(), oc=off)
    page.draw_rect(pymupdf.Rect(72, 300, 200, 340), color=(0, 0, 1), fill=(0, 1, 0), oc=off)
    page.draw_bezier((100, 400), (150, 370), (200, 430), (250, 400), oc=off)
    other = pymupdf.open()
    other.new_page(width=200, height=50).insert_text((10, 30), "Eingebettet: Bergenthaler")
    page.show_pdf_page(pymupdf.Rect(72, 450, 272, 500), other, 0, oc=off)
    page.insert_text((72, 550), "Sichtbare Ebene", oc=on)
    page.insert_image(pymupdf.Rect(300, 520, 380, 600), pixmap=_square((0, 0, 200)), oc=on)
    page.insert_text((72, 650), "Sichtbar zwei")
    pdf.new_page().insert_text((72, 100), "Seite ohne Ebenen")
    return pdf.tobytes()


def test_hidden_layer_content_is_removed(tmp_path):
    original = _layered_pdf()
    shown_before = _images(pymupdf.open(stream=original))
    doc = _load(tmp_path, original)
    assert [line for line in doc.text.splitlines() if line] == ["Sichtbar eins", "Sichtbare Ebene", "Sichtbar zwei",
                                                                "Seite ohne Ebenen"]
    notice = _notices(doc)["removed"]
    assert notice.level == Level.INFO and notice.pages == [0] and "ausgeblendeter Ebenen" in notice.detail
    assert not doc.critical_notices
    loaded = pymupdf.open(stream=doc.pdf_bytes)
    assert loaded.get_ocgs() == {} and _images(loaded) == shown_before
    assert len(loaded[0].get_drawings()) == 0 and len(loaded[0].get_image_info()) == 1

    out = redact_pdf(doc, [], ReplaceMode.BLACKOUT)
    assert verify_pdf(out, []) == []
    assert _in_file(original) and not _in_file(out)   # auch die eingebettete Seite ist aus der Datei
    assert len(pymupdf.open(stream=out)[0].get_images()) == 1   # nur das Bild der sichtbaren Ebene


def test_layers_are_shown_when_removal_is_not_safe(tmp_path):
    """Zusammengesetzte Sichtbarkeit (OCMD) wird nicht entfernt, sondern eingeblendet und geprüft."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    a, b = pdf.add_ocg("A", on=False), pdf.add_ocg("B", on=True)
    page.insert_text((72, 100), "Sichtbar")
    page.insert_text((72, 200), "Alle an: Herr Karl Bergenthaler", oc=pdf.set_ocmd(ocgs=[a, b], policy="AllOn"))
    page.insert_text((72, 300), "A aus: Frau Zofia Jaeger", oc=pdf.set_ocmd(ocgs=[a], policy="AnyOff"))
    doc = _load(tmp_path, pdf.tobytes())
    assert "Karl Bergenthaler" in doc.text and "Zofia Jaeger" in doc.text
    notice = _notices(doc)["layers_shown"]
    assert notice.critical and notice.pages == [0]
    assert pymupdf.open(stream=doc.pdf_bytes).get_ocgs() == {}


def test_layers_are_shown_when_page_would_look_different(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_hidden, "_strip_hidden_layers", lambda pdf: None)   # „Entfernen“ ohne Wirkung
    doc = _load(tmp_path, _layered_pdf())
    assert "Karl Bergenthaler" in doc.text and _notices(doc)["layers_shown"].pages == [0]


def test_visible_layers_give_no_notice(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Text auf einer Ebene", oc=pdf.add_ocg("Entwurf", on=True))
    doc = _load(tmp_path, pdf.tobytes())
    assert "Text auf einer Ebene" in doc.text and not doc.notices
    assert pymupdf.open(stream=doc.pdf_bytes).get_ocgs() == {}
    assert "Ebenen noch vorhanden" in verify_pdf(pdf.tobytes(), [])


# ---------------------------------------------------------------- Inhaltsstrom zerlegen
def test_instructions_keep_compound_operands_together():
    stream = (b"q 1 0 0 1 72 700 cm % Kommentar ) ( \n"
              b"/OC /MC0 BDC BT /F1 11 Tf (Klammer \\) und (verschachtelt)) Tj [(A) -20 <4142> (B)] TJ ET EMC\n"
              b"/Span <</ActualText (x) /Lang (de)>> BDC EMC\n"
              b"BI /W 2 /H 1 /BPC 8 /CS /G ID \x00EI\xff EI Q")
    got = [(ops, op) for ops, op in _instructions(stream)]
    ops = [op for _, op in got]
    assert ops == [b"q", b"cm", b"BDC", b"BT", b"Tf", b"Tj", b"TJ", b"ET", b"EMC", b"BDC", b"EMC", b"BI", b"Q"]
    assert got[2][0] == [b"/OC", b"/MC0"]
    assert got[5][0] == [b"(Klammer \\) und (verschachtelt))"]
    assert got[6][0] == [b"[(A) -20 <4142> (B)]"]
    assert got[9][0] == [b"/Span", b"<</ActualText (x) /Lang (de)>>"]
    assert got[11][0][0].startswith(b"BI /W 2") and got[11][0][0].endswith(b"\xff EI")
