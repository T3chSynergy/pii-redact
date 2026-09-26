"""Anwenderhilfe: vollständig, Platzhalter ersetzt, interne Links gültig."""

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pii_redact import __version__
from pii_redact.core.entities import ENTITIES
from pii_redact.ui.help_window import chapters, load_help_html


def test_help_content():
    text = load_help_html()
    assert "{{" not in text and __version__ in text
    titles = [t for _a, t in chapters(text)]
    assert len(titles) >= 12 and any("Ordner" in t for t in titles) and any("Tastenkürzel" in t for t in titles)
    for key, e in ENTITIES.items():  # Tabelle der Datenarten stammt aus dem Programm
        if key != "CUSTOM":
            assert e.label in text


def test_internal_links_resolve():
    text = load_help_html()
    anchors = set(re.findall(r'<a name="([^"]+)"', text))
    for target in re.findall(r'<a href="#([^"]+)"', text):
        assert target in anchors, target


def test_help_window_opens(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from pii_redact.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow()
    assert w.actions_["help"].shortcut().toString() == "F1"
    w.show_help("ordner")
    assert w._help.isVisible() and w._help.toc.count() >= 13
    w._help.search.setText("Arbeitsstand")
    assert w._help.find_next()
    w._help.close()
    w.close()


def test_help_readable_in_dark_mode():
    from pii_redact.ui.help_window import DARK, LIGHT, browser_css

    dark = load_help_html(dark=True)
    light = load_help_html(dark=False)
    assert "{{STYLE}}" not in dark
    # dunkles Design: keine hellen Hintergründe für Tasten/Tabellenköpfe/Hinweise
    for key in ("code_bg", "th_bg", "hinweis_bg", "wichtig_bg", "tipp_bg"):
        assert f"background-color: {DARK[key]}" in dark
        assert f"background-color: {LIGHT[key]}" not in dark
    assert f"background-color: {LIGHT['code_bg']}" in light
    assert "<font" not in dark  # keine fest eingefärbten Texte mehr
    assert "prefers-color-scheme: dark" in browser_css()


def test_components_in_help_and_about(tmp_path):
    """Alle wesentlichen Open-Source-Komponenten stehen in Hilfe und „Über“-Dialog."""
    import html as _html

    from pii_redact.about import all_components
    from pii_redact.core import Settings
    from pii_redact.ui.about_dialog import about_html

    help_text = _html.unescape(load_help_html())
    about = _html.unescape(about_html(Settings()))
    names = [c.name for c in all_components()]
    assert len(names) >= 20
    for name in ("Microsoft Presidio", "PyMuPDF", "RapidOCR", "Qt 6", "ONNX Runtime", "Davlan", "OpenCV"):
        assert any(name in n for n in names), name
    for n in names:
        assert n in help_text and n in about, n
    assert "AGPL" in help_text and "LGPL" in about


def test_license_files_generated(tmp_path):
    """tools/lizenzen.py erzeugt Übersicht und Lizenztexte für alle installierten Pakete."""
    import importlib.util
    from pathlib import Path

    tool = Path(__file__).resolve().parents[1] / "tools" / "lizenzen.py"
    spec = importlib.util.spec_from_file_location("lizenzen", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(tmp_path) == 0
    overview = (tmp_path / "LIZENZEN.txt").read_text(encoding="utf-8-sig")
    texts = (tmp_path / "LIZENZTEXTE.txt").read_text(encoding="utf-8-sig")
    for pkg in ("presidio", "spacy", "onnxruntime", "PyMuPDF", "PySide6", "rapidocr"):
        assert pkg.lower() in overview.lower(), pkg
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in texts   # Qt/PySide6 über Standardtext
    assert "Apache License" in texts
