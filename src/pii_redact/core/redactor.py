"""Anwenden der Funde: Ersetzung im Text und echte Schwärzung im PDF."""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from .entities import info
from .loaders import LoadedDocument, strip_pdf_extras
from .models import Finding, ReplaceMode, Settings

BLACK_CHAR = "█"


# ====================================================================== Text
@dataclass
class Segment:
    orig_start: int
    orig_end: int
    red_start: int
    red_end: int
    finding_ids: list[int]
    replacement: str


@dataclass
class RedactedText:
    text: str
    segments: list[Segment] = field(default_factory=list)

    def __post_init__(self):
        self._orig_starts = [s.orig_start for s in self.segments]
        self._red_starts = [s.red_start for s in self.segments]

    def to_redacted(self, orig_offset: int) -> int:
        """Offset im Original → Offset im anonymisierten Text."""
        i = bisect.bisect_right(self._orig_starts, orig_offset) - 1
        if i < 0:
            return orig_offset
        s = self.segments[i]
        if orig_offset < s.orig_end:
            return s.red_start
        return s.red_end + (orig_offset - s.orig_end)

    def to_original(self, red_offset: int) -> int:
        """Offset im anonymisierten Text → Offset im Original."""
        i = bisect.bisect_right(self._red_starts, red_offset) - 1
        if i < 0:
            return red_offset
        s = self.segments[i]
        if red_offset < s.red_end:
            return s.orig_start
        return s.orig_end + (red_offset - s.red_end)

    def to_original_end(self, red_offset: int) -> int:
        """Wie :meth:`to_original`, aber für ein Bereichsende: Endet eine Markierung mitten in einem
        Platzhalter, zählt der ganze ersetzte Originalbereich dazu."""
        i = bisect.bisect_right(self._red_starts, red_offset) - 1
        if i >= 0:
            s = self.segments[i]
            if s.red_start < red_offset < s.red_end:
                return s.orig_end
        return self.to_original(red_offset)

    def segment_at_redacted(self, red_offset: int) -> Segment | None:
        i = bisect.bisect_right(self._red_starts, red_offset) - 1
        if i >= 0 and self.segments[i].red_start <= red_offset < self.segments[i].red_end:
            return self.segments[i]
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def replacement_tokens(findings: list[Finding], mode: str) -> dict[int, str]:
    """Ersetzungstext je Fund-ID. Im Modus „nummeriert“ bekommen gleiche Werte gleiche Nummern."""
    tokens: dict[int, str] = {}
    counters: dict[str, int] = {}
    seen: dict[tuple[str, str], int] = {}
    for f in sorted(findings, key=lambda f: f.start):
        tok = info(f.entity_type).token
        if mode == ReplaceMode.BLACKOUT:
            tokens[f.id] = BLACK_CHAR * max(len(f.text), 3)
        elif mode == ReplaceMode.NUMBERED:
            key = (tok, _normalize(f.text))
            if key not in seen:
                counters[tok] = counters.get(tok, 0) + 1
                seen[key] = counters[tok]
            tokens[f.id] = f"[{tok}_{seen[key]}]"
        else:
            tokens[f.id] = f"[{tok}]"
    return tokens


def active_groups(findings: list[Finding]) -> list[list[Finding]]:
    """Aktive Funde, überlappende zu Gruppen zusammengefasst, nach Position sortiert."""
    groups: list[list[Finding]] = []
    end = -1
    for f in sorted((f for f in findings if f.active and f.area is None), key=lambda f: (f.start, -f.end)):
        if groups and f.start < end:
            groups[-1].append(f)
            end = max(end, f.end)
        else:
            groups.append([f])
            end = f.end
    return groups


def redact_text(text: str, findings: list[Finding], mode: str) -> RedactedText:
    groups = active_groups(findings)
    lead = [g[0] for g in groups]
    tokens = replacement_tokens(lead, mode)
    out: list[str] = []
    segments: list[Segment] = []
    pos = 0
    red_len = 0
    for g in groups:
        s = g[0].start
        e = max(f.end for f in g)
        if mode == ReplaceMode.BLACKOUT:
            # gleiche Länge, Zeilenumbrüche bleiben erhalten (stabiles Layout)
            repl ="".join("\n" if text[i] == "\n" else BLACK_CHAR for i in range(s, e))
        else:
            repl = tokens[g[0].id]
        chunk = text[pos:s]
        out.append(chunk)
        red_len += len(chunk)
        out.append(repl)
        segments.append(Segment(s, e, red_len, red_len + len(repl), [f.id for f in g], repl))
        red_len += len(repl)
        pos = e
    out.append(text[pos:])
    return RedactedText("".join(out), segments)


# ====================================================================== PDF
def finding_rects(doc: LoadedDocument, start: int, end: int) -> list[tuple[int, fitz.Rect]]:
    """Rechtecke (je Seite und Zeile vereinigt) für einen Textbereich im PDF."""
    if not doc.char_boxes:
        return []
    cache = doc.__dict__.setdefault("_rect_cache", {})
    if (start, end) not in cache:
        cache[(start, end)] = _compute_rects(doc, start, end)
    return [(page, fitz.Rect(rect)) for page, rect in cache[(start, end)]]


#: Zusätzlicher Rand (pt) um Schwärzungen in per OCR gelesenem Text – dessen Positionen sind
#: nur angenähert (Wortrahmen, gleichmäßig auf die Buchstaben verteilt).
OCR_PAD_X = 2.5
OCR_PAD_Y = 1.5


def _compute_rects(doc: LoadedDocument, start: int, end: int) -> list[tuple[int, fitz.Rect]]:
    groups: dict[tuple[int, int], fitz.Rect] = {}
    ocr_groups: set[tuple[int, int]] = set()
    for i in range(max(start, 0), min(end, len(doc.char_boxes))):
        box = doc.char_boxes[i]
        if box is None:
            continue
        r = fitz.Rect(box.rect)
        if r.is_empty:
            continue
        key = (box.page, box.line)
        if box.ocr:
            ocr_groups.add(key)
        if key in groups:
            groups[key] |= r
        else:
            groups[key] = r
    out = []
    for key, rect in groups.items():
        if key in ocr_groups:
            rect = fitz.Rect(rect.x0 - OCR_PAD_X, rect.y0 - OCR_PAD_Y, rect.x1 + OCR_PAD_X, rect.y1 + OCR_PAD_Y)
        out.append((key[0], rect))
    return out


def pdf_redaction_plan(
    doc: LoadedDocument, findings: list[Finding], mode: str, labels: bool = True
) -> list[tuple[int, fitz.Rect, str | None, list[int]]]:
    """Liste von (Seite, Rechteck, Beschriftung, Fund-IDs) – genutzt für Export UND Vorschau,
    damit beide garantiert übereinstimmen."""
    groups = active_groups(findings)
    tokens = replacement_tokens([g[0] for g in groups], mode)
    plan = []
    for g in groups:
        s, e = g[0].start, max(f.end for f in g)
        ids = [f.id for f in g]
        for i, (page, rect) in enumerate(finding_rects(doc, s, e)):
            label = None
            if labels and mode != ReplaceMode.BLACKOUT and i == 0:
                label = tokens[g[0].id]
            plan.append((page, rect, label, ids))
    for f in findings:
        if f.active and f.area is not None and 0 <= int(f.area[0]) < max(doc.page_count, 1):
            page, rect = area_rect(f)
            label = f"[{info(f.entity_type).token}]" if labels and mode != ReplaceMode.BLACKOUT else None
            plan.append((page, rect, label, [f.id]))
    return plan


def area_rect(f: Finding) -> tuple[int, fitz.Rect]:
    page, x0, y0, x1, y1 = f.area
    return int(page), fitz.Rect(x0, y0, x1, y1)


def rects_of(doc: LoadedDocument, f: Finding) -> list[tuple[int, fitz.Rect]]:
    """Rechtecke eines Fundes im PDF – Textstelle oder frei gezogener Bereich."""
    if f.area is not None:
        return [area_rect(f)]
    return finding_rects(doc, f.start, f.end)


def label_fontsize(rect: fitz.Rect, label: str) -> float:
    size = min(rect.height * 0.72, rect.width / max(len(label) * 0.56, 1))
    return size if size >= 3.5 else 0.0


def redact_pdf(doc: LoadedDocument, findings: list[Finding], mode: str, labels: bool = True) -> bytes:
    """Erzeugt ein PDF, in dem die Funde physisch entfernt (nicht nur überdeckt) sind.

    Zusätzlich werden Kommentare/Markierungen, Lesezeichen, Metadaten, Links (z. B. mailto:),
    eingebettete Dateien, JavaScript und unsichtbarer Text entfernt; Formularfelder werden zu
    festem Seiteninhalt."""
    if doc.pdf_bytes is None:
        raise ValueError("Kein PDF-Dokument")
    pdf = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
    # Beim Laden bereits geschehen – hier nur zur Sicherheit (muss VOR den Schwärzungen laufen,
    # da diese selbst Annotationen sind).
    strip_pdf_extras(pdf)
    touched: set[int] = set()
    # Seiten mit frei gezogenen Bereichen: dort auch Vektorgrafik entfernen (z. B. eine als Linien
    # gezeichnete Unterschrift). Auf anderen Seiten bleibt Grafik unangetastet.
    area_pages = {int(f.area[0]) for f in findings if f.active and f.area is not None}
    plan = pdf_redaction_plan(doc, findings, mode, labels)
    for page_no, rect, label, _ids in plan:
        page = pdf[page_no]
        kwargs = {"fill": (0, 0, 0)}
        if label:
            fs = label_fontsize(rect, label)
            if fs:
                kwargs.update(text=label, fontname="helv", fontsize=fs, text_color=(1, 1, 1), align=fitz.TEXT_ALIGN_CENTER)
        page.add_redact_annot(rect, **kwargs)
        touched.add(page_no)
    for page_no in touched:
        page = pdf[page_no]
        if page_no in area_pages:
            rects = [r for p_no, r, _l, _i in plan if p_no == page_no]
            keep = collateral_drawings(page, rects)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
            redraw_behind(page, keep)
        else:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    pdf.scrub(
        attached_files=True,
        clean_pages=True,
        embedded_files=True,
        hidden_text=True,
        javascript=True,
        metadata=True,
        redactions=True,
        remove_links=True,
        reset_fields=True,
        reset_responses=True,
        thumbnails=True,
        xml_metadata=True,
    )
    data = pdf.tobytes(garbage=4, deflate=True, clean=True)
    pdf.close()
    return data


#: Vektorgrafik, die zu weniger als diesem Anteil in Schwärzungsbereichen liegt, gilt als „nur berührt“
#: (Tabellenrahmen, Hintergrundflächen, lange Linien) und wird nach dem Schwärzen wiederhergestellt.
COLLATERAL_INSIDE = 0.5


def _inside_fraction(r: fitz.Rect, rects: list[fitz.Rect]) -> float:
    """Anteil eines Grafik-Rahmens, der in den Schwärzungsrechtecken liegt (bei Linien: Längenanteil)."""
    best = 0.0
    for a in rects:
        i = r & a
        if i.is_empty and not (r.width < 1 or r.height < 1):
            continue
        i = fitz.Rect(max(r.x0, a.x0), max(r.y0, a.y0), min(r.x1, a.x1), min(r.y1, a.y1))
        if i.x1 < i.x0 or i.y1 < i.y0:
            continue
        if r.width < 1 or r.height < 1:  # (fast) waagerechte oder senkrechte Linie
            frac = max(i.width, i.height) / max(r.width, r.height, 1e-6)
        else:
            frac = abs(i) / abs(r)
        best = max(best, frac)
    return best


def collateral_drawings(page: fitz.Page, rects: list[fitz.Rect]) -> list[dict]:
    """Grafik, die die Schwärzungen nur am Rand berührt und nach dem Entfernen wieder gezeichnet wird.

    Hintergrund: Beim Schwärzen freier Bereiche entfernt MuPDF jede Vektorgrafik, die einen Bereich
    berührt – nur so verschwinden gezeichnete Unterschriften sicher. Dabei würden aber auch
    Tabellenrahmen oder Hintergrundflächen verschwinden, die nur kurz hineinragen."""
    keep = []
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"])
        pad = (d.get("width") or 0) / 2 + 0.5
        grown = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
        if not any(grown.intersects(a) for a in rects):
            continue  # wird ohnehin nicht berührt
        if _inside_fraction(r, rects) < COLLATERAL_INSIDE:
            keep.append(d)
    return keep


def redraw_behind(page: fitz.Page, drawings: list[dict]) -> None:
    """Grafik (aus ``get_drawings``) hinter dem übrigen Seiteninhalt neu zeichnen."""
    if not drawings:
        return
    shape = page.new_shape()
    for d in drawings:
        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                shape.draw_line(item[1], item[2])
            elif kind == "re":
                shape.draw_rect(item[1])
            elif kind == "qu":
                shape.draw_quad(item[1])
            elif kind == "c":
                shape.draw_bezier(item[1], item[2], item[3], item[4])
        caps = d.get("lineCap") or (0,)
        shape.finish(
            fill=d.get("fill"), color=d.get("color"), dashes=d.get("dashes"),
            even_odd=bool(d.get("even_odd", True)), closePath=bool(d.get("closePath", False)),
            lineJoin=int(d.get("lineJoin") or 0), lineCap=int(max(caps)),
            width=d.get("width") or 1,
            stroke_opacity=d.get("stroke_opacity", 1) if d.get("stroke_opacity") is not None else 1,
            fill_opacity=d.get("fill_opacity", 1) if d.get("fill_opacity") is not None else 1,
        )
    shape.commit(overlay=False)


def verify_pdf(pdf_bytes: bytes, findings: list[Finding], ocr_pages: list[int] | None = None) -> list[str]:
    """Kontrolle des fertigen PDFs.

    Prüft, ob Texte aktiver Funde noch extrahierbar sind – im Seitentext, in Kommentaren,
    Formularfeldern, Lesezeichen und Metadaten – und ob noch Kommentare, Formularfelder,
    Lesezeichen, Links, Anhänge oder Metadaten vorhanden sind.

    Seiten aus ``ocr_pages`` (per Texterkennung gelesen, ohne Textebene) werden dazu erneut per
    OCR gelesen.

    Liefert die Liste der gefundenen Reste (leer = alles entfernt). Texte, die an anderer Stelle
    bewusst NICHT geschwärzt wurden, werden ignoriert."""
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = [page.get_text() for page in pdf]
    if ocr_pages:
        from . import ocr as ocr_mod

        key = ocr_mod.doc_key(pdf_bytes)
        for pno in ocr_pages:
            if 0 <= pno < pdf.page_count:
                parts.append(ocr_mod.ocr_page_text(pdf[pno], key))
    n_annots = n_widgets = n_links = 0
    for page in pdf:
        for a in page.annots():
            n_annots += 1
            parts += [a.info.get("content", ""), a.info.get("title", ""), a.info.get("subject", "")]
        for w in page.widgets():
            n_widgets += 1
            parts.append(str(w.field_value or ""))
        n_links += len(page.get_links())
    toc = pdf.get_toc(simple=True)
    parts += [str(t[1]) for t in toc]
    meta = {k: v for k, v in (pdf.metadata or {}).items() if v and k not in ("format", "encryption")}
    parts += [str(v) for v in meta.values()]
    xml = pdf.get_xml_metadata() or ""
    parts.append(xml)
    n_files = pdf.embfile_count()
    pdf.close()

    problems = []
    for n, what in ((n_annots, "Kommentare/Markierungen"), (n_widgets, "Formularfelder"), (len(toc), "Lesezeichen"),
                    (n_links, "Links"), (n_files, "Dateianhänge")):
        if n:
            problems.append(f"{what} noch vorhanden: {n}")
    if meta or xml.strip():
        problems.append("Metadaten noch vorhanden: " + ", ".join(list(meta) + (["XMP"] if xml.strip() else [])))

    content = re.sub(r"\s+", "", "".join(parts)).casefold()
    kept = {re.sub(r"\s+", "", f.text).casefold() for f in findings if not f.active}
    leftovers = []
    for f in findings:
        if not f.active:
            continue
        needle = re.sub(r"\s+", "", f.text).casefold()
        if len(needle) < 3 or needle in kept:
            continue
        if needle in content and f.text not in leftovers:
            leftovers.append(f.text)
    return problems + leftovers


def save_redacted_pdf(doc: LoadedDocument, findings: list[Finding], settings: Settings, target: Path) -> list[str]:
    """PDF schwärzen, kontrollieren und speichern.

    Geschrieben wird erst in ``<Ziel>.tmp``, das danach umbenannt wird – bricht etwas ab, bleibt keine
    halbe Datei unter dem Zielnamen liegen. Rückgabe: im PDF noch lesbare Reste (leer = sauber)."""
    data = redact_pdf(doc, findings, settings.replace_mode, settings.pdf_labels)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        leftovers = verify_pdf(data, findings, doc.ocr_pages)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return leftovers
