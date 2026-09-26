"""Tests für Modus „Gründlich“ (ONNX-Transformer) und zentrale Vorgaben."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from pii_redact.core import AnalysisMode, PiiAnalyzer, Settings, ThoroughModelMissingError
from pii_redact.core.transformer_ner import _split_label, aggregate

ROOT = Path(__file__).resolve().parent.parent
HAS_TORCH = all(importlib.util.find_spec(m) for m in ("torch", "transformers", "onnx"))


# ---------------------------------------------------------------- Aggregation (ohne Modell)
def test_split_label():
    assert _split_label("B-PER") == ("B", "PER")
    assert _split_label("I-LOC") == ("I", "LOC")
    assert _split_label("S-ORG") == ("B", "ORG")
    assert _split_label("PER") == ("I", "PER")
    assert _split_label("O") == ("O", "")


def test_aggregate_words_and_bio():
    text = "Herr Maxi Mustermann wohnt in Bad Homburg."
    offsets = [(0, 0), (0, 4), (5, 9), (10, 16), (16, 20), (21, 26), (27, 29), (30, 33), (34, 41), (41, 42), (0, 0)]
    word_ids = [None, 0, 1, 2, 2, 3, 4, 5, 6, 7, None]
    special = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    id2label = {0: "O", 1: "B-PER", 2: "I-PER", 3: "B-LOC", 4: "I-LOC", 5: "B-PERderiv"}
    # Das zweite Teil-Token von „Mustermann“ ist absichtlich O – zählt trotzdem zum Wort (Strategie „first“)
    labels = np.array([0, 0, 1, 2, 0, 0, 0, 3, 4, 0, 0])
    scores = np.full(len(labels), 0.9)
    spans = aggregate(text, offsets, word_ids, special, labels, scores, id2label)
    assert [(text[s.start:s.end], s.entity) for s in spans] == [
        ("Maxi Mustermann", "PERSON"),
        ("Bad Homburg", "LOCATION"),
    ]


def test_aggregate_ignores_unmapped_labels():
    text = "deutsche Firma"
    spans = aggregate(text, [(0, 8), (9, 14)], [0, 1], [0, 0], np.array([1, 2]), np.array([0.9, 0.9]),
                      {0: "O", 1: "B-LOCderiv", 2: "B-MISC"})
    assert spans == []


# ---------------------------------------------------------------- Vorgaben / Einstellungen
def test_defaults_and_org_lists(tmp_path):
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps({"analysis_mode": "gruendlich", "threshold": 0.6,
                                    "allow_list": ["Firma"], "deny_list": ["Falke"]}), encoding="utf-8")
    user = tmp_path / "settings.json"
    s = Settings.load(user, defaults)
    assert s.analysis_mode == AnalysisMode.THOROUGH and s.threshold == 0.6
    assert s.all_allow == ["Firma"] and s.allow_list == []
    s.threshold = 0.5
    s.allow_list = ["Privat"]
    s.save(user)
    saved = json.loads(user.read_text(encoding="utf-8"))
    assert "org_allow_list" not in saved
    s2 = Settings.load(user, defaults)
    assert s2.threshold == 0.5 and s2.all_allow == ["Firma", "Privat"] and s2.all_deny == ["Falke"]


def test_thorough_without_model_raises(monkeypatch, tmp_path):
    import pii_redact.paths as paths

    monkeypatch.setattr(paths, "list_ner_models", lambda: [])
    with pytest.raises(ThoroughModelMissingError):
        PiiAnalyzer("de_core_news_md", AnalysisMode.THOROUGH).load()


# ---------------------------------------------------------------- komplette ONNX-Kette (braucht Werkzeuge)
@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch/transformers/onnx nicht installiert (requirements-tools.txt)")
def test_convert_and_run_tiny_model(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import convert_model
    import make_test_model

    src = tmp_path / "tiny"
    make_test_model.main(src)
    out = tmp_path / "models" / "ner"
    # fp32: ONNX muss exakt dieselben Entitäten liefern wie PyTorch (Rückgabe 0)
    assert convert_model.main([str(src), "--name", "tiny", "--out", str(out), "--no-quantize"]) == 0
    model_dir = out / "tiny"
    for f in ("model.onnx", "tokenizer.json", "config.json", "pii_redact_meta.json"):
        assert (model_dir / f).is_file()

    from pii_redact.core.transformer_ner import OnnxNerModel

    model = OnnxNerModel(model_dir)
    # Langer Text → mehrere überlappende Fenster; Offsets müssen gültig bleiben
    text = ("Sehr geehrte Frau Anna Schneider, vielen Dank für Ihr Schreiben aus Hamburg. " * 40).strip()
    spans = model.predict(text)
    assert len(model.tokenizer.encode(text).overflowing) > 0
    for s in spans:
        assert 0 <= s.start < s.end <= len(text)
        assert text[s.start:s.end].strip()

    # Gepolsterter Stapel (unterschiedlich lange Fenster) muss dieselben Logits liefern wie PyTorch
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(src)
    ref_model = AutoModelForTokenClassification.from_pretrained(src).eval()
    batch = tok(["Anna Schneider aus Hamburg.", "Maximilian Bergmann arbeitet seit 2019 in Berlin bei Nordlicht."],
                return_tensors="pt", padding=True)
    with torch.no_grad():
        ref = ref_model(**batch).logits.numpy()
    feeds = {k: v.numpy() for k, v in batch.items() if k in model.input_names}
    got = model.session.run(None, feeds)[0]
    mask = batch["attention_mask"].numpy().astype(bool)
    assert np.abs(ref[mask] - got[mask]).max() < 1e-4

    # Integration: Presidio-Analyse im Modus „Gründlich“
    monkeypatch.setenv("PII_REDACT_MODEL_DIR", str(tmp_path / "models"))
    settings = Settings(analysis_mode=AnalysisMode.THOROUGH)
    analyzer = PiiAnalyzer.for_settings(settings)
    findings = analyzer.analyze("Herr Max Mustermann, IBAN DE89 3704 0044 0532 0130 00", settings)
    assert analyzer.ner_model_dir == model_dir
    assert any(f.entity_type == "IBAN_CODE" for f in findings)
