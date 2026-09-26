"""Ordner-Bearbeitung: Arbeitsstand, Fortsetzen, Änderungserkennung, Protokoll, CLI."""

import csv
import os
import shutil
import time
from pathlib import Path

import pytest
import spacy

from pii_redact.core import Finding, Settings
from pii_redact.core.batch import (
    PROTOCOL_NAME,
    WORKSPACE_NAME,
    Status,
    Workspace,
    findings_from_compact,
    findings_to_compact,
    scan_folder,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")


@pytest.fixture()
def folder(tmp_path):
    src = tmp_path / "akten"
    (src / "Verträge").mkdir(parents=True)
    (src / ".versteckt").mkdir()
    shutil.copy(SAMPLES / "beispiel.txt", src / "Verträge" / "brief.txt")
    shutil.copy(SAMPLES / "beispiel.md", src / "protokoll.md")
    shutil.copy(SAMPLES / "beispiel.pdf", src / "Verträge" / "akte.pdf")
    (src / "Verträge" / "brief_anonymisiert.txt").write_text("alt", encoding="utf-8")  # früheres Ergebnis
    (src / "~$temp.txt").write_text("Office-Sperrdatei", encoding="utf-8")
    (src / ".versteckt" / "x.txt").write_text("x", encoding="utf-8")
    (src / "bild.png").write_bytes(b"\x89PNG")
    return src


def test_scan_folder_filters(folder):
    assert scan_folder(folder, True) == ["protokoll.md", "Verträge/akte.pdf", "Verträge/brief.txt"]
    assert scan_folder(folder, False) == ["protokoll.md"]
    inner = folder / "ziel"
    inner.mkdir()
    (inner / "kopie.txt").write_text("x", encoding="utf-8")
    assert "ziel/kopie.txt" not in scan_folder(folder, True, exclude=inner)


def test_compact_roundtrip_without_text():
    text = "Herr Max Mustermann wohnt in Köln."
    f = Finding(5, 19, "PERSON", "Max Mustermann", 0.85, active=False, original_type="LOCATION")
    compact = findings_to_compact([f])
    assert "Max" not in str(compact)
    (g,) = findings_from_compact(compact, text)
    assert (g.text, g.active, g.original_type) == ("Max Mustermann", False, "LOCATION")
    assert findings_from_compact([{"s": 5, "e": 999, "t": "PERSON"}], text) == []  # ungültig → verworfen


def _analyze_fake(ws: Workspace):
    """Analyse ohne Sprachmodell simulieren: jede Datei bekommt einen Fund an Position 0–4."""
    from pii_redact.core.batch import sha256_of
    from pii_redact.core.loaders import load_document

    for e in ws.ordered():
        p = ws.abs(e.rel)
        doc = load_document(p)
        st = p.stat()
        ws.set_analysis(e.rel, sha256=sha256_of(p), size=st.st_size, mtime_ns=st.st_mtime_ns,
                        findings=[Finding(0, 4, "PERSON", doc.text[0:4])], warnings=doc.warnings, mode="schnell")


def test_workspace_resume_and_change_detection(folder, tmp_path):
    target = tmp_path / "ziel"
    ws = Workspace.create(folder, target)
    assert Workspace.exists_in(target) and len(ws.entries) == 3
    _analyze_fake(ws)
    assert all(e.status == Status.TO_REVIEW for e in ws.entries.values())

    # Datei prüfen + exportieren
    from pii_redact.core.batch import export_document
    from pii_redact.core.loaders import load_document

    rel = "Verträge/brief.txt"
    doc = load_document(ws.abs(rel))
    findings = findings_from_compact(ws.entries[rel].findings, doc.text)
    out = ws.out(rel)
    export_document(doc, findings, Settings(), out)
    ws.mark_done(rel, reviewed=True, exported_as=out, leftovers=[], findings=findings)
    ws.save()
    assert out == target / "Verträge" / "brief_anonymisiert.txt" and out.read_text(encoding="utf-8").startswith("[PERSON]")

    # Kein Klartext im Arbeitsstand
    raw = (target / WORKSPACE_NAME).read_text(encoding="utf-8")
    for secret in ("Bergmann", "DE89", "Hoffmann"):
        assert secret not in raw

    # Fortsetzen: Stand wiederhergestellt, nächste offene Datei stimmt
    ws2 = Workspace.load(target)
    assert ws2.entries[rel].status == Status.REVIEWED and ws2.entries[rel].reviewed_by
    assert ws2.next_open() == "protokoll.md"
    assert ws2.sync_with_disk() == []

    # Nur Zeitstempel geändert (z. B. kopiert) → Prüfsumme gleich → Stand bleibt
    p = ws2.abs(rel)
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    assert ws2.sync_with_disk() == [] and ws2.entries[rel].status == Status.REVIEWED

    # Inhalt geändert → Funde und Prüfstatus verworfen, muss neu analysiert werden
    p.write_text(p.read_text(encoding="utf-8") + "\nNachtrag", encoding="utf-8")
    assert ws2.sync_with_disk() == [rel]
    assert ws2.entries[rel].status == Status.WAITING and ws2.entries[rel].findings is None
    assert "geändert" in ws2.entries[rel].note

    # Datei gelöscht / neu hinzugekommen
    (folder / "protokoll.md").unlink()
    shutil.copy(SAMPLES / "schwierig.txt", folder / "neu.txt")
    todo = ws2.sync_with_disk()
    assert ws2.entries["protokoll.md"].status == Status.MISSING
    assert "neu.txt" in todo


def test_edit_after_review_requires_confirmation(folder, tmp_path):
    ws = Workspace.create(folder, tmp_path / "ziel")
    _analyze_fake(ws)
    rel = "protokoll.md"
    from pii_redact.core.loaders import load_document

    doc = load_document(ws.abs(rel))
    findings = findings_from_compact(ws.entries[rel].findings, doc.text)
    ws.mark_done(rel, reviewed=True, exported_as=ws.out(rel), leftovers=[], findings=findings)
    ws.update_findings(rel, findings)  # unverändert → bleibt geprüft
    assert ws.entries[rel].status == Status.REVIEWED
    findings[0].active = False
    ws.update_findings(rel, findings)
    assert ws.entries[rel].status == Status.TO_REVIEW and "erneut" in ws.entries[rel].note


def test_protocol_has_no_clear_text(folder, tmp_path):
    ws = Workspace.create(folder, tmp_path / "ziel")
    _analyze_fake(ws)
    ws.save()
    rows = list(csv.reader(open(tmp_path / "ziel" / PROTOCOL_NAME, encoding="utf-8-sig"), delimiter=";"))
    assert rows[0][:2] == ["Datei", "Status"] and len(rows) == 4
    assert all(r[1] == "zu prüfen" for r in rows[1:])
    content = (tmp_path / "ziel" / PROTOCOL_NAME).read_text(encoding="utf-8-sig")
    assert "Person: 1" in content  # nur Datenart und Anzahl
    assert "Bergmann" not in content and "Petra" not in content


@needs_model
def test_cli_folder(folder, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from pii_redact.cli import main

    target = tmp_path / "ergebnis"
    assert main([str(folder), "-o", str(target)]) == 0
    assert (target / "Verträge" / "akte_geschwaerzt.pdf").is_file()
    assert (target / "protokoll_anonymisiert.md").is_file()
    ws = Workspace.load(target)
    assert {e.status for e in ws.entries.values()} == {Status.AUTO}
    assert "Bergmann" not in (target / "Verträge" / "brief_anonymisiert.txt").read_text(encoding="utf-8")
