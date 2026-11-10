"""Tests der Kernlogik (ohne Oberfläche)."""

from pathlib import Path

import pymupdf
import pytest
import spacy

from pii_redact.core import (
    Finding, PiiAnalyzer, ReplaceMode, Settings, UnsupportedFileError, load_document, redact_pdf, redact_text, verify_pdf,
)
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
    assert any(p.startswith("Erika Musterfrau – an anderer Stelle") for p in problems)  # steht im Kommentartext


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


# ---------------------------------------------------------------- Sonder-Leerzeichen, Stern-Datum, Postfach
def test_plain_spaces_keeps_positions():
    from pii_redact.core.analyzer import plain_spaces

    text = "DE89\u00a03704\u202f0044\u20070532\u2009x"
    assert plain_spaces(text) == "DE89 3704 0044 0532 x" and len(plain_spaces(text)) == len(text)


@needs_model
def test_special_spaces_do_not_hide_numbers(analyzer):
    """Geschützte und schmale Leerzeichen (Satzprogramme, Textverarbeitung) in IBAN und Telefonnummer."""
    text = "Frau Zofia\u00a0Jaeger, IBAN DE89\u00a03704\u00a00044\u00a00532\u00a00130\u00a000, Tel.\u202f030\u202f1234567"
    found = {f.entity_type: f for f in analyzer.analyze(text, Settings())}
    assert found["IBAN_CODE"].text == "DE89\u00a03704\u00a00044\u00a00532\u00a00130\u00a000"     # Wortlaut des Dokuments
    assert found["PHONE_NUMBER"].text == "030\u202f1234567"
    assert all(text[f.start:f.end] == f.text for f in found.values())
    lists = Settings(deny_list=["Projekt Adler"], allow_list=["Zofia Jaeger"])
    hits = analyzer.analyze("Frau Zofia\u00a0Jaeger leitet Projekt\u00a0Adler.", lists)
    assert [(f.text, f.entity_type) for f in hits] == [("Projekt\u00a0Adler", "CUSTOM")]


@needs_model
@pytest.mark.parametrize("text, dates", [
    ("Yılmaz, Ayşe, *17.11.1984, Köln", ["17.11.1984"]),
    ("Jaeger, Zofia (* 3.5.62, † 12.01.2020)", ["3.5.62", "12.01.2020"]),
    ("Termin am *12.03.2024* und **13.03.2024**, Fußnote* 14.03.2024, Faktor 5*15.03.2024", []),
    ("Der Unfall ereignete sich am 03.05.2024 gegen 14 Uhr.", []),
])
def test_birth_date_with_star(analyzer, text, dates):
    assert [f.text for f in analyzer.analyze(text, Settings()) if f.entity_type == "DATE_TIME"] == dates


@needs_model
def test_post_office_box_and_field_labels(analyzer):
    text = "Postfach 10 20 30, 10117 Berlin. Bitte ans Postfach senden. Personalnr. und Kundennr. angeben."
    hits = [(f.text, f.entity_type) for f in analyzer.analyze(text, Settings())]
    assert ("Postfach 10 20 30", "DE_ADDRESS") in hits
    assert not any(t in ("Postfach", "Personalnr", "Kundennr") for t, _ in hits), hits


@pytest.mark.parametrize("data", [b"%PDF-1.4\nkaputt", b""], ids=["defekt", "leer"])
def test_broken_pdf_gives_clear_message(tmp_path, data):
    path = tmp_path / "defekt.pdf"
    path.write_bytes(data)
    with pytest.raises(UnsupportedFileError, match="beschädigt oder kein gültiges PDF"):
        load_document(path)


# ---------------------------------------------------------------- Namen und Orte in Großbuchstaben
def test_normal_case_keeps_positions():
    from pii_redact.core.analyzer import normal_case

    text = "Herr MÜLLER-LÜDENSCHEID aus GROSSSTADT, IBAN DE89, EU, Maßstab STRAßE"
    out, spans = normal_case(text)
    assert out == "Herr Müller-Lüdenscheid aus Grossstadt, Iban DE89, EU, Maßstab Straße" and len(out) == len(text)
    assert [text[s:e] for s, e in spans] == ["MÜLLER-LÜDENSCHEID", "GROSSSTADT", "IBAN", "STRAßE"]
    assert normal_case("Kein Wort in Versalien.") == ("Kein Wort in Versalien.", [])


@needs_model
def test_upper_case_names_and_places_found(analyzer):
    """Formulare und Adressfelder schreiben Namen und Orte oft in Großbuchstaben."""
    text = "ANTRAGSTELLER: SCHNEIDER, ANNA, MUSTERWEG 7, 80331 MÜNCHEN\nTelefon 030 1234567"
    hits = {f.text: f.entity_type for f in analyzer.analyze(text, Settings())}
    assert {"SCHNEIDER", "ANNA", "80331 MÜNCHEN", "030 1234567"} <= set(hits), hits
    assert hits["80331 MÜNCHEN"] == "DE_ADDRESS" and "ANTRAGSTELLER" not in hits
    assert all(text[f.start:f.end] == f.text for f in analyzer.analyze(text, Settings()))


@needs_model
def test_upper_case_headings_and_abbreviations_stay(analyzer):
    text = ("BETREFF: ANTRAG AUF KOSTENÜBERNAHME\nWICHTIG: Hinweise zur DSGVO und zum BGB beachten.\n"
            "Die AOK, der TÜV und die IHK haben zugestimmt. Zahlung per SEPA, IBAN und BIC siehe unten.\n"
            "ANLAGE 1: KOPIE DES BESCHEIDS\nMIT FREUNDLICHEN GRÜSSEN\nSEITE 1 VON 3 – VERTRAULICH")
    assert [(f.text, f.entity_type) for f in analyzer.analyze(text, Settings())] == []


# ---------------------------------------------------------------- Helfer für erzeugte PDFs
def _find(doc, needle: str, typ: str = "PERSON") -> Finding:
    s = doc.text.index(needle)
    return Finding(s, s + len(needle), typ, needle)


def _load_bytes(tmp_path, data: bytes, name: str = "test.pdf"):
    path = tmp_path / name
    path.write_bytes(data)
    return load_document(path, ocr=False)


# ---------------------------------------------------------------- Kontrolle nach dem Export
def _two_lines(tmp_path, first: str, second: str):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), first)
    page.insert_text((72, 200), second)
    pdf.new_page().insert_text((72, 100), "Seite zwei ohne Besonderheiten")
    return _load_bytes(tmp_path, pdf.tobytes())


@pytest.mark.parametrize("mode", [ReplaceMode.PLACEHOLDER, ReplaceMode.NUMBERED, ReplaceMode.BLACKOUT])
def test_verify_checks_each_redacted_spot(tmp_path, mode):
    """Geprüft wird die geschwärzte Stelle selbst – auch wenn derselbe Text anderswo bewusst stehen bleibt."""
    doc = _two_lines(tmp_path, "Zofia Jaeger schreibt.", "Briefkopf: Zofia Jaeger")
    first = _find(doc, "Zofia Jaeger")
    second = Finding(doc.text.rindex("Zofia Jaeger"), doc.text.rindex("Zofia Jaeger") + 12, "PERSON", "Zofia Jaeger",
                     active=False)
    findings = [first, second]
    assert verify_pdf(redact_pdf(doc, findings, mode), findings, None, doc, mode) == []
    # Schwärzung ohne Wirkung (unverändertes PDF): früher unbemerkt, weil der Text abgewählt noch einmal vorkommt
    assert verify_pdf(doc.pdf_bytes, findings, None, doc, mode) == [
        "Zofia Jaeger – an der geschwärzten Stelle noch vorhanden (Seite 1)"]


def test_verify_reports_other_occurrences_but_not_parts_of_words(tmp_path):
    doc = _two_lines(tmp_path, "Herr Ende hat den Antrag am Wochenende eingereicht.", "Anhang: akte_ende.pdf, Tel. 4711, Nr. 14711")
    findings = [_find(doc, "Ende"), _find(doc, "4711", "PHONE_NUMBER")]
    out = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER)
    # „Wochenende“ und „14711“ zählen nicht, der Dateiname schon
    assert verify_pdf(out, findings, None, doc) == ["Ende – an anderer Stelle noch lesbar (Seite 1)"]
    doc = _two_lines(tmp_path, "Herr Ende hat den Antrag am Wochenende eingereicht.", "Ohne weiteren Treffer, Nr. 14711")
    findings = [_find(doc, "Ende")]
    assert verify_pdf(redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER), findings, None, doc) == []


def test_verify_counts_deselected_occurrences(tmp_path):
    """Drei Vorkommen, eines geschwärzt, eines abgewählt, eines nie markiert → das dritte wird gemeldet."""
    doc = _two_lines(tmp_path, "Zofia Jaeger schreibt an Zofia Jaeger.", "Gruß, Zofia Jaeger")
    spots = [m.start() for m in __import__("re").finditer("Zofia Jaeger", doc.text)]
    findings = [Finding(spots[0], spots[0] + 12, "PERSON", "Zofia Jaeger"),
                Finding(spots[1], spots[1] + 12, "PERSON", "Zofia Jaeger", active=False)]
    out = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER)
    assert verify_pdf(out, findings, None, doc) == ["Zofia Jaeger – an anderer Stelle noch lesbar (Seite 1)"]
    findings.append(Finding(spots[2], spots[2] + 12, "PERSON", "Zofia Jaeger", active=False))
    assert verify_pdf(out, findings, None, doc) == []


def test_verify_area_and_tight_lines(tmp_path):
    """Frei gezogener Bereich: aller Text darin muss weg sein. Enge Zeilen: Nachbarzeilen zählen nicht."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=700, height=300)
    for i, line in enumerate(_NEIGHBOURS):
        page.insert_text((40, 80 + i * 11 * 0.85), line, fontsize=11)
    doc = _load_bytes(tmp_path, pdf.tobytes())
    findings = [_find(doc, "Zofia Jaeger"),
                Finding(0, 0, "AREA", "", source="manuell", area=(0, 30, 60, 300, 100))]
    out = redact_pdf(doc, findings, ReplaceMode.NUMBERED)
    assert verify_pdf(out, findings, None, doc, ReplaceMode.NUMBERED) == []
    problems = verify_pdf(doc.pdf_bytes, findings, None, doc, ReplaceMode.NUMBERED)
    assert problems == ["Zofia Jaeger – an der geschwärzten Stelle noch vorhanden (Seite 1)",
                        "Bereich – an der geschwärzten Stelle noch vorhanden (Seite 1)"]


# ---------------------------------------------------------------- Kontrolle vor dem Speichern: weitere Vorkommen
def test_further_occurrences_become_inactive_suggestions():
    from pii_redact.core.analyzer import further_occurrences

    text = ("Herr Deniz Kaya stellt den Antrag.\nAnhang: antrag_kaya.pdf, Rückfragen an KAYA oder Frau Kayala.\n"
            "Am Wochenende war Herr Ende nicht erreichbar, Tel. 4711 (nicht 14711).\nGruß, Deniz Kaya")
    def at(needle, typ, nth=0, active=True):
        pos = [m.start() for m in __import__("re").finditer(needle, text)][nth]
        return Finding(pos, pos + len(needle), typ, needle, active=active)

    findings = [at("Kaya", "PERSON"), at("Ende", "PERSON"), at("4711", "PHONE_NUMBER")]
    # Satzzeichen am Rand eines Fundes gehören nicht zum gesuchten Text
    assert [f.text for f in further_occurrences("Fragen an Herrn KAYA. Anhang: antrag_kaya.pdf",
                                                [Finding(16, 21, "PERSON", "KAYA.")])] == ["kaya"]
    # Anrede und Titel ebenso wenig („Herrn Dr. Kaya“ → „kaya“)
    assert [f.text for f in further_occurrences("Antrag von Herrn Dr. Kaya. Anhang: antrag_kaya.pdf",
                                                [Finding(11, 25, "PERSON", "Herrn Dr. Kaya")])] == ["kaya"]
    new = further_occurrences(text, findings)
    # im Dateinamen, in Großbuchstaben und im Gruß – nicht „Kayala“, „Wochenende“, „14711“
    assert [(f.text, text[max(f.start - 7, 0):f.start]) for f in new] == [("kaya", "antrag_"), ("KAYA", "gen an "), ("Kaya", " Deniz ")]
    assert all(f.source == "kontrolle" and not f.active and f.entity_type == "PERSON" for f in new)
    assert all(text[f.start:f.end] == f.text for f in new)
    # mit den Vorschlägen in der Liste (auch unbestätigt) wird nichts erneut vorgeschlagen
    assert further_occurrences(text, findings + new) == []
    # eine abgewählte Stelle ist eine Entscheidung; ein abgewählter Fund löst selbst keine Suche aus
    kept = at("Kaya", "PERSON", nth=2, active=False)   # das „Kaya“ im Gruß
    assert [f.text for f in further_occurrences(text, findings + [kept])] == ["kaya", "KAYA"]
    assert further_occurrences(text, [at("Kaya", "PERSON", active=False)]) == []


def test_further_occurrences_surname_alone():
    """Mehrteiliger Personenname geschwärzt: der Nachname allein wird an anderer Stelle vorgeschlagen."""
    from pii_redact.core.analyzer import further_occurrences, surname

    assert surname("Herrn Dr. Max Mustermann") == "Mustermann" and surname("Mustermann, Max") == "Mustermann"
    assert surname("Erika Müller-Lüdenscheid.") == "Müller-Lüdenscheid"
    assert surname("Mustermann") is None and surname("Herr Mustermann") is None      # kein mehrteiliger Name
    assert surname("Max Li") is None and surname("Zimmer 12") is None

    def found(text, name, typ="PERSON"):
        pos = text.index(name)
        return [(f.text, f.recognizer.split(":")[1].strip()[:8])
                for f in further_occurrences(text, [Finding(pos, pos + len(name), typ, name)])]

    text = ("Frau Erika Winter stellt den Antrag. Im Winter ist das Büro geschlossen, der Winter war lang.\n"
            "Rückfragen an Winter oder WINTER, Anhang: antrag_winter.pdf, Akte winter2 – winterfest, im winter.\n"
            "Gruß, Erika Winter")
    # ganzer Name hat Vorrang; allein: groß geschrieben, nicht nach „im“/„der“; klein nur in Dateiname/Kennung
    assert found(text, "Erika Winter") == [("Winter", "Nachname"), ("WINTER", "Nachname"), ("winter", "Nachname"),
                                           ("winter", "Nachname"), ("Erika Winter", "weiteres")]
    assert found("Max Mustermann wohnt in Musterstadt. Mustermann zahlt.", "Max Mustermann") == [("Mustermann", "Nachname")]
    # nur bei Personen – ein Ort oder eine Firma aus zwei Wörtern löst keine Suche nach dem letzten Wort aus
    assert found("Sitz in Bad Homburg. Die Homburg GmbH liefert.", "Bad Homburg", "LOCATION") == []
    # steht der Nachname schon als Fund in der Liste (auch abgewählt), wird er nicht vorgeschlagen
    t2 = "Max Mustermann und Mustermann."
    fs = [Finding(0, 14, "PERSON", "Max Mustermann"), Finding(19, 29, "PERSON", "Mustermann", active=False)]
    assert further_occurrences(t2, fs) == []


def test_further_occurrences_across_hyphenation_and_special_spaces():
    from pii_redact.core.analyzer import further_occurrences

    text = "Frau Zofia Jaeger wohnt in Bergenthal.\nUnterlagen von Zofia\u00a0Jaeger aus Bergen-\nthal liegen vor."
    findings = [_find_text(text, "Zofia Jaeger"), _find_text(text, "Bergenthal", "LOCATION")]
    assert [f.text for f in further_occurrences(text, findings)] == ["Zofia\u00a0Jaeger", "Bergen-\nthal"]


def _find_text(text: str, needle: str, typ: str = "PERSON") -> Finding:
    s = text.index(needle)
    return Finding(s, s + len(needle), typ, needle)


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
