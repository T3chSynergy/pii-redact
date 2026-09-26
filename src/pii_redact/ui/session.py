"""Zustand eines geöffneten Dokuments inkl. Nachbearbeitung und Rückgängig/Wiederholen.

Die Liste der Funde ist die einzige Wahrheit: Anonymisierter Text und PDF-Vorschau werden
daraus jederzeit neu berechnet. Widgets ändern Funde ausschließlich über diese Klasse.
"""

from __future__ import annotations

import copy
import re

from PySide6.QtCore import QObject, Signal

from ..core import Finding, LoadedDocument, RedactedText, Settings, redact_text
from ..core.analyzer import find_terms

UNDO_LIMIT = 100


class DocumentSession(QObject):
    changed = Signal()            # Funde geändert (alles neu zeichnen)
    manualTextChanged = Signal()  # freie Bearbeitung an/aus

    def __init__(self, doc: LoadedDocument, settings: Settings, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.settings = settings
        self.findings: list[Finding] = []
        self._by_id: dict[int, Finding] = {}
        self._undo: list[list[Finding]] = []
        self._redo: list[list[Finding]] = []
        self._redacted: RedactedText | None = None
        #: Frei bearbeiteter anonymisierter Text (None = automatisch aus Funden erzeugt)
        self.manual_text: str | None = None
        self.dirty = False

    # ------------------------------------------------------------------ Lesen
    def get(self, fid: int) -> Finding | None:
        return self._by_id.get(fid)

    def redacted(self) -> RedactedText:
        if self._redacted is None:
            self._redacted = redact_text(self.doc.text, self.findings, self.settings.replace_mode)
        return self._redacted

    def output_text(self) -> str:
        return self.manual_text if self.manual_text is not None else self.redacted().text

    def findings_at(self, offset: int) -> list[Finding]:
        return [f for f in self.findings if f.start <= offset < f.end]

    def same_text_ids(self, fid: int) -> list[int]:
        f = self.get(fid)
        if not f:
            return []
        if f.is_area:
            return [fid]
        key = _norm(f.text)
        return [g.id for g in self.findings if not g.is_area and _norm(g.text) == key]

    def counts(self) -> tuple[int, int]:
        return len(self.findings), sum(1 for f in self.findings if f.active)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # ------------------------------------------------------------------ Schreiben
    def set_findings(self, findings: list[Finding], record_undo: bool = False) -> None:
        if record_undo:
            self._snapshot()
        self.findings = sorted(findings, key=lambda f: (f.start, f.end))
        self._reindex()

    def apply_reanalysis(self, new_auto: list[Finding]) -> None:
        """Neue Analyse übernehmen, ohne Nachbearbeitung zu verlieren: manuelle Funde bleiben,
        deaktivierte oder umtypisierte Stellen behalten ihren Zustand."""
        previous = {f.key: f for f in self.findings if f.source == "auto"}
        manual = [f for f in self.findings if f.source == "manuell"]
        for f in new_auto:
            old = previous.get(f.key)
            if old is not None:
                f.active = old.active
                f.entity_type = old.entity_type
        self.set_findings(new_auto + manual, record_undo=bool(self.findings))

    def set_active(self, ids: list[int], active: bool) -> None:
        targets = [self._by_id[i] for i in ids if i in self._by_id and self._by_id[i].active != active]
        if not targets:
            return
        self._snapshot()
        for f in targets:
            f.active = active
        self._reindex()

    def toggle(self, fid: int) -> None:
        f = self.get(fid)
        if f:
            self.set_active([fid], not f.active)

    def set_type(self, ids: list[int], entity_type: str) -> None:
        targets = [self._by_id[i] for i in ids if i in self._by_id]
        if not targets:
            return
        self._snapshot()
        for f in targets:
            if f.source == "auto" and not f.original_type and f.entity_type != entity_type:
                f.original_type = f.entity_type
            f.entity_type = entity_type
        self._reindex()

    def remove(self, ids: list[int]) -> None:
        ids_set = set(ids)
        if not ids_set & self._by_id.keys():
            return
        self._snapshot()
        self.findings = [f for f in self.findings if f.id not in ids_set]
        self._reindex()

    def add_manual(self, ranges: list[tuple[int, int]], entity_type: str) -> list[Finding]:
        text = self.doc.text
        new = []
        for s, e in ranges:
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if e <= s:
                continue
            # identische Stelle existiert schon → nur aktivieren/umtypisieren
            existing = next((f for f in self.findings if f.start == s and f.end == e), None)
            if existing:
                new.append(existing)
                continue
            new.append(Finding(s, e, entity_type, text[s:e], 1.0, source="manuell", recognizer="manuell"))
        if not new:
            return []
        self._snapshot()
        for f in new:
            if f in self.findings:
                f.active = True
                f.entity_type = entity_type
            else:
                self.findings.append(f)
        self.findings.sort(key=lambda f: (f.start, f.end))
        self._reindex()
        return new

    def add_area(self, page: int, rect: tuple[float, float, float, float], entity_type: str,
                 ranges: list[tuple[int, int]] | None = None) -> list[Finding]:
        """Frei gezogenen Bereich schwärzen (Unterschrift, Foto …). ``ranges``: Text im Bereich, der
        zusätzlich als Textstelle geschwärzt wird (damit er auch im Text-Export verschwindet).
        Ein Rückgängig-Schritt für alles."""
        x0, y0, x1, y1 = (float(v) for v in rect)
        anchor = self.doc.page_offsets[page] if 0 <= page < len(self.doc.page_offsets) else 0
        area = Finding(anchor, anchor, entity_type, "", 1.0, source="manuell", recognizer="Bereich",
                       area=(int(page), min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        text_new: list[Finding] = []
        text = self.doc.text
        for s, e in ranges or []:
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            if e > s and not any(f.start == s and f.end == e and not f.is_area for f in self.findings):
                text_new.append(Finding(s, e, entity_type, text[s:e], 1.0, source="manuell", recognizer="Bereich"))
        self._snapshot()
        self.findings.extend([area] + text_new)
        for f in self.findings:  # vorhandene Textfunde im Bereich aktivieren
            if not f.is_area and any(f.start == s and f.end == e for s, e in ranges or []):
                f.active = True
        self.findings.sort(key=lambda f: (f.start, f.end))
        self._reindex()
        return [area] + text_new

    def add_all_occurrences(self, value: str, entity_type: str) -> list[Finding]:
        ranges = [(f.start, f.end) for f in find_terms(self.doc.text, [value])]
        if not ranges:  # z. B. Teilwort-Markierung → exakte Vorkommen
            ranges = [(m.start(), m.end()) for m in re.finditer(re.escape(value), self.doc.text)]
        return self.add_manual(ranges, entity_type)

    def set_manual_text(self, text: str | None) -> None:
        self.manual_text = text
        self.dirty = True
        self.manualTextChanged.emit()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(copy.deepcopy(self.findings))
        self.findings = self._undo.pop()
        self._reindex()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(copy.deepcopy(self.findings))
        self.findings = self._redo.pop()
        self._reindex()

    def settings_changed(self) -> None:
        """Ersetzungsmodus o. Ä. geändert – nur Ausgabe neu berechnen."""
        self._redacted = None
        self.changed.emit()

    # ------------------------------------------------------------------ intern
    def _snapshot(self) -> None:
        self._undo.append(copy.deepcopy(self.findings))
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()

    def _reindex(self) -> None:
        self._by_id = {f.id: f for f in self.findings}
        self._redacted = None
        self.dirty = True
        self.changed.emit()


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()
