"""Anwenden der Funde: Ersetzung im Text und echte Schwärzung im PDF."""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from .analyzer import word_pattern
from .entities import info
from .hyphenation import join_hyphenated
from .loaders import LoadedDocument, strip_pdf_extras
from .models import Finding, ReplaceMode, Settings
from .pdf_hidden import has_layers, has_outside_content

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
    return re.sub(r"\s+", " ", join_hyphenated(value)).strip().casefold()


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
    return [(page, fitz.Rect(rect)) for page, rect, _core in cache[(start, end)]]


def text_removal_rects(doc: LoadedDocument, start: int, end: int) -> list[tuple[int, fitz.Rect]]:
    """Wie :func:`finding_rects`, aber in der Höhe auf die Zeilenmitte verkleinert – zum Entfernen des Textes.

    MuPDF entfernt jedes Zeichen, dessen Rahmen (ohne etwa ein Zehntel oben und unten, gemessen mit
    PyMuPDF 1.28) das Schwärzungsrechteck überlappt. Die Zeichenrahmen reichen über Ober- und Unterlänge der
    Schrift; bei engem Zeilenabstand ragen sie in die Nachbarzeilen, und ein Rechteck in voller Höhe nähme dort
    Text mit. Ein schmales Band durch die Mitte der Zeichen trifft nur die eigene Zeile."""
    finding_rects(doc, start, end)  # füllt den Zwischenspeicher
    return [(page, fitz.Rect(core)) for page, _rect, cores in doc.__dict__["_rect_cache"].get((start, end), [])
            for core in cores]


#: Zusätzlicher Rand (pt) um Schwärzungen in per OCR gelesenem Text – dessen Positionen sind
#: nur angenähert (Wortrahmen, gleichmäßig auf die Buchstaben verteilt).
OCR_PAD_X = 2.5
OCR_PAD_Y = 1.5
#: Anteil der Zeichenhöhe, um den das Rechteck zum Entfernen des Textes oben und unten verkleinert wird.
TEXT_INSET = 0.35
#: Haben die Zeichen einer Zeile kein gemeinsames Band von mindestens dieser Höhe (pt) – gemischte
#: Schriftgrößen, hochgestellte Zeichen, senkrechter Text –, bekommt jedes Zeichen sein eigenes.
MIN_CORE_HEIGHT = 1.0


def _core(r: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(r.x0, r.y0 + TEXT_INSET * r.height, r.x1, r.y1 - TEXT_INSET * r.height)


def _compute_rects(doc: LoadedDocument, start: int, end: int) -> list[tuple[int, fitz.Rect, list[fitz.Rect]]]:
    """(Seite, Rechteck, Kern-Rechtecke) je Zeile. Der Kern ist das Band, das alle Zeichen der Zeile in ihrer
    Mitte schneidet (siehe :func:`text_removal_rects`)."""
    groups: dict[tuple[int, int], fitz.Rect] = {}
    chars: dict[tuple[int, int], list[fitz.Rect]] = {}
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
        chars.setdefault(key, []).append(_core(r))
        if key in groups:
            groups[key] |= r
        else:
            groups[key] = r
    out = []
    for key, rect in groups.items():
        top, bottom = max(c.y0 for c in chars[key]), min(c.y1 for c in chars[key])
        if key in ocr_groups:
            # Positionen aus der Texterkennung sind nur angenähert – großzügig und in voller Höhe
            rect = fitz.Rect(rect.x0 - OCR_PAD_X, rect.y0 - OCR_PAD_Y, rect.x1 + OCR_PAD_X, rect.y1 + OCR_PAD_Y)
            cores = [fitz.Rect(rect)]
        elif bottom - top >= MIN_CORE_HEIGHT or len(chars[key]) == 1:
            cores = [fitz.Rect(rect.x0, top, rect.x1, bottom)]
        else:
            cores = chars[key]
        out.append((key[0], rect, cores))
    return out


def pdf_redaction_plan(
    doc: LoadedDocument, findings: list[Finding], mode: str, labels: bool = True
) -> list[tuple[int, fitz.Rect, str | None, list[int]]]:
    """Liste von (Seite, Rechteck, Beschriftung, Fund-IDs) – genutzt für Export UND Vorschau,
    damit beide garantiert übereinstimmen."""
    return _text_plan(doc, findings, mode, labels) + _area_plan(doc, findings, mode, labels)


def _text_plan(doc: LoadedDocument, findings: list[Finding], mode: str, labels: bool) -> list:
    """Textstellen: (Seite, Rechteck, Beschriftung, Fund-IDs)."""
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
    return plan


def _area_plan(doc: LoadedDocument, findings: list[Finding], mode: str, labels: bool) -> list:
    """Frei gezogene Bereiche: (Seite, Rechteck, Beschriftung, Fund-IDs)."""
    plan = []
    for f in findings:
        if f.active and f.area is not None and 0 <= int(f.area[0]) < max(doc.page_count, 1):
            page, rect = area_rect(f)
            label = f"[{info(f.entity_type).token}]" if labels and mode != ReplaceMode.BLACKOUT else None
            plan.append((page, rect, label, [f.id]))
    return plan


def _add_bars(pdf: fitz.Document, plan: list) -> dict[int, list[fitz.Rect]]:
    """Schwärzungsbalken (mit Beschriftung) als Schwärzungs-Annotationen anlegen. Rückgabe: Rechtecke je Seite."""
    pages: dict[int, list[fitz.Rect]] = {}
    for page_no, rect, label, _ids in plan:
        kwargs = {"fill": (0, 0, 0)}
        if label:
            fs = label_fontsize(rect, label)
            if fs:
                kwargs.update(text=label, fontname="helv", fontsize=fs, text_color=(1, 1, 1),
                              align=fitz.TEXT_ALIGN_CENTER)
        pdf[page_no].add_redact_annot(rect, **kwargs)
        pages.setdefault(page_no, []).append(rect)
    return pages


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
    # 1. Text der Fundstellen entfernen – mit in der Höhe verkleinerten Rechtecken, damit bei engem
    #    Zeilenabstand kein Text der Nachbarzeilen verschwindet (siehe text_removal_rects). Noch ohne Balken.
    text_pages: set[int] = set()
    for g in active_groups(findings):
        for page_no, core in text_removal_rects(doc, g[0].start, max(f.end for f in g)):
            pdf[page_no].add_redact_annot(core, fill=False)
            text_pages.add(page_no)
    for page_no in text_pages:
        pdf[page_no].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
    # 2. Frei gezogene Bereiche: alles darin entfernen, auch Vektorgrafik (z. B. eine als Linien gezeichnete
    #    Unterschrift). Grafik, die nur hineinragt (Tabellenrahmen …), wird danach wieder gezeichnet.
    for page_no, rects in _add_bars(pdf, _area_plan(doc, findings, mode, labels)).items():
        page = pdf[page_no]
        keep = collateral_drawings(page, rects)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
        redraw_behind(page, keep)
    # 3. Balken der Textstellen in voller Höhe (wie in der Vorschau): Bildpunkte darunter werden entfernt
    #    (gescannte Seiten), Text und Grafik daneben bleiben unangetastet.
    for page_no in _add_bars(pdf, _text_plan(doc, findings, mode, labels)):
        pdf[page_no].apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_NONE)
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


def verify_pdf(pdf_bytes: bytes, findings: list[Finding], ocr_pages: list[int] | None = None,
               doc: LoadedDocument | None = None, mode: str = ReplaceMode.PLACEHOLDER,
               labels: bool = True) -> list[str]:
    """Kontrolle des fertigen PDFs. Liefert die Liste der Beanstandungen (leer = sauber).

    1. **Aufbau:** Sind noch Kommentare, Formularfelder, Lesezeichen, Links, Anhänge, Metadaten, Ebenen oder
       Inhalt außerhalb des sichtbaren Seitenbereichs vorhanden?
    2. **Geschwärzte Stellen** (nur mit ``doc``): Steht an einer Stelle, die geschwärzt wurde, noch Text?
       Geprüft wird jedes Schwärzungsrechteck des Plans – unabhängig davon, wie der Text lautet und ob er
       anderswo abgewählt wurde.
    3. **Andere Stellen:** Kommt der Text eines aktiven Fundes sonst noch im Ergebnis vor – im Seitentext
       (als eigenes Wort bzw. eigene Nummer, nicht als Teil eines längeren Wortes), in Kommentaren,
       Formularfeldern, Lesezeichen oder Metadaten? Vorkommen, die bewusst abgewählt wurden, zählen nicht –
       und (mit ``doc``) auch nicht die Beschriftungen der Balken: „[PERSON]“ ist kein Vorkommen von „Person“.

    Seiten aus ``ocr_pages`` (per Texterkennung gelesen, ohne Textebene) werden dazu erneut per OCR gelesen."""
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    plan = pdf_redaction_plan(doc, findings, mode, labels) if doc is not None else []
    page_texts, ocr_words = _texts_for_check(pdf, pdf_bytes, ocr_pages, plan)
    problems, other = _structure_problems(pdf)
    failed = _unredacted_spots(pdf, plan, findings, ocr_words)
    pdf.close()
    by_id = {f.id: f for f in findings}
    reported: set[str] = set()
    for fid, pages in failed.items():
        f = by_id[fid]
        label = f.text if f.text.strip() else info(f.entity_type).label
        if label not in reported:
            reported.add(label)
            problems.append(f"{label} – an der geschwärzten Stelle noch vorhanden ({_pages(pages)})")
    problems += _other_occurrences(findings, page_texts, other, reported)
    return problems


def _texts_for_check(pdf: fitz.Document, pdf_bytes: bytes, ocr_pages: list[int] | None,
                     plan: list) -> tuple[list[str], dict[int, list]]:
    """Text je Seite – ohne die Beschriftungen der Balken aus ``plan``, denn sie sind kein Text des Dokuments
    („[PERSON]“ wäre sonst ein Vorkommen des Wortes „Person“). Seiten aus ``ocr_pages`` werden zusätzlich per
    Texterkennung gelesen (Rückgabe auch der dort gelesenen Wörter je Seite)."""
    labelled: dict[int, list] = {}
    for entry in plan:
        if entry[2]:
            labelled.setdefault(entry[0], []).append(entry)
    page_texts = [_text_without_labels(page, labelled[pno]) if pno in labelled else page.get_text()
                  for pno, page in enumerate(pdf)]
    ocr_words: dict[int, list] = {}
    if ocr_pages:
        from . import ocr as ocr_mod

        key = ocr_mod.doc_key(pdf_bytes)
        for pno in ocr_pages:
            if 0 <= pno < pdf.page_count:
                lines = ocr_mod.ocr_page(pdf[pno], key)
                ocr_words[pno] = [w for line in lines for w in line]
                entries = labelled.get(pno, [])
                page_texts[pno] += "\n" + "\n".join(" ".join(_ocr_text_without_labels(w, entries) for w in line)
                                                     for line in lines)
    return page_texts, ocr_words


def _text_without_labels(page: fitz.Page, entries: list) -> str:
    """Seitentext wie ``page.get_text()``, aber mit Leerzeichen an der Stelle der Balkenbeschriftungen. Als
    Beschriftung zählt nur, was im Rechteck des eigenen Balkens vollständig als dessen Beschriftung steht
    (siehe ``_label_chars``) – gleich lautender Text an anderer Stelle bleibt stehen."""
    chars: list[tuple[str, tuple[float, float]]] = []
    line_ends: list[int] = []
    for block in page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            chars += [(c["c"], ((c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2))
                      for span in line["spans"] for c in span["chars"]]
            line_ends.append(len(chars))
    marked = _label_chars(chars, entries)
    out, start = [], 0
    for end in line_ends:
        out.append("".join(" " if i in marked else chars[i][0] for i in range(start, end)) + "\n")
        start = end
    return "".join(out)


def _ocr_text_without_labels(word, entries: list) -> str:
    """Text eines erneut gelesenen Wortes ohne die Beschriftung eines Balkens, den es berührt – die
    Texterkennung liest auch die Beschriftungen, mitunter zusammen mit dem Wort daneben („geb.[DATUM]“)."""
    text = word.text
    for _p, rect, label, _ids in entries:
        if label.casefold() in text.casefold() and fitz.Rect(word.rect).intersects(rect):
            text = re.sub(re.escape(label), " ", text, flags=re.IGNORECASE)
    return text


def _structure_problems(pdf: fitz.Document) -> tuple[list[str], list[str]]:
    """Aufbau des fertigen PDFs. Rückgabe: Beanstandungen und die Texte aus Kommentaren, Formularfeldern,
    Lesezeichen und Metadaten (dort wird anschließend nach Fundtexten gesucht)."""
    other: list[str] = []
    n_annots = n_widgets = n_links = n_outside = 0
    for page in pdf:
        n_outside += has_outside_content(page, leftover=True)
        for a in page.annots():
            n_annots += 1
            other += [a.info.get("content", ""), a.info.get("title", ""), a.info.get("subject", "")]
        for w in page.widgets():
            n_widgets += 1
            other.append(str(w.field_value or ""))
        n_links += len(page.get_links())
    toc = pdf.get_toc(simple=True)
    other += [str(t[1]) for t in toc]
    meta = {k: v for k, v in (pdf.metadata or {}).items() if v and k not in ("format", "encryption")}
    other += [str(v) for v in meta.values()]
    xml = pdf.get_xml_metadata() or ""
    other.append(xml)
    n_files = pdf.embfile_count()
    layers = has_layers(pdf)

    problems = []
    for n, what in ((n_annots, "Kommentare/Markierungen"), (n_widgets, "Formularfelder"), (len(toc), "Lesezeichen"),
                    (n_links, "Links"), (n_files, "Dateianhänge"),
                    (n_outside, "Seiten mit Inhalt außerhalb des sichtbaren Bereichs")):
        if n:
            problems.append(f"{what} noch vorhanden: {n}")
    if layers:
        problems.append("Ebenen noch vorhanden")
    if meta or xml.strip():
        problems.append("Metadaten noch vorhanden: " + ", ".join(list(meta) + (["XMP"] if xml.strip() else [])))
    return problems, other


def _pages(pages: list[int]) -> str:
    pages = sorted(set(pages))
    return ("Seite " if len(pages) == 1 else "Seiten ") + ", ".join(str(p + 1) for p in pages)


def _unredacted_spots(pdf: fitz.Document, plan: list, findings: list[Finding],
                      ocr_words: dict[int, list]) -> dict[int, list[int]]:
    """Fund-ID → Seiten, auf denen an seiner geschwärzten Stelle (Rechtecke aus ``plan``) noch Text steht.

    Im Ergebnis dürfen in einem Schwärzungsrechteck nur Balkenbeschriftungen stehen – die eigene, die eines
    Balkens, der ganz darin liegt, und Bruchstücke der Beschriftung eines Balkens, der sich mit dem Rechteck nur
    überschneidet (frei gezogener Bereich über einem Teil eines Textfundes). Maßgeblich ist die Mitte jedes
    Zeichens – Zeichen der Nachbarzeilen, deren Rahmen nur hineinragen, zählen nicht."""
    by_page: dict[int, list] = {}
    for entry in plan:
        by_page.setdefault(entry[0], []).append(entry)
    by_id = {f.id: f for f in findings}
    failed: dict[int, list[int]] = {}
    for pno, entries in by_page.items():
        if not 0 <= pno < pdf.page_count:
            continue
        # Beschriftungen der Balken dieser Seite (längste zuerst) – nur sie dürfen in einem Rechteck stehen
        page_labels = sorted({_squash(label) for _p, _r, label, _i in entries if label}, key=len, reverse=True)
        chars = [(c["c"], ((c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2))
                 for block in pdf[pno].get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                 if block.get("type") == 0 for line in block["lines"] for span in line["spans"] for c in span["chars"]]
        label_chars: set[int] | None = None
        for _p, rect, _label, ids in entries:
            within = [i for i, (_c, (x, y)) in enumerate(chars) if rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1]
            inside = _squash("".join(chars[i][0] for i in within))
            for label in page_labels:   # auch die eines Nachbar-Balkens, der ganz in diesem Rechteck liegt
                inside = inside.replace(label, "")
            bad = bool(inside)
            if bad:
                # Rest könnte das Bruchstück einer Beschriftung sein, deren Balken dieses Rechteck nur überschneidet
                if label_chars is None:
                    label_chars = _label_chars(chars, entries)
                bad = any(i not in label_chars and not chars[i][0].isspace() for i in within)
            if not bad and pno in ocr_words:
                # Texterkennung liest auch die Beschriftung des Balkens – nur melden, was dem Fund gleicht
                seen = _squash("".join(w.text for w in ocr_words[pno]
                                       if rect.x0 <= (w.rect[0] + w.rect[2]) / 2 <= rect.x1
                                       and rect.y0 <= (w.rect[1] + w.rect[3]) / 2 <= rect.y1))
                texts = [_squash(by_id[i].text) for i in ids if i in by_id]
                bad = len(seen) >= 3 and any(seen in t or t in seen for t in texts if len(t) >= 3)
            if bad:
                for i in ids:
                    failed.setdefault(i, []).append(pno)
    return failed


def _label_chars(chars: list, entries: list) -> set[int]:
    """Nummern der Zeichen einer Seite, die zu einer Balkenbeschriftung gehören. Gezählt wird nur, was im Rechteck
    des eigenen Balkens vollständig als dessen Beschriftung steht – Text, der bloß wie ein Stück einer
    Beschriftung aussieht, gehört nicht dazu."""
    marked: set[int] = set()
    for _p, rect, label, _ids in entries:
        if not label:
            continue
        want = _squash(label)
        text, index = "", []            # Zeichen im Rechteck ohne Leerraum, dazu je Zeichen seine Nummer
        for i, (c, (x, y)) in enumerate(chars):
            if rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1 and not c.isspace():
                folded = c.casefold()
                text += folded
                index += [i] * len(folded)
        pos = text.find(want)
        while want and pos >= 0:
            marked.update(index[pos:pos + len(want)])
            pos = text.find(want, pos + len(want))
    return marked


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _other_occurrences(findings: list[Finding], page_texts: list[str], other: list[str],
                       reported: set[str]) -> list[str]:
    """Texte aktiver Funde, die im Ergebnis noch vorkommen – auf den Seiten öfter, als sie bewusst abgewählt
    wurden, oder in Kommentaren, Formularfeldern, Lesezeichen und Metadaten."""
    kept: dict[str, int] = {}
    for f in findings:
        if not f.active and f.area is None:
            key = _squash(join_hyphenated(f.text))
            kept[key] = kept.get(key, 0) + 1
    pages = [join_hyphenated(t) for t in page_texts]
    rest = _squash("".join(other))
    out = []
    for f in findings:
        if not f.active or f.area is not None or f.text in reported:
            continue
        pattern = word_pattern(f.text)
        if pattern is None:
            continue
        hits = [pno for pno, text in enumerate(pages) for _ in pattern.finditer(text)]
        key = _squash(join_hyphenated(f.text))
        where = []
        if len(hits) > kept.get(key, 0):
            where.append(_pages(hits))
        if key in rest:
            where.append("Kommentare/Formularfelder/Lesezeichen/Metadaten")
        if where:
            reported.add(f.text)
            out.append(f"{f.text} – an anderer Stelle noch lesbar ({', '.join(where)})")
    return out


def save_redacted_pdf(doc: LoadedDocument, findings: list[Finding], settings: Settings, target: Path) -> list[str]:
    """PDF schwärzen, kontrollieren und speichern.

    Geschrieben wird erst in ``<Ziel>.tmp``, das danach umbenannt wird – bricht etwas ab, bleibt keine
    halbe Datei unter dem Zielnamen liegen. Rückgabe: im PDF noch lesbare Reste (leer = sauber)."""
    data = redact_pdf(doc, findings, settings.replace_mode, settings.pdf_labels)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(data)
        leftovers = verify_pdf(data, findings, doc.ocr_pages, doc, settings.replace_mode, settings.pdf_labels)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return leftovers
