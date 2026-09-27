"""Einstellungsdialog."""

from __future__ import annotations

import copy
import os
import threading

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import AnalysisMode, ReplaceMode, Settings
from ..core import llm_review
from ..paths import list_ner_models, ner_model_info
from ..core.entities import all_keys, info

MODELS = [
    ("de_core_news_sm", "klein – schnell, weniger genau (~15 MB)"),
    ("de_core_news_md", "mittel – empfohlen (~45 MB)"),
    ("de_core_news_lg", "groß – am genauesten (~550 MB)"),
]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.resize(640, 560)
        self._orig = settings
        s = copy.deepcopy(settings)

        # ---- Erkennung
        self.threshold = QDoubleSpinBox(minimum=0.05, maximum=1.0, singleStep=0.05, decimals=2, value=s.threshold)
        self.threshold.setToolTip("Funde mit geringerer Sicherheit werden ignoriert. Niedriger = mehr Funde, mehr Fehlalarme.")
        self.model = QComboBox(editable=True)
        for name, desc in MODELS:
            self.model.addItem(f"{name}", name)
            self.model.setItemData(self.model.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        self.model.setCurrentText(s.spacy_model)
        self.mode = QComboBox()
        for key, label in ReplaceMode.LABELS.items():
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.replace_mode)))
        self.analysis = QComboBox()
        for key, label in AnalysisMode.LABELS.items():
            self.analysis.addItem(label, key)
        self.analysis.setCurrentIndex(max(0, self.analysis.findData(s.analysis_mode)))
        self.ner_model = QComboBox()
        models = list_ner_models()
        for m in models:
            meta = ner_model_info(m)
            self.ner_model.addItem(f"{m.name}  ({meta.get('source', '?')})", m.name)
            self.ner_model.setItemData(self.ner_model.count() - 1,
                                       f"Lizenz: {meta.get('license') or 'unbekannt'} · {m}", Qt.ItemDataRole.ToolTipRole)
        if not models:
            self.ner_model.addItem("(kein Modell installiert)", "")
            self.ner_model.setEnabled(False)
            self.analysis.model().item(1).setEnabled(False)
        self.ner_model.setCurrentIndex(max(0, self.ner_model.findData(s.ner_model)))
        self.pdf_labels = QCheckBox("Platzhalter im PDF-Schwärzungsbalken anzeigen", checked=s.pdf_labels)
        self.ocr = QCheckBox("Gescannte PDF-Seiten per Texterkennung (OCR) lesen", checked=s.ocr)
        self.ocr.setToolTip(
            "Nur für Seiten ohne Textebene (z. B. eingescannte Mail-Anhänge). Die Texterkennung ist "
            "fehleranfälliger – solche Seiten werden deutlich gekennzeichnet und müssen gründlich geprüft werden.\n"
            "Wirkt beim nächsten Öffnen einer Datei."
        )
        self.compact = QCheckBox("Kompakte Hinweise (für häufige Nutzung)", checked=s.compact_notices)
        self.compact.setToolTip(
            "Hinweise erscheinen nur noch als Zähler in der Statusleiste und als Symbol an der betroffenen "
            "Seite – auch die Leiste für gescannte Seiten entfällt.\n"
            "Die Rückfrage vor dem Export von Seiten aus der Texterkennung bleibt immer."
        )

        form = QFormLayout()
        form.addRow("Mindest-Score:", self.threshold)
        form.addRow("spaCy-Modell:", self.model)
        form.addRow("Analyse-Modus:", self.analysis)
        form.addRow("Transformer-Modell:", self.ner_model)
        form.addRow("Ersetzung:", self.mode)
        form.addRow("", self.pdf_labels)
        form.addRow("", self.ocr)
        form.addRow("", self.compact)
        general = QGroupBox("Allgemein")
        general.setLayout(form)

        # ---- Entitäten
        self.entities = QListWidget()
        for key in all_keys():
            if key == "CUSTOM" or not info(key).searchable:
                continue
            item = QListWidgetItem(info(key).label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in s.entities else Qt.CheckState.Unchecked)
            self.entities.addItem(item)
        ent_box = QGroupBox("Gesuchte Datenarten")
        QVBoxLayout(ent_box).addWidget(self.entities)

        # ---- Listen
        self.allow = QPlainTextEdit("\n".join(s.allow_list))
        self.allow.setPlaceholderText("z. B. Firmenname, eigene Ortsangaben …")
        self.deny = QPlainTextEdit("\n".join(s.deny_list))
        self.deny.setPlaceholderText("z. B. Projektnamen, Spitznamen …")
        lists = QWidget()
        ll = QVBoxLayout(lists)
        ll.setContentsMargins(0, 0, 0, 0)
        if s.org_allow_list or s.org_deny_list:
            org = QLabel(f"Zusätzlich gelten zentrale Vorgaben der Organisation: {len(s.org_allow_list)} Ausnahmen, "
                         f"{len(s.org_deny_list)} Sperrbegriffe.")
            org.setWordWrap(True)
            org.setStyleSheet("color: gray")
            ll.addWidget(org)
        ll.addWidget(QLabel("<b>Ausnahmen</b> – nie schwärzen (ein Begriff pro Zeile):"))
        ll.addWidget(self.allow)
        ll.addWidget(QLabel("<b>Sperrliste</b> – immer schwärzen (ein Begriff pro Zeile):"))
        ll.addWidget(self.deny)

        middle = QHBoxLayout()
        middle.addWidget(ent_box, 1)
        middle.addWidget(lists, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        ki_page = self._build_ki(s)

        # Von der IT gesperrte Einstellungen (defaults.json → "locked")
        widgets = {"threshold": self.threshold, "spacy_model": self.model, "replace_mode": self.mode,
                   "analysis_mode": self.analysis, "ner_model": self.ner_model, "pdf_labels": self.pdf_labels,
                   "ocr": self.ocr, "compact_notices": self.compact, "entities": self.entities,
                   "llm_enabled": self.llm_enabled, "llm_url": self.llm_url, "llm_model": self.llm_model,
                   "llm_api_key": self.llm_key, "llm_timeout": self.llm_timeout, "llm_max_chars": self.llm_chars}
        for key in s.locked:
            w = widgets.get(key)
            if w is not None:
                w.setEnabled(False)
                w.setToolTip("Von der IT festgelegt")

        main_page = QWidget()
        ml = QVBoxLayout(main_page)
        ml.addWidget(general)
        ml.addLayout(middle, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(main_page, "Allgemein")
        self.tabs.addTab(ki_page, "KI-Nachprüfung")
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(buttons)

    def _build_ki(self, s: Settings) -> QWidget:
        page = QWidget()
        intro = QLabel(
            "<b>Optional.</b> Ein Sprachmodell bewertet auf Knopfdruck das <b>geschwärzte</b> Ergebnis: Sind "
            "Personen trotzdem erkennbar – durch übersehene Angaben oder den Zusammenhang? Seine Hinweise erscheinen "
            "als nicht aktivierte Vorschläge. Das Original wird nie gesendet.<br><br>"
            "Benötigt wird ein Server mit OpenAI-kompatibler Schnittstelle – direkt oder über ein LLM-Portal/-Proxy. "
            "<b>Nur einen von Ihrer Organisation freigegebenen Server eintragen</b> – übersehene Angaben im "
            "Ergebnis verlassen dabei den Rechner.")
        intro.setWordWrap(True)
        self.llm_enabled = QCheckBox("KI-Nachprüfung einschalten", checked=s.llm_enabled)
        self.llm_url = QLineEdit(s.llm_url)
        self.llm_url.setPlaceholderText("z. B. https://llm.intern.example/v1")
        self.llm_model = QLineEdit(s.llm_model)
        self.llm_model.setPlaceholderText("Modellname laut Server, z. B. qwen3-27b")
        self.llm_key = QLineEdit(s.llm_api_key)
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        env = bool(os.environ.get(llm_review.KEY_ENV))
        self.llm_key.setPlaceholderText(f"wird aus der Umgebungsvariable {llm_review.KEY_ENV} gelesen" if env
                                        else "falls der Server einen verlangt")
        self.llm_key.setToolTip(f"Wird im Klartext im Benutzerprofil gespeichert. Besser: Umgebungsvariable "
                                f"{llm_review.KEY_ENV} setzen (hat Vorrang).")
        self.llm_timeout = QSpinBox(minimum=10, maximum=1800, suffix=" s", value=int(s.llm_timeout))
        self.llm_chars = QSpinBox(minimum=2000, maximum=500000, singleStep=2000, value=int(s.llm_max_chars))
        self.llm_chars.setToolTip("Längere Dokumente werden (bei PDFs seitenweise) auf mehrere Anfragen verteilt.")
        self.test_btn = QPushButton("Verbindung testen")
        self.test_btn.clicked.connect(self._test_llm)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        form = QFormLayout()
        form.addRow("", self.llm_enabled)
        form.addRow("Server-Adresse:", self.llm_url)
        form.addRow("Modell:", self.llm_model)
        form.addRow("API-Schlüssel:", self.llm_key)
        form.addRow("Zeitlimit je Anfrage:", self.llm_timeout)
        form.addRow("Zeichen je Anfrage:", self.llm_chars)
        row = QHBoxLayout()
        row.addWidget(self.test_btn)
        row.addWidget(self.test_result, 1)
        lay = QVBoxLayout(page)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _ki_settings(self) -> Settings:
        s = copy.deepcopy(self._orig)
        s.llm_enabled = True
        s.llm_url = self.llm_url.text().strip()
        s.llm_model = self.llm_model.text().strip()
        s.llm_api_key = self.llm_key.text().strip()
        s.llm_timeout = self.llm_timeout.value()
        s.llm_max_chars = self.llm_chars.value()
        return s

    def _test_llm(self) -> None:
        s = self._ki_settings()
        if not (s.llm_url and s.llm_model):
            self.test_result.setText("Bitte Server-Adresse und Modell eintragen.")
            return
        self.test_result.setText(f"Teste mit einer Anfrage über {s.llm_max_chars:,} Zeichen … (bis zu "
                                 f"{s.llm_timeout} s)".replace(",", "."))
        self.test_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        state: dict = {}

        def work() -> None:
            try:
                state["msg"] = "✓ " + llm_review.test_connection(s)
            except Exception as exc:  # noqa: BLE001
                state["msg"] = f"✗ {exc}"

        th = threading.Thread(target=work, daemon=True)
        th.start()
        try:
            while th.is_alive():  # Dialog bleibt bedienbar
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                th.join(0.05)
        finally:
            QApplication.restoreOverrideCursor()
            self.test_btn.setEnabled(True)
        self.test_result.setText(state.get("msg", ""))

    def result_settings(self) -> Settings:
        s = copy.deepcopy(self._orig)
        s.threshold = round(self.threshold.value(), 2)
        s.spacy_model = self.model.currentText().strip() or "de_core_news_md"
        s.replace_mode = self.mode.currentData()
        s.analysis_mode = self.analysis.currentData()
        s.ner_model = self.ner_model.currentData() or ""
        if len(list_ner_models()) <= 1:
            s.ner_model = ""  # nur ein Modell → automatisch wählen (robust bei Modell-Updates)
        s.pdf_labels = self.pdf_labels.isChecked()
        s.ocr = self.ocr.isChecked()
        s.compact_notices = self.compact.isChecked()
        s.llm_enabled = self.llm_enabled.isChecked()
        s.llm_url = self.llm_url.text().strip()
        s.llm_model = self.llm_model.text().strip()
        s.llm_api_key = self.llm_key.text().strip()
        s.llm_timeout = self.llm_timeout.value()
        s.llm_max_chars = self.llm_chars.value()
        s.entities = [
            self.entities.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.entities.count())
            if self.entities.item(i).checkState() == Qt.CheckState.Checked
        ]
        s.allow_list = _lines(self.allow.toPlainText())
        s.deny_list = _lines(self.deny.toPlainText())
        return s


def _lines(text: str) -> list[str]:
    seen, out = set(), []
    for line in text.splitlines():
        line = line.strip()
        if line and line.casefold() not in seen:
            seen.add(line.casefold())
            out.append(line)
    return out


def needs_reanalysis(old: Settings, new: Settings) -> bool:
    return (
        old.threshold != new.threshold
        or old.spacy_model != new.spacy_model
        or old.analysis_mode != new.analysis_mode
        or old.ner_model != new.ner_model
        or set(old.entities) != set(new.entities)
        or old.allow_list != new.allow_list
        or old.deny_list != new.deny_list
    )
