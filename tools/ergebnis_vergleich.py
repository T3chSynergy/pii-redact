"""Ergebnisse zweier Programmstände vergleichen – Sicherheitsnetz für Umbauten und Paket-Updates.

Hält für alle Beispieldateien (``samples/``) in beiden Analyse-Modi fest, was das Programm liefert: geladener
Text und Hinweise, Funde, Vorschläge der Kontrolle, geschwärzter Text, geschwärztes PDF (Text und Seitenbild je
Seite), Ergebnis der Kontrolle sowie Ausgaben und Rückgabewerte der Kommandozeile. Zwei solche Aufnahmen lassen
sich vergleichen; jede Abweichung wird benannt.

    python tools/ergebnis_vergleich.py pruefen --basis origin/dev      # Arbeitsstand gegen einen Git-Stand
    python tools/ergebnis_vergleich.py aufnehmen ergebnis_alt.json     # … oder in zwei Schritten
    python tools/ergebnis_vergleich.py vergleichen ergebnis_alt.json ergebnis_neu.json

Rückgabewert: 0 = gleich, 1 = Abweichungen, 2 = Aufruf- oder Laufzeitfehler.

Zusätzliche PDFs (``--ordner``, ``--liste``) werden nur geladen und bereinigt, nicht analysiert; von ihnen
stehen ausschließlich Prüfsummen in der Aufnahme. Die Aufnahme der Beispieldateien enthält dagegen deren Text
im Klartext (alles erfunden) – deshalb eigene Dokumente nie nach ``samples/`` legen. Aufnahmen
(``ergebnis_*.json``) werden nicht versioniert.

Die Aufnahme hängt von den installierten Paketen ab (Seitenbilder von PyMuPDF, Texterkennung): verglichen wird
deshalb immer in derselben Umgebung. Unterschiedliche Paketversionen nennt der Vergleich vorab.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import difflib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SUFFIXES = (".pdf", ".txt", ".md")
TMP_MARK = "<TMP>"
#: Felder des Arbeitsstands und Spalten des Protokolls, die von Uhrzeit, Benutzer oder Dateisystem abhängen
VOLATILE_KEYS = {"created", "updated", "analyzed_at", "reviewed_at", "reviewed_by", "mtime_ns", "source", "target"}
VOLATILE_COLUMNS = {"Geprüft von", "Zeitpunkt"}
#: fester Rahmen für einen frei gezogenen Bereich auf Seite 1 (prüft die Schwärzung von Grafik und Bildern mit)
AREA = (0, 40.0, 60.0, 260.0, 140.0)


def sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------- Aufnahme
def record(samples: Path, modes: list[str], extra: list[Path], log=print) -> dict:
    """Aufnahme erstellen. Importiert ``pii_redact`` erst hier – so gilt der Stand, der im Suchpfad vorn liegt."""
    import pymupdf

    try:
        import pii_redact
    except ImportError:          # nicht installiert: den Stand neben diesem Skript verwenden
        sys.path.insert(0, str(ROOT / "src"))
        import pii_redact
    from pii_redact.core import PiiAnalyzer, Settings

    pymupdf.TOOLS.mupdf_display_errors(False)
    files = sorted(p for p in samples.rglob("*") if p.suffix.lower() in SUFFIXES and p.is_file())
    out: dict = {
        "meta": {"pii_redact": pii_redact.__version__, "quelle": str(Path(pii_redact.__file__).parent),
                 "python": sys.version.split()[0], "pymupdf": pymupdf.__version__, "modi": modes,
                 "pakete": _package_versions()},
        "dokumente": {}, "analyse": {}, "kommandozeile": {}, "zusatz": {},
    }
    docs = {}
    with tempfile.TemporaryDirectory(prefix="pii-vergleich-") as name:
        special = _special_cases(Path(name))
        for rel, path in [(p.relative_to(samples).as_posix(), p) for p in files] + special:
            log(f"  laden      {rel}")
            docs[rel] = _load(path)
            out["dokumente"][rel] = _document(docs[rel])
    for mode in modes:
        settings = Settings(analysis_mode=mode)
        analyzer = PiiAnalyzer.for_settings(settings)
        for rel, doc in docs.items():
            if isinstance(doc, Exception):
                continue
            log(f"  {mode:10} {rel}")
            out["analyse"][f"{mode}/{rel}"] = _analysis(doc, analyzer, settings)
    for mode in modes:
        log(f"  Kommandozeile ({mode})")
        out["kommandozeile"][mode] = _command_line(samples, files, mode)
    for i, path in enumerate(extra):
        if i % 50 == 0:
            log(f"  zusätzliche PDFs {i}/{len(extra)}")
        out["zusatz"][str(path)] = _extra(path)
    return out


def _special_cases(folder: Path) -> list[tuple[str, Path]]:
    """Erzeugte PDFs für Fälle, die in den Beispieldateien nicht vorkommen, bei denen die Schwärzung aber auf
    Maße oder den Aufbau reagiert: enger Zeilenabstand, gemischte Schriftgrößen, gedrehte und zugeschnittene
    Seiten, ausgeblendete Ebene."""
    import pymupdf

    name = "Frau Erika Beispiel, IBAN DE89 3704 0044 0532 0130 00, Telefon 030 1234567"
    plain = "Gewöhnlicher Text mit Ober- und Unterlängen: Äpfel, Öl, Übung, gjpqy – bleibt lesbar."
    out = []

    def save(pdf, filename: str) -> None:
        path = folder / filename
        pdf.save(path)
        pdf.close()
        out.append((f"sonderfall/{filename}", path))

    pdf = pymupdf.open()                      # Zeilenabstand 85 % und 100 % der Schriftgröße
    page = pdf.new_page()
    y = 80.0
    for factor in (0.85, 1.0):
        for i in range(8):
            page.insert_text((60, y), name if i % 2 else plain, fontsize=11, fontname="helv")
            y += 11 * factor
        y += 30
    save(pdf, "enger_zeilenabstand.pdf")

    pdf = pymupdf.open()                      # verschiedene Größen und ein hochgestelltes Zeichen in einer Zeile
    page = pdf.new_page()
    x = 60.0
    for part, size, dy in (("Vermerk ", 11, 0), ("Erika ", 11, 0), ("Beispiel", 7, 0), ("2", 6, -5),
                           (" wohnt in Musterstadt.", 11, 0), (" Max Mustermann", 16, 0)):
        page.insert_text((x, 100 + dy), part, fontsize=size, fontname="helv")
        x += pymupdf.get_text_length(part, fontname="helv", fontsize=size)
    page.insert_text((60, 130), plain, fontsize=9, fontname="helv")
    save(pdf, "gemischte_groessen.pdf")

    pdf = pymupdf.open()                      # gedrehte Seite; zugeschnittene Seite mit Text außerhalb
    page = pdf.new_page()
    page.insert_text((60, 100), name, fontsize=11, fontname="helv")
    page.insert_text((60, 120), plain, fontsize=11, fontname="helv")
    page.set_rotation(90)
    page = pdf.new_page()
    page.insert_text((60, 100), "Herr Max Mustermann wohnt in Musterstadt.", fontsize=11, fontname="helv")
    page.insert_text((60, 700), "Außerhalb des Zuschnitts: Frau Erika Beispiel", fontsize=11, fontname="helv")
    page.set_cropbox(pymupdf.Rect(30, 40, 500, 400))
    save(pdf, "gedreht_und_zugeschnitten.pdf")

    pdf = pymupdf.open()                      # ausgeblendete Ebene neben sichtbarem Text
    page = pdf.new_page()
    hidden = pdf.add_ocg("Entwurf", on=False)
    shown = pdf.add_ocg("Reinschrift", on=True)
    page.insert_text((60, 100), "Herr Max Mustermann wohnt in Musterstadt.", fontsize=11, fontname="helv", oc=shown)
    page.insert_text((60, 130), "Ausgeblendet: Frau Erika Beispiel", fontsize=11, fontname="helv", oc=hidden)
    page.insert_text((60, 160), plain, fontsize=11, fontname="helv")
    save(pdf, "ausgeblendete_ebene.pdf")
    return out


def _package_versions() -> dict:
    from importlib import metadata

    names = ("PyMuPDF", "presidio-analyzer", "spacy", "de-core-news-md", "onnxruntime", "tokenizers", "rapidocr",
             "opencv-python", "numpy")
    found = {}
    for name in names:
        with contextlib.suppress(metadata.PackageNotFoundError):
            found[name] = metadata.version(name)
    return found


def _load(path: Path):
    from pii_redact.core import load_document

    try:
        return load_document(path)
    except Exception as exc:  # noqa: BLE001 – auch ein Fehler ist ein Ergebnis, das gleich bleiben soll
        return exc


def _pdf_state(pdf_bytes: bytes, with_text: bool = True) -> dict:
    """Zustand eines PDFs: Zahl der Ebenen und je Seite Text, Prüfsumme des Seitenbilds (72 dpi) und Prüfsumme
    aller Zeichenschritte. Die Zeichenschritte erfassen auch, was nicht angezeigt wird (Inhalt außerhalb des
    Seitenbereichs, ausgeblendete Ebenen) – Bild und Text allein blieben dabei gleich."""
    import pymupdf

    pages = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        layers = len(pdf.get_ocgs())
        for page in pdf:
            pix = page.get_pixmap(dpi=72, annots=True)
            text = page.get_text()
            steps = [(kind, [round(v, 1) for v in rect]) for kind, rect in page.get_bboxlog()]
            pages.append({"text": text if with_text else sha(text),
                          "bild": f"{pix.width}x{pix.height}:{sha(pix.samples)}",
                          "zeichenschritte": f"{len(steps)}:{sha(repr(steps))}"})
    return {"ebenen": layers, "seiten": pages}


def _document(doc) -> dict:
    if isinstance(doc, Exception):
        return {"fehler": f"{type(doc).__name__}: {doc}"}
    boxes = None
    if doc.char_boxes is not None:
        boxes = sha(repr([None if b is None else (b.page, b.line, [round(v, 1) for v in b.rect], b.ocr)
                          for b in doc.char_boxes]))
    return {
        "art": doc.kind, "kodierung": doc.encoding, "text": doc.text, "seiten": doc.page_count,
        "seitenanfaenge": list(doc.page_offsets), "ocr_seiten": list(doc.ocr_pages),
        "ocr_unsicher": doc.ocr_uncertain, "zeichenpositionen": boxes,
        "hinweise": [[n.kind, n.level, n.short, n.detail, list(n.pages), n.badge] for n in doc.notices],
        "warnungen": list(doc.warnings),
        "bereinigtes_pdf": _pdf_state(doc.pdf_bytes) if doc.pdf_bytes else None,
    }


def _finding(f) -> list:
    area = None if f.area is None else [f.area[0]] + [round(v, 1) for v in f.area[1:]]
    return [f.start, f.end, f.entity_type, f.text, round(f.score, 3), f.recognizer, f.source, f.active, area]


def _analysis(doc, analyzer, settings) -> dict:
    from pii_redact.core import Finding, ReplaceMode, redact_pdf, redact_text, verify_pdf
    from pii_redact.core.analyzer import further_occurrences

    findings = analyzer.analyze(doc.text, settings)
    out = {
        "funde": [_finding(f) for f in findings],
        "weitere_vorkommen": [_finding(f) for f in further_occurrences(doc.text, findings)],
        "text": {mode: redact_text(doc.text, findings, mode).text
                 for mode in (ReplaceMode.PLACEHOLDER, ReplaceMode.NUMBERED, ReplaceMode.BLACKOUT)},
    }
    if doc.is_pdf:
        out["pdf"] = {}
        variants = [("platzhalter", findings, ReplaceMode.PLACEHOLDER, True),
                    ("schwaerzen_ohne_beschriftung", findings, ReplaceMode.BLACKOUT, False),
                    ("mit_bereich", [*findings, Finding(0, 0, "AREA", "", area=AREA)], ReplaceMode.NUMBERED, True)]
        for name, used, mode, labels in variants:
            try:
                data = redact_pdf(doc, used, mode, labels)
                rest = verify_pdf(data, used, doc.ocr_pages, doc=doc, mode=mode, labels=labels)
                out["pdf"][name] = {"ergebnis": _pdf_state(data), "kontrolle": rest}
            except Exception as exc:  # noqa: BLE001
                out["pdf"][name] = {"fehler": f"{type(exc).__name__}: {exc}"}
    return out


def _extra(path: Path) -> dict:
    """Fremdes PDF nur laden und bereinigen (ohne Texterkennung, ohne Analyse) – ausschließlich Prüfsummen."""
    from pii_redact.core import load_document

    try:
        doc = load_document(path, ocr=False)
    except Exception as exc:  # noqa: BLE001
        return {"fehler": type(exc).__name__}
    return {"text": sha(doc.text), "seiten": doc.page_count,
            "hinweise": [[n.kind, n.level, list(n.pages)] for n in doc.notices],
            "bereinigtes_pdf": _pdf_state(doc.pdf_bytes, with_text=False) if doc.pdf_bytes else None}


# ---------------------------------------------------------------------- Kommandozeile
def _run_cli(args: list[str], tmp: Path) -> dict:
    from pii_redact import cli

    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = cli.main(args)
        except SystemExit as exc:
            code = exc.code

    def mark(text: str) -> str:
        """Den zufällig benannten Arbeitsordner unkenntlich machen (unter Windows auch in der Schreibweise mit
        doppelten Rückstrichen, wie sie in Fehlermeldungen steht) und Pfadtrenner vereinheitlichen."""
        for variant in (str(tmp).replace("\\", "\\\\"), str(tmp)):
            text = text.replace(variant, TMP_MARK)
        return text.replace("\\\\", "/").replace("\\", "/")

    return {"aufruf": [mark(a) for a in args], "rueckgabewert": code, "ausgabe": mark(stdout.getvalue()),
            "meldungen": mark(stderr.getvalue())}


def _tree(folder: Path) -> dict:
    """Inhalt eines Ergebnisordners: Text im Klartext, PDFs als Seiten, Arbeitsstand und Protokoll ohne Zeitangaben."""
    out = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if p.suffix.lower() == ".pdf":
            out[rel] = _pdf_state(p.read_bytes())
        elif p.suffix.lower() == ".json":
            out[rel] = _stable(json.loads(p.read_text(encoding="utf-8")))
        elif p.suffix.lower() == ".csv":
            with open(p, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.reader(fh, delimiter=";"))
            keep = [i for i, name in enumerate(rows[0]) if name not in VOLATILE_COLUMNS] if rows else []
            out[rel] = [[row[i] for i in keep] for row in rows]
        else:
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _command_line(samples: Path, files: list[Path], mode: str) -> dict:
    """Kommandozeile mit eigener, leerer Einstellungsdatei auf einer Kopie der Beispiele laufen lassen."""
    flag = "--schnell" if mode == "schnell" else "--gruendlich"
    runs = {}
    with tempfile.TemporaryDirectory(prefix="pii-vergleich-") as name:
        tmp = Path(name).resolve()
        source = tmp / "quelle"
        shutil.copytree(samples, source, ignore=lambda _d, names: [n for n in names
                                                                    if not n.lower().endswith(SUFFIXES)
                                                                    and not (Path(_d) / n).is_dir()])
        settings = tmp / "einstellungen.json"
        settings.write_text("{}", encoding="utf-8")
        base = ["--einstellungen", str(settings), flag]
        runs["ordner"] = _run_cli([str(source), "-o", str(tmp / "ziel"), *base], tmp)
        runs["ordner_erneut"] = _run_cli([str(source), "-o", str(tmp / "ziel"), *base], tmp)
        runs["ordner_ergebnis"] = _tree(tmp / "ziel")
        by_kind = {}
        for p in files:      # je Dateiart die erste Datei einzeln
            by_kind.setdefault(p.suffix.lower(), source / p.relative_to(samples))
        for suffix, path in sorted(by_kind.items()):
            key = f"einzeln{suffix}"
            runs[f"{key}_anzeigen"] = _run_cli([str(path), "--nur-anzeigen", *base], tmp)
            runs[f"{key}_details"] = _run_cli([str(path), "-o", str(tmp / "einzeln"), "--details",
                                               "--modus", "nummeriert", *base], tmp)
            runs[f"{key}_vorhanden"] = _run_cli([str(path), "-o", str(tmp / "einzeln"), *base], tmp)
            runs[f"{key}_als_text"] = _run_cli([str(path), "-o", str(tmp / "als_text" / suffix[1:]), "--als-text",
                                                *base], tmp)
        runs["einzeln_ergebnis"] = _tree(tmp / "einzeln") if (tmp / "einzeln").is_dir() else {}
        runs["als_text_ergebnis"] = _tree(tmp / "als_text") if (tmp / "als_text").is_dir() else {}
        runs["fehlt"] = _run_cli([str(tmp / "gibt-es-nicht.txt"), *base], tmp)
        runs["ohne_argumente"] = _run_cli(base, tmp)
    return runs


# ---------------------------------------------------------------------- Vergleich
def compare(old: dict, new: dict) -> list[str]:
    """Liste lesbarer Abweichungen (leer = gleich). ``meta`` wird nicht verglichen."""
    out: list[str] = []
    for key in ("dokumente", "analyse", "kommandozeile", "zusatz"):
        _diff(old.get(key, {}), new.get(key, {}), key, out)
    return out


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else repr(value)
    return text if len(text) <= 160 else text[:157] + "…"


def _diff(a, b, path: str, out: list[str]) -> None:
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            if key not in a:
                out.append(f"{path} › {key}: nur im neuen Stand")
            elif key not in b:
                out.append(f"{path} › {key}: nur im alten Stand")
            else:
                _diff(a[key], b[key], f"{path} › {key}", out)
    elif isinstance(a, str) and isinstance(b, str) and ("\n" in a or "\n" in b):
        lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "alt", "neu", lineterm="", n=0))
        out.append(f"{path}: Text weicht ab\n      " + "\n      ".join(lines[2:14])
                   + ("\n      …" if len(lines) > 14 else ""))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(isinstance(x, dict) for x in a + b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):      # z. B. Seiten eines PDFs: je Seite vergleichen
            _diff(x, y, f"{path} › {i + 1}", out)
    elif isinstance(a, list) and isinstance(b, list):
        gone = [x for x in a if x not in b]
        added = [x for x in b if x not in a]
        if not gone and not added:
            out.append(f"{path}: gleiche Einträge in anderer Reihenfolge oder Anzahl ({len(a)} → {len(b)})")
            return
        lines = [f"- {_short(x)}" for x in gone[:6]] + [f"+ {_short(x)}" for x in added[:6]]
        more = len(gone) + len(added) - len(lines)
        out.append(f"{path}: {len(gone)} entfallen, {len(added)} neu\n      " + "\n      ".join(lines)
                   + (f"\n      … und {more} weitere" if more > 0 else ""))
    else:
        out.append(f"{path}: {_short(a)} → {_short(b)}")


def report(old: dict, new: dict, log=print) -> int:
    for key in ("python", "pymupdf", "pakete", "modi"):
        if old.get("meta", {}).get(key) != new.get("meta", {}).get(key):
            log(f"Hinweis: unterschiedliche Umgebung – {key}: {_short(old.get('meta', {}).get(key))} → "
                f"{_short(new.get('meta', {}).get(key))}")
    differences = compare(old, new)
    counts = ", ".join(f"{len(new.get(k, {}))} {label}" for k, label in
                       (("dokumente", "Dokumente"), ("analyse", "Analysen"), ("zusatz", "zusätzliche PDFs")))
    if not differences:
        log(f"Keine Abweichung ({counts}).")
        return 0
    log(f"{len(differences)} Abweichung(en) ({counts}):")
    for d in differences:
        log(f"  • {d}")
    return 1


# ---------------------------------------------------------------------- Aufrufe
def _extra_files(folders: list[Path], lists: list[Path]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files += sorted(p for p in folder.rglob("*.pdf") if p.is_file())
    for lst in lists:
        files += [Path(line.strip()) for line in lst.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [p for p in dict.fromkeys(files) if p.is_file()]


def _modes(only_fast: bool) -> list[str]:
    from pii_redact.paths import find_ner_model

    if only_fast or not find_ner_model(""):
        return ["schnell"]
    return ["schnell", "gruendlich"]


def _record_to(target: Path, args) -> None:
    # Einstellungen des Benutzers dürfen das Ergebnis nicht beeinflussen
    with tempfile.TemporaryDirectory(prefix="pii-vergleich-cfg-") as cfg:
        os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = cfg
        data = record(args.beispiele, _modes(args.nur_schnell), _extra_files(args.ordner, args.liste))
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Aufnahme: {target} ({len(data['dokumente'])} Dokumente, {len(data['analyse'])} Analysen, "
          f"{len(data['zusatz'])} zusätzliche PDFs) – Stand {data['meta']['quelle']}")


def _check(args) -> int:
    """Git-Stand ``--basis`` in einen eigenen Ordner auschecken, beide Stände aufnehmen, vergleichen."""
    with tempfile.TemporaryDirectory(prefix="pii-vergleich-") as name:
        tmp = Path(name)
        base = tmp / "basis"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(base), args.basis],
                       check=True, capture_output=True, text=True)
        # beide Stände mit denselben Modellen: der ausgecheckte Ordner hat keinen eigenen Ordner „models“
        models = os.pathsep.join(filter(None, [os.environ.get("PII_REDACT_MODEL_DIR", ""), str(ROOT / "models")]))
        try:
            common = ["aufnehmen", "--beispiele", str(args.beispiele)]
            common += ["--nur-schnell"] if args.nur_schnell else []
            for folder in args.ordner:
                common += ["--ordner", str(folder)]
            for lst in args.liste:
                common += ["--liste", str(lst)]
            for label, src, target in (("Basis " + args.basis, base / "src", tmp / "alt.json"),
                                       ("Arbeitsstand", ROOT / "src", tmp / "neu.json")):
                print(f"== {label}")
                env = dict(os.environ, PYTHONPATH=str(src), PYTHONIOENCODING="utf-8", PII_REDACT_MODEL_DIR=models)
                subprocess.run([sys.executable, str(Path(__file__).resolve()), *common, str(target)],
                               check=True, env=env)
            old = json.loads((tmp / "alt.json").read_text(encoding="utf-8"))
            new = json.loads((tmp / "neu.json").read_text(encoding="utf-8"))
            if Path(old["meta"]["quelle"]) == Path(new["meta"]["quelle"]):
                print("Fehler: beide Aufnahmen stammen aus demselben Quellordner – der Vergleich wäre wertlos.",
                      file=sys.stderr)
                return 2
            if args.behalten:
                args.behalten.mkdir(parents=True, exist_ok=True)   # fehlte der Ordner, ging der ganze Lauf verloren
                for f in ("alt.json", "neu.json"):
                    shutil.copy(tmp / f, args.behalten / f"ergebnis_{f}")
            return report(old, new)
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(base)],
                           capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ergebnisse zweier Programmstände vergleichen.")
    sub = p.add_subparsers(dest="befehl", required=True)

    def common(sp) -> None:
        sp.add_argument("--beispiele", type=Path, default=SAMPLES, help="Ordner mit den Beispieldateien")
        sp.add_argument("--ordner", type=Path, action="append", default=[],
                        help="zusätzliche PDFs (nur laden und bereinigen, nur Prüfsummen); mehrfach möglich")
        sp.add_argument("--liste", type=Path, action="append", default=[],
                        help="Textdatei mit je einem PDF-Pfad pro Zeile")
        sp.add_argument("--nur-schnell", action="store_true", help="Modus „Gründlich“ auslassen")

    a = sub.add_parser("aufnehmen", help="Ergebnisse des Stands im Suchpfad in eine Datei schreiben")
    a.add_argument("ziel", type=Path)
    common(a)
    v = sub.add_parser("vergleichen", help="zwei Aufnahmen vergleichen")
    v.add_argument("alt", type=Path)
    v.add_argument("neu", type=Path)
    c = sub.add_parser("pruefen", help="Arbeitsstand gegen einen Git-Stand prüfen")
    c.add_argument("--basis", default="origin/dev", help="Git-Stand zum Vergleich (Standard: origin/dev)")
    c.add_argument("--behalten", type=Path, metavar="ORDNER", help="beide Aufnahmen dort ablegen")
    common(c)
    args = p.parse_args(argv)
    try:
        if args.befehl == "aufnehmen":
            _record_to(args.ziel, args)
            return 0
        if args.befehl == "vergleichen":
            return report(json.loads(args.alt.read_text(encoding="utf-8")),
                          json.loads(args.neu.read_text(encoding="utf-8")))
        return _check(args)
    except subprocess.CalledProcessError as exc:
        print(f"Fehler: {' '.join(map(str, exc.cmd[:6]))} … ({exc.returncode})\n{exc.stderr or ''}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
