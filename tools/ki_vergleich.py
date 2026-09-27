"""Vergleich von Sprachmodellen für die KI-Nachprüfung – mit festen, frei erfundenen Testfällen.

Jedes Modell bekommt dieselben geschwärzten Testdokumente (tools/ki_testfaelle.json), genau so, wie das
Programm sie senden würde. Gezählt wird:

* Risiko passend    – gemeldetes Restrisiko liegt im erwarteten Bereich
* Gefunden          – erwartete Stellen (z. B. „Schwerbehindertenvertreterin im Einkauf …“) gemeldet
* Fehlalarme        – ausdrücklich harmlose Stellen gemeldet
* Sonstige          – weitere Hinweise (nicht unbedingt falsch, bitte im Bericht ansehen)
* Ohne Beleg        – Hinweise, deren Zitat nicht wörtlich im Text steht (werden im Programm verworfen)
* Fehler            – Anfrage fehlgeschlagen (Zeitüberschreitung, kein JSON, Prüfkennung fehlt …)

Server-Adresse und Schlüssel kommen aus den Einstellungen des Programms (bzw. defaults.json und
Umgebungsvariable PII_REDACT_LLM_KEY) oder aus den Parametern:

    python tools/ki_vergleich.py --modell qwen/qwen3.8-27b --modell google/gemma-4-31b
    python tools/ki_vergleich.py --url https://llm.intern.example/v1 --modell modell-a --wiederholungen 3

Ergebnis: Konsole + ki_vergleich_<Datum>.md im Projektordner. Nur die erfundenen Testfälle werden
gesendet – keine echten Dokumente.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pii_redact.core import Finding, LoadedDocument, Settings  # noqa: E402
from pii_redact.core import llm_review as lr  # noqa: E402

CASES_FILE = Path(__file__).with_name("ki_testfaelle.json")


@dataclass
class CaseResult:
    case: str
    risk: str = ""
    risk_ok: bool = False
    expected: int = 0
    found: int = 0
    false_alarms: int = 0
    other: int = 0
    unmatched: int = 0
    error: str = ""
    seconds: float = 0.0
    hints: list[str] = field(default_factory=list)


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["faelle"]


def build(case: dict) -> tuple[LoadedDocument, list[Finding]]:
    """Testdokument und die bereits vorhandenen Schwärzungen."""
    text = case["text"]
    doc = LoadedDocument(path=Path(f"{case['name']}.txt"), kind="txt", text=text)
    findings = []
    for item in case.get("schwaerzen", []):
        for m in re.finditer(re.escape(item["text"]), text):
            findings.append(Finding(m.start(), m.end(), item.get("typ", "PERSON"), item["text"]))
    return doc, findings


def _spans(text: str, needles: list[str]) -> list[tuple[int, int]]:
    out = []
    for n in needles:
        out += [(m.start(), m.end()) for m in re.finditer(re.escape(n), text)]
    return out


def _overlaps(a: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(a[0] < e and s < a[1] for s, e in spans)


def score(case: dict, res: lr.ReviewResult) -> CaseResult:
    text = case["text"]
    out = CaseResult(case["name"], risk=res.risk, risk_ok=res.risk in case.get("risiko", []),
                     expected=len(case.get("erwartet", [])), seconds=res.seconds)
    hint_spans = [sp for h in res.hints for sp in h.spans]
    for alternatives in case.get("erwartet", []):
        if any(_overlaps(sp, _spans(text, alternatives)) for sp in hint_spans):
            out.found += 1
    harmless = _spans(text, case.get("harmlos", []))
    expected_spans = [sp for alt in case.get("erwartet", []) for sp in _spans(text, alt)]
    for h in res.hints:
        if not h.found:
            out.unmatched += 1
            label = "ohne Beleg"
        elif any(_overlaps(sp, harmless) for sp in h.spans):
            out.false_alarms += 1
            label = "FEHLALARM"
        elif any(_overlaps(sp, expected_spans) for sp in h.spans):
            label = "erwartet"
        else:
            out.other += 1
            label = "sonstig"
        out.hints.append(f"[{label}] „{h.quote}“ ({h.kind})" + (f" – {h.reason}" if h.reason else ""))
    return out


def run_model(settings: Settings, cases: list[dict], repeats: int, log=print) -> list[CaseResult]:
    results = []
    for rep in range(repeats):
        for case in cases:
            doc, findings = build(case)
            t = time.time()
            try:
                r = score(case, lr.review(doc, findings, settings))
            except lr.LlmError as exc:
                r = CaseResult(case["name"], expected=len(case.get("erwartet", [])), error=str(exc),
                               seconds=time.time() - t)
            results.append(r)
            state = f"Fehler: {r.error[:70]}" if r.error else (
                f"Risiko {r.risk:7s} {'ok ' if r.risk_ok else 'ABW'}  gefunden {r.found}/{r.expected}  "
                f"Fehlalarme {r.false_alarms}  sonstige {r.other}  ohne Beleg {r.unmatched}")
            log(f"  {'[' + str(rep + 1) + '] ' if repeats > 1 else ''}{case['name'][:28]:28s} {r.seconds:5.0f} s  {state}")
    return results


def summary(results: list[CaseResult]) -> dict:
    ok = [r for r in results if not r.error]
    return {
        "risk_ok": sum(r.risk_ok for r in ok), "runs": len(results),
        "found": sum(r.found for r in ok), "expected": sum(r.expected for r in ok),
        "false_alarms": sum(r.false_alarms for r in ok), "other": sum(r.other for r in ok),
        "unmatched": sum(r.unmatched for r in ok), "errors": len(results) - len(ok),
        "avg_s": sum(r.seconds for r in ok) / len(ok) if ok else 0.0,
    }


def report(models: dict[str, list[CaseResult]], settings: Settings, repeats: int) -> str:
    now = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"# KI-Nachprüfung – Modellvergleich ({now})", "",
             f"Server: {lr.host_of(settings)} · Testfälle: {CASES_FILE.name} · Wiederholungen: {repeats}", "",
             "| Modell | Risiko passend | Gefunden | Fehlalarme | Sonstige | Ohne Beleg | Fehler | Ø Zeit |",
             "|---|---|---|---|---|---|---|---|"]
    for name, results in models.items():
        s = summary(results)
        lines.append(f"| {name} | {s['risk_ok']}/{s['runs'] - s['errors']} | {s['found']}/{s['expected']} | "
                     f"{s['false_alarms']} | {s['other']} | {s['unmatched']} | {s['errors']} | {s['avg_s']:.0f} s |")
    lines += ["", "*Risiko passend* = gemeldetes Restrisiko im erwarteten Bereich · *Gefunden* = erwartete Stellen "
              "gemeldet · *Fehlalarme* = ausdrücklich harmlose Stellen gemeldet · *Sonstige* = weitere Hinweise "
              "(im Einzelnen ansehen) · *Ohne Beleg* = Zitat steht nicht wörtlich im Text (verwirft das Programm).",
              ""]
    for name, results in models.items():
        lines += [f"## {name}", ""]
        for r in results:
            if r.error:
                lines += [f"**{r.case}** – Fehler: {r.error}", ""]
                continue
            lines.append(f"**{r.case}** – Risiko {r.risk} ({'passend' if r.risk_ok else 'abweichend'}), "
                         f"gefunden {r.found}/{r.expected}, {r.seconds:.0f} s")
            lines += [f"- {h}" for h in r.hints] or ["- (keine Hinweise)"]
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    from pii_redact.settings_store import defaults_path, settings_path

    ap = argparse.ArgumentParser(description="Sprachmodelle für die KI-Nachprüfung vergleichen.")
    ap.add_argument("--modell", action="append", help="Modellname (mehrfach möglich); Standard: aus den Einstellungen")
    ap.add_argument("--url", help="Server-Adresse; Standard: aus den Einstellungen")
    ap.add_argument("--schluessel", help=f"API-Schlüssel; besser Umgebungsvariable {lr.KEY_ENV}")
    ap.add_argument("--wiederholungen", type=int, default=1, help="Durchläufe je Modell (Beständigkeit prüfen)")
    ap.add_argument("--faelle", type=Path, default=CASES_FILE, help="eigene Testfall-Datei (gleiches Format)")
    ap.add_argument("--ausgabe", type=Path, help="Berichtsdatei (Standard: ki_vergleich_<Datum>.md)")
    args = ap.parse_args(argv)

    settings = Settings.load(settings_path(), defaults_path())
    settings.llm_enabled = True
    if args.url:
        settings.llm_url = args.url
    if args.schluessel:
        settings.llm_api_key = args.schluessel
    models = args.modell or ([settings.llm_model] if settings.llm_model else [])
    if not lr.endpoint(settings) or not models:
        print("Bitte Server-Adresse (--url) und mindestens ein Modell (--modell) angeben "
              "oder die KI-Nachprüfung in den Einstellungen einrichten.")
        return 2
    cases = load_cases(args.faelle)
    print(f"Server {lr.host_of(settings)} · {len(cases)} Testfälle · {len(models)} Modell(e) · "
          f"{args.wiederholungen} Durchlauf/Durchläufe\n")
    results: dict[str, list[CaseResult]] = {}
    for name in models:
        print(name)
        settings.llm_model = name
        results[name] = run_model(settings, cases, max(1, args.wiederholungen))
        s = summary(results[name])
        print(f"  → Risiko passend {s['risk_ok']}/{s['runs'] - s['errors']}, gefunden {s['found']}/{s['expected']}, "
              f"Fehlalarme {s['false_alarms']}, Fehler {s['errors']}, Ø {s['avg_s']:.0f} s\n")
    out = args.ausgabe or ROOT / f"ki_vergleich_{dt.datetime.now():%Y-%m-%d_%H%M}.md"
    out.write_text(report(results, settings, max(1, args.wiederholungen)), encoding="utf-8")
    print(f"Bericht: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
