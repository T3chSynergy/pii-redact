"""Rückfragen mit deutsch beschrifteten Schaltflächen.

Qt beschriftet seine Standard-Schaltflächen englisch („Yes“, „Cancel“), solange keine Übersetzung geladen
ist. ``app.main`` lädt zwar die deutsche Qt-Übersetzung – fehlt sie in einer Installation, blieben die
Rückfragen aber englisch. Deshalb werden die Schaltflächen hier ausdrücklich beschriftet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

_LABELS = {
    QDialogButtonBox.StandardButton.Ok: "OK",
    QDialogButtonBox.StandardButton.Cancel: "Abbrechen",
    QDialogButtonBox.StandardButton.Close: "Schließen",
    QDialogButtonBox.StandardButton.Yes: "Ja",
    QDialogButtonBox.StandardButton.No: "Nein",
}


def german_buttons(box: QDialogButtonBox) -> QDialogButtonBox:
    """Standard-Schaltflächen einer Schaltflächenleiste deutsch beschriften (eigene Texte bleiben)."""
    for which, text in _LABELS.items():
        button = box.button(which)
        if button is not None:
            button.setText(text)
    return box


def ask_yes_no(parent, title: str, text: str, yes: str = "Ja", no: str = "Nein", default_yes: bool = False) -> bool:
    """Ja/Nein-Rückfrage. Rückgabe: True bei „Ja“ (Schließen des Fensters zählt als „Nein“)."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    yes_button = box.addButton(yes, QMessageBox.ButtonRole.YesRole)
    no_button = box.addButton(no, QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes_button if default_yes else no_button)
    box.setEscapeButton(no_button)
    box.exec()
    return box.clickedButton() is yes_button
