"""Nicht angezeigten PDF-Inhalt entfernen.

Ein PDF kann Inhalt tragen, den kein PDF-Programm anzeigt, der aber in der Datei steckt und sich wieder
hervorholen lässt:

* Inhalt **außerhalb des sichtbaren Seitenbereichs** – der abgeschnittene Teil einer zugeschnittenen Seite
  (CropBox) und alles, was neben dem Blatt liegt (außerhalb der MediaBox),
* Inhalt auf **ausgeblendeten Ebenen** (optionale Inhalte, OCG).

Die Textextraktion liest beides nicht – es bliebe ungeprüft im Ergebnis. Deshalb wird es beim Laden entfernt,
wie Kommentare und Lesezeichen: Das Ergebnis enthält, was die Ansicht zeigt, und sonst nichts.

Jeder Schritt wird nachgeprüft. Gelingt das Entfernen nicht nachweislich, wird der Inhalt stattdessen
**eingeblendet** (Zuschnitt aufgehoben bzw. alle Ebenen sichtbar) und damit geprüft wie alles andere – dann mit
kritischem Hinweis. Ungeprüft bleibt nur, was sich weder entfernen noch einblenden lässt; auch das wird gemeldet.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

import pymupdf as fitz

log = logging.getLogger(__name__)

#: Überstand (pt, gut 1 mm), der bei Linien, Flächen und Bildern noch nicht als „außerhalb“ zählt – Rahmenlinien
#: auf der Blattkante, Rundung. Für Text gilt er nicht (dort zählt die Mitte jedes Zeichens).
EDGE_TOLERANCE = 3.0
#: Reichweite der Rechtecke, mit denen alles außerhalb des sichtbaren Bereichs entfernt wird.
FAR = 30000.0


@dataclass
class HiddenReport:
    """Was beim Laden an nicht angezeigtem Inhalt gefunden wurde (Seiten 0-basiert)."""
    outside_removed: list[int] = field(default_factory=list)   # Inhalt außerhalb des Seitenbereichs entfernt
    layers_removed: list[int] = field(default_factory=list)    # Inhalt ausgeblendeter Ebenen entfernt
    uncropped: list[int] = field(default_factory=list)         # Entfernen misslang → Zuschnitt aufgehoben
    layers_shown: list[int] = field(default_factory=list)      # Entfernen misslang → Ebenen eingeblendet
    outside_left: list[int] = field(default_factory=list)      # weder entfernt noch einblendbar

    @property
    def changed(self) -> bool:
        return bool(self.outside_removed or self.layers_removed or self.uncropped or self.layers_shown)


def remove_hidden_content(pdf: fitz.Document) -> tuple[fitz.Document, HiddenReport, bool]:
    """Nicht angezeigten Inhalt entfernen. Rückgabe: Dokument (ggf. neu geöffnet – das übergebene ist dann
    geschlossen), Bericht und ob das Dokument verändert wurde (auch ohne Fund, z. B. Ebenen aufgelöst)."""
    report = HiddenReport()
    pdf, layers_changed = _resolve_layers(pdf, report)
    outside_changed = _remove_outside(pdf, report)
    if report.uncropped:
        pdf = _reopen(pdf)   # der aufgehobene Zuschnitt wirkt erst nach erneutem Öffnen
        report.outside_left = [p.number for p in pdf if has_outside_content(p, leftover=True)]
    return pdf, report, layers_changed or outside_changed


def _reopen(pdf: fitz.Document) -> fitz.Document:
    data = pdf.tobytes()
    pdf.close()
    return fitz.open(stream=data, filetype="pdf")


# ====================================================================== außerhalb des Seitenbereichs
def is_cropped(page: fitz.Page) -> bool:
    """Zeigt die Seite nur einen Ausschnitt (CropBox kleiner als MediaBox)?"""
    crop, media = page.cropbox, page.mediabox
    return abs(crop.width - media.width) > 0.01 or abs(crop.height - media.height) > 0.01


def has_outside_content(page: fitz.Page, leftover: bool = False) -> bool:
    """Liegt Inhalt außerhalb des sichtbaren Seitenbereichs?

    Text zählt, sobald die Mitte eines Zeichens außerhalb liegt; Linien und Flächen nur, wenn sie ganz
    außerhalb liegen (Hintergründe und Rahmen ragen oft über den Rand). Bilder zählen schon, wenn sie
    hinausragen – der überstehende Teil kann Inhalt tragen (zugeschnittener Scan). Mit ``leftover`` (Kontrolle
    nach dem Entfernen) zählen Bilder nur noch, wenn sie ganz außerhalb liegen: Bei hinausragenden Bildern
    werden die Bildpunkte außerhalb geleert, das Bild selbst behält seine Größe."""
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)   # Positionen und sichtbarer Bereich im selben (ungedrehten) Koordinatensystem
    try:
        visible = page.rect
        outside, check_text = _drawing_outside(page, visible, leftover)
        return outside or (check_text and _text_outside(page, visible))
    finally:
        if rotation:
            page.set_rotation(rotation)


def _drawing_outside(page: fitz.Page, visible: fitz.Rect, leftover: bool) -> tuple[bool, bool]:
    """Zeichenschritte der Seite durchgehen. Rückgabe: Liegt eine Linie, Fläche oder ein Bild außerhalb? Und:
    Ragt ein Textstück über den sichtbaren Bereich (dann entscheidet die Mitte der einzelnen Zeichen)?"""
    grown = fitz.Rect(visible.x0 - EDGE_TOLERANCE, visible.y0 - EDGE_TOLERANCE,
                      visible.x1 + EDGE_TOLERANCE, visible.y1 + EDGE_TOLERANCE)
    check_text = False
    for kind, bbox, *_ in page.get_bboxlog():
        r = fitz.Rect(bbox)
        if kind.startswith("clip") and "text" not in kind:
            continue
        if r.is_infinite or r.is_empty or not r.is_valid:
            continue   # zeichnet nichts (leerer Pfad) oder ohne Begrenzung
        if "text" in kind:
            check_text = check_text or not visible.contains(r)
        elif grown.contains(r):
            continue
        elif (not r.intersects(visible)
              or ("image" in kind or "imgmask" in kind or "shade" in kind) and not leftover):
            return True, check_text
    return False, check_text


def _text_outside(page: fitz.Page, visible: fitz.Rect) -> bool:
    """Liegt die Mitte eines Zeichens außerhalb des sichtbaren Bereichs?"""
    for span in page.get_texttrace():
        for char in span["chars"]:
            x0, y0, x1, y1 = char[3]
            if not visible.contains(fitz.Point((x0 + x1) / 2, (y0 + y1) / 2)):
                return True
    return False


def _remove_outside(pdf: fitz.Document, report: HiddenReport) -> bool:
    """Auf allen Seiten entfernen, was außerhalb des sichtbaren Bereichs liegt. Der Zuschnitt bleibt – die
    Seite sieht aus wie vorher. Wo danach noch etwas außerhalb liegt, wird ein Zuschnitt aufgehoben."""
    changed = False
    for page in pdf:
        try:
            if not has_outside_content(page):
                continue
            _redact_outside(page)
            changed = True
            failed = has_outside_content(page, leftover=True)
        except Exception as exc:  # noqa: BLE001 – defekte Seite: wie „nicht gelungen“ behandeln
            log.warning("Inhalt außerhalb des Seitenbereichs nicht entfernbar (Seite %d): %s", page.number + 1, exc)
            failed = True
        if not failed:
            report.outside_removed.append(page.number)
        elif is_cropped(page):
            # CropBox direkt an der Seite gleich der MediaBox setzen (set_cropbox scheitert bei Seiten mit
            # verschobenem Nullpunkt; ein Eintrag an der Seite hat Vorrang vor einem geerbten)
            pdf.xref_set_key(page.xref, "CropBox", "[{:g} {:g} {:g} {:g}]".format(*page.mediabox))
            report.uncropped.append(page.number)
            changed = True
        else:
            report.outside_left.append(page.number)
    return changed


def _redact_outside(page: fitz.Page) -> None:
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        v = page.rect
        for strip in (fitz.Rect(-FAR, -FAR, FAR, v.y0), fitz.Rect(-FAR, v.y1, FAR, FAR),
                      fitz.Rect(-FAR, v.y0, v.x0, v.y1), fitz.Rect(v.x1, v.y0, FAR, v.y1)):
            page.add_redact_annot(strip, fill=False)
        # Linien/Flächen nur, wenn ganz außerhalb – was über den Rand ragt, bleibt unversehrt
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
    finally:
        if rotation:
            page.set_rotation(rotation)


# ====================================================================== Ebenen
def has_layers(pdf: fitz.Document) -> bool:
    """Enthält das PDF Ebenen (optionale Inhalte, die ein- und ausgeblendet werden können)?"""
    return pdf.xref_get_key(pdf.pdf_catalog(), "OCProperties")[0] not in ("null", "")


def _snapshot(pdf: fitz.Document) -> list[tuple]:
    """Was je Seite gezeichnet wird: Art und Lage aller Zeichenschritte, dazu das Seitenbild (grob)."""
    out = []
    for page in pdf:
        steps = tuple((kind, tuple(round(v, 1) for v in bbox)) for kind, bbox, *_ in page.get_bboxlog())
        image = hashlib.sha1(page.get_pixmap(dpi=36, annots=False).samples).hexdigest()
        out.append((steps, image))
    return out


def _resolve_layers(pdf: fitz.Document, report: HiddenReport) -> tuple[fitz.Document, bool]:
    """Ebenen auflösen. Ausgeblendeter Inhalt wird aus dem Seiteninhalt entfernt; danach muss jede Seite
    genau das zeichnen, was sie vorher (mit ausgeblendeten Ebenen) gezeichnet hat. Stimmt das nicht, werden
    stattdessen alle Ebenen eingeblendet."""
    if not has_layers(pdf):
        return pdf, False
    original = pdf.tobytes()
    before = _snapshot(pdf)
    pdf.close()

    shown = fitz.open(stream=original, filetype="pdf")
    shown.xref_set_key(shown.pdf_catalog(), "OCProperties", "null")   # ohne Ebenenverzeichnis ist alles sichtbar
    shown = _reopen(shown)
    after = _snapshot(shown)
    affected = [i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b]
    if not affected:
        return shown, True   # nichts war ausgeblendet

    try:
        edited = fitz.open(stream=original, filetype="pdf")
        _strip_hidden_layers(edited)
        edited.xref_set_key(edited.pdf_catalog(), "OCProperties", "null")
        edited = _reopen(edited)
        if _snapshot(edited) != before:
            edited.close()
            raise _Unsupported("Seiten sehen nach dem Entfernen anders aus")
    except Exception as exc:  # noqa: BLE001 – dann einblenden statt entfernen
        log.warning("Inhalt ausgeblendeter Ebenen nicht entfernbar, wird eingeblendet: %s", exc)
        report.layers_shown = affected
        return shown, True
    shown.close()
    report.layers_removed = affected
    return edited, True


class _Unsupported(Exception):
    """Aufbau, den das Entfernen nicht sicher beherrscht → Ebenen einblenden."""


_NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)\Z")
_WS = b"\x00\t\n\x0c\r "
_DELIMS = b"()<>[]{}/%"
_INLINE_IMAGE_END = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ]|\Z)")
_PATH_BUILD = {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}
_PATH_PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"n"}


class _LayerStripper:
    """Entfernt aus den Inhaltsströmen, was auf ausgeblendeten Ebenen liegt.

    Ebenen-Inhalt steht zwischen ``/OC /Name BDC`` und ``EMC`` oder ist ein Bild/Formular (XObject) mit
    eigenem ``/OC``-Eintrag. Entfernt werden dort nur die zeichnenden Anweisungen (Text, Pfade, Bilder,
    Formulare, Verläufe); Zustandsänderungen (Farbe, Matrix, Schrift, Beschneidung) bleiben stehen, damit der
    folgende, sichtbare Inhalt unverändert bleibt."""

    def __init__(self, pdf: fitz.Document):
        self.pdf = pdf
        self.on = {xref: bool(o.get("on", True)) for xref, o in pdf.get_ocgs().items()}
        self.done: set[int] = set()

    # ------------------------------------------------------------------ Sichtbarkeit
    def hidden(self, value: tuple[str, str]) -> bool:
        """Ist der Inhalt mit diesem ``/OC``-Eintrag ausgeblendet?"""
        kind, text = value
        if kind == "null":
            return False
        if kind != "xref":
            raise _Unsupported("Ebenen-Eintrag ist kein Verweis")
        xref = int(text.split()[0])
        typ = self.pdf.xref_get_key(xref, "Type")[1]
        if typ != "/OCG":
            # Zusammengesetzte Sichtbarkeit (OCMD: „sichtbar, wenn alle/irgendeine Ebene an/aus“) werten die
            # PDF-Programme nicht einheitlich aus – nicht entfernen, sondern einblenden und prüfen lassen.
            raise _Unsupported(f"Ebenen-Eintrag vom Typ {typ}")
        return not self._is_on(xref)

    def _is_on(self, xref: int) -> bool:
        if xref not in self.on:
            raise _Unsupported("Ebene nicht im Verzeichnis")
        return self.on[xref]

    # ------------------------------------------------------------------ Ressourcen
    def _resource_holder(self, xref: int) -> int | None:
        """Objekt, dessen ``/Resources`` gelten (bei Seiten ggf. geerbt vom übergeordneten Knoten)."""
        for _ in range(50):
            if self.pdf.xref_get_key(xref, "Resources")[0] != "null":
                return xref
            parent = self.pdf.xref_get_key(xref, "Parent")
            if parent[0] != "xref":
                return None
            xref = int(parent[1].split()[0])
        return None

    def _resource(self, holder: int | None, category: str, name: bytes) -> tuple[str, str]:
        if holder is None:
            return "null", "null"
        return self.pdf.xref_get_key(holder, f"Resources/{category}/{_name(name)}")

    # ------------------------------------------------------------------ Ströme
    def run(self) -> None:
        used: dict[int, int] = {}
        for page in self.pdf:
            for xref in page.get_contents():
                used[xref] = used.get(xref, 0) + 1
        if any(n > 1 for n in used.values()):
            raise _Unsupported("Inhaltsstrom von mehreren Seiten genutzt")
        for page in self.pdf:
            xrefs = page.get_contents()
            if not xrefs:
                continue
            data = b"\n".join(self.pdf.xref_stream(x) or b"" for x in xrefs)
            new = self._strip(data, self._resource_holder(page.xref))
            if new != data:
                self.pdf.update_stream(xrefs[0], new)
                for x in xrefs[1:]:
                    self.pdf.update_stream(x, b"\n")

    def _form(self, xref: int, fallback_holder: int | None) -> None:
        """Formular-XObject (eigener Inhaltsstrom) einmal bearbeiten."""
        if xref in self.done:
            return
        self.done.add(xref)
        data = self.pdf.xref_stream(xref) or b""
        holder = xref if self.pdf.xref_get_key(xref, "Resources")[0] != "null" else fallback_holder
        new = self._strip(data, holder)
        if new != data:
            self.pdf.update_stream(xref, new)

    def _strip(self, data: bytes, holder: int | None) -> bytes:
        """Inhaltsstrom ohne die zeichnenden Anweisungen ausgeblendeter Abschnitte."""
        parts: list[bytes | None] = []
        stack: list[bool] = []          # je offenem markiertem Abschnitt: ausgeblendet?
        path: list[int] = []            # Positionen der Pfad-Anweisungen seit dem letzten Zeichnen
        clip = False
        for operands, op in _instructions(data):
            hidden = any(stack)
            raw = operands[0] if op == b"BI" else b" ".join(operands + ([op] if op else []))
            if op in (b"BDC", b"BMC", b"EMC"):
                self._marked_content(stack, op, operands, holder)
            elif op in _PATH_BUILD:
                path.append(len(parts))
            elif op in (b"W", b"W*"):
                clip = True
            elif op in _PATH_PAINT:
                if hidden and op != b"n":
                    raw = _drop_path(parts, path, clip)
                path, clip = [], False
            elif op == b"Do" and operands:
                raw = self._xobject(operands[-1], holder, hidden, raw)
            elif hidden:
                raw = _without_text(op, operands, raw)
            parts.append(raw)
        return b"\n".join(p for p in parts if p)

    def _marked_content(self, stack: list[bool], op: bytes, operands: list[bytes], holder: int | None) -> None:
        """Beginn und Ende markierter Abschnitte mitführen; ``/OC … BDC`` entscheidet über „ausgeblendet“."""
        if op == b"BDC":
            is_hidden = False
            if len(operands) == 2 and operands[0] == b"/OC" and operands[1].startswith(b"/"):
                is_hidden = self.hidden(self._resource(holder, "Properties", operands[1]))
            stack.append(is_hidden)
        elif op == b"BMC":
            stack.append(False)
        elif stack:                     # EMC
            stack.pop()

    def _xobject(self, name: bytes, holder: int | None, hidden: bool, raw: bytes) -> bytes:
        """``Do``: Bild oder Formular zeichnen – entfällt in ausgeblendeten Abschnitten und wenn das Objekt
        selbst einer ausgeblendeten Ebene angehört; sichtbare Formulare werden ihrerseits bereinigt."""
        target = self._resource(holder, "XObject", name)
        if hidden:
            return b""
        if target[0] == "xref":
            xref = int(target[1].split()[0])
            if self.hidden(self.pdf.xref_get_key(xref, "OC")):
                return b""
            if self.pdf.xref_get_key(xref, "Subtype")[1] == "/Form":
                self._form(xref, holder)
        return raw


def _drop_path(parts: list[bytes | None], path: list[int], clip: bool) -> bytes:
    """Zeichnen eines Pfades in einem ausgeblendeten Abschnitt: Dient der Pfad zugleich als Beschneidung, bleibt
    er stehen und wird nur nicht gezeichnet (``n``); sonst entfallen auch seine Pfad-Anweisungen."""
    if clip:
        return b"n"
    for i in path:
        parts[i] = None
    return b""


def _without_text(op: bytes, operands: list[bytes], raw: bytes) -> bytes:
    """Text-, Verlaufs- und eingebettete Bild-Anweisungen eines ausgeblendeten Abschnitts: nichts zeichnen, aber
    die Wirkung auf die Textposition erhalten (Zeilenwechsel, Abstände)."""
    if op in (b"Tj", b"TJ", b"sh", b"BI"):
        return b""
    if op == b"'":
        return b"T*"
    if op == b'"' and len(operands) == 3:
        return operands[0] + b" Tw " + operands[1] + b" Tc T*"
    return raw


def _strip_hidden_layers(pdf: fitz.Document) -> None:
    _LayerStripper(pdf).run()


def _name(token: bytes) -> str:
    """``/Im#20A`` → ``Im A``"""
    raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), token[1:])
    return raw.decode("latin-1")


def _instructions(data: bytes):
    """Inhaltsstrom in Anweisungen zerlegen: (Operanden, Operator). Zusammengesetzte Operanden (Zeichenkette,
    Feld, Wörterbuch) bleiben je ein Stück; ein eingebettetes Bild (``BI … EI``) ist eine Anweisung ``BI``."""
    operands: list[bytes] = []
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
        elif c == 0x25:                                   # % Kommentar
            while i < n and data[i] not in b"\r\n":
                i += 1
        elif c == 0x28:                                   # ( Zeichenkette )
            j, depth = i + 1, 1
            while j < n and depth:
                if data[j] == 0x5C:
                    j += 1
                elif data[j] == 0x28:
                    depth += 1
                elif data[j] == 0x29:
                    depth -= 1
                j += 1
            operands.append(data[i:j])
            i = j
        elif c in b"[<" :                                 # Feld, Wörterbuch oder Hex-Zeichenkette
            j = _compound_end(data, i)
            operands.append(data[i:j])
            i = j
        elif c == 0x2F:                                   # /Name
            j = i + 1
            while j < n and data[j] not in _WS and data[j] not in _DELIMS:
                j += 1
            operands.append(data[i:j])
            i = j
        elif c in _DELIMS:
            raise _Unsupported("Inhaltsstrom nicht lesbar")
        else:
            j = i
            while j < n and data[j] not in _WS and data[j] not in _DELIMS:
                j += 1
            word = data[i:j]
            i = j
            if _NUMBER.match(word) or word in (b"true", b"false", b"null"):
                operands.append(word)
            elif word == b"BI":
                end = _INLINE_IMAGE_END.search(data, i)
                if end is None:
                    raise _Unsupported("eingebettetes Bild ohne Ende")
                yield [data[i - 2:end.end()]], b"BI"      # ganzes Bild als ein Operand
                i = end.end()
                operands = []
            else:
                yield operands, word
                operands = []
    if operands:
        yield operands, b""


def _compound_end(data: bytes, i: int) -> int:
    """Ende eines Felds ``[…]``, Wörterbuchs ``<<…>>`` oder einer Hex-Zeichenkette ``<…>`` ab Position ``i``."""
    n = len(data)
    depth = 0
    while i < n:
        c = data[i]
        if c == 0x28:                                     # Zeichenkette im Feld
            depth_s, i = 1, i + 1
            while i < n and depth_s:
                if data[i] == 0x5C:
                    i += 1
                elif data[i] == 0x28:
                    depth_s += 1
                elif data[i] == 0x29:
                    depth_s -= 1
                i += 1
            continue
        if c == 0x5B or data[i:i + 2] == b"<<":
            depth += 1
            i += 2 if c == 0x3C else 1
        elif c == 0x5D or data[i:i + 2] == b">>":
            depth -= 1
            i += 2 if c == 0x3E else 1
        elif c == 0x3C:                                   # <Hex>
            end = data.find(b">", i)
            if end < 0:
                raise _Unsupported("Hex-Zeichenkette ohne Ende")
            i = end + 1
            if depth == 0:
                return i
            continue
        else:
            i += 1
        if depth == 0:
            return i
    raise _Unsupported("Feld oder Wörterbuch ohne Ende")
