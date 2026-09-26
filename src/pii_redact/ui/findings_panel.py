"""Fundliste: an/aus schalten, Typ ändern, filtern, zur Fundstelle springen."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPoint,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.entities import AREA_KEYS, all_keys, info
from .session import DocumentSession

COL_ON, COL_TYPE, COL_TEXT, COL_WHERE, COL_SCORE, COL_SOURCE = range(6)
HEADERS = ["", "Typ", "Text", "Stelle", "Score", "Quelle"]
ID_ROLE = Qt.ItemDataRole.UserRole + 1
AREA_ROLE = Qt.ItemDataRole.UserRole + 2


class FindingsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: DocumentSession | None = None
        self._rows = []

    def set_session(self, session: DocumentSession | None) -> None:
        self.beginResetModel()
        self.session = session
        self._rows = list(session.findings) if session else []
        self.endResetModel()

    def reload(self) -> None:
        self.set_session(self.session)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def _where(self, f) -> str:
        doc = self.session.doc
        if f.is_area:
            return f"S. {int(f.area[0]) + 1}"
        page = doc.page_of(f.start)
        if page is not None:
            return f"S. {page + 1}" + (" · OCR" if doc.is_ocr(f.start, f.end) else "")
        return f"Z. {doc.text.count(chr(10), 0, f.start) + 1}"

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        f = self._rows[index.row()]
        col = index.column()
        if role == ID_ROLE:
            return f.id
        if role == AREA_ROLE:
            return f.is_area
        if role == Qt.ItemDataRole.CheckStateRole and col == COL_ON:
            return Qt.CheckState.Checked if f.active else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if col == COL_TYPE:
                return info(f.entity_type).label
            if col == COL_TEXT:
                if f.is_area:
                    return area_description(f)
                return f.text.replace("\n", " ⏎ ")
            if col == COL_WHERE:
                if role == Qt.ItemDataRole.ToolTipRole and self.session.doc.is_ocr(f.start, f.end):
                    return ("Per Texterkennung (OCR) gelesen – der Text kann falsch gelesen sein. "
                            "Bitte mit der Seitenansicht vergleichen.")
                return self._where(f)
            if col == COL_SCORE:
                return f"{f.score:.2f}"
            if col == COL_SOURCE:
                return f.recognizer if role == Qt.ItemDataRole.ToolTipRole else ("manuell" if f.source == "manuell" else "auto")
        if role == Qt.ItemDataRole.EditRole and col == COL_TYPE:
            return f.entity_type
        if role == Qt.ItemDataRole.ForegroundRole and not f.active:
            return QBrush(QColor(134, 142, 150))
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_WHERE and self.session.doc.is_ocr(f.start, f.end):
            return QBrush(QColor(232, 89, 12))
        if role == Qt.ItemDataRole.DecorationRole and col == COL_TYPE:
            return QColor(info(f.entity_type).color)
        if role == Qt.ItemDataRole.UserRole:  # Sortierschlüssel
            return {COL_ON: int(f.active), COL_TYPE: info(f.entity_type).label, COL_TEXT: f.text.casefold(),
                    COL_WHERE: f.start, COL_SCORE: f.score, COL_SOURCE: f.source}[col]
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_ON:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == COL_TYPE:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or self.session is None:
            return False
        f = self._rows[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == COL_ON:
            self.session.set_active([f.id], Qt.CheckState(value) == Qt.CheckState.Checked)
            return True
        if role == Qt.ItemDataRole.EditRole and index.column() == COL_TYPE and value:
            self.session.set_type([f.id], value)
            return True
        return False


def area_description(f) -> str:
    """Text für einen frei gezogenen Bereich, z. B. „▭ Bereich 4,2 × 1,5 cm“."""
    _p, x0, y0, x1, y1 = f.area
    w, h = (x1 - x0) / 72 * 2.54, (y1 - y0) / 72 * 2.54
    return f"▭ Bereich {w:.1f} × {h:.1f} cm".replace(".", ",")


class _TypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        model = index.model()
        is_area = bool(model.data(index, AREA_ROLE))
        for key in (AREA_KEYS if is_area else all_keys()):
            box.addItem(info(key).label, key)
        return box

    def setEditorData(self, editor, index):
        i = editor.findData(index.data(Qt.ItemDataRole.EditRole))
        editor.setCurrentIndex(max(i, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class _FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_filter = ""
        self.type_filter = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def filterAcceptsRow(self, row, parent):
        m: FindingsModel = self.sourceModel()
        f = m._rows[row]
        if self.type_filter and f.entity_type != self.type_filter:
            return False
        text = area_description(f) if f.is_area else f.text
        if self.text_filter and self.text_filter not in text.casefold():
            return False
        return True


class FindingsPanel(QWidget):
    findingActivated = Signal(int)        # Klick in Liste → zur Stelle springen
    contextRequested = Signal(list, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = FindingsModel(self)
        self.proxy = _FilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.summary = QLabel("Kein Dokument geladen")
        self.search = QLineEdit(placeholderText="Filtern …", clearButtonEnabled=True)
        self.search.textChanged.connect(self._apply_filter)
        self.type_box = QComboBox()
        self.type_box.addItem("Alle Typen", "")
        for key in all_keys(include_areas=True):
            self.type_box.addItem(info(key).label, key)
        self.type_box.currentIndexChanged.connect(self._apply_filter)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_WHERE, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.setItemDelegateForColumn(COL_TYPE, _TypeDelegate(self.table))
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_ON, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_TYPE, 135)
        h.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        for c in (COL_WHERE, COL_SCORE, COL_SOURCE):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: self.contextRequested.emit(self.selected_ids(), self.table.viewport().mapToGlobal(pos))
        )
        self.table.selectionModel().currentRowChanged.connect(self._on_current)
        self.table.clicked.connect(lambda idx: self._on_current(idx, None))

        self.btn_on = QPushButton("Sichtbare an")
        self.btn_off = QPushButton("Sichtbare aus")
        self.btn_on.clicked.connect(lambda: self._set_visible(True))
        self.btn_off.clicked.connect(lambda: self._set_visible(False))

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.type_box)
        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_on)
        bottom.addWidget(self.btn_off)
        bottom.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.summary)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------------ API
    def set_session(self, session: DocumentSession | None) -> None:
        self.model.set_session(session)
        self._update_summary()

    def reload(self) -> None:
        keep = self.selected_ids()
        self.model.reload()
        self._update_summary()
        self.select_ids(keep, scroll=False)

    def selected_ids(self) -> list[int]:
        ids = []
        for idx in self.table.selectionModel().selectedRows():
            ids.append(self.proxy.data(idx, ID_ROLE))
        return ids

    def select_ids(self, ids: list[int], scroll: bool = True) -> None:
        sel = self.table.selectionModel()
        sel.blockSignals(True)
        self.table.clearSelection()
        first = None
        for row in range(self.proxy.rowCount()):
            idx = self.proxy.index(row, 0)
            if self.proxy.data(idx, ID_ROLE) in ids:
                sel.select(idx, sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
                if first is None:
                    first = idx
                    sel.setCurrentIndex(idx, sel.SelectionFlag.NoUpdate)
        sel.blockSignals(False)
        if first is not None and scroll:
            self.table.scrollTo(first, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ------------------------------------------------------------------ intern
    def _update_summary(self) -> None:
        s = self.model.session
        if s is None:
            self.summary.setText("Kein Dokument geladen")
            return
        total, active = s.counts()
        text = f"<b>{total}</b> Funde · <b>{active}</b> werden geschwärzt"
        if s.doc.has_ocr:
            n = sum(1 for f in s.findings if s.doc.is_ocr(f.start, f.end))
            text += f" · <span style='color:#e8590c'><b>{n}</b> aus Texterkennung</span>"
        self.summary.setText(text)

    def _apply_filter(self) -> None:
        self.proxy.text_filter = self.search.text().strip().casefold()
        self.proxy.type_filter = self.type_box.currentData() or ""
        self.proxy.invalidateFilter()

    def _on_current(self, idx, _prev) -> None:
        if idx is not None and idx.isValid():
            self.findingActivated.emit(self.proxy.data(idx, ID_ROLE))

    def _visible_ids(self) -> list[int]:
        return [self.proxy.data(self.proxy.index(r, 0), ID_ROLE) for r in range(self.proxy.rowCount())]

    def _set_visible(self, active: bool) -> None:
        if self.model.session:
            self.model.session.set_active(self._visible_ids(), active)
