"""Dokumente laden: TXT, Markdown und PDF.

Für PDFs wird der Text zeichenweise mit Bounding-Box extrahiert, damit jeder Fund
später exakt auf Rechtecke im PDF abgebildet und dort echt geschwärzt werden kann.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf as fitz  # PyMuPDF

#: Anfang des Hinweises auf per OCR gelesene Seiten (die Oberfläche zeigt ihn gesondert an).
OCR_WARNING_PREFIX = "Per Texterkennung (OCR)"

SUPPORTED_SUFFIXES = {".txt": "txt", ".text": "txt", ".log": "txt", ".md": "md", ".markdown": "md", ".pdf": "pdf"}


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
    warnings: list[str] = field(default_factory=list)
    #: Seiten (0-basiert), deren Text ganz oder teilweise per OCR gelesen wurde.
    ocr_pages: list[int] = field(default_factory=list)
    #: Anzahl unsicher erkannter Wörter.
    ocr_uncertain: int = 0

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
        raise UnsupportedFileError(
            f"Dateityp {path.suffix or '(ohne Endung)'} wird nicht unterstützt. Erlaubt: PDF, TXT, Markdown."
        )
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
    warnings: list[str] = []
    line_no = 0
    offset = 0
    pages_without_text: list[int] = []
    pages_with_images: list[int] = []
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
        if page.get_images(full=False) and pno not in ocr_pages:
            pages_with_images.append(pno + 1)

    text = "".join(parts)
    assert len(text) == len(boxes), "Interner Fehler bei der PDF-Textzuordnung"

    if ocr_pages:
        msg = (f"{OCR_WARNING_PREFIX} gelesene Seiten: {_fmt_pages([p + 1 for p in ocr_pages])}. Die Texterkennung macht "
               "Fehler – falsch gelesene Namen oder Nummern werden nicht erkannt. Diese Seiten bitte Zeile für "
               "Zeile vollständig prüfen.")
        if ocr_low:
            n = len(ocr_low)
            msg += f" {n} Wort wurde nur unsicher erkannt." if n == 1 else f" {n} Wörter wurden nur unsicher erkannt."
        warnings.append(msg)
    if ocr_error:
        warnings.append(ocr_error)
    if pages_without_text:
        if len(pages_without_text) == doc.page_count:
            warnings.append(
                "Das PDF enthält keine lesbare Textebene (vermutlich gescannt) – es können keine Daten erkannt "
                "werden." + ("" if ocr else " Die Texterkennung (OCR) ist in den Einstellungen ausgeschaltet.")
            )
        else:
            warnings.append(f"Seiten ohne Text (nicht geprüft): {_fmt_pages(pages_without_text)}")
    removed = []
    if extras["annots"]:
        removed.append(_count(extras["annots"], "Kommentar/Markierung", "Kommentare/Markierungen")
                       + " (in der Ansicht bereits ausgeblendet)")
    if extras["files"]:
        removed.append(_count(extras["files"], "Dateianhang", "Dateianhänge") + " (Inhalt wird nicht geprüft)")
    if removed:
        warnings.append("Wird beim Schwärzen entfernt: " + ", ".join(removed) + ".")
    if extras["widgets"]:
        warnings.append(
            _count(extras["widgets"], "Formularfeld", "Formularfelder") + " gefunden: Inhalte werden wie normaler "
            "Text geprüft und sind im Ergebnis nicht mehr ausfüllbar."
        )
    if pages_with_images:
        warnings.append(
            f"Seiten mit Bildern: {_fmt_pages(pages_with_images)} – Inhalte in Bildern (z. B. Unterschrift, Foto) "
            "werden nicht erkannt. Bei Bedarf in der Seitenansicht einen Rahmen darum ziehen."
        )

    result = LoadedDocument(
        path=path,
        kind="pdf",
        text=text,
        pdf_bytes=data,
        char_boxes=boxes,
        page_count=doc.page_count,
        page_offsets=page_offsets,
        warnings=warnings,
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

    Ändert ``pdf`` direkt. Rückgabe: Anzahl der gefundenen Elemente je Art."""
    counts = {"annots": 0, "widgets": 0, "toc": 0, "files": 0}
    for page in pdf:
        counts["widgets"] += sum(1 for _ in page.widgets())
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


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _fmt_pages(pages: list[int]) -> str:
    if len(pages) > 12:
        return ", ".join(map(str, pages[:12])) + f" … (+{len(pages) - 12})"
    return ", ".join(map(str, pages))
