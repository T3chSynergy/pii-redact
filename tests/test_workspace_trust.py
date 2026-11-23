"""Arbeitsstand im Zielordner: nichts Erratbares, Quellordner erst nach Bestätigung, gespeicherte Angaben nicht
blind übernehmen.

Der Zielordner wird oft weitergegeben oder ist eine Ablage, die andere lesen; wer dort schreiben darf, kann den
Arbeitsstand ändern. Deshalb:

* keine Prüfsumme, aus der sich geschwärzte Werte erraten lassen (Protokoll ohne „SHA-256 Original“, im
  Arbeitsstand nur das Textlayout und Datei-Prüfsummen nur bei PDFs),
* der gespeicherte Quellordner wird erst als Text verglichen bzw. bestätigt, bevor auf ihn zugegriffen wird,
* ob Texterkennung im Spiel ist, entscheidet das geladene Dokument, und Funde ohne Prüfsumme gelten nicht.
"""

import csv
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
import spacy

from pii_redact import cli
from pii_redact.core import Finding, Settings
from pii_redact.core.batch import (
    OLD_WORKSPACE_NAME,
    PROTOCOL_NAME,
    WORKSPACE_NAME,
    Status,
    Workspace,
    export_document,
    is_generated,
    layout_sha,
    text_sha,
)
from pii_redact.core.loaders import load_document

needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")
NOTE = "Termin mit Frau Anna Kaya am 17.11.1984 in Hamm.\n"


@pytest.fixture()
def notes(tmp_path):
    src = tmp_path / "notizen"
    src.mkdir()
    (src / "termin.txt").write_text(NOTE, encoding="utf-8")
    return src


def _analyzed(src: Path, target: Path) -> Workspace:
    """Arbeitsstand mit drei Funden (Name, Datum, Ort) – ohne Sprachmodell."""
    ws = Workspace.create(src, target)
    doc = load_document(ws.abs("termin.txt"))
    spans = [(NOTE.index(t), NOTE.index(t) + len(t), k) for t, k in
             (("Anna Kaya", "PERSON"), ("17.11.1984", "DATE_TIME"), ("Hamm", "LOCATION"))]
    st = ws.abs("termin.txt").stat()
    ws.set_analysis("termin.txt", sha256="", size=st.st_size, mtime_ns=st.st_mtime_ns, warnings=[], mode="schnell",
                    findings=[Finding(s, e, k, NOTE[s:e]) for s, e, k in spans], layout_sha=layout_sha(doc.text))
    ws.save()
    return ws


# ------------------------------------------------------------------ nichts zu erraten
def test_layout_hash_detects_shifts_but_not_content():
    assert layout_sha("Frau Anna Kaya") == layout_sha("Herr Otto Mohn")       # gleiches Layout
    assert layout_sha("Frau Anna Kaya") != layout_sha("Frau Anna Kayan")      # Länge verschoben
    assert layout_sha("Anna\nKaya") != layout_sha("Anna Kaya")                # Zeilen verschoben


def test_target_folder_holds_nothing_to_guess(notes, tmp_path):
    """Angreifer mit dem Zielordner rät Namen, Datum und Ort und vergleicht mit jeder Prüfsumme darin."""
    target = tmp_path / "ziel"
    _analyzed(notes, target)
    guessable = {hashlib.sha256(NOTE.encode()).hexdigest(), text_sha(NOTE),
                 hashlib.sha256((notes / "termin.txt").read_bytes()).hexdigest()}
    for f in target.rglob("*"):
        content = f.read_text(encoding="utf-8-sig")
        assert not any(h in content for h in guessable), f.name
        assert "Kaya" not in content and "1984" not in content
    with open(target / PROTOCOL_NAME, encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh, delimiter=";"))
    assert not any("SHA" in col for col in header)
    data = json.loads((target / WORKSPACE_NAME).read_text(encoding="utf-8"))
    assert "nicht weitergeben" in data["hinweis"] and data["version"] == 2
    assert data["files"]["termin.txt"]["sha256"] == "" and data["files"]["termin.txt"]["text_sha"] == ""


def test_workspace_name_says_do_not_pass_on(notes, tmp_path):
    assert WORKSPACE_NAME == "pii-redact-arbeitsstand (nicht weitergeben).json"
    assert is_generated(WORKSPACE_NAME) and is_generated(OLD_WORKSPACE_NAME)


def test_old_workspace_is_read_and_converted(notes, tmp_path):
    """Arbeitsstand von 0.8.7: alter Dateiname, Prüfsumme des ganzen Texts, Datei-Prüfsumme der Textdatei."""
    target = tmp_path / "ziel"
    _analyzed(notes, target)
    data = json.loads((target / WORKSPACE_NAME).read_text(encoding="utf-8"))
    entry = data["files"]["termin.txt"]
    entry.update(layout_sha="", text_sha=text_sha(NOTE), sha256=hashlib.sha256(NOTE.encode()).hexdigest())
    data["version"] = 1
    (target / WORKSPACE_NAME).unlink()
    (target / OLD_WORKSPACE_NAME).write_text(json.dumps(data), encoding="utf-8")

    assert Workspace.exists_in(target)
    ws = Workspace.load(target)
    assert ws.entries["termin.txt"].sha256 == ""                   # beim Laden verworfen
    restored = ws.restore_findings("termin.txt", load_document(ws.abs("termin.txt")))
    assert [f.text for f in restored] == ["Anna Kaya", "17.11.1984", "Hamm"]
    e = ws.entries["termin.txt"]
    assert e.layout_sha == layout_sha(NOTE) and e.text_sha == ""   # umgestellt
    ws.save()
    assert not (target / OLD_WORKSPACE_NAME).exists() and (target / WORKSPACE_NAME).is_file()


def test_findings_without_any_hash_are_not_taken(notes, tmp_path):
    ws = _analyzed(notes, tmp_path / "ziel")
    ws.entries["termin.txt"].layout_sha = ""                       # z. B. von Hand gelöscht
    assert ws.restore_findings("termin.txt", load_document(ws.abs("termin.txt"))) is None
    assert ws.entries["termin.txt"].status == Status.WAITING and ws.entries["termin.txt"].findings is None


# ------------------------------------------------------------------ Quellordner
def _redirect(target: Path, source: str, recursive: bool = True) -> None:
    path = target / WORKSPACE_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["source"], data["recursive"] = source, recursive
    path.write_text(json.dumps(data), encoding="utf-8")


def test_stored_source_is_read_without_touching_it(notes, tmp_path, monkeypatch):
    target = tmp_path / "ziel"
    _analyzed(notes, target)
    _redirect(target, r"\\server.example\freigabe\personal", recursive=False)

    def no_access(*_a, **_k):
        raise AssertionError("Pfad aus dem Arbeitsstand angefasst")

    monkeypatch.setattr(Path, "resolve", no_access)
    monkeypatch.setattr(Path, "is_dir", lambda self: self.name == "ziel" or no_access())
    assert Workspace.stored_source(target) == (r"\\server.example\freigabe\personal", False)


def test_cli_refuses_other_source_before_touching_it(notes, tmp_path, monkeypatch, capsys):
    target = tmp_path / "ziel"
    _analyzed(notes, target)
    _redirect(target, r"\\server.example\freigabe\personal")
    monkeypatch.setattr(Workspace, "load", classmethod(lambda cls, t: pytest.fail("geladen trotz fremder Quelle")))
    assert cli.open_workspace(notes, target, recursive=True) is None
    assert r"enthält einen Arbeitsstand für \\server.example\freigabe\personal" in capsys.readouterr().err


def test_cli_does_not_export_when_document_needs_ocr(notes, tmp_path, monkeypatch):
    """Der Arbeitsstand sagt „keine Texterkennung“ (verändert) – das geladene Dokument sagt etwas anderes."""
    ws = _analyzed(notes, tmp_path / "ziel")
    e = ws.entries["termin.txt"]
    e.findings, e.ocr_pages = [], []

    def with_ocr(path, ocr=True):
        doc = load_document(path, ocr=ocr)
        doc.ocr_pages = [0]
        return doc

    monkeypatch.setattr(cli, "load_document", with_ocr)
    assert cli.export_entry(ws, e, Settings(), analyzer=None) == cli.OCR_SKIPPED
    assert e.ocr_pages == [1] and "Texterkennung" in e.note
    assert not ws.out("termin.txt").exists()


# ------------------------------------------------------------------ Oberfläche
@needs_model
def test_window_asks_before_touching_a_redirected_source(window, notes, tmp_path, monkeypatch):
    from test_ui_export import Dialogs

    window.settings.analysis_mode = "schnell"
    target = tmp_path / "ziel"
    window._activate_batch(Workspace.create(notes, target))        # selbst angelegt → gemerkt
    window.close_batch()
    other = tmp_path / "fremd"
    other.mkdir()
    (other / "personal.txt").write_text("Frau Erika Beispiel\n", encoding="utf-8")
    _redirect(target, str(other.resolve()))
    before = (target / WORKSPACE_NAME).read_text(encoding="utf-8")

    d = Dialogs(monkeypatch, click="Abbrechen")
    window.resume_folder(target)                                    # z. B. über „Zuletzt bearbeitet“
    assert [b["title"] for b in d.boxes] == ["Ordner-Arbeit fortsetzen"] and str(other.resolve()) in d.boxes[0]["text"]
    assert window.batch is None and (target / WORKSPACE_NAME).read_text(encoding="utf-8") == before
    assert not (target / "personal_anonymisiert.txt").exists()

    d = Dialogs(monkeypatch, click="Fortsetzen")
    window.resume_folder(target)
    assert window.batch is not None and window.batch.source == other.resolve()
    window.close_batch()
    d = Dialogs(monkeypatch, click="Abbrechen")
    window.resume_folder(target)                                    # jetzt bestätigt: keine Rückfrage mehr
    assert not d.boxes and window.batch is not None
    window.close_batch()


@needs_model
def test_window_unreviewed_export_checks_ocr_of_loaded_document(window, notes, tmp_path, monkeypatch):
    from pii_redact.ui import window_batch

    window.settings.analysis_mode = "schnell"
    ws = _analyzed(notes, tmp_path / "ziel")
    window.batch = ws
    e = ws.entries["termin.txt"]
    real_load = window._load

    def with_ocr(path):
        doc = real_load(path)
        doc.ocr_pages = [0]
        return doc

    monkeypatch.setattr(window, "_load", with_ocr)
    window.batch_rel = None
    assert window._export_unreviewed_file(e) is False
    assert e.note == window_batch.OCR_NOT_AUTO and not ws.out("termin.txt").exists()
    window.batch = None


def test_confirmed_sources_survive_saving(tmp_path):
    s = Settings(confirmed_sources={os.path.normcase(str(tmp_path)): {"source": "/quelle", "recursive": True}})
    s.save(tmp_path / "settings.json")
    assert Settings.load(tmp_path / "settings.json").confirmed_sources == s.confirmed_sources


# ------------------------------------------------------------------ Ergebnisse als ZIP
def _export(ws: Workspace, rel: str) -> Path:
    doc = load_document(ws.abs(rel))
    findings = ws.restore_findings(rel, doc)
    out = ws.out(rel)
    export_document(doc, findings, Settings(), out)
    ws.mark_done(rel, reviewed=True, exported_as=out, leftovers=[], findings=findings)
    ws.save()
    return out


def test_zip_holds_only_exported_results(notes, tmp_path):
    (notes / "unter").mkdir()
    (notes / "unter" / "zweit.txt").write_text(NOTE, encoding="utf-8")
    target = tmp_path / "ziel"
    ws = _analyzed(notes, target)                                  # „unter/zweit.txt“ bleibt offen
    _export(ws, "termin.txt")
    (target / "fremd_geschwaerzt.pdf").write_bytes(b"untergeschoben")
    (tmp_path / "geheim_anonymisiert.txt").write_text("außerhalb", encoding="utf-8")
    assert ws.result_files() == (["termin_anonymisiert.txt"], 1)

    dest = tmp_path / "weitergeben.zip"
    assert ws.pack_results(dest) == ["termin_anonymisiert.txt"]
    with zipfile.ZipFile(dest) as z:
        assert z.namelist() == ["termin_anonymisiert.txt"]
        assert "Kaya" not in z.read("termin_anonymisiert.txt").decode("utf-8")
    ws.pack_results(dest, with_protocol=True)
    with zipfile.ZipFile(dest) as z:
        assert z.namelist() == ["termin_anonymisiert.txt", PROTOCOL_NAME]
    with pytest.raises(ValueError, match="außerhalb des Zielordners"):
        ws.pack_results(target / "im_ziel.zip")
    assert not (target / "im_ziel.zip").exists()


@pytest.mark.parametrize("pointer", ["../geheim_anonymisiert.txt", WORKSPACE_NAME, PROTOCOL_NAME,
                                     "fehlt_anonymisiert.txt"])
def test_zip_ignores_manipulated_export_paths(notes, tmp_path, pointer):
    """Wer den Arbeitsstand ändert, kann „Exportiert als“ auf beliebige Dateien zeigen lassen – ins ZIP kommt nur,
    was wie ein Ergebnis heißt und im Zielordner liegt."""
    target = tmp_path / "ziel"
    ws = _analyzed(notes, target)
    _export(ws, "termin.txt")
    (tmp_path / "geheim_anonymisiert.txt").write_text("außerhalb", encoding="utf-8")
    ws.entries["termin.txt"].exported_as = pointer
    assert ws.result_files()[0] == []


def test_zip_ignores_link_out_of_target(notes, tmp_path):
    target = tmp_path / "ziel"
    ws = _analyzed(notes, target)
    _export(ws, "termin.txt")
    secret = tmp_path / "geheim.txt"
    secret.write_text("außerhalb", encoding="utf-8")
    link = target / "verweis_anonymisiert.txt"
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("Verknüpfungen nicht erlaubt (Windows ohne Entwicklermodus)")
    ws.entries["termin.txt"].exported_as = link.name
    assert ws.result_files()[0] == []


@needs_model
def test_window_offers_and_saves_zip(window, notes, tmp_path, monkeypatch):
    from test_ui_export import Dialogs

    window.settings.analysis_mode = "schnell"
    target = tmp_path / "ziel"
    window._activate_batch(_analyzed(notes, target))
    dest = tmp_path / "weitergeben.zip"
    d = Dialogs(monkeypatch, target=dest)
    window.save_results_zip()                                      # noch nichts fertig
    assert d.shown == [("information", "Ergebnisse als ZIP speichern",
                        "Es gibt noch keine fertigen Ergebnisse – erst Dateien prüfen und exportieren.")]
    window.confirm_and_next()                                      # letzte Datei → Abschluss mit Angebot
    assert d.boxes[-1]["title"] == "Ordner bearbeitet" and "Als ZIP speichern …" in d.boxes[-1]["buttons"]
    assert not dest.exists()                                       # „OK“ ist voreingestellt

    window.save_results_zip()
    ask = d.boxes[-1]
    assert ask["title"] == "Ergebnisse als ZIP speichern" and ask["default"] == "Speichern …"
    assert d.asked_for_file[-1] == str(target.parent / "ziel.zip")
    with zipfile.ZipFile(dest) as z:
        assert z.namelist() == ["termin_anonymisiert.txt"]       # Protokoll ist voreingestellt nicht dabei
    assert "1 Ergebnis(se) als ZIP" in window.status_label.text()

    monkeypatch.setattr(type(window), "_ask_zip", lambda self, n, pending: True)
    window.save_results_zip()
    with zipfile.ZipFile(dest) as z:
        assert z.namelist() == ["termin_anonymisiert.txt", PROTOCOL_NAME]
    assert window.actions_["zip_results"].isEnabled()
    window.close_batch()
    assert not window.actions_["zip_results"].isEnabled()
