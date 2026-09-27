"""Seitenansicht für PDFs: Seiten werden erst beim Sichtbarwerden gerendert, Hervorhebungen
und Schwärzungsbalken als Overlay gezeichnet (schnell, ohne das PDF neu zu erzeugen)."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QRubberBand, QScrollArea, QToolTip, QVBoxLayout, QWidget

from ..core.entities import info

ORANGE = "#e8590c"


@dataclass
class Overlay:
    page: int
    rect: fitz.Rect
    ids: list[int]
    active: bool
    entity_type: str
    label: str | None = None


@dataclass
class PageMark:
    """Kleines Symbol oben auf einer Seite (z. B. „OCR“, „Bild“, „Formular“) mit Erklärung als Tooltip."""
    label: str
    tooltip: str
    critical: bool = False


class _PageWidget(QWidget):
    def __init__(self, view: "PdfPagesView", index: int, size: QRectF):
        super().__init__()
        self.view = view
        self.index = index
        self.page_size = size  # in PDF-Punkten
        self.pixmap: QPixmap | None = None
        self.overlays: list[Overlay] = []
        self._band: QRubberBand | None = None
        self._origin: QPoint | None = None
        self._badges: list[tuple[QRectF, str]] = []   # gezeichnete Seitensymbole + Tooltip
        self.setMouseTracking(True)
        self.update_size()

    def update_size(self) -> None:
        z = self.view.zoom
        self.setFixedSize(int(self.page_size.width() * z), int(self.page_size.height() * z))

    def to_widget(self, r: fitz.Rect) -> QRectF:
        z = self.view.zoom
        return QRectF(r.x0 * z, r.y0 * z, r.width * z, r.height * z)

    def to_pdf(self, p: QPointF) -> fitz.Point:
        z = self.view.zoom
        return fitz.Point(p.x() / z, p.y() / z)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        if self.pixmap is not None:
            p.drawPixmap(self.rect(), self.pixmap)
        else:
            p.setPen(QColor("#adb5bd"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"Seite {self.index + 1} …")
        selected = self.view.selected_ids
        for ov in self.overlays:
            r = self.to_widget(ov.rect)
            is_sel = bool(selected.intersection(ov.ids))
            if self.view.mode == "redacted":
                if not ov.active:  # abgewählter Fund: bleibt lesbar, grau gestrichelt umrandet
                    p.setPen(QPen(QColor(134, 142, 150), 2 if is_sel else 1, Qt.PenStyle.DashLine))
                    p.drawRect(r)
                    continue
                p.fillRect(r, QColor("black"))
                if ov.label:
                    f = QFont("Arial")
                    f.setPixelSize(max(6, int(min(r.height() * 0.7, r.width() / max(len(ov.label) * 0.58, 1)))))
                    p.setFont(f)
                    p.setPen(QColor("white"))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, ov.label)
                if is_sel:
                    p.setPen(QPen(QColor(info(ov.entity_type).color), 2))
                    p.drawRect(r.adjusted(-2, -2, 2, 2))
            else:
                c = QColor(info(ov.entity_type).color)
                if ov.active:
                    c.setAlpha(150 if is_sel else 70)
                    p.fillRect(r, c)
                    p.setPen(QPen(QColor(info(ov.entity_type).color), 2 if is_sel else 1))
                else:
                    p.setPen(QPen(QColor(134, 142, 150), 2 if is_sel else 1, Qt.PenStyle.DashLine))
                p.drawRect(r)
        # Seitenrand
        if self.index in self.view.ocr_pages:
            p.setPen(QPen(QColor(ORANGE), 4))
            p.drawRect(self.rect().adjusted(2, 2, -2, -2))
        else:
            p.setPen(QColor("#ced4da"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        self._paint_badges(p)
        p.end()

    def _paint_badges(self, p: QPainter) -> None:
        """Seitensymbole oben links: kritische (orange) zuerst, dann Informationen (grau)."""
        self._badges = []
        marks = sorted(self.view.page_marks.get(self.index, []), key=lambda m: not m.critical)
        if not marks:
            return
        f = QFont("Arial")
        f.setPixelSize(11)
        p.setFont(f)
        x = 6.0
        for m in marks:
            f.setBold(m.critical)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(m.label) + 14
            r = QRectF(x, 6, w, 18)
            if r.right() > self.width() - 4 and x > 6:
                break  # sehr kleine Zoomstufe: nur so viele Symbole wie passen
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ORANGE) if m.critical else QColor(73, 80, 87, 200))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor("white"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, m.label)
            self._badges.append((r, m.tooltip))
            x = r.right() + 4
        p.setBrush(Qt.BrushStyle.NoBrush)

    # Klick = Fund auswählen, Ziehen = Bereich markieren (nur Original)
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position().toPoint()
            if self.view.allow_area_select:
                self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
                self._band.setGeometry(QRect(self._origin, self._origin))
                self._band.show()
        elif e.button() == Qt.MouseButton.RightButton:
            ids = self._ids_at(e.position())
            self.view.contextRequested.emit(ids, e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._band is not None and self._origin is not None:
            self._band.setGeometry(QRect(self._origin, e.position().toPoint()).normalized())
        ids = self._ids_at(e.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if ids else Qt.CursorShape.CrossCursor
                       if self.view.allow_area_select else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        rect = QRect(self._origin, e.position().toPoint()).normalized()
        if self._band is not None:
            self._band.hide()
            self._band.deleteLater()
            self._band = None
        self._origin = None
        if rect.width() > 4 and rect.height() > 4 and self.view.allow_area_select:
            a, b = self.to_pdf(QPointF(rect.topLeft())), self.to_pdf(QPointF(rect.bottomRight()))
            self.view.areaSelected.emit(self.index, fitz.Rect(a, b), e.globalPosition().toPoint())
        else:
            self.view.overlayClicked.emit(self._ids_at(e.position()))

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            badge = next((tip for r, tip in self._badges if r.contains(QPointF(e.pos()))), "")
            if badge:
                QToolTip.showText(e.globalPos(), badge, self)
                return True
        if e.type() == QEvent.Type.ToolTip and self.view.tooltip_provider is not None:
            ids = self._ids_at(QPointF(e.pos()))
            text = self.view.tooltip_provider(ids) if ids else ""
            if text:
                QToolTip.showText(e.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def _ids_at(self, pos: QPointF) -> list[int]:
        pt = self.to_pdf(pos)
        for ov in self.overlays:
            if pt in (fitz.Rect(ov.rect) + (-1, -1, 1, 1)):
                return ov.ids
        return []


class PdfPagesView(QScrollArea):
    overlayClicked = Signal(list)              # Fund-IDs
    contextRequested = Signal(list, QPoint)    # Fund-IDs, globale Position
    areaSelected = Signal(int, object, QPoint)  # Seite, fitz.Rect, globale Position
    zoomRequested = Signal(int)               # Strg+Mausrad: +1 / -1
    viewResized = Signal()                    # Größe der Ansicht geändert (für „Seitenbreite“/„Ganze Seite“)

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.mode = mode  # "original" | "redacted"
        self.allow_area_select = True  # Bereich aufziehen = Text darunter schwärzen (auch in der Vorschau)
        self.tooltip_provider = None     # Funktion(ids) -> Tooltip-Text
        self.zoom = 1.25
        self.selected_ids: set[int] = set()
        self.ocr_pages: set[int] = set()   # per Texterkennung gelesene Seiten (orangefarbener Rahmen)
        self.page_marks: dict[int, list[PageMark]] = {}   # Seitensymbole je Seite
        self._doc: fitz.Document | None = None
        self._pages: list[_PageWidget] = []
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setBackgroundRole(self.palette().ColorRole.Dark)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setSpacing(12)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(self._container)
        self._render_timer = QTimer(self, singleShot=True, interval=30)
        self._render_timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._render_timer.start())

    # ------------------------------------------------------------------ Inhalt
    def set_pdf(self, data: bytes | None) -> None:
        for w in self._pages:
            w.setParent(None)
            w.deleteLater()
        self._pages = []
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        if not data:
            return
        self._doc = fitz.open(stream=data, filetype="pdf")
        for i, page in enumerate(self._doc):
            w = _PageWidget(self, i, QRectF(0, 0, page.rect.width, page.rect.height))
            self._layout.addWidget(w)
            self._pages.append(w)
        if not self._pages:
            self._layout.addWidget(QLabel("PDF hat keine Seiten."))
        self._relayout()
        self.viewResized.emit()  # neue Seitengrößen → Zoom ggf. neu anpassen

    def set_ocr_pages(self, pages) -> None:
        self.ocr_pages = set(pages or ())
        for w in self._pages:
            w.update()

    def set_page_marks(self, marks: dict[int, list[PageMark]] | None) -> None:
        self.page_marks = dict(marks or {})
        for w in self._pages:
            w.update()

    def set_overlays(self, overlays: list[Overlay]) -> None:
        per_page: dict[int, list[Overlay]] = {}
        for ov in overlays:
            per_page.setdefault(ov.page, []).append(ov)
        for w in self._pages:
            w.overlays = per_page.get(w.index, [])
            w.update()

    def set_selected_ids(self, ids: set[int]) -> None:
        self.selected_ids = ids
        for w in self._pages:
            if w.overlays:
                w.update()

    # ------------------------------------------------------------------ Zoom
    def real_size_zoom(self) -> float:
        """Zoomfaktor für 100 % (echte Papiergröße): PDF-Punkte (1/72 Zoll) → logische Pixel."""
        return max(self.logicalDpiX(), 72) / 72

    def max_page_size(self) -> tuple[float, float]:
        """Größte Seitenbreite und -höhe in PDF-Punkten (0, 0 ohne Dokument)."""
        if not self._pages:
            return 0.0, 0.0
        return (max(w.page_size.width() for w in self._pages), max(w.page_size.height() for w in self._pages))

    def fit_zoom(self, mode: str) -> float | None:
        """Zoom für „Seitenbreite“ (``breite``) bzw. „Ganze Seite“ (``seite``) bei der aktuellen Größe.

        Gerechnet mit der Größe der Ansicht selbst (nicht des Viewports), einschließlich Platz für die
        senkrechte Bildlaufleiste – sonst würde das Ein-/Ausblenden der Leiste den Zoom hin- und herschalten."""
        pw, ph = self.max_page_size()
        if not pw or not ph:
            return None
        m = self._layout.contentsMargins()
        frame = 2 * self.frameWidth()
        avail_w = self.width() - frame - self.verticalScrollBar().sizeHint().width() - m.left() - m.right() - 2
        avail_h = self.height() - frame - m.top() - m.bottom() - 2
        if avail_w < 80 or avail_h < 80:
            return None  # Ansicht (noch) nicht aufgebaut
        zoom = avail_w / pw
        if mode == "seite":
            zoom = min(zoom, avail_h / ph)
        return zoom

    def set_zoom(self, zoom: float) -> None:
        zoom = max(0.2, min(zoom, 6.0))
        if abs(zoom - self.zoom) < 1e-3:
            return
        rel = self.verticalScrollBar().value() / max(1, self.verticalScrollBar().maximum())
        self.zoom = zoom
        for w in self._pages:
            w.pixmap = None
            w.update_size()
        self._relayout()
        self.verticalScrollBar().setValue(int(rel * self.verticalScrollBar().maximum()))
        self._render_timer.start()

    def reveal(self, page: int, rect: fitz.Rect | None = None) -> None:
        if not (0 <= page < len(self._pages)):
            return
        w = self._pages[page]
        y = w.y()
        if rect is not None:
            y += int(rect.y0 * self.zoom) - self.viewport().height() // 3
        self.verticalScrollBar().setValue(max(0, y))

    # ------------------------------------------------------------------ Rendering
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render_timer.start()
        self.viewResized.emit()

    def showEvent(self, e):
        super().showEvent(e)
        # War die Ansicht beim Laden verborgen (z. B. Reiter „Seiten“ nach einem Text-Dokument erst neu
        # angelegt), stimmt die Größe des Seiten-Containers noch nicht → nach dem Anzeigen neu setzen.
        QTimer.singleShot(0, self._relayout)
        QTimer.singleShot(0, self.viewResized.emit)

    def _relayout(self) -> None:
        """Größe des Seiten-Containers aus den Seitengrößen berechnen.

        Nicht ``adjustSize()``: Solange die Ansicht verborgen ist, ist das Layout nicht aktiv und
        liefert eine veraltete Größe (z. B. 24×24 vom vorherigen, leeren Zustand) – das PDF bliebe
        dann unsichtbar, bis ein Größenwechsel das Layout auslöst."""
        if self._pages:
            m = self._layout.contentsMargins()
            width = max(w.width() for w in self._pages) + m.left() + m.right()
            height = (sum(w.height() for w in self._pages) + self._layout.spacing() * (len(self._pages) - 1)
                      + m.top() + m.bottom())
            self._container.resize(width, height)
        else:
            self._container.adjustSize()
        self._layout.invalidate()
        self._render_timer.start()

    def _render_visible(self) -> None:
        if self._doc is None:
            return
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        margin = self.viewport().height()
        for w in self._pages:
            visible = w.y() + w.height() >= top - margin and w.y() <= bottom + margin
            if visible and w.pixmap is None:
                w.pixmap = self._render(w.index)
                w.update()
            elif not visible and w.pixmap is not None and abs(w.y() - top) > 6 * margin:
                w.pixmap = None  # Speicher freigeben

    def _render(self, index: int) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pix = self._doc[index].get_pixmap(matrix=fitz.Matrix(self.zoom * ratio, self.zoom * ratio), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        qp = QPixmap.fromImage(img)
        qp.setDevicePixelRatio(ratio)
        return qp

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)
