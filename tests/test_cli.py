"""Kommandozeile: Ausgabe ohne Klartext, kein stilles Überschreiben, Rückgabewerte, Einstellungsdatei."""

import json
import shutil
from pathlib import Path

import pytest
import spacy

from pii_redact import __version__, cli

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    shutil.copy(SAMPLES / "beispiel.txt", tmp_path / "brief.txt")
    return tmp_path


def test_worst_order():
    assert cli.worst(0, 3) == 3
    assert cli.worst(3, 1) == 1
    assert cli.worst(1, 2) == 2 and cli.worst(2, 1) == 2
    assert cli.worst(0) == 0


def test_usage_error_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == cli.ERROR          # nicht 2 – das bedeutet „Reste im Ergebnis“
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0 and __version__ in capsys.readouterr().out


def test_missing_settings_file(work):
    with pytest.raises(SystemExit) as exc:
        cli.main([str(work / "brief.txt"), "--einstellungen", str(work / "gibt-es-nicht.json")])
    assert exc.value.code == cli.ERROR


@needs_model
def test_output_without_plain_text_and_no_overwrite(work, capsys):
    src = work / "brief.txt"
    target = work / "brief_anonymisiert.txt"
    assert cli.main([str(src), "--schnell"]) == cli.OK
    out = capsys.readouterr()
    assert "Funde (" in out.out and "Person" in out.out
    for secret in ("Jonas Weber", "DE89 3704", "kowalski@nordlicht.example"):   # kein Klartext in der Ausgabe
        assert secret not in out.out + out.err
    assert target.is_file() and "Jonas Weber" not in target.read_text(encoding="utf-8")
    assert not (work / "brief_anonymisiert.txt.tmp").exists()

    # zweiter Lauf: vorhandenes Ergebnis bleibt unangetastet
    target.write_text("von Hand geprüft", encoding="utf-8")
    assert cli.main([str(src), "--schnell"]) == cli.ERROR
    assert "--ueberschreiben" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "von Hand geprüft"
    assert cli.main([str(src), "--schnell", "--ueberschreiben"]) == cli.OK
    assert target.read_text(encoding="utf-8") != "von Hand geprüft"


@needs_model
def test_details_show_plain_text(work, capsys):
    assert cli.main([str(work / "brief.txt"), "--schnell", "--details", "-o", str(work / "aus")]) == cli.OK
    assert "Jonas Weber" in capsys.readouterr().out
    assert cli.main([str(work / "brief.txt"), "--schnell", "--nur-anzeigen"]) == cli.OK
    assert "Jonas Weber" in capsys.readouterr().out
    assert not (work / "brief_anonymisiert.txt").exists()


@needs_model
def test_settings_file(work):
    settings = work / "dienstkonto.json"
    settings.write_text(json.dumps({"replace_mode": "nummeriert"}), encoding="utf-8")
    assert cli.main([str(work / "brief.txt"), "--schnell", "--einstellungen", str(settings)]) == cli.OK
    assert "[PERSON_1]" in (work / "brief_anonymisiert.txt").read_text(encoding="utf-8")


@needs_model
def test_pdf_and_as_text(work):
    shutil.copy(SAMPLES / "beispiel.pdf", work / "akte.pdf")
    assert cli.main([str(work / "akte.pdf"), "--schnell"]) == cli.OK
    assert (work / "akte_geschwaerzt.pdf").is_file()
    assert cli.main([str(work / "akte.pdf"), "--schnell", "--als-text"]) == cli.OK
    assert (work / "akte_anonymisiert.txt").is_file()


@needs_model
def test_return_code_keeps_most_severe(work, monkeypatch):
    """Ein Fehler bei einer Datei darf nicht von späteren Ergebnissen überdeckt werden – und Reste im PDF
    wiegen schwerer als ein Fehler."""
    missing = str(work / "fehlt.txt")
    assert cli.main([missing, str(work / "brief.txt"), "--schnell"]) == cli.ERROR
    monkeypatch.setattr(cli, "export_document", lambda *a, **k: ["Text „Muster“ auf Seite 1"])
    assert cli.main([missing, str(work / "brief.txt"), "--schnell", "--ueberschreiben"]) == cli.LEFTOVERS


@needs_model
def test_broken_pdf_is_reported_and_run_continues(work, capsys):
    """Ein defektes PDF beendet den Lauf nicht – weder bei einzelnen Dateien noch im Ordner."""
    (work / "defekt.pdf").write_bytes(b"%PDF-1.4\nkaputt")
    (work / "leer.pdf").write_bytes(b"")
    rc = cli.main([str(work / "defekt.pdf"), str(work / "leer.pdf"), str(work / "brief.txt"), "--schnell"])
    err = capsys.readouterr().err
    assert rc == cli.ERROR and err.count("beschädigt oder kein gültiges PDF") == 2
    assert (work / "brief_anonymisiert.txt").is_file()

    source = work / "ordner"
    source.mkdir()
    (source / "a_defekt.pdf").write_bytes(b"%PDF-1.4\nkaputt")
    shutil.copy(SAMPLES / "beispiel.txt", source / "b_brief.txt")
    rc = cli.main([str(source), "-o", str(work / "aus"), "--schnell"])
    assert rc == cli.ERROR
    assert (work / "aus" / "b_brief_anonymisiert.txt").is_file()
    protocol = (work / "aus" / "pii-redact-protokoll.csv").read_text(encoding="utf-8-sig")
    assert "a_defekt.pdf;Fehler" in protocol and "beschädigt" in protocol
