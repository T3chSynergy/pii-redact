"""Namenserkennung mit einem Transformer-Modell im ONNX-Format – ohne PyTorch.

Laufzeit-Abhängigkeiten: ``onnxruntime`` und ``tokenizers`` (zusammen ~20 MB).
Ein Modellordner enthält (erzeugt von ``tools/convert_model.py``):

    model.onnx              Netz (i. d. R. 8-Bit-quantisiert)
    tokenizer.json          Tokenizer (Hugging-Face-„fast“-Format)
    config.json             u. a. id2label
    pii_redact_meta.json    Herkunft, Lizenz, max. Sequenzlänge

Unterstützt werden Token-Klassifikationsmodelle mit BIO-Labels (B-PER, I-LOC …). Labels, die
nicht auf PER/LOC/ORG abbildbar sind (MISC, OTH, *deriv, *part), werden ignoriert.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

LABEL_MAP = {"PER": "PERSON", "PERSON": "PERSON", "LOC": "LOCATION", "ORG": "ORGANIZATION"}


@dataclass
class NerSpan:
    start: int
    end: int
    entity: str   # PERSON | LOCATION | ORGANIZATION
    score: float


class NerModelMissingError(RuntimeError):
    pass


def _split_label(label: str) -> tuple[str, str]:
    """'B-PER' → ('B', 'PER'); 'PER' → ('I', 'PER'); 'O' → ('O', '')."""
    if label in ("O", ""):
        return "O", ""
    if len(label) > 2 and label[1] in "-_" and label[0] in "BIES":
        prefix, typ = label[0], label[2:]
        return ("B" if prefix in "BS" else "I"), typ
    return "I", label


def aggregate(
    text: str,
    offsets: list[tuple[int, int]],
    word_ids: list[int | None],
    special: list[int],
    label_ids: np.ndarray,
    scores: np.ndarray,
    id2label: dict[int, str],
) -> list[NerSpan]:
    """Token-Vorhersagen → Entitäten.

    Strategie „first“: Ein Wort bekommt das Label seines ersten Teil-Tokens. Danach BIO-Gruppierung
    über Wörter. So entstehen keine halben Wörter („Muster##mann“)."""
    words: list[tuple[int, int, str, float]] = []  # start, end, label, score
    current_word = None
    for i, (s, e) in enumerate(offsets):
        if special[i] or e <= s:
            continue
        wid = word_ids[i]
        if wid is not None and wid == current_word and words:
            ws, _we, lab, sc = words[-1]
            words[-1] = (ws, e, lab, sc)
            continue
        current_word = wid
        words.append((s, e, id2label.get(int(label_ids[i]), "O"), float(scores[i])))

    spans: list[NerSpan] = []
    cur: list | None = None  # [start, end, typ, [scores]]

    def close():
        nonlocal cur
        if cur is not None:
            spans.append(NerSpan(cur[0], cur[1], LABEL_MAP[cur[2]], float(np.mean(cur[3]))))
            cur = None

    for s, e, label, sc in words:
        prefix, typ = _split_label(label)
        if prefix == "O" or typ not in LABEL_MAP:
            close()
            continue
        gap = text[cur[1]:s] if cur is not None else ""
        if cur is not None and prefix == "I" and cur[2] == typ and gap.strip(" -") == "" and "\n\n" not in gap:
            cur[1] = e
            cur[3].append(sc)
        else:
            close()
            cur = [s, e, typ, [sc]]
    close()
    return spans


class OnnxNerModel:
    def __init__(self, model_dir: str | Path, threads: int | None = None, batch_size: int = 8):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        if not (model_dir / "model.onnx").is_file():
            raise NerModelMissingError(f"Kein ONNX-Modell in {model_dir}")
        self.model_dir = model_dir
        cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self.id2label = {int(k): v for k, v in cfg["id2label"].items()}
        meta = {}
        meta_file = model_dir / "pii_redact_meta.json"
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.meta = meta
        self.max_length = int(meta.get("max_length", 512))
        self.stride = min(128, self.max_length // 4)
        self.batch_size = batch_size

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(max_length=self.max_length, stride=self.stride)

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = threads or max(1, min(8, (os.cpu_count() or 2)))
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        pad = self.tokenizer.token_to_id("<pad>")
        if pad is None:
            pad = self.tokenizer.token_to_id("[PAD]")
        self.pad_id = int(pad if pad is not None else cfg.get("pad_token_id", 0) or 0)

    # ------------------------------------------------------------------
    def predict(self, text: str) -> list[NerSpan]:
        if not text.strip():
            return []
        enc = self.tokenizer.encode(text)
        windows = [enc] + list(enc.overflowing)
        spans: list[NerSpan] = []
        for b in range(0, len(windows), self.batch_size):
            batch = windows[b : b + self.batch_size]
            length = max(len(w.ids) for w in batch)
            ids = np.full((len(batch), length), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(batch), length), dtype=np.int64)
            for j, w in enumerate(batch):
                ids[j, : len(w.ids)] = w.ids
                mask[j, : len(w.ids)] = 1
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            logits = self.session.run(None, {k: v for k, v in feeds.items() if k in self.input_names})[0]
            probs = _softmax(logits)
            label_ids = probs.argmax(-1)
            scores = probs.max(-1)
            for j, w in enumerate(batch):
                n = len(w.ids)
                spans.extend(
                    aggregate(text, w.offsets, w.word_ids, w.special_tokens_mask,
                              label_ids[j, :n], scores[j, :n], self.id2label)
                )
        return _dedupe(spans)


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(-1, keepdims=True)


def _dedupe(spans: list[NerSpan]) -> list[NerSpan]:
    """Überlappende Fenster liefern Entitäten doppelt – identische Bereiche zusammenfassen."""
    best: dict[tuple[int, int, str], NerSpan] = {}
    for s in spans:
        key = (s.start, s.end, s.entity)
        if key not in best or s.score > best[key].score:
            best[key] = s
    return sorted(best.values(), key=lambda s: (s.start, s.end))
