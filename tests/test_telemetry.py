"""onnxruntime darf keine Telemetrie an Microsoft schicken (Modus „Gründlich“, Texterkennung)."""

import os
import subprocess
import sys

import pytest

import pii_redact
from pii_redact.core import ocr
from pii_redact.core.transformer_ner import OnnxNerModel
from pii_redact.paths import find_ner_model

ort = pytest.importorskip("onnxruntime")


def test_variable_set_on_import():
    """Linux/macOS: Der Sende-Client lässt sich nur vor dem ersten Laden von onnxruntime abschalten."""
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    out = subprocess.run([sys.executable, "-c", "import os, pii_redact; print(os.environ.get('ORT_DISABLE_TELEMETRY'))"],
                         env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"
    assert pii_redact  # Import oben setzt die Variable auch im Testprozess


def _calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(ort, "disable_telemetry_events", lambda: calls.append("aus"))
    return calls


@pytest.mark.skipif(not ocr.is_available(), reason="RapidOCR nicht installiert")
def test_ocr_disables_telemetry_before_first_model(monkeypatch):
    calls = _calls(monkeypatch)
    monkeypatch.setattr(ocr, "_engine", None)
    created = []
    import rapidocr
    monkeypatch.setattr(rapidocr, "RapidOCR", lambda **kw: created.append(list(calls)) or object())
    ocr._get_engine()
    assert created == [["aus"]]          # Telemetrie war schon aus, als die Texterkennung startete


@pytest.mark.skipif(find_ner_model() is None, reason="Transformer-Modell für „Gründlich“ nicht installiert")
def test_ner_model_disables_telemetry(monkeypatch):
    calls = _calls(monkeypatch)
    sessions = []
    real = ort.InferenceSession
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: sessions.append(list(calls)) or real(*a, **k))
    OnnxNerModel(find_ner_model())
    assert sessions and sessions[0] == ["aus"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Sende-Client mit Geräte-ID nur unter Linux/macOS")
def test_no_device_id_or_event_store(tmp_path):
    """Ohne die Variable legt onnxruntime eine Geräte-ID und eine Ereignis-Datenbank im Cache-Ordner an."""
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    env.update(HOME=str(tmp_path), XDG_CACHE_HOME=str(tmp_path / "cache"))
    code = "import pii_redact.core.ocr as o; o._get_engine()"
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=300)
    assert not [p for p in tmp_path.rglob("*") if "DeveloperTools" in str(p) or p.name == "onnxruntime.db"]
