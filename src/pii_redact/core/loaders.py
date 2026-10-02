"""Dokumente laden: TXT, Markdown und PDF.

Für PDFs wird der Text zeichenweise mit Bounding-Box extrahiert, damit jeder Fund
später exakt auf Rechtecke im PDF abgebildet und dort echt geschwärzt werden kann.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

#: Anfang des Hinweises auf per OCR gelesene Seiten (die Oberfläche zeigt ihn gesondert an).
OCR_WARNING_PREFIX = "Per Texterkennung (OCR)"

SUPPORTED_SUFFIXES = {".txt": "txt", ".text": "txt", ".log": "txt", ".md": "md", ".markdown": "md", ".pdf": "pdf"}
#: Office-Formate werden bewusst nicht unterstützt (ein Format – PDF – dafür gründlich); Hinweis auf den Umweg.
OFFICE_SUFFIXES = {".doc", ".docx", ".docm", ".dot", ".dotx", ".rtf", ".odt",
                   ".xls", ".xlsx", ".xlsm", ".ods",
                   ".ppt", ".pptx", ".pptm", ".odp"}
OFFICE_HINT = ("Word-, Excel- und PowerPoint-Dateien bitte zuerst im jeweiligen Programm als PDF speichern "
               "(Datei → Speichern unter → PDF) und die PDF-Datei öffnen.")


class Level:
    CRITICAL = "kritisch"   # betrifft die Sicherheit des Ergebnisses – bleibt sichtbar
    INFO = "info"           # nur zur Information – erscheint in der Hinweisliste bzw. an der Seite


@dataclass
class Notice:
    """Hinweis zu einem geladenen Dokument.

    ``short`` ist eine Zeile für Hinweisleiste und Liste, ``detail`` die ausführliche Fassung (Tooltip,
    Protokoll, Kommandozeile), ``badge`` die Beschriftung des Seitensymbols in der Seitenansicht."""
    kind: str                     # "ocr", "ocr_error", "no_text", "pages_no_text", "removed", "widgets", "images"
    level: str
    short: str
    detail: str
    pages: list[int] = field(default_factory=list)   # betroffene Seiten (0-basiert)
    badge: str = ""

    @property
    def critical(self) -> bool:
        return self.level == Level.CRITICAL


@dataclass
class CharBox:
    page: int
    line: int              # fortlaufende Zeilennummer (dokumentweit)
    rect: tuple[float, float, float, float]
    ocr: bool = False      # per Texterkennung gelesen (ungenauer, fehleranfälliger)


@dataclass
class LoadedDocument:
    path: Path
    kind: str                          # "txt" | "md" | "pdf"
    text: str
    encoding: str = "utf-8"
    pdf_bytes: bytes | None = None
    #: Pro Zeichen in ``text`` die Position im PDF (None für eingefügte Zeilenumbrüche).
    char_boxes: list[CharBox | None] | None = None
    page_count: int = 0
    #: Zeichen-Offset, an dem jede PDF-Seite im Text beginnt.
    page_offsets: list[int] = field(default_factory=list)
    #: Ausführliche Hinweistexte (für Protokoll und Kommandozeile), entspricht ``notices``.
    warnings: list[str] = field(default_factory=list)
    #: Strukturierte Hinweise (Stufe, Kurztext, betroffene Seiten) für die Oberfläche.
    notices: list[Notice] = field(default_factory=list)
    #: Seiten (0-basiert), deren Text ganz oder teilweise per OCR gelesen wurde.
    ocr_pages: list[int] = field(default_factory=list)
    #: Anzahl unsicher erkannter Wörter.
    ocr_uncertain: int = 0

    @property
    def critical_notices(self) -> list[Notice]:
        return [n for n in self.notices if n.critical]

    @property
    def has_ocr(self) -> bool:
        return bool(self.ocr_pages)

    def is_ocr(self, start: int, end: int) -> bool:
        """Liegt der Textbereich (teilweise) in per OCR gelesenem Text?"""
        if not self.char_boxes or not self.ocr_pages:
            return False
        return any(b is not None and b.ocr for b in self.char_boxes[max(start, 0):end])

    @property
    def is_pdf(self) -> bool:
        return self.kind == "pdf"

    def page_of(self, offset: int) -> int | None:
        """Seitennummer (0-basiert) zu einem Text-Offset."""
        if not self.page_offsets:
            return None
        page = 0
        for i, start in enumerate(self.page_offsets):
            if offset >= start:
                page = i
            else:
                break
        return page


class UnsupportedFileError(ValueError):
    pass


def load_document(path: str | Path, *, ocr: bool = True,
                  progress: Callable[[int, int], None] | None = None) -> LoadedDocument:
    """Lädt ein Dokument. Bei PDFs werden gescannte Seiten per OCR gelesen, wenn ``ocr`` gesetzt ist
    (``progress(fertig, gesamt)`` meldet dabei den Fortschritt über die OCR-Seiten)."""
    path = Path(path)
    kind = SUPPORTED_SUFFIXES.get(path.suffix.lower())
    if kind is None:
        msg = f"Dateityp {path.suffix or '(ohne Endung)'} wird nicht unterstützt. Erlaubt: PDF, TXT, Markdown."
        if path.suffix.lower() in OFFICE_SUFFIXES:
            msg += " " + OFFICE_HINT
        raise UnsupportedFileError(msg)
    if kind == "pdf":
        return _load_pdf(path, ocr=ocr, progress=progress)
    return _load_text(path, kind)


def _decode(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _load_text(path: Path, kind: str) -> LoadedDocument:
    text, enc = _decode(path.read_bytes())
    # Einheitliche Zeilenumbrüche – Offsets müssen zur Anzeige passen.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return LoadedDocument(path=path, kind=kind, text=text, encoding=enc)


def _load_pdf(path: Path, ocr: bool = True, progress=None) -> LoadedDocument:
    data = path.read_bytes()
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        raise UnsupportedFileError("Das PDF ist passwortgeschützt.")

    # Kommentare, Lesezeichen und Formularfelder vorab bereinigen: Ansicht, Analyse und
    # Ergebnis beruhen damit auf demselben Inhalt.
    extras = strip_pdf_extras(doc)
    if extras["annots"] or extras["widgets"] or extras["toc"]:
        data = doc.tobytes()
        doc.close()
        doc = fitz.open(stream=data, filetype="pdf")

    parts: list[str] = []
    boxes: list[CharBox | None] = []
    page_offsets: list[int] = []
    line_no = 0
    offset = 0
    pages_without_text: list[int] = []
    pages_with_images: list[int] = []   # 1-basiert
    ocr_pages: list[int] = []
    ocr_low: list[str] = []
    ocr_error = ""

    def add(c: str, box: CharBox | None) -> None:
        nonlocal offset
        parts.append(c)
        boxes.append(box)
        offset += 1

    # Welche Seiten brauchen OCR? (erst zählen, damit der Fortschritt stimmt)
    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    raws = [page.get_text("rawdict", flags=flags) for page in doc]
    char_counts = [sum(len(ch["c"]) for b in r.get("blocks", []) if b.get("type") == 0
                       for ln in b.get("lines", []) for sp in ln.get("spans", []) for ch in sp.get("chars", []))
                   for r in raws]
    want_ocr = []
    if ocr:
        from . import ocr as ocr_mod

        want_ocr = [pno for pno, page in enumerate(doc) if ocr_mod.needs_ocr(page, char_counts[pno])]
        if want_ocr and not ocr_mod.is_available():
            ocr_error = "Texterkennung (OCR) ist nicht installiert."
            want_ocr = []
    key = None
    if want_ocr:
        key = ocr_mod.doc_key(data)
    content_images = _content_image_pages(doc)

    for pno, page in enumerate(doc):
        page_offsets.append(offset)
        page_chars = 0
        text_rects: list[fitz.Rect] = []
        for block in raws[pno].get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    for ch in span.get("chars", []):
                        box = CharBox(pno, line_no, tuple(ch["bbox"]))
                        if want_ocr and pno in want_ocr and ch["c"].strip():
                            text_rects.append(fitz.Rect(ch["bbox"]))
                        for c in ch["c"]:  # i. d. R. genau ein Zeichen
                            add(c, box)
                            page_chars += 1
                add("\n", None)
                line_no += 1
            add("\n", None)

        if pno in want_ocr:
            if progress:
                progress(want_ocr.index(pno), len(want_ocr))
            try:
                lines = ocr_mod.ocr_page(page, key)
            except Exception as exc:  # noqa: BLE001 – OCR ist nur Rückfallebene
                ocr_error = f"Texterkennung fehlgeschlagen: {exc}"
                lines = []
            added = 0
            for words in lines:
                # Wörter, die schon als echter Text vorliegen (z. B. Kopfzeile), nicht doppelt aufnehmen
                words = [w for w in _split_glued(words) if not _covered(fitz.Rect(w.rect), text_rects)]
                if not words:
                    continue
                for i, w in enumerate(words):
                    if i:
                        prev = words[i - 1].rect
                        gap = (prev[2], min(prev[1], w.rect[1]), max(prev[2], w.rect[0]), max(prev[3], w.rect[3]))
                        add(" ", CharBox(pno, line_no, gap, ocr=True))
                    x0, y0, x1, y1 = w.rect
                    n = len(w.text)
                    for k, c in enumerate(w.text):
                        add(c, CharBox(pno, line_no, (x0 + (x1 - x0) * k / n, y0, x0 + (x1 - x0) * (k + 1) / n, y1),
                                       ocr=True))
                        added += 1
                    if w.score < ocr_mod.LOW_CONFIDENCE:
                        ocr_low.append(w.text)
                add("\n", None)
                line_no += 1
            if added:
                ocr_pages.append(pno)
                page_chars += added
                add("\n", None)
            if progress:
                progress(want_ocr.index(pno) + 1, len(want_ocr))

        if page_chars == 0:
            pages_without_text.append(pno + 1)
        if pno not in ocr_pages and pno in content_images:
            pages_with_images.append(pno + 1)

    text = "".join(parts)
    assert len(text) == len(boxes), "Interner Fehler bei der PDF-Textzuordnung"

    notices: list[Notice] = []
    if ocr_pages:
        pages_txt = _fmt_pages([p + 1 for p in ocr_pages])
        seite = "Seite" if len(ocr_pages) == 1 else "Seiten"
        msg = (f"{OCR_WARNING_PREFIX} gelesene Seiten: {pages_txt}. Die Texterkennung macht "
               "Fehler – falsch gelesene Namen oder Nummern werden nicht erkannt. Diese Seiten bitte Zeile für "
               "Zeile vollständig prüfen.")
        if ocr_low:
            n = len(ocr_low)
            msg += f" {n} Wort wurde nur unsicher erkannt." if n == 1 else f" {n} Wörter wurden nur unsicher erkannt."
        notices.append(Notice("ocr", Level.CRITICAL,
                              f"{seite} {pages_txt} per Texterkennung gelesen – bitte vollständig prüfen",
                              msg, list(ocr_pages), "OCR – Seite vollständig prüfen"))
    if ocr_error:
        notices.append(Notice("ocr_error", Level.CRITICAL, ocr_error, ocr_error))
    if pages_without_text:
        if len(pages_without_text) == doc.page_count:
            off = "" if ocr else " Die Texterkennung (OCR) ist in den Einstellungen ausgeschaltet."
            notices.append(Notice(
                "no_text", Level.CRITICAL, "Kein lesbarer Text – es können keine Daten erkannt werden",
                "Das PDF enthält keine lesbare Textebene (vermutlich gescannt) – es können keine Daten erkannt "
                "werden." + off, [p - 1 for p in pages_without_text], "ohne Text – nicht geprüft"))
        else:
            pages_txt = _fmt_pages(pages_without_text)
            notices.append(Notice(
                "pages_no_text", Level.CRITICAL, f"{_seiten(pages_without_text)} ohne Text – nicht geprüft",
                f"Seiten ohne Text (nicht geprüft): {pages_txt}", [p - 1 for p in pages_without_text],
                "ohne Text – nicht geprüft"))
    removed = []
    if extras["annots"]:
        removed.append(_count(extras["annots"], "Kommentar/Markierung", "Kommentare/Markierungen")
                       + " (in der Ansicht bereits ausgeblendet)")
    if extras["toc"]:
        removed.append(_count(extras["toc"], "Lesezeichen", "Lesezeichen"))
    if extras["files"]:
        removed.append(_count(extras["files"], "Dateianhang", "Dateianhänge") + " (Inhalt wird nicht geprüft)")
    if removed:
        short = ", ".join(r.split(" (")[0] for r in removed)
        notices.append(Notice("removed", Level.INFO, f"Wird beim Schwärzen entfernt: {short}",
                              "Wird beim Schwärzen entfernt: " + ", ".join(removed) + "."))
    if extras["widgets"]:
        notices.append(Notice(
            "widgets", Level.INFO,
            _count(extras["widgets"], "Formularfeld", "Formularfelder") + " – Inhalte werden wie Text geprüft",
            _count(extras["widgets"], "Formularfeld", "Formularfelder") + " gefunden: Inhalte werden wie normaler "
            "Text geprüft und sind im Ergebnis nicht mehr ausfüllbar.",
            sorted(extras.get("widget_pages", [])), "Formular"))
    if pages_with_images:
        pages_txt = _fmt_pages(pages_with_images)
        notices.append(Notice(
            "images", Level.INFO, f"Bilder auf {_seiten(pages_with_images)} – Inhalt wird nicht erkannt",
            f"Seiten mit Bildern: {pages_txt} – Inhalte in Bildern (z. B. Unterschrift, Foto) "
            "werden nicht erkannt. Bei Bedarf in der Seitenansicht einen Rahmen darum ziehen.",
            [p - 1 for p in pages_with_images], "Bild"))
    warnings = [n.detail for n in notices]

    result = LoadedDocument(
        path=path,
        kind="pdf",
        text=text,
        pdf_bytes=data,
        char_boxes=boxes,
        page_count=doc.page_count,
        page_offsets=page_offsets,
        warnings=warnings,
        notices=notices,
        ocr_pages=ocr_pages,
        ocr_uncertain=len(ocr_low),
    )
    doc.close()
    return result


def strip_pdf_extras(pdf: fitz.Document) -> dict[str, int]:
    """Entfernt alles, was neben dem Seiteninhalt personenbezogene Daten tragen kann und nicht
    gezielt geschwärzt wird:

    * Kommentare und Markierungen (Notizen, Hervorhebungen, Stempel, Freitext, Dateianhänge …)
      samt Autorenname werden gelöscht,
    * Lesezeichen (Inhaltsverzeichnis in der Seitenleiste) werden gelöscht,
    * Formularfelder werden in festen Seiteninhalt umgewandelt – ihre Werte sind danach
      normaler Text, der geprüft und exakt geschwärzt wird.

    Ändert ``pdf`` direkt. Rückgabe: Anzahl der gefundenen Elemente je Art (dazu ``widget_pages``:
    Seiten mit Formularfeldern)."""
    counts: dict = {"annots": 0, "widgets": 0, "toc": 0, "files": 0}
    widget_pages: list[int] = []
    for page in pdf:
        n = sum(1 for _ in page.widgets())
        counts["widgets"] += n
        if n:
            widget_pages.append(page.number)
        counts["annots"] += sum(1 for a in page.annots() if a.type[0] != fitz.PDF_ANNOT_POPUP)
    counts["toc"] = len(pdf.get_toc(simple=True))
    counts["files"] = pdf.embfile_count()
    if counts["widgets"]:
        pdf.bake(annots=False, widgets=True)
    for page in pdf:
        for _ in range(10000):  # Schutz gegen Endlosschleife bei defekten PDFs
            annot = page.first_annot
            if annot is None:
                break
            page.delete_annot(annot)
    if counts["toc"]:
        pdf.set_toc([])
    counts["widget_pages"] = widget_pages  # type: ignore[assignment]
    return counts


_GLUED = re.compile(r"^([A-Za-zÄÖÜäöüß][\w\-.]*[A-Za-zäöüß]\.?:)(\w.*)$")


def _split_glued(words: list) -> list:
    """OCR verschluckt oft das Leerzeichen nach Doppelpunkten („Geburtsdatum:17.11.1984“).
    Solche Wörter werden getrennt, damit die Erkennung den Wert als eigenes Wort sieht."""
    from .ocr import OcrWord

    out = []
    for w in words:
        m = _GLUED.match(w.text)
        if not m or "//" in w.text:
            out.append(w)
            continue
        a, b = m.group(1), m.group(2)
        x0, y0, x1, y1 = w.rect
        cut = x0 + (x1 - x0) * len(a) / len(w.text)
        out.append(OcrWord(a, w.score, (x0, y0, cut, y1)))
        out.append(OcrWord(b, w.score, (cut, y0, x1, y1)))
    return out


def _covered(r: fitz.Rect, text_rects: list[fitz.Rect]) -> bool:
    """Überdeckt vorhandener Text das OCR-Wort größtenteils?"""
    if r.is_empty or not text_rects:
        return False
    hit = 0.0
    for t in text_rects:
        i = r & t
        if not i.is_empty:
            hit += abs(i)
    return hit >= 0.4 * abs(r)


#: Bilder unter diesem Anteil der Seitenfläche gelten als Zierelement (Aufzählungszeichen, Linien, Symbole).
MIN_IMAGE_SHARE = 0.003


def _content_image_pages(pdf: fitz.Document) -> set[int]:
    """Seiten (0-basiert) mit Bildern, die Inhalte tragen könnten (Foto, Unterschrift, eingefügter Scan).

    Nicht gezählt werden winzige Bilder und Bilder, die auf mehreren Seiten wiederkehren
    (Briefkopf-Logo, Fußzeile) – sonst stünde der Hinweis an fast jedem PDF."""
    per_page: list[list[tuple[int, float]]] = []
    seen_on: dict[int, set[int]] = {}
    for page in pdf:
        area = abs(page.rect) or 1.0
        items = []
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:  # noqa: BLE001 – defekte Bilddaten: vorsichtshalber als Inhalt werten
            infos = [{"xref": 0, "bbox": tuple(page.rect)}]
        for im in infos:
            share = abs(fitz.Rect(im.get("bbox") or (0, 0, 0, 0)) & page.rect) / area
            xref = int(im.get("xref") or 0)
            items.append((xref, share))
            if xref:
                seen_on.setdefault(xref, set()).add(page.number)
        per_page.append(items)
    out = set()
    multi = pdf.page_count > 1
    for pno, items in enumerate(per_page):
        for xref, share in items:
            if share < MIN_IMAGE_SHARE:
                continue
            if multi and xref and len(seen_on.get(xref, ())) > 1:
                continue
            out.add(pno)
            break
    return out


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _seiten(pages: list[int]) -> str:
    """„Seite 3“ bzw. „Seiten 3, 4“ (Seitenzahlen 1-basiert)."""
    return f"{'Seite' if len(pages) == 1 else 'Seiten'} {_fmt_pages(pages)}"


def _fmt_pages(pages: list[int]) -> str:
    if len(pages) > 12:
        return ", ".join(map(str, pages[:12])) + f" … (+{len(pages) - 12})"
    return ", ".join(map(str, pages))
