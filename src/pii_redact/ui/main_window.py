"""Hauptfenster: Original und bearbeitete Fassung nebeneinander, Fundliste rechts."""

from __future__ import annotations

import html
import threading
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import AnalysisMode, ReplaceMode, Settings, UnsupportedFileError, load_document, redact_pdf, verify_pdf
from ..core import llm_review
from ..core.batch import Status, Workspace, export_document
from ..core.entities import AREA_KEYS, all_keys, info
from ..core.loaders import SUPPORTED_SUFFIXES
from ..core.redactor import pdf_redaction_plan, rects_of
from ..paths import find_ner_model, ner_model_info
from ..settings_store import defaults_path, settings_path
from .batch_panel import BatchPanel, NewBatchDialog, ReviewBar, confirm_auto_export, status_style
from .findings_panel import FindingsPanel
from .ki_panel import KiReviewPanel
from .notices import NoticeBar, NoticeButton, page_marks
from .pdf_view import Overlay, PdfPagesView
from .session import DocumentSession
from .settings_dialog import SettingsDialog, needs_reanalysis
from .text_view import HighlightTextView
from .worker import AnalysisRunner

QUICK_TYPES = ["PERSON", "LOCATION", "DE_ADDRESS", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER",
               "DATE_TIME", "IBAN_CODE", "CUSTOM"]
FILE_FILTER = "Dokumente (*.pdf *.txt *.md *.markdown *.text *.log);;PDF (*.pdf);;Text (*.txt);;Markdown (*.md *.markdown)"


class DocPanel(QWidget):
    """Überschrift + Tabs (Seiten / Text) für eine Seite des Vergleichs."""

    def __init__(self, title: str, mode: str, parent=None):
        super().__init__(parent)
        self.title = QLabel(f"<b>{title}</b>")
        self.header = QHBoxLayout()
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.pages = PdfPagesView(mode)
        self.text = HighlightTextView()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(self.header)
        lay.addWidget(self.tabs, 1)

    def configure(self, is_pdf: bool) -> None:
        self.tabs.clear()
        if is_pdf:
            self.tabs.addTab(self.pages, "Seiten")
        self.tabs.addTab(self.text, "Text")


#: Zoomstufen für Strg+Plus/Minus und Strg+Mausrad (100 % = echte Papiergröße)
ZOOM_STEPS = [25, 33, 50, 67, 75, 90, 100, 110, 125, 150, 175, 200, 250, 300, 400]
ZOOM_PRESETS = [("Seitenbreite", "breite"), ("Ganze Seite", "seite"), ("50 %", 50), ("75 %", 75), ("100 %", 100),
                ("125 %", 125), ("150 %", 150), ("200 %", 200)]

OCR_NOT_AUTO = "Texterkennung (OCR) – wird nicht automatisch exportiert, bitte einzeln prüfen"


class _KiBridge(QObject):
    """Meldet Ergebnisse der KI-Nachprüfung aus dem Hintergrund-Thread an die Oberfläche."""
    done = Signal(object, object)      # Sitzung, ReviewResult
    failed = Signal(object, str)       # Sitzung, Fehlermeldung
    progress = Signal(int, int)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load(settings_path(), defaults_path())
        self._mode_fallback = False
        if self.settings.analysis_mode == AnalysisMode.THOROUGH and find_ner_model(self.settings.ner_model) is None:
            self.settings.analysis_mode = AnalysisMode.FAST  # Modell (noch) nicht installiert
            self._mode_fallback = True
        self.session: DocumentSession | None = None
        self.selected_ids: set[int] = set()
        self._syncing = False
        self.runner = AnalysisRunner(self)
        self.runner.progress.connect(self._on_progress)
        self.runner.status.connect(lambda msg: self.status_label.setText(msg))
        self.runner.finished.connect(self._on_analysis_finished)
        self.runner.failed.connect(self._on_analysis_failed)
        self.runner.fileFinished.connect(self._on_file_finished)
        self.runner.fileFailed.connect(self._on_file_failed)
        self.runner.fileStarted.connect(lambda *_: self._refresh_batch())
        self.runner.queueChanged.connect(lambda *_: self._refresh_batch())
        # Ordner-Bearbeitung
        self.batch: Workspace | None = None
        self.batch_id = 0
        self.batch_rel: str | None = None
        self._awaiting_rel: str | None = None     # geöffnete Datei, deren Analyse noch läuft
        self._batch_loading = False
        self._auto_export = False                  # nach „Ungeprüfte exportieren“: Rest nach Analyse exportieren
        self._model_error_shown = False
        self._analysis_session = None
        self._ki_session = None                    # Sitzung, deren KI-Nachprüfung gerade läuft
        self._ki_confirmed = False                 # Senden an den Server in dieser Sitzung bestätigt
        self._ki_bridge = _KiBridge(self)
        self._ki_bridge.done.connect(self._on_ki_done)
        self._ki_bridge.failed.connect(self._on_ki_failed)
        self._ki_bridge.progress.connect(self._on_ki_progress)

        self.setWindowTitle("pii-redact")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_actions()
        self._apply_locks()
        self._update_actions()
        if self._mode_fallback:
            self.status_label.setText("Modus „Gründlich“ nicht verfügbar (kein Transformer-Modell) – verwende „Schnell“.")

    # ================================================================== Aufbau
    def _build_ui(self) -> None:
        # Startseite
        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.addStretch(1)
        hint = QLabel(self._welcome_html())
        self.welcome_label = hint
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn = QPushButton("Datei öffnen …")
        btn.setFixedWidth(220)
        btn.clicked.connect(self.open_dialog)
        btn2 = QPushButton("Ordner bearbeiten …")
        btn2.setFixedWidth(220)
        btn2.clicked.connect(lambda: self.open_folder_dialog())
        btn3 = QPushButton("Ordner-Arbeit fortsetzen …")
        btn3.setFixedWidth(220)
        btn3.clicked.connect(self.resume_folder_dialog)
        wl.addWidget(hint)
        wl.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        wl.addWidget(btn2, 0, Qt.AlignmentFlag.AlignHCenter)
        wl.addWidget(btn3, 0, Qt.AlignmentFlag.AlignHCenter)
        wl.addStretch(2)

        # Dokumentansicht
        self.left = DocPanel("Original", "original")
        self.right = DocPanel("Bearbeitet (Vorschau)", "redacted")
        self.edit_btn = QPushButton("Frei bearbeiten")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip("Anonymisierten Text direkt bearbeiten (wirkt auf den Text-Export)")
        self.edit_btn.toggled.connect(self._toggle_free_edit)
        self.right.header.addWidget(self.edit_btn)

        # Kritische Hinweise (OCR, Seiten ohne Text): eine Zeile, pro Dokument schließbar.
        # Alle übrigen Hinweise stehen im Zähler der Statusleiste und als Symbol an der Seite.
        self.notice_bar = NoticeBar()
        self.notice_bar.helpRequested.connect(self.show_help)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left)
        splitter.addWidget(self.right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        doc_view = QWidget()
        dl = QVBoxLayout(doc_view)
        dl.setContentsMargins(4, 4, 4, 0)
        self.review_bar = ReviewBar()
        self.review_bar.hide()
        self.review_bar.previous.connect(lambda: self._step_file(-1))
        self.review_bar.next.connect(lambda: self._step_file(+1))
        self.review_bar.confirm.connect(self.confirm_and_next)
        dl.addWidget(self.review_bar)
        dl.addWidget(self.notice_bar)
        dl.addWidget(splitter, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(welcome)
        self.stack.addWidget(doc_view)
        self.setCentralWidget(self.stack)

        # Fundliste
        self.findings = FindingsPanel()
        dock = QDockWidget("Funde", self)
        dock.setObjectName("findings")
        dock.setWidget(self.findings)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [520], Qt.Orientation.Horizontal)
        self.findings_dock = dock

        # KI-Nachprüfung (optional) – als Reiter neben den Funden
        self.ki_panel = KiReviewPanel()
        self.ki_dock = QDockWidget("KI-Bewertung", self)
        self.ki_dock.setObjectName("ki")
        self.ki_dock.setWidget(self.ki_panel)
        self.ki_dock.setFeatures(dock.features())
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ki_dock)
        self.tabifyDockWidget(dock, self.ki_dock)
        dock.raise_()
        self.ki_panel.runRequested.connect(self.start_ki_review)
        self.ki_panel.hintActivated.connect(lambda ids: self.select(ids, reveal=True))
        self.ki_panel.activateAll.connect(lambda: self._ki_set_all(True))
        self.ki_panel.discardAll.connect(self._ki_discard)

        # Arbeitsliste (Ordner-Bearbeitung)
        self.batch_panel = BatchPanel()
        self.batch_dock = QDockWidget("Arbeitsliste", self)
        self.batch_dock.setObjectName("batch")
        self.batch_dock.setWidget(self.batch_panel)
        self.batch_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.batch_dock)
        self.resizeDocks([self.batch_dock], [380], Qt.Orientation.Horizontal)
        self.batch_dock.hide()
        self.batch_panel.fileActivated.connect(self._open_batch_file)
        self.batch_panel.exportUnreviewed.connect(self.export_unreviewed)
        self.batch_panel.openProtocol.connect(lambda: self._open_path(self.batch.protocol_path if self.batch else None))
        self.batch_panel.openTarget.connect(lambda: self._open_path(self.batch.target if self.batch else None))
        self.batch_panel.closeRequested.connect(self.close_batch)
        self._batch_save_timer = QTimer(self, singleShot=True, interval=800)
        self._batch_save_timer.timeout.connect(self._save_batch)

        # Statusleiste
        self.status_label = QLabel("Bereit")
        self.status_label.linkActivated.connect(self._on_status_link)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.mode_label = QLabel()
        self.notice_btn = NoticeButton()
        self.notice_btn.helpRequested.connect(self.show_help)
        self.notice_btn.pageRequested.connect(self._show_page)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.notice_btn)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.mode_label)
        # Zoom der Seitenansicht (unten rechts, wie in Office-Programmen)
        self.zoom_out_btn = QToolButton(text="−", autoRaise=True, toolTip="Verkleinern (Strg+Minus)")
        self.zoom_in_btn = QToolButton(text="+", autoRaise=True, toolTip="Vergrößern (Strg+Plus)")
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_box.setMinimumContentsLength(11)
        for label, value in ZOOM_PRESETS:
            self.zoom_box.addItem(label, value)
        self.zoom_box.setToolTip("Zoom der Seitenansicht – Seitenbreite (Strg+0), Ganze Seite (Strg+2), "
                                 "100 % = Papiergröße (Strg+1), oder Wert eintippen")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_step(-1))
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_step(+1))
        self.zoom_box.activated.connect(self._on_zoom_box_activated)
        self.zoom_box.lineEdit().editingFinished.connect(self._on_zoom_box_edited)
        for w in (self.zoom_out_btn, self.zoom_box, self.zoom_in_btn):
            self.statusBar().addPermanentWidget(w)
        self._zoom_timer = QTimer(self, singleShot=True, interval=60)
        self._zoom_timer.timeout.connect(self._apply_zoom)
        self._update_zoom_box()

        # Signale
        self.findings.findingActivated.connect(lambda fid: self.select([fid], reveal=True, from_table=True))
        self.findings.contextRequested.connect(lambda ids, pos: self._finding_menu(ids, pos))
        self.left.text.clickedAt.connect(self._on_left_text_click)
        self.left.text.contextAt.connect(self._on_left_text_context)
        self.right.text.clickedAt.connect(self._on_right_text_click)
        self.right.text.contextAt.connect(self._on_right_text_context)
        self.right.text.textChanged.connect(self._on_free_text_changed)
        for panel in (self.left, self.right):
            panel.pages.overlayClicked.connect(lambda ids: ids and self.select(ids, reveal=True))
            panel.pages.contextRequested.connect(lambda ids, pos: ids and self._finding_menu(ids, pos))
            panel.pages.zoomRequested.connect(self.zoom_step)
            panel.pages.viewResized.connect(self._on_view_resized)
        self.left.pages.areaSelected.connect(self._on_area_selected)
        self.right.pages.areaSelected.connect(self._on_area_selected)
        # Tooltips: was steckt hinter einer Markierung bzw. einem Platzhalter?
        self.left.text.tooltip_provider = lambda off: self._ids_tooltip(
            [f.id for f in self.session.findings_at(off)] if self.session else [])
        self.right.text.tooltip_provider = self._redacted_tooltip
        self.left.pages.tooltip_provider = self._ids_tooltip
        self.right.pages.tooltip_provider = self._ids_tooltip
        self.left.setVisible(self.settings.show_original)
        self.left.tabs.currentChanged.connect(lambda i: self._sync_tabs(self.right, i))
        self.right.tabs.currentChanged.connect(lambda i: self._sync_tabs(self.left, i))
        self.left.text.verticalScrollBar().valueChanged.connect(lambda _v: self._sync_text_scroll(self.left))
        self.right.text.verticalScrollBar().valueChanged.connect(lambda _v: self._sync_text_scroll(self.right))
        self.left.pages.verticalScrollBar().valueChanged.connect(lambda v: self._sync_page_scroll(self.right, v))
        self.right.pages.verticalScrollBar().valueChanged.connect(lambda v: self._sync_page_scroll(self.left, v))

        self._refresh_timer = QTimer(self, singleShot=True, interval=0)
        self._refresh_timer.timeout.connect(self._refresh_views)

    def _welcome_html(self) -> str:
        if llm_review.is_configured(self.settings):
            local = (f"Alles läuft lokal auf diesem Rechner – außer der KI-Nachprüfung: Sie sendet auf Knopfdruck "
                     f"nur den bereits geschwärzten Text an {llm_review.host_of(self.settings)}.")
        else:
            local = "Alles läuft lokal auf diesem Rechner – es werden keine Daten übertragen."
        return ("<h2>Personenbezogene Daten entfernen</h2>"
                "<p>PDF-, TXT- oder Markdown-Datei (oder einen ganzen Ordner) hierher ziehen<br>"
                "oder <b>Datei → Öffnen</b> (Strg+O).</p>"
                f"<p style='color:gray'>{local}</p>"
                "<p style='color:gray'>Hilfe: <b>F1</b> oder <b>Hilfe → Anwenderhilfe</b></p>")

    def _build_actions(self) -> None:
        st = self.style()
        a = self.actions_ = {}

        def act(key, text, slot, shortcut=None, icon=None, tip=None):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if icon is not None:
                action.setIcon(st.standardIcon(icon))
            if tip:
                action.setToolTip(tip)
            action.triggered.connect(slot)
            a[key] = action
            return action

        SP = QStyle.StandardPixmap
        act("open", "Öffnen …", self.open_dialog, QKeySequence.StandardKey.Open, SP.SP_DialogOpenButton)
        act("open_folder", "Ordner bearbeiten …", lambda: self.open_folder_dialog(), "Ctrl+Shift+O",
            SP.SP_DirOpenIcon, "Alle Dateien eines Ordners analysieren, prüfen und exportieren")
        act("resume_folder", "Ordner-Arbeit fortsetzen …", self.resume_folder_dialog)
        act("close_folder", "Ordner-Arbeit schließen", self.close_batch)
        act("confirm_next", "Geprüft && weiter", self.confirm_and_next, "Ctrl+Return")
        act("prev_file", "Vorherige Datei", lambda: self._step_file(-1), "Alt+Left")
        act("next_file", "Nächste Datei", lambda: self._step_file(+1), "Alt+Right")
        act("reanalyze", "Neu analysieren", self.start_analysis, "F5", SP.SP_BrowserReload,
            "Dokument erneut prüfen (manuelle Änderungen bleiben erhalten)")
        act("ki_review", "KI-Prüfung", self.start_ki_review, "Ctrl+K", SP.SP_MessageBoxQuestion,
            "Geschwärztes Ergebnis von einem Sprachmodell bewerten lassen: Sind Personen trotzdem erkennbar?")
        act("export_text", "Als Text/Markdown exportieren …", self.export_text, "Ctrl+Shift+S")
        act("export_pdf", "Als geschwärztes PDF exportieren …", self.export_pdf, QKeySequence.StandardKey.Save)
        act("quit", "Beenden", self.close, QKeySequence.StandardKey.Quit)
        act("undo", "Rückgängig", self.undo, QKeySequence.StandardKey.Undo, SP.SP_ArrowBack)
        act("redo", "Wiederholen", self.redo, QKeySequence.StandardKey.Redo, SP.SP_ArrowForward)
        act("mark", "Markierung schwärzen …", lambda: self._mark_selection(False), "Ctrl+R")
        act("mark_all", "Alle Vorkommen der Markierung schwärzen …", lambda: self._mark_selection(True), "Ctrl+Shift+R")
        act("all_on", "Alle Funde schwärzen", lambda: self._set_all(True))
        act("all_off", "Keinen Fund schwärzen", lambda: self._set_all(False))
        act("settings", "Einstellungen …", self.open_settings, "Ctrl+,", SP.SP_FileDialogDetailedView)
        act("zoom_in", "Vergrößern", lambda: self.zoom_step(+1), QKeySequence.StandardKey.ZoomIn)
        act("zoom_out", "Verkleinern", lambda: self.zoom_step(-1), QKeySequence.StandardKey.ZoomOut)
        act("zoom_width", "Seitenbreite", lambda: self.set_zoom_mode("breite"), "Ctrl+0")
        act("zoom_page", "Ganze Seite", lambda: self.set_zoom_mode("seite"), "Ctrl+2")
        act("zoom_100", "Originalgröße (100 %)", lambda: self.set_zoom_mode("fest", 100), "Ctrl+1")
        act("about", "Über pii-redact", self.about)
        act("help", "Anwenderhilfe", lambda: self.show_help(), "F1", SP.SP_DialogHelpButton)
        act("help_keys", "Tastenkürzel", lambda: self.show_help("tasten"))
        act("help_folder", "Ordner bearbeiten – Anleitung", lambda: self.show_help("ordner"))
        a["show_original"] = QAction(st.standardIcon(SP.SP_FileDialogContentsView), "Original anzeigen", self,
                                     checkable=True, checked=self.settings.show_original)
        a["show_original"].setShortcut(QKeySequence("Ctrl+Shift+V"))
        a["show_original"].setToolTip("Originaldokument neben der bearbeiteten Fassung einblenden (Strg+Umschalt+V)")
        a["show_original"].toggled.connect(self._set_show_original)
        a["sync"] = QAction("Scrollen koppeln", self, checkable=True, checked=self.settings.sync_scroll)
        a["sync"].toggled.connect(self._set_sync)

        # Menüs
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        m.addActions([a["open"], a["open_folder"], a["resume_folder"]])
        self.recent_menu = m.addMenu("Zuletzt bearbeitete Ordner")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addAction(a["close_folder"])
        m.addSeparator()
        m.addAction(a["reanalyze"])
        m.addSeparator()
        m.addActions([a["export_pdf"], a["export_text"]])
        m.addSeparator()
        m.addAction(a["quit"])
        m = mb.addMenu("&Bearbeiten")
        m.addActions([a["undo"], a["redo"]])
        m.addSeparator()
        m.addActions([a["confirm_next"], a["prev_file"], a["next_file"]])
        m.addSeparator()
        m.addActions([a["mark"], a["mark_all"], a["all_on"], a["all_off"]])
        m.addSeparator()
        m.addAction(a["ki_review"])
        m.addSeparator()
        m.addAction(a["settings"])
        m = mb.addMenu("&Ansicht")
        m.addAction(a["show_original"])
        m.addSeparator()
        m.addActions([a["zoom_in"], a["zoom_out"]])
        m.addSeparator()
        m.addActions([a["zoom_width"], a["zoom_page"], a["zoom_100"]])
        m.addSeparator()
        m.addAction(a["sync"])
        m = mb.addMenu("&Hilfe")
        m.addActions([a["help"], a["help_folder"], a["help_keys"]])
        m.addSeparator()
        m.addAction(a["about"])

        # Werkzeugleiste
        tb = QToolBar("Werkzeuge")
        tb.setObjectName("main_toolbar")
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        tb.addActions([a["open"], a["open_folder"], a["reanalyze"], a["ki_review"]])
        tb.addSeparator()
        tb.addActions([a["undo"], a["redo"]])
        tb.addSeparator()
        tb.addAction(a["show_original"])
        tb.addSeparator()
        tb.addWidget(QLabel(" Ersetzung: "))
        self.mode_box = QComboBox()
        for key, label in ReplaceMode.LABELS.items():
            self.mode_box.addItem(label, key)
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(self.settings.replace_mode)))
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        tb.addWidget(self.mode_box)
        tb.addSeparator()
        tb.addWidget(QLabel(" Analyse: "))
        self.analysis_box = QComboBox()
        self.analysis_box.addItem("Schnell", AnalysisMode.FAST)
        self.analysis_box.addItem("Gründlich", AnalysisMode.THOROUGH)
        self.analysis_box.setItemData(0, "spaCy + Muster – Sekundenbruchteile pro Seite", Qt.ItemDataRole.ToolTipRole)
        self._refresh_analysis_box()
        self.analysis_box.currentIndexChanged.connect(self._on_analysis_mode_changed)
        tb.addWidget(self.analysis_box)
        tb.addSeparator()
        export_btn_menu = QMenu(self)
        export_btn_menu.addActions([a["export_pdf"], a["export_text"]])
        self.export_action = QAction(st.standardIcon(SP.SP_DialogSaveButton), "Exportieren", self)
        self.export_action.setMenu(export_btn_menu)
        self.export_action.triggered.connect(self._export_default)
        tb.addAction(self.export_action)
        tb.addSeparator()
        tb.addAction(a["settings"])

    # ================================================================== Dateien
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Dokument öffnen", "", FILE_FILTER)
        if path:
            self.open_file(Path(path))

    def open_file(self, path: Path) -> None:
        if path.is_dir():
            self.open_folder_dialog(str(path))
            return
        if self.runner.busy:
            QMessageBox.information(self, "Bitte warten", "Die laufende Analyse ist noch nicht fertig.")
            return
        if not self._confirm_discard():
            return
        try:
            doc = self._load(path)
        except (UnsupportedFileError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Datei kann nicht geöffnet werden", str(exc))
            return
        if self.batch:
            self.close_batch(show_welcome=False)
        self._show_document(doc)
        self.start_analysis()

    def _show_document(self, doc) -> None:
        """Dokument in beiden Ansichten anzeigen und eine neue Sitzung anlegen (ohne Analyse)."""
        self.edit_btn.blockSignals(True)
        self.edit_btn.setChecked(False)
        self.edit_btn.blockSignals(False)
        self.right.text.setReadOnly(True)

        self.session = DocumentSession(doc, self.settings, self)
        self.session.changed.connect(self._on_session_changed)
        self.selected_ids = set()
        for panel in (self.left, self.right):
            panel.configure(doc.is_pdf)
            panel.pages.set_pdf(doc.pdf_bytes if doc.is_pdf else None)
            panel.pages.set_ocr_pages(doc.ocr_pages)
        self.left.text.set_text(doc.text)
        self.right.text.set_text(doc.text)
        self.findings.set_session(self.session)
        self._show_notices(doc)
        stored = self.batch.entries[self.batch_rel].ki_review if (self.batch and self.batch_rel in self.batch.entries) else None
        self.ki_panel.clear(stored if self._batch_loading else None)
        if doc.has_ocr:
            self.right.tabs.setCurrentWidget(self.right.pages)
        self.stack.setCurrentIndex(1)
        self._update_title()
        QTimer.singleShot(0, self._fit_layout)

    def _fit_layout(self) -> None:
        """Beide Seiten gleich breit; Zoom passt sich über ``viewResized`` bzw. ``_apply_zoom`` an."""
        total = sum(self.splitter.sizes())
        self.splitter.setSizes([total // 2, total - total // 2])
        self._zoom_timer.start()

    def _confirm_discard(self) -> bool:
        if self.batch:  # Ordner-Bearbeitung speichert laufend
            return True
        if not self.session or not self.session.dirty:
            return True
        r = QMessageBox.question(
            self,
            "Nicht exportierte Änderungen",
            "Die Nachbearbeitung wurde noch nicht exportiert. Trotzdem fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

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
            data = redact_pdf(doc, findings, self.settings.replace_mode, self.settings.pdf_labels)
            Path(path).write_bytes(data)
            return verify_pdf(data, findings, doc.ocr_pages)

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

    # ================================================================== Analyse
    def start_analysis(self) -> None:
        if not self.session or self.runner.busy:
            return
        if self.batch and self._awaiting_rel == self.batch_rel:
            return  # Hintergrund-Analyse dieser Datei läuft ohnehin
        self.progress.setRange(0, 0)
        self.progress.show()
        self._analysis_session = self.session
        self.runner.start(self.session.doc.text, self.settings)
        self._update_actions()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def _on_analysis_finished(self, findings) -> None:
        self.progress.hide()
        if not self.session or self.session is not self._analysis_session:
            self._update_actions()
            return  # inzwischen wurde eine andere Datei geöffnet
        if self.batch and self.batch_rel:
            self.batch.entries[self.batch_rel].analysis_mode = self.settings.analysis_mode
        first = not self.session.findings and not self.session.can_undo
        if first:
            self.session.set_findings(findings)
            self.session.dirty = False
        else:
            self.session.apply_reanalysis(findings)
        total, active = self.session.counts()
        self.status_label.setText(f"Analyse fertig: {total} Funde.")
        self._update_actions()
        self._update_title()

    def _on_analysis_failed(self, message: str) -> None:
        self.progress.hide()
        self.status_label.setText("Analyse fehlgeschlagen")
        self._update_actions()
        QMessageBox.critical(self, "Analyse fehlgeschlagen", message)

    # ================================================================== Anzeige
    def _on_session_changed(self) -> None:
        self._refresh_timer.start()
        if self.batch and self.batch_rel and not self._batch_loading and self._awaiting_rel != self.batch_rel:
            self.batch.update_findings(self.batch_rel, self.session.findings)
            self._batch_save_timer.start()

    def _refresh_views(self) -> None:
        s = self.session
        if not s:
            return
        self.findings.reload()
        self.left.text.set_marks([(f.start, f.end, [f.id], f.active, f.entity_type)
                                  for f in s.findings if not f.is_area])
        if s.manual_text is None:
            red = s.redacted()
            self.right.text.blockSignals(True)
            self.right.text.set_text(red.text)
            self.right.text.blockSignals(False)
            marks = []
            for seg in red.segments:
                f = s.get(seg.finding_ids[0])
                marks.append((seg.red_start, seg.red_end, seg.finding_ids, True, f.entity_type if f else "CUSTOM"))
            # abgewählte Funde bleiben in der Vorschau erkennbar (grau gepunktet) – wichtig, wenn das
            # Original ausgeblendet ist
            for f in self._inactive_uncovered(s):
                if not f.is_area:
                    marks.append((red.to_redacted(f.start), red.to_redacted(f.end), [f.id], False, f.entity_type))
            self.right.text.set_marks(marks)
        if s.doc.is_pdf:
            orig = []
            for f in s.findings:
                for page, rect in rects_of(s.doc, f):
                    orig.append(Overlay(page, rect, [f.id], f.active, f.entity_type))
            self.left.pages.set_overlays(orig)
            plan = pdf_redaction_plan(s.doc, s.findings, self.settings.replace_mode, self.settings.pdf_labels)
            red_ov = []
            for page, rect, label, ids in plan:
                f = s.get(ids[0])
                red_ov.append(Overlay(page, rect, ids, True, f.entity_type if f else "CUSTOM", label))
            for f in self._inactive_uncovered(s):
                for page, rect in rects_of(s.doc, f):
                    red_ov.append(Overlay(page, rect, [f.id], False, f.entity_type))
            self.right.pages.set_overlays(red_ov)
        self._apply_selection()
        self._update_actions()
        self._update_title()
        self._refresh_batch()

    @staticmethod
    def _inactive_uncovered(s) -> list:
        active = [f for f in s.findings if f.active]
        return [f for f in s.findings if not f.active and not any(a.overlaps(f) for a in active)]

    def select(self, ids: list[int], reveal: bool = False, from_table: bool = False) -> None:
        self.selected_ids = set(ids)
        self._apply_selection()
        if not from_table:
            self.findings.select_ids(ids)
        if reveal and ids and self.session:
            f = self.session.get(ids[0])
            if not f:
                return
            self._syncing = True
            if not f.is_area:
                self.left.text.reveal(f.start, f.end)
                if self.session.manual_text is None:
                    r = self.session.redacted().to_redacted(f.start)
                    self.right.text.reveal(r, r)
            else:
                for panel in (self.left, self.right):
                    if panel.tabs.indexOf(panel.pages) >= 0:
                        panel.tabs.setCurrentWidget(panel.pages)
            if self.session.doc.is_pdf:
                rects = rects_of(self.session.doc, f)
                if rects:
                    self.left.pages.reveal(*rects[0])
                    self.right.pages.reveal(*rects[0])
            self._syncing = False

    def _apply_selection(self) -> None:
        for view in (self.left.text, self.right.text, self.left.pages, self.right.pages):
            view.set_selected_ids(self.selected_ids)

    def _show_notices(self, doc) -> None:
        """Hinweise zum Dokument verteilen: kritische als einzeilige Leiste (außer bei „Kompakte
        Hinweise“), alle im Zähler der Statusleiste, seitenbezogene als Symbol in der Seitenansicht."""
        notices = doc.notices if doc else []
        self.notice_bar.set_notices([] if self.settings.compact_notices else notices)
        self.notice_btn.set_notices(notices)
        marks = page_marks(notices)
        for panel in (self.left, self.right):
            panel.pages.set_page_marks(marks)

    def _show_page(self, page: int) -> None:
        if not (self.session and self.session.doc.is_pdf):
            return
        for panel in (self.left, self.right):
            panel.tabs.setCurrentWidget(panel.pages)
        self.right.pages.reveal(page)
        if self.left.isVisible():
            self.left.pages.reveal(page)

    def _show_saved(self, path: Path, what: str, details: str = "") -> None:
        """Erfolgsmeldung in der Statusleiste mit Link zum Zielordner (statt eines Dialogs)."""
        self._saved_path = path
        self.status_label.setText(f"✓ {html.escape(path.name)} – {html.escape(what)} · "
                                  "<a href='open-folder'>Ordner öffnen</a>")
        self.status_label.setToolTip(details or f"Gespeichert: {path}")

    def _on_status_link(self, link: str) -> None:
        if link == "open-folder" and getattr(self, "_saved_path", None):
            self._open_path(self._saved_path.parent)

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

    def _run_busy(self, label: str, fn):
        """``fn(progress)`` in einem Hintergrund-Thread ausführen (z. B. Laden mit Texterkennung),
        dabei einen Fortschrittsdialog zeigen. Kurze Aufgaben (< 0,3 s) laufen ohne Dialog."""
        state: dict = {}

        def progress(done: int, total: int) -> None:
            state["progress"] = (done, total)

        def work() -> None:
            try:
                state["result"] = fn(progress)
            except BaseException as exc:  # noqa: BLE001 – im Hauptthread erneut auslösen
                state["error"] = exc

        th = threading.Thread(target=work, name="pii-redact-busy", daemon=True)
        th.start()
        th.join(0.3)
        if th.is_alive():
            dlg = QProgressDialog(label, None, 0, 0, self)
            dlg.setWindowTitle("Bitte warten")
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.setMinimumDuration(0)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.show()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                while th.is_alive():
                    QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                    th.join(0.05)
                    p = state.get("progress")
                    if p and p[1]:
                        dlg.setRange(0, p[1])
                        dlg.setValue(min(p[0], p[1]))
                        dlg.setLabelText(f"Texterkennung (OCR) für gescannte Seiten …\n"
                                         f"Seite {min(p[0] + 1, p[1])} von {p[1]}")
            finally:
                QApplication.restoreOverrideCursor()
                dlg.close()
                dlg.deleteLater()
        if "error" in state:
            raise state["error"]
        return state.get("result")

    def _load(self, path: Path):
        return self._run_busy("Dokument wird geladen …",
                              lambda progress: load_document(path, ocr=self.settings.ocr, progress=progress))

    def _update_title(self) -> None:
        if not self.session:
            self.setWindowTitle("pii-redact")
            return
        if self.batch and self.batch_rel:
            self.setWindowTitle(f"{self.batch_rel} – Ordner {self.batch.source.name} – pii-redact")
            return
        star = " •" if self.session.dirty else ""
        self.setWindowTitle(f"{self.session.doc.path.name}{star} – pii-redact")

    def _update_actions(self) -> None:
        has = self.session is not None
        busy = self.runner.busy
        a = self.actions_
        a["reanalyze"].setEnabled(has and not busy)
        a["export_text"].setEnabled(has and not busy)
        a["export_pdf"].setEnabled(has and not busy and self.session.doc.is_pdf)
        self.export_action.setEnabled(has and not busy)
        a["undo"].setEnabled(has and self.session.can_undo)
        a["redo"].setEnabled(has and self.session.can_redo)
        for k in ("mark", "mark_all", "all_on", "all_off"):
            a[k].setEnabled(has and not busy)
        self.edit_btn.setEnabled(has and not busy)
        self.analysis_box.setEnabled(not busy)
        in_batch = self.batch is not None
        self.edit_btn.setVisible(not in_batch)  # freie Bearbeitung passt nicht zum protokollierten Ablauf
        for k in ("export_text", "export_pdf"):
            a[k].setEnabled(a[k].isEnabled() and not in_batch)
        self.export_action.setEnabled(self.export_action.isEnabled() and not in_batch)
        awaiting = in_batch and self._awaiting_rel is not None and self._awaiting_rel == self.batch_rel
        a["reanalyze"].setEnabled(a["reanalyze"].isEnabled() and not awaiting)
        a["confirm_next"].setEnabled(in_batch and has and not busy and not awaiting)
        for k in ("prev_file", "next_file", "close_folder"):
            a[k].setEnabled(in_batch)
        ki = llm_review.is_configured(self.settings)
        a["ki_review"].setVisible(ki)
        a["ki_review"].setEnabled(ki and has and not busy and not awaiting and self._ki_session is None)
        self.ki_dock.toggleViewAction().setVisible(ki)
        if not ki and self.ki_dock.isVisible():
            self.ki_dock.hide()
        elif ki and not self.ki_dock.isVisible() and not getattr(self, "_ki_dock_shown", False):
            self._ki_dock_shown = True
            self.ki_dock.show()
            self.findings_dock.raise_()

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

    def _set_show_original(self, on: bool) -> None:
        self.settings.show_original = on
        self._save_settings()
        self.left.setVisible(on)
        if self.session:
            QTimer.singleShot(0, self._fit_layout)

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
        s = self.session
        if not s:
            return
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
        if menu.isEmpty():
            return
        menu.exec(pos)

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

    # ================================================================== Einstellungen
    def _on_mode_changed(self) -> None:
        self.settings.replace_mode = self.mode_box.currentData()
        self._save_settings()
        if self.session:
            self.session.settings_changed()

    def _apply_locks(self) -> None:
        """Von der IT gesperrte Einstellungen (defaults.json → "locked") in der Werkzeugleiste sperren."""
        locked = set(self.settings.locked)
        tip = "Von der IT festgelegt"
        for box, key in ((self.mode_box, "replace_mode"), (self.analysis_box, "analysis_mode")):
            box.setEnabled(key not in locked)
            if key in locked:
                box.setToolTip(tip)

    def _refresh_analysis_box(self) -> None:
        model_dir = find_ner_model(self.settings.ner_model)
        item = self.analysis_box.model().item(1)
        if model_dir is None:
            item.setEnabled(False)
            tip = "Kein Transformer-Modell installiert (Modell-Paket bzw. tools\\modelle_testen.bat)"
        else:
            item.setEnabled(True)
            meta = ner_model_info(model_dir)
            tip = f"Zusätzlich Transformer-Modell „{meta.get('source', model_dir.name)}“ – langsamer, findet mehr Namen"
        self.analysis_box.setItemData(1, tip, Qt.ItemDataRole.ToolTipRole)
        self.analysis_box.blockSignals(True)
        self.analysis_box.setCurrentIndex(max(0, self.analysis_box.findData(self.settings.analysis_mode)))
        self.analysis_box.blockSignals(False)
        self.mode_label.setText(f"Modus: {AnalysisMode.LABELS.get(self.settings.analysis_mode, '?')}")

    def _on_analysis_mode_changed(self) -> None:
        mode = self.analysis_box.currentData()
        if mode == self.settings.analysis_mode:
            return
        self._mode_fallback = False  # ausdrücklich gewählt
        self.settings.analysis_mode = mode
        self._save_settings()
        self._refresh_analysis_box()
        self._requeue_batch()
        if self.session:
            self.start_analysis()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if not dlg.exec():
            return
        new = dlg.result_settings()
        reanalyze = needs_reanalysis(self.settings, new)
        if new.analysis_mode != self.settings.analysis_mode:
            self._mode_fallback = False  # ausdrücklich gewählt
        for field in new.__dataclass_fields__:
            setattr(self.settings, field, getattr(new, field))  # gleiche Instanz (Session hält Referenz)
        self._save_settings()
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(self.settings.replace_mode)))
        self.mode_box.blockSignals(False)
        self._refresh_analysis_box()
        self._apply_locks()
        self.welcome_label.setText(self._welcome_html())
        self._update_actions()
        if self.session:
            self._show_notices(self.session.doc)
        if reanalyze:
            self._requeue_batch()
        if self.session:
            if reanalyze:
                self.start_analysis()
            else:
                self.session.settings_changed()

    def _save_settings(self) -> None:
        to_save = self.settings
        if self._mode_fallback and self.settings.analysis_mode == AnalysisMode.FAST:
            # „Schnell“ nur ersatzweise (Modell fehlt) – gewünscht bleibt „Gründlich“, sobald das Modell da ist
            import copy

            to_save = copy.copy(self.settings)
            to_save.analysis_mode = AnalysisMode.THOROUGH
        try:
            to_save.save(settings_path())
        except OSError as exc:
            self.status_label.setText(f"Einstellungen konnten nicht gespeichert werden: {exc}")

    def show_help(self, anchor: str = "") -> None:
        from .help_window import HelpWindow

        if getattr(self, "_help", None) is None:
            self._help = HelpWindow(self)
        self._help.show_chapter(anchor)

    def about(self) -> None:
        from .about_dialog import AboutDialog

        AboutDialog(self.settings, self).exec()

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
                ws = Workspace.load(tgt)
                if ws.source != src.resolve():
                    QMessageBox.warning(
                        self, "Anderer Arbeitsstand",
                        f"Im Zielordner liegt bereits ein Arbeitsstand für den Quellordner\n{ws.source}\n\n"
                        "Bitte einen anderen Zielordner wählen.")
                    return
            else:
                ws = Workspace.create(src, tgt, recursive)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Ordner kann nicht bearbeitet werden", str(exc))
            return
        self._activate_batch(ws)

    def resume_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Zielordner mit Arbeitsstand wählen")
        if d:
            self.resume_folder(Path(d))

    def resume_folder(self, target: Path) -> None:
        if not Workspace.exists_in(target):
            QMessageBox.information(self, "Kein Arbeitsstand",
                                    f"In {target} wurde kein pii-redact-Arbeitsstand gefunden.")
            self._forget_recent(target)
            return
        try:
            ws = Workspace.load(target)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Arbeitsstand kann nicht geladen werden", str(exc))
            return
        self._activate_batch(ws)

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
            QMessageBox.warning(self, "Ordner kann nicht bearbeitet werden", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.batch = ws
        self.batch_id += 1
        self.batch_rel = None
        self._awaiting_rel = None
        self._auto_export = False
        self._model_error_shown = False
        self._remember_recent(ws.target)
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
        self.batch.set_analysis(rel, sha256=result["sha256"], size=result["size"], mtime_ns=result["mtime_ns"],
                                findings=findings, warnings=result["warnings"], mode=result["mode"],
                                text_sha=result.get("text_sha", ""), ocr_pages=result.get("ocr_pages"))
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
                self._export_entry(rel, reviewed=False, findings=findings)
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
            QMessageBox.critical(self, "Analyse nicht möglich", message)
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
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            leftovers = self._export_entry(rel, True, self.session.findings, self.session.doc)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export fehlgeschlagen", f"{rel}:\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        self.session.dirty = False
        self._save_batch()
        if leftovers:
            QMessageBox.warning(
                self, "Prüfung: Reste gefunden",
                f"{rel} wurde exportiert, aber die Kontrolle hat im Ergebnis noch Folgendes gefunden:\n\n• "
                + "\n• ".join(leftovers[:20]) + "\n\nDer Hinweis steht im Protokoll.")
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
        failed = requeued = 0
        for i, e in enumerate(ready):
            if dlg.wasCanceled():
                break
            dlg.setLabelText(e.rel)
            dlg.setValue(i)
            QApplication.processEvents()
            try:
                if e.rel == self.batch_rel and self.session is not None:
                    self._export_entry(e.rel, False, self.session.findings, self.session.doc)
                else:
                    doc = self._load(self.batch.abs(e.rel))
                    findings = self.batch.restore_findings(e.rel, doc)
                    if findings is None:  # Text passt nicht mehr → neu analysieren, dann exportieren
                        self.runner.enqueue_file(self.batch.abs(e.rel), e.rel, self.settings, self.batch_id)
                        waiting.append(e)
                        requeued += 1
                        continue
                    self._export_entry(e.rel, False, findings, doc)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.batch.set_error(e.rel, f"Export fehlgeschlagen: {exc}")
        dlg.setValue(len(ready))
        if waiting and not dlg.wasCanceled():
            self._auto_export = True
        self._save_batch()
        self._refresh_batch()
        self.status_label.setText(
            f"{len(ready) - failed - requeued} Datei(en) automatisch exportiert"
            + (f", {len(waiting)} folgen nach der Analyse" if self._auto_export else "")
            + (f", {failed} Fehler" if failed else "")
            + (f", {len(ocr_skipped)} mit Texterkennung bleiben zur Prüfung stehen" if ocr_skipped else "") + ".")

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

    # ------------------------------------------------------------------ Zuletzt bearbeitet
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

    def _open_path(self, path: Path | None) -> None:
        if path is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not path.exists():
            QMessageBox.information(self, "Nicht vorhanden", f"{path} existiert (noch) nicht.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ================================================================== Fenster
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                self.open_folder_dialog(str(p))
                return
            if p.suffix.lower() in SUPPORTED_SUFFIXES:
                self.open_file(p)
                return
        QMessageBox.information(self, "Nicht unterstützt", "Bitte eine PDF-, TXT- oder Markdown-Datei oder einen Ordner ablegen.")

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        if self.batch:
            self._store_batch_session()
            self._save_batch()
        self.runner.shutdown()
        e.accept()
