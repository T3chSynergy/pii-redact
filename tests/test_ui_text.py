"""Fremder Text wird in der Oberfläche nie als HTML angezeigt (``ui/safe_text.py``).

Text aus dem Dokument, vom KI-Server, aus dem Arbeitsstand im Zielordner oder aus Fehlermeldungen könnte sonst
Formatierungen und Bilder einschleusen. Ein ``<img src="//server/…">`` lässt Windows eine Verbindung zu diesem
Server aufbauen und sich dort mit dem Konto des Nutzers anmelden."""

import os
import random
from pathlib import Path

import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6 import QtGui
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QLabel
from test_ui import _open, _wait
from test_ui_export import Dialogs

from pii_redact.core import Finding
from pii_redact.core.batch import FileEntry, Workspace
from pii_redact.core.llm_review import ReviewHint, ReviewResult
from pii_redact.ui.findings_panel import COL_SOURCE, COL_TEXT
from pii_redact.ui.safe_text import StatusLabel, plain_text
from pii_redact.ui.settings_dialog import SettingsDialog

Qt = QtGui.Qt
#: so sähe ein eingeschleustes Bild aus (Freigabe auf einem fremden Server)
IMG = "<img src='//server.example/freigabe/bild.png'>"
#: HTML in einem Datei- oder Ordnernamen (ohne Schrägstrich, sonst wäre es kein einzelner Name)
NAME = "<b>fett<i>kursiv"
needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def shown(text: str, fmt: Qt.TextFormat = Qt.TextFormat.AutoText) -> str:
    """Was Qt aus dem Text macht. Bei ``AutoText`` entscheidet Qt selbst – wie in Meldungsfenstern und Tooltips.
    Ein als HTML gelesenes ``<img>`` erscheint dabei als Platzhalterzeichen U+FFFC, nicht als Text."""
    doc = QTextDocument()
    rich = fmt == Qt.TextFormat.RichText or (fmt == Qt.TextFormat.AutoText and Qt.mightBeRichText(text))
    (doc.setHtml if rich else doc.setPlainText)(text)
    return doc.toPlainText()


def label_shows(label: QLabel) -> str:
    return shown(label.text(), label.textFormat())


# ------------------------------------------------------------------ Hilfsfunktion und Statuszeile
def test_plain_text_is_shown_literally(app):
    rnd = random.Random(5)
    parts = ["<", ">", "&", "\n", " ", "\t", "a", "img", "b", "/", "'", '"', "=", "lt;", "ä", "<!--", "<p>", "<br>"]
    texts = [IMG, f"\n{IMG}\n", "  <b>fett</b>  und\tTab", "&lt;b&gt;", "<!DOCTYPE html><p>x</p>", "ohne Tag"]
    texts += ["".join(rnd.choice(parts) for _ in range(rnd.randint(1, 15))) for _ in range(5000)]
    for text in texts:
        assert shown(plain_text(text)) == text, text
        if not Qt.mightBeRichText(text):
            assert plain_text(text) == text            # unverdächtiger Text bleibt, wie er ist


def test_injected_image_is_loaded_only_without_plain_text(app, tmp_path):
    """Belegt den Mechanismus mit einem Bild auf der Festplatte: Qt lädt es, sobald der Text als HTML gilt."""
    picture = tmp_path / "bild.png"
    image = QImage(150, 150, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(picture))
    tag = f"<img src='{QUrl.fromLocalFile(str(picture)).toString()}'>"
    label = QLabel()
    label.setText(tag)
    assert label.sizeHint().height() >= 150            # Bild geladen
    label.setText(plain_text(tag))
    assert label.sizeHint().height() < 60 and label_shows(label) == tag


def test_status_label_is_plain_unless_html_is_asked_for(app):
    status = StatusLabel("Bereit")
    status.setText(IMG)
    assert status.textFormat() == Qt.TextFormat.PlainText and label_shows(status) == IMG
    status.set_html("✓ gespeichert · <a href='open-folder'>Ordner öffnen</a>")
    assert status.textFormat() == Qt.TextFormat.RichText and label_shows(status) == "✓ gespeichert · Ordner öffnen"
    status.setText(f"„{IMG}“ wird künftig nie geschwärzt")
    assert status.textFormat() == Qt.TextFormat.PlainText


# ------------------------------------------------------------------ Oberfläche
@pytest.fixture()
def opened(window, tmp_path):
    """Fenster mit einem Text, der ein eingeschleustes Bild enthält, und einem Vorschlag der KI dazu."""
    window.settings.analysis_mode = "schnell"
    text = f"Hinweis für den Prüfer: {IMG}\nFrau Erika Beispiel wohnt in Musterstadt.\n"
    source = tmp_path / "brief.txt"
    source.write_text(text, encoding="utf-8")
    _open(window, source)
    start = text.index(IMG)
    window.session.add_findings([Finding(start, start + len(IMG), "CONTEXT", IMG, source="ki", active=False,
                                         recognizer=f"KI-Nachprüfung: {IMG}")])
    _wait(50)
    return window


@needs_model
def test_ki_result_and_failure_are_plain_text(opened, monkeypatch):
    window = opened
    hint = ReviewHint(quote=IMG, kind="KONTEXT", reason=f"{IMG} Begründung", entity_type="CONTEXT")
    window.ki_panel.show_result(ReviewResult("hoch", f"{IMG} Zusammenfassung", [hint], IMG, "llm.example"), [[]])
    panel = window.ki_panel
    assert label_shows(panel.summary) == f"{IMG} Zusammenfassung"
    assert label_shows(panel.meta).startswith(f"{IMG} über llm.example")
    assert shown(panel.list.item(0).toolTip()) == f"{IMG} Begründung"

    d = Dialogs(monkeypatch)
    window._ki_session = window.session
    window._on_ki_failed(window.session, f"{IMG} Fehler vom Server")
    assert [shown(text) for _kind, _title, text in d.shown] == [f"{IMG} Fehler vom Server"]
    assert label_shows(panel.meta) == f"Fehlgeschlagen: {IMG} Fehler vom Server"


@needs_model
def test_findings_tooltips_are_plain_text(opened):
    model = opened.findings.model
    row = next(i for i in range(model.rowCount()) if model._rows[i].source == "ki")
    tips = {col: model.data(model.index(row, col), Qt.ItemDataRole.ToolTipRole) for col in (COL_TEXT, COL_SOURCE)}
    assert shown(tips[COL_TEXT]) == IMG
    assert shown(tips[COL_SOURCE]) == f"KI-Nachprüfung: {IMG}"


@needs_model
def test_messages_and_status_are_plain_text(opened, monkeypatch, tmp_path):
    window = opened
    d = Dialogs(monkeypatch)
    window._on_analysis_failed(f"{IMG} Analyse")
    assert [shown(text) for _kind, _title, text in d.shown] == [f"{IMG} Analyse"]

    window._show_saved(tmp_path / f"{NAME}.pdf", "PDF geschwärzt", details=f"{IMG} Rest")
    assert label_shows(window.status_label) == f"✓ {NAME}.pdf – PDF geschwärzt · Ordner öffnen"
    assert shown(window.status_label.toolTip()) == f"{IMG} Rest"


@needs_model
def test_folder_names_and_notes_are_plain_text(window, tmp_path):
    """Dateinamen, Quellordner und Vermerke stehen im Arbeitsstand – den kann ändern, wer in den Zielordner
    schreiben darf (unter Windows sind ``<`` und ``>`` in echten Dateinamen gar nicht erlaubt)."""
    source = tmp_path / "quelle"
    source.mkdir()
    (source / "brief.txt").write_text("Frau Erika Beispiel.\n", encoding="utf-8")
    ws = Workspace.create(source, tmp_path / "ziel")
    rel = f"{IMG}.txt"
    ws.entries[rel] = FileEntry(rel=rel, note=f"{IMG} Vermerk")
    ws.source = Path(tmp_path / f"quelle{NAME}")
    window.batch_panel.load(ws)
    panel = window.batch_panel
    item = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
                if panel.tree.topLevelItem(i).text(0) == rel)
    assert shown(item.toolTip(0)) == rel
    assert shown(item.toolTip(2)).endswith(f"\n{IMG} Vermerk")
    assert label_shows(panel.title) == f"quelle{NAME} → ziel"
    assert shown(panel.title.toolTip()).startswith(f"Quelle: {ws.source}\n")

    window.review_bar.set_file(1, 2, rel, "zu prüfen", "#000000", True)
    assert label_shows(window.review_bar.label) == f"Datei 1 von 2: {rel}"


def test_connection_test_result_is_plain_text(window):
    dlg = SettingsDialog(window.settings, window)
    assert dlg.test_result.textFormat() == Qt.TextFormat.PlainText
