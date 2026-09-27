"""Hinweise zum geöffneten Dokument – so knapp wie möglich, so deutlich wie nötig.

* ``NoticeBar``     – eine Zeile über dem Dokument, nur für kritische Hinweise (OCR, Seiten ohne Text),
                      pro Dokument schließbar
* ``NoticeButton``  – Zähler in der Statusleiste („⚠ 2 Hinweise“), ein Klick zeigt alle Hinweise
* ``page_marks``    – Seitensymbole für die Seitenansicht (OCR, ohne Text, Bild, Formular)
"""

from __future__ import annotations

import html

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout

from ..core.loaders import Notice
from .pdf_view import ORANGE, PageMark

#: Hilfekapitel je Hinweisart
HELP_ANCHOR = {"ocr": "ocr", "ocr_error": "ocr", "no_text": "ocr", "pages_no_text": "ocr"}
DEFAULT_ANCHOR = "hinweise"


def help_anchor(n: Notice) -> str:
    return HELP_ANCHOR.get(n.kind, DEFAULT_ANCHOR)


def page_marks(notices: list[Notice]) -> dict[int, list[PageMark]]:
    marks: dict[int, list[PageMark]] = {}
    for n in notices:
        if not n.badge:
            continue
        for page in n.pages:
            marks.setdefault(page, []).append(PageMark(n.badge, n.detail, n.critical))
    return marks


class NoticeBar(QFrame):
    """Einzeilige Leiste für kritische Hinweise. Der volle Text steht im Tooltip."""

    helpRequested = Signal(str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("noticeBar")
        self.setStyleSheet(
            f"#noticeBar {{ background:#fff4e6; border:1px solid #ffd8a8; border-left:4px solid {ORANGE};"
            " border-radius:3px; }"
            "#noticeBar QLabel { color:#5f1f00; background:transparent; }"
            "#noticeBar QToolButton { color:#5f1f00; border:none; padding:0 4px; }"
            "#noticeBar QToolButton:hover { background:#ffe8cc; }"
        )
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(False)
        # darf schmaler werden als der Text (wird abgeschnitten, nie umbrochen → immer genau eine Zeile)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.label.linkActivated.connect(self.helpRequested.emit)
        self.close_btn = QToolButton(text="✕", autoRaise=True)
        self.close_btn.setToolTip("Für dieses Dokument ausblenden – der Hinweis bleibt in der Statusleiste "
                                  "und an der Seite sichtbar")
        self.close_btn.clicked.connect(self._close)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 2, 2)
        lay.setSpacing(6)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.close_btn)
        self.notices: list[Notice] = []
        self.hide()

    def set_notices(self, notices: list[Notice]) -> None:
        """Nur kritische Hinweise; leere Liste blendet die Leiste aus."""
        self.notices = [n for n in notices if n.critical]
        if not self.notices:
            self.hide()
            return
        parts = [f"<b>{html.escape(n.short)}</b>" for n in self.notices]
        anchor = help_anchor(self.notices[0])
        self.label.setText(f"⚠ {'  ·  '.join(parts)}  <a href='{anchor}' style='color:#a33a00'>Mehr …</a>")
        self.setToolTip("\n\n".join(n.detail for n in self.notices))
        self.show()

    def _close(self) -> None:
        self.hide()
        self.closed.emit()


class NoticePopup(QFrame):
    """Liste aller Hinweise (öffnet sich über dem Zähler in der Statusleiste)."""

    linkActivated = Signal(str)

    def __init__(self, notices: list[Notice], parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        head = QLabel("<b>Hinweise zu diesem Dokument</b>")
        lay.addWidget(head)
        for n in sorted(notices, key=lambda x: not x.critical):
            links = []
            if n.pages:
                links.append(f"<a href='page:{n.pages[0]}'>Seite {n.pages[0] + 1} zeigen</a>")
            links.append(f"<a href='help:{help_anchor(n)}'>Mehr …</a>")
            row = QLabel(
                f"<span style='color:{ORANGE if n.critical else 'gray'}'>{'⚠' if n.critical else 'ⓘ'}</span> "
                f"<b>{html.escape(n.short)}</b><br>"
                f"<span style='font-size:small'>{html.escape(n.detail)}</span><br>"
                f"<span style='font-size:small'>{' · '.join(links)}</span>"
            )
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setWordWrap(True)
            row.setMinimumWidth(420)
            row.setMaximumWidth(520)
            row.linkActivated.connect(self._link)
            lay.addWidget(row)
        self.rows = self.findChildren(QLabel)[1:]

    def _link(self, link: str) -> None:
        self.linkActivated.emit(link)
        self.close()


class NoticeButton(QToolButton):
    """Zähler in der Statusleiste; ein Klick öffnet die Liste aller Hinweise."""

    pageRequested = Signal(int)
    helpRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.clicked.connect(self.show_popup)
        self.notices: list[Notice] = []
        self.popup: NoticePopup | None = None
        self.hide()

    def set_notices(self, notices: list[Notice]) -> None:
        self.notices = list(notices)
        if not self.notices:
            self.hide()
            return
        n = len(self.notices)
        critical = any(x.critical for x in self.notices)
        self.setText(f"{'⚠' if critical else 'ⓘ'} {n} {'Hinweis' if n == 1 else 'Hinweise'}")
        self.setStyleSheet(f"color:{ORANGE}; font-weight:bold;" if critical else "")
        self.setToolTip("\n".join(("⚠ " if x.critical else "ⓘ ") + x.short for x in self.notices)
                        + "\n\nKlicken für Details")
        self.show()

    def show_popup(self) -> None:
        if not self.notices:
            return
        self.popup = NoticePopup(self.notices, self.window())
        self.popup.linkActivated.connect(self._link)
        self.popup.adjustSize()
        top_right = self.mapToGlobal(QPoint(self.width(), 0))
        self.popup.move(QPoint(max(0, top_right.x() - self.popup.width()), top_right.y() - self.popup.height() - 4))
        self.popup.show()

    def _link(self, link: str) -> None:
        kind, _, value = link.partition(":")
        if kind == "page":
            self.pageRequested.emit(int(value))
        elif kind == "help":
            self.helpRequested.emit(value)

