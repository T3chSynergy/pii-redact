"""Vergleichswerkzeug für Umbauten (tools/ergebnis_vergleich.py): Aufnahme ist wiederholbar, Abweichungen
werden benannt."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import spacy

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ergebnis_vergleich", ROOT / "tools" / "ergebnis_vergleich.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["ergebnis_vergleich"] = ev
spec.loader.exec_module(ev)

needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")


def test_compare_names_each_kind_of_difference():
    old = {"meta": {"python": "3.13"},
           "dokumente": {"a.txt": {"text": "Zeile 1\nZeile 2\n", "seiten": 0}, "weg.txt": {}},
           "analyse": {"schnell/a.txt": {"funde": [[0, 4, "PERSON"], [9, 12, "LOCATION"]], "reihenfolge": [1, 2]}}}
    same = json.loads(json.dumps(old))
    same["meta"]["python"] = "3.14"                       # die Umgebung wird nicht verglichen, nur genannt
    assert ev.compare(old, same) == []

    new = json.loads(json.dumps(old))
    new["dokumente"]["a.txt"] = {"text": "Zeile 1\nZeile zwei\n", "seiten": 1}
    del new["dokumente"]["weg.txt"]
    new["dokumente"]["neu.txt"] = {}
    new["analyse"]["schnell/a.txt"] = {"funde": [[0, 4, "PERSON"], [9, 13, "LOCATION"]], "reihenfolge": [2, 1]}
    diff = ev.compare(old, new)
    text = "\n".join(diff)
    assert len(diff) == 6
    assert "dokumente › a.txt › text: Text weicht ab" in text and "-Zeile 2" in text and "+Zeile zwei" in text
    assert "dokumente › a.txt › seiten: 0 → 1" in text
    assert "dokumente › weg.txt: nur im alten Stand" in text and "dokumente › neu.txt: nur im neuen Stand" in text
    assert "funde: 1 entfallen, 1 neu" in text and '- [9, 12, "LOCATION"]' in text and '+ [9, 13, "LOCATION"]' in text
    assert "reihenfolge: gleiche Einträge in anderer Reihenfolge" in text

    lines = []
    assert ev.report(old, same, log=lines.append) == 0
    assert lines[0].startswith("Hinweis: unterschiedliche Umgebung – python") and "Keine Abweichung" in lines[-1]
    lines.clear()
    assert ev.report(old, new, log=lines.append) == 1 and lines[0].startswith("6 Abweichung(en)")


def test_stable_drops_time_user_and_paths():
    data = {"version": 3, "source": "/tmp/x", "created": "2026-11-10", "files": {
        "a.txt": {"status": "zu_pruefen", "mtime_ns": 1, "reviewed_by": "jemand", "findings": [{"s": 1}]}}}
    assert ev._stable(data) == {"version": 3, "files": {"a.txt": {"status": "zu_pruefen", "findings": [{"s": 1}]}}}


@needs_model
def test_recording_is_repeatable_and_notices_a_changed_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    samples = tmp_path / "beispiele"
    samples.mkdir()
    (samples / "brief.txt").write_text("Frau Erika Beispiel wohnt in Musterstadt.\nIBAN DE89 3704 0044 0532 0130 00\n",
                                       encoding="utf-8")
    shutil.copy(ROOT / "samples" / "beispiel.pdf", samples / "akte.pdf")
    (samples / "kaputt.pdf").write_bytes(b"%PDF-1.4 kein PDF")
    (samples / "notiz.docx").write_bytes(b"wird nicht beachtet")
    quiet = lambda *_a: None  # noqa: E731

    first = ev.record(samples, ["schnell"], [ROOT / "samples" / "kommentare.pdf", samples / "kaputt.pdf"], log=quiet)
    second = ev.record(samples, ["schnell"], [ROOT / "samples" / "kommentare.pdf", samples / "kaputt.pdf"], log=quiet)
    assert ev.compare(first, second) == []                # gleicher Stand → keine Abweichung
    capsys.readouterr()

    special = ["sonderfall/ausgeblendete_ebene.pdf", "sonderfall/enger_zeilenabstand.pdf",
               "sonderfall/gedreht_und_zugeschnitten.pdf", "sonderfall/gemischte_groessen.pdf"]
    layer = first["dokumente"]["sonderfall/ausgeblendete_ebene.pdf"]       # ausgeblendete Ebene ist entfernt
    assert "Ausgeblendet" not in layer["text"] and "Max Mustermann" in layer["text"]
    assert layer["bereinigtes_pdf"]["ebenen"] == 0
    assert sorted(first["dokumente"]) == ["akte.pdf", "brief.txt", "kaputt.pdf", *special]
    # zugeschnittene Seite: der Text außerhalb ist beim Laden entfernt worden, auch aus den Zeichenschritten
    cropped = first["dokumente"]["sonderfall/gedreht_und_zugeschnitten.pdf"]
    assert "Außerhalb des Zuschnitts" not in cropped["text"] and "removed" in [n[0] for n in cropped["hinweise"]]
    assert cropped["bereinigtes_pdf"]["seiten"][1]["zeichenschritte"].startswith("1:")
    assert first["dokumente"]["kaputt.pdf"]["fehler"].startswith("UnsupportedFileError")
    assert sorted(first["analyse"]) == ["schnell/akte.pdf", "schnell/brief.txt", *(f"schnell/{n}" for n in special)]
    types = {f[2] for f in first["analyse"]["schnell/brief.txt"]["funde"]}
    assert {"PERSON", "IBAN_CODE"} <= types
    assert "Erika Beispiel" not in first["analyse"]["schnell/brief.txt"]["text"]["platzhalter"]
    pdf = first["analyse"]["schnell/akte.pdf"]["pdf"]
    assert set(pdf) == {"platzhalter", "schwaerzen_ohne_beschriftung", "mit_bereich"}
    page = pdf["platzhalter"]["ergebnis"]["seiten"][0]
    assert pdf["platzhalter"]["kontrolle"] == [] and "x" in page["bild"] and "[PERSON]" in page["text"]
    assert page["bild"] != pdf["schwaerzen_ohne_beschriftung"]["ergebnis"]["seiten"][0]["bild"]

    cli = first["kommandozeile"]["schnell"]
    assert cli["ordner"]["rueckgabewert"] == 1 and "kaputt.pdf" in cli["ordner"]["meldungen"]     # defektes PDF
    assert cli["einzeln.txt_anzeigen"]["rueckgabewert"] == 0 and cli["einzeln.txt_vorhanden"]["rueckgabewert"] == 1
    assert cli["fehlt"]["rueckgabewert"] == 1 and cli["ohne_argumente"]["rueckgabewert"] == 1
    assert "brief_anonymisiert.txt" in cli["ordner_ergebnis"] and "akte_geschwaerzt.pdf" in cli["ordner_ergebnis"]
    dump = json.dumps(first["kommandozeile"], ensure_ascii=False)
    assert ev.TMP_MARK in dump and "pii-vergleich-" not in dump and str(tmp_path) not in dump
    assert "reviewed_at" not in dump and "mtime_ns" not in dump and "Zeitpunkt" not in dump

    # zusätzliche PDFs: nur Prüfsummen, kein Text
    extra = first["zusatz"][str(ROOT / "samples" / "kommentare.pdf")]
    assert extra["seiten"] == 2 and len(extra["text"]) == 16 and any(n[0] == "removed" for n in extra["hinweise"])
    assert extra["bereinigtes_pdf"]["ebenen"] == 0
    assert all(len(page["text"]) == 16 for page in extra["bereinigtes_pdf"]["seiten"])
    assert first["zusatz"][str(samples / "kaputt.pdf")] == {"fehler": "UnsupportedFileError"}

    # ein geändertes Ergebnis fällt auf – hier: eine Erkennung liefert plötzlich nichts mehr
    from pii_redact.core import analyzer as an

    real = an.PiiAnalyzer.analyze
    monkeypatch.setattr(an.PiiAnalyzer, "analyze", lambda self, text, settings, **k: [
        f for f in real(self, text, settings, **k) if f.entity_type != "IBAN_CODE"])
    changed = ev.record(samples, ["schnell"], [], log=quiet)
    diff = "\n".join(ev.compare(first, changed))
    assert "analyse › schnell/brief.txt › funde: 1 entfallen, 0 neu" in diff and "IBAN_CODE" in diff
    assert "analyse › schnell/brief.txt › text › platzhalter" in diff
    assert "analyse › schnell/akte.pdf › pdf › platzhalter › ergebnis › seiten › 1 › text" in diff
    assert "analyse › schnell/akte.pdf › pdf › platzhalter › ergebnis › seiten › 1 › bild" in diff
    assert "kommandozeile › schnell › ordner_ergebnis › brief_anonymisiert.txt" in diff
    assert "zusatz" in diff                               # die zusätzlichen PDFs fehlen in der zweiten Aufnahme


def test_main_compares_two_files(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"dokumente": {"x": {"seiten": 1}}}), encoding="utf-8")
    b.write_text(json.dumps({"dokumente": {"x": {"seiten": 2}}}), encoding="utf-8")
    assert ev.main(["vergleichen", str(a), str(a)]) == 0
    assert ev.main(["vergleichen", str(a), str(b)]) == 1
    assert "dokumente › x › seiten: 1 → 2" in capsys.readouterr().out
