"""Teil des Hauptfensters: Zoom der Seitenansicht und gekoppeltes Scrollen von Original und Vorschau.

``ViewMixin`` wird nur von ``MainWindow`` geerbt und nutzt dessen Attribute (``session``, ``settings``,
``runner``, Ansichten …)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

if TYPE_CHECKING:
    from .main_window import DocPanel


#: Zoomstufen für Strg+Plus/Minus und Strg+Mausrad (100 % = echte Papiergröße)
ZOOM_STEPS = [25, 33, 50, 67, 75, 90, 100, 110, 125, 150, 175, 200, 250, 300, 400]
ZOOM_PRESETS = [("Seitenbreite", "breite"), ("Ganze Seite", "seite"), ("50 %", 50), ("75 %", 75), ("100 %", 100),
                ("125 %", 125), ("150 %", 150), ("200 %", 200)]


class ViewMixin:
    # ================================================================== Zoom
    def _zoom_views(self) -> list:
        return [p.pages for p in (self.left, self.right) if p.pages.max_page_size()[0]]

    def current_zoom_percent(self) -> int:
        v = self.right.pages
        return max(1, round(v.zoom / v.real_size_zoom() * 100))

    def _apply_zoom(self) -> None:
        """Zoom gemäß Einstellung setzen: Seitenbreite / Ganze Seite passen sich der Fenstergröße an,
        ein fester Prozentwert gilt für alle Dokumente (100 % = echte Papiergröße)."""
        views = self._zoom_views()
        if views:
            mode = self.settings.zoom_mode
            zoom = None
            if mode in ("breite", "seite"):
                fits = [z for z in (v.fit_zoom(mode) for v in views if v.isVisible()) if z]
                zoom = min(fits) if fits else None
            else:
                zoom = views[0].real_size_zoom() * self.settings.zoom_percent / 100
            if zoom:
                for v in (self.left.pages, self.right.pages):
                    v.set_zoom(zoom)
        self._update_zoom_box()

    def _on_view_resized(self) -> None:
        if self.settings.zoom_mode != "fest":
            self._zoom_timer.start()

    def set_zoom_mode(self, mode: str, percent: int | None = None) -> None:
        self.settings.zoom_mode = mode
        if percent is not None:
            self.settings.zoom_percent = max(10, min(int(percent), 400))
        self._save_settings()
        self._apply_zoom()

    def zoom_step(self, direction: int) -> None:
        """Eine Zoomstufe größer (+1) oder kleiner (-1) – danach gilt ein fester Wert."""
        cur = self.current_zoom_percent() if self._zoom_views() else self.settings.zoom_percent
        if direction > 0:
            nxt = next((s for s in ZOOM_STEPS if s > cur + 0.5), ZOOM_STEPS[-1])
        else:
            nxt = next((s for s in reversed(ZOOM_STEPS) if s < cur - 0.5), ZOOM_STEPS[0])
        self.set_zoom_mode("fest", nxt)

    def _on_zoom_box_activated(self, index: int) -> None:
        value = self.zoom_box.itemData(index)
        if value in ("breite", "seite"):
            self.set_zoom_mode(value)
        elif value is not None:
            self.set_zoom_mode("fest", int(value))

    def _on_zoom_box_edited(self) -> None:
        text = self.zoom_box.currentText().strip()
        for label, _value in ZOOM_PRESETS:
            if text.casefold() == label.casefold():
                self._on_zoom_box_activated(self.zoom_box.findText(label))
                return
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            self.set_zoom_mode("fest", int(digits))
        else:
            self._update_zoom_box()

    def _update_zoom_box(self) -> None:
        box = self.zoom_box
        box.blockSignals(True)
        mode = self.settings.zoom_mode
        if mode in ("breite", "seite"):
            box.setCurrentIndex(box.findData(mode))
            if self._zoom_views():
                box.setToolTip(f"{box.currentText()} – entspricht {self.current_zoom_percent()} %")
        else:
            idx = box.findData(self.settings.zoom_percent)
            if idx >= 0:
                box.setCurrentIndex(idx)
            box.setEditText(f"{self.settings.zoom_percent} %")
            box.setToolTip("Zoom der Seitenansicht (100 % = Papiergröße)")
        box.blockSignals(False)
        enabled = bool(self.session and self.session.doc.is_pdf)
        for w in (self.zoom_box, self.zoom_in_btn, self.zoom_out_btn):
            w.setEnabled(enabled)

    # ================================================================== Synchronisation
    def _sync_tabs(self, other: DocPanel, index: int) -> None:
        if other.tabs.currentIndex() != index and index < other.tabs.count():
            other.tabs.setCurrentIndex(index)

    def _sync_text_scroll(self, source: DocPanel) -> None:
        if self._syncing or not self.settings.sync_scroll or not self.session or self.session.manual_text is not None:
            return
        self._syncing = True
        red = self.session.redacted()
        if source is self.left:
            self.right.text.scroll_to_offset(red.to_redacted(self.left.text.first_visible_offset()))
        else:
            self.left.text.scroll_to_offset(red.to_original(self.right.text.first_visible_offset()))
        self._syncing = False

    def _sync_page_scroll(self, other: DocPanel, value: int) -> None:
        if self._syncing or not self.settings.sync_scroll:
            return
        self._syncing = True
        other.pages.verticalScrollBar().setValue(value)
        self._syncing = False

    def _set_sync(self, on: bool) -> None:
        self.settings.sync_scroll = on
        self._save_settings()

    def _set_show_original(self, on: bool) -> None:
        self.settings.show_original = on
        self._save_settings()
        self.left.setVisible(on)
        if self.session:
            QTimer.singleShot(0, self._fit_layout)
