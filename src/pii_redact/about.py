"""Verwendete Open-Source-Komponenten – eine Quelle für „Über pii-redact“, die Anwenderhilfe und
die Lizenzdatei im Programmordner (``tools/lizenzen.py``).

Hier stehen die *wesentlichen* Komponenten mit ihrer Aufgabe. Die vollständige Liste aller
enthaltenen Python-Pakete (samt Lizenztexten) erzeugt ``tools/lizenzen.py`` beim Build.
"""

from __future__ import annotations

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    name: str
    purpose: str
    license: str
    url: str


COMPONENTS: list[tuple[str, list[Component]]] = [
    ("Erkennung personenbezogener Daten", [
        Component("Microsoft Presidio (Analyzer)", "Rahmen für die Erkennung, Muster-Erkenner",
                  "MIT", "https://github.com/microsoft/presidio"),
        Component("spaCy, Thinc", "Sprachverarbeitung (Modus „Schnell“ und Grundlage)",
                  "MIT", "https://spacy.io"),
        Component("spaCy-Modell de_core_news_md 3.8", "Deutsches Sprachmodell für Namen und Orte",
                  "MIT (Trainingsdaten u. a. TIGER-Korpus, WikiNER CC BY 4.0)",
                  "https://spacy.io/models/de#de_core_news_md"),
        Component("Davlan/xlm-roberta-base-ner-hrl", "KI-Modell für Namen, Orte, Organisationen (Modus „Gründlich“), "
                  "nach ONNX umgewandelt", "AFL-3.0 (Basismodell XLM-RoBERTa: MIT)",
                  "https://huggingface.co/Davlan/xlm-roberta-base-ner-hrl"),
        Component("ONNX Runtime", "Ausführung der KI-Modelle ohne Internet und ohne GPU",
                  "MIT", "https://onnxruntime.ai"),
        Component("Hugging Face Tokenizers", "Zerlegung des Texts für das KI-Modell",
                  "Apache-2.0", "https://github.com/huggingface/tokenizers"),
        Component("phonenumbers", "Erkennung und Prüfung von Telefonnummern",
                  "Apache-2.0", "https://github.com/daviddrysdale/python-phonenumbers"),
        Component("tldextract", "Erkennung von E-Mail- und Web-Adressen",
                  "BSD-3-Clause", "https://github.com/john-kurkowski/tldextract"),
        Component("regex", "Erweiterte reguläre Ausdrücke",
                  "Apache-2.0", "https://github.com/mrabarnett/mrab-regex"),
    ]),
    ("Texterkennung (OCR)", [
        Component("RapidOCR", "Texterkennung gescannter Seiten",
                  "Apache-2.0", "https://github.com/RapidAI/RapidOCR"),
        Component("PaddleOCR-Modelle PP-OCRv6", "KI-Modelle für Texterkennung (Erkennung und Lesen)",
                  "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR"),
        Component("OpenCV", "Bildverarbeitung", "Apache-2.0", "https://opencv.org"),
        Component("Shapely mit GEOS", "Geometrie der Textbereiche",
                  "BSD-3-Clause (GEOS: LGPL-2.1)", "https://github.com/shapely/shapely"),
        Component("pyclipper mit Clipper", "Geometrie der Textbereiche",
                  "MIT (Clipper: Boost Software License 1.0)", "https://github.com/fonttools/pyclipper"),
        Component("OmegaConf, ANTLR-Runtime", "Konfiguration der Texterkennung",
                  "BSD-3-Clause", "https://github.com/omry/omegaconf"),
        Component("Pillow", "Bildformate", "MIT-CMU (HPND)", "https://python-pillow.org"),
    ]),
    ("PDF und Oberfläche", [
        Component("PyMuPDF / MuPDF", "PDF lesen, anzeigen, schwärzen und bereinigen",
                  "GNU AGPL-3.0 oder kommerzielle Lizenz (Artifex)", "https://pymupdf.readthedocs.io"),
        Component("Qt 6 / PySide6", "Programmoberfläche",
                  "LGPL-3.0 (Qt-Bibliotheken als separate, austauschbare Dateien)", "https://www.qt.io"),
    ]),
    ("Grundlage", [
        Component("Python 3.12", "Programmiersprache und Laufzeit", "PSF-2.0", "https://www.python.org"),
        Component("NumPy", "Numerik", "BSD-3-Clause", "https://numpy.org"),
        Component("pydantic", "Datenprüfung (von spaCy/Presidio genutzt)", "MIT", "https://docs.pydantic.dev"),
        Component("PyYAML", "Konfigurationsdateien", "MIT", "https://pyyaml.org"),
        Component("certifi", "Zertifikatsliste (von Bibliotheken mitgeliefert, pii-redact baut keine "
                  "Verbindungen auf)", "MPL-2.0", "https://github.com/certifi/python-certifi"),
        Component("tqdm", "Fortschrittsanzeige (intern)", "MPL-2.0 und MIT", "https://github.com/tqdm/tqdm"),
        Component("PyInstaller", "Erstellung des Programmpakets",
                  "GPL-2.0 mit Ausnahme für erstellte Programme", "https://pyinstaller.org"),
    ]),
]

NOTES = [
    "PyMuPDF steht unter der GNU AGPL-3.0. Der Einsatz innerhalb der eigenen Organisation gilt in der Regel als "
    "unkritisch. Bei einer Weitergabe an Dritte sind die Pflichten der AGPL zu erfüllen (u. a. Bereitstellung "
    "des Quellcodes) oder eine kommerzielle Lizenz bei Artifex zu erwerben.",
    "Die Qt-Bibliotheken (LGPL-3.0) liegen als separate Dateien im Programmordner und können ausgetauscht werden.",
    "Die vollständige Liste aller enthaltenen Bibliotheken mit ihren Lizenztexten steht in den Dateien "
    "LIZENZEN.txt und LIZENZTEXTE.txt im Programmordner.",
]


def all_components() -> list[Component]:
    return [c for _group, items in COMPONENTS for c in items]


def components_html(links: bool = True) -> str:
    """Tabelle für Hilfe und „Über“-Dialog (einfaches HTML, von QTextBrowser darstellbar)."""
    rows = []
    for group, items in COMPONENTS:
        rows.append(f'<tr><th colspan="3" align="left">{html.escape(group)}</th></tr>')
        for c in items:
            name = html.escape(c.name)
            if links:
                name = f'<a href="{html.escape(c.url)}">{name}</a>'
            rows.append(f"<tr><td>{name}</td><td>{html.escape(c.purpose)}</td><td>{html.escape(c.license)}</td></tr>")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in NOTES)
    return (
        '<table border="1" cellspacing="0" cellpadding="4" width="100%">'
        '<tr><th width="30%">Komponente</th><th>Aufgabe</th><th width="28%">Lizenz</th></tr>'
        + "".join(rows) + "</table>"
        + f"<ul>{notes}</ul>"
    )


def components_text() -> str:
    """Dieselbe Übersicht als Text (für LIZENZEN.txt)."""
    out = []
    for group, items in COMPONENTS:
        out.append(f"{group}\n" + "-" * len(group))
        for c in items:
            out.append(f"  {c.name}\n      {c.purpose}\n      Lizenz: {c.license}\n      {c.url}")
        out.append("")
    out.append("Hinweise")
    out.append("--------")
    out += [f"  * {n}" for n in NOTES]
    return "\n".join(out)
