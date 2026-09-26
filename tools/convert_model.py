"""Transformer-NER-Modell von Hugging Face → ONNX für pii-redact.

Nur für die Entwicklung/den Build nötig – braucht PyTorch + transformers (requirements-tools.txt).
Der ausgelieferte Client benötigt danach nur noch onnxruntime + tokenizers.

    python tools/convert_model.py Davlan/xlm-roberta-base-ner-hrl
    python tools/convert_model.py fhswf/bert_de_ner --name fhswf-bert-de
    python tools/convert_model.py C:\\pfad\\zu\\lokalem\\modell --name eigenes
    python tools/convert_model.py Davlan/xlm-roberta-base-ner-hrl --quant embed --name davlan-emb

Quantisierung (--quant):
    int8   alle Gewichtsmatrizen 8-Bit (kleinste Datei, schnellste Analyse, etwas ungenauer)
    embed  nur die Wort-Einbettungen 8-Bit, Rechenwerk bleibt 32-Bit (fast verlustfrei;
           bei XLM-R machen die Einbettungen ~70 % der Größe aus)
    none   unverändert 32-Bit (groß)

Ergebnis: models/ner/<name>/{model.onnx, tokenizer.json, config.json, pii_redact_meta.json, MODEL_CARD.md}
Am Ende wird geprüft, ob das ONNX-Modell dieselben Entitäten findet wie das Original.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
import warnings
from pathlib import Path

# Harmlose Hinweise von PyTorch/transformers beim Export ausblenden (Ausgabe bleibt lesbar)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*TracerWarning.*")
warnings.filterwarnings("ignore", message=".*Converting a tensor to a Python boolean.*")
warnings.filterwarnings("ignore", message=".*aten::index.*")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHECK_SENTENCES = [
    "Sehr geehrte Frau Anna Schneider, vielen Dank für Ihr Schreiben aus Hamburg.",
    "Maximilian Bergmann arbeitet seit 2019 bei der Nordlicht Software GmbH in Berlin.",
    "Laut Adeyemi hat Herr Grünewald aus Wanne-Eickel bereits zugestimmt.",
    "Die Vertretung übernimmt Dr. Ayşe Yıldırım-Schäfer vom Klinikum Nord in Köln.",
    "Frank Winter bat darum, dass Paul Koch und Sabine Bauer die Unterlagen an Lan schicken.",
]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model", help="Hugging-Face-ID oder lokaler Ordner")
    p.add_argument("--name", help="Ordnername unter models/ner (Standard: aus der ID abgeleitet)")
    p.add_argument("--out", type=Path, default=ROOT / "models" / "ner")
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--quant", choices=["int8", "embed", "none"], default="int8", help="Quantisierung (s. o.)")
    p.add_argument("--no-quantize", action="store_true", help="= --quant none")
    p.add_argument("--opset", type=int, default=17)
    args = p.parse_args(argv)
    if args.no_quantize:
        args.quant = "none"
    try:
        import torch.jit  # noqa: F401
        from torch.jit import TracerWarning

        warnings.filterwarnings("ignore", category=TracerWarning)
    except ImportError:
        pass

    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    name = args.name or args.model.rstrip("/\\").replace("\\", "/").split("/")[-1].lower()
    target = args.out / name
    tmp = args.out / f".{name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    print(f"[1/5] Lade {args.model} …")
    tok = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if not getattr(tok, "is_fast", False):
        sys.exit("Für dieses Modell gibt es keinen „fast“-Tokenizer – nicht unterstützt.")
    model = AutoModelForTokenClassification.from_pretrained(args.model)
    model.eval()
    max_len = min(args.max_length, int(getattr(tok, "model_max_length", 512) or 512), 512)

    print("[2/5] Speichere Tokenizer und Konfiguration …")
    tok.backend_tokenizer.save(str(tmp / "tokenizer.json"))
    model.config.to_json_file(str(tmp / "config.json"))

    print("[3/5] Exportiere nach ONNX …")
    sample = tok(CHECK_SENTENCES[:2], return_tensors="pt", padding=True)
    input_names = [n for n in ("input_ids", "attention_mask", "token_type_ids") if n in sample]
    dynamic = {n: {0: "batch", 1: "seq"} for n in input_names}
    dynamic["logits"] = {0: "batch", 1: "seq"}
    fp32 = tmp / "model_fp32.onnx"

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, *inputs):
            kwargs = dict(zip(input_names, inputs))
            return self.m(**kwargs).logits

    with torch.no_grad():
        torch.onnx.export(
            Wrapper(model),
            tuple(sample[n] for n in input_names),
            str(fp32),
            input_names=input_names,
            output_names=["logits"],
            dynamic_axes=dynamic,
            opset_version=args.opset,
            dynamo=False,
        )

    final = tmp / "model.onnx"
    if args.quant == "none":
        fp32.rename(final)
        print("[4/5] Keine Quantisierung (32-Bit).")
    else:
        print(f"[4/5] Quantisiere ({args.quant}) …")
        fp32_size = fp32.stat().st_size
        quantize(fp32, final, args.quant)
        for f in tmp.glob("model_*.onnx"):
            f.unlink()
        print(f"      {fp32_size / 1e6:.0f} MB → {final.stat().st_size / 1e6:.0f} MB")

    meta = {
        "source": args.model,
        "revision": getattr(model.config, "_commit_hash", None),
        "license": _license(args.model),
        "labels": sorted(set(model.config.id2label.values())),
        "max_length": max_len,
        "quantized": args.quant != "none",
        "quantization": args.quant,
        "converted": dt.date.today().isoformat(),
        "converter": "pii-redact tools/convert_model.py",
    }
    (tmp / "pii_redact_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    _copy_card(args.model, tmp)

    print("[5/5] Prüfe ONNX gegen Original …")
    model.eval()  # torch.onnx.export hinterlässt das Modell ggf. im Trainingsmodus (Dropout!)
    check = _verify(tok, model, tmp, max_len)
    meta["verification"] = check
    (tmp / "pii_redact_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    if check["token_agreement"] < 0.9:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"\nFEHLER: Das ONNX-Modell weicht stark vom Original ab ({check['token_agreement']:.1%} gleiche "
              "Token-Labels) – es wurde NICHT installiert.")
        return 2
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    print(f"\nFertig: {target}")
    print(f"Lizenz laut Modellseite: {meta['license'] or 'unbekannt – bitte prüfen!'}")
    if check["token_agreement"] < 0.995 and args.quant != "none":
        print(
            f"\nHINWEIS: Durch die Quantisierung weichen {1 - check['token_agreement']:.1%} der Token-Labels vom "
            "Original ab. Ob das die Erkennung spürbar verschlechtert, zeigt benchmark_models.py "
            "(Vergleich hf:… gegen onnx:…). Alternative: --quant embed (fast verlustfrei, größer)."
        )
    return 0


def _classifier_nodes(model_path: Path) -> list[str]:
    """Name(n) der letzten MatMul/Gemm-Knoten vor „logits“ – der Klassifikationskopf ist klein,
    aber empfindlich; er bleibt bei der Quantisierung in 32 Bit."""
    import onnx

    graph = onnx.load(str(model_path), load_external_data=False).graph
    producer = {out: node for node in graph.node for out in node.output}
    todo, seen, found = ["logits"], set(), []
    for _ in range(6):
        nxt = []
        for name in todo:
            node = producer.get(name)
            if node is None or node.name in seen:
                continue
            seen.add(node.name)
            if node.op_type in ("MatMul", "Gemm"):
                found.append(node.name)
            else:
                nxt.extend(node.input)
        if found:
            break
        todo = nxt
    return found


def quantize(fp32: Path, out: Path, mode: str) -> None:
    import logging

    from onnxruntime.quantization import QuantType, quantize_dynamic

    logging.getLogger().setLevel(logging.ERROR)  # „please consider pre-processing“ – erledigen wir selbst
    import os

    src = fp32
    pre = fp32.with_name("model_pre.onnx")
    # quant_pre_process legt Zwischendateien (sym_shape_infer_temp.onnx, <uuid>.data) im
    # aktuellen Arbeitsordner ab – deshalb im temporären Modellordner arbeiten und aufräumen.
    cwd = os.getcwd()
    os.chdir(fp32.parent)
    try:
        from onnxruntime.quantization.shape_inference import quant_pre_process

        try:
            quant_pre_process(str(fp32), str(pre))
        except Exception:  # noqa: BLE001 – symbolische Formerkennung scheitert bei manchen Modellen
            quant_pre_process(str(fp32), str(pre), skip_symbolic_shape=True)
        src = pre
    except Exception as exc:  # noqa: BLE001
        print(f"      (Vorverarbeitung übersprungen: {exc})")
    finally:
        os.chdir(cwd)
        for junk in list(fp32.parent.glob("sym_shape_infer_temp*")) + list(fp32.parent.glob("*.data")):
            junk.unlink(missing_ok=True)

    if mode == "embed":
        quantize_dynamic(str(src), str(out), weight_type=QuantType.QUInt8, op_types_to_quantize=["Gather"])
    else:
        exclude = _classifier_nodes(src)
        quantize_dynamic(
            str(src),
            str(out),
            weight_type=QuantType.QInt8,
            per_channel=True,
            op_types_to_quantize=["MatMul", "Gather"],
            nodes_to_exclude=exclude,
            extra_options={"MatMulConstBOnly": True},
        )


def _license(model_id: str) -> str | None:
    if Path(model_id).exists():
        return None
    try:
        from huggingface_hub import model_info

        info = model_info(model_id)
        return (info.card_data or {}).get("license") if info.card_data else None
    except Exception:  # noqa: BLE001
        return None


def _copy_card(model_id: str, out: Path) -> None:
    src = Path(model_id) / "README.md"
    try:
        if not src.exists():
            from huggingface_hub import hf_hub_download

            src = Path(hf_hub_download(model_id, "README.md"))
        shutil.copy(src, out / "MODEL_CARD.md")
    except Exception:  # noqa: BLE001
        pass


def _verify(tok, model, model_dir: Path, max_len: int = 512) -> dict:
    """Vergleicht PyTorch und ONNX: Entitäten auf Beispielsätzen + Token-Labels auf allen Beispieltexten."""
    import torch

    from pii_redact.core.transformer_ner import OnnxNerModel, aggregate

    onnx_model = OnnxNerModel(model_dir)
    agree = total = 0
    t_onnx = t_torch = 0.0
    for sent in CHECK_SENTENCES:
        t = time.time()
        ours = {(s.start, s.end, s.entity) for s in onnx_model.predict(sent)}
        t_onnx += time.time() - t

        t = time.time()
        enc = tok(sent, return_tensors="pt", return_offsets_mapping=True)
        offsets = enc.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = model(**enc).logits[0]
        t_torch += time.time() - t
        # gleiche Aggregation wie im Client, nur mit PyTorch-Logits
        probs = torch.softmax(logits, -1).numpy()
        special = [1 if (s == e) else 0 for s, e in offsets]
        ref = {
            (s.start, s.end, s.entity)
            for s in aggregate(sent, [tuple(o) for o in offsets], enc.word_ids(), special,
                               probs.argmax(-1), probs.max(-1), model.config.id2label)
        }
        both = ours | ref
        total += len(both)
        agree += len(ours & ref)
        if ours != ref:
            print(f"      Abweichung: {sent}\n        PyTorch: {sorted(ref)}\n        ONNX:    {sorted(ours)}")
        else:
            names = ", ".join(f"{sent[s:e]} ({t[:3]})" for s, e, t in sorted(ours))
            print(f"      ✓ {names or '(keine Entitäten)'}")
    # Token-Labels auf allen Beispieltexten, als gepolsterter Stapel (so rechnet der Client)
    texts = list(CHECK_SENTENCES)
    for f in sorted((ROOT / "samples").glob("*.txt")) + sorted((ROOT / "samples").glob("*.md")):
        texts.extend(p for p in f.read_text(encoding="utf-8").split("\n\n") if p.strip())
    same_tokens = all_tokens = 0
    for i in range(0, len(texts), 8):
        batch = tok(texts[i:i + 8], return_tensors="pt", padding=True, truncation=True, max_length=max_len)
        with torch.no_grad():
            ref_logits = model(**batch).logits.numpy()
        feeds = {k: v.numpy() for k, v in batch.items() if k in onnx_model.input_names}
        got = onnx_model.session.run(None, feeds)[0]
        mask = batch["attention_mask"].numpy().astype(bool)
        same_tokens += int((ref_logits[mask].argmax(-1) == got[mask].argmax(-1)).sum())
        all_tokens += int(mask.sum())
    token_rate = same_tokens / max(all_tokens, 1)
    rate = agree / total if total else 1.0
    print(f"      Token-Labels gleich: {token_rate:.1%} von {all_tokens} Tokens · Entitäten gleich: {rate:.0%}")
    print(f"      Tempo Beispielsätze: ONNX {t_onnx * 1000:.0f} ms · PyTorch {t_torch * 1000:.0f} ms")
    return {"token_agreement": round(token_rate, 4), "entity_agreement": round(rate, 4), "tokens": all_tokens}


if __name__ == "__main__":
    sys.exit(main())
