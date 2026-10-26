"""Programmsymbol: tools/make_icon.py erzeugt genau die eingecheckten Dateien (Herkunft nachvollziehbar)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _make_icon():
    spec = importlib.util.spec_from_file_location("make_icon", ROOT / "tools" / "make_icon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_icon"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_icon_matches_script(tmp_path):
    m = _make_icon()
    ico, png = m.write(tmp_path)
    assert m.same_image(ico, m.ICO), "packaging/pii-redact.ico weicht von tools/make_icon.py ab"
    assert m.same_image(png, m.PNG), "resources/icon.png weicht von tools/make_icon.py ab"


def test_icon_comparison_detects_changes(tmp_path):
    """Gegenprobe: schon eine minimal andere Farbe muss als Abweichung auffallen."""
    m = _make_icon()
    m.LINES = [(box, (174, 181, 189, 255) if color == m.GREY else color) for box, color in m.LINES]
    ico, png = m.write(tmp_path)
    assert not m.same_image(png, m.PNG)
    assert not m.same_image(ico, m.ICO)
