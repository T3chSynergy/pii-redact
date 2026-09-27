"""„Über pii-redact“: Version, installierte Modelle, Open-Source-Komponenten und Lizenzen."""

from __future__ import annotations

import html

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from .. import __version__
from ..about import LICENSE, SOURCE_URL, components_html
from ..paths import app_dir, find_ner_model, find_spacy_model, ner_model_info
from .help_window import help_css, is_dark


def _version(module: str, attr: str = "__version__") -> str:
    try:
        mod = __import__(module)
        return str(getattr(mod, attr))
    except Exception:  # noqa: BLE001
        try:
            import importlib.metadata as md

            return md.version(module)
        except Exception:  # noqa: BLE001
            return "?"


def license_files() -> list:
    return [p for p in (app_dir() / "LIZENZEN.txt", app_dir() / "LIZENZTEXTE.txt") if p.is_file()]


def about_html(settings, dark: bool = False) -> str:
    spacy_ref = find_spacy_model(settings.spacy_model)
    ner_dir = find_ner_model(settings.ner_model)
    if ner_dir:
        meta = ner_model_info(ner_dir)
        ner = (f"{html.escape(str(meta.get('source', ner_dir.name)))} – ONNX"
               f"{' 8-Bit' if meta.get('quantized') else ''}, Lizenz {html.escape(str(meta.get('license') or 'siehe MODEL_CARD.md'))}")
    else:
        ner = "nicht installiert – Modus „Gründlich“ nicht verfügbar"
    from ..core import ocr

    from ..core import llm_review

    if llm_review.is_configured(settings):
        ki = (f"eingeschaltet: {html.escape(settings.llm_model)} über {html.escape(llm_review.host_of(settings))} "
              "(sendet auf Knopfdruck nur geschwärzten Text)")
        local = ("<b>lokal</b> – nur die eingeschaltete KI-Nachprüfung sendet auf Knopfdruck den bereits "
                 "geschwärzten Text an den eingestellten Server")
    else:
        ki = "aus"
        local = "<b>vollständig lokal</b>, ohne Internetverbindung"
    ocr_state = f"RapidOCR {_version('rapidocr')} (PP-OCRv6)" if ocr.is_available() else "nicht installiert"
    files = license_files()
    if files:
        lic = " · ".join(f'<a href="{QUrl.fromLocalFile(str(p)).toString()}">{p.name}</a>' for p in files)
        lic_line = f"<p>Vollständige Liste aller Bibliotheken und Lizenztexte: {lic}</p>"
    else:
        lic_line = ("<p class='klein'>LIZENZEN.txt und LIZENZTEXTE.txt entstehen beim Erstellen des "
                    "Programmpakets (build.bat).</p>")
    source = (f' Quellcode: <a href="{html.escape(SOURCE_URL)}">{html.escape(SOURCE_URL)}</a>'
              if SOURCE_URL else "")
    versions = " · ".join([
        f"Python {_version('sys', 'version').split()[0]}",
        f"Presidio {_version('presidio_analyzer')}",
        f"spaCy {_version('spacy')}",
        f"ONNX Runtime {_version('onnxruntime')}",
        f"PyMuPDF {_version('pymupdf', 'VersionBind')}",
        f"Qt/PySide6 {_version('PySide6')}",
    ])
    return f"""<html><head><style>{help_css(dark)}</style></head><body>
<h1>pii-redact {__version__}</h1>
<p>Erkennt personenbezogene Daten in PDF-, TXT- und Markdown-Dateien und entfernt sie –
{local}.</p>
<p class="wichtig">Automatische Erkennung ist nie perfekt – bitte das Ergebnis immer prüfen.</p>
<p>Freie Software unter der {html.escape(LICENSE)} – ohne Gewährleistung.{source}</p>
<h3>Installierte Modelle</h3>
<table border="1" cellspacing="0" cellpadding="4" width="100%">
<tr><td width="30%">Sprachmodell (beide Modi)</td><td>spaCy {html.escape(settings.spacy_model)}{'' if spacy_ref else ' – FEHLT'}</td></tr>
<tr><td>KI-Modell „Gründlich“</td><td>{ner}</td></tr>
<tr><td>Texterkennung (OCR)</td><td>{ocr_state}</td></tr>
<tr><td>KI-Nachprüfung</td><td>{ki}</td></tr>
</table>
<p class="klein">{versions}</p>
<h3>Open-Source-Komponenten</h3>
{components_html()}
{lic_line}
</body></html>"""


class AboutDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Über pii-redact")
        self.resize(860, 720)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)  # Links (Web und Lizenzdateien) im Standardprogramm öffnen
        self.browser.anchorClicked.connect(QDesktopServices.openUrl)
        self.browser.setHtml(about_html(settings, dark=is_dark(self.palette())))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.browser)
        lay.addWidget(buttons)
