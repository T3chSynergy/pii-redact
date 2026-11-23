"""Teil des Hauptfensters: Ordner-Bearbeitung (Arbeitsliste, Prüfen und weiter, automatischer Export).

``BatchMixin`` wird nur von ``MainWindow`` geerbt und nutzt dessen Attribute (``session``, ``settings``,
``runner``, Ansichten …)."""

from __future__ import annotations

import html
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from ..core import UnsupportedFileError
from ..core.batch import Status, Workspace, export_document, held_for_check
from . import dialogs
from .batch_panel import NewBatchDialog, confirm_auto_export, status_style
from .safe_text import plain_text
from .window_export import ELSEWHERE

OCR_NOT_AUTO = "Texterkennung (OCR) – wird nicht automatisch exportiert, bitte einzeln prüfen"


class BatchMixin:
    def _init_batch(self) -> None:
        self.batch: Workspace | None = None
        self.batch_id = 0
        self.batch_rel: str | None = None
        self._awaiting_rel: str | None = None     # geöffnete Datei, deren Analyse noch läuft
        self._batch_loading = False
        self._auto_export = False                  # nach „Ungeprüfte exportieren“: Rest nach Analyse exportieren
        self._model_error_shown = False

    # ================================================================== Ordner-Bearbeitung
    def open_folder_dialog(self, source: str = "") -> None:
        if self.runner.busy:
            QMessageBox.information(self, "Bitte warten", "Die laufende Analyse ist noch nicht fertig.")
            return
        dlg = NewBatchDialog(self, source)
        if not dlg.exec():
            return
        src, tgt, recursive = dlg.values()
        try:
            if Workspace.exists_in(tgt):
                # erst als Text vergleichen – der Pfad im Arbeitsstand wird nicht angefasst, bevor er passt
                stored, _recursive = Workspace.stored_source(tgt)
                if not _same_path(stored, str(src.resolve())):
                    QMessageBox.warning(
                        self, "Anderer Arbeitsstand",
                        plain_text(f"Im Zielordner liegt bereits ein Arbeitsstand für den Quellordner\n{stored}\n\n"
                                   "Bitte einen anderen Zielordner wählen."))
                    return
                ws = Workspace.load(tgt)
            else:
                ws = Workspace.create(src, tgt, recursive)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Ordner kann nicht bearbeitet werden", plain_text(str(exc)))
            return
        self._activate_batch(ws)

    def resume_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Zielordner mit Arbeitsstand wählen")
        if d:
            self.resume_folder(Path(d))

    def resume_folder(self, target: Path) -> None:
        if not Workspace.exists_in(target):
            QMessageBox.information(self, "Kein Arbeitsstand",
                                    plain_text(f"In {target} wurde kein pii-redact-Arbeitsstand gefunden."))
            self._forget_recent(target)
            return
        try:
            stored, recursive = Workspace.stored_source(target)
            # Der Arbeitsstand liegt im Zielordner und kann von anderen geändert worden sein: Einen Quellordner,
            # den diese Person hier nicht selbst angelegt oder bestätigt hat, erst zeigen – vor jedem Zugriff
            if not self._source_confirmed(target, stored, recursive) and not self._ask_source(target, stored,
                                                                                               recursive):
                return
            ws = Workspace.load(target)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Arbeitsstand kann nicht geladen werden", plain_text(str(exc)))
            return
        self._activate_batch(ws)

    # ------------------------------------------------------------------ Quellordner bestätigen
    def _source_confirmed(self, target: Path, source: str, recursive: bool) -> bool:
        known = self.settings.confirmed_sources.get(_target_key(target))
        return (isinstance(known, dict) and isinstance(known.get("source"), str)
                and _same_path(known["source"], source) and known.get("recursive") is recursive)

    def _ask_source(self, target: Path, source: str, recursive: bool) -> bool:
        return dialogs.ask_yes_no(
            self, "Ordner-Arbeit fortsetzen",
            f"Der Arbeitsstand in <b>{html.escape(str(target))}</b> gehört zum Quellordner<br>"
            f"<b>{html.escape(source)}</b> ({'mit' if recursive else 'ohne'} Unterordner).<br><br>"
            "Dieser Ordner wird jetzt durchsucht, seine Dateien werden analysiert und die Ergebnisse in den "
            "Zielordner geschrieben. Ist das der richtige Quellordner?<br><span style='color:gray'>Die Frage "
            "erscheint, wenn Sie diese Ordner-Arbeit auf diesem Rechner nicht selbst begonnen oder bestätigt haben "
            "oder der Arbeitsstand inzwischen auf einen anderen Quellordner zeigt.</span>",
            yes="Fortsetzen", no="Abbrechen")

    def _remember_source(self, ws: Workspace) -> None:
        """Quellordner einer angelegten oder bestätigten Ordner-Arbeit merken (höchstens 50 Zielordner)."""
        sources = {k: v for k, v in self.settings.confirmed_sources.items() if k != _target_key(ws.target)}
        sources[_target_key(ws.target)] = {"source": str(ws.source), "recursive": ws.recursive}
        self.settings.confirmed_sources = dict(list(sources.items())[-50:])

    def _activate_batch(self, ws: Workspace) -> None:
        if self.batch:
            self.close_batch(show_welcome=False)
        elif not self._confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            todo = ws.sync_with_disk()
            ws.save()
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Ordner kann nicht bearbeitet werden", plain_text(str(exc)))
            return
        QApplication.restoreOverrideCursor()
        self.batch = ws
        self.batch_id += 1
        self.batch_rel = None
        self._awaiting_rel = None
        self._auto_export = False
        self._model_error_shown = False
        self._remember_source(ws)
        self._remember_recent(ws.target)          # speichert die Einstellungen
        self.batch_panel.load(ws)
        self.batch_dock.show()
        self.review_bar.show()
        for rel in todo:
            self.runner.enqueue_file(ws.abs(rel), rel, self.settings, self.batch_id)
        first = ws.next_open() or (ws.ordered()[0].rel if ws.entries else None)
        if first:
            self._open_batch_file(first)
        c = ws.counts()
        done = c.get(Status.REVIEWED, 0) + c.get(Status.AUTO, 0)
        self.status_label.setText(
            f"Ordner-Arbeit: {len(ws.entries)} Dateien, {done} erledigt, {len(todo)} werden analysiert."
            + (f" {c[Status.MISSING]} Datei(en) fehlen im Quellordner." if c.get(Status.MISSING) else "")
        )
        self._update_actions()
        self._refresh_batch()

    def close_batch(self, show_welcome: bool = True) -> None:
        if not self.batch:
            return
        self._store_batch_session()
        self._save_batch()
        self.runner.cancel_batch(self.batch_id)
        self.batch = None
        self.batch_rel = None
        self._awaiting_rel = None
        self._auto_export = False
        self.batch_dock.hide()
        self.review_bar.hide()
        self.progress.hide()
        if show_welcome:
            self.session = None
            self.findings.set_session(None)
            self.stack.setCurrentIndex(0)
            self.status_label.setText("Ordner-Arbeit geschlossen – der Stand ist gespeichert.")
        self._update_actions()
        self._update_title()

    def _store_batch_session(self) -> None:
        if (self.batch and self.batch_rel and self.session and self._awaiting_rel != self.batch_rel
                and self.batch_rel in self.batch.entries):
            self.batch.update_findings(self.batch_rel, self.session.findings)

    def _save_batch(self) -> None:
        if not self.batch:
            return
        try:
            self.batch.save()
        except OSError as exc:
            self.status_label.setText(f"Arbeitsstand konnte nicht gespeichert werden: {exc}")

    def _open_batch_file(self, rel: str) -> None:
        if not self.batch or rel not in self.batch.entries:
            return
        if rel == self.batch_rel and self.session is not None:
            return
        if self.runner.busy:
            self.status_label.setText("Bitte warten, bis die laufende Analyse fertig ist.")
            return
        self._store_batch_session()
        self._batch_save_timer.start()
        e = self.batch.entries[rel]
        if e.status == Status.MISSING:
            self.status_label.setText(f"{rel}: Datei fehlt im Quellordner.")
            return
        try:
            doc = self._load(self.batch.abs(rel))
        except (UnsupportedFileError, OSError, RuntimeError) as exc:
            self.batch.set_error(rel, str(exc))
            self._batch_save_timer.start()
            self._refresh_batch()
            self.status_label.setText(f"{rel}: {exc}")
            return
        self.batch_rel = rel
        self._batch_loading = True
        try:
            self._show_document(doc)
            restored = self.batch.restore_findings(rel, doc) if e.analyzed else None
            if restored is not None:
                self._awaiting_rel = None
                self.session.set_findings(restored)
                self.progress.hide()
            else:
                self._awaiting_rel = rel
                if not self.runner.prioritize(rel, self.batch_id):
                    self.runner.enqueue_file(self.batch.abs(rel), rel, self.settings, self.batch_id, front=True)
                self.progress.setRange(0, 0)
                self.progress.show()
                self.status_label.setText(f"{rel} wird analysiert …")
            self.session.dirty = False
        finally:
            self._batch_loading = False
        self._update_actions()
        self._refresh_batch()

    def _on_file_finished(self, rel: str, batch_id: int, result) -> None:
        if not self.batch or batch_id != self.batch_id or rel not in self.batch.entries:
            return
        findings = result["findings"]
        self.batch.set_analysis_result(rel, result)
        if rel == self._awaiting_rel and rel == self.batch_rel and self.session is not None:
            self._batch_loading = True
            try:
                self.session.set_findings(findings)
                self.session.dirty = False
            finally:
                self._batch_loading = False
            self._awaiting_rel = None
            self.progress.hide()
            self.status_label.setText(f"Analyse fertig: {len(findings)} Funde.")
        entry = self.batch.entries[rel]
        if self._auto_export and entry.status == Status.TO_REVIEW and entry.has_ocr:
            entry.note = OCR_NOT_AUTO
        elif self._auto_export and entry.status == Status.TO_REVIEW:
            try:
                self._export_auto(rel, findings)
            except Exception as exc:  # noqa: BLE001
                self.batch.set_error(rel, f"Export fehlgeschlagen: {exc}")
        if self._auto_export and not self.runner.pending_files(self.batch_id):
            self._auto_export = False
            self.status_label.setText("Automatischer Export abgeschlossen.")
        self._batch_save_timer.start()
        self._update_actions()
        self._refresh_batch()

    def _on_file_failed(self, rel: str, batch_id: int, message: str) -> None:
        if not self.batch or batch_id != self.batch_id or rel not in self.batch.entries:
            return
        self.batch.set_error(rel, message)
        if rel == self._awaiting_rel:
            self._awaiting_rel = None
            self.progress.hide()
            self.status_label.setText(f"{rel}: Analyse fehlgeschlagen.")
        if ("Sprachmodell" in message or "Transformer-Modell" in message) and not self._model_error_shown:
            self._model_error_shown = True
            self.runner.cancel_batch(self.batch_id)
            QMessageBox.critical(self, "Analyse nicht möglich", plain_text(message))
        self._batch_save_timer.start()
        self._update_actions()
        self._refresh_batch()

    def _export_entry(self, rel: str, reviewed: bool, findings, doc=None) -> list[str]:
        doc = doc or self._load(self.batch.abs(rel))
        out = self.batch.out(rel)
        leftovers = self._run_busy("Schwärze und kontrolliere …",
                                   lambda _p: export_document(doc, findings, self.settings, out))
        self.batch.mark_done(rel, reviewed=reviewed, exported_as=out, leftovers=leftovers, findings=findings)
        return leftovers

    def _export_auto(self, rel: str, findings, doc=None) -> bool:
        """Export ohne Prüfung. Steht ein geschwärzter Text noch an anderer Stelle, bleibt die Datei stattdessen
        zur Prüfung stehen (Vorschläge der Kontrolle in der Fundliste). Rückgabe: exportiert?"""
        doc = doc or self._load(self.batch.abs(rel))
        held = held_for_check(doc.text, findings)
        if held is None:
            self._export_entry(rel, False, findings, doc)
            return True
        if rel == self.batch_rel and self.session is not None and self.session.doc is doc:
            known = {f.id for f in findings}
            self.session.add_findings([f for f in held if f.id not in known])
        self.batch.hold_for_check(rel, held)
        return False

    def confirm_and_next(self) -> None:
        if not (self.batch and self.batch_rel and self.session):
            return
        if self._awaiting_rel == self.batch_rel:
            self.status_label.setText("Die Analyse dieser Datei läuft noch.")
            return
        if self.runner.busy:
            return
        rel = self.batch_rel
        if not self._confirm_ocr(self.session.doc):
            return
        accepted = self._confirm_further()
        if accepted is None:
            self._store_batch_session()   # Vorschläge der Kontrolle im Arbeitsstand festhalten
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            leftovers = self._export_entry(rel, True, self.session.findings, self.session.doc)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export fehlgeschlagen", plain_text(f"{rel}:\n{exc}"))
            return
        QApplication.restoreOverrideCursor()
        self.session.dirty = False
        self._save_batch()
        if accepted:   # vor dem Speichern gezeigt und bewusst belassen – nicht erneut melden
            leftovers = [x for x in leftovers if ELSEWHERE not in x]
        if leftovers:
            QMessageBox.warning(
                self, "Prüfung: Reste gefunden",
                plain_text(f"{rel} wurde exportiert, aber die Kontrolle hat im Ergebnis noch Folgendes gefunden:\n\n• "
                           + "\n• ".join(leftovers[:20]) + "\n\nDer Hinweis steht im Protokoll."))
        nxt = self.batch.next_open(rel)
        if nxt:
            self._open_batch_file(nxt)
            self.status_label.setText(f"{rel} geprüft und exportiert.")
        else:
            self._refresh_batch()
            c = self.batch.counts()
            QMessageBox.information(
                self, "Ordner bearbeitet",
                f"Alle Dateien sind bearbeitet.\n\n{c.get(Status.REVIEWED, 0)} geprüft, "
                f"{c.get(Status.AUTO, 0)} automatisch, {c.get(Status.ERROR, 0)} mit Fehler.\n\n"
                f"Ergebnisse und Protokoll:\n{self.batch.target}")

    def _step_file(self, delta: int) -> None:
        if not self.batch:
            return
        order = [e.rel for e in self.batch.ordered()]
        if not order:
            return
        idx = order.index(self.batch_rel) if self.batch_rel in order else -1
        new = idx + delta
        if 0 <= new < len(order):
            self._open_batch_file(order[new])

    def export_unreviewed(self) -> None:
        if not self.batch:
            return
        self._store_batch_session()
        entries = self.batch.ordered()
        ready = [e for e in entries if e.status == Status.TO_REVIEW and not e.has_ocr]
        ocr_skipped = [e for e in entries if e.status == Status.TO_REVIEW and e.has_ocr]
        waiting = [e for e in entries if e.status == Status.WAITING]
        if not ready and not waiting:
            if ocr_skipped:
                QMessageBox.information(
                    self, "Ungeprüfte Dateien exportieren",
                    f"{len(ocr_skipped)} Datei(en) enthalten per Texterkennung gelesene Seiten. Sie werden nicht "
                    "automatisch exportiert und müssen einzeln geprüft werden.")
            return
        if not confirm_auto_export(self, len(ready) + len(waiting), len(waiting), len(ocr_skipped)):
            return
        for e in ocr_skipped:
            e.note = OCR_NOT_AUTO
        dlg = QProgressDialog("Exportiere …", "Abbrechen", 0, len(ready), self)
        dlg.setWindowTitle("Ungeprüfte Dateien exportieren")
        dlg.setMinimumDuration(300)
        failed = requeued = held = 0
        for i, e in enumerate(ready):
            if dlg.wasCanceled():
                break
            dlg.setLabelText(e.rel)
            dlg.setValue(i)
            QApplication.processEvents()
            try:
                outcome = self._export_unreviewed_file(e)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.batch.set_error(e.rel, f"Export fehlgeschlagen: {exc}")
                continue
            if outcome is None:
                waiting.append(e)
                requeued += 1
            else:
                held += not outcome
        dlg.setValue(len(ready))
        if waiting and not dlg.wasCanceled():
            self._auto_export = True
        self._save_batch()
        self._refresh_batch()
        self.status_label.setText(
            f"{len(ready) - failed - requeued - held} Datei(en) automatisch exportiert"
            + (f", {len(waiting)} folgen nach der Analyse" if self._auto_export else "")
            + (f", {failed} Fehler" if failed else "")
            + (f", {len(ocr_skipped)} mit Texterkennung bleiben zur Prüfung stehen" if ocr_skipped else "")
            + (f", {held} hält die Kontrolle zur Prüfung zurück (geschwärzter Text steht noch an anderer Stelle)"
               if held else "") + ".")

    def _export_unreviewed_file(self, e) -> bool | None:
        """Eine ungeprüfte Datei automatisch exportieren. True = exportiert, False = von der Kontrolle
        zurückgehalten, None = der Text passt nicht mehr zum Arbeitsstand: neu analysieren, danach exportieren."""
        current = e.rel == self.batch_rel and self.session is not None
        doc = self.session.doc if current else self._load(self.batch.abs(e.rel))
        if doc.ocr_pages:   # der Arbeitsstand kann verändert sein – entscheidend ist das geladene Dokument
            e.ocr_pages = [p + 1 for p in doc.ocr_pages]
            e.note = OCR_NOT_AUTO
            return False
        if current:
            return self._export_auto(e.rel, self.session.findings, doc)
        findings = self.batch.restore_findings(e.rel, doc)
        if findings is None:
            self.runner.enqueue_file(self.batch.abs(e.rel), e.rel, self.settings, self.batch_id)
            return None
        return self._export_auto(e.rel, findings, doc)

    def _requeue_batch(self) -> None:
        """Nach Änderung von Modus/Einstellungen: noch nicht analysierte Dateien mit neuen Einstellungen."""
        if not self.batch:
            return
        self.runner.cancel_batch(self.batch_id)
        running = self.runner.current_file
        for e in self.batch.ordered():
            if e.status == Status.WAITING and e.rel != running:
                self.runner.enqueue_file(self.batch.abs(e.rel), e.rel, self.settings, self.batch_id,
                                         front=e.rel == self._awaiting_rel)

    def _refresh_batch(self) -> None:
        if not self.batch:
            return
        pending = set(self.runner.pending_files(self.batch_id))
        self.batch_panel.refresh(self.batch, self.batch_rel, self.runner.current_file, pending)
        if not self.batch_rel or self.batch_rel not in self.batch.entries:
            return
        order = [e.rel for e in self.batch.ordered()]
        e = self.batch.entries[self.batch_rel]
        sym, text, color = status_style(e.status)
        if self._awaiting_rel == self.batch_rel:
            sym, text, color = "◌", "wird analysiert …", "#868e96"
        elif e.status == Status.REVIEWED and e.reviewed_by:
            text = f"geprüft von {e.reviewed_by}, {e.reviewed_at[:16].replace('T', ' ')}"
        elif e.note:
            text = f"{text} – {e.note}"
        can = (self.session is not None and self._awaiting_rel != self.batch_rel
               and e.status not in (Status.MISSING, Status.ERROR) and not self.runner.busy)
        self.review_bar.set_file(order.index(self.batch_rel) + 1, len(order), self.batch_rel,
                                 f"{sym} {text}", color, can)
        self.review_bar.btn_prev.setEnabled(order.index(self.batch_rel) > 0)
        self.review_bar.btn_next.setEnabled(order.index(self.batch_rel) < len(order) - 1)

    # ================================================================== Zuletzt bearbeitet
    def _remember_recent(self, target: Path) -> None:
        t = str(target)
        lst = [x for x in self.settings.recent_batches if x != t]
        self.settings.recent_batches = [t] + lst[:7]
        self._save_settings()

    def _forget_recent(self, target: Path) -> None:
        self.settings.recent_batches = [x for x in self.settings.recent_batches if x != str(target)]
        self._save_settings()

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        if not self.settings.recent_batches:
            act = self.recent_menu.addAction("(noch keine)")
            act.setEnabled(False)
            return
        for t in self.settings.recent_batches:
            self.recent_menu.addAction(t, lambda t=t: self.resume_folder(Path(t)))


def _target_key(target: Path) -> str:
    """Schlüssel eines Zielordners in ``confirmed_sources`` (den Zielordner hat diese Person selbst gewählt)."""
    return os.path.normcase(str(Path(target).resolve()))


def _same_path(a: str, b: str) -> bool:
    """Gleicher Pfad als Text (unter Windows ohne Unterschied der Groß-/Kleinschreibung) – ohne Zugriff."""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
