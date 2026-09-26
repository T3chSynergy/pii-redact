"""Texterkennung (OCR) als Rückfallebene für gescannte PDF-Seiten."""

import os
import time
from pathlib import Path

import pymupdf
import pytest

from pii_redact.core import Finding, ReplaceMode, Settings, load_document, redact_pdf, verify_pdf
from pii_redact.core import ocr
from pii_redact.core.loaders import OCR_WARNING_PREFIX

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
pytestmark = pytest.mark.skipif(not ocr.is_available(), reason="RapidOCR nicht installiert")


def _finding(doc, text: str, etype: str = "PERSON") -> Finding:
    start = doc.text.index(text)
    return Finding(start, start + len(text), etype, text)


def test_scan_is_read_and_marked():
    doc = load_document(SAMPLES / "scan.pdf")
    assert doc.ocr_pages == [0] and doc.has_ocr
    assert doc.warnings[0].startswith(OCR_WARNING_PREFIX)
    for text in ("Lena Fischer", "Gartenstraße 17", "lena.fischer@example.de", "0151 23456789"):
        assert text in doc.text
    f = _finding(doc, "Lena Fischer")
    assert doc.is_ocr(f.start, f.end)


def test_scan_without_ocr_warns():
    doc = load_document(SAMPLES / "scan.pdf", ocr=False)
    assert not doc.has_ocr and doc.text.strip() == ""
    assert any("keine lesbare Textebene" in w for w in doc.warnings)


def test_mail_with_scanned_attachment():
    doc = load_document(SAMPLES / "mail_mit_scan.pdf")
    assert doc.ocr_pages == [1]                      # nur die Anhang-Seite
    assert doc.text.count("antrag_kaya.pdf") == 2    # Kopfzeile nicht doppelt (echter Text + OCR)
    assert "Geburtsdatum: 17.11.1984" in doc.text    # verschluckte Leerzeichen nach „:“ ergänzt
    f = _finding(doc, "Deniz Kaya")                  # auf Seite 1 echter Text
    assert not doc.is_ocr(f.start, f.end)


def test_scan_redaction_removes_pixels_and_verifies():
    doc = load_document(SAMPLES / "scan.pdf")
    findings = [_finding(doc, "Lena Fischer"), _finding(doc, "0151 23456789", "PHONE_NUMBER")]
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER)
    assert verify_pdf(data, findings, doc.ocr_pages) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        text = ocr.ocr_page_text(pdf[0])
    assert "Fischer" not in text and "23456789" not in text
    assert "Mietvertrag" in text                      # Rest bleibt lesbar


def test_verify_rereads_ocr_pages():
    doc = load_document(SAMPLES / "scan.pdf")
    missed = _finding(doc, "Lena Fischer")
    data = redact_pdf(doc, [], ReplaceMode.PLACEHOLDER)   # nichts geschwärzt
    assert verify_pdf(data, [missed]) == []                # ohne OCR-Seiten: keine Textebene, nichts zu sehen
    assert verify_pdf(data, [missed], doc.ocr_pages) == ["Lena Fischer"]


def test_needs_ocr_heuristic():
    pdf = pymupdf.open()
    text_page = pdf.new_page()
    text_page.insert_text((72, 72), "Nur Text")
    assert not ocr.needs_ocr(text_page, 8)
    img_page = pdf.new_page()
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    img_page.insert_image(pymupdf.Rect(0, 0, 595, 700), pixmap=pix)
    assert ocr.needs_ocr(img_page, 0)          # Scan ohne Text
    assert ocr.needs_ocr(img_page, 30)         # Scan mit Kopfzeile
    assert not ocr.needs_ocr(img_page, 2000)   # Scan mit OCR-Textebene der zentralen Texterkennung
    small = pdf.new_page()
    small.insert_image(pymupdf.Rect(0, 0, 100, 50), pixmap=pix)
    assert not ocr.needs_ocr(small, 30)        # Logo auf Textseite
    assert ocr.needs_ocr(small, 0)             # einziges Bild auf leerer Seite


def test_batch_skips_ocr_in_auto_export_and_checks_text(tmp_path):
    import shutil

    from pii_redact.cli import main
    from pii_redact.core.batch import Workspace

    src = tmp_path / "quelle"
    src.mkdir()
    shutil.copy(SAMPLES / "scan.pdf", src / "scan.pdf")
    shutil.copy(SAMPLES / "beispiel.txt", src / "brief.txt")
    out = tmp_path / "ziel"
    rc = main([str(src), "-o", str(out)])
    assert rc == 3
    ws = Workspace.load(out)
    e = ws.entries["scan.pdf"]
    assert e.ocr_pages == [1] and not e.done and "Texterkennung" in e.note
    assert ws.entries["brief.txt"].done
    protocol = (out / "pii-redact-protokoll.csv").read_text(encoding="utf-8-sig")
    assert "OCR-Seiten" in protocol and "erhöhte Fehlerquote" in protocol

    # Gespeicherte Funde passen nur zum selben Text
    doc = load_document(src / "scan.pdf")
    assert ws.restore_findings("scan.pdf", doc) is not None
    doc_no_ocr = load_document(src / "scan.pdf", ocr=False)
    assert ws.restore_findings("scan.pdf", doc_no_ocr) is None
    assert ws.entries["scan.pdf"].findings is None     # zur Neu-Analyse zurückgesetzt


# ---------------------------------------------------------------- Oberfläche
@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM", "offscreen") != "offscreen", reason="nur offscreen")
def test_ui_marks_ocr_and_asks_before_export(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from pii_redact.ui.main_window import MainWindow

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    w = MainWindow()
    w.show()
    try:
        w.open_file(SAMPLES / "scan.pdf")
        t = time.time()
        while w.runner.busy and time.time() - t < 120:
            wait(100)
        wait(100)
        assert w.ocr_banner.isVisible() and "Texterkennung" in w.ocr_banner.text()
        assert OCR_WARNING_PREFIX not in w.banner.text()
        assert w.right.pages.ocr_pages == {0}
        assert "aus Texterkennung" in w.findings.summary.text()

        target = tmp_path / "scan_geschwaerzt.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
        # Nutzer verneint die gründliche Prüfung → kein Export
        monkeypatch.setattr(MainWindow, "_confirm_ocr", lambda self, doc: False)
        w.export_pdf()
        assert not target.exists()
        monkeypatch.setattr(MainWindow, "_confirm_ocr", lambda self, doc: True)
        w.export_pdf()
        assert target.exists()
    finally:
        if w.session:
            w.session.dirty = False
        w.close()
