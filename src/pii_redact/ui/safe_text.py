"""Fremder Text wird nie als HTML angezeigt.

Qt zeigt Text in Beschriftungen, Meldungsfenstern und Hinweistexten (Tooltips) als HTML an, sobald er bis zum
ersten Zeilenumbruch nach einem HTML-Tag aussieht (``Qt.mightBeRichText``). Viele Texte kommen aber von außen: aus
dem Dokument, vom KI-Server, aus dem Arbeitsstand im Zielordner, aus Datei- und Fehlermeldungen. Als HTML gelesen,
könnten sie Formatierungen und Bilder einschleusen – ein ``<img src="//server/freigabe/bild.png">`` lässt Windows
eine Verbindung zu diesem Server aufbauen und sich dort mit dem Konto des Nutzers anmelden.

Deshalb gilt in der Oberfläche:

* Beschriftungen, die fremden Text zeigen und kein HTML brauchen, stehen auf ``Qt.TextFormat.PlainText``.
* Wo Qt selbst entscheidet – Meldungsfenster über ``QMessageBox.warning`` u. Ä., Tooltips –, läuft fremder Text
  durch :func:`plain_text`.
* Wo bewusst HTML gebaut wird (fett, Link), werden fremde Bestandteile mit ``html.escape`` maskiert.
* Die Statusleiste (:class:`StatusLabel`) zeigt immer reinen Text, außer über :meth:`StatusLabel.set_html`.
"""

from __future__ import annotations

import html

from PySide6 import QtGui
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


def plain_text(text: str) -> str:
    """Text so aufbereiten, dass Qt ihn wörtlich zeigt – auch dort, wo Qt selbst entscheidet, ob er HTML ist.

    Sieht der Text nicht nach HTML aus, bleibt er unverändert. Sonst kommt er als HTML mit maskierten Zeichen
    zurück, das genau den ursprünglichen Text zeigt – Leerzeichen und Zeilenumbrüche bleiben erhalten."""
    if not QtGui.Qt.mightBeRichText(text):   # dieselbe Prüfung, die Qt selbst anwendet (gehört zu QtGui)
        return text
    return "<p style='white-space:pre-wrap'>" + html.escape(text, quote=False).replace("\n", "<br>") + "</p>"


class StatusLabel(QLabel):
    """Statuszeile des Hauptfensters. Sie zeigt Dateinamen, Fehlermeldungen und markierten Dokumenttext – deshalb
    immer als reinen Text. Nur :meth:`set_html` (Erfolgsmeldung mit dem Link „Ordner öffnen“) zeigt HTML; fremde
    Bestandteile müssen dort maskiert sein."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)

    def setText(self, text: str) -> None:
        self.setTextFormat(Qt.TextFormat.PlainText)
        super().setText(text)

    def set_html(self, text: str) -> None:
        self.setTextFormat(Qt.TextFormat.RichText)
        super().setText(text)
