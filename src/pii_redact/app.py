"""Startpunkt der Desktop-Anwendung."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

# Niemals Modelle o. Ä. aus dem Internet nachladen – auch nicht versehentlich über Bibliotheken.
for _var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    os.environ.setdefault(_var, "1")


def log_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "pii-redact" / "logs"


def setup_logging() -> Path | None:
    """Protokoll in eine Datei (die EXE hat kein Konsolenfenster). Es werden keine Dokumentinhalte
    geloggt – nur Programmereignisse und Fehler."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    path = None
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "pii-redact.log"
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        path = None
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    log_path = setup_logging()
    log = logging.getLogger("pii_redact")

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import __version__
    from .ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName("pii-redact")
    app.setOrganizationName("pii-redact")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    icon = Path(__file__).resolve().parent / "resources" / "icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    def excepthook(exc_type, exc, tb):
        log.error("Unerwarteter Fehler:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        QMessageBox.critical(
            None, "Unerwarteter Fehler",
            f"{exc_type.__name__}: {exc}\n\nDetails stehen im Protokoll:\n{log_path or '(nicht verfügbar)'}")

    sys.excepthook = excepthook
    log.info("pii-redact %s startet", __version__)

    win = MainWindow()
    win.show()
    if "--smoke-test" in argv:
        # Für den Build: Fenster aufbauen, kurz laufen lassen, beenden (Exitcode 0 = alles geladen)
        QTimer.singleShot(1500, app.quit)
        return app.exec()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        win.open_file(Path(files[0]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
