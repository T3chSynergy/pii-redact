"""Rauchtest der Oberfläche (läuft ohne Bildschirm mit QT_QPA_PLATFORM=offscreen)."""

import os
import time
from pathlib import Path

import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

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
    _app = QApplication.instance() or QApplication([])  # Referenz halten, sonst räumt Python die App ab
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
    from pii_redact.ui import dialogs

    monkeypatch.setattr(dialogs, "ask_yes_no", lambda *a, **k: True)
    window.edit_btn.setChecked(False)
    assert s.manual_text is None
    assert window.right.text.toPlainText() == s.redacted().text


def test_batch_review_flow(window, tmp_path, monkeypatch):
    import shutil

    import pii_redact.ui.window_batch as wb
    from pii_redact.core.batch import Status, Workspace

    src = tmp_path / "akten"
    (src / "sub").mkdir(parents=True)
    shutil.copy(SAMPLES / "beispiel.txt", src / "a.txt")
    shutil.copy(SAMPLES / "beispiel.md", src / "sub" / "b.md")
    shutil.copy(SAMPLES / "beispiel.pdf", src / "sub" / "c.pdf")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(wb, "confirm_auto_export", lambda *a: True)

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


def _menu_texts(menu) -> list[str]:
    from PySide6.QtWidgets import QMenu

    texts = [a.text() for a in menu.actions()]
    for sub in menu.findChildren(QMenu):
        texts.append(sub.title())
    return texts


def test_finding_menu_and_lists(window):
    """Kontextmenü zu einem Fund und zu einer Markierung; Ausnahme- und Sperrliste."""
    _open(window, SAMPLES / "beispiel.txt")
    s = window.session
    f = next(x for x in s.findings if x.text == "Hamburg")
    same = s.same_text_ids(f.id)

    menu = window.build_finding_menu([f.id])
    texts = _menu_texts(menu)
    assert "Nicht schwärzen" in texts and "Typ ändern" in texts and "Fund löschen" in texts
    next(a for a in menu.actions() if a.text() == "Nicht schwärzen").trigger()
    assert not s.get(f.id).active
    next(a for a in menu.actions() if a.text().startswith("Immer ignorieren")).trigger()
    assert "Hamburg" in window.settings.allow_list
    assert not any(s.get(i).active for i in same)

    # Markierung ohne Fund: Schwärzen und Sperrliste werden angeboten
    word = "Personalabteilung"
    start = s.doc.text.index(word)
    menu = window.build_finding_menu([], [(start, start + len(word))])
    texts = _menu_texts(menu)
    assert "Schwärzen als" in texts and "Kopieren" in texts
    next(a for a in menu.actions() if a.text().startswith("Immer schwärzen")).trigger()
    assert word in window.settings.deny_list
    assert word not in s.redacted().text

    assert window.build_finding_menu([]) is None                 # nichts anzubieten → kein Menü


def test_sync_scroll(window):
    """Gekoppeltes Scrollen der Seitenansicht und des Textes; abschaltbar."""
    window.actions_["show_original"].setChecked(True)
    window.resize(1000, 500)
    _open(window, SAMPLES / "beispiel.pdf")
    window.set_zoom_mode("fest", 200)
    _wait(200)
    left, right = window.left.pages.verticalScrollBar(), window.right.pages.verticalScrollBar()
    assert left.maximum() > 0
    left.setValue(left.maximum() // 2)
    assert right.value() == left.value()

    # Text: Position im Original → passende Stelle in der Vorschau
    window.left.tabs.setCurrentWidget(window.left.text)
    assert window.right.tabs.currentWidget() is window.right.text      # Reiter folgen
    _wait(100)
    tl = window.left.text.verticalScrollBar()
    assert tl.maximum() > 0
    tl.setValue(tl.maximum())
    _wait(50)
    assert window.right.text.verticalScrollBar().value() > 0

    window.actions_["sync"].setChecked(False)
    assert not window.settings.sync_scroll
    left.setValue(0)
    assert right.value() > 0                                          # bleibt stehen
    window.actions_["sync"].setChecked(True)
    window.actions_["show_original"].setChecked(False)


def test_keyboard_selection_and_detail(window):
    """Barrierefreiheit: Textcursor per Tastatur auf einem Platzhalter wählt den Fund; die Angaben stehen
    dauerhaft unter der Fundliste (nicht nur im Tooltip); Leertaste-Funktion schaltet an/aus."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    _open(window, SAMPLES / "beispiel.txt")
    s = window.session
    f = next(x for x in s.findings if x.text == "Hamburg")
    view = window.right.text
    r = s.redacted().to_redacted(f.start)
    cur = view.textCursor()
    cur.setPosition(view._map.to_qt(r))
    view.setTextCursor(cur)
    assert window.findings.detail.text() == "" or f.id not in window.findings.selected_ids()
    QTest.keyClick(view, Qt.Key.Key_Right)          # in den Platzhalter hinein
    _wait(50)
    assert f.id in window.findings.selected_ids()
    detail = window.findings.detail
    assert detail.isVisible() and "Hamburg" in detail.text() and "wird geschwärzt" in detail.text()

    window.findings.toggle_selected()               # wie Leertaste in der Fundliste
    assert not s.get(f.id).active
    assert window.findings.last_announcement == "Hamburg: nicht geschwärzt"   # Ansage für Bildschirmleser
    _wait(50)
    assert "NICHT geschwärzt" in window.findings.detail.text()
    window.findings.toggle_selected()
    assert s.get(f.id).active
    assert window.findings.last_announcement == "Hamburg: wird geschwärzt"

    # Bildschirmleser lesen die aktuelle Zelle: der Status steht in jeder Zelle der Zeile
    from pii_redact.ui.findings_panel import COL_ON, COL_TEXT
    proxy = window.findings.proxy
    row = next(r for r in range(proxy.rowCount()) if proxy.index(r, COL_TEXT).data() == "Hamburg")
    acc = Qt.ItemDataRole.AccessibleTextRole
    assert proxy.index(row, COL_TEXT).data(acc) == "Hamburg, wird geschwärzt"
    assert proxy.index(row, COL_ON).data(acc) == "wird geschwärzt"
    assert proxy.headerData(COL_ON, Qt.Orientation.Horizontal, acc) == "Schwärzen"

    assert window.findings.table.accessibleName() == "Fundliste"
    assert window.left.pages.accessibleDescription()


def test_findings_focus_lands_on_text_column(window):
    """Barrierefreiheit: Fokus in der Fundliste landet auf der Spalte „Text“ (die liest der Bildschirmleser vor)."""
    from PySide6.QtCore import Qt

    from pii_redact.ui.findings_panel import COL_ON, COL_TEXT

    _open(window, SAMPLES / "beispiel.txt")
    table = window.findings.table
    window.left.text.setFocus()
    _wait(20)
    table.setFocus(Qt.FocusReason.TabFocusReason)
    _wait(20)
    assert table.currentIndex().column() == COL_TEXT and table.currentIndex().row() == 0
    assert window.findings.selected_ids()                     # Zeile ist gewählt → Detailzeile gefüllt

    table.setCurrentIndex(table.model().index(2, COL_ON))      # z. B. nach Mausklick auf das Häkchen
    window.left.text.setFocus()
    _wait(20)
    table.setFocus(Qt.FocusReason.BacktabFocusReason)
    _wait(20)
    assert (table.currentIndex().row(), table.currentIndex().column()) == (2, COL_TEXT)

    # Auswahl aus dem Text heraus setzt die aktuelle Zelle ebenfalls auf „Text“
    fid = table.model().index(1, COL_TEXT).data(Qt.ItemDataRole.UserRole + 1)
    window.findings.select_ids([fid])
    assert table.currentIndex().column() == COL_TEXT


def test_tab_leaves_findings_table(window):
    """Barrierefreiheit: Tab springt aus der Fundliste heraus, nicht von Zelle zu Zelle."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    _open(window, SAMPLES / "beispiel.txt")
    table = window.findings.table
    assert table.model().rowCount() > 0
    table.setFocus()
    table.setCurrentIndex(table.model().index(0, 0))
    _wait(20)
    QTest.keyClick(table, Qt.Key.Key_Tab)
    _wait(20)
    assert QApplication.focusWidget() is not table


def test_keyboard_context_menu_at_cursor():
    """Menütaste / Umschalt+F10 öffnet das Kontextmenü an der Textcursor-Position."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication

    from pii_redact.ui.text_view import HighlightTextView

    _app = QApplication.instance() or QApplication([])
    view = HighlightTextView()
    view.set_text("Hallo Welt, hier ist ein Test.")
    cur = view.textCursor()
    cur.setPosition(6)
    view.setTextCursor(cur)
    got = []
    view.contextAt.connect(lambda off, pos: got.append(off))
    QApplication.sendEvent(view, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0)))
    assert got == [6]


def test_notice_bar_details_accessible():
    """Hinweisleiste: voller Text als Beschreibung für Bildschirmleser, Knopf „Details …“."""
    from PySide6.QtWidgets import QApplication

    from pii_redact.core.loaders import Level, Notice
    from pii_redact.ui.notices import NoticeBar

    _app = QApplication.instance() or QApplication([])
    bar = NoticeBar()
    bar.set_notices([Notice("ocr", Level.CRITICAL, "Seite 2 per Texterkennung", "Ausführlicher Hinweis zur OCR.")])
    assert "Ausführlicher Hinweis" in bar.accessibleDescription()
    assert bar.close_btn.accessibleName() and bar.details_btn.accessibleName()
    hits = []
    bar.detailsRequested.connect(lambda: hits.append(1))
    bar.details_btn.click()
    assert hits == [1]


def test_menu_mnemonics_unique(window):
    """Barrierefreiheit: jeder Menüeintrag hat einen eigenen unterstrichenen Buchstaben (Alt, D, P …)."""
    import re

    def key(text):
        m = re.search(r"&([^&])", text.replace("&&", ""))
        return m.group(1).casefold() if m else None

    menus = [a.menu() for a in window.menuBar().actions() if a.menu()]
    assert [key(m.title()) for m in menus] == ["d", "b", "a", "h"]
    for menu in menus:
        keys = [key(a.text()) for a in menu.actions() if not a.isSeparator()]
        assert None not in keys, (menu.title(), [a.text() for a in menu.actions()])
        assert len(keys) == len(set(keys)), (menu.title(), keys)


def test_check_before_saving_adds_suggestions_and_asks(window, tmp_path, monkeypatch):
    """Geschwärzter Text steht noch an anderer Stelle: Vorschläge in die Fundliste, Rückfrage vor dem Speichern."""
    from pii_redact.core import Finding
    from pii_redact.ui.main_window import MainWindow

    source = tmp_path / "antrag.txt"
    source.write_text("Antrag von Herrn Deniz Kaya.\nAnhang: antrag_kaya.pdf\nMit freundlichen Grüßen",
                      encoding="utf-8")
    _open(window, source)
    s = window.session
    start = s.doc.text.index("Kaya")
    s.set_findings([Finding(start, start + 4, "PERSON", "Kaya")])
    target = tmp_path / "out.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    asked = []

    # 1. „Zurück zur Prüfung“: nichts gespeichert, Vorschlag steht ohne Häkchen in der Liste
    monkeypatch.setattr(MainWindow, "_ask_further", lambda self, new: asked.append([f.text for f in new]) or False)
    window.export_text()
    assert asked == [["kaya"]] and not target.exists()
    suggestion = next(f for f in s.findings if f.source == "kontrolle")
    assert suggestion.text == "kaya" and not suggestion.active and suggestion.entity_type == "PERSON"
    assert window.findings.selected_ids() == [suggestion.id]
    assert "Kontrolle" in window.status_label.text()

    # 2. Häkchen gesetzt: Speichern ohne weitere Rückfrage, die Stelle ist geschwärzt
    s.set_active([suggestion.id], True)
    window.export_text()
    assert len(asked) == 1 and "kaya" not in target.read_text(encoding="utf-8").casefold()

    # 3. Vorschlag bewusst ohne Häkchen gelassen: ebenfalls keine erneute Rückfrage
    s.set_active([suggestion.id], False)
    target.unlink()
    window.export_text()
    assert len(asked) == 1 and "antrag_kaya.pdf" in target.read_text(encoding="utf-8")
    assert not target.with_name(target.name + ".tmp").exists()      # geschrieben über <Ziel>.tmp, dann umbenannt


def test_check_before_saving_pdf_save_anyway(window, tmp_path, monkeypatch):
    """„Trotzdem speichern“: gespeichert wird, und die Kontrolle danach meldet dieselben Stellen nicht noch einmal."""
    import pymupdf

    from pii_redact.core import Finding
    from pii_redact.ui.main_window import MainWindow

    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Antrag von Herrn Deniz Kaya.")
    page.insert_text((72, 140), "Anhang: antrag_kaya.pdf")
    source = tmp_path / "antrag.pdf"
    source.write_bytes(pdf.tobytes())
    _open(window, source)
    s = window.session
    start = s.doc.text.index("Kaya")
    s.set_findings([Finding(start, start + 4, "PERSON", "Kaya")])
    target = tmp_path / "out.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(MainWindow, "_ask_further", lambda self, new: True)
    shown = {}
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.setdefault("warn", a[2]))
    window.export_pdf()
    assert target.exists() and not shown
    assert [f.text for f in s.findings if f.source == "kontrolle" and not f.active] == ["kaya"]
    text = pymupdf.open(target)[0].get_text()
    assert "Deniz" in text and "Kaya" not in text and "antrag_kaya.pdf" in text


def test_auto_export_holds_back_files_with_further_occurrences(window, tmp_path, monkeypatch):
    """„Ungeprüfte Dateien exportieren“: Was die Kontrolle beanstandet, bleibt zur Prüfung stehen."""
    import shutil

    import pii_redact.ui.window_batch as wb
    from pii_redact.core.batch import CHECK_NOT_AUTO, Status, Workspace
    from pii_redact.ui.main_window import MainWindow

    src = tmp_path / "akten"
    src.mkdir()
    (src / "a_antrag.txt").write_text("Antrag von Herrn Kaya.\nAnhang: antrag_kaya.pdf\n", encoding="utf-8")
    shutil.copy(SAMPLES / "beispiel.txt", src / "b_brief.txt")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(wb, "confirm_auto_export", lambda *a: True)

    def idle():
        t = time.time()
        while window.runner.running and time.time() - t < 120:
            _wait(100)
        _wait(200)

    window._activate_batch(Workspace.create(src, tmp_path / "ziel"))
    idle()
    window.export_unreviewed()
    idle()
    ws = window.batch
    assert ws.entries["b_brief.txt"].status == Status.AUTO
    assert (tmp_path / "ziel" / "b_brief_anonymisiert.txt").is_file()
    held = ws.entries["a_antrag.txt"]
    assert held.status == Status.TO_REVIEW and held.note == CHECK_NOT_AUTO
    assert not (tmp_path / "ziel" / "a_antrag_anonymisiert.txt").exists()
    assert "hält die Kontrolle" in window.status_label.text()
    # die offene Datei zeigt den Vorschlag in der Fundliste; mit Häkchen und „Geprüft & weiter“ wird exportiert
    assert window.batch_rel == "a_antrag.txt"
    suggestion = next(f for f in window.session.findings if f.source == "kontrolle")
    window.session.set_active([suggestion.id], True)
    monkeypatch.setattr(MainWindow, "_ask_further", lambda self, new: pytest.fail("keine Rückfrage erwartet"))
    window.confirm_and_next()
    assert ws.entries["a_antrag.txt"].status == Status.REVIEWED
    assert "kaya" not in (tmp_path / "ziel" / "a_antrag_anonymisiert.txt").read_text(encoding="utf-8").casefold()
    window.close_batch()


def test_dialog_buttons_are_german(window, monkeypatch):
    """Rückfragen und Dialoge tragen deutsche Schaltflächen – auch ohne geladene Qt-Übersetzung."""
    from PySide6.QtWidgets import QDialogButtonBox

    from pii_redact.core import Settings
    from pii_redact.ui import batch_panel, dialogs
    from pii_redact.ui.settings_dialog import SettingsDialog

    seen = {}

    def fake_exec(box):
        seen["texts"] = sorted(b.text() for b in box.buttons())
        seen["default"] = box.defaultButton().text()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert dialogs.ask_yes_no(window, "Titel", "Frage?") is False
    assert seen == {"texts": ["Ja", "Nein"], "default": "Nein"}
    batch_panel.confirm_auto_export(window, 2, 0, 1)
    assert seen["texts"] == ["Abbrechen", "Ohne Prüfung exportieren"] and seen["default"] == "Abbrechen"

    english = {"Cancel", "Yes", "No", "Close", "Apply"}
    for dialog in (SettingsDialog(Settings(), window), batch_panel.NewBatchDialog(window)):
        texts = {b.text().replace("&", "") for box in dialog.findChildren(QDialogButtonBox) for b in box.buttons()}
        assert texts and not texts & english, texts
