"""Rauchtest der Oberfläche (läuft ohne Bildschirm mit QT_QPA_PLATFORM=offscreen)."""

import os
import time
from pathlib import Path

import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
pytestmark = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")


def _wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    from pii_redact.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    yield w
    if w.session:
        w.session.dirty = False
    w.close()


def _open(window, path: Path) -> None:
    window.open_file(path)
    t = time.time()
    while window.runner.busy and time.time() - t < 120:
        _wait(100)
    _wait(100)


def test_markdown_edit_and_export(window, tmp_path, monkeypatch):
    _open(window, SAMPLES / "beispiel.md")
    s = window.session
    total, active = s.counts()
    assert total > 5 and total == active
    assert "Petra Hoffmann" not in window.right.text.toPlainText()

    # Fund deaktivieren → erscheint wieder im Ergebnis
    f = next(f for f in s.findings if f.text == "Lena Krüger")
    s.set_active([f.id], False)
    _wait(50)
    assert "Lena Krüger" in window.right.text.toPlainText()

    # Manuell ergänzen + Rückgängig/Wiederholen
    start = s.doc.text.index("Sommerfests")
    window._add_ranges([(start, start + len("Sommerfests"))], "CUSTOM", False)
    _wait(50)
    assert "Sommerfests" not in s.output_text()
    s.undo()
    assert "Sommerfests" in s.output_text()
    s.redo()
    assert "Sommerfests" not in s.output_text()

    # Alle Vorkommen
    window._add_ranges([(s.doc.text.index("Yilmaz"), s.doc.text.index("Yilmaz") + 6)], "PERSON", True)
    assert "Yilmaz" not in s.output_text()

    # Export
    target = tmp_path / "out.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    window.export_text()
    out = target.read_text(encoding="utf-8")
    assert "Lena Krüger" in out and "petra.hoffmann@" not in out
    assert not s.dirty


def test_pdf_export(window, tmp_path, monkeypatch):
    _open(window, SAMPLES / "beispiel.pdf")
    assert window.left.tabs.count() == 2
    target = tmp_path / "out.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    shown = {}
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.setdefault("info", a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.setdefault("warn", a[2]))
    window.export_pdf()
    # Erfolg ohne Dialog – nur Meldung in der Statusleiste mit Link zum Ordner
    assert target.exists() and not shown
    assert "out.pdf" in window.status_label.text() and "open-folder" in window.status_label.text()


def test_free_edit_roundtrip(window, monkeypatch):
    _open(window, SAMPLES / "beispiel.txt")
    s = window.session
    window.edit_btn.setChecked(True)
    assert not window.right.text.isReadOnly()
    window.right.text.setPlainText("komplett neu")
    assert s.output_text() == "komplett neu"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.edit_btn.setChecked(False)
    assert s.manual_text is None
    assert window.right.text.toPlainText() == s.redacted().text


def test_batch_review_flow(window, tmp_path, monkeypatch):
    import shutil

    from pii_redact.core.batch import Status, Workspace
    import pii_redact.ui.main_window as mw

    src = tmp_path / "akten"
    (src / "sub").mkdir(parents=True)
    shutil.copy(SAMPLES / "beispiel.txt", src / "a.txt")
    shutil.copy(SAMPLES / "beispiel.md", src / "sub" / "b.md")
    shutil.copy(SAMPLES / "beispiel.pdf", src / "sub" / "c.pdf")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(mw, "confirm_auto_export", lambda *a: True)

    def idle():
        t = time.time()
        while window.runner.running and time.time() - t < 120:
            _wait(100)
        _wait(200)

    window._activate_batch(Workspace.create(src, tmp_path / "ziel"))
    idle()
    ws = window.batch
    assert window.batch_rel == "a.txt" and window.session.counts()[0] > 5
    assert all(e.status == Status.TO_REVIEW for e in ws.entries.values())

    # Prüfen: Fund abwählen, bestätigen → exportiert, nächste Datei offen
    f = window.session.findings[0]
    window.session.set_active([f.id], False)
    window.confirm_and_next()
    assert ws.entries["a.txt"].status == Status.REVIEWED
    assert (tmp_path / "ziel" / "a_anonymisiert.txt").is_file()
    assert window.batch_rel == "sub/b.md"

    # Änderung an b.md, dann schließen und fortsetzen → Änderung ist noch da
    start = window.session.doc.text.index("Sommerfests")
    window.session.add_manual([(start, start + 11)], "CUSTOM")
    window.close_batch()
    window.resume_folder(tmp_path / "ziel")
    idle()
    assert window.batch_rel == "sub/b.md"
    assert any(x.source == "manuell" for x in window.session.findings)

    # Rest automatisch exportieren
    window.export_unreviewed()
    idle()
    assert window.batch.entries["sub/c.pdf"].status == Status.AUTO
    assert (tmp_path / "ziel" / "sub" / "c_geschwaerzt.pdf").is_file()
    assert window.batch.entries["a.txt"].status == Status.REVIEWED
    window.close_batch()


def test_original_optional_and_review_in_preview(window):
    from PySide6.QtGui import QTextCursor

    assert not window.left.isVisibleTo(window)  # Standard: nur die bearbeitete Fassung
    _open(window, SAMPLES / "beispiel.txt")
    s = window.session
    red = s.redacted()

    # Tooltip hinter einem Platzhalter zeigt den Originaltext
    seg = red.segments[0]
    tip = window._redacted_tooltip(seg.red_start)
    assert s.doc.text[seg.orig_start:seg.orig_end] in tip

    # In der Vorschau markieren → korrekter Bereich im Original
    word = "Personalabteilung"
    r0 = red.text.index(word)
    cur = window.right.text.textCursor()
    cur.setPosition(r0)
    cur.setPosition(r0 + len(word), QTextCursor.MoveMode.KeepAnchor)
    window.right.text.setTextCursor(cur)
    a, b = window._right_selection()
    assert s.doc.text[a:b] == word
    window._add_ranges([(a, b)], "CUSTOM", False)
    assert word not in s.redacted().text

    # Original einblenden (und die Einstellung merkt sich das)
    window.actions_["show_original"].setChecked(True)
    assert window.left.isVisibleTo(window) and window.settings.show_original
    window.actions_["show_original"].setChecked(False)


def test_deselected_finding_visible_in_preview(window):
    _open(window, SAMPLES / "beispiel.txt")
    s = window.session
    f = next(x for x in s.findings if x.text == "Hamburg")
    s.set_active([f.id], False)
    _wait(50)
    red = s.redacted()
    off = red.text.index("Hamburg")
    assert window._right_ids_at(off) == [f.id]
    assert "NICHT" in window._redacted_tooltip(off)
    assert any(m[2] == [f.id] and m[3] is False for m in window.right.text._marks)


def test_pdf_visible_after_text_document(window):
    """Regression: Nach einem Text-Dokument blieb ein PDF leer (Seiten-Container 24×24),
    bis „Original anzeigen“ umgeschaltet wurde."""
    for path in ("beispiel.md", "kommentare.pdf", "beispiel.txt", "kommentare.pdf"):
        _open(window, SAMPLES / path)
    view = window.right.pages
    pages = view._pages
    assert len(pages) == 2
    assert view._container.height() >= sum(p.height() for p in pages)
    assert view._container.width() >= pages[0].width()
    assert pages[1].y() >= pages[0].y() + pages[0].height()
    assert pages[0].pixmap is not None


def test_thorough_is_default_and_fallback_not_saved(tmp_path, monkeypatch):
    """„Gründlich“ ist Standard. Fehlt das Modell, wird „Schnell“ nur ersatzweise genutzt –
    gespeichert bleibt der Wunsch „Gründlich“."""
    import json

    from pii_redact.core import AnalysisMode, Settings
    from pii_redact.paths import find_ner_model
    from pii_redact.settings_store import settings_path

    assert Settings().analysis_mode == AnalysisMode.THOROUGH
    if find_ner_model("") is not None:
        pytest.skip("Transformer-Modell installiert – Rückfall nicht testbar")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    QApplication.instance() or QApplication([])
    from pii_redact.ui.main_window import MainWindow

    w = MainWindow()
    try:
        assert w.settings.analysis_mode == AnalysisMode.FAST and w._mode_fallback
        w._save_settings()
        saved = json.loads(settings_path().read_text(encoding="utf-8"))
        assert saved["analysis_mode"] == AnalysisMode.THOROUGH
    finally:
        w.close()


def test_zoom_modes(window):
    """Seitenbreite passt sich der Fenstergröße an; ein fester Wert bleibt über Dokumente erhalten."""
    _open(window, SAMPLES / "beispiel.pdf")
    view = window.right.pages
    window.set_zoom_mode("breite")
    _wait(150)
    w1 = view._pages[0].width()
    window.resize(window.width() + 300, window.height())
    _wait(300)
    assert view._pages[0].width() > w1 + 150                      # passt sich an
    assert not view.horizontalScrollBar().isVisible()
    window.set_zoom_mode("seite")
    _wait(150)
    assert view._pages[0].height() <= view.viewport().height()   # ganze Seite sichtbar
    window.set_zoom_mode("fest", 100)
    window.zoom_step(+1)
    assert window.settings.zoom_percent == 110 and window.current_zoom_percent() == 110
    _open(window, SAMPLES / "kommentare.pdf")                     # bleibt beim nächsten Dokument
    assert window.current_zoom_percent() == 110 and window.zoom_box.currentText() == "110 %"
    window.zoom_box.setEditText("150")
    window._on_zoom_box_edited()
    assert window.settings.zoom_mode == "fest" and window.settings.zoom_percent == 150
