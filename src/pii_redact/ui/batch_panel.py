"""Oberfläche der Ordner-Bearbeitung: Start-Dialog, Arbeitsliste und Prüf-Leiste."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.batch import Status, Workspace, scan_folder

STATUS_STYLE = {
    # Status: (Symbol, Text, Farbe)
    Status.WAITING: ("○", "wartet", "#868e96"),
    Status.TO_REVIEW: ("●", "zu prüfen", "#e67700"),
    Status.REVIEWED: ("✔", "geprüft", "#2b8a3e"),
    Status.AUTO: ("◐", "automatisch", "#1c7ed6"),
    Status.ERROR: ("✖", "Fehler", "#c92a2a"),
    Status.MISSING: ("–", "fehlt", "#868e96"),
}
# Hellere Varianten für dunkles Windows-Design (besserer Kontrast)
_DARK_COLORS = {"#868e96": "#adb5bd", "#e67700": "#ffa94d", "#2b8a3e": "#69db7c",
                "#1c7ed6": "#74c0fc", "#c92a2a": "#ff8787", "#000": "#fff"}


def status_style(status: str) -> tuple[str, str, str]:
    """(Symbol, Text, Farbe) – Farbe passend zum hellen/dunklen Design."""
    from .help_window import is_dark

    sym, text, color = STATUS_STYLE.get(status, ("?", status, "#000"))
    return sym, text, (_DARK_COLORS.get(color, color) if is_dark() else color)


FILTERS = [
    ("Alle Dateien", None),
    ("Zu prüfen", {Status.TO_REVIEW, Status.WAITING}),
    ("Erledigt", {Status.REVIEWED, Status.AUTO}),
    ("Hinweise & Fehler", "hints"),
]


# ====================================================================== Start-Dialog
class NewBatchDialog(QDialog):
    def __init__(self, parent=None, source: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Ordner bearbeiten")
        self.resize(640, 260)
        self.source = QLineEdit(source)
        self.target = QLineEdit()
        self.recursive = QCheckBox("Unterordner einbeziehen", checked=True)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self._target_touched = False

        b1 = QPushButton("Durchsuchen …")
        b1.clicked.connect(self._pick_source)
        b2 = QPushButton("Durchsuchen …")
        b2.clicked.connect(self._pick_target)
        row1 = QHBoxLayout()
        row1.addWidget(self.source, 1)
        row1.addWidget(b1)
        row2 = QHBoxLayout()
        row2.addWidget(self.target, 1)
        row2.addWidget(b2)

        form = QFormLayout()
        form.addRow("Quellordner:", row1)
        form.addRow("Zielordner:", row2)
        form.addRow("", self.recursive)
        hint = QLabel(
            "Die Originale bleiben unverändert. Im Zielordner entstehen die bearbeiteten Dateien "
            "(gleiche Ordnerstruktur), ein Protokoll und der Arbeitsstand zum späteren Weitermachen."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Starten")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.info)
        lay.addWidget(hint)
        lay.addStretch(1)
        lay.addWidget(self.buttons)

        self.source.textChanged.connect(self._on_source_changed)
        self.target.textEdited.connect(lambda _t: setattr(self, "_target_touched", True))
        self.target.textChanged.connect(self._update_info)
        self.recursive.toggled.connect(self._update_info)
        self._on_source_changed()

    def _pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Quellordner wählen", self.source.text())
        if d:
            self.source.setText(d)

    def _pick_target(self):
        d = QFileDialog.getExistingDirectory(self, "Zielordner wählen", self.target.text() or self.source.text())
        if d:
            self._target_touched = True
            self.target.setText(d)

    def _on_source_changed(self):
        src = self.source.text().strip()
        if src and not self._target_touched:
            p = Path(src)
            self.target.setText(str(p.with_name(p.name + "_geschwaerzt")))
        self._update_info()

    def _update_info(self):
        src, tgt = Path(self.source.text().strip() or "."), self.target.text().strip()
        ok = False
        if not self.source.text().strip():
            msg = "Bitte einen Quellordner wählen."
        elif not src.is_dir():
            msg = "Quellordner nicht gefunden."
        else:
            try:
                files = scan_folder(src, self.recursive.isChecked(), exclude=Path(tgt) if tgt else None)
            except OSError as exc:
                files, msg = [], f"Ordner kann nicht gelesen werden: {exc}"
            else:
                msg = f"<b>{len(files)}</b> Datei(en) gefunden (PDF, TXT, Markdown)."
                ok = bool(files)
                if tgt and Path(tgt).resolve() == src.resolve():
                    msg, ok = "Quell- und Zielordner dürfen nicht gleich sein.", False
                elif tgt and Workspace.exists_in(Path(tgt)):
                    msg += "<br>Im Zielordner gibt es bereits einen Arbeitsstand – die Arbeit wird <b>fortgesetzt</b>."
        self.info.setText(msg)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok and bool(tgt))

    def _accept(self):
        self.accept()

    def values(self) -> tuple[Path, Path, bool]:
        return Path(self.source.text().strip()), Path(self.target.text().strip()), self.recursive.isChecked()


# ====================================================================== Arbeitsliste
class BatchPanel(QWidget):
    fileActivated = Signal(str)
    exportUnreviewed = Signal()
    openProtocol = Signal()
    openTarget = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.summary = QLabel()
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setMaximumHeight(16)
        self.analysis = QLabel()
        self.analysis.setStyleSheet("color: gray")
        self.filter = QComboBox()
        for label, _ in FILTERS:
            self.filter.addItem(label)
        self.filter.currentIndexChanged.connect(self._apply_filter)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Datei", "Funde", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(False)
        self.tree.itemActivated.connect(lambda it, _c: self.fileActivated.emit(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemClicked.connect(lambda it, _c: self.fileActivated.emit(it.data(0, Qt.ItemDataRole.UserRole)))
        self._items: dict[str, QTreeWidgetItem] = {}

        self.btn_auto = QPushButton("Ungeprüfte trotzdem exportieren …")
        self.btn_auto.setToolTip("Schwärzt alle noch nicht geprüften Dateien automatisch – im Protokoll als "
                                 "„nicht geprüft“ vermerkt.")
        self.btn_auto.clicked.connect(self.exportUnreviewed)
        btn_prot = QToolButton(text="Protokoll")
        btn_prot.clicked.connect(self.openProtocol)
        btn_target = QToolButton(text="Zielordner")
        btn_target.clicked.connect(self.openTarget)
        btn_close = QToolButton(text="Schließen")
        btn_close.setToolTip("Ordner-Arbeit beenden – der Stand ist gespeichert und kann fortgesetzt werden.")
        btn_close.clicked.connect(self.closeRequested)

        row = QHBoxLayout()
        row.addWidget(btn_prot)
        row.addWidget(btn_target)
        row.addStretch(1)
        row.addWidget(btn_close)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.title)
        lay.addWidget(self.summary)
        lay.addWidget(self.bar)
        lay.addWidget(self.analysis)
        lay.addWidget(self.filter)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.btn_auto)
        lay.addLayout(row)

    # ------------------------------------------------------------------
    def load(self, ws: Workspace) -> None:
        self.tree.clear()
        self._items = {}
        self.title.setText(f"<b>{ws.source.name}</b> → {ws.target.name}")
        self.title.setToolTip(f"Quelle: {ws.source}\nZiel: {ws.target}")
        for e in ws.ordered():
            it = QTreeWidgetItem([e.rel, "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, e.rel)
            it.setToolTip(0, e.rel)
            self.tree.addTopLevelItem(it)
            self._items[e.rel] = it
        self.refresh(ws, current=None, running=None, pending=set())

    def refresh(self, ws: Workspace, current: str | None, running: str | None, pending: set[str]) -> None:
        for e in ws.ordered():
            it = self._items.get(e.rel)
            if it is None:
                continue
            sym, text, color = status_style(e.status)
            if e.status == Status.WAITING and e.rel == running:
                sym, text = "◌", "wird analysiert"
            n = e.active_count
            hint = bool(e.warnings or e.error or e.export_note or e.note)
            it.setText(1, "" if n is None else str(n))
            it.setText(2, f"{sym} {text}" + (" · OCR" if e.has_ocr else "") + (" ⚠" if hint else ""))
            it.setForeground(2, QBrush(QColor("#e8590c" if e.has_ocr and not e.done else color)))
            tips = [Status.LABELS.get(e.status, e.status)]
            if e.reviewed_by:
                tips.append(f"geprüft von {e.reviewed_by} am {e.reviewed_at.replace('T', ' ')}")
            tips += e.warnings + [x for x in (e.note, e.export_note, e.error.splitlines()[0] if e.error else "") if x]
            it.setToolTip(2, "\n".join(tips))
            f = it.font(0)
            f.setBold(e.rel == current)
            for c in range(3):
                it.setFont(c, f)
            it.setData(0, Qt.ItemDataRole.UserRole + 1, "hints" if hint else "")
            it.setData(0, Qt.ItemDataRole.UserRole + 2, e.status)
        c = ws.counts()
        total = len(ws.entries)
        done = c.get(Status.REVIEWED, 0) + c.get(Status.AUTO, 0)
        self.summary.setText(
            f"<b>{c.get(Status.REVIEWED, 0)}</b> geprüft · <b>{c.get(Status.AUTO, 0)}</b> automatisch · "
            f"<b>{c.get(Status.TO_REVIEW, 0) + c.get(Status.WAITING, 0)}</b> offen"
            + (f" · <span style='color:#c92a2a'>{c.get(Status.ERROR, 0)} Fehler</span>" if c.get(Status.ERROR) else "")
        )
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} von {total} erledigt")
        waiting = c.get(Status.WAITING, 0)
        self.analysis.setText(f"Analyse im Hintergrund: noch {len(pending)} Datei(en) …" if pending else
                              ("" if not waiting else f"{waiting} Datei(en) noch nicht analysiert"))
        self.btn_auto.setEnabled(c.get(Status.TO_REVIEW, 0) + waiting > 0)
        if current and current in self._items:
            self.tree.setCurrentItem(self._items[current])
        self._apply_filter()

    def _apply_filter(self) -> None:
        _label, flt = FILTERS[self.filter.currentIndex()]
        for it in self._items.values():
            status = it.data(0, Qt.ItemDataRole.UserRole + 2)
            if flt is None:
                show = True
            elif flt == "hints":
                show = bool(it.data(0, Qt.ItemDataRole.UserRole + 1)) or status in (Status.ERROR, Status.MISSING)
            else:
                show = status in flt
            it.setHidden(not show)


# ====================================================================== Prüf-Leiste
class ReviewBar(QWidget):
    """Leiste über der Vergleichsansicht: aktuelle Datei, vor/zurück, „Geprüft & weiter“."""

    previous = Signal()
    next = Signal()
    confirm = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reviewBar")
        self.setStyleSheet("#reviewBar { background: palette(alternate-base); border-radius: 4px; }")
        self.label = QLabel()
        self.state = QLabel()
        self.btn_prev = QToolButton(text="◀")
        self.btn_prev.setToolTip("Vorherige Datei (Alt+Links)")
        self.btn_prev.clicked.connect(self.previous)
        self.btn_next = QToolButton(text="▶")
        self.btn_next.setToolTip("Nächste Datei (Alt+Rechts)")
        self.btn_next.clicked.connect(self.next)
        self.btn_ok = QPushButton("Geprüft ✓ && weiter")
        self.btn_ok.setToolTip("Datei als geprüft markieren, in den Zielordner exportieren und die nächste "
                               "offene Datei öffnen (Strg+Enter)")
        f = self.btn_ok.font()
        f.setBold(True)
        self.btn_ok.setFont(f)
        self.btn_ok.clicked.connect(self.confirm)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self.btn_prev)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.state)
        lay.addWidget(self.btn_next)
        lay.addSpacing(12)
        lay.addWidget(self.btn_ok)

    def set_file(self, index: int, total: int, rel: str, status_text: str, color: str, can_confirm: bool) -> None:
        self.label.setText(f"<b>Datei {index} von {total}:</b> {rel}")
        self.state.setText(f"<span style='color:{color}'>{status_text}</span>")
        self.btn_ok.setEnabled(can_confirm)


def confirm_auto_export(parent, count: int, waiting: int, ocr_skipped: int = 0) -> bool:
    text = (
        f"<p><b>{count} Datei(en)</b> werden <b>ohne manuelle Prüfung</b> automatisch geschwärzt und exportiert."
        + (f" {waiting} davon werden erst noch analysiert und danach exportiert." if waiting else "")
        + (f"</p><p><b>{ocr_skipped} Datei(en) mit Texterkennung (OCR)</b> werden nicht automatisch exportiert – "
           "sie müssen einzeln gründlich geprüft werden." if ocr_skipped else "")
        + ("</p><p>Auch Dateien, bei denen erst die Analyse Texterkennung ergibt, bleiben zur Prüfung stehen."
           if waiting else "")
        + "</p><p>Automatische Erkennung ist nicht vollständig. Die Dateien werden im Protokoll als "
        "<b>„automatisch exportiert (nicht geprüft)“</b> vermerkt und können später noch geprüft werden.</p>"
        "<p>Fortfahren?</p>"
    )
    box = QMessageBox(QMessageBox.Icon.Warning, "Ungeprüfte Dateien exportieren", text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, parent)
    box.button(QMessageBox.StandardButton.Yes).setText("Ohne Prüfung exportieren")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes

