"""Programmstart: Protokolldatei und Start des Fensters (wie der Rauchtest nach dem Build)."""

import logging
import os
import subprocess
import sys

import pytest
import spacy

from pii_redact import app


def test_log_dir_follows_the_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lokal"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert app.log_dir() == tmp_path / "lokal" / "pii-redact" / "logs"
    monkeypatch.setattr(sys, "platform", "linux")
    assert app.log_dir() == tmp_path / "state" / "pii-redact" / "logs"


def test_logging_writes_a_file_and_survives_an_unwritable_folder(monkeypatch, tmp_path):
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        monkeypatch.setattr(app, "log_dir", lambda: tmp_path / "logs")
        path = app.setup_logging()
        logging.getLogger("pii_redact").info("Testzeile")
        for h in root.handlers:
            h.flush()
        assert path == tmp_path / "logs" / "pii-redact.log" and "INFO pii_redact: Testzeile" in path.read_text("utf-8")

        blocker = tmp_path / "datei"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(app, "log_dir", lambda: blocker / "logs")      # Ordner lässt sich nicht anlegen
        assert app.setup_logging() is None                                 # Programm startet trotzdem
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(level)


@pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")
def test_program_starts_and_writes_a_log(tmp_path):
    """Start mit ``--smoke-test``: Fenster aufbauen, kurz laufen, beenden – Rückgabewert 0 und ein Protokoll
    ohne Fehlereintrag."""
    pytest.importorskip("PySide6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", LOCALAPPDATA=str(tmp_path / "lokal"),
               XDG_STATE_HOME=str(tmp_path / "lokal"), APPDATA=str(tmp_path / "cfg"),
               XDG_CONFIG_HOME=str(tmp_path / "cfg"))
    done = subprocess.run([sys.executable, "-m", "pii_redact", "--smoke-test"], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    log = (tmp_path / "lokal" / "pii-redact" / "logs" / "pii-redact.log").read_text(encoding="utf-8")
    assert "pii-redact" in log and "startet" in log
    assert "Unerwarteter Fehler" not in log and "Traceback" not in log
    # Offline-Schalter: das Programm setzt sie selbst, bevor Bibliotheken geladen werden
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
