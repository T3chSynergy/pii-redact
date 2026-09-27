"""Ergebnis der KI-Nachprüfung: Restrisiko, Begründung und Hinweise mit Sprung zur Stelle."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.batch import ki_summary
from ..core.entities import info
from ..core.llm_review import ReviewResult

RISK_COLORS = {"gering": "#2b8a3e", "mittel": "#e8590c", "hoch": "#c92a2a", "unklar": "#868e96"}


class KiReviewPanel(QWidget):
    hintActivated = Signal(list)       # Fund-IDs der Vorschläge zu einem Hinweis
    activateAll = Signal()             # alle Vorschläge schwärzen
    discardAll = Signal()              # nicht aktivierte Vorschläge entfernen
    runRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.head = QLabel()
        self.head.setTextFormat(Qt.TextFormat.RichText)
        self.meta = QLabel()
        self.meta.setStyleSheet("color: gray")
        self.meta.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAlternatingRowColors(True)
        self.list.setSpacing(3)
        self.list.itemActivated.connect(self._activated)
        self.list.itemClicked.connect(self._activated)
        self.note = QLabel("Vorschläge stehen in der Fundliste (Quelle „KI“, nicht aktiviert). Das Modell kann "
                           "irren – bitte selbst beurteilen.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray")
        self.run_btn = QPushButton("KI-Prüfung starten")
        self.run_btn.clicked.connect(self.runRequested.emit)
        self.all_btn = QPushButton("Alle Vorschläge schwärzen")
        self.all_btn.clicked.connect(self.activateAll.emit)
        self.discard_btn = QPushButton("Vorschläge verwerfen")
        self.discard_btn.setToolTip("Nicht aktivierte Vorschläge der KI-Nachprüfung aus der Fundliste entfernen")
        self.discard_btn.clicked.connect(self.discardAll.emit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.all_btn)
        buttons.addWidget(self.discard_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.run_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.head)
        lay.addWidget(self.meta)
        lay.addWidget(self.summary)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.note)
        lay.addLayout(buttons)
        self.result: ReviewResult | None = None
        self.clear()

    # ------------------------------------------------------------------ Inhalt
    def clear(self, stored: dict | None = None, text: str = "") -> None:
        """Kein Ergebnis für das aktuelle Dokument (ggf. mit Hinweis auf eine frühere Prüfung)."""
        self.result = None
        self.head.setText("<b>KI-Nachprüfung</b>")
        if stored:
            self.meta.setText(f"Zuletzt: {ki_summary(stored)}. Die Hinweise selbst werden nicht gespeichert.")
        else:
            self.meta.setText(text or "Für dieses Dokument noch nicht durchgeführt.")
        self.summary.setText("Ein Sprachmodell bewertet das geschwärzte Ergebnis: Sind Personen trotzdem "
                             "erkennbar – durch übersehene Angaben oder den Zusammenhang?")
        self.list.clear()
        self.note.hide()
        self.all_btn.hide()
        self.discard_btn.hide()

    def set_running(self, running: bool, text: str = "") -> None:
        self.run_btn.setEnabled(not running)
        self.run_btn.setText("Läuft …" if running else "KI-Prüfung starten")
        if running:
            self.meta.setText(text or "Anfrage läuft …")

    def show_result(self, res: ReviewResult, ids_by_hint: list[list[int]]) -> None:
        self.result = res
        color = RISK_COLORS.get(res.risk, "#868e96")
        self.head.setText(f"<b>Restrisiko: <span style='color:{color}'>{html.escape(res.risk.upper())}</span></b>")
        self.meta.setText(f"{res.model} über {res.host} · {res.seconds:.0f} s"
                          + (f" · {res.chunks} Anfragen" if res.chunks > 1 else ""))
        self.summary.setText(res.summary or "(keine Begründung geliefert)")
        self.list.clear()
        for hint, ids in zip(res.hints, ids_by_hint):
            label = info(hint.entity_type).label
            text = f"„{hint.quote}“ – {label}"
            if hint.reason:
                text += f"\n{hint.reason}"
            if not hint.found:
                text += "\n(nicht wörtlich im Text gefunden – bitte selbst suchen)"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ids)
            if not hint.found:
                item.setForeground(Qt.GlobalColor.gray)
            item.setToolTip(hint.reason or hint.quote)
            self.list.addItem(item)
        if not res.hints:
            self.list.addItem(QListWidgetItem("Keine Hinweise."))
        has = any(ids for ids in ids_by_hint)
        self.note.setVisible(has)
        self.all_btn.setVisible(has)
        self.discard_btn.setVisible(has)

    def _activated(self, item: QListWidgetItem) -> None:
        ids = item.data(Qt.ItemDataRole.UserRole) or []
        if ids:
            self.hintActivated.emit(list(ids))
