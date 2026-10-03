"""Hauptfenster: Original und bearbeitete Fassung nebeneinander, Fundliste rechts."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QEventLoop, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
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

from ..core import AnalysisMode, ReplaceMode, Settings, UnsupportedFileError, load_document
from ..core import llm_review
from ..core.loaders import SUPPORTED_SUFFIXES
from ..core.redactor import pdf_redaction_plan, rects_of
from ..paths import find_ner_model, ner_model_info
from ..settings_store import defaults_path, settings_path
from .batch_panel import BatchPanel, ReviewBar
from .findings_panel import FindingsPanel
from .ki_panel import KiReviewPanel
from .notices import NoticeBar, NoticeButton, page_marks
from .pdf_view import Overlay, PdfPagesView
from .session import DocumentSession
from .settings_dialog import SettingsDialog, needs_reanalysis
from .text_view import HighlightTextView
from .window_batch import BatchMixin
from .window_edit import EditMixin
from .window_export import ExportMixin
from .window_ki import KiMixin
from .window_view import ZOOM_PRESETS, ViewMixin
from .worker import AnalysisRunner

FILE_FILTER = "Dokumente (*.pdf *.txt *.md *.markdown *.text *.log);;PDF (*.pdf);;Text (*.txt);;Markdown (*.md *.markdown)"


class DocPanel(QWidget):
    """Überschrift + Tabs (Seiten / Text) für eine Seite des Vergleichs."""

    def __init__(self, title: str, mode: str, parent=None):
        super().__init__(parent)
        self.title = QLabel(f"<b>{title}</b>")
        self.setAccessibleName(title)
        self.header = QHBoxLayout()
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.pages = PdfPagesView(mode)
        self.text = HighlightTextView()
        self.text.setAccessibleName(f"{title} – Text")
        self.tabs.setAccessibleName(f"{title}: Ansicht wählen")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(self.header)
        lay.addWidget(self.tabs, 1)

    def configure(self, is_pdf: bool) -> None:
        self.tabs.clear()
        if is_pdf:
            self.tabs.addTab(self.pages, "Seiten")
        self.tabs.addTab(self.text, "Text")


class MainWindow(BatchMixin, KiMixin, EditMixin, ViewMixin, ExportMixin, QMainWindow):
    """Hauptfenster. Die Teilbereiche stehen in den ``window_*``-Modulen (Mixins)."""

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
        self._analysis_session = None
        self._init_batch()
        self._init_ki()

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
        self.notice_bar.detailsRequested.connect(lambda: self.notice_btn.show_popup())

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
        self.status_label.setAccessibleName("Status")
        self.status_label.linkActivated.connect(self._on_status_link)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setAccessibleName("Fortschritt der Analyse")
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
        self.zoom_out_btn.setAccessibleName("Seitenansicht verkleinern")
        self.zoom_in_btn.setAccessibleName("Seitenansicht vergrößern")
        self.zoom_box.setAccessibleName("Zoom der Seitenansicht")
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
        # Tastatur: Textcursor auf einem Fund wählt ihn aus (wie ein Klick) → Angaben unter der Fundliste
        self.left.text.caretAt.connect(lambda off: self._on_left_text_click(off, None))
        self.right.text.caretAt.connect(lambda off: self._on_right_text_click(off, None))
        self.findings.detail_provider = self._ids_tooltip
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
        act("open", "Ö&ffnen …", self.open_dialog, QKeySequence.StandardKey.Open, SP.SP_DialogOpenButton)
        act("open_folder", "&Ordner bearbeiten …", lambda: self.open_folder_dialog(), "Ctrl+Shift+O",
            SP.SP_DirOpenIcon, "Alle Dateien eines Ordners analysieren, prüfen und exportieren")
        act("resume_folder", "Ordner-Arbeit fo&rtsetzen …", self.resume_folder_dialog)
        act("close_folder", "Ordner-Arbeit &schließen", self.close_batch)
        act("confirm_next", "&Geprüft && weiter", self.confirm_and_next, "Ctrl+Return")
        act("prev_file", "&Vorherige Datei", lambda: self._step_file(-1), "Alt+Left")
        act("next_file", "Nä&chste Datei", lambda: self._step_file(+1), "Alt+Right")
        act("reanalyze", "&Neu analysieren", self.start_analysis, "F5", SP.SP_BrowserReload,
            "Dokument erneut prüfen (manuelle Änderungen bleiben erhalten)")
        act("ki_review", "KI-&Prüfung", self.start_ki_review, "Ctrl+K", SP.SP_MessageBoxQuestion,
            "Geschwärztes Ergebnis von einem Sprachmodell bewerten lassen: Sind Personen trotzdem erkennbar?")
        act("export_text", "Als &Text/Markdown exportieren …", self.export_text, "Ctrl+Shift+S")
        act("export_pdf", "Als geschwärztes &PDF exportieren …", self.export_pdf, QKeySequence.StandardKey.Save)
        act("quit", "&Beenden", self.close, QKeySequence.StandardKey.Quit)
        act("undo", "&Rückgängig", self.undo, QKeySequence.StandardKey.Undo, SP.SP_ArrowBack)
        act("redo", "&Wiederholen", self.redo, QKeySequence.StandardKey.Redo, SP.SP_ArrowForward)
        act("mark", "&Markierung schwärzen …", lambda: self._mark_selection(False), "Ctrl+R")
        act("mark_all", "Alle Vor&kommen der Markierung schwärzen …", lambda: self._mark_selection(True), "Ctrl+Shift+R")
        act("all_on", "&Alle Funde schwärzen", lambda: self._set_all(True))
        act("all_off", "Kei&nen Fund schwärzen", lambda: self._set_all(False))
        act("settings", "&Einstellungen …", self.open_settings, "Ctrl+,", SP.SP_FileDialogDetailedView)
        act("zoom_in", "&Vergrößern", lambda: self.zoom_step(+1), QKeySequence.StandardKey.ZoomIn)
        act("zoom_out", "Ver&kleinern", lambda: self.zoom_step(-1), QKeySequence.StandardKey.ZoomOut)
        act("zoom_width", "&Seitenbreite", lambda: self.set_zoom_mode("breite"), "Ctrl+0")
        act("zoom_page", "&Ganze Seite", lambda: self.set_zoom_mode("seite"), "Ctrl+2")
        act("zoom_100", "Originalgröße (&100 %)", lambda: self.set_zoom_mode("fest", 100), "Ctrl+1")
        act("about", "Ü&ber pii-redact", self.about)
        act("help", "&Anwenderhilfe", lambda: self.show_help(), "F1", SP.SP_DialogHelpButton)
        act("help_keys", "&Tastenkürzel", lambda: self.show_help("tasten"))
        act("help_folder", "&Ordner bearbeiten – Anleitung", lambda: self.show_help("ordner"))
        a["show_original"] = QAction(st.standardIcon(SP.SP_FileDialogContentsView), "&Original anzeigen", self,
                                     checkable=True, checked=self.settings.show_original)
        a["show_original"].setShortcut(QKeySequence("Ctrl+Shift+V"))
        a["show_original"].setToolTip("Originaldokument neben der bearbeiteten Fassung einblenden (Strg+Umschalt+V)")
        a["show_original"].toggled.connect(self._set_show_original)
        a["sync"] = QAction("Scrollen ko&ppeln", self, checkable=True, checked=self.settings.sync_scroll)
        a["sync"].toggled.connect(self._set_sync)

        # Menüs
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        m.addActions([a["open"], a["open_folder"], a["resume_folder"]])
        self.recent_menu = m.addMenu("&Zuletzt bearbeitete Ordner")
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
        self.mode_box.setAccessibleName("Ersetzung")
        tb.addWidget(self.mode_box)
        tb.addSeparator()
        tb.addWidget(QLabel(" Analyse: "))
        self.analysis_box = QComboBox()
        self.analysis_box.addItem("Schnell", AnalysisMode.FAST)
        self.analysis_box.addItem("Gründlich", AnalysisMode.THOROUGH)
        self.analysis_box.setItemData(0, "spaCy + Muster – Sekundenbruchteile pro Seite", Qt.ItemDataRole.ToolTipRole)
        self._refresh_analysis_box()
        self.analysis_box.currentIndexChanged.connect(self._on_analysis_mode_changed)
        self.analysis_box.setAccessibleName("Analyse-Modus")
        tb.addWidget(self.analysis_box)
        tb.addSeparator()
        export_btn_menu = QMenu(self)
        export_btn_menu.addActions([a["export_pdf"], a["export_text"]])
        self.export_action = QAction(st.standardIcon(SP.SP_DialogSaveButton), "E&xportieren", self)
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
        total, _active = self.session.counts()
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

    # ================================================================== Hintergrundarbeit, Titel, Aktionen
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

    # ================================================================== Fenster
    def _open_path(self, path: Path | None) -> None:
        if path is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not path.exists():
            QMessageBox.information(self, "Nicht vorhanden", f"{path} existiert (noch) nicht.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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
