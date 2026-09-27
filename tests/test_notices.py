"""Hinweise: Stufen, Seitensymbole, Bildfilter, gesperrte Einstellungen und Anzeige in der Oberfläche."""

import json
import os
import time
from pathlib import Path

import pymupdf as fitz
import pytest

from pii_redact.core import Level, Settings, load_document
from pii_redact.core import ocr as ocr_mod

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _kinds(doc):
    return {n.kind: n for n in doc.notices}


def test_levels_and_pages():
    form = _kinds(load_document(SAMPLES / "formular.pdf"))
    assert form["widgets"].level == Level.INFO and form["widgets"].pages == [0] and form["widgets"].badge == "Formular"
    extras = _kinds(load_document(SAMPLES / "kommentare.pdf"))
    assert extras["removed"].level == Level.INFO and "Lesezeichen" in extras["removed"].short
    assert not load_document(SAMPLES / "beispiel.pdf").notices
    doc = load_document(SAMPLES / "beispiel.pdf")
    assert doc.warnings == [n.detail for n in doc.notices]


@pytest.mark.skipif(not ocr_mod.is_available(), reason="RapidOCR nicht installiert")
def test_ocr_notice_is_critical_and_short():
    doc = load_document(SAMPLES / "scan.pdf")
    n = _kinds(doc)["ocr"]
    assert n.critical and n.pages == [0] and len(n.short) < 80
    assert doc.critical_notices == [n]


def _image(w, h, value):
    return fitz.Pixmap(fitz.csGRAY, w, h, bytes([value]) * (w * h), False)


def test_image_notice_skips_logos_and_icons(tmp_path):
    pdf = fitz.open()
    logo = _image(60, 20, 90)
    for i in range(3):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((60, 120), f"Seite {i + 1} mit etwas Text", fontsize=11)
        page.insert_image(fitz.Rect(440, 30, 560, 70), pixmap=logo)   # Briefkopf-Logo auf jeder Seite
        page.insert_image(fitz.Rect(60, 200, 66, 206), pixmap=_image(4, 4, 30 + i))  # Aufzählungspunkt
    pdf[1].insert_image(fitz.Rect(60, 400, 210, 450), pixmap=_image(120, 40, 50))  # Unterschrift nur auf Seite 2
    out = tmp_path / "logo.pdf"
    pdf.save(out)
    n = _kinds(load_document(out))["images"]
    assert n.pages == [1] and n.short == "Bilder auf Seite 2 – Inhalt wird nicht erkannt"


def test_signature_sample_is_flagged():
    n = _kinds(load_document(SAMPLES / "unterschrift.pdf"))["images"]
    assert n.pages == [0] and n.badge == "Bild"


def test_locked_settings(tmp_path):
    defaults = tmp_path / "defaults.json"
    user = tmp_path / "settings.json"
    defaults.write_text(json.dumps({"compact_notices": True, "ocr": True, "locked": ["compact_notices", "ocr", "fehlt"]}))
    user.write_text(json.dumps({"compact_notices": False, "ocr": False, "pdf_labels": False, "locked": []}))
    s = Settings.load(user, defaults)
    assert s.compact_notices is True and s.ocr is True and s.pdf_labels is False
    assert s.locked == ["compact_notices", "ocr"]
    s.save(user)
    assert "locked" not in json.loads(user.read_text())


# ---------------------------------------------------------------- Oberfläche
@pytest.fixture()
def window(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from pii_redact.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    yield w
    if w.session:
        w.session.dirty = False
    w.close()


def _wait_idle(w, timeout=120):
    from PySide6.QtCore import QEventLoop, QTimer

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    t = time.time()
    while w.runner.busy and time.time() - t < timeout:
        wait(100)
    wait(50)


def test_info_notices_only_in_status_bar(window):
    window.open_file(SAMPLES / "formular.pdf")
    _wait_idle(window)
    assert not window.notice_bar.isVisible()
    assert window.notice_btn.isVisible() and window.notice_btn.text() == "ⓘ 1 Hinweis"
    assert [m.label for m in window.left.pages.page_marks[0]] == ["Formular"]
    window.open_file(SAMPLES / "beispiel.pdf")
    _wait_idle(window)
    assert not window.notice_btn.isVisible() and not window.right.pages.page_marks


@pytest.mark.skipif(not ocr_mod.is_available(), reason="RapidOCR nicht installiert")
def test_critical_bar_one_line_closable_and_compact(window):
    window.open_file(SAMPLES / "mail_mit_scan.pdf")
    _wait_idle(window)
    bar = window.notice_bar
    assert bar.isVisible() and not bar.label.wordWrap()
    assert bar.height() < 40  # genau eine Zeile
    assert "Seite 2" in bar.label.text() and "Texterkennung" in bar.toolTip()
    assert window.notice_btn.text().startswith("⚠")
    bar.close_btn.click()
    assert not bar.isVisible() and window.notice_btn.isVisible()
    # nächstes Öffnen zeigt den Hinweis wieder
    window.open_file(SAMPLES / "mail_mit_scan.pdf")
    _wait_idle(window)
    assert bar.isVisible()
    # Kompakte Hinweise: keine Leiste, Zähler und Seitensymbol bleiben
    window.settings.compact_notices = True
    window._show_notices(window.session.doc)
    assert not bar.isVisible() and window.notice_btn.isVisible()
    assert window.right.pages.page_marks[1][0].critical


def test_notice_popup_links(window):
    window.open_file(SAMPLES / "unterschrift.pdf")
    _wait_idle(window)
    pages, helps = [], []
    window.notice_btn.pageRequested.connect(pages.append)
    window.notice_btn.helpRequested.connect(helps.append)
    window.notice_btn.show_popup()
    popup = window.notice_btn.popup
    assert popup is not None and popup.isVisible()
    assert "Seite 1 zeigen" in popup.rows[0].text()
    popup.rows[0].linkActivated.emit("page:0")
    window.notice_btn._link("help:hinweise")
    assert pages == [0] and helps == ["hinweise"]
