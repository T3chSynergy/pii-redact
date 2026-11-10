"""Gemeinsame Test-Hilfen.

Jedes Hauptfenster lädt eigene Analysemodelle (spaCy, im Modus „Gründlich“ zusätzlich das
Transformer-Modell – zusammen rund 1 GB). Geschlossene Fenster werden von Qt nicht von selbst
freigegeben; ohne Aufräumen wächst der Speicher mit jedem Oberflächentest, bis der Testlauf
abbricht. Deshalb nach jedem Test alle Hauptfenster samt Modellen freigeben.
"""

import gc
import sys

import pytest


@pytest.fixture(autouse=True)
def _release_main_windows():
    yield
    if "pii_redact.ui.main_window" not in sys.modules:
        return  # Test ohne Oberfläche – nichts zu tun
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from pii_redact.ui.main_window import MainWindow

    app = QApplication.instance()
    if app is None:
        return
    # ausstehende Null-Timer (z. B. Layout-Anpassung) noch ausführen, solange die Fenster existieren –
    # sonst feuern sie im nächsten Test auf bereits gelöschte Qt-Objekte
    QCoreApplication.processEvents()
    for w in app.topLevelWidgets():
        if isinstance(w, MainWindow):
            w.runner.shutdown()
            w.runner._analyzer = None
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.fixture()
def window(tmp_path, monkeypatch):
    """Hauptfenster ohne Bildschirm; Einstellungen und Arbeitsstand liegen im Testordner."""
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _app = QApplication.instance() or QApplication([])  # Referenz halten, sonst räumt Python die App ab
    from pii_redact.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    yield w
    if w.session:
        w.session.dirty = False
    w.close()
