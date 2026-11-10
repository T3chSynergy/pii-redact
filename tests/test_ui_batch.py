"""Oberfläche: Ordner-Bearbeitung – Ordner wählen und fortsetzen, Fehler einzelner Dateien, „Geprüft & weiter“,
Blättern, „Ungeprüfte Dateien exportieren“.

Dialoge werden durch vorgegebene Antworten ersetzt (Klasse ``Dialogs``); geprüft wird der Arbeitsstand, was im
Zielordner liegt und was das Fenster meldet."""

import json
import os
import time
from pathlib import Path

import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog
from test_ui import _wait
from test_ui_export import KAYA, Dialogs

import pii_redact.ui.window_batch as wb
from pii_redact.core.batch import CHECK_NOT_AUTO, WORKSPACE_NAME, Status, Workspace
from pii_redact.ui.batch_panel import NewBatchDialog

pytestmark = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")

BRIEF = "Frau Erika Beispiel wohnt in Musterstadt.\nTelefon 030 1234567\n"


def _idle(window) -> None:
    t = time.time()
    while window.runner.running and time.time() - t < 120:
        _wait(100)
    _wait(200)


@pytest.fixture()
def folder(tmp_path):
    """Quellordner mit drei kleinen Textdateien; Reihenfolge in der Arbeitsliste: a, b, c."""
    src = tmp_path / "akten"
    src.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (src / name).write_text(f"Vermerk {name[0].upper()}\n{BRIEF}", encoding="utf-8")
    return src


@pytest.fixture()
def batch(window, folder, tmp_path):
    """Fenster mit geöffnetem Ordner, alle Dateien analysiert (Modus „Schnell“ – hier geht es um den Ablauf)."""
    window.settings.analysis_mode = "schnell"
    window._activate_batch(Workspace.create(folder, tmp_path / "ziel"))
    _idle(window)
    assert window.batch_rel == "a.txt" and all(e.status == Status.TO_REVIEW for e in window.batch.entries.values())
    yield window
    window.close_batch()


def _fake_new_batch_dialog(monkeypatch, values=None):
    """Ersetzt den Start-Dialog: ``values`` = (Quelle, Ziel, Unterordner) oder None für „Abbrechen“."""
    seen = []

    class Fake:
        def __init__(self, _parent, source=""):
            seen.append(source)

        def exec(self):
            return values is not None

        def values(self):
            return values

    monkeypatch.setattr(wb, "NewBatchDialog", Fake)
    return seen


# ------------------------------------------------------------------ Start-Dialog
def test_new_batch_dialog_suggests_target_and_validates(window, folder, tmp_path, monkeypatch):
    dlg = NewBatchDialog(window)
    start = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert start.text() == "Starten" and not start.isEnabled() and "Quellordner wählen" in dlg.info.text()

    dlg.source.setText(str(tmp_path / "fehlt"))
    assert dlg.info.text() == "Quellordner nicht gefunden." and not start.isEnabled()

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    dlg._pick_source()                                    # „Durchsuchen …“ neben dem Quellordner
    assert Path(dlg.target.text()) == tmp_path / "akten_geschwaerzt"      # Vorschlag neben dem Quellordner
    assert "<b>3</b> Datei(en) gefunden" in dlg.info.text() and start.isEnabled()
    assert dlg.values() == (folder, tmp_path / "akten_geschwaerzt", True)

    dlg.target.setText(str(folder))
    assert dlg.info.text() == "Quell- und Zielordner dürfen nicht gleich sein." and not start.isEnabled()
    dlg.target.setText("")
    assert not start.isEnabled()                          # ohne Zielordner kein Start

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "eigenes_ziel"))
    dlg._pick_target()
    dlg.source.setText(str(folder))                       # ein selbst gewähltes Ziel bleibt bei neuer Quelle stehen
    assert Path(dlg.target.text()) == tmp_path / "eigenes_ziel" and start.isEnabled()

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dlg._pick_source()
    dlg._pick_target()                                    # Abbrechen im Ordnerdialog ändert nichts
    assert dlg.values() == (folder, tmp_path / "eigenes_ziel", True)

    (folder / "sub").mkdir()
    (folder / "sub" / "d.md").write_text("# Notiz\n", encoding="utf-8")
    dlg.recursive.setChecked(False)
    assert "<b>3</b>" in dlg.info.text() and dlg.values()[2] is False
    dlg.recursive.setChecked(True)
    assert "<b>4</b>" in dlg.info.text()

    Workspace.create(folder, tmp_path / "eigenes_ziel")
    dlg._update_info()
    assert "wird <b>fortgesetzt</b>" in dlg.info.text()

    leer = tmp_path / "leer"
    leer.mkdir()
    dlg.source.setText(str(leer))
    assert "<b>0</b> Datei(en)" in dlg.info.text() and not start.isEnabled()
    dlg._accept()
    assert dlg.result() == NewBatchDialog.DialogCode.Accepted


# ------------------------------------------------------------------ Ordner öffnen
def test_open_folder_cancel_start_and_resume(window, folder, tmp_path, monkeypatch):
    window.settings.analysis_mode = "schnell"
    d = Dialogs(monkeypatch)
    seen = _fake_new_batch_dialog(monkeypatch)            # Abbrechen
    window.open_folder_dialog(str(folder))
    assert seen == [str(folder)] and window.batch is None and not d.shown

    target = tmp_path / "ziel"
    _fake_new_batch_dialog(monkeypatch, (folder, target, True))
    window.open_folder_dialog()
    _idle(window)
    ws = window.batch
    assert ws is not None and sorted(ws.entries) == ["a.txt", "b.txt", "c.txt"] and window.batch_rel == "a.txt"
    assert window.batch_dock.isVisible() and window.review_bar.isVisible()
    assert window.settings.recent_batches[0] == str(ws.target)

    window.confirm_and_next()                             # a.txt erledigt
    window.close_batch()
    assert window.batch is None and not window.batch_dock.isVisible() and window.session is None
    assert "Stand ist gespeichert" in window.status_label.text()

    window.open_folder_dialog()                           # gleicher Ziel- und Quellordner → wird fortgesetzt
    _idle(window)
    assert window.batch.entries["a.txt"].status == Status.REVIEWED and window.batch_rel == "b.txt"
    window.close_batch()
    assert not d.shown


def test_open_folder_refuses_foreign_workspace_and_reports_errors(window, folder, tmp_path, monkeypatch):
    other = tmp_path / "andere_akten"
    other.mkdir()
    (other / "x.txt").write_text(BRIEF, encoding="utf-8")
    target = tmp_path / "ziel"
    Workspace.create(other, target)
    d = Dialogs(monkeypatch)
    _fake_new_batch_dialog(monkeypatch, (folder, target, True))
    window.open_folder_dialog()
    kind, title, text = d.shown[0]
    assert (kind, title) == ("warning", "Anderer Arbeitsstand") and str(other.resolve()) in text
    assert window.batch is None

    _fake_new_batch_dialog(monkeypatch, (tmp_path / "fehlt", tmp_path / "ziel2", True))
    window.open_folder_dialog()
    assert d.shown[1][:2] == ("warning", "Ordner kann nicht bearbeitet werden") and "fehlt" in d.shown[1][2]
    assert window.batch is None and not (tmp_path / "ziel2").exists()

    monkeypatch.setattr(type(window.runner), "busy", property(lambda self: True))
    window.open_folder_dialog()
    assert d.shown[2][:2] == ("information", "Bitte warten") and len(d.shown) == 3


def test_resume_folder_dialog_missing_and_broken_workspace(window, folder, tmp_path, monkeypatch):
    window.settings.analysis_mode = "schnell"
    d = Dialogs(monkeypatch)
    resumed = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    monkeypatch.setattr(type(window), "resume_folder", lambda self, p: resumed.append(p))
    window.resume_folder_dialog()                         # Abbrechen
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "ziel"))
    window.resume_folder_dialog()
    assert resumed == [tmp_path / "ziel"]
    monkeypatch.undo()
    d = Dialogs(monkeypatch)

    empty = tmp_path / "ohne_stand"
    empty.mkdir()
    window.settings.recent_batches = [str(empty), "anderswo"]
    window.resume_folder(empty)
    assert d.shown[0][:2] == ("information", "Kein Arbeitsstand") and str(empty) in d.shown[0][2]
    assert window.settings.recent_batches == ["anderswo"]         # aus „Zuletzt bearbeitet“ entfernt

    target = tmp_path / "ziel"
    Workspace.create(folder, target)
    good = (target / WORKSPACE_NAME).read_text(encoding="utf-8")
    (target / WORKSPACE_NAME).write_text(good[: len(good) // 2], encoding="utf-8")      # abgeschnittene Datei
    window.resume_folder(target)
    assert d.shown[1][:2] == ("warning", "Arbeitsstand kann nicht geladen werden") and window.batch is None

    data = json.loads(good)
    data["version"] = 999
    (target / WORKSPACE_NAME).write_text(json.dumps(data), encoding="utf-8")
    window.resume_folder(target)
    assert "neueren Programmversion" in d.shown[2][2] and window.batch is None

    (target / WORKSPACE_NAME).write_text(good, encoding="utf-8")
    monkeypatch.setattr(Workspace, "sync_with_disk", lambda self: (_ for _ in ()).throw(OSError("Laufwerk getrennt")))
    window.resume_folder(target)
    assert d.shown[3] == ("warning", "Ordner kann nicht bearbeitet werden", "Laufwerk getrennt")
    assert window.batch is None and QApplication.overrideCursor() is None


def test_missing_and_unreadable_files_are_marked_not_fatal(window, folder, tmp_path, monkeypatch):
    """Eine Datei, die im Quellordner fehlt oder sich nicht öffnen lässt, hält die übrigen nicht auf."""
    window.settings.analysis_mode = "schnell"
    target = tmp_path / "ziel"
    Workspace.create(folder, target)
    (folder / "a.txt").unlink()                                   # nach dem Anlegen gelöscht
    (folder / "b.txt").write_bytes(b"")                           # leer
    (folder / "d.pdf").write_bytes(b"%PDF-1.4 kaputt")            # kein gültiges PDF
    d = Dialogs(monkeypatch)
    window.resume_folder(target)
    _idle(window)
    ws = window.batch
    assert ws.entries["a.txt"].status == Status.MISSING
    assert ws.entries["d.pdf"].status == Status.ERROR and "PDF" in ws.entries["d.pdf"].error
    assert ws.entries["c.txt"].status == Status.TO_REVIEW

    window._open_batch_file("a.txt")
    assert window.status_label.text() == "a.txt: Datei fehlt im Quellordner." and window.batch_rel != "a.txt"
    window._open_batch_file("d.pdf")
    assert window.status_label.text().startswith("d.pdf: ") and window.batch_rel != "d.pdf"
    window._open_batch_file("gibt-es-nicht.txt")                  # unbekannter Name: nichts passiert
    window._open_batch_file("c.txt")
    _idle(window)
    assert window.batch_rel == "c.txt" and window.session.doc.path.name == "c.txt"
    # die Leiste „Geprüft & weiter“ ist nur bei einer prüfbaren Datei aktiv
    assert window.review_bar.btn_ok.isEnabled()
    assert not d.shown
    window.close_batch()


# ------------------------------------------------------------------ Analyse im Hintergrund
def test_failed_analysis_marks_file_and_missing_model_stops_the_batch(batch, monkeypatch):
    window, ws = batch, batch.batch
    d = Dialogs(monkeypatch)
    window._on_file_failed("b.txt", window.batch_id + 1, "veraltet")      # Meldung eines früheren Ordners
    window._on_file_failed("unbekannt.txt", window.batch_id, "egal")
    assert ws.entries["b.txt"].status == Status.TO_REVIEW

    window._awaiting_rel = "b.txt"
    window.progress.show()
    window._on_file_failed("b.txt", window.batch_id, "Datei nicht lesbar")
    assert ws.entries["b.txt"].status == Status.ERROR and ws.entries["b.txt"].error == "Datei nicht lesbar"
    assert window._awaiting_rel is None and not window.progress.isVisible()
    assert window.status_label.text() == "b.txt: Analyse fehlgeschlagen." and not d.shown

    cancelled = []
    monkeypatch.setattr(type(window.runner), "cancel_batch", lambda self, bid: cancelled.append(bid))
    window._on_file_failed("c.txt", window.batch_id, "Das Sprachmodell „de_core_news_md“ fehlt.")
    window._on_file_failed("a.txt", window.batch_id, "Das Transformer-Modell fehlt.")
    # eine Meldung für den ganzen Ordner, nicht eine je Datei; die Warteschlange wird geleert
    assert [m[:2] for m in d.shown] == [("critical", "Analyse nicht möglich")] and cancelled == [window.batch_id]
    assert ws.entries["a.txt"].status == Status.ERROR


def test_late_analysis_result_updates_open_file_and_ignores_strangers(batch):
    window, ws = batch, batch.batch
    result = {"findings": [], "sha256": "x", "size": 1, "mtime_ns": 1, "text_sha": "y", "warnings": [],
              "mode": "schnell"}
    window._on_file_finished("a.txt", window.batch_id + 1, result)        # anderer Ordner
    window._on_file_finished("fremd.txt", window.batch_id, result)        # unbekannte Datei
    assert window.session.counts()[0] > 0 and ws.entries["a.txt"].sha256 != "x"

    # Ergebnis für eine andere als die offene Datei: Arbeitsstand ja, Ansicht nein
    window._on_file_finished("b.txt", window.batch_id, result)
    assert ws.entries["b.txt"].sha256 == "x" and ws.entries["b.txt"].findings == []
    assert window.session.counts()[0] > 0

    # Ergebnis für die offene Datei, auf das die Ansicht wartet
    window._awaiting_rel = "a.txt"
    window.progress.show()
    window.session.dirty = True
    window._on_file_finished("a.txt", window.batch_id, result)
    assert window.session.counts()[0] == 0 and not window.session.dirty and window._awaiting_rel is None
    assert not window.progress.isVisible() and window.status_label.text() == "Analyse fertig: 0 Funde."


# ------------------------------------------------------------------ Geprüft & weiter
def test_confirm_and_next_guards(window, batch, monkeypatch):
    d = Dialogs(monkeypatch)
    window._awaiting_rel = "a.txt"
    window.confirm_and_next()
    assert window.status_label.text() == "Die Analyse dieser Datei läuft noch."
    window._awaiting_rel = None
    monkeypatch.setattr(type(window.runner), "busy", property(lambda self: True))
    window.confirm_and_next()
    assert window.batch.entries["a.txt"].status == Status.TO_REVIEW and not d.shown and not d.boxes

    monkeypatch.undo()
    d = Dialogs(monkeypatch)
    window.session.doc.ocr_pages[:] = [0]                 # Texterkennung: Voreinstellung „Zurück zur Prüfung“
    window.confirm_and_next()
    assert [b["title"] for b in d.boxes] == ["Texterkennung – geprüft?"]
    assert window.batch.entries["a.txt"].status == Status.TO_REVIEW
    assert not (window.batch.target / "a_anonymisiert.txt").exists()


def test_confirm_and_next_without_folder_does_nothing(window, monkeypatch):
    d = Dialogs(monkeypatch)
    window.confirm_and_next()
    window.export_unreviewed()
    window._step_file(1)
    window._requeue_batch()
    window._refresh_batch()
    window.close_batch()
    window._save_batch()
    assert not d.shown and not d.boxes and window.batch is None


def test_confirm_and_next_export_failure_keeps_file_open(batch, monkeypatch):
    window, ws = batch, batch.batch
    d = Dialogs(monkeypatch)

    def broken(*_a, **_k):
        raise OSError("Zielordner nicht erreichbar")

    monkeypatch.setattr(wb, "export_document", broken)
    window.session.dirty = True
    window.confirm_and_next()
    assert d.shown == [("critical", "Export fehlgeschlagen", "a.txt:\nZielordner nicht erreichbar")]
    assert ws.entries["a.txt"].status == Status.TO_REVIEW and window.batch_rel == "a.txt"
    assert window.session.dirty and QApplication.overrideCursor() is None


def test_confirm_and_next_reports_leftovers_and_end_of_folder(batch, monkeypatch):
    window, ws = batch, batch.batch
    d = Dialogs(monkeypatch)
    real = wb.export_document
    leftovers = [f"Rest {i} – an der geschwärzten Stelle noch vorhanden" for i in range(1, 23)]

    def with_leftovers(doc, findings, settings, out):
        real(doc, findings, settings, out)
        return list(leftovers)

    monkeypatch.setattr(wb, "export_document", with_leftovers)
    window.confirm_and_next()
    kind, title, text = d.shown[0]
    assert (kind, title) == ("warning", "Prüfung: Reste gefunden") and text.startswith("a.txt wurde exportiert")
    assert text.count("• Rest") == 20 and "steht im Protokoll" in text
    assert ws.entries["a.txt"].status == Status.REVIEWED
    assert ws.entries["a.txt"].export_note == "Kontrolle: 22 Rest(e) im Ergebnis gefunden"
    assert window.batch_rel == "b.txt" and window.status_label.text() == "a.txt geprüft und exportiert."

    monkeypatch.setattr(wb, "export_document", real)
    ws.set_error("c.txt", "kaputt")
    window.confirm_and_next()                             # b.txt war die letzte offene Datei
    kind, title, text = d.shown[1]
    assert (kind, title) == ("information", "Ordner bearbeitet") and len(d.shown) == 2
    assert "2 geprüft, 0 automatisch, 1 mit Fehler" in text and str(ws.target) in text
    assert window.batch_rel == "b.txt"
    # Anzeige in der Leiste: wer geprüft hat
    assert "geprüft von" in window.review_bar.state.text()


def test_confirm_and_next_back_to_review_keeps_suggestions_in_workspace(window, tmp_path, monkeypatch):
    window.settings.analysis_mode = "schnell"
    src = tmp_path / "akten"
    src.mkdir()
    (src / "antrag.txt").write_text(KAYA, encoding="utf-8")
    window._activate_batch(Workspace.create(src, tmp_path / "ziel"))
    _idle(window)
    s = window.session
    pos = s.doc.text.index("Kaya")
    from pii_redact.core import Finding

    s.findings[:] = [Finding(pos, pos + 4, "PERSON", "Kaya")]
    d = Dialogs(monkeypatch)                              # Voreinstellung der Rückfrage: zurück zur Prüfung
    window.confirm_and_next()
    assert [b["title"] for b in d.boxes] == ["Kontrolle vor dem Speichern"]
    ws = window.batch
    assert ws.entries["antrag.txt"].status == Status.TO_REVIEW
    assert not (tmp_path / "ziel" / "antrag_anonymisiert.txt").exists()
    window.close_batch()
    stored = Workspace.load(tmp_path / "ziel").entries["antrag.txt"].findings
    assert [(f["src"], f["a"]) for f in stored] == [("auto", True), ("kontrolle", False)]


# ------------------------------------------------------------------ Blättern
def test_step_between_files_stops_at_both_ends(batch):
    window = batch
    window._step_file(-1)
    assert window.batch_rel == "a.txt" and not window.review_bar.btn_prev.isEnabled()
    window._step_file(1)
    window._step_file(1)
    assert window.batch_rel == "c.txt" and not window.review_bar.btn_next.isEnabled()
    assert window.review_bar.btn_prev.isEnabled() and "3" in window.review_bar.label.text()
    window._step_file(1)
    assert window.batch_rel == "c.txt"
    window._step_file(-2)
    assert window.batch_rel == "a.txt"


def test_changes_survive_switching_files(batch):
    """Abgewählte Funde bleiben erhalten, wenn man zu einer anderen Datei und zurück blättert."""
    window = batch
    f = window.session.findings[0]
    window.session.set_active([f.id], False)
    window._step_file(1)
    window._step_file(-1)
    again = next(x for x in window.session.findings if (x.start, x.end) == (f.start, f.end))
    assert again.active is False


# ------------------------------------------------------------------ Ungeprüfte exportieren
def test_export_unreviewed_declined_or_nothing_to_do(batch, monkeypatch):
    window, ws = batch, batch.batch
    d = Dialogs(monkeypatch)                              # Voreinstellung der Rückfrage: Abbrechen
    window.export_unreviewed()
    assert d.boxes and all(e.status == Status.TO_REVIEW for e in ws.entries.values())
    assert not any(ws.target.glob("*_anonymisiert.txt"))

    for e in ws.entries.values():
        e.ocr_pages = [0]                                 # nur noch Dateien mit Texterkennung offen
    d.boxes.clear()
    window.export_unreviewed()
    assert d.shown == [("information", "Ungeprüfte Dateien exportieren", d.shown[0][2])]
    assert d.shown[0][2].startswith("3 Datei(en) enthalten per Texterkennung") and not d.boxes

    for e in ws.entries.values():
        e.ocr_pages = []
        e.status = Status.REVIEWED
    window.export_unreviewed()                            # nichts offen: keine Meldung, keine Rückfrage
    assert len(d.shown) == 1 and not d.boxes


def test_export_unreviewed_counts_failures_ocr_and_held_files(batch, folder, monkeypatch):
    window, ws = batch, batch.batch
    (folder / "d.txt").write_text(KAYA, encoding="utf-8")
    window.close_batch()
    window.resume_folder(ws.target)
    _idle(window)
    ws = window.batch
    ws.entries["c.txt"].ocr_pages = [0]                   # c: Texterkennung → bleibt stehen
    pos = KAYA.index("Kaya")
    from pii_redact.core import Finding

    ws.update_findings("d.txt", [Finding(pos, pos + 4, "PERSON", "Kaya")])        # d: Kontrolle hält zurück
    Dialogs(monkeypatch, click="Ohne Prüfung exportieren")
    real = wb.export_document

    def fail_for_b(doc, findings, settings, out):
        if doc.path.name == "b.txt":
            raise OSError("kein Platz")
        return real(doc, findings, settings, out)

    monkeypatch.setattr(wb, "export_document", fail_for_b)
    window.export_unreviewed()
    assert ws.entries["a.txt"].status == Status.AUTO and (ws.target / "a_anonymisiert.txt").is_file()
    assert ws.entries["b.txt"].status == Status.ERROR
    assert ws.entries["b.txt"].error == "Export fehlgeschlagen: kein Platz"
    assert ws.entries["c.txt"].status == Status.TO_REVIEW and ws.entries["c.txt"].note == wb.OCR_NOT_AUTO
    assert ws.entries["d.txt"].status == Status.TO_REVIEW and ws.entries["d.txt"].note == CHECK_NOT_AUTO
    assert sorted(p.name for p in ws.target.glob("*_anonymisiert.txt")) == ["a_anonymisiert.txt"]
    text = window.status_label.text()
    assert text.startswith("1 Datei(en) automatisch exportiert") and ", 1 Fehler" in text
    assert "1 mit Texterkennung bleiben zur Prüfung stehen" in text and "1 hält die Kontrolle" in text
    # der Stand ist gespeichert
    assert Workspace.load(ws.target).entries["a.txt"].status == Status.AUTO


def test_export_unreviewed_reanalyses_changed_file_first(batch, folder, monkeypatch):
    """Hat sich eine Datei seit der Analyse geändert, wird sie neu analysiert und erst danach exportiert."""
    window, ws = batch, batch.batch
    (folder / "c.txt").write_text("Neuer Inhalt: Herr Max Mustermann aus Musterstadt.\n", encoding="utf-8")
    d = Dialogs(monkeypatch, click="Ohne Prüfung exportieren")
    window.export_unreviewed()
    assert "folgen nach der Analyse" in window.status_label.text()
    _idle(window)
    assert ws.entries["c.txt"].status == Status.AUTO
    assert window.status_label.text() == "Automatischer Export abgeschlossen."
    out = (ws.target / "c_anonymisiert.txt").read_text(encoding="utf-8")
    assert out.startswith("Neuer Inhalt") and "Mustermann" not in out
    assert all(e.status == Status.AUTO for e in ws.entries.values()) and not d.shown


def test_auto_export_after_analysis_skips_ocr_and_records_export_errors(batch, monkeypatch):
    window, ws = batch, batch.batch
    result = {"findings": [], "sha256": ws.entries["b.txt"].sha256, "size": ws.entries["b.txt"].size,
              "mtime_ns": ws.entries["b.txt"].mtime_ns, "text_sha": ws.entries["b.txt"].text_sha, "ocr_pages": [1],
              "warnings": [], "mode": "schnell"}
    window._auto_export = True
    window._on_file_finished("b.txt", window.batch_id, result)
    assert ws.entries["b.txt"].status == Status.TO_REVIEW and ws.entries["b.txt"].note == wb.OCR_NOT_AUTO

    def broken(*_a, **_k):
        raise RuntimeError("Schreibfehler")

    monkeypatch.setattr(wb, "export_document", broken)
    result = dict(result, sha256=ws.entries["c.txt"].sha256, size=ws.entries["c.txt"].size,
                  mtime_ns=ws.entries["c.txt"].mtime_ns, text_sha=ws.entries["c.txt"].text_sha, ocr_pages=[])
    window._auto_export = True
    window._on_file_finished("c.txt", window.batch_id, result)
    assert ws.entries["c.txt"].status == Status.ERROR
    assert ws.entries["c.txt"].error == "Export fehlgeschlagen: Schreibfehler"
    assert window._auto_export is False                   # Warteschlange leer → automatischer Export beendet


# ------------------------------------------------------------------ Speichern, Zuletzt bearbeitet
def test_workspace_save_error_is_shown_in_status_bar(batch, monkeypatch):
    window = batch
    monkeypatch.setattr(Workspace, "save", lambda self: (_ for _ in ()).throw(OSError("schreibgeschützt")))
    window._save_batch()
    assert window.status_label.text() == "Arbeitsstand konnte nicht gespeichert werden: schreibgeschützt"
    monkeypatch.undo()


def test_recent_folders_menu(window, tmp_path, monkeypatch):
    window.settings.recent_batches = []
    window._fill_recent_menu()
    acts = window.recent_menu.actions()
    assert [a.text() for a in acts] == ["(noch keine)"] and not acts[0].isEnabled()

    for i in range(10):
        window._remember_recent(tmp_path / f"ziel{i}")
    window._remember_recent(tmp_path / "ziel5")           # erneut geöffnet → rückt nach vorn, kein Doppel
    names = [Path(p).name for p in window.settings.recent_batches]
    assert names == ["ziel5", "ziel9", "ziel8", "ziel7", "ziel6", "ziel4", "ziel3", "ziel2"]

    resumed = []
    monkeypatch.setattr(type(window), "resume_folder", lambda self, p: resumed.append(p))
    window._fill_recent_menu()
    acts = window.recent_menu.actions()
    assert len(acts) == 8
    acts[1].trigger()
    assert resumed == [tmp_path / "ziel9"]
