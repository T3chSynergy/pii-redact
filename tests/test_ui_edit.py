"""Oberfläche: Bearbeiten der Funde (Klicks, Kontextmenüs, Markierung, Bereiche, freie Bearbeitung),
Einstellungsdialog und Fehlerwege der KI-Nachprüfung.

Menüs, die das Programm selbst öffnet, bedient ``_in_menu``: Es wählt den genannten Eintrag und schließt das
Menü, sobald es offen ist."""

import os
import time

import pymupdf
import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import QApplication, QMenu
from test_ui import SAMPLES, _open, _wait
from test_ui_export import Dialogs

from pii_redact.core import Finding, Settings, llm_review
from pii_redact.ui import dialogs, settings_dialog
from pii_redact.ui.settings_dialog import SettingsDialog

pytestmark = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")

TEXT = ("Frau Erika Beispiel wohnt in Musterstadt.\n"
        "Erika Beispiel ist per E-Mail erreichbar: erika.beispiel@firma.example\n"
        "Das Projekt Falke startet im Mai. Projekt Falke ist vertraulich.\n")


@pytest.fixture()
def editor(window, tmp_path):
    """Fenster mit geöffnetem Text und drei festen Funden: zweimal „Erika Beispiel“, einmal die E-Mail-Adresse."""
    window.settings.analysis_mode = "schnell"
    source = tmp_path / "brief.txt"
    source.write_text(TEXT, encoding="utf-8")
    _open(window, source)
    s = window.session
    first = TEXT.index("Erika Beispiel")
    second = TEXT.index("Erika Beispiel", first + 1)
    mail = TEXT.index("erika.beispiel@")
    s.set_findings([Finding(first, first + 14, "PERSON", "Erika Beispiel"),
                    Finding(second, second + 14, "PERSON", "Erika Beispiel"),
                    Finding(mail, mail + 28, "EMAIL_ADDRESS", TEXT[mail:mail + 28])])
    s.dirty = False
    _wait(50)
    return window


def _in_menu(path: list[str] | None) -> list[list[str]]:
    """Das nächste Menü, das sich öffnet, bedienen: Eintrag ``path`` wählen (Untermenüs der Reihe nach) oder nur
    schließen (None). Rückgabe: Liste, in der nach dem Schließen die Einträge der obersten Ebene stehen."""
    seen: list[list[str]] = []
    deadline = time.time() + 10

    def act():
        menus = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible()]
        if not menus:
            if time.time() < deadline:
                QTimer.singleShot(20, act)
            return
        menu = menus[0]
        try:
            seen.append([a.text() for a in menu.actions() if a.text()])
            if path:
                _action(menu, *path).trigger()
        finally:
            menu.close()                 # immer schließen – sonst wartet der Test auf das offene Menü

    QTimer.singleShot(0, act)
    return seen


def _action(menu: QMenu, *path: str):
    """Eintrag eines mit ``build_*_menu`` erzeugten Menüs, auch in Untermenüs."""
    target = menu
    for i, name in enumerate(path):
        action = next(a for a in target.actions() if a.text() == name)
        if i == len(path) - 1:
            return action
        target = action.menu()


def _select(view, start: int, end: int) -> None:
    cur = view.textCursor()
    cur.setPosition(start)
    cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cur)


# ------------------------------------------------------------------ Klicks
def test_click_selects_finding_in_both_views(editor):
    window, s = editor, editor.session
    first, second, mail = s.findings
    window._on_left_text_click(second.start + 3, QPoint())
    assert window.findings.selected_ids() == [second.id]
    window._on_left_text_click(TEXT.index("wohnt"), QPoint())             # kein Fund an der Stelle
    assert window.findings.selected_ids() == [second.id]

    red = s.redacted()
    window._on_right_text_click(red.to_redacted(mail.start) + 1, QPoint())        # Klick auf den Platzhalter
    assert window.findings.selected_ids() == [mail.id]
    assert window._redacted_tooltip(red.to_redacted(mail.start) + 1).startswith("E-Mail: erika.beispiel@")

    s.set_active([first.id], False)                                       # abgewählt: Klartext in der Vorschau
    _wait(50)
    pos = s.redacted().text.index("Erika Beispiel") + 2
    window._on_right_text_click(pos, QPoint())
    assert window.findings.selected_ids() == [first.id]
    assert "wird NICHT geschwärzt" in window._redacted_tooltip(pos)
    window._on_right_text_click(s.redacted().text.index("wohnt"), QPoint())
    assert window.findings.selected_ids() == [first.id]

    s.manual_text = "frei bearbeitet"                                     # freie Bearbeitung: Vorschau reagiert nicht
    window._on_right_text_click(pos, QPoint())
    window._on_left_text_click(second.start, QPoint())                    # links weiter möglich
    assert window.findings.selected_ids() == [second.id]
    assert window._redacted_tooltip(pos) == "" and window._right_selection() is None


def test_tooltip_explains_origin_of_suggestions(editor):
    window, s = editor, editor.session
    falke = TEXT.index("Projekt Falke")
    s.add_findings([Finding(falke, falke + 13, "CONTEXT", "Projekt Falke", source="ki", active=False,
                            recognizer="KI-Nachprüfung"),
                    Finding(TEXT.index("Musterstadt"), TEXT.index("Musterstadt") + 11, "LOCATION", "Musterstadt",
                            source="kontrolle", active=False)])
    ki = next(f for f in s.findings if f.source == "ki")
    check = next(f for f in s.findings if f.source == "kontrolle")
    assert window._ids_tooltip([ki.id]).splitlines()[1] == "  Vorschlag der KI-Nachprüfung"
    assert "Vorschlag der Kontrolle" in window._ids_tooltip([check.id])
    assert window._ids_tooltip([]) == "" and window._ids_tooltip([99999]) == ""


# ------------------------------------------------------------------ Kontextmenüs
def test_context_menu_requests_carry_findings_and_selection(editor, monkeypatch):
    window, s = editor, editor.session
    first = s.findings[0]
    calls = []
    monkeypatch.setattr(type(window), "_finding_menu", lambda self, ids, pos, ranges=None: calls.append((ids, ranges)))
    falke = TEXT.index("Projekt Falke")
    _select(window.left.text, falke, falke + 13)
    window._on_left_text_context(first.start + 1, QPoint())
    assert calls[-1] == ([first.id], [(falke, falke + 13)])
    _select(window.left.text, 0, 0)
    window._on_left_text_context(TEXT.index("wohnt"), QPoint())
    assert calls[-1] == ([], None)

    n = len(calls)
    window._on_right_text_context(s.redacted().text.index("wohnt"), QPoint())      # weder Fund noch Markierung
    assert len(calls) == n
    red = s.redacted()
    r0 = red.text.index("Projekt Falke")
    _select(window.right.text, r0, r0 + 13)
    window._on_right_text_context(r0, QPoint())
    assert calls[-1] == ([], [(falke, falke + 13)])                        # Markierung der Vorschau → Original
    s.manual_text = "frei"
    window._on_right_text_context(r0, QPoint())
    assert len(calls) == n + 1


def test_finding_menu_actions(editor):
    window, s = editor, editor.session
    first, second, mail = s.findings
    menu = window.build_finding_menu([first.id])
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Alle 2 Vorkommen schwärzen" in texts and "Alle 2 Vorkommen nicht schwärzen" in texts
    _action(menu, "Alle 2 Vorkommen nicht schwärzen").trigger()
    assert not first.active and not second.active and mail.active
    menu = window.build_finding_menu([first.id])
    assert _action(menu, "Schwärzen") is not None                         # abgewählt → Angebot dreht sich um
    _action(menu, "Alle 2 Vorkommen schwärzen").trigger()
    assert first.active and second.active

    _action(window.build_finding_menu([mail.id]), "Typ ändern", "Weitere").menu().actions()[0].trigger()
    assert s.get(mail.id).entity_type != "EMAIL_ADDRESS"
    _action(window.build_finding_menu([mail.id]), "Typ ändern", "E-Mail").trigger()
    assert s.get(mail.id).entity_type == "EMAIL_ADDRESS"

    _action(window.build_finding_menu([first.id]), "Immer ignorieren (Ausnahmeliste)").trigger()
    assert window.settings.allow_list == ["Erika Beispiel"] and not first.active and not second.active
    assert "wird künftig nie geschwärzt" in window.status_label.text()
    _action(window.build_finding_menu([second.id]), "Immer ignorieren (Ausnahmeliste)").trigger()
    assert window.settings.allow_list == ["Erika Beispiel"]               # kein doppelter Eintrag

    _action(window.build_finding_menu([mail.id]), "Fund löschen").trigger()
    assert s.get(mail.id) is None and len(s.findings) == 2
    assert window.build_finding_menu([]) is None and window.build_finding_menu([99999]) is None


def test_selection_menu_actions_and_lists(editor):
    window, s = editor, editor.session
    falke = TEXT.index("Projekt Falke")
    ranges = [(falke, falke + 13)]
    menu = window.build_finding_menu([], ranges)
    assert menu.actions()[0].text() == "Markierung: Projekt Falke"
    _action(menu, "Kopieren").trigger()
    assert QGuiApplication.clipboard().text() == "Projekt Falke"
    _action(menu, "Schwärzen als", "Organisation").trigger()
    assert [(f.text, f.entity_type) for f in s.findings if f.source == "manuell"] == [("Projekt Falke", "ORGANIZATION")]
    assert window.status_label.text() == "1 Stelle(n) hinzugefügt."

    _action(window.build_finding_menu([], ranges), "Immer schwärzen (Sperrliste)").trigger()
    assert window.settings.deny_list == ["Projekt Falke"]
    assert sum(f.text == "Projekt Falke" and f.active for f in s.findings) == 2          # beide Vorkommen
    assert "wird künftig immer geschwärzt" in window.status_label.text()

    long = [(0, 60)]
    assert window.build_finding_menu([], long).actions()[0].text().endswith("…")


def test_mark_selection_uses_marked_text_or_explains(editor, monkeypatch):
    window, s = editor, editor.session
    d = Dialogs(monkeypatch)
    window._mark_selection(False)
    assert d.shown[0][:2] == ("information", "Markierung schwärzen") and "Textstelle markieren" in d.shown[0][2]

    r0 = s.redacted().text.index("Musterstadt")
    _select(window.right.text, r0, r0 + 11)                               # in der Vorschau markiert
    seen = _in_menu(["Schwärzen als", "Ort"])
    window._mark_selection(False)
    assert seen == [["Schwärzen als"]]
    new = [f for f in s.findings if f.source == "manuell"]
    assert [(f.text, f.entity_type) for f in new] == [("Musterstadt", "LOCATION")]

    r0 = s.redacted().text.index("Projekt Falke")
    _select(window.right.text, r0, r0 + 13)
    _in_menu(["Schwärzen als", "Benutzerdefiniert"])
    window._mark_selection(True)                                          # alle Vorkommen
    assert sum(f.text == "Projekt Falke" for f in s.findings) == 2

    _select(window.right.text, 0, 0)
    _in_menu(None)                                                        # Menü ohne Auswahl geschlossen
    n = len(s.findings)
    window._mark_selection(False)
    assert len(s.findings) == n and len(d.shown) == 2


def test_edit_actions_without_document_do_nothing(window, monkeypatch):
    d = Dialogs(monkeypatch)
    window._on_left_text_click(0, QPoint())
    window._on_right_text_click(0, QPoint())
    window._on_left_text_context(0, QPoint())
    window._on_right_text_context(0, QPoint())
    window._on_area_selected(0, pymupdf.Rect(0, 0, 10, 10), QPoint())
    window._mark_selection(False)
    window._set_all(False)
    window.undo()
    window.redo()
    window._toggle_free_edit(True)
    window._on_free_text_changed()
    window._ki_set_all(True)
    window._ki_discard()
    window.start_ki_review()
    assert window._ids_tooltip([1]) == "" and window._redacted_tooltip(0) == "" and window._ki_ids(False) == []
    assert window.build_finding_menu([1]) is None and not d.shown and not d.boxes


def test_set_all_undo_redo(editor):
    window, s = editor, editor.session
    window._set_all(False)
    assert s.counts() == (3, 0)
    window.undo()
    assert s.counts() == (3, 3)
    window.redo()
    assert s.counts() == (3, 0)
    window._set_all(True)
    assert s.counts() == (3, 3)


# ------------------------------------------------------------------ Bereiche im PDF
def test_area_drawn_over_text_offers_that_text(window, monkeypatch):
    window.settings.analysis_mode = "schnell"
    _open(window, SAMPLES / "beispiel.pdf")
    doc = window.session.doc
    word = "Bergmann"
    start = doc.text.index(word)
    boxes = [doc.char_boxes[i] for i in range(start, start + len(word))]
    page = boxes[0].page
    rect = pymupdf.Rect(min(b.rect[0] for b in boxes) - 1, min(b.rect[1] for b in boxes) - 1,
                        max(b.rect[2] for b in boxes) + 1, max(b.rect[3] for b in boxes) + 1)
    calls = []
    monkeypatch.setattr(type(window), "_area_menu", lambda self, p, r, pos, ranges: calls.append((p, ranges)))
    window._on_area_selected(page, rect, QPoint())
    assert calls == [(page, [(start, start + len(word))])]

    # Rahmen über zwei Wörter einer Zeile: ein zusammenhängender Bereich, der Zwischenraum gehört dazu
    name = "Maximilian Bergmann"
    s0 = doc.text.index(name)
    b2 = [doc.char_boxes[i] for i in range(s0, s0 + len(name)) if doc.char_boxes[i] is not None]
    wide = pymupdf.Rect(min(b.rect[0] for b in b2) - 1, min(b.rect[1] for b in b2) - 1,
                        max(b.rect[2] for b in b2) + 1, max(b.rect[3] for b in b2) + 1)
    window._on_area_selected(page, wide, QPoint())
    assert calls[-1] == (page, [(s0, s0 + len(name))])

    window._on_area_selected(page, pymupdf.Rect(0, 0, 3, 3), QPoint())    # leere Ecke: kein Text
    assert calls[-1] == (page, [])
    window._on_area_selected(page + 50, rect, QPoint())                   # Seite ohne Zeichen
    assert calls[-1] == (page + 50, [])


def test_area_menu_with_text_adds_text_and_area(window):
    window.settings.analysis_mode = "schnell"
    _open(window, SAMPLES / "beispiel.pdf")
    s = window.session
    s.set_findings([])
    start = s.doc.text.index("Bergmann")
    ranges = [(start, start + 8)]
    rect = pymupdf.Rect(50, 50, 200, 80)
    menu = window.build_area_menu(0, rect, ranges)
    assert menu.actions()[0].text() == "Text im Rahmen: Bergmann"
    _action(menu, "Text schwärzen als", "Person").trigger()
    assert [(f.text, f.entity_type, f.is_area) for f in s.findings] == [("Bergmann", "PERSON", False)]

    _action(window.build_area_menu(0, rect, ranges), "Alle Vorkommen schwärzen als", "Person").trigger()
    assert {f.text.casefold() for f in s.findings} == {"bergmann"} and len(s.findings) >= 2

    s.set_findings([])
    _action(window.build_area_menu(0, rect, ranges), "Ganzen Bereich schwärzen als", "Unterschrift").trigger()
    area = [f for f in s.findings if f.is_area]
    assert len(area) == 1 and area[0].entity_type == "AREA_SIGNATURE"
    assert [f.text for f in s.findings if not f.is_area] == ["Bergmann"]          # Text im Bereich gehört dazu
    assert window.status_label.text() == "Bereich auf Seite 1 wird geschwärzt (Unterschrift)."
    assert window.selected_ids == {s.findings[0].id}                     # der neue Bereich ist hervorgehoben

    # Menü zu einem Bereich: abwählen, Typ ändern, löschen
    aid = [area[0].id]
    m = window.build_finding_menu(aid)
    assert m.actions()[0].text() == "Unterschrift – Bereich auf Seite 1"
    _action(m, "Nicht schwärzen").trigger()
    assert not s.get(aid[0]).active
    _action(window.build_finding_menu(aid), "Typ ändern", "Handschrift").trigger()
    assert s.get(aid[0]).entity_type == "AREA_HANDWRITING"
    assert "frei gezogener Bereich  (wird NICHT geschwärzt)" in window._ids_tooltip(aid)
    _action(window.build_finding_menu(aid), "Bereich löschen").trigger()
    assert s.get(aid[0]) is None


def test_area_selection_ignored_for_text_documents(editor, monkeypatch):
    calls = []
    monkeypatch.setattr(type(editor), "_area_menu", lambda self, *a: calls.append(a))
    editor._on_area_selected(0, pymupdf.Rect(0, 0, 500, 500), QPoint())
    assert not calls


# ------------------------------------------------------------------ Freie Bearbeitung
def test_free_edit_keeps_changes_when_discard_is_declined(editor, monkeypatch):
    window, s = editor, editor.session
    window.edit_btn.setChecked(True)
    assert "freie Bearbeitung aktiv" in window.right.title.text() and not window.right.text.isReadOnly()
    window.right.text.setPlainText("von Hand neu geschrieben")
    assert s.manual_text == "von Hand neu geschrieben" and s.dirty and window.windowTitle()

    asked = []
    monkeypatch.setattr(dialogs, "ask_yes_no", lambda *a, **k: asked.append(a[1:3]) and False)
    window.edit_btn.setChecked(False)                                     # „Nein“ → Bearbeitung bleibt offen
    assert asked == [("Freie Bearbeitung beenden", "Handänderungen am anonymisierten Text verwerfen?")]
    assert window.edit_btn.isChecked() and s.manual_text == "von Hand neu geschrieben"
    assert not window.right.text.isReadOnly()

    window.right.text.setPlainText(s.redacted().text)                     # unverändert → keine Rückfrage
    window.edit_btn.setChecked(False)
    assert len(asked) == 1 and s.manual_text is None and window.right.text.isReadOnly()
    assert window.right.title.text() == "<b>Bearbeitet (Vorschau)</b>" and window.right.title.toolTip() == ""


# ------------------------------------------------------------------ KI-Nachprüfung: Fehlerwege
def _ki_ready(window) -> None:
    window.settings.llm_enabled = True
    window.settings.llm_url = "https://llm.intern.example/v1"
    window.settings.llm_model = "test/modell"


def _ki_wait(window) -> None:
    t = time.time()
    while window._ki_session is not None and time.time() - t < 30:
        _wait(30)


def test_ki_review_not_configured_or_declined(editor, monkeypatch):
    window = editor
    d = Dialogs(monkeypatch)
    called = []
    monkeypatch.setattr(llm_review, "review", lambda *a, **k: called.append(1))
    window.start_ki_review()
    assert d.shown == [("information", "KI-Nachprüfung", d.shown[0][2])] and "nicht eingerichtet" in d.shown[0][2]

    _ki_ready(window)
    asked = []
    monkeypatch.setattr(dialogs, "ask_yes_no", lambda *a, **k: asked.append(a[2]) and False)
    window.start_ki_review()
    assert "llm.intern.example" in asked[0] and "einmal pro Programmstart" in asked[0]
    assert window._ki_session is None and not window._ki_confirmed and not called

    monkeypatch.setattr(type(window.runner), "busy", property(lambda self: True))
    window.start_ki_review()                                              # während einer Analyse: nichts
    assert len(asked) == 1 and not called


def test_ki_review_failure_is_reported_and_asks_only_once(editor, monkeypatch):
    window = editor
    _ki_ready(window)
    d = Dialogs(monkeypatch)
    asked = []
    monkeypatch.setattr(dialogs, "ask_yes_no", lambda *a, **k: asked.append(1) or True)

    def broken(doc, findings, settings, progress=None):
        progress(0, 3)
        progress(1, 3)
        raise llm_review.LlmError("Server nicht erreichbar")

    monkeypatch.setattr(llm_review, "review", broken)
    before = [(f.start, f.end, f.active) for f in window.session.findings]
    window.start_ki_review()
    assert window._ki_session is window.session and window.ki_dock.isVisible()
    assert window.status_label.text() == "KI-Nachprüfung läuft …"
    window.start_ki_review()                                              # läuft schon: kein zweiter Start
    _ki_wait(window)
    assert d.shown == [("warning", "KI-Nachprüfung fehlgeschlagen", "Server nicht erreichbar")]
    assert window.status_label.text() == "KI-Nachprüfung fehlgeschlagen."
    assert [(f.start, f.end, f.active) for f in window.session.findings] == before

    window.start_ki_review()                                              # zweiter Versuch: keine erneute Rückfrage
    _ki_wait(window)
    assert len(asked) == 1 and len(d.shown) == 2


def test_ki_result_for_a_document_closed_meanwhile_is_dropped(editor, tmp_path, monkeypatch):
    window = editor
    d = Dialogs(monkeypatch)
    old = window.session
    other = tmp_path / "anderes.txt"
    other.write_text("Herr Max Mustermann aus Musterstadt.\n", encoding="utf-8")
    _open(window, other)
    n = len(window.session.findings)
    window._ki_session = old
    window._on_ki_failed(old, "zu spät")
    assert window._ki_session is None and not d.shown
    window._ki_session = old
    window._on_ki_done(old, object())                                     # Ergebnis wird nicht einmal angesehen
    assert window._ki_session is None and len(window.session.findings) == n

    window._on_ki_progress(0, 1)                                          # eine Anfrage: kein Zähler
    window._on_ki_progress(1, 3)
    assert window.ki_panel.meta.text() == "Anfrage 2 von 3 läuft …"


def test_ki_suggestions_accept_all_and_discard(editor):
    window, s = editor, editor.session
    falke = TEXT.index("Projekt Falke")
    stadt = TEXT.index("Musterstadt")
    s.add_findings([Finding(falke, falke + 13, "CONTEXT", "Projekt Falke", source="ki", active=False),
                    Finding(stadt, stadt + 11, "CONTEXT", "Musterstadt", source="ki", active=False)])
    assert len(window._ki_ids(False)) == 2 and len(window._ki_ids(True)) == 2
    first_ki = window._ki_ids(False)[0]
    s.set_active([first_ki], True)
    assert window._ki_ids(True) == [i for i in window._ki_ids(False) if i != first_ki]
    window._ki_discard()                                                  # verwirft nur die nicht angenommenen
    assert window._ki_ids(False) == [first_ki] and len(s.findings) == 4
    window._ki_set_all(False)
    assert not s.get(first_ki).active
    window._ki_set_all(True)
    assert s.get(first_ki).active


# ------------------------------------------------------------------ Einstellungsdialog
def test_settings_dialog_returns_changed_values(window):
    orig = Settings(allow_list=["Alt"], deny_list=[], threshold=0.45)
    dlg = SettingsDialog(orig, window)
    dlg.threshold.setValue(0.6)
    dlg.mode.setCurrentIndex(dlg.mode.findData("schwaerzen"))
    dlg.analysis.setCurrentIndex(dlg.analysis.findData("schnell"))
    dlg.pdf_labels.setChecked(False)
    dlg.ocr.setChecked(False)
    dlg.compact.setChecked(not orig.compact_notices)
    dlg.model.setCurrentText("   ")
    dlg.allow.setPlainText("Musterfirma GmbH\n\n  musterfirma gmbh \nStadtwerke")
    dlg.deny.setPlainText("Projekt Falke\nProjekt Falke")
    item = dlg.entities.item(0)
    key = item.data(Qt.ItemDataRole.UserRole)
    was_checked = item.checkState() == Qt.CheckState.Checked
    item.setCheckState(Qt.CheckState.Unchecked if was_checked else Qt.CheckState.Checked)
    dlg.llm_enabled.setChecked(True)
    dlg.llm_url.setText("  https://llm.intern.example/v1 ")
    dlg.llm_model.setText(" test/modell ")
    dlg.llm_key.setText(" geheim ")
    dlg.llm_timeout.setValue(45)
    dlg.llm_chars.setValue(12000)
    new = dlg.result_settings()
    assert (new.threshold, new.replace_mode, new.analysis_mode) == (0.6, "schwaerzen", "schnell")
    assert (new.pdf_labels, new.ocr, new.compact_notices) == (False, False, not orig.compact_notices)
    assert new.spacy_model == "de_core_news_md"                           # leeres Feld → Voreinstellung
    assert new.allow_list == ["Musterfirma GmbH", "Stadtwerke"] and new.deny_list == ["Projekt Falke"]
    assert (key in new.entities) != was_checked
    assert (new.llm_enabled, new.llm_url, new.llm_model, new.llm_api_key) == (
        True, "https://llm.intern.example/v1", "test/modell", "geheim")
    assert (new.llm_timeout, new.llm_max_chars) == (45, 12000)
    assert orig.threshold == 0.45 and orig.allow_list == ["Alt"]         # die Vorlage bleibt unverändert
    assert settings_dialog.needs_reanalysis(orig, new)


def test_settings_dialog_locked_values_and_missing_model(window, monkeypatch):
    s = Settings(locked=["threshold", "llm_url", "gibt_es_nicht"], org_allow_list=["Stadt"], org_deny_list=["A", "B"])
    dlg = SettingsDialog(s, window)
    assert not dlg.threshold.isEnabled() and dlg.threshold.toolTip() == "Von der IT festgelegt"
    assert not dlg.llm_url.isEnabled() and dlg.llm_model.isEnabled()
    from PySide6.QtWidgets import QLabel

    assert any("1 Ausnahmen, 2 Sperrbegriffe" in lbl.text() for lbl in dlg.findChildren(QLabel))

    monkeypatch.setattr(settings_dialog, "list_ner_models", lambda: [])
    dlg = SettingsDialog(Settings(ner_model="alt"), window)
    assert not dlg.ner_model.isEnabled() and dlg.ner_model.currentText() == "(kein Modell installiert)"
    assert not dlg.analysis.model().item(1).isEnabled()                   # „Gründlich“ nicht wählbar
    assert dlg.result_settings().ner_model == ""


def test_settings_dialog_connection_test(window, monkeypatch):
    dlg = SettingsDialog(Settings(), window)
    dlg.llm_url.setText("")
    dlg._test_llm()
    assert dlg.test_result.text() == "Bitte Server-Adresse und Modell eintragen."

    seen = []

    def ok(settings):
        seen.append((settings.llm_enabled, settings.llm_url, settings.llm_model, settings.llm_max_chars))
        return "Verbindung in Ordnung."

    monkeypatch.setattr(llm_review, "test_connection", ok)
    dlg.llm_url.setText("https://llm.intern.example/v1")
    dlg.llm_model.setText("test/modell")
    dlg.llm_chars.setValue(8000)
    dlg._test_llm()
    assert seen == [(True, "https://llm.intern.example/v1", "test/modell", 8000)]     # auch wenn (noch) abgeschaltet
    assert dlg.test_result.text() == "✓ Verbindung in Ordnung." and dlg.test_btn.isEnabled()
    assert QApplication.overrideCursor() is None

    def broken(_settings):
        raise llm_review.LlmError("Zeitüberschreitung nach 30 s")

    monkeypatch.setattr(llm_review, "test_connection", broken)
    dlg._test_llm()
    assert dlg.test_result.text() == "✗ Zeitüberschreitung nach 30 s" and dlg.test_btn.isEnabled()
    assert QApplication.overrideCursor() is None
