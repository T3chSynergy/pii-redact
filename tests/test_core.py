"""Tests der Kernlogik (ohne Oberfläche)."""

from pathlib import Path

import pymupdf
import pytest
import spacy

from pii_redact.core import Finding, PiiAnalyzer, ReplaceMode, Settings, load_document, redact_pdf, redact_text, verify_pdf
from pii_redact.core.analyzer import apply_allow_list, clean_ner, find_terms, merge_overlaps

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MODEL = "de_core_news_md"
needs_model = pytest.mark.skipif(not spacy.util.is_package(MODEL), reason=f"spaCy-Modell {MODEL} fehlt")


# ---------------------------------------------------------------- reine Logik
def test_merge_prefers_pattern_type_and_unites_spans():
    text = "Anschrift: 10969 Berlin"
    a = Finding(11, 16, "DE_PLZ", "10969", 0.4, recognizer="DePlzRecognizer")
    b = Finding(17, 23, "LOCATION", "Berlin", 0.85, recognizer="SpacyRecognizer")
    c = Finding(11, 23, "DE_ADDRESS", "10969 Berlin", 0.55, recognizer="DeAddressRecognizer")
    (m,) = merge_overlaps([a, b, c], text)
    assert (m.start, m.end) == (11, 23)
    assert m.entity_type == "DE_ADDRESS"


def test_clean_ner_splits_markdown_and_newlines():
    text = "[Petra Hoffmann](mailto:p@example.de) Berlin\nTelefon"
    end = text.index(")") + 1
    f1 = Finding(1, end, "PERSON", text[1:end], 0.85, recognizer="SpacyRecognizer")
    f2 = Finding(end + 1, len(text), "LOCATION", text[end + 1:], 0.85, recognizer="SpacyRecognizer")
    out = clean_ner([f1, f2], text)
    assert [f.text for f in out] == ["Petra Hoffmann", "Berlin"]


def test_find_terms_whole_words_case_insensitive():
    text = "Projekt Adler; adlerhorst; ADLER."
    hits = find_terms(text, ["Adler"])
    assert [h.text for h in hits] == ["Adler", "ADLER"]


def test_allow_list():
    fs = [Finding(0, 5, "ORGANIZATION", "ACME ", 0.9), Finding(6, 9, "PERSON", "Max", 0.9)]
    assert [f.text for f in apply_allow_list(fs, ["acme"])] == ["Max"]


def test_redact_text_modes_and_offset_mapping():
    text = "Max trifft Anna und Max."
    fs = [
        Finding(0, 3, "PERSON", "Max"),
        Finding(11, 15, "PERSON", "Anna"),
        Finding(20, 23, "PERSON", "Max"),
    ]
    assert redact_text(text, fs, ReplaceMode.PLACEHOLDER).text == "[PERSON] trifft [PERSON] und [PERSON]."
    numbered = redact_text(text, fs, ReplaceMode.NUMBERED)
    assert numbered.text == "[PERSON_1] trifft [PERSON_2] und [PERSON_1]."
    black = redact_text(text, fs, ReplaceMode.BLACKOUT)
    assert len(black.text) == len(text) and "Max" not in black.text

    # Offset-Abbildung in beide Richtungen
    red = numbered
    o = text.index("trifft")
    r = red.to_redacted(o)
    assert red.text[r:r + 6] == "trifft"
    assert red.to_original(r) == o
    seg = red.segment_at_redacted(red.text.index("[PERSON_2]") + 2)
    assert seg is not None and text[seg.orig_start:seg.orig_end] == "Anna"


def test_inactive_findings_are_kept():
    text = "Max und Anna"
    fs = [Finding(0, 3, "PERSON", "Max", active=False), Finding(8, 12, "PERSON", "Anna")]
    assert redact_text(text, fs, ReplaceMode.PLACEHOLDER).text == "Max und [PERSON]"


def test_overlapping_manual_and_auto_findings():
    text = "Herr Max Mustermann"
    fs = [Finding(5, 8, "PERSON", "Max"), Finding(5, 19, "PERSON", "Max Mustermann", source="manuell")]
    assert redact_text(text, fs, ReplaceMode.PLACEHOLDER).text == "Herr [PERSON]"


def test_load_cp1252_text(tmp_path):
    p = tmp_path / "alt.txt"
    p.write_bytes("Grüße aus Köln\r\n".encode("cp1252"))
    doc = load_document(p)
    assert doc.text == "Grüße aus Köln\n"
    assert doc.encoding == "cp1252"


@pytest.mark.parametrize("name, office", [("brief.docx", True), ("liste.XLSX", True), ("bild.png", False)])
def test_unsupported_file_hint(tmp_path, name, office):
    from pii_redact.core import UnsupportedFileError

    p = tmp_path / name
    p.write_bytes(b"x")
    with pytest.raises(UnsupportedFileError) as exc:
        load_document(p)
    assert "wird nicht unterstützt" in str(exc.value)
    assert ("als PDF speichern" in str(exc.value)) is office


def test_pdf_char_mapping_matches_text():
    doc = load_document(SAMPLES / "beispiel.pdf")
    assert doc.is_pdf and doc.page_count == 1
    assert len(doc.text) == len(doc.char_boxes)
    i = doc.text.index("Lindenstraße")
    assert doc.char_boxes[i] is not None and doc.char_boxes[i].page == 0


# ---------------------------------------------------------------- mit Sprachmodell
@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer(MODEL)


@needs_model
def test_sample_txt_findings(analyzer):
    doc = load_document(SAMPLES / "beispiel.txt")
    found = {f.text: f.entity_type for f in analyzer.analyze(doc.text, Settings())}
    expected = {
        "Maximilian Bergmann": "PERSON",
        "14.03.1987": "DATE_TIME",
        "Lindenstraße 42a": "DE_ADDRESS",
        "10969 Berlin": "DE_ADDRESS",
        "+49 30 98765432": "PHONE_NUMBER",
        "0171 2345678": "PHONE_NUMBER",
        "m.bergmann@example.de": "EMAIL_ADDRESS",
        "DE89 3704 0044 0532 0130 00": "IBAN_CODE",
        "86095742719": "DE_TAX_ID",
        "65 170839 J 00 3": "DE_SOCIAL_SECURITY",
        "B-MB 1987": "DE_KFZ",
        "Jonas Weber": "PERSON",
    }
    for text, etype in expected.items():
        assert found.get(text) == etype, f"{text!r} nicht als {etype} erkannt: {found.get(text)}"
    # Keine Fehlalarme für normales Rechnungsdatum und Betrag
    assert "12.09.2026" not in found
    assert not any("Euro" in t for t in found)


@needs_model
def test_deny_list_and_allow_list(analyzer):
    text = "Projekt Seestern wird von Jonas Weber geleitet."
    s = Settings(deny_list=["Seestern"], allow_list=["Jonas Weber"])
    found = {f.text: f.entity_type for f in analyzer.analyze(text, s)}
    assert found == {"Seestern": "CUSTOM"}


@needs_model
def test_pdf_redaction_removes_text_and_metadata(analyzer):
    doc = load_document(SAMPLES / "beispiel.pdf")
    findings = analyzer.analyze(doc.text, Settings())
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER, labels=True)
    assert verify_pdf(data, findings) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        content = pdf[0].get_text()
        assert "Bergmann" not in content and "DE89" not in content
        assert "Personalabteilung" in content  # Nicht-PII bleibt erhalten
        assert "Rechnung vom 12.09.2026" in content
        assert not pdf.metadata.get("author")
        assert pdf[0].get_links() == []


@needs_model
def test_pdf_deactivated_finding_stays(analyzer):
    doc = load_document(SAMPLES / "beispiel.pdf")
    findings = analyzer.analyze(doc.text, Settings())
    hamburg = next(f for f in findings if f.text == "Hamburg")
    hamburg.active = False
    data = redact_pdf(doc, findings, ReplaceMode.BLACKOUT)
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert "Hamburg" in pdf[0].get_text()
    assert verify_pdf(data, findings) == []



def test_save_redacted_pdf_writes_safely(tmp_path, monkeypatch):
    """Ergebnis erst als .tmp, dann umbenannt; bei einem Fehler bleibt keine (halbe) Zieldatei liegen."""
    from pii_redact.core import redactor, save_redacted_pdf

    doc = load_document(SAMPLES / "beispiel.pdf")
    start = doc.text.index("Personalabteilung")
    findings = [Finding(start, start + 17, "CUSTOM", "Personalabteilung")]
    target = tmp_path / "out.pdf"
    assert save_redacted_pdf(doc, findings, Settings(), target) == []
    assert target.is_file() and not (tmp_path / "out.pdf.tmp").exists()
    with pymupdf.open(target) as pdf:
        assert "Personalabteilung" not in pdf[0].get_text()

    def broken(*_a, **_k):
        raise RuntimeError("Kontrolle fehlgeschlagen")

    monkeypatch.setattr(redactor, "verify_pdf", broken)
    target2 = tmp_path / "out2.pdf"
    with pytest.raises(RuntimeError):
        save_redacted_pdf(doc, findings, Settings(), target2)
    assert list(tmp_path.iterdir()) == [target]


def _pdf_with_extras(path: Path) -> None:
    """PDF mit Kommentaren, Formularfeld, Lesezeichen, Metadaten und Anhang."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Max Mustermann wohnt in Berlin.")
    note = page.add_text_annot((300, 100), "Anruf bei Erika Musterfrau")
    note.set_info(title="Hans Autor")
    note.update()
    free = page.add_freetext_annot(pymupdf.Rect(72, 200, 300, 240), "Freitext Erika Musterfrau")
    free.update()
    hl = page.add_highlight_annot(pymupdf.Rect(72, 88, 200, 104))
    hl.set_info(content="Markiert von Hans Autor", title="Hans Autor")
    hl.update()
    w = pymupdf.Widget()
    w.field_name, w.field_type = "name", pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect, w.field_value = pymupdf.Rect(72, 300, 300, 320), "Petra Hoffmann"
    page.add_widget(w)
    pdf.set_toc([[1, "Akte Max Mustermann", 1]])
    pdf.set_metadata({"author": "Hans Autor", "title": "Akte Mustermann"})
    pdf.embfile_add("anhang.txt", b"Erika Musterfrau")
    pdf.save(path)
    pdf.close()


def test_pdf_extras_are_stripped_on_load(tmp_path):
    src = tmp_path / "extras.pdf"
    _pdf_with_extras(src)
    doc = load_document(src)
    # Formularwert ist normaler Text geworden, Kommentartexte sind nicht mehr Teil des Dokuments
    assert "Petra Hoffmann" in doc.text
    assert "Erika" not in doc.text and "Hans" not in doc.text
    hint = " ".join(doc.warnings)
    assert "3 Kommentare/Markierungen" in hint and "1 Dateianhang" in hint and "1 Formularfeld" in hint
    with pymupdf.open(stream=doc.pdf_bytes, filetype="pdf") as pdf:
        assert list(pdf[0].annots()) == [] and list(pdf[0].widgets()) == [] and pdf.get_toc() == []


def test_pdf_redaction_removes_extras(tmp_path):
    src = tmp_path / "extras.pdf"
    _pdf_with_extras(src)
    doc = load_document(src)
    s = doc.text.index("Petra Hoffmann")
    findings = [Finding(s, s + 14, "PERSON", "Petra Hoffmann")]
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER)
    assert verify_pdf(data, findings) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        assert list(pdf[0].annots()) == [] and list(pdf[0].widgets()) == []
        assert pdf.get_toc() == [] and pdf.embfile_count() == 0
        assert not pdf.metadata.get("author") and not pdf.metadata.get("title")
        text = pdf[0].get_text()
        assert "Petra" not in text and "Max Mustermann wohnt" in text


def test_verify_pdf_reports_extras(tmp_path):
    src = tmp_path / "extras.pdf"
    _pdf_with_extras(src)
    findings = [Finding(0, 16, "PERSON", "Erika Musterfrau")]
    problems = verify_pdf(src.read_bytes(), findings)
    joined = " | ".join(problems)
    for what in ("Kommentare/Markierungen", "Formularfelder", "Lesezeichen", "Dateianhänge", "Metadaten"):
        assert what in joined
    assert "Erika Musterfrau" in problems  # steht im Kommentartext


def test_to_original_end_expands_partial_placeholder():
    text = "Hallo Max Mustermann, wie geht es?"
    fs = [Finding(6, 20, "PERSON", "Max Mustermann")]
    red = redact_text(text, fs, ReplaceMode.PLACEHOLDER)  # "Hallo [PERSON], wie geht es?"
    inside = red.text.index("[PERSON]") + 3
    assert red.to_original_end(inside) == 20
    assert red.to_original(inside) == 6
    assert red.to_original_end(red.text.index(",")) == text.index(",")


# ---------------------------------------------------------------- Silbentrennung am Zeilenende
def test_dehyphenate_joins_soft_breaks_only():
    from pii_redact.core.hyphenation import dehyphenate, join_hyphenated

    assert join_hyphenated("Herr Bergen-\nthaler aus") == "Herr Bergenthaler aus"
    assert join_hyphenated("Bergen­\nthaler") == "Bergenthaler"         # weiches Trennzeichen
    assert join_hyphenated("Bergen-\n\nthaler") == "Bergenthaler"            # Zeile in eigenem Textblock
    for keep in ("Schneider-\nKowalczyk", "E-\nMail", "Seite 12-\n13", "Absatz-\n\n\nneu"):
        assert join_hyphenated(keep) == keep                                 # echte Bindestriche, Zahlen
    text = "xx Bergen-\nthaler yy, Wester-\nmann zz"
    joined, hmap = dehyphenate(text)
    for word in ("Bergenthaler", "Westermann"):
        s = joined.index(word)
        a, b = hmap.span(s, s + len(word))
        assert join_hyphenated(text[a:b]) == word and text[a:b].endswith(word[-4:])
    assert hmap.span(joined.index("zz"), len(joined)) == (text.index("zz"), len(text))


def test_find_terms_and_lists_across_hyphenation():
    text = "Herr Bergen-\nthaler kommt; Bergenthaler bleibt."
    hits = find_terms(text, ["Bergenthaler"])
    assert [h.text for h in hits] == ["Bergen-\nthaler", "Bergenthaler"]
    assert [h.text for h in find_terms(text, ["Bergen-\nthaler"])] == ["Bergen-\nthaler", "Bergenthaler"]
    fs = [Finding(5, 19, "PERSON", "Bergen-\nthaler"), Finding(0, 4, "PERSON", "Herr")]
    assert [f.text for f in apply_allow_list(fs, ["Bergenthaler"])] == ["Herr"]


def test_numbered_same_number_across_hyphenation():
    text = "Bergen-\nthaler und Bergenthaler"
    fs = [Finding(0, 14, "PERSON", text[0:14]), Finding(19, 31, "PERSON", text[19:31])]
    red = redact_text(text, fs, ReplaceMode.NUMBERED)
    assert red.text == "[PERSON_1] und [PERSON_1]"


@needs_model
def test_hyphenated_names_fully_redacted_in_pdf(analyzer):
    """Meldung eines Anwenders: Bei Silbentrennung blieb der Wortteil in der zweiten Zeile stehen."""
    doc = load_document(SAMPLES / "trennung.pdf")
    findings = analyzer.analyze(doc.text, Settings())
    for name in ("Bergen-\nthaler", "Wester-\nmann", "Lichten-\nberger"):
        start = doc.text.index(name)
        assert any(f.start <= start and f.end >= start + len(name) for f in findings), name
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER, labels=True)
    assert verify_pdf(data, findings) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        words = {w[4].strip(".,;") for w in pdf[0].get_text("words")}
    for rest in ("thaler", "mann", "berger", "Bergen-", "Wester-", "Lichten-", "Kowalczyk", "Bergenthaler"):
        assert rest not in words, rest
    assert {"Kostenerstattung", "Fachbereich", "Servicestelle"} <= words   # Nicht-PII bleibt erhalten


# ---------------------------------------------------------------- Nummern über Zeilengrenzen
@needs_model
@pytest.mark.parametrize("text, typ", [
    ("Zahlung an IBAN DE89 3704 0044\n0532 0130 00 bitte bis Monatsende.", "IBAN_CODE"),
    ("Sie erreichen uns telefonisch unter 0171 23\n45678 bis 18 Uhr.", "PHONE_NUMBER"),
    ("Bezahlt mit Kreditkarte 4111 1111\n1111 1111 am Schalter.", "CREDIT_CARD"),
    ("Ihre Rentenversicherungsnummer 65 170839\nJ 00 3 liegt vor.", "DE_SOCIAL_SECURITY"),
])
def test_number_across_line_break_found(analyzer, text, typ):
    nl = text.index("\n")
    hits = [f for f in analyzer.analyze(text, Settings()) if f.start < nl < f.end]
    assert hits and hits[0].entity_type == typ, [(f.entity_type, f.text) for f in analyzer.analyze(text, Settings())]
    line1, line2 = text.split("\n")
    assert hits[0].start <= text.index(line1.split()[-1]) and hits[0].end >= nl + 1 + len(line2.split()[0])


@needs_model
@pytest.mark.parametrize("text", [
    "Bitte das Aktenzeichen 4711\n2026 angeben.",
    "Kapitel 3.2\n4 Ergebnisse der Umfrage",
    "Summe 1.240,50 EUR\n19 % Umsatzsteuer sind enthalten.",
    "Stand: 21.10.2026\n2 Anlagen",
    "Raum 204\nB 12 Flur links",
    "Sitzung am 12.03.\n2026 im Rathaus",
])
def test_numbers_across_line_break_no_false_alarm(analyzer, text):
    assert [f.text for f in analyzer.analyze(text, Settings()) if "\n" in f.text] == []


@needs_model
def test_address_block_stays_two_findings(analyzer):
    text = "Herrn\nDieter Wallmeier\nKastanienweg 12\n56410 Montabaur\n"
    found = analyzer.analyze(text, Settings())
    nl = text.index("12\n56410") + 2
    assert not [f for f in found if f.start < nl < f.end]                   # kein Fund über den Umbruch
    assert any("Kastanienweg 12" in f.text for f in found) and any("56410" in f.text for f in found)


@needs_model
def test_numbers_across_lines_redacted_in_pdf(analyzer):
    doc = load_document(SAMPLES / "trennung.pdf")
    findings = analyzer.analyze(doc.text, Settings())
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER, labels=True)
    assert verify_pdf(data, findings) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        words = {w[4].strip(".,;") for w in pdf[0].get_text("words")}
    for rest in ("DE89", "3704", "0044", "0532", "0130", "040", "55512345", "170839", "J"):
        assert rest not in words, rest
    assert {"4711", "2026", "Seite", "Aktenzeichen"} <= words   # keine personenbezogenen Daten


# ---------------------------------------------------------------- nicht angezeigter Inhalt: Zuschnitt, Ebenen
def _find(doc, needle: str, typ: str = "PERSON") -> Finding:
    s = doc.text.index(needle)
    return Finding(s, s + len(needle), typ, needle)


def _load_bytes(tmp_path, data: bytes, name: str = "test.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    return load_document(path, ocr=False)


@pytest.mark.parametrize("offset", [False, True], ids=["Nullpunkt", "versetzte Seite"])
@pytest.mark.parametrize("inherited", [False, True], ids=["an der Seite", "geerbt"])
def test_cropped_page_is_uncropped_checked_and_redacted(tmp_path, offset, inherited):
    """Der abgeschnittene Teil einer Seite steckt weiter in der Datei – er muss geprüft und geschwärzt werden."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), "Sichtbar: Frau Zofia Jaeger")
    page.insert_text((72, 700), "Abgeschnitten: Herr Karl Bergenthaler")
    if offset:
        pdf.xref_set_key(page.xref, "MediaBox", "[10 20 605 862]")
    holder = int(pdf.xref_get_key(page.xref, "Parent")[1].split()[0]) if inherited else page.xref
    pdf.xref_set_key(holder, "CropBox", "[10 462 605 862]" if offset else "[0 442 595 842]")   # nur obere Hälfte
    original = pdf.tobytes()
    assert "Bergenthaler" not in pymupdf.open(stream=original)[0].get_text()   # Ausgangslage: nicht angezeigt

    doc = _load_bytes(tmp_path, original)
    assert "Karl Bergenthaler" in doc.text
    notice = {n.kind: n for n in doc.notices}["cropped"]
    assert notice.critical and notice.pages == [0]

    findings = [_find(doc, "Zofia Jaeger"), _find(doc, "Karl Bergenthaler")]
    out = redact_pdf(doc, findings, ReplaceMode.BLACKOUT)
    assert verify_pdf(out, findings) == []
    result = pymupdf.open(stream=out)
    assert result[0].cropbox.height == pytest.approx(result[0].mediabox.height)
    text = result[0].get_text()
    assert "Abgeschnitten: Herr" in text and "Bergenthaler" not in text and "Jaeger" not in text
    # die Kontrolle erkennt ein zugeschnittenes Ergebnis
    assert any("Zugeschnittene Seiten" in p for p in verify_pdf(original, []))


def test_hidden_layer_is_shown_checked_and_redacted(tmp_path):
    """Text auf einer ausgeblendeten Ebene lässt sich im PDF-Programm wieder einblenden."""
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Sichtbar: Frau Zofia Jaeger")
    layer = pdf.add_ocg("Intern", on=False)
    page.insert_text((72, 200), "Intern: Herr Karl Bergenthaler", oc=layer)
    original = pdf.tobytes()
    assert "Bergenthaler" not in pymupdf.open(stream=original)[0].get_text()

    doc = _load_bytes(tmp_path, original)
    assert "Karl Bergenthaler" in doc.text
    notice = {n.kind: n for n in doc.notices}["layers"]
    assert notice.critical and "1 ausgeblendete Ebene" in notice.short

    findings = [_find(doc, "Karl Bergenthaler")]
    out = redact_pdf(doc, findings, ReplaceMode.BLACKOUT)
    assert verify_pdf(out, findings) == []
    result = pymupdf.open(stream=out)
    assert result.get_ocgs() == {}
    assert b"Bergenthaler" not in b"".join(result.xref_stream(x) or b"" for x in range(1, result.xref_length()))
    assert "Intern: Herr" in result[0].get_text()
    assert "Ebenen noch vorhanden" in verify_pdf(original, [])


def test_visible_layers_give_no_notice(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Text auf einer Ebene", oc=pdf.add_ocg("Entwurf", on=True))
    doc = _load_bytes(tmp_path, pdf.tobytes())
    assert "Text auf einer Ebene" in doc.text and not doc.notices
    assert pymupdf.open(stream=doc.pdf_bytes).get_ocgs() == {}


# ---------------------------------------------------------------- Schwärzung trifft nur die eigene Zeile
_NEIGHBOURS = ("Die Bearbeitung liegt bei der Abteilung Finanzen im Haus (Äpfel, Übung, gyp).",
               "Zuständig ist dort Frau Zofia Jaeger aus dem zweiten Stock, Telefon 030 1234567.",
               "Bitte beachten Sie die neuen Zeiten und die Regeln der Übergabe im Haus (ÄÖÜ).")


def _squash(text: str) -> str:
    return "".join(text.split())


@pytest.mark.parametrize("font", ["helv", "tiro", "cour"])
@pytest.mark.parametrize("leading", [0.85, 1.0, 1.15, 1.4])
@pytest.mark.parametrize("mode", [ReplaceMode.BLACKOUT, ReplaceMode.NUMBERED])
def test_tight_line_spacing_keeps_neighbour_lines(tmp_path, font, leading, mode):
    """Bei engem Zeilenabstand ragen die Zeichenrahmen in die Nachbarzeilen – dort darf nichts verschwinden."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=700, height=300)
    for i, line in enumerate(_NEIGHBOURS):
        page.insert_text((40, 80 + i * 11 * leading), line, fontsize=11, fontname=font)
    doc = _load_bytes(tmp_path, pdf.tobytes())
    findings = [_find(doc, "Zofia Jaeger"), _find(doc, "030 1234567", "PHONE_NUMBER")]
    out = redact_pdf(doc, findings, mode)
    assert verify_pdf(out, findings) == []
    got = pymupdf.open(stream=out)[0].get_text()
    for token in ("[PERSON_1]", "[TELEFON_1]"):
        got = got.replace(token, "")
    expected = doc.text.replace("Zofia Jaeger", "").replace("030 1234567", "")
    assert _squash(got) == _squash(expected)


def test_mixed_sizes_and_vertical_text_are_removed(tmp_path):
    """Kein gemeinsames Band (Schriftgrößen gemischt, hochgestellt, senkrecht): jedes Zeichen einzeln."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=400)
    page.insert_text((40, 69), "Zeile darüber mit Text über die ganze Breite der Seite", fontsize=11)
    x = 40.0
    for part, size, dy in (("Vermerk ", 11, 0), ("Zofia", 11, 0), ("Jaeger", 7, 0), ("2", 6, -5), (" Rest der Zeile", 11, 0)):
        page.insert_text((x, 80 + dy), part, fontsize=size)
        x += pymupdf.get_text_length(part, fontsize=size)
    page.insert_text((40, 91), "Zeile darunter mit Text über die ganze Breite der Seite", fontsize=11)
    page.insert_text((300, 380), "Senkrecht Zofia Jaeger hier", fontsize=11, rotate=90)
    doc = _load_bytes(tmp_path, pdf.tobytes())
    import re

    findings = [Finding(m.start(), m.end(), "PERSON", m.group()) for m in re.finditer(r"Zofia\s*Jaeger\s*2?", doc.text)]
    assert len(findings) == 2
    out = redact_pdf(doc, findings, ReplaceMode.BLACKOUT)
    assert verify_pdf(out, findings) == []
    expected = doc.text
    for f in findings:
        expected = expected.replace(f.text, "")
    assert _squash(pymupdf.open(stream=out)[0].get_text()) == _squash(expected)


def test_scan_with_text_layer_loses_pixels_in_full_height(tmp_path):
    """Gescannte Seite mit unsichtbarer Textebene: Der Text wird mit dem schmalen Band entfernt, die
    Bildpunkte darunter aber im ganzen Balken."""
    src = pymupdf.open()
    page = src.new_page(width=400, height=120)
    page.insert_text((20, 60), "Frau Zofia Jaeger", fontsize=22)
    pix = page.get_pixmap(dpi=150)
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=120)
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text((20, 60), "Frau Zofia Jaeger", fontsize=22, render_mode=3)
    doc = _load_bytes(tmp_path, pdf.tobytes())
    finding = _find(doc, "Zofia Jaeger")
    from pii_redact.core.redactor import finding_rects

    ((_, rect),) = finding_rects(doc, finding.start, finding.end)
    result = pymupdf.open(stream=redact_pdf(doc, [finding], ReplaceMode.BLACKOUT))
    assert "Zofia" not in result[0].get_text(flags=0)
    image = pymupdf.Pixmap(result, result[0].get_images()[0][0])
    sx, sy = image.width / 400, image.height / 120
    inside = {image.pixel(x, y)
              for x in range(int(rect.x0 * sx) + 2, int(rect.x1 * sx) - 2, 3)
              for y in range(int(rect.y0 * sy) + 2, int(rect.y1 * sy) - 2)}
    assert len(inside) == 1   # einheitlich gefüllt – keine Reste der Buchstaben am oberen/unteren Rand
