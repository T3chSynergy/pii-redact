"""Anwenderhilfe (Hilfe → Anwenderhilfe, F1).

Der Text liegt als HTML in ``resources/hilfe.html``. Die Tabelle der Datenarten und die Versionsnummer
werden beim Öffnen eingesetzt, damit die Hilfe immer zum Programmstand passt.
"""

from __future__ import annotations

import html
import re
import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
)

from .. import __version__
from ..about import components_html
from ..core.entities import ENTITIES, info

HELP_FILE = Path(__file__).resolve().parent.parent / "resources" / "hilfe.html"

# Farben für helles und dunkles Windows-Design. QTextBrowser kennt keine CSS-Variablen oder
# Media-Queries – deshalb wird das Stylesheet passend zum aktuellen Design erzeugt.
LIGHT = dict(text="#212529", muted="#6c757d", th_bg="#e9ecef", border="#adb5bd", code_bg="#f1f3f5",
             hinweis_bg="#fff3bf", hinweis_fg="#5c3c00", wichtig_bg="#ffe3e3", wichtig_fg="#7d1a1a",
             tipp_bg="#e7f5ff", tipp_fg="#0b4f8a", link="#1971c2", page_bg="#ffffff",
             st_review="#d9480f", st_done="#2b8a3e", st_auto="#1971c2", st_error="#c92a2a")
DARK = dict(text="#e9ecef", muted="#adb5bd", th_bg="#3b4048", border="#5c636a", code_bg="#3b4048",
            hinweis_bg="#4a3b00", hinweis_fg="#ffe8a3", wichtig_bg="#5a1e1e", wichtig_fg="#ffd8d8",
            tipp_bg="#10385a", tipp_fg="#d0ebff", link="#74c0fc", page_bg="#1e1e1e",
            st_review="#ffa94d", st_done="#69db7c", st_auto="#74c0fc", st_error="#ff8787")

_RULES = """
  body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 140%; color: {text}; }}
  h1 {{ font-size: 18pt; }}
  h2 {{ font-size: 14pt; margin-top: 22px; }}
  h3 {{ font-size: 11.5pt; margin-top: 14px; }}
  a {{ color: {link}; }}
  table {{ border-color: {border}; }}
  td, th {{ padding: 3px 6px; vertical-align: top; border-color: {border}; }}
  th {{ background-color: {th_bg}; color: {text}; text-align: left; }}
  .hinweis {{ background-color: {hinweis_bg}; color: {hinweis_fg}; padding: 6px; }}
  .wichtig {{ background-color: {wichtig_bg}; color: {wichtig_fg}; padding: 6px; }}
  .tipp {{ background-color: {tipp_bg}; color: {tipp_fg}; padding: 6px; }}
  .klein {{ color: {muted}; font-size: 9pt; }}
  kbd, code {{ font-family: Consolas, "Courier New", monospace; background-color: {code_bg}; color: {text}; }}
  .st-review {{ color: {st_review}; }}
  .st-done {{ color: {st_done}; }}
  .st-auto {{ color: {st_auto}; }}
  .st-error {{ color: {st_error}; }}
"""


def help_css(dark: bool) -> str:
    return _RULES.format(**(DARK if dark else LIGHT))


def browser_css() -> str:
    """Für den Standardbrowser: hell, bei dunklem System-Design automatisch dunkel."""
    return (
        f"body {{ background-color: {LIGHT['page_bg']}; max-width: 980px; margin: 24px auto; line-height: 145%; }}"
        + _RULES.format(**LIGHT)
        + "@media (prefers-color-scheme: dark) {"
        + f"body {{ background-color: {DARK['page_bg']}; }}"
        + _RULES.format(**DARK)
        + "}"
        + "table { border-collapse: collapse; }"
    )


def is_dark(palette=None) -> bool:
    from PySide6.QtGui import QGuiApplication, QPalette

    pal = palette or QGuiApplication.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def _entity_table() -> str:
    rows = []
    for key, e in ENTITIES.items():
        if key == "CUSTOM":
            continue
        on = "✔ voreingestellt" if e.default_on else "aus (in den Einstellungen zuschaltbar)"
        rows.append(f"<tr><td>{html.escape(e.label)}</td><td><code>[{html.escape(e.token)}]</code></td><td>{on}</td></tr>")
    return (
        '<table border="1" cellspacing="0" cellpadding="4" width="100%">'
        "<tr><th>Datenart</th><th>Platzhalter</th><th>Standard</th></tr>" + "".join(rows) + "</table>"
        f"<p>Mit der Sperrliste oder manuell ergänzte Stellen erscheinen als "
        f"<code>[{html.escape(info('CUSTOM').token)}]</code>.</p>"
    )


def load_help_html(dark: bool = False, for_browser: bool = False) -> str:
    try:
        text = HELP_FILE.read_text(encoding="utf-8")
    except OSError:
        return "<h1>Hilfe nicht gefunden</h1><p>Die Datei hilfe.html fehlt in der Installation.</p>"
    css = browser_css() if for_browser else help_css(dark)
    return (text.replace("{{VERSION}}", __version__)
            .replace("{{DATENARTEN}}", _entity_table())
            .replace("{{KOMPONENTEN}}", components_html())
            .replace("{{STYLE}}", css))


def chapters(text: str) -> list[tuple[str, str]]:
    """(Anker, Titel) aller Kapitel – aus den <h2>-Überschriften."""
    return [(m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip())
            for m in re.finditer(r'<h2><a name="([^"]+)"></a>(.*?)</h2>', text, re.S)]


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("pii-redact – Anwenderhilfe")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1050, 780)
        self._html = load_help_html(dark=is_dark(self.palette()))

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setHtml(self._html)

        self.toc = QListWidget()
        self.toc.setMaximumWidth(320)
        self.toc.setSpacing(2)
        top = QListWidgetItem("Übersicht")
        top.setData(Qt.ItemDataRole.UserRole, "")
        self.toc.addItem(top)
        for anchor, title in chapters(self._html):
            it = QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, anchor)
            self.toc.addItem(it)
        self.toc.currentItemChanged.connect(self._on_toc)

        back = QToolButton(text="◀ Zurück")
        back.clicked.connect(self.browser.backward)
        self.browser.backwardAvailable.connect(back.setEnabled)
        back.setEnabled(False)
        self.search = QLineEdit(placeholderText="In der Hilfe suchen … (Enter = weiter)", clearButtonEnabled=True)
        self.search.returnPressed.connect(self.find_next)
        btn_find = QPushButton("Suchen")
        btn_find.clicked.connect(self.find_next)
        btn_browser = QPushButton("Im Browser öffnen")
        btn_browser.setToolTip("Hilfe im Standardbrowser öffnen – z. B. zum Drucken oder Speichern als PDF")
        btn_browser.clicked.connect(self.open_in_browser)
        btn_close = QPushButton("Schließen")
        btn_close.clicked.connect(self.close)

        bar = QHBoxLayout()
        bar.addWidget(back)
        bar.addWidget(self.search, 1)
        bar.addWidget(btn_find)
        bar.addSpacing(12)
        bar.addWidget(btn_browser)
        bar.addWidget(btn_close)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 800])

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(split, 1)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.search.setFocus)

    # ------------------------------------------------------------------
    def show_chapter(self, anchor: str = "") -> None:
        if anchor:
            self.browser.scrollToAnchor(anchor)
            for i in range(self.toc.count()):
                if self.toc.item(i).data(Qt.ItemDataRole.UserRole) == anchor:
                    self.toc.blockSignals(True)
                    self.toc.setCurrentRow(i)
                    self.toc.blockSignals(False)
        else:
            self.browser.verticalScrollBar().setValue(0)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_toc(self, item, _prev) -> None:
        if item is None:
            return
        anchor = item.data(Qt.ItemDataRole.UserRole)
        if anchor:
            self.browser.scrollToAnchor(anchor)
        else:
            self.browser.verticalScrollBar().setValue(0)

    def find_next(self) -> bool:
        text = self.search.text().strip()
        if not text:
            return False
        if self.browser.find(text):
            return True
        # vom Anfang weitersuchen
        cur = self.browser.textCursor()
        cur.movePosition(cur.MoveOperation.Start)
        self.browser.setTextCursor(cur)
        return self.browser.find(text, QTextDocument.FindFlag(0))

    def changeEvent(self, e):
        # Wechsel zwischen hellem und dunklem Design während die Hilfe offen ist
        from PySide6.QtCore import QEvent

        if e.type() == QEvent.Type.PaletteChange and hasattr(self, "browser"):
            pos = self.browser.verticalScrollBar().value()
            self._html = load_help_html(dark=is_dark(self.palette()))
            self.browser.setHtml(self._html)
            self.browser.verticalScrollBar().setValue(pos)
        super().changeEvent(e)

    def open_in_browser(self) -> None:
        target = Path(tempfile.gettempdir()) / "pii-redact-hilfe.html"
        target.write_text(load_help_html(for_browser=True), encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

