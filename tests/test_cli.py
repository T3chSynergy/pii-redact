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
