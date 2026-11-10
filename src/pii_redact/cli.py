"""Kommandozeile für Stapelverarbeitung ohne Oberfläche.

Beispiel:
    pii-redact-cli dokument.pdf                 → dokument_geschwaerzt.pdf
    pii-redact-cli notizen.md --modus nummeriert → notizen_anonymisiert.md
    pii-redact-cli dokument.pdf --als-text      → dokument_anonymisiert.txt
    pii-redact-cli C:\\Akten -o C:\\Akten_geschwaerzt   → ganzer Ordner (automatisch, NICHT geprüft)

Die Ausgabe nennt nur Anzahl und Datenart der Funde – den gefundenen Klartext zeigen nur --nur-anzeigen
und --details (Vorsicht beim Umleiten in Log-Dateien).

Rückgabewerte: 0 = OK, 1 = Fehler (auch falsche Aufrufparameter), 2 = Kontrolle fand Reste im Ergebnis,
               3 = Dateien wurden nicht automatisch exportiert und müssen in der Oberfläche geprüft werden
                   (Texterkennung/OCR, oder geschwärzter Text steht noch an anderer Stelle),
               4 = mit dem Modus „Schnell“ bearbeitet, obwohl „Gründlich“ eingestellt ist (Modell fehlt).
Bei mehreren Dateien gilt der schwerwiegendste Wert: 2 vor 1 vor 3 vor 4 vor 0.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

for _var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    os.environ.setdefault(_var, "1")  # nie etwas aus dem Internet nachladen

from . import __version__
from .core import (
    AnalysisMode,
    ModelMissingError,
    PiiAnalyzer,
    ReplaceMode,
    Settings,
    ThoroughModelMissingError,
    UnsupportedFileError,
    load_document,
)
from .core.batch import CHECK_NOT_AUTO, export_document, held_for_check
from .core.entities import info
from .settings_store import defaults_path, settings_path

OK, ERROR, LEFTOVERS, OCR_SKIPPED, FAST_FALLBACK = 0, 1, 2, 3, 4
#: Fehler an einer einzelnen Datei – melden und mit der nächsten weitermachen. RuntimeError deckt Fehler der
#: PDF-Bibliothek bei defekten Dateien ab (die Modell-Fehler sind ebenfalls RuntimeError).
FILE_ERRORS = (UnsupportedFileError, ModelMissingError, ThoroughModelMissingError, OSError, RuntimeError)
_SEVERITY = {OK: 0, FAST_FALLBACK: 1, OCR_SKIPPED: 2, ERROR: 3, LEFTOVERS: 4}


def worst(*codes: int) -> int:
    """Schwerwiegendster Rückgabewert: Reste im Ergebnis vor Fehler vor nicht exportierter OCR-Datei vor
    Bearbeitung im Modus „Schnell“ statt „Gründlich“."""
    return max(codes, key=_SEVERITY.__getitem__)


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # Rückgabewert 2 ist für „Reste im Ergebnis“ reserviert
        self.print_usage(sys.stderr)
        self.exit(ERROR, f"{self.prog}: Fehler: {message}\n")


def summary(findings) -> str:
    """„3 Funde (Person 2, IBAN 1)“ – ohne Klartext."""
    counts: dict[str, int] = {}
    for f in findings:
        label = info(f.entity_type).label
        counts[label] = counts.get(label, 0) + 1
    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return f"{len(findings)} Funde" + (f" ({parts})" if parts else "")


def build_parser() -> _Parser:
    p = _Parser(prog="pii-redact-cli", description="Personenbezogene Daten erkennen und entfernen.")
    p.add_argument("dateien", nargs="*", type=Path, help="PDF-, TXT- oder Markdown-Dateien oder Ordner")
    p.add_argument("--ohne-unterordner", action="store_true", help="bei Ordnern nur die oberste Ebene")
    p.add_argument("-o", "--ausgabe", type=Path, help="Ausgabeordner (Standard: neben der Quelldatei)")
    p.add_argument("--modus", choices=[ReplaceMode.PLACEHOLDER, ReplaceMode.NUMBERED, ReplaceMode.BLACKOUT])
    p.add_argument("--gruendlich", action="store_true",
                   help="Modus „Gründlich“ erzwingen (Standard; Fehler, wenn das Modell fehlt)")
    p.add_argument("--schnell", action="store_true",
                   help="Modus „Schnell“ (nur spaCy + Muster – weniger Treffer, weniger Speicher)")
    p.add_argument("--selftest", action="store_true", help="Beispieldokument analysieren und Installation prüfen")
    p.add_argument("--schwelle", type=float, help="Mindest-Score 0–1 (Standard aus Einstellungen)")
    p.add_argument("--als-text", action="store_true", help="PDFs als anonymisierten Text statt als PDF ausgeben")
    p.add_argument("--nur-anzeigen", action="store_true",
                   help="Funde mit Klartext nur auflisten, nichts schreiben")
    p.add_argument("--details", action="store_true",
                   help="Funde mit Klartext auflisten (Vorsicht: nicht in Log-Dateien umleiten)")
    p.add_argument("--ueberschreiben", action="store_true", help="vorhandene Ergebnisdateien ersetzen")
    p.add_argument("--einstellungen", type=Path, metavar="DATEI",
                   help="diese Einstellungsdatei statt der persönlichen verwenden (z. B. für Dienstkonten); "
                        "zentrale Vorgaben aus defaults.json gelten weiterhin")
    p.add_argument("--version", action="version", version=f"pii-redact {__version__}")
    p.add_argument("--ohne-ocr", action="store_true", help="gescannte PDF-Seiten nicht per Texterkennung lesen")
    return p


def load_settings(args, p: _Parser) -> tuple[Settings, bool]:
    """Einstellungen laden und die Schalter für Texterkennung und Analyse-Modus anwenden. Zweiter Wert: Es wird
    mit „Schnell“ gearbeitet, obwohl „Gründlich“ eingestellt ist (Modell fehlt)."""
    if args.einstellungen is not None and not args.einstellungen.is_file():
        p.error(f"Einstellungsdatei nicht gefunden: {args.einstellungen}")
    settings = Settings.load(args.einstellungen or settings_path(), defaults_path())
    if args.ohne_ocr:
        settings.ocr = False
    if args.gruendlich:
        settings.analysis_mode = AnalysisMode.THOROUGH
    if args.schnell:
        settings.analysis_mode = AnalysisMode.FAST
    if args.gruendlich or settings.analysis_mode != AnalysisMode.THOROUGH:
        return settings, False
    from .paths import find_ner_model

    if find_ner_model(settings.ner_model) is not None:
        return settings, False
    # Weniger Treffer als eingestellt – nicht stillschweigend: eigener Rückgabewert, damit Skripte es
    # bemerken. Wer „Schnell“ will, sagt es mit --schnell oder in den Einstellungen.
    print("⚠ Modus „Gründlich“ nicht verfügbar (kein Transformer-Modell) – bearbeitet wird mit „Schnell“ "
          f"(weniger Treffer, Rückgabewert {FAST_FALLBACK}). Bewusst wählen mit --schnell.", file=sys.stderr)
    settings.analysis_mode = AnalysisMode.FAST
    return settings, True


def apply_output_options(args, settings: Settings, p: _Parser) -> None:
    """Ersetzungsart und Schwelle aus dem Aufruf übernehmen."""
    if args.modus:
        settings.replace_mode = args.modus
    if args.schwelle is not None:
        if not 0 <= args.schwelle <= 1:   # schließt auch „nan“ aus
            p.error("--schwelle erwartet einen Wert zwischen 0 und 1 (zum Beispiel 0.45).")
        settings.threshold = args.schwelle


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings, fallback = load_settings(args, p)
    if args.selftest:
        return selftest(settings)
    if not args.dateien:
        p.error("Bitte mindestens eine Datei angeben.")
    apply_output_options(args, settings, p)

    analyzer = PiiAnalyzer.for_settings(settings)
    rc = FAST_FALLBACK if fallback else OK
    for path in args.dateien:
        process = process_folder if path.is_dir() else process_file
        rc = worst(rc, process(path, args, settings, analyzer))
    return rc


def target_for(path: Path, doc, args) -> Path:
    out_dir = args.ausgabe or path.parent
    if doc.is_pdf and not args.als_text:
        return out_dir / f"{path.stem}_geschwaerzt.pdf"
    return out_dir / f"{path.stem}_anonymisiert{'.txt' if doc.is_pdf else path.suffix}"


def print_findings(doc, findings) -> None:
    """Funde mit Klartext auflisten: Seite bzw. Zeile, Datenart, Sicherheit, Text."""
    for f in findings:
        page = doc.page_of(f.start)
        where = f"S. {page + 1}" if page is not None else f"Z. {doc.text.count(chr(10), 0, f.start) + 1}"
        print(f"  {where:>7}  {info(f.entity_type).label:<22} {f.score:.2f}  {f.text!r}")


def process_file(path: Path, args, settings: Settings, analyzer) -> int:
    try:
        doc = load_document(path, ocr=settings.ocr)
        findings = analyzer.analyze(doc.text, settings)
    except FILE_ERRORS as exc:
        print(f"✗ {path}: {exc}", file=sys.stderr)
        return ERROR

    for w in doc.warnings:
        print(f"  ⚠ {path.name}: {w}", file=sys.stderr)
    print(f"{path}: {summary(findings)}")
    if args.nur_anzeigen or args.details:
        print_findings(doc, findings)
    if args.nur_anzeigen:
        return OK
    if doc.has_ocr:
        # wie im Ordnerlauf: Texterkennung macht Fehler – nicht ungeprüft exportieren
        pages = ", ".join(str(p + 1) for p in doc.ocr_pages)
        print(f"  ! {path}: Texterkennung (OCR, Seiten {pages}) – nicht automatisch exportiert, bitte in der "
              "Oberfläche prüfen", file=sys.stderr)
        return OCR_SKIPPED
    if held_for_check(doc.text, findings) is not None:
        print(f"  ! {path}: {CHECK_NOT_AUTO}", file=sys.stderr)
        return OCR_SKIPPED

    target = target_for(path, doc, args)
    if target.exists() and not args.ueberschreiben:
        print(f"✗ {path}: {target} ist schon vorhanden – nichts geschrieben (ersetzen mit --ueberschreiben)",
              file=sys.stderr)
        return ERROR
    try:
        rest = export_document(doc, findings, settings, target, as_text=args.als_text)
    except FILE_ERRORS as exc:
        print(f"✗ {path}: {exc}", file=sys.stderr)
        return ERROR
    print(f"  → {target}")
    if rest:
        print(f"  ⚠ Im Ergebnis noch vorhanden: {'; '.join(rest)}", file=sys.stderr)
        return LEFTOVERS
    return OK


def open_workspace(source: Path, target: Path, recursive: bool):
    """Arbeitsstand im Zielordner fortsetzen oder neu anlegen und mit dem Quellordner abgleichen.
    None (mit Meldung), wenn das nicht geht."""
    from .core.batch import Workspace

    try:
        if Workspace.exists_in(target):
            ws = Workspace.load(target)
            if ws.source != source.resolve():
                print(f"✗ {target} enthält einen Arbeitsstand für {ws.source}", file=sys.stderr)
                return None
        else:
            ws = Workspace.create(source, target, recursive=recursive)
        ws.sync_with_disk()
    except (OSError, ValueError) as exc:
        print(f"✗ {source}: {exc}", file=sys.stderr)
        return None
    return ws


def export_entry(ws, e, settings: Settings, analyzer) -> int:
    """Eine analysierte Datei des Ordners exportieren – oder zur Prüfung stehen lassen (Texterkennung, Kontrolle
    vor dem Speichern). Rückgabewert für diese Datei."""
    from .core.batch import analyze_file

    if e.has_ocr:
        e.note = "Texterkennung (OCR) – nicht automatisch exportiert, bitte in der Oberfläche prüfen"
        print(f"  ! {e.rel}: Texterkennung (OCR, Seiten {', '.join(map(str, e.ocr_pages))}) – "
              "nicht automatisch exportiert, bitte in der Oberfläche prüfen")
        return OCR_SKIPPED
    path = ws.abs(e.rel)
    doc = load_document(path, ocr=settings.ocr)
    findings = ws.restore_findings(e.rel, doc)
    if findings is None:
        res = analyze_file(path, analyzer, settings)
        ws.set_analysis_result(e.rel, res)
        findings = res["findings"]
    held = held_for_check(doc.text, findings)
    if held is not None:
        ws.hold_for_check(e.rel, held)
        print(f"  ! {e.rel}: {CHECK_NOT_AUTO}")
        return OCR_SKIPPED
    out = ws.out(e.rel)
    leftovers = export_document(doc, findings, settings, out)
    ws.mark_done(e.rel, reviewed=False, exported_as=out, leftovers=leftovers, findings=findings)
    print(f"  → {e.rel}: {e.active_count} Stellen geschwärzt" + (" ⚠ Reste im PDF" if leftovers else ""))
    return LEFTOVERS if leftovers else OK


def process_folder(source: Path, args, settings: Settings, analyzer) -> int:
    """Ordner vollautomatisch bearbeiten. Bereits in der Oberfläche geprüfte Dateien bleiben
    unangetastet; alle anderen werden als „automatisch (nicht geprüft)“ protokolliert."""
    from .core.batch import Status, analyze_file

    target = args.ausgabe or source.with_name(source.name + "_geschwaerzt")
    ws = open_workspace(source, target, recursive=not args.ohne_unterordner)
    if ws is None:
        return ERROR
    print(f"Ordner {ws.source} → {ws.target} ({len(ws.entries)} Dateien)")
    rc = OK
    for e in ws.ordered():
        if e.status in (*Status.DONE, Status.MISSING):
            print(f"  = {e.rel}: {Status.LABELS[e.status]}")
            continue
        try:
            if not e.analyzed:
                ws.set_analysis_result(e.rel, analyze_file(ws.abs(e.rel), analyzer, settings))
            if args.nur_anzeigen:
                print(f"  • {e.rel}: {e.active_count} Funde" + (" (Texterkennung)" if e.has_ocr else ""))
                continue
            rc = worst(rc, export_entry(ws, e, settings, analyzer))
        except FILE_ERRORS as exc:
            ws.set_error(e.rel, str(exc))
            print(f"  ✗ {e.rel}: {exc}", file=sys.stderr)
            rc = worst(rc, ERROR)
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
