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
