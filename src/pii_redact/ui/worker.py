"""Analyse im Hintergrund – eine Warteschlange, ein Analyzer.

* **Interaktive Aufträge** (aktuelles Dokument neu analysieren) haben Vorrang.
* **Datei-Aufträge** (Ordner-Bearbeitung) laufen nacheinander im Hintergrund; die gerade
  geöffnete Datei kann nach vorne gezogen werden.

Es läuft immer nur ein Auftrag gleichzeitig, weil die spaCy-/ONNX-Modelle nicht für parallele
Aufrufe aus mehreren Threads ausgelegt sind.
"""

from __future__ import annotations

import copy
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..core import AnalysisMode, ModelMissingError, PiiAnalyzer, Settings, ThoroughModelMissingError


@dataclass
class Job:
    kind: str                 # "text" | "file"
    settings: Settings
    text: str = ""
    path: Path | None = None
    tag: str = ""             # bei Datei-Aufträgen: relativer Pfad
    batch_id: int = 0
    extra: dict = field(default_factory=dict)


class _Worker(QObject):
    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, analyzer: PiiAnalyzer, job: Job):
        super().__init__()
        self.analyzer = analyzer
        self.job = job

    @Slot()
    def run(self) -> None:
        job = self.job
        try:
            if not self.analyzer.loaded:
                extra = " + Transformer-Modell" if self.analyzer.mode == AnalysisMode.THOROUGH else ""
                self.status.emit(
                    f"Lade Sprachmodell {self.analyzer.model_name}{extra} … (einmalig, dauert einige Sekunden)"
                )
                self.analyzer.load()
            if job.kind == "file":
                from ..core.batch import analyze_file

                result = analyze_file(job.path, self.analyzer, job.settings, progress=self.progress.emit)
            else:
                self.status.emit("Analysiere Dokument …")
                result = self.analyzer.analyze(job.text, job.settings, progress=self.progress.emit)
            self.finished.emit(result)
        except (ModelMissingError, ThoroughModelMissingError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 – Fehler anzeigen statt abstürzen
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=4)}")


class AnalysisRunner(QObject):
    # interaktive Aufträge (wie bisher)
    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    # Datei-Aufträge
    fileStarted = Signal(str, int)            # rel, batch_id
    fileFinished = Signal(str, int, object)   # rel, batch_id, Ergebnis-dict
    fileFailed = Signal(str, int, str)        # rel, batch_id, Meldung
    queueChanged = Signal(int)                # verbleibende Datei-Aufträge (inkl. laufendem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analyzer: PiiAnalyzer | None = None
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._current: Job | None = None
        self._queue: deque[Job] = deque()

    # ------------------------------------------------------------------ Zustand
    @property
    def busy(self) -> bool:
        """Läuft (oder wartet) ein interaktiver Auftrag? Datei-Aufträge blockieren die Oberfläche nicht."""
        return (self._current is not None and self._current.kind == "text") or any(
            j.kind == "text" for j in self._queue
        )

    @property
    def running(self) -> bool:
        return self._current is not None

    @property
    def current_file(self) -> str | None:
        return self._current.tag if self._current is not None and self._current.kind == "file" else None

    def pending_files(self, batch_id: int | None = None) -> list[str]:
        jobs = ([self._current] if self._current else []) + list(self._queue)
        return [j.tag for j in jobs if j.kind == "file" and (batch_id is None or j.batch_id == batch_id)]

    # ------------------------------------------------------------------ Aufträge
    def start(self, text: str, settings: Settings) -> bool:
        """Interaktiver Auftrag (vorrangig). False, wenn schon einer ansteht."""
        if self.busy:
            return False
        self._queue.appendleft(Job("text", copy.deepcopy(settings), text=text))
        self._next()
        return True

    def enqueue_file(self, path: Path, rel: str, settings: Settings, batch_id: int, front: bool = False) -> None:
        job = Job("file", copy.deepcopy(settings), path=path, tag=rel, batch_id=batch_id)
        if front:
            self._insert_front(job)
        else:
            self._queue.append(job)
        self.queueChanged.emit(len(self.pending_files()))
        self._next()

    def prioritize(self, rel: str, batch_id: int) -> bool:
        """Datei-Auftrag nach vorne ziehen (hinter evtl. interaktive Aufträge)."""
        for job in list(self._queue):
            if job.kind == "file" and job.tag == rel and job.batch_id == batch_id:
                self._queue.remove(job)
                self._insert_front(job)
                return True
        return self._current is not None and self._current.tag == rel

    def cancel_batch(self, batch_id: int) -> None:
        self._queue = deque(j for j in self._queue if not (j.kind == "file" and j.batch_id == batch_id))
        self.queueChanged.emit(len(self.pending_files()))

    def _insert_front(self, job: Job) -> None:
        idx = 0
        while idx < len(self._queue) and self._queue[idx].kind == "text":
            idx += 1
        self._queue.insert(idx, job)

    # ------------------------------------------------------------------ Ausführung
    def _next(self) -> None:
        if self._current is not None or not self._queue:
            return
        job = self._queue.popleft()
        if self._analyzer is None or not self._analyzer.matches(job.settings):
            self._analyzer = PiiAnalyzer.for_settings(job.settings)
        self._current = job
        thread = QThread()
        worker = _Worker(self._analyzer, job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if job.kind == "text":
            worker.progress.connect(self.progress)
        worker.status.connect(self.status)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._thread, self._worker = thread, worker
        if job.kind == "file":
            self.fileStarted.emit(job.tag, job.batch_id)
        thread.start()

    def _cleanup(self) -> Job | None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        job = self._current
        self._thread = self._worker = self._current = None
        return job

    @Slot(object)
    def _on_finished(self, result) -> None:
        job = self._cleanup()
        if job is not None and job.kind == "file":
            self.fileFinished.emit(job.tag, job.batch_id, result)
            self.queueChanged.emit(len(self.pending_files()))
        elif job is not None:
            self.finished.emit(result)
        self._next()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        job = self._cleanup()
        if job is not None and job.kind == "file":
            self.fileFailed.emit(job.tag, job.batch_id, message)
            self.queueChanged.emit(len(self.pending_files()))
        elif job is not None:
            self.failed.emit(message)
        self._next()

    def shutdown(self) -> None:
        self._queue.clear()
        self._cleanup()
