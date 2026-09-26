"""Vergleich der Namenserkennung: spaCy vs. Transformer-Modelle – auf DIESEM Rechner.

Misst Ladezeit, Analysezeit, Arbeitsspeicher und Trefferquote (Personen/Orte) gegen die
Soll-Liste samples/soll_funde.json. Jedes Modell läuft in einem eigenen Prozess, damit die
Speicherwerte sauber getrennt sind. Ergebnis: Konsole + benchmark_ergebnis.md

    python tools/benchmark_models.py                       # Standard-Kandidaten
    python tools/benchmark_models.py --nur spacy:de_core_news_md onnx:all

Kandidaten-Schreibweise:
    spacy:<modell>          spaCy-NER allein
    hf:<hf-id>              Original-Transformer (PyTorch, transformers-Pipeline)
    gliner:<hf-id>          GLiNER-Modell (PyTorch)
    onnx:<ordner>|all       umgewandelte Modelle aus models/ner (so wie im Client)
    app:schnell|gruendlich  komplette pii-redact-Analyse (Personen/Orte-Anteil);
                            „gruendlich“ läuft einmal je umgewandeltem Modell
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SAMPLES = ROOT / "samples"
GOLD_FILE = SAMPLES / "soll_funde.json"
TYPES = ("PERSON", "LOCATION")

DEFAULT = [
    "spacy:de_core_news_md",
    "hf:Davlan/xlm-roberta-base-ner-hrl",
    "hf:fhswf/bert_de_ner",
    "gliner:urchade/gliner_multi_pii-v1",
    "onnx:all",
    "app:schnell",
    "app:gruendlich",
]
LABEL_MAP = {"PER": "PERSON", "PERSON": "PERSON", "LOC": "LOCATION", "LOCATION": "LOCATION",
             "ORG": "ORGANIZATION", "ORGANIZATION": "ORGANIZATION"}


# ====================================================================== Soll-Daten
def load_gold():
    data = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    docs, gold, neutral = {}, {}, {}
    for fname, types in data.items():
        if fname.startswith("_"):
            continue
        text = (SAMPLES / fname).read_text(encoding="utf-8").replace("\r\n", "\n")
        docs[fname] = text
        gold[fname] = []
        neutral[fname] = []
        for typ, items in types.items():
            for item in items:
                ctx = item.replace("«", "").replace("»", "")
                pos = text.index(ctx)
                s = pos + item.index("«")
                e = s + (item.index("»") - item.index("«") - 1)
                (neutral if typ == "_neutral" else gold)[fname].append((s, e, typ))
    return docs, gold, neutral


# ====================================================================== Speicher
def peak_rss_mb() -> float:
    try:
        import psutil

        mi = psutil.Process().memory_info()
        if hasattr(mi, "peak_wset"):  # Windows
            return mi.peak_wset / 2**20
    except ImportError:
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        return 0.0


def dir_size_mb(path: Path) -> float:
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            total += os.path.getsize(p)
    return total / 1e6


# ====================================================================== Kandidaten
def make_predictor(spec: str):
    """Liefert (predict(text) -> [(start, end, typ)], modellgröße_mb)."""
    kind, _, name = spec.partition(":")
    if kind == "spacy":
        from pii_redact.paths import find_spacy_model
        import spacy

        ref = find_spacy_model(name) or name
        nlp = spacy.load(ref)
        size = dir_size_mb(Path(nlp.path)) if getattr(nlp, "path", None) else 0

        def predict(text):
            return [(e.start_char, e.end_char, LABEL_MAP.get(e.label_, e.label_)) for e in nlp(text).ents]

        return predict, size

    if kind == "hf":
        from huggingface_hub import snapshot_download
        from transformers import pipeline

        pipe = pipeline("token-classification", model=name, aggregation_strategy="first", device=-1)
        try:
            size = dir_size_mb(Path(snapshot_download(name, local_files_only=True)))
        except Exception:  # noqa: BLE001
            size = 0

        def predict(text):
            out = []
            for e in pipe(text):
                grp = e.get("entity_group", "")
                if grp in LABEL_MAP:
                    out.append((int(e["start"]), int(e["end"]), LABEL_MAP[grp]))
            return out

        return predict, size

    if kind == "gliner":
        from gliner import GLiNER
        from huggingface_hub import snapshot_download

        model = GLiNER.from_pretrained(name)
        try:
            size = dir_size_mb(Path(snapshot_download(name, local_files_only=True)))
        except Exception:  # noqa: BLE001
            size = 0
        labels = {"person": "PERSON", "location": "LOCATION", "organization": "ORGANIZATION"}

        def predict(text):
            out, pos = [], 0
            for para in text.split("\n\n"):  # GLiNER: begrenztes Kontextfenster
                start = text.index(para, pos) if para else pos
                pos = start + len(para)
                if not para.strip():
                    continue
                for e in model.predict_entities(para, list(labels), threshold=0.5):
                    out.append((start + e["start"], start + e["end"], labels[e["label"]]))
            return out

        return predict, size

    if kind == "onnx":
        from pii_redact.core.transformer_ner import OnnxNerModel

        model = OnnxNerModel(name)
        size = dir_size_mb(Path(name))

        def predict(text):
            return [(s.start, s.end, s.entity) for s in model.predict(text)]

        return predict, size

    if kind == "app":
        from pii_redact.core import AnalysisMode, PiiAnalyzer, Settings

        settings = Settings(entities=["PERSON", "LOCATION", "DE_ADDRESS"])
        settings.analysis_mode = AnalysisMode.THOROUGH if name.startswith("gr") else AnalysisMode.FAST
        settings.ner_model = name.partition("@")[2]
        analyzer = PiiAnalyzer.for_settings(settings)
        analyzer.load()

        def predict(text):
            out = []
            for f in analyzer.analyze(text, settings):
                typ = "LOCATION" if f.entity_type == "DE_ADDRESS" else f.entity_type
                out.append((f.start, f.end, typ))
            return out

        return predict, 0

    raise SystemExit(f"Unbekannter Kandidat: {spec}")


def run_single(spec: str) -> dict:
    """Wird im Unterprozess ausgeführt."""
    if spec.split(":")[0] in ("spacy", "onnx", "app"):
        # Wie im ausgelieferten Client: ohne PyTorch/transformers. Presidio würde sie sonst beim
        # Import mitladen, sobald sie installiert sind – das verfälscht die Speicherwerte.
        for mod in ("torch", "transformers", "gliner"):
            sys.modules[mod] = None
    docs, _gold, _neutral = load_gold()
    base = peak_rss_mb()
    t = time.time()
    predict, size = make_predictor(spec)
    load_s = time.time() - t
    predict("Aufwärmen: Herr Max Mustermann wohnt in Köln.")
    result = {"spec": spec, "load_s": load_s, "size_mb": size, "docs": {}}
    total_t, total_chars = 0.0, 0
    for fname, text in docs.items():
        t = time.time()
        spans = predict(text)
        dt_ = time.time() - t
        total_t += dt_
        total_chars += len(text)
        result["docs"][fname] = {"time_s": dt_, "spans": spans}
    result["analyze_s"] = total_t
    result["ms_per_1000_chars"] = 1000 * total_t / max(total_chars, 1) * 1000
    result["ram_mb"] = max(0.0, peak_rss_mb() - base)
    return result


# ====================================================================== Auswertung
def overlaps(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def score(result: dict, docs, gold, neutral) -> dict:
    stats = {t: {"tp": 0, "gold": 0, "pred": 0, "correct_pred": 0} for t in TYPES}
    misses, false_pos = [], []
    for fname, text in docs.items():
        preds = [tuple(p) for p in result["docs"][fname]["spans"] if p[2] in TYPES]
        for g in gold[fname]:
            stats[g[2]]["gold"] += 1
            if any(p[2] == g[2] and overlaps(p, g) for p in preds):
                stats[g[2]]["tp"] += 1
            else:
                misses.append(f"{text[g[0]:g[1]]} ({g[2][:3]}, {fname})")
        for p in preds:
            if any(overlaps(p, n) for n in neutral[fname]):
                continue
            stats[p[2]]["pred"] += 1
            if any(g[2] == p[2] and overlaps(p, g) for g in gold[fname]):
                stats[p[2]]["correct_pred"] += 1
            else:
                false_pos.append(f"{text[p[0]:p[1]].strip()!r} als {p[2][:3]} ({fname})")
    out = {}
    for t in TYPES:
        s = stats[t]
        out[t] = {
            "recall": s["tp"] / s["gold"] if s["gold"] else 0.0,
            "precision": s["correct_pred"] / s["pred"] if s["pred"] else 0.0,
        }
    out["misses"] = misses
    out["false_pos"] = false_pos
    return out


def expand(specs: list[str]) -> list[str]:
    out = []
    for spec in specs:
        if spec == "onnx:all":
            from pii_redact.paths import list_ner_models

            out.extend(f"onnx:{p}" for p in list_ner_models())
        elif spec == "app:gruendlich":
            from pii_redact.paths import list_ner_models

            out.extend(f"app:gruendlich@{p.name}" for p in list_ner_models())
        else:
            out.append(spec)
    return out


def system_info() -> str:
    lines = [f"- Datum: {dt.datetime.now():%d.%m.%Y %H:%M}",
             f"- System: {platform.system()} {platform.release()} ({platform.machine()})",
             f"- Prozessor: {platform.processor() or 'unbekannt'} · {os.cpu_count()} logische Kerne",
             f"- Python: {platform.python_version()}"]
    try:
        import psutil

        lines.append(f"- Arbeitsspeicher: {psutil.virtual_memory().total / 2**30:.1f} GB")
    except ImportError:
        pass
    return "\n".join(lines)


def label(spec: str) -> str:
    kind, _, name = spec.partition(":")
    if kind == "onnx":
        return f"onnx:{Path(name).name}"
    return spec


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nur", nargs="+", help="nur diese Kandidaten")
    p.add_argument("--single", help=argparse.SUPPRESS)
    p.add_argument("--bericht", type=Path, default=ROOT / "benchmark_ergebnis.md")
    args = p.parse_args(argv)

    if args.single:
        print("__RESULT__" + json.dumps(run_single(args.single)))
        return 0

    docs, gold, neutral = load_gold()
    specs = expand(args.nur or DEFAULT)
    rows = []
    for spec in specs:
        print(f"▶ {label(spec)} …", flush=True)
        proc = subprocess.run([sys.executable, __file__, "--single", spec], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        line = next((l for l in proc.stdout.splitlines() if l.startswith("__RESULT__")), None)
        if line is None:
            err = (proc.stderr.strip().splitlines() or ["unbekannter Fehler"])[-1]
            print(f"  ✗ übersprungen: {err}")
            rows.append({"spec": spec, "error": err})
            continue
        res = json.loads(line[len("__RESULT__"):])
        res["score"] = score(res, docs, gold, neutral)
        rows.append(res)
        sc = res["score"]
        print(f"  Laden {res['load_s']:.1f}s · Analyse {res['analyze_s']:.2f}s · RAM +{res['ram_mb']:.0f} MB · "
              f"Personen {sc['PERSON']['recall']:.0%} gefunden · Orte {sc['LOCATION']['recall']:.0%} gefunden")

    report = render(rows, docs)
    args.bericht.write_text(report, encoding="utf-8")
    print(f"\nBericht: {args.bericht}")
    return 0


def render(rows, docs) -> str:
    chars = sum(len(t) for t in docs.values())
    out = ["# Vergleich Namenserkennung", "", system_info(),
           f"- Testtexte: {', '.join(docs)} ({chars} Zeichen)", "",
           "| Kandidat | Größe | Laden | Analyse | ms/1000 Zeichen | RAM | Personen gefunden | Personen korrekt | Orte gefunden | Orte korrekt |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            out.append(f"| {label(r['spec'])} | – | – | – | – | – | Fehler: {r['error'][:80]} | | | |")
            continue
        s = r["score"]
        size = f"{r['size_mb']:.0f} MB" if r["size_mb"] else "–"
        out.append(
            f"| {label(r['spec'])} | {size} | {r['load_s']:.1f} s | {r['analyze_s']:.2f} s | {r['ms_per_1000_chars']:.0f} "
            f"| +{r['ram_mb']:.0f} MB | {s['PERSON']['recall']:.0%} | {s['PERSON']['precision']:.0%} "
            f"| {s['LOCATION']['recall']:.0%} | {s['LOCATION']['precision']:.0%} |"
        )
    out += ["", "„gefunden“ = Anteil der Soll-Funde, die erkannt wurden. „korrekt“ = Anteil der Treffer, die "
            "wirklich Personen/Orte sind (Rest = Fehlalarme).", ""]
    for r in rows:
        if "error" in r:
            continue
        s = r["score"]
        out += [f"## {label(r['spec'])}", "",
                f"**Übersehen ({len(s['misses'])}):** " + (", ".join(s["misses"]) or "–"), "",
                f"**Fehlalarme ({len(s['false_pos'])}):** " + (", ".join(s["false_pos"]) or "–"), ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
