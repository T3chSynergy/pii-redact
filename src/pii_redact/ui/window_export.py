"""Teil des Hauptfensters: Export als Text oder geschwärztes PDF (Einzeldatei).

``ExportMixin`` wird nur von ``MainWindow`` geerbt und nutzt dessen Attribute (``session``, ``settings``,
``runner``, Ansichten …)."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ..core import save_redacted_pdf


class ExportMixin:
    # ------------------------------------------------------------------ Export
    def _suggest(self, suffix: str, tag: str) -> str:
        p = self.session.doc.path
        return str(p.with_name(f"{p.stem}_{tag}{suffix}"))

    def _export_default(self) -> None:
        if self.session and self.session.doc.is_pdf:
            self.export_pdf()
        else:
            self.export_text()

    def export_text(self) -> None:
        if not self.session:
            return
        doc = self.session.doc
        if not self._confirm_ocr(doc):
            return
        suffix = ".md" if doc.kind == "md" else ".txt"
        path, _ = QFileDialog.getSaveFileName(self, "Anonymisierten Text speichern", self._suggest(suffix, "anonymisiert"),
                                              "Markdown (*.md);;Text (*.txt);;Alle Dateien (*)")
        if not path:
            return
        if Path(path).resolve() == doc.path.resolve():
            QMessageBox.warning(self, "Export", "Das Original darf nicht überschrieben werden.")
            return
        Path(path).write_text(self.session.output_text(), encoding="utf-8")
        self.session.dirty = False
        self._update_title()
        self._show_saved(Path(path), "Text gespeichert")

    def export_pdf(self) -> None:
        if not self.session:
            return
        doc = self.session.doc
        if not doc.is_pdf:
            QMessageBox.information(self, "Export", "PDF-Export ist nur für PDF-Dokumente möglich.")
            return
        if not self._confirm_ocr(doc):
            return
        if self.session.manual_text is not None:
            QMessageBox.information(
                self, "Hinweis",
                "Die freie Textbearbeitung wirkt nur auf den Text-Export. Das PDF wird aus den Funden erzeugt."
            )
        path, _ = QFileDialog.getSaveFileName(self, "Geschwärztes PDF speichern", self._suggest(".pdf", "geschwaerzt"), "PDF (*.pdf)")
        if not path:
            return
        if Path(path).resolve() == doc.path.resolve():
            QMessageBox.warning(self, "Export", "Das Original darf nicht überschrieben werden.")
            return
        findings = self.session.findings

        def work(_progress):
            return save_redacted_pdf(doc, findings, self.settings, Path(path))

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            leftovers = self._run_busy("Schwärze und kontrolliere …", work)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.session.dirty = False
        self._update_title()
        if leftovers:
            QMessageBox.warning(
                self, "Prüfung: Reste gefunden",
                "Das PDF wurde gespeichert, aber die Kontrolle hat darin noch Folgendes gefunden "
                "(z. B. doppelt im PDF enthaltener Text):\n\n• " + "\n• ".join(leftovers[:20]),
            )
        else:
            # Erfolg: keine Rückfrage, nur eine Meldung in der Statusleiste (Details im Tooltip)
            n = sum(1 for f in self.session.findings if f.active)
            self._show_saved(
                Path(path), f"{n} {'Stelle' if n == 1 else 'Stellen'} geschwärzt, Kontrolle ohne Reste",
                f"Gespeichert: {path}\n\n{n} Stellen wurden physisch aus dem PDF entfernt. Die Kontrolle hat "
                "keine Reste gefunden. Kommentare, Lesezeichen, Metadaten, Links und Anhänge wurden ebenfalls "
                "entfernt." + ("\n\nHinweise: " + " ".join(doc.warnings) if doc.warnings else ""))

    def _confirm_ocr(self, doc) -> bool:
        """Vor dem Export eines Dokuments mit OCR-Seiten ausdrücklich die gründliche Prüfung bestätigen lassen."""
        if not doc or not doc.has_ocr:
            return True
        n = len(doc.ocr_pages)
        pages = ", ".join(str(p + 1) for p in doc.ocr_pages)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Texterkennung – geprüft?")
        box.setText(f"<b>{'Seite' if n == 1 else 'Seiten'} {pages} {'wurde' if n == 1 else 'wurden'} "
                    "per Texterkennung gelesen.</b>")
        box.setInformativeText("Falsch gelesene Namen oder Nummern werden dort nicht erkannt. "
                               "Haben Sie diese Seiten vollständig geprüft?")
        yes = box.addButton("Ja, vollständig geprüft", QMessageBox.ButtonRole.AcceptRole)
        back = box.addButton("Zurück zur Prüfung", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
        box.exec()
        return box.clickedButton() is yes

    def _show_saved(self, path: Path, what: str, details: str = "") -> None:
        """Erfolgsmeldung in der Statusleiste mit Link zum Zielordner (statt eines Dialogs)."""
        self._saved_path = path
        self.status_label.setText(f"✓ {html.escape(path.name)} – {html.escape(what)} · "
                                  "<a href='open-folder'>Ordner öffnen</a>")
        self.status_label.setToolTip(details or f"Gespeichert: {path}")

    def _on_status_link(self, link: str) -> None:
        if link == "open-folder" and getattr(self, "_saved_path", None):
            self._open_path(self._saved_path.parent)
