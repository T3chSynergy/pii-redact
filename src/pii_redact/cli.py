"""Kommandozeile für Stapelverarbeitung ohne Oberfläche.

Beispiel:
    pii-redact-cli dokument.pdf                 → dokument_geschwaerzt.pdf
    pii-redact-cli notizen.md --modus nummeriert → notizen_anonymisiert.md
    pii-redact-cli dokument.pdf --als-text      → dokument_anonymisiert.txt
    pii-redact-cli C:\\Akten -o C:\\Akten_geschwaerzt   → ganzer Ordner (automatisch, NICHT geprüft)

Rückgabewerte: 0 = OK, 1 = Fehler, 2 = Kontrolle fand Reste im Ergebnis,
               3 = Dateien mit Texterkennung (OCR) wurden nicht automatisch exportiert (Ordner)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

for _var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    os.environ.setdefault(_var, "1")  # nie etwas aus dem Internet nachladen

from .core import (
    AnalysisMode,
    ModelMissingError,
    ThoroughModelMissingError,
    PiiAnalyzer,
    ReplaceMode,
    Settings,
    UnsupportedFileError,
    load_document,
    redact_pdf,
    redact_text,
    verify_pdf,
)
from .core.entities import info
from .settings_store import defaults_path, settings_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pii-redact-cli", description="Personenbezogene Daten erkennen und entfernen.")
    p.add_argument("dateien", nargs="*", type=Path, help="PDF-, TXT- oder Markdown-Dateien oder Ordner")
    p.add_argument("--ohne-unterordner", action="store_true", help="bei Ordnern nur die oberste Ebene")
    p.add_argument("-o", "--ausgabe", type=Path, help="Ausgabeordner (Standard: neben der Quelldatei)")
    p.add_argument("--modus", choices=[ReplaceMode.PLACEHOLDER, ReplaceMode.NUMBERED, ReplaceMode.BLACKOUT])
    p.add_argument("--gruendlich", action="store_true", help="Modus „Gründlich“ erzwingen (Standard; Fehler, wenn das Modell fehlt)")
    p.add_argument("--schnell", action="store_true", help="Modus „Schnell“ (nur spaCy + Muster – weniger Treffer, weniger Speicher)")
    p.add_argument("--selftest", action="store_true", help="Beispieldokument analysieren und Installation prüfen")
    p.add_argument("--schwelle", type=float, help="Mindest-Score 0–1 (Standard aus Einstellungen)")
    p.add_argument("--als-text", action="store_true", help="PDFs als anonymisierten Text statt als PDF ausgeben")
    p.add_argument("--nur-anzeigen", action="store_true", help="Funde nur auflisten, nichts schreiben")
    p.add_argument("--ohne-ocr", action="store_true", help="gescannte PDF-Seiten nicht per Texterkennung lesen")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    settings = Settings.load(settings_path(), defaults_path())
    if args.ohne_ocr:
        settings.ocr = False
    if args.gruendlich:
        settings.analysis_mode = AnalysisMode.THOROUGH
    if args.schnell:
        settings.analysis_mode = AnalysisMode.FAST
    if not args.gruendlich and settings.analysis_mode == AnalysisMode.THOROUGH:
        from .paths import find_ner_model

        if find_ner_model(settings.ner_model) is None:
            print("Hinweis: Modus „Gründlich“ nicht verfügbar (kein Transformer-Modell) – verwende „Schnell“.",
                  file=sys.stderr)
            settings.analysis_mode = AnalysisMode.FAST
    if args.selftest:
        return selftest(settings)
    if not args.dateien:
        p.error("Bitte mindestens eine Datei angeben.")
    if args.modus:
        settings.replace_mode = args.modus
    if args.schwelle is not None:
        settings.threshold = args.schwelle

    analyzer = PiiAnalyzer.for_settings(settings)
    rc = 0
    for path in args.dateien:
        if path.is_dir():
            rc = process_folder(path, args, settings, analyzer) or rc
            continue
        try:
            doc = load_document(path, ocr=settings.ocr)
            findings = analyzer.analyze(doc.text, settings)
        except (UnsupportedFileError, ModelMissingError, ThoroughModelMissingError, OSError) as exc:
            print(f"✗ {path}: {exc}", file=sys.stderr)
            rc = 1
            continue

        for w in doc.warnings:
            print(f"  ⚠ {w}")
        print(f"{path}: {len(findings)} Funde")
        for f in findings:
            page = doc.page_of(f.start)
            where = f"S. {page + 1}" if page is not None else f"Z. {doc.text.count(chr(10), 0, f.start) + 1}"
            print(f"  {where:>7}  {info(f.entity_type).label:<22} {f.score:.2f}  {f.text!r}")
        if args.nur_anzeigen:
            continue

        out_dir = args.ausgabe or path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        if doc.is_pdf and not args.als_text:
            target = out_dir / f"{path.stem}_geschwaerzt.pdf"
            data = redact_pdf(doc, findings, settings.replace_mode, settings.pdf_labels)
            target.write_bytes(data)
            rest = verify_pdf(data, findings, doc.ocr_pages)
            if rest:
                print(f"  ⚠ Im Ergebnis noch vorhanden: {'; '.join(rest)}", file=sys.stderr)
                rc = 2
        else:
            suffix = ".txt" if doc.is_pdf else path.suffix
            target = out_dir / f"{path.stem}_anonymisiert{suffix}"
            target.write_text(redact_text(doc.text, findings, settings.replace_mode).text, encoding="utf-8")
        print(f"  → {target}")
    return rc


def process_folder(source: Path, args, settings: Settings, analyzer) -> int:
    """Ordner vollautomatisch bearbeiten. Bereits in der Oberfläche geprüfte Dateien bleiben
    unangetastet; alle anderen werden als „automatisch (nicht geprüft)“ protokolliert."""
    from .core.batch import Status, Workspace, analyze_file, export_document

    target = args.ausgabe or source.with_name(source.name + "_geschwaerzt")
    try:
        if Workspace.exists_in(target):
            ws = Workspace.load(target)
            if ws.source != source.resolve():
                print(f"✗ {target} enthält einen Arbeitsstand für {ws.source}", file=sys.stderr)
                return 1
        else:
            ws = Workspace.create(source, target, recursive=not args.ohne_unterordner)
        ws.sync_with_disk()
    except (OSError, ValueError) as exc:
        print(f"✗ {source}: {exc}", file=sys.stderr)
        return 1
    print(f"Ordner {ws.source} → {ws.target} ({len(ws.entries)} Dateien)")
    rc = 0
    for e in ws.ordered():
        if e.status in (*Status.DONE, Status.MISSING):
            print(f"  = {e.rel}: {Status.LABELS[e.status]}")
            continue
        path = ws.abs(e.rel)
        try:
            if not e.analyzed:
                res = analyze_file(path, analyzer, settings)
                ws.set_analysis(e.rel, sha256=res["sha256"], size=res["size"], mtime_ns=res["mtime_ns"],
                                findings=res["findings"], warnings=res["warnings"], mode=res["mode"],
                                text_sha=res["text_sha"], ocr_pages=res["ocr_pages"])
            if args.nur_anzeigen:
                print(f"  • {e.rel}: {e.active_count} Funde" + (" (Texterkennung)" if e.has_ocr else ""))
                continue
            if e.has_ocr:
                e.note = "Texterkennung (OCR) – nicht automatisch exportiert, bitte in der Oberfläche prüfen"
                print(f"  ! {e.rel}: Texterkennung (OCR, Seiten {', '.join(map(str, e.ocr_pages))}) – "
                      "nicht automatisch exportiert, bitte in der Oberfläche prüfen")
                rc = rc or 3
                ws.save()
                continue
            doc = load_document(path, ocr=settings.ocr)
            findings = ws.restore_findings(e.rel, doc)
            if findings is None:
                res = analyze_file(path, analyzer, settings)
                ws.set_analysis(e.rel, sha256=res["sha256"], size=res["size"], mtime_ns=res["mtime_ns"],
                                findings=res["findings"], warnings=res["warnings"], mode=res["mode"],
                                text_sha=res["text_sha"], ocr_pages=res["ocr_pages"])
                findings = res["findings"]
            out = ws.out(e.rel)
            leftovers = export_document(doc, findings, settings, out)
            ws.mark_done(e.rel, reviewed=False, exported_as=out, leftovers=leftovers, findings=findings)
            print(f"  → {e.rel}: {e.active_count} Stellen geschwärzt" + (" ⚠ Reste im PDF" if leftovers else ""))
            if leftovers:
                rc = 2
        except (UnsupportedFileError, ModelMissingError, ThoroughModelMissingError, OSError) as exc:
            ws.set_error(e.rel, str(exc))
            print(f"  ✗ {e.rel}: {exc}", file=sys.stderr)
            rc = 1
        ws.save()
    ws.save()
    print(f"Protokoll: {ws.protocol_path}")
    return rc


SELFTEST_TEXT = (
    "Sehr geehrte Frau Anna Schneider, Ihre IBAN DE89 3704 0044 0532 0130 00 und die E-Mail "
    "anna.schneider@example.de wurden gespeichert. Anschrift: Lindenstraße 42a, 10969 Berlin."
)


def selftest(settings: Settings) -> int:
    """Prüft eine Installation ohne Oberfläche (z. B. nach dem PyInstaller-Build)."""
    import time

    from .paths import find_ner_model, find_spacy_model, is_frozen, model_dirs

    print(f"pii-redact Selbsttest – {'EXE' if is_frozen() else 'Quellcode'}")
    print(f"  Modellordner:  {', '.join(map(str, model_dirs())) or '(keine)'}")
    print(f"  spaCy-Modell:  {find_spacy_model(settings.spacy_model) or 'FEHLT'}")
    ner = find_ner_model(settings.ner_model)
    print(f"  Transformer:   {ner or '(nicht installiert – nur Modus Schnell)'}")
    help_file = Path(__file__).resolve().parent / "resources" / "hilfe.html"
    print(f"  Anwenderhilfe: {'OK' if help_file.is_file() else 'FEHLT'}")
    rc = 0 if help_file.is_file() else 1
    from .core import ocr as ocr_mod

    try:
        t = time.time()
        text = ocr_mod.selftest()
        ok = "Größe" in text and "12345" in text
        print(f"  {'✓' if ok else '✗'} Texterkennung: {'OK' if ok else 'FEHLER – gelesen: ' + repr(text)}"
              f" ({time.time() - t:.1f}s)")
        rc = rc or (0 if ok else 1)
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ Texterkennung: {exc}")
        rc = 1
    modes = [AnalysisMode.FAST] + ([AnalysisMode.THOROUGH] if ner else [])
    for mode in modes:
        settings.analysis_mode = mode
        try:
            t = time.time()
            analyzer = PiiAnalyzer.for_settings(settings)
            analyzer.load()
            t_load = time.time() - t
            t = time.time()
            found = {f.entity_type for f in analyzer.analyze(SELFTEST_TEXT, settings)}
            t_run = time.time() - t
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Modus {mode}: {exc}")
            rc = 1
            continue
        missing = {"PERSON", "IBAN_CODE", "EMAIL_ADDRESS", "DE_ADDRESS"} - found
        state = "OK" if not missing else f"FEHLT: {', '.join(sorted(missing))}"
        print(f"  {'✓' if not missing else '✗'} Modus {mode}: {state} (Laden {t_load:.1f}s, Analyse {t_run:.2f}s)")
        rc = rc or (1 if missing else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
