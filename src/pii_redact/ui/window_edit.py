"""Teil des Hauptfensters: Bearbeiten der Funde (Klicks, Kontextmenüs, Markierungen, Bereiche, freie Bearbeitung).

``EditMixin`` wird nur von ``MainWindow`` geerbt und nutzt dessen Attribute (``session``, ``settings``,
``runner``, Ansichten …)."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QMessageBox

from ..core.entities import AREA_KEYS, all_keys, info

QUICK_TYPES = ["PERSON", "LOCATION", "DE_ADDRESS", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
               "DATE_TIME", "IBAN_CODE", "CUSTOM"]


class EditMixin:
    # ================================================================== Interaktion
    def _on_left_text_click(self, offset: int, _pos: QPoint) -> None:
        if not self.session:
            return
        hits = self.session.findings_at(offset)
        if hits:
            self.select([h.id for h in hits], reveal=False)
            self._reveal_partner(hits[0])

    def _reveal_partner(self, f) -> None:
        self._syncing = True
        if self.session.manual_text is None:
            r = self.session.redacted().to_redacted(f.start)
            self.right.text.reveal(r, r)
        self._syncing = False

    def _on_right_text_click(self, offset: int, _pos: QPoint) -> None:
        if not self.session or self.session.manual_text is not None:
            return
        ids = self._right_ids_at(offset)
        if ids:
            self.select(ids, reveal=False)
            f = self.session.get(ids[0])
            self._syncing = True
            self.left.text.reveal(f.start, f.end)
            self._syncing = False

    def _right_ids_at(self, offset: int) -> list[int]:
        """Funde an einer Stelle der bearbeiteten Fassung: Platzhalter oder abgewählter Fund."""
        red = self.session.redacted()
        seg = red.segment_at_redacted(offset)
        if seg:
            return list(seg.finding_ids)
        orig = red.to_original(offset)
        return [f.id for f in self.session.findings_at(orig) if not f.active]

    def _on_left_text_context(self, offset: int, pos: QPoint) -> None:
        if not self.session:
            return
        ids = [f.id for f in self.session.findings_at(offset)]
        sel = self.left.text.selection_range()
        self._finding_menu(ids, pos, [sel] if sel else None)

    def _on_right_text_context(self, offset: int, pos: QPoint) -> None:
        if not self.session or self.session.manual_text is not None:
            return
        ids = self._right_ids_at(offset)
        sel = self._right_selection()
        if ids or sel:
            self._finding_menu(ids, pos, [sel] if sel else None)

    def _right_selection(self) -> tuple[int, int] | None:
        """Markierung in der bearbeiteten Fassung → Bereich im Original."""
        if not self.session or self.session.manual_text is not None:
            return None
        r = self.right.text.selection_range()
        if not r:
            return None
        red = self.session.redacted()
        start, end = red.to_original(r[0]), red.to_original_end(r[1])
        return (start, end) if end > start else None

    def _ids_tooltip(self, ids: list[int]) -> str:
        if not self.session or not ids:
            return ""
        lines = []
        for fid in ids:
            f = self.session.get(fid)
            if f:
                state = "wird geschwärzt" if f.active else "wird NICHT geschwärzt"
                if f.is_area:
                    lines.append(f"{info(f.entity_type).label} – frei gezogener Bereich  ({state})")
                    continue
                ocr = "  – per Texterkennung gelesen, bitte prüfen" if self.session.doc.is_ocr(f.start, f.end) else ""
                lines.append(f"{info(f.entity_type).label}: {f.text}  ({state}){ocr}")
                if f.source == "ki":
                    lines.append(f"  Vorschlag der {f.recognizer}" if f.recognizer else "  Vorschlag der KI-Nachprüfung")
        return "\n".join(lines)

    def _redacted_tooltip(self, offset: int) -> str:
        if not self.session or self.session.manual_text is not None:
            return ""
        return self._ids_tooltip(self._right_ids_at(offset))

    def _on_area_selected(self, page: int, rect, pos: QPoint) -> None:
        """Rechteck auf einer PDF-Seite aufgezogen → enthaltene Zeichen als Bereiche anbieten."""
        doc = self.session.doc if self.session else None
        if not doc or not doc.char_boxes:
            return
        ranges: list[list[int]] = []
        last = None
        for i, box in enumerate(doc.char_boxes):
            if box is None or box.page != page:
                continue
            x0, y0, x1, y1 = box.rect
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
                if last is not None and all(
                    doc.char_boxes[j] is None or doc.text[j].isspace() for j in range(last + 1, i)
                ) and ranges:
                    ranges[-1][1] = i + 1
                else:
                    ranges.append([i, i + 1])
                last = i
        self._area_menu(page, rect, pos, [tuple(r) for r in ranges])

    def _area_menu(self, page: int, rect, pos: QPoint, ranges: list[tuple[int, int]]) -> None:
        """Menü nach dem Aufziehen eines Rahmens: Text darin schwärzen und/oder den ganzen Bereich
        (auch Bilder, Unterschriften, Handschrift) als freien Bereich schwärzen."""
        self.build_area_menu(page, rect, ranges).exec(pos)

    def build_area_menu(self, page: int, rect, ranges: list[tuple[int, int]]) -> QMenu:
        s = self.session
        menu = QMenu(self)
        area = (rect.x0, rect.y0, rect.x1, rect.y1)

        def add_area(t: str, with_text: bool) -> None:
            added = s.add_area(page, area, t, ranges if with_text else None)
            self.select([f.id for f in added[:1]])
            self.status_label.setText(f"Bereich auf Seite {page + 1} wird geschwärzt ({info(t).label}).")

        if ranges:
            value = " ".join(s.doc.text[a:b] for a, b in ranges).strip()
            short = value if len(value) <= 40 else value[:37] + "…"
            menu.addSection(f"Text im Rahmen: {short}")
            self._add_type_menu(menu, "Text schwärzen als", lambda t: self._add_ranges(ranges, t, False))
            self._add_type_menu(menu, "Alle Vorkommen schwärzen als", lambda t: self._add_ranges(ranges, t, True))
            menu.addSection("Ganzer Bereich (auch Bilder und Grafik)")
            sub = menu.addMenu("Ganzen Bereich schwärzen als")
        else:
            menu.addSection("Kein Text im Rahmen – z. B. Unterschrift, Foto, Handschrift")
            sub = menu.addMenu("Bereich schwärzen als")
        for key in AREA_KEYS:
            sub.addAction(info(key).label, lambda k=key: add_area(k, True))
        return menu

    def _mark_selection(self, all_occurrences: bool) -> None:
        if not self.session:
            return
        view = self.left.text
        sel = self.left.text.selection_range() if self.left.isVisible() else None
        if not sel:
            sel = self._right_selection()
            view = self.right.text
        if not sel:
            QMessageBox.information(self, "Markierung schwärzen",
                                    "Bitte zuerst im Reiter „Text“ eine Textstelle markieren – "
                                    "oder in der Seitenansicht einen Bereich aufziehen.")
            return
        rect = view.cursorRect()
        pos = view.viewport().mapToGlobal(rect.bottomRight())
        menu = QMenu(self)
        self._add_type_menu(menu, "Schwärzen als", lambda t: self._add_ranges([sel], t, all_occurrences))
        menu.exec(pos)

    def _add_ranges(self, ranges, entity_type: str, all_occurrences: bool) -> None:
        s = self.session
        if all_occurrences:
            value = " ".join(s.doc.text[a:b] for a, b in ranges).strip()
            added = s.add_all_occurrences(value, entity_type)
        else:
            added = s.add_manual(list(ranges), entity_type)
        if added:
            self.select([f.id for f in added])
            self.status_label.setText(f"{len(added)} Stelle(n) hinzugefügt.")

    def _add_type_menu(self, menu: QMenu, title: str, handler) -> QMenu:
        sub = menu.addMenu(title)
        for key in QUICK_TYPES:
            sub.addAction(info(key).label, lambda k=key: handler(k))
        more = sub.addMenu("Weitere")
        for key in all_keys():
            if key not in QUICK_TYPES:
                more.addAction(info(key).label, lambda k=key: handler(k))
        return sub

    def _finding_menu(self, ids: list[int], pos: QPoint, ranges=None) -> None:
        menu = self.build_finding_menu(ids, ranges)
        if menu is not None:
            menu.exec(pos)

    def build_finding_menu(self, ids: list[int], ranges=None) -> QMenu | None:
        """Kontextmenü zu Funden und/oder einer Markierung (``None``, wenn es nichts anzubieten gibt)."""
        s = self.session
        if not s:
            return None
        menu = QMenu(self)
        findings = [s.get(i) for i in ids if s.get(i)]
        if findings and all(x.is_area for x in findings):
            all_active = all(x.active for x in findings)
            f = findings[0]
            menu.addSection(f"{info(f.entity_type).label} – Bereich auf Seite {int(f.area[0]) + 1}")
            menu.addAction("Nicht schwärzen" if all_active else "Schwärzen", lambda: s.set_active(ids, not all_active))
            sub = menu.addMenu("Typ ändern")
            for key in AREA_KEYS:
                sub.addAction(info(key).label, lambda k=key: s.set_type(ids, k))
            menu.addSeparator()
            menu.addAction("Bereich löschen", lambda: s.remove(ids))
            findings = []
        findings = [x for x in findings if not x.is_area]
        if findings:
            ids = [x.id for x in findings]
            f = findings[0]
            all_active = all(x.active for x in findings)
            short = f.text if len(f.text) <= 40 else f.text[:37] + "…"
            menu.addSection(f"{info(f.entity_type).label}: {short}")
            menu.addAction("Nicht schwärzen" if all_active else "Schwärzen",
                           lambda: s.set_active(ids, not all_active))
            self._add_type_menu(menu, "Typ ändern", lambda t: s.set_type(ids, t))
            same = s.same_text_ids(f.id)
            if len(same) > 1:
                menu.addAction(f"Alle {len(same)} Vorkommen schwärzen", lambda: s.set_active(same, True))
                menu.addAction(f"Alle {len(same)} Vorkommen nicht schwärzen", lambda: s.set_active(same, False))
            menu.addSeparator()
            menu.addAction("Immer ignorieren (Ausnahmeliste)", lambda: self._to_allow_list(f.text, same))
            menu.addAction("Fund löschen", lambda: s.remove(ids))
        if ranges:
            value = " ".join(s.doc.text[a:b] for a, b in ranges).strip()
            short = value if len(value) <= 40 else value[:37] + "…"
            menu.addSection(f"Markierung: {short}")
            self._add_type_menu(menu, "Schwärzen als", lambda t: self._add_ranges(ranges, t, False))
            self._add_type_menu(menu, "Alle Vorkommen schwärzen als", lambda t: self._add_ranges(ranges, t, True))
            menu.addAction("Immer schwärzen (Sperrliste)", lambda: self._to_deny_list(value))
            menu.addSeparator()
            menu.addAction("Kopieren", lambda: QGuiApplication.clipboard().setText(value))
        return None if menu.isEmpty() else menu

    def _to_allow_list(self, value: str, ids: list[int]) -> None:
        value = value.strip()
        if value and value.casefold() not in {v.casefold() for v in self.settings.allow_list}:
            self.settings.allow_list.append(value)
            self._save_settings()
        self.session.set_active(ids, False)
        self.status_label.setText(f"„{value}“ wird künftig nie geschwärzt (Einstellungen → Ausnahmen).")

    def _to_deny_list(self, value: str) -> None:
        if value and value.casefold() not in {v.casefold() for v in self.settings.deny_list}:
            self.settings.deny_list.append(value)
            self._save_settings()
        self.session.add_all_occurrences(value, "CUSTOM")
        self.status_label.setText(f"„{value}“ wird künftig immer geschwärzt (Einstellungen → Sperrliste).")

    def _set_all(self, active: bool) -> None:
        if self.session:
            self.session.set_active([f.id for f in self.session.findings], active)

    def undo(self) -> None:
        if self.session:
            self.session.undo()

    def redo(self) -> None:
        if self.session:
            self.session.redo()

    # ================================================================== Freie Bearbeitung
    def _toggle_free_edit(self, on: bool) -> None:
        s = self.session
        if not s:
            return
        if on:
            self.right.tabs.setCurrentWidget(self.right.text)
            s.set_manual_text(s.redacted().text)
            self.right.text.set_marks([])
            self.right.text.setReadOnly(False)
            self.right.title.setText("<b>Bearbeitet</b> – <span style='color:#e8590c'>freie Bearbeitung aktiv</span>")
            self.right.title.setToolTip("Änderungen an Funden werden erst nach Beenden der freien Bearbeitung "
                                        "angezeigt. Beenden verwirft die Handänderungen. Wirkt nur auf den Text-Export.")
        else:
            if s.manual_text is not None and s.manual_text != s.redacted().text:
                r = QMessageBox.question(self, "Freie Bearbeitung beenden",
                                         "Handänderungen am anonymisierten Text verwerfen?")
                if r != QMessageBox.StandardButton.Yes:
                    self.edit_btn.blockSignals(True)
                    self.edit_btn.setChecked(True)
                    self.edit_btn.blockSignals(False)
                    return
            self.right.text.setReadOnly(True)
            s.set_manual_text(None)
            self.right.title.setText("<b>Bearbeitet (Vorschau)</b>")
            self.right.title.setToolTip("")
            self.right.text.invalidate()
            self._refresh_views()

    def _on_free_text_changed(self) -> None:
        if self.session and self.session.manual_text is not None and not self.right.text.isReadOnly():
            self.session.manual_text = self.right.text.toPlainText()
            self.session.dirty = True
            self._update_title()
