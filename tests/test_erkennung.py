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
        for s, e, typ in spans + neutral[name]:
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
