"""Ordner-Bearbeitung: Arbeitsstand, Export mit gespiegelter Ordnerstruktur, Protokoll.

Der Arbeitsstand liegt als ``pii-redact-arbeitsstand (nicht weitergeben).json`` im Zielordner. Er enthält bewusst
KEINEN Klartext – Funde werden nur als Zeichenpositionen gespeichert und beim Öffnen aus dem Original
rekonstruiert. Lage und Länge der geschwärzten Stellen stehen aber darin; deshalb gehört er nicht zu den
Ergebnissen, die weitergegeben werden.

Prüfsummen (seit Format 2): Der Zielordner wird oft weitergegeben oder ist eine Ablage, die andere lesen. Eine
Prüfsumme des Originals ließe sich dort durch Raten umkehren („Termin mit Frau [PERSON] am [DATUM]“ – Namen und
Daten durchprobieren, bis die Prüfsumme passt). Deshalb:

* ``layout_sha`` prüft, ob die gespeicherten Positionen noch zum erkannten Text passen – gebildet über das
  Textlayout (jedes Zeichen außer Leerraum wird ``x``). Länge und Zeilen weichen ab, sobald sich die Positionen
  verschieben (Texterkennung ein/aus, andere Programmversion); zu raten gibt es nichts.
* ``sha256`` (Datei) nur noch bei PDFs – dort lässt sich die Datei nicht aus geratenen Werten nachbauen. Bei
  TXT/Markdown entscheiden Größe und Änderungszeit; ändert sich die Zeit, wird die Datei erneut geprüft.
* Ältere Arbeitsstände (Format 1) mit der Prüfsumme des ganzen Texts werden beim nächsten Öffnen einer Datei
  umgestellt; die Datei-Prüfsumme von TXT/Markdown entfällt beim Laden.
"""

from __future__ import annotations

import csv
import datetime as dt
import getpass
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analyzer import CHECK_SOURCE, PiiAnalyzer, ProgressFn, further_occurrences
from .entities import info
from .loaders import OCR_WARNING_PREFIX, SUPPORTED_SUFFIXES, LoadedDocument
from .models import Finding, Settings
from .redactor import redact_text, save_redacted_pdf

WORKSPACE_NAME = "pii-redact-arbeitsstand (nicht weitergeben).json"
OLD_WORKSPACE_NAME = "pii-redact-arbeitsstand.json"      # bis 0.8.7; wird gelesen und beim Speichern ersetzt
PROTOCOL_NAME = "pii-redact-protokoll.csv"
FORMAT_VERSION = 2
WORKSPACE_HINT = ("pii-redact Arbeitsstand – nur zum Fortsetzen der Bearbeitung, nicht weitergeben. Enthält keine "
                  "Texte der Dokumente, aber Lage und Länge der geschwärzten Stellen.")


class Status:
    WAITING = "wartet"
    TO_REVIEW = "zu_pruefen"
    REVIEWED = "geprueft"
    AUTO = "automatisch"
    ERROR = "fehler"
    MISSING = "fehlt"

    LABELS = {
        WAITING: "wartet auf Analyse",
        TO_REVIEW: "zu prüfen",
        REVIEWED: "geprüft",
        AUTO: "automatisch exportiert (nicht geprüft)",
        ERROR: "Fehler",
        MISSING: "Datei fehlt",
    }
    DONE = (REVIEWED, AUTO)


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unbekannt"


def text_sha(text: str) -> str:
    """Prüfsumme des ganzen Texts – nur noch zum Umstellen älterer Arbeitsstände (Format 1)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def layout_sha(text: str) -> str:
    """Prüfsumme des Textlayouts: Jedes Zeichen außer Leerraum wird ``x``. Passen die gespeicherten Positionen
    nicht mehr (Texterkennung ein/aus, andere Programmversion), weichen Länge oder Zeilen ab; über den Inhalt
    verrät die Prüfsumme nichts, was nicht schon im geschwärzten Ergebnis steht."""
    return hashlib.sha256(re.sub(r"\S", "x", text).encode("utf-8")).hexdigest()


def keeps_file_sha(rel: str) -> bool:
    """Prüfsumme der Datei nur bei PDFs – bei TXT/Markdown wäre sie eine Prüfsumme des Texts selbst."""
    return Path(rel).suffix.lower() == ".pdf"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------- Funde ohne Klartext
def findings_to_compact(findings: list[Finding]) -> list[dict]:
    out = []
    for f in findings:
        # Begründungen der KI-Nachprüfung können Inhalte zitieren → nicht im Arbeitsstand speichern
        rec = "KI-Nachprüfung" if f.source == "ki" else f.recognizer
        item = {"s": f.start, "e": f.end, "t": f.entity_type, "a": f.active, "src": f.source,
                "sc": f.score, "rec": rec}
        if f.original_type:
            item["ot"] = f.original_type
        if f.area is not None:
            item["ar"] = [int(f.area[0])] + [round(float(v), 2) for v in f.area[1:]]
        out.append(item)
    return out


def findings_from_compact(items: list[dict], text: str) -> list[Finding]:
    out = []
    n = len(text)
    for it in items or []:
        s, e = int(it["s"]), int(it["e"])
        if it.get("ar"):  # frei gezogener Bereich – unabhängig vom Text
            ar = it["ar"]
            anchor = max(0, min(s, n))
            out.append(Finding(anchor, anchor, it["t"], "", float(it.get("sc", 1.0)), source="manuell",
                               active=bool(it.get("a", True)), recognizer=it.get("rec", "Bereich"),
                               area=(int(ar[0]), float(ar[1]), float(ar[2]), float(ar[3]), float(ar[4]))))
            continue
        if not (0 <= s < e <= n):
            continue
        out.append(Finding(s, e, it["t"], text[s:e], float(it.get("sc", 1.0)), source=it.get("src", "auto"),
                           active=bool(it.get("a", True)), recognizer=it.get("rec", ""),
                           original_type=it.get("ot", "")))
    return out


def csv_safe(value: object) -> object:
    """Zelle für das Protokoll: Text, der mit ``=``, ``+``, ``-`` oder ``@`` beginnt, liest eine Tabellenkalkulation
    als Formel (z. B. ein Dateiname „=Summe.pdf“). Ein vorangestelltes Hochkomma macht daraus reinen Text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


#: Vermerk an Dateien, die die Kontrolle vor dem Speichern zurückhält (automatischer Export, Kommandozeile)
CHECK_NOT_AUTO = ("Kontrolle: geschwärzter Text steht noch an anderer Stelle – nicht automatisch exportiert, "
                  "bitte in der Oberfläche prüfen")


def held_for_check(text: str, findings: list[Finding]) -> list[Finding] | None:
    """Kontrolle vor dem Speichern für Läufe ohne Rückfrage (automatischer Export, Kommandozeile).

    Steht der Text eines aktiven Fundes noch an einer nicht markierten Stelle, wird die Datei nicht exportiert,
    sondern bleibt zur Prüfung in der Oberfläche stehen – wie Dateien mit Texterkennung. Rückgabe: die Funde
    samt Vorschlägen der Kontrolle, wenn die Datei zurückgehalten wird, sonst None.

    Auch ein schon vorhandener, noch nicht bestätigter Vorschlag hält die Datei zurück: Ohne Prüfung durch
    einen Menschen ist er keine Entscheidung."""
    new = further_occurrences(text, findings)
    if new or any(f.source == CHECK_SOURCE and not f.active for f in findings):
        return sorted(findings + new, key=lambda f: (f.start, f.end))
    return None


def ki_summary(review: dict | None) -> str:
    """„hoch – 3 Hinweise (Modell, Zeitpunkt)“ für Protokoll und Arbeitsliste."""
    if not review:
        return ""
    n = int(review.get("hints", 0))
    return (f"{review.get('risk', '?')} – {n} {'Hinweis' if n == 1 else 'Hinweise'} "
            f"({review.get('model', '?')}, {review.get('at', '')})")


def finding_stats(findings: list[Finding]) -> dict:
    """Zahlen fürs Protokoll – ohne Inhalte."""
    per_type: dict[str, int] = {}
    for f in findings:
        if f.active:
            label = info(f.entity_type).label
            per_type[label] = per_type.get(label, 0) + 1
    return {
        "total": len(findings),
        "active": sum(1 for f in findings if f.active),
        "per_type": dict(sorted(per_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "added": sum(1 for f in findings if f.source == "manuell" and f.active and not f.is_area),
        "areas": sum(1 for f in findings if f.is_area and f.active),
        "deselected": sum(1 for f in findings if f.source == "auto" and not f.active),
        "retyped": sum(1 for f in findings if f.original_type),
    }


# ---------------------------------------------------------------------- Ordner
def is_generated(name: str) -> bool:
    stem = Path(name).stem
    return (stem.endswith("_geschwaerzt") or stem.endswith("_anonymisiert")
            or name in (WORKSPACE_NAME, OLD_WORKSPACE_NAME, PROTOCOL_NAME))


def scan_folder(source: Path, recursive: bool, exclude: Path | None = None) -> list[str]:
    """Relative Pfade (mit /) aller unterstützten Dateien, sortiert."""
    source = source.resolve()
    exclude = exclude.resolve() if exclude else None
    pattern = source.rglob("*") if recursive else source.glob("*")
    found = []
    for p in pattern:
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if p.name.startswith(("~$", ".")) or is_generated(p.name):
            continue
        rel_parts = p.relative_to(source).parts
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue
        if exclude and (p.resolve() == exclude or exclude in p.resolve().parents):
            continue
        found.append(p.relative_to(source).as_posix())
    return sorted(found, key=lambda r: r.casefold())


def output_name(rel: str) -> str:
    p = Path(rel)
    kind = SUPPORTED_SUFFIXES.get(p.suffix.lower(), "txt")
    if kind == "pdf":
        return str(p.with_name(f"{p.stem}_geschwaerzt.pdf").as_posix())
    return str(p.with_name(f"{p.stem}_anonymisiert{p.suffix}").as_posix())


def export_document(doc: LoadedDocument, findings: list[Finding], settings: Settings, target: Path,
                    as_text: bool = False) -> list[str]:
    """Schreibt die bearbeitete Fassung (PDF bleibt PDF, außer ``as_text``).
    Rückgabe: im PDF noch lesbare Reste (leer = sauber)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if doc.is_pdf and not as_text:
        return save_redacted_pdf(doc, findings, settings, target)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(redact_text(doc.text, findings, settings.replace_mode).text, encoding="utf-8")
    os.replace(tmp, target)
    return []


# ---------------------------------------------------------------------- Arbeitsstand
@dataclass
class FileEntry:
    rel: str
    status: str = Status.WAITING
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""        # Prüfsumme der Datei – nur bei PDFs (``keeps_file_sha``)
    findings: list[dict] | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    analysis_mode: str = ""
    analyzed_at: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    exported_as: str = ""
    export_note: str = ""
    stats: dict = field(default_factory=dict)
    note: str = ""          # z. B. „nach Prüfung geändert“, „Original geändert“
    layout_sha: str = ""    # Prüfsumme des Textlayouts – die Funde passen nur zu einem Text mit genau diesem Layout
    text_sha: str = ""      # nur Format 1: Prüfsumme des ganzen Texts; wird beim nächsten Öffnen ersetzt
    ocr_pages: list[int] = field(default_factory=list)   # per Texterkennung gelesene Seiten (1-basiert)
    ki_review: dict = field(default_factory=dict)         # letzte KI-Nachprüfung: Risiko, Anzahl Hinweise, Modell

    @property
    def analyzed(self) -> bool:
        return self.findings is not None and self.status not in (Status.WAITING, Status.ERROR, Status.MISSING)

    @property
    def done(self) -> bool:
        return self.status in Status.DONE

    @property
    def has_ocr(self) -> bool:
        return bool(self.ocr_pages)

    @property
    def active_count(self) -> int | None:
        if self.findings is None:
            return None
        return sum(1 for f in self.findings if f.get("a", True))


class Workspace:
    def __init__(self, source: Path, target: Path, recursive: bool = True):
        self.source = Path(source).resolve()
        self.target = Path(target).resolve()
        self.recursive = recursive
        self.created = now_iso()
        self.updated = self.created
        self.entries: dict[str, FileEntry] = {}

    # ------------------------------------------------------------------ Dateien
    @property
    def path(self) -> Path:
        return self.target / WORKSPACE_NAME

    @property
    def protocol_path(self) -> Path:
        return self.target / PROTOCOL_NAME

    def abs(self, rel: str) -> Path:
        return self.source / rel

    def out(self, rel: str) -> Path:
        return self.target / output_name(rel)

    def ordered(self) -> list[FileEntry]:
        return [self.entries[k] for k in sorted(self.entries, key=str.casefold)]

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for e in self.entries.values():
            c[e.status] = c.get(e.status, 0) + 1
        return c

    def next_open(self, after: str | None = None) -> str | None:
        """Nächste Datei, die noch geprüft werden muss (zyklisch ab ``after``)."""
        order = [e.rel for e in self.ordered()]
        if not order:
            return None
        start = order.index(after) + 1 if after in order else 0
        for rel in order[start:] + order[:start]:
            e = self.entries[rel]
            if e.status in (Status.TO_REVIEW, Status.WAITING) and rel != after:
                return rel
        return None

    # ------------------------------------------------------------------ Anlegen / Laden
    @classmethod
    def create(cls, source: Path, target: Path, recursive: bool = True) -> Workspace:
        ws = cls(source, target, recursive)
        if not ws.source.is_dir():
            raise FileNotFoundError(f"Quellordner nicht gefunden: {ws.source}")
        ws.target.mkdir(parents=True, exist_ok=True)
        ws.sync_with_disk()
        ws.save()
        return ws

    @staticmethod
    def _read(target: Path) -> dict:
        """Inhalt der Datei des Arbeitsstands (neuer oder alter Name), Format geprüft."""
        path = Path(target) / WORKSPACE_NAME
        if not path.is_file():
            path = Path(target) / OLD_WORKSPACE_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("source"), str):
            raise ValueError("Der Arbeitsstand ist beschädigt (kein Quellordner).")
        if data.get("version", 0) > FORMAT_VERSION:
            raise ValueError("Der Arbeitsstand stammt aus einer neueren Programmversion.")
        return data

    @classmethod
    def stored_source(cls, target: Path) -> tuple[str, bool]:
        """Quellordner und „mit Unterordnern“ laut Arbeitsstand – nur gelesen, ohne auf den Ordner zuzugreifen.
        Wer in den Zielordner schreiben darf, kann hier einen anderen Ordner eintragen (auch einen
        Netzwerkpfad, bei dem sich Windows dort anmeldet). Deshalb erst vergleichen bzw. bestätigen lassen, dann
        :meth:`load`."""
        data = cls._read(target)
        return data["source"], bool(data.get("recursive", True))

    @classmethod
    def load(cls, target: Path) -> Workspace:
        data = cls._read(target)
        ws = cls(Path(data["source"]), Path(target), data.get("recursive", True))
        ws.created = data.get("created", ws.created)
        ws.updated = data.get("updated", ws.updated)
        known = FileEntry.__dataclass_fields__
        for rel, raw in data.get("files", {}).items():
            e = FileEntry(rel=rel, **{k: v for k, v in raw.items() if k in known and k != "rel"})
            if not keeps_file_sha(rel):
                e.sha256 = ""           # Format 1: Prüfsumme einer Textdatei = Prüfsumme ihres Texts
            ws.entries[rel] = e
        return ws

    @staticmethod
    def exists_in(target: Path) -> bool:
        return (Path(target) / WORKSPACE_NAME).is_file() or (Path(target) / OLD_WORKSPACE_NAME).is_file()

    def save(self) -> None:
        self.updated = now_iso()
        data = {
            "version": FORMAT_VERSION,
            "hinweis": WORKSPACE_HINT,
            "source": str(self.source),
            "target": str(self.target),
            "recursive": self.recursive,
            "created": self.created,
            "updated": self.updated,
            "files": {rel: {k: v for k, v in asdict(e).items() if k != "rel"}
                      for rel, e in sorted(self.entries.items())},
        }
        self.target.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        (self.target / OLD_WORKSPACE_NAME).unlink(missing_ok=True)     # bis 0.8.7 hieß die Datei so
        self.write_protocol()

    def sync_with_disk(self) -> list[str]:
        """Neue, fehlende und geänderte Dateien erkennen. Rückgabe: Dateien, die (neu) analysiert
        werden müssen. Geänderte Originale verlieren ihre Funde und ihren Prüfstatus."""
        if not self.source.is_dir():
            raise FileNotFoundError(f"Quellordner nicht gefunden: {self.source}")
        present = set(scan_folder(self.source, self.recursive, exclude=self.target))
        todo: list[str] = []
        for rel in present:
            p = self.abs(rel)
            st = p.stat()
            e = self.entries.get(rel)
            if e is None:
                self.entries[rel] = FileEntry(rel=rel, size=st.st_size, mtime_ns=st.st_mtime_ns)
                todo.append(rel)
                continue
            if e.status == Status.MISSING:
                e.status = Status.WAITING
            if e.size != st.st_size or e.mtime_ns != st.st_mtime_ns:
                # Zeitstempel/Größe weichen ab → bei PDFs entscheidet die Prüfsumme (Kopieren ändert nur die Zeit);
                # TXT/Markdown haben keine (siehe Modulbeschreibung) und werden erneut geprüft
                e.size, e.mtime_ns = st.st_size, st.st_mtime_ns
                if e.sha256 and sha256_of(p) == e.sha256:
                    continue
                self._reset(e, "Original seit der Analyse geändert – bitte erneut prüfen")
                todo.append(rel)
            elif not e.analyzed:
                todo.append(rel)
        for rel, e in self.entries.items():
            if rel not in present and e.status != Status.MISSING:
                e.status = Status.MISSING
                e.note = "Datei im Quellordner nicht mehr vorhanden"
        return sorted(todo, key=str.casefold)

    @staticmethod
    def _reset(e: FileEntry, note: str) -> None:
        e.findings = None
        e.sha256 = e.layout_sha = e.text_sha = ""
        e.status = Status.WAITING
        e.reviewed_by = e.reviewed_at = ""
        e.stats = {}
        e.ki_review = {}
        e.note = note

    # ------------------------------------------------------------------ Zustandsänderungen
    def set_analysis(self, rel: str, *, sha256: str, size: int, mtime_ns: int, findings: list[Finding],
                     warnings: list[str], mode: str, layout_sha: str = "", ocr_pages: list[int] | None = None) -> None:
        e = self.entries[rel]
        e.sha256 = sha256 if keeps_file_sha(rel) else ""
        e.size, e.mtime_ns = size, mtime_ns
        e.layout_sha, e.text_sha = layout_sha, ""
        e.ocr_pages = list(ocr_pages or [])
        e.findings = findings_to_compact(findings)
        e.warnings = list(warnings)
        e.analysis_mode = mode
        e.analyzed_at = now_iso()
        e.error = ""
        e.stats = finding_stats(findings)
        e.ki_review = {}  # neue Analyse → frühere KI-Bewertung passt nicht mehr
        if e.status in (Status.WAITING, Status.ERROR):
            e.status = Status.TO_REVIEW

    def set_analysis_result(self, rel: str, result: dict) -> None:
        """Ergebnis von :func:`analyze_file` übernehmen."""
        self.set_analysis(rel, sha256=result["sha256"], size=result["size"], mtime_ns=result["mtime_ns"],
                          findings=result["findings"], warnings=result["warnings"], mode=result["mode"],
                          layout_sha=result.get("layout_sha", ""), ocr_pages=result.get("ocr_pages"))

    def restore_findings(self, rel: str, doc: LoadedDocument) -> list[Finding] | None:
        """Gespeicherte Funde für das geladene Dokument – oder None, wenn der erkannte Text nicht mehr
        zu den gespeicherten Positionen passt (z. B. andere Programmversion, Texterkennung ein/aus) oder sich das
        nicht prüfen lässt (keine Prüfsumme). Der Eintrag wird dann zur erneuten Analyse zurückgesetzt."""
        e = self.entries[rel]
        if e.findings is None:
            return None
        if e.layout_sha:
            fits = e.layout_sha == layout_sha(doc.text)
        elif e.text_sha:                                  # Format 1: einmal mit der alten Prüfsumme vergleichen
            fits = e.text_sha == text_sha(doc.text)
            if fits:
                e.layout_sha, e.text_sha = layout_sha(doc.text), ""
        else:
            fits = False                                  # ohne Prüfsumme nicht ungeprüft übernehmen
        if not fits:
            self._reset(e, "Erkannter Text hat sich geändert (z. B. Texterkennung) – neu analysiert, "
                           "bitte erneut prüfen")
            return None
        return findings_from_compact(e.findings, doc.text)

    def set_ki_review(self, rel: str, summary: dict) -> None:
        self.entries[rel].ki_review = dict(summary)

    def set_error(self, rel: str, message: str) -> None:
        e = self.entries[rel]
        e.status = Status.ERROR
        e.error = message

    def update_findings(self, rel: str, findings: list[Finding]) -> None:
        """Nachbearbeitung speichern. Eine bereits geprüfte Datei muss danach erneut bestätigt werden."""
        e = self.entries[rel]
        compact = findings_to_compact(findings)
        if compact == e.findings:
            return
        e.findings = compact
        e.stats = finding_stats(findings)
        if e.status in Status.DONE:
            e.status = Status.TO_REVIEW
            e.note = "nach der Prüfung geändert – bitte erneut bestätigen"

    def hold_for_check(self, rel: str, findings: list[Finding]) -> None:
        """Datei bleibt zur Prüfung stehen; die Vorschläge der Kontrolle stehen beim Öffnen in der Fundliste."""
        e = self.entries[rel]
        e.findings = findings_to_compact(findings)
        e.stats = finding_stats(findings)
        e.note = CHECK_NOT_AUTO

    def mark_done(self, rel: str, *, reviewed: bool, exported_as: Path, leftovers: list[str],
                  findings: list[Finding]) -> None:
        e = self.entries[rel]
        e.findings = findings_to_compact(findings)
        e.stats = finding_stats(findings)
        e.status = Status.REVIEWED if reviewed else Status.AUTO
        e.reviewed_by = current_user() if reviewed else ""
        e.reviewed_at = now_iso()
        e.exported_as = exported_as.relative_to(self.target).as_posix()
        e.export_note = (f"Kontrolle: {len(leftovers)} Rest(e) im Ergebnis gefunden" if leftovers else "")
        e.note = ""

    # ------------------------------------------------------------------ Protokoll
    def write_protocol(self) -> None:
        cols = ["Datei", "Status", "Geprüft von", "Zeitpunkt", "Exportiert als", "Funde gesamt", "Geschwärzt",
                "Datenarten", "Manuell hinzugefügt", "Bereiche", "Abgewählt", "Typ geändert", "Analyse-Modus",
                "OCR-Seiten", "KI-Prüfung", "Hinweise"]
        tmp = self.protocol_path.with_name(self.protocol_path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(cols)
            for e in self.ordered():
                st = e.stats or {}
                hints = [w for w in e.warnings if not w.startswith(OCR_WARNING_PREFIX)]
                if e.has_ocr:
                    hints.insert(0, "Texterkennung (OCR) – erhöhte Fehlerquote, gründliche Prüfung nötig")
                if e.export_note:
                    hints.append(e.export_note)
                if e.note:
                    hints.append(e.note)
                if e.error:
                    hints.append(f"Fehler: {e.error.splitlines()[0]}")
                w.writerow(csv_safe(cell) for cell in [
                    e.rel,
                    Status.LABELS.get(e.status, e.status),
                    e.reviewed_by,
                    e.reviewed_at if e.done else "",
                    e.exported_as if e.done else "",
                    st.get("total", ""),
                    st.get("active", ""),
                    ", ".join(f"{k}: {v}" for k, v in (st.get("per_type") or {}).items()),
                    st.get("added", ""),
                    st.get("areas", ""),
                    st.get("deselected", ""),
                    st.get("retyped", ""),
                    {"schnell": "Schnell", "gruendlich": "Gründlich"}.get(e.analysis_mode, e.analysis_mode),
                    ", ".join(map(str, e.ocr_pages)),
                    ki_summary(e.ki_review),
                    " | ".join(hints),
                ])
        try:
            os.replace(tmp, self.protocol_path)
        except PermissionError:
            # Protokoll ist z. B. in Excel geöffnet – Arbeitsstand ist trotzdem gesichert
            alt = self.protocol_path.with_name("pii-redact-protokoll (aktuell).csv")
            os.replace(tmp, alt)


def analyze_file(path: Path, analyzer: PiiAnalyzer, settings: Settings,
                 progress: ProgressFn | None = None) -> dict:
    """Datei laden, Prüfsumme bilden, analysieren – läuft im Hintergrund-Thread."""
    from .loaders import load_document

    st = path.stat()
    digest = sha256_of(path) if keeps_file_sha(path.name) else ""
    doc = load_document(path, ocr=settings.ocr)
    findings = analyzer.analyze(doc.text, settings, progress=progress)
    return {
        "layout_sha": layout_sha(doc.text),
        "ocr_pages": [p + 1 for p in doc.ocr_pages],
        "sha256": digest,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "findings": findings,
        "warnings": list(doc.warnings),
        "mode": settings.analysis_mode,
    }
