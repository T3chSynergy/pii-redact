"""Oberfläche: Export und Speichern einer einzelnen Datei – Rückfragen, Abbruch und Fehlerwege.

Dialoge werden durch vorgegebene Antworten ersetzt; geprüft wird, was danach auf dem Datenträger liegt und
was das Fenster meldet."""

import os
from pathlib import Path

import pytest
import spacy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from test_ui import SAMPLES, _open

from pii_redact.core import Finding
from pii_redact.ui import window_export as we

pytestmark = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")

KAYA = "Antrag von Herrn Deniz Kaya.\nAnhang: antrag_kaya.pdf\nMit freundlichen Grüßen"


class Dialogs:
    """Ersetzt Dateidialog und Meldungsfenster und merkt sich, was gezeigt wurde."""

    def __init__(self, monkeypatch, target: Path | None = None, click: str | None = None):
        self.target, self.click = target, click
        self.asked_for_file: list[str] = []      # Vorschlag des Dateidialogs
        self.shown: list[tuple[str, str, str]] = []   # (Art, Titel, Text) der einfachen Meldungen
        self.boxes: list[dict] = []              # Rückfragen mit eigenen Schaltflächen
        monkeypatch.setattr(QFileDialog, "getSaveFileName", self._save_name)
        for kind in ("information", "warning", "critical"):
            monkeypatch.setattr(QMessageBox, kind, lambda _p, title, text, *a, _k=kind, **k: self.shown.append(
                (_k, title, text)))
        monkeypatch.setattr(QMessageBox, "exec", lambda box: self._exec(box))

    def _save_name(self, _parent, _title, suggestion, _filter="", *a, **k):
        self.asked_for_file.append(suggestion)
        return (str(self.target) if self.target else "", "")

    def _exec(self, box):
        buttons = {b.text(): b for b in box.buttons()}
        self.boxes.append({"title": box.windowTitle(), "text": box.text(), "info": box.informativeText(),
                           "buttons": sorted(buttons), "default": box.defaultButton().text()})
        # ohne Vorgabe: die voreingestellte Schaltfläche (Eingabetaste)
        button = buttons[self.click] if self.click else box.defaultButton()
        button.click()
        return box.standardButton(button)        # wie das echte exec(): Kennung einer Standard-Schaltfläche


def _text_file(window, tmp_path, text=KAYA, name="brief.txt"):
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    _open(window, source)
    return source


def _only_person(window, name: str) -> None:
    """Fundliste auf genau einen aktiven Personenfund setzen – unabhängig davon, was die Erkennung liefert."""
    s = window.session
    pos = s.doc.text.index(name)
    s.findings[:] = [Finding(pos, pos + len(name), "PERSON", name)]


# ------------------------------------------------------------------ ohne Dokument
def test_export_without_document_does_nothing(window, monkeypatch):
    d = Dialogs(monkeypatch)
    window.export_text()
    window.export_pdf()
    window._export_default()
    assert not d.asked_for_file and not d.shown and not d.boxes


# ------------------------------------------------------------------ Text-Export
def test_text_export_cancelled_in_file_dialog(window, tmp_path, monkeypatch):
    source = _text_file(window, tmp_path, "Frau Erika Beispiel wohnt in Musterstadt.")
    window.session.dirty = True
    d = Dialogs(monkeypatch)                      # Dateidialog liefert keinen Namen = Abbrechen
    window.export_text()
    assert d.asked_for_file == [str(source.with_name("brief_anonymisiert.txt"))]
    assert window.session.dirty and sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["brief.txt"]
    assert "gespeichert" not in window.status_label.text()


def test_text_export_never_overwrites_original(window, tmp_path, monkeypatch):
    text = "Frau Erika Beispiel wohnt in Musterstadt."
    source = _text_file(window, tmp_path, text)
    d = Dialogs(monkeypatch, target=source)
    window.export_text()
    assert d.shown == [("warning", "Export", "Das Original darf nicht überschrieben werden.")]
    assert source.read_text(encoding="utf-8") == text
    # auch über einen anderen Schreibweg zum selben Ort (Unterordner und zurück)
    (tmp_path / "x").mkdir()
    d.target = tmp_path / "x" / ".." / "brief.txt"
    window.export_text()
    assert len(d.shown) == 2 and source.read_text(encoding="utf-8") == text


def test_text_export_suggests_markdown_and_reports_in_status_bar(window, tmp_path, monkeypatch):
    source = _text_file(window, tmp_path, "# Notiz\n\nFrau Erika Beispiel wohnt in Musterstadt.\n", "notiz.md")
    target = tmp_path / "aus" / "ergebnis.md"
    target.parent.mkdir()
    d = Dialogs(monkeypatch, target=target)
    window.session.dirty = True
    window._export_default()                      # kein PDF → Text-Export
    assert d.asked_for_file == [str(source.with_name("notiz_anonymisiert.md"))]
    out = target.read_text(encoding="utf-8")
    assert out.startswith("# Notiz") and "Erika Beispiel" not in out
    assert not window.session.dirty and not d.shown and not d.boxes
    assert "ergebnis.md" in window.status_label.text() and "Text gespeichert" in window.status_label.text()
    assert str(target) in window.status_label.toolTip()

    opened = []
    monkeypatch.setattr(type(window), "_open_path", lambda self, p: opened.append(p))
    window._on_status_link("open-folder")         # Link „Ordner öffnen“ in der Statusleiste
    window._on_status_link("etwas-anderes")
    assert opened == [target.parent]


def test_text_export_with_free_edit_skips_the_check_and_writes_the_edited_text(window, tmp_path, monkeypatch):
    """Freie Bearbeitung: gespeichert wird der Text aus dem Editor; die Kontrolle auf weitere Vorkommen entfällt
    (sie bezöge sich auf die Funde, nicht auf den von Hand geänderten Text)."""
    _text_file(window, tmp_path)
    _only_person(window, "Kaya")
    window.session.manual_text = "von Hand geschrieben"
    target = tmp_path / "out.txt"
    d = Dialogs(monkeypatch, target=target)
    window.export_text()
    assert target.read_text(encoding="utf-8") == "von Hand geschrieben" and not d.boxes
    assert all(f.source != "kontrolle" for f in window.session.findings)


def test_text_export_failure_is_reported_and_leaves_no_file(window, tmp_path, monkeypatch):
    """Scheitert das Schreiben (Ziel schreibgeschützt, Netzlaufwerk getrennt), erscheint eine verständliche
    Fehlermeldung statt „Unerwarteter Fehler“; das Dokument gilt weiter als ungespeichert."""
    _text_file(window, tmp_path, "Frau Erika Beispiel wohnt in Musterstadt.")
    window.session.dirty = True
    target = tmp_path / "gibt" / "es" / "nicht" / "out.txt"
    d = Dialogs(monkeypatch, target=target)
    window.export_text()
    kind, title, text = d.shown[0]
    assert (kind, title) == ("critical", "Export fehlgeschlagen") and len(d.shown) == 1
    assert str(target) in text and "konnte nicht gespeichert werden" in text
    assert window.session.dirty and not target.exists() and "gespeichert" not in window.status_label.text()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["brief.txt"]      # auch keine .tmp-Datei


# ------------------------------------------------------------------ Rückfrage „Kontrolle vor dem Speichern“
def test_question_before_saving_default_goes_back(window, tmp_path, monkeypatch):
    """Die echte Rückfrage: Voreinstellung ist „Zurück zur Prüfung“ – ein Druck auf die Eingabetaste speichert
    nicht. Der Vorschlag steht danach in der Fundliste und ist ausgewählt."""
    _text_file(window, tmp_path)
    _only_person(window, "Kaya")
    d = Dialogs(monkeypatch, target=tmp_path / "out.txt")
    window.export_text()
    box = d.boxes[0]
    assert box["title"] == "Kontrolle vor dem Speichern" and "an einer weiteren Stelle" in box["text"]
    assert box["buttons"] == ["Trotzdem speichern", "Zurück zur Prüfung"] and box["default"] == "Zurück zur Prüfung"
    assert "• kaya (Zeile 2)" in box["info"] and "Herkunft „Kontrolle“" in box["info"]
    assert not d.asked_for_file and not (tmp_path / "out.txt").exists()
    new = [f for f in window.session.findings if f.source == "kontrolle"]
    assert [(f.text, f.active) for f in new] == [("kaya", False)]
    assert window.findings.selected_ids() == [new[0].id]
    assert "1 Vorschlag" in window.status_label.text()


def test_question_before_saving_save_anyway(window, tmp_path, monkeypatch):
    _text_file(window, tmp_path)
    _only_person(window, "Kaya")
    target = tmp_path / "out.txt"
    d = Dialogs(monkeypatch, target=target, click="Trotzdem speichern")
    window.export_text()
    assert len(d.boxes) == 1 and "antrag_kaya.pdf" in target.read_text(encoding="utf-8")
    # der Vorschlag bleibt ohne Häkchen in der Liste; ein zweites Speichern fragt nicht noch einmal
    window.export_text()
    assert len(d.boxes) == 1 and [f.active for f in window.session.findings if f.source == "kontrolle"] == [False]


def test_question_before_saving_lists_at_most_twelve_and_names_pages(window, tmp_path, monkeypatch):
    lines = ["Antrag von Herrn Deniz Kaya."] + [f"Anlage {i}: akte{i}_kaya.pdf" for i in range(1, 16)]
    _text_file(window, tmp_path, "\n".join(lines))
    _only_person(window, "Kaya")
    d = Dialogs(monkeypatch)
    window.export_text()
    box = d.boxes[0]
    assert "an 15 weiteren Stellen" in box["text"] and "15 Vorschläge" in window.status_label.text()
    assert box["info"].count("• kaya") == 12 and "• … und 3 weitere" in box["info"]
    assert "• kaya (Zeile 2)" in box["info"] and "(Zeile 13)" in box["info"] and "(Zeile 14)" not in box["info"]

    window.session.dirty = False                          # sonst fragt das Fenster vor dem Öffnen nach
    _open(window, SAMPLES / "mail_mit_scan.pdf")          # PDF: Seite statt Zeile
    window.session.doc.ocr_pages.clear()                  # hier geht es nur um die Kontrolle
    _only_person(window, "Kaya")
    d.boxes.clear()
    window.export_pdf()
    assert "(Seite 1)" in d.boxes[0]["info"] and "Zeile" not in d.boxes[0]["info"]
    assert not d.asked_for_file


def test_text_with_markup_in_question_is_escaped(window, tmp_path, monkeypatch):
    """Fundtext mit spitzen Klammern darf in der Rückfrage nicht als Formatierung gelesen werden."""
    _text_file(window, tmp_path, "Kennung <b>A1</b> vergeben.\nErneut: <b>A1</b> genannt.")
    s = window.session
    s.findings[:] = [Finding(8, 17, "CUSTOM", "<b>A1</b>")]
    d = Dialogs(monkeypatch)
    window.export_text()
    assert "• &lt;b&gt;A1&lt;/b&gt; (Zeile 2)" in d.boxes[0]["info"]


# ------------------------------------------------------------------ Rückfrage „Texterkennung – geprüft?“
def test_ocr_question_blocks_export_until_confirmed(window, tmp_path, monkeypatch):
    _open(window, SAMPLES / "beispiel.pdf")
    doc = window.session.doc
    doc.ocr_pages[:] = [0]                                # als wäre Seite 1 per Texterkennung gelesen
    d = Dialogs(monkeypatch, target=tmp_path / "out.txt")
    window.export_text()
    window.export_pdf()
    assert [b["title"] for b in d.boxes] == ["Texterkennung – geprüft?"] * 2
    assert d.boxes[0]["text"] == "<b>Seite 1 wurde per Texterkennung gelesen.</b>"
    assert d.boxes[0]["buttons"] == ["Ja, vollständig geprüft", "Zurück zur Prüfung"]
    assert d.boxes[0]["default"] == "Zurück zur Prüfung"
    assert not d.asked_for_file and not (tmp_path / "out.txt").exists()

    doc.ocr_pages[:] = [0, 2]
    d.click = "Ja, vollständig geprüft"
    window.export_text()
    assert d.boxes[-1]["text"] == "<b>Seiten 1, 3 wurden per Texterkennung gelesen.</b>"
    assert (tmp_path / "out.txt").is_file()


# ------------------------------------------------------------------ PDF-Export
def test_pdf_export_only_for_pdf_documents(window, tmp_path, monkeypatch):
    _text_file(window, tmp_path, "Frau Erika Beispiel wohnt in Musterstadt.")
    d = Dialogs(monkeypatch, target=tmp_path / "out.pdf")
    window.export_pdf()
    assert d.shown == [("information", "Export", "PDF-Export ist nur für PDF-Dokumente möglich.")]
    assert not d.asked_for_file and not (tmp_path / "out.pdf").exists()


def test_pdf_export_cancelled_or_onto_original(window, tmp_path, monkeypatch):
    source = tmp_path / "beispiel.pdf"
    source.write_bytes((SAMPLES / "beispiel.pdf").read_bytes())
    before = source.read_bytes()
    _open(window, source)
    window.session.dirty = True
    d = Dialogs(monkeypatch)
    window._export_default()                              # PDF → PDF-Export; Dateidialog abgebrochen
    assert d.asked_for_file == [str(source.with_name("beispiel_geschwaerzt.pdf"))]
    assert window.session.dirty and [p.name for p in tmp_path.iterdir() if p.is_file()] == ["beispiel.pdf"]

    d.target = source
    window.export_pdf()
    assert d.shown == [("warning", "Export", "Das Original darf nicht überschrieben werden.")]
    assert source.read_bytes() == before and window.session.dirty


def test_pdf_export_failure_is_reported_and_leaves_no_file(window, tmp_path, monkeypatch):
    """Scheitert das Schreiben, erscheint eine Fehlermeldung; der Mauszeiger ist wieder normal, das Dokument
    gilt weiter als ungespeichert, und unter dem Zielnamen liegt nichts."""
    _open(window, SAMPLES / "beispiel.pdf")
    window.session.dirty = True
    target = tmp_path / "gibt" / "es" / "nicht" / "out.pdf"
    d = Dialogs(monkeypatch, target=target)
    window.export_pdf()
    assert len(d.shown) == 1 and d.shown[0][:2] == ("critical", "Export fehlgeschlagen") and d.shown[0][2]
    assert QApplication.overrideCursor() is None and window.session.dirty
    assert not target.exists() and "geschwärzt" not in window.status_label.text()

    def broken(*_a, **_k):
        raise RuntimeError("Kontrolle abgebrochen")

    monkeypatch.setattr(we, "save_redacted_pdf", broken)
    d.target = tmp_path / "out.pdf"
    window.export_pdf()
    assert d.shown[-1] == ("critical", "Export fehlgeschlagen", "Kontrolle abgebrochen")
    assert QApplication.overrideCursor() is None and window.session.dirty


def test_pdf_export_reports_leftovers_of_the_check(window, tmp_path, monkeypatch):
    """Meldet die Kontrolle nach dem Speichern Reste, erscheint eine Warnung mit höchstens 20 Einträgen –
    keine Erfolgsmeldung in der Statusleiste."""
    _open(window, SAMPLES / "beispiel.pdf")
    leftovers = [f"Rest {i} – an der geschwärzten Stelle noch vorhanden (Seite 1)" for i in range(1, 26)]
    monkeypatch.setattr(we, "save_redacted_pdf", lambda *a, **k: list(leftovers))
    d = Dialogs(monkeypatch, target=tmp_path / "out.pdf")
    window.session.dirty = True
    window.export_pdf()
    kind, title, text = d.shown[0]
    assert (kind, title) == ("warning", "Prüfung: Reste gefunden") and len(d.shown) == 1
    assert text.count("• Rest") == 20 and "Rest 20 " in text and "Rest 21 " not in text
    assert "Häufige Fragen" in text and "Kontrolle ohne Reste" not in window.status_label.text()
    assert not window.session.dirty                       # die Datei ist geschrieben


def test_pdf_export_success_message_counts_active_findings(window, tmp_path, monkeypatch):
    _open(window, SAMPLES / "beispiel.pdf")
    s = window.session
    d = Dialogs(monkeypatch, target=tmp_path / "out.pdf")
    window.export_pdf()
    n = sum(1 for f in s.findings if f.active)
    assert n > 1 and f"{n} Stellen geschwärzt, Kontrolle ohne Reste" in window.status_label.text()
    tip = window.status_label.toolTip()
    assert str(tmp_path / "out.pdf") in tip and "Metadaten" in tip and not d.shown

    keep = next(f for f in s.findings if f.active)
    s.set_active([f.id for f in s.findings if f.id != keep.id], False)
    s.doc.warnings.append("Beispielhinweis zum Dokument.")
    d.target = tmp_path / "eins.pdf"
    d.click = "Trotzdem speichern"                        # falls der eine Fund noch anderswo steht
    window.export_pdf()
    assert "1 Stelle geschwärzt" in window.status_label.text()
    assert "Hinweise: Beispielhinweis zum Dokument." in window.status_label.toolTip()


def test_pdf_export_with_free_edit_explains_that_it_uses_the_findings(window, tmp_path, monkeypatch):
    _open(window, SAMPLES / "beispiel.pdf")
    window.session.manual_text = "von Hand geändert"
    target = tmp_path / "out.pdf"
    d = Dialogs(monkeypatch, target=target)
    window.export_pdf()
    assert [m[:2] for m in d.shown] == [("information", "Hinweis")] and "nur auf den Text-Export" in d.shown[0][2]
    assert target.is_file()


def test_open_path_reports_missing_folder(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    d = Dialogs(monkeypatch)
    window._open_path(None)
    window._open_path(tmp_path / "fehlt")
    window._open_path(tmp_path)
    assert [m[:2] for m in d.shown] == [("information", "Nicht vorhanden")] and "fehlt" in d.shown[0][2]
    assert [Path(p) for p in opened] == [tmp_path]
