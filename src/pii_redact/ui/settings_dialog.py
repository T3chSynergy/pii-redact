"""Einstellungsdialog."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import AnalysisMode, ReplaceMode, Settings
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
            if key == "CUSTOM":
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

        # Von der IT gesperrte Einstellungen (defaults.json → "locked")
        widgets = {"threshold": self.threshold, "spacy_model": self.model, "replace_mode": self.mode,
                   "analysis_mode": self.analysis, "ner_model": self.ner_model, "pdf_labels": self.pdf_labels,
                   "ocr": self.ocr, "compact_notices": self.compact, "entities": self.entities}
        for key in s.locked:
            w = widgets.get(key)
            if w is not None:
                w.setEnabled(False)
                w.setToolTip("Von der IT festgelegt")

        lay = QVBoxLayout(self)
        lay.addWidget(general)
        lay.addLayout(middle, 1)
        lay.addWidget(buttons)

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
