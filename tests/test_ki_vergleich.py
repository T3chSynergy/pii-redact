"""Modellvergleich (tools/ki_vergleich.py) gegen den lokalen Testserver."""

import importlib.util
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

from test_llm_review import FakeLLM

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ki_vergleich", ROOT / "tools" / "ki_vergleich.py")
kv = importlib.util.module_from_spec(spec)
sys.modules["ki_vergleich"] = kv  # für dataclasses nötig
spec.loader.exec_module(kv)


@pytest.fixture()
def url(monkeypatch, tmp_path):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("PII_REDACT_LLM_KEY", raising=False)
    FakeLLM.requests, FakeLLM.status, FakeLLM.echo_kennung = [], 200, True
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_cases_are_consistent():
    cases = kv.load_cases()
    assert len(cases) >= 5
    for c in cases:
        doc, findings = kv.build(c)
        assert findings, c["name"]  # jeder Fall enthält bereits Geschwärztes
        for alternatives in c["erwartet"]:
            assert any(a in c["text"] for a in alternatives), (c["name"], alternatives)
        for h in c["harmlos"]:
            assert h in c["text"], (c["name"], h)
        red = kv.lr.reviewed_text(doc, findings).text
        for item in c["schwaerzen"]:
            assert item["text"] not in red  # wirklich geschwärzt
        assert set(c["risiko"]) <= set(kv.lr.RISK_ORDER)


def test_scoring_and_report(url, tmp_path):
    case = kv.load_cases()[0]
    FakeLLM.answer = {"risiko": "hoch", "begruendung": "Funktion identifiziert.", "hinweise": [
        {"zitat": "Schwerbehindertenvertreterin im Einkauf", "art": "KONTEXT", "begruendung": "einmalig"},
        {"zitat": "Die Sitzung begann um 9 Uhr", "art": "DATUM", "begruendung": "Uhrzeit"},
        {"zitat": "Beleuchtung in Halle 2", "art": "ORT", "begruendung": "Ort"},
        {"zitat": "Frau Dr. Erfunden", "art": "PERSON", "begruendung": ""},
    ]}
    s = kv.Settings(llm_enabled=True, llm_url=url, llm_model="modell-a")
    [r] = kv.run_model(s, [case], 1, log=lambda *_: None)
    assert (r.risk_ok, r.found, r.expected, r.false_alarms, r.other, r.unmatched) == (True, 1, 1, 1, 1, 1)
    FakeLLM.echo_kennung = False
    [bad] = kv.run_model(s, [case], 1, log=lambda *_: None)
    assert "Kontextfenster" in bad.error
    text = kv.report({"modell-a": [r], "modell-b": [bad]}, s, 1)
    assert "| modell-a | 1/1 | 1/1 | 1 | 1 | 1 | 0 |" in text and "| modell-b | 0/0 | 0/0 |" in text
    assert "[FEHLALARM]" in text and "[erwartet]" in text and "Kontextfenster" in text


def test_main_writes_report_and_only_sends_redacted_text(url, tmp_path):
    FakeLLM.answer = {"risiko": "gering", "begruendung": "", "hinweise": []}
    out = tmp_path / "bericht.md"
    assert kv.main(["--url", url, "--modell", "m1", "--modell", "m2", "--ausgabe", str(out)]) == 0
    assert out.exists() and "| m1 |" in out.read_text(encoding="utf-8")
    n = len(kv.load_cases())
    assert len(FakeLLM.requests) == 2 * n
    names = [i["text"] for c in kv.load_cases() for i in c["schwaerzen"]]
    for req in FakeLLM.requests:
        sent = req["body"]["messages"][1]["content"]
        assert not any(name in sent for name in names)
    assert {r["body"]["model"] for r in FakeLLM.requests} == {"m1", "m2"}


def test_main_without_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert kv.main([]) == 2
    assert "Server-Adresse" in capsys.readouterr().out
