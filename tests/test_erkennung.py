"""Erkennungsquote auf den Testtexten (samples/soll_funde.json).

Fällt auf, wenn ein Update (spaCy, Presidio, onnxruntime, Modell) die Namenserkennung verschlechtert.
Gemessen wie in tools/benchmark_models.py; die Schwellen liegen etwas unter dem Stand vom 03.10.2026
(Gründlich: Personen 99 %, Orte 96 %, 9 Fehlalarme · Schnell: Personen 80 %, Orte 79 %).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from pii_redact.paths import find_ner_model

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("benchmark_models", ROOT / "tools" / "benchmark_models.py")
bm = importlib.util.module_from_spec(spec)
sys.modules["benchmark_models"] = bm
spec.loader.exec_module(bm)


def _measure(mode: str) -> dict:
    docs, gold, neutral = bm.load_gold()
    predict, _size = bm.make_predictor(f"app:{mode}")
    result = {"docs": {name: {"spans": predict(text)} for name, text in docs.items()}}
    return bm.score(result, docs, gold, neutral)


def test_gold_data_consistent():
    """Jeder Soll-Fund ist eindeutig im Text verankert (sonst zählt der Vergleich falsch)."""
    docs, gold, neutral = bm.load_gold()
    assert len(docs) >= 9
    assert sum(map(len, gold.values())) >= 150
    for name, spans in gold.items():
        text = docs[name]
        for s, e, _typ in spans + neutral[name]:
            assert text[s:e].strip() == text[s:e] and text[s:e], (name, s, e)


def test_recognition_fast():
    r = _measure("schnell")
    assert r["PERSON"]["recall"] >= 0.75, r["misses"]
    assert r["LOCATION"]["recall"] >= 0.70, r["misses"]


@pytest.mark.skipif(find_ner_model() is None, reason="Transformer-Modell für „Gründlich“ nicht installiert")
def test_recognition_thorough():
    r = _measure("gruendlich")
    assert r["PERSON"]["recall"] >= 0.95, r["misses"]
    assert r["LOCATION"]["recall"] >= 0.90, r["misses"]
    assert r["PERSON"]["precision"] >= 0.93, r["false_pos"]
    assert len(r["false_pos"]) <= 12, r["false_pos"]


def _upper(text: str) -> str:
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in text)   # längenneutral („ß“ bleibt)


def _covered_share(mode: str) -> tuple[float, float]:
    """Anteil der Soll-Funde (Personen, Orte), die vollständig geschwärzt würden, wenn sie in
    Großbuchstaben stehen („SCHNEIDER, Anna“) – gleich welche Datenart der Fund trägt."""
    from pii_redact.core import PiiAnalyzer, Settings

    docs, gold, _neutral = bm.load_gold()
    settings = Settings(analysis_mode=mode)
    analyzer = PiiAnalyzer.for_settings(settings)
    hit = {"PERSON": 0, "LOCATION": 0}
    total = {"PERSON": 0, "LOCATION": 0}
    for name, text in docs.items():
        chars = list(text)
        for s, e, _typ in gold[name]:
            chars[s:e] = _upper(text[s:e])
        text = "".join(chars)
        covered = bytearray(len(text))
        for f in analyzer.analyze(text, settings):
            covered[f.start:f.end] = b"\x01" * (f.end - f.start)
        for s, e, typ in gold[name]:
            total[typ] += 1
            hit[typ] += all(covered[i] or text[i].isspace() for i in range(s, e))
    return hit["PERSON"] / total["PERSON"], hit["LOCATION"] / total["LOCATION"]


def test_recognition_upper_case_fast():
    """Stand 10.11.2026: Personen 65 %, Orte 69 % (ohne den Durchgang für Großschrift: 12 % und 21 %)."""
    persons, places = _covered_share("schnell")
    assert persons >= 0.55 and places >= 0.60, (persons, places)


@pytest.mark.skipif(find_ner_model() is None, reason="Transformer-Modell für „Gründlich“ nicht installiert")
def test_recognition_upper_case_thorough():
    """Stand 10.11.2026: Personen 95 %, Orte 98 % (ohne den Durchgang für Großschrift: 78 % und 79 %)."""
    persons, places = _covered_share("gruendlich")
    assert persons >= 0.90 and places >= 0.90, (persons, places)
