"""Teil des Hauptfensters: KI-Nachprüfung des geschwärzten Ergebnisses.

``KiMixin`` wird nur von ``MainWindow`` geerbt und nutzt dessen Attribute (``session``, ``settings``,
``runner``, Ansichten …)."""

from __future__ import annotations

import html
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from ..core import llm_review


class _KiBridge(QObject):
    """Meldet Ergebnisse der KI-Nachprüfung aus dem Hintergrund-Thread an die Oberfläche."""
    done = Signal(object, object)      # Sitzung, ReviewResult
    failed = Signal(object, str)       # Sitzung, Fehlermeldung
    progress = Signal(int, int)


class KiMixin:
    def _init_ki(self) -> None:
        self._ki_session = None                    # Sitzung, deren KI-Nachprüfung gerade läuft
        self._ki_confirmed = False                 # Senden an den Server in dieser Sitzung bestätigt
        self._ki_bridge = _KiBridge(self)
        self._ki_bridge.done.connect(self._on_ki_done)
        self._ki_bridge.failed.connect(self._on_ki_failed)
        self._ki_bridge.progress.connect(self._on_ki_progress)

    # ================================================================== KI-Nachprüfung
    def start_ki_review(self) -> None:
        """Geschwärztes Ergebnis im Hintergrund von einem Sprachmodell bewerten lassen."""
        s = self.session
        if not s or self._ki_session is not None or self.runner.busy:
            return
        if not llm_review.is_configured(self.settings):
            QMessageBox.information(self, "KI-Nachprüfung", "Die KI-Nachprüfung ist nicht eingerichtet "
                                    "(Einstellungen → KI-Nachprüfung).")
            return
        if not self._ki_confirmed:
            host = llm_review.host_of(self.settings)
            r = QMessageBox.question(
                self, "KI-Nachprüfung",
                f"Der geschwärzte Text wird an <b>{html.escape(host)}</b> "
                f"(Modell {html.escape(self.settings.llm_model)}) gesendet.<br><br>"
                "Das Original verlässt den Rechner nicht – übersehene Angaben im Ergebnis aber schon. "
                "Fortfahren?<br><span style='color:gray'>(Diese Frage erscheint einmal pro Programmstart.)</span>",
            )
            if r != QMessageBox.StandardButton.Yes:
                return
            self._ki_confirmed = True
        self._ki_session = s
        doc, findings, settings = s.doc, [f for f in s.findings], self.settings
        self.ki_dock.show()
        self.ki_dock.raise_()
        self.ki_panel.set_running(True, f"Anfrage an {llm_review.host_of(settings)} läuft …")
        self.status_label.setText("KI-Nachprüfung läuft …")
        self._update_actions()
        bridge = self._ki_bridge

        def work() -> None:
            try:
                res = llm_review.review(doc, findings, settings, progress=lambda i, n: bridge.progress.emit(i, n))
            except Exception as exc:  # noqa: BLE001 – Meldung für die Oberfläche
                bridge.failed.emit(s, str(exc))
            else:
                bridge.done.emit(s, res)

        threading.Thread(target=work, name="pii-redact-ki", daemon=True).start()

    def _on_ki_progress(self, done: int, total: int) -> None:
        if total > 1 and done < total:
            self.ki_panel.set_running(True, f"Anfrage {done + 1} von {total} läuft …")

    def _on_ki_failed(self, session, message: str) -> None:
        self._ki_session = None
        self.ki_panel.set_running(False)
        self._update_actions()
        if session is not self.session:
            return
        self.ki_panel.clear(text=f"Fehlgeschlagen: {message}")
        self.status_label.setText("KI-Nachprüfung fehlgeschlagen.")
        QMessageBox.warning(self, "KI-Nachprüfung fehlgeschlagen", message)

    def _on_ki_done(self, session, res) -> None:
        self._ki_session = None
        self.ki_panel.set_running(False)
        self._update_actions()
        if session is not self.session:  # inzwischen anderes Dokument geöffnet
            return
        new = llm_review.suggestions(res, session.findings, session.doc.text)
        if new:
            session.add_findings(new)
        spans = {(f.start, f.end): f.id for f in session.findings if not f.is_area}
        ids_by_hint = [[spans[sp] for sp in h.spans if sp in spans] for h in res.hints]
        self.ki_panel.show_result(res, ids_by_hint)
        self.ki_dock.raise_()
        if self.batch and self.batch_rel in self.batch.entries:
            self.batch.set_ki_review(self.batch_rel, res.compact())
            self._batch_save_timer.start()
        n = len(res.hints)
        self.status_label.setText(f"KI-Nachprüfung: Restrisiko {res.risk}, {n} {'Hinweis' if n == 1 else 'Hinweise'}"
                                  + (f", {len(new)} {'neuer Vorschlag' if len(new) == 1 else 'neue Vorschläge'} in der "
                                     "Fundliste" if new else "") + ".")

    def _ki_ids(self, only_inactive: bool) -> list[int]:
        if not self.session:
            return []
        return [f.id for f in self.session.findings if f.source == "ki" and not (only_inactive and f.active)]

    def _ki_set_all(self, active: bool) -> None:
        if self.session:
            self.session.set_active(self._ki_ids(False), active)

    def _ki_discard(self) -> None:
        if self.session:
            self.session.remove(self._ki_ids(True))
