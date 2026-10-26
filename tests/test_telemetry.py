"""onnxruntime darf keine Telemetrie an Microsoft schicken (Modus „Gründlich“, Texterkennung)."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pii_redact
from pii_redact.core import ocr
from pii_redact.core.transformer_ner import OnnxNerModel
from pii_redact.paths import find_ner_model

ort = pytest.importorskip("onnxruntime")
ROOT = Path(__file__).resolve().parent.parent


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


# --------------------------------------------------------------------------- ETW (Windows)
def _etw_tool():
    spec = importlib.util.spec_from_file_location("etw_pruefen", ROOT / "tools" / "etw_pruefen.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etw_pruefen"] = mod
    spec.loader.exec_module(mod)
    return mod


_TRACERPT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Name="Microsoft.ML.ONNXRuntime" /><Execution ProcessID="4711" ThreadID="1" /></System>
  <EventData><Data Name="runtimeVersion">1.31.0</Data></EventData>
  <RenderingInfo Culture="de-DE"><Task>ProcessInfo</Task></RenderingInfo>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Name="Microsoft.ML.ONNXRuntime" /><Execution ProcessID="4711" ThreadID="1" /></System>
  <EventData><Data Name="registrationName">CPU</Data></EventData>
  <RenderingInfo Culture="de-DE"><Task>RegisterEpLibraryStart</Task></RenderingInfo>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Name="Microsoft.ML.ONNXRuntime" /><Execution ProcessID="4711" ThreadID="1" /></System>
  <EventData><Data Name="sessionId">1</Data><Data Name="modelFileName">layout_rf2.4.1+imf1.onnx</Data></EventData>
  <RenderingInfo Culture="de-DE"><Task>SessionCreation</Task></RenderingInfo>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Provider Name="Microsoft.ML.ONNXRuntime" /><Execution ProcessID="815" ThreadID="2" /></System>
  <EventData><Data Name="modelFileName">anderes_programm.onnx</Data></EventData>
  <RenderingInfo Culture="de-DE"><Task>SessionCreation</Task></RenderingInfo>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><Execution ProcessID="42" /></System>
</Event>
</Events>
"""


def test_etw_evaluation(tmp_path):
    """Auswertung der tracerpt-Ausgabe – nur Ereignisse des geprüften Prozesses, Lade-Ereignisse erlaubt."""
    etw = _etw_tool()
    xml = tmp_path / "ort.xml"
    xml.write_text(_TRACERPT_XML, encoding="utf-8")
    events = etw.parse_events(xml)
    assert [(e.pid, e.task, e.model) for e in events] == [
        (4711, "ProcessInfo", ""), (4711, "RegisterEpLibraryStart", ""),
        (4711, "SessionCreation", "layout_rf2.4.1+imf1.onnx"),
        (815, "SessionCreation", "anderes_programm.onnx"), (42, "", "")]
    assert [str(e) for e in etw.violations(events, 4711)] == ["SessionCreation – layout_rf2.4.1+imf1.onnx"]
    assert etw.violations(events, 9999) == []


@pytest.mark.skipif(sys.platform != "win32", reason="ETW nur unter Windows")
@pytest.mark.skipif(find_ner_model() is None, reason="Transformer-Modell für „Gründlich“ nicht installiert")
def test_windows_etw_no_model_events(tmp_path):
    """Echte ETW-Aufzeichnung: Kommandozeile mit Texterkennung und „Gründlich“ darf keine Modell-Ereignisse
    melden (z. B. wenn ein Paket wie PyMuPDF 2 beim Start eigene Modelle lädt, bevor die Abschaltung greift).
    Braucht Administratorrechte – in der CI vorhanden, lokal sonst übersprungen."""
    etw = _etw_tool()
    try:
        result = etw.check(workdir=tmp_path)
    except etw.NotPossible as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(str(exc))
    assert result["control_events"], "Gegenprobe ohne Ereignisse"
    assert not result["violations"], [str(e) for e in result["violations"]]
