"""Textansichten für Original und anonymisierte Fassung mit farbigen Hervorhebungen."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip

from ..core.entities import info


class Utf16Map:
    """Python-Indizes zählen Codepoints, Qt zählt UTF-16-Einheiten. Unterschied nur bei
    Zeichen außerhalb der BMP (z. B. Emojis) – dann wird eine Umrechnungstabelle aufgebaut."""

    def __init__(self, text: str):
        self._identity = all(ord(c) < 0x10000 for c in text)
        if self._identity:
            return
        self._to_qt = [0] * (len(text) + 1)
        q = 0
        for i, c in enumerate(text):
            self._to_qt[i] = q
            q += 2 if ord(c) >= 0x10000 else 1
        self._to_qt[len(text)] = q
        self._from_qt: dict[int, int] = {v: i for i, v in enumerate(self._to_qt)}

    def to_qt(self, i: int) -> int:
        return i if self._identity else self._to_qt[max(0, min(i, len(self._to_qt) - 1))]

    def from_qt(self, q: int) -> int:
        if self._identity:
            return q
        while q > 0 and q not in self._from_qt:
            q -= 1
        return self._from_qt.get(q, 0)


def _mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(max(f.pointSize(), 10))
    return f


class HighlightTextView(QPlainTextEdit):
    """Schreibgeschützte Textansicht. Hervorhebungen werden als (start, end, finding_ids,
    active)-Tupel in Python-Offsets übergeben."""

    clickedAt = Signal(int, QPoint)          # Python-Offset, globale Position (Linksklick)
    contextAt = Signal(int, QPoint)          # Python-Offset, globale Position (Rechtsklick)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(_mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context)
        self._map = Utf16Map("")
        self._text = ""
        self._marks: list[tuple[int, int, list[int], bool, str]] = []
        self._selected: set[int] = set()
        #: optional: Funktion(offset) -> Tooltip-Text (z. B. Original hinter einem Platzhalter)
        self.tooltip_provider = None

    # ------------------------------------------------------------------ Inhalt
    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        bar = self.verticalScrollBar().value()
        self._text = text
        self._map = Utf16Map(text)
        self.setPlainText(text)
        self.verticalScrollBar().setValue(bar)

    def invalidate(self) -> None:
        """Nächstes set_text() baut den Inhalt sicher neu auf (z. B. nach freier Bearbeitung)."""
        self._text = None

    def set_marks(self, marks: list[tuple[int, int, list[int], bool, str]]) -> None:
        """marks: (start, end, finding_ids, active, entity_type)"""
        self._marks = marks
        self._refresh()

    def set_selected_ids(self, ids: set[int]) -> None:
        self._selected = ids
        self._refresh()

    def _refresh(self) -> None:
        sels = []
        for start, end, ids, active, etype in self._marks:
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(self.document())
            cur.setPosition(self._map.to_qt(start))
            cur.setPosition(self._map.to_qt(end), QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            fmt = QTextCharFormat()
            color = QColor(info(etype).color)
            is_sel = bool(self._selected.intersection(ids))
            if active:
                color.setAlpha(150 if is_sel else 60)
                fmt.setBackground(color)
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                fmt.setUnderlineColor(QColor(info(etype).color))
            else:
                grey = QColor(134, 142, 150, 110 if is_sel else 35)
                fmt.setBackground(grey)
                fmt.setFontStrikeOut(False)
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
                fmt.setUnderlineColor(QColor(134, 142, 150))
            if is_sel:
                fmt.setProperty(QTextFormat.Property.OutlinePen, QColor(info(etype).color))
            sel.format = fmt
            sels.append(sel)
        self.setExtraSelections(sels)

    # ------------------------------------------------------------------ Navigation
    def reveal(self, start: int, end: int) -> None:
        cur = QTextCursor(self.document())
        cur.setPosition(self._map.to_qt(start))
        self.setTextCursor(cur)
        self.centerCursor()

    def selection_range(self) -> tuple[int, int] | None:
        cur = self.textCursor()
        if not cur.hasSelection():
            return None
        return self._map.from_qt(cur.selectionStart()), self._map.from_qt(cur.selectionEnd())

    def offset_at(self, pos: QPoint) -> int:
        return self._map.from_qt(self.cursorForPosition(pos).position())

    def first_visible_offset(self) -> int:
        return self._map.from_qt(self.cursorForPosition(QPoint(0, 0)).position())

    def scroll_to_offset(self, offset: int) -> None:
        block = self.document().findBlock(self._map.to_qt(offset))
        if block.isValid():
            self.verticalScrollBar().setValue(block.firstLineNumber())

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip and self.tooltip_provider is not None:
            text = self.tooltip_provider(self.offset_at(event.pos()))
            if text:
                QToolTip.showText(event.globalPos(), text, self.viewport())
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().viewportEvent(event)

    # ------------------------------------------------------------------ Maus
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            self.clickedAt.emit(self.offset_at(event.position().toPoint()), event.globalPosition().toPoint())

    def _on_context(self, pos: QPoint) -> None:
        self.contextAt.emit(self.offset_at(pos), self.viewport().mapToGlobal(pos))
