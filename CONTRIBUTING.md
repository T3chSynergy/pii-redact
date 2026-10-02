# Mitarbeiten an pii-redact

Danke für dein Interesse! Fehlerberichte, Hinweise zur Erkennung und Pull Requests sind willkommen.
Diese Seite erklärt, wie du die Entwicklungsumgebung einrichtest und worauf es im Projekt ankommt.

> 🔒 **Sicherheitslücken** (z. B. Reste im geschwärzten PDF, Datenabfluss) bitte **nicht** als Issue oder
> Pull Request, sondern vertraulich melden – siehe [SECURITY.md](SECURITY.md).

## Grundregeln

- **Keine echten Daten.** Nirgends – weder in Code, Tests, Beispieldateien, Issues noch Screenshots. Nur
  erfundene Namen und Nummern sowie Adressen mit `.example`. Beispieldokumente unter `samples/` werden per Skript
  erzeugt (`samples/make_*.py`).
- **Schlank und offline.** Das Programm läuft vollständig auf dem eigenen Rechner. Keine neuen
  Netzwerkzugriffe (einzige Ausnahme: die optionale, standardmäßig ausgeschaltete KI-Nachprüfung) und keine
  schweren Abhängigkeiten wie PyTorch im Programm.
- **Nur klar abgrenzbare Erkennungen.** Angaben, die sich nicht zuverlässig erkennen lassen – z. B.
  Gesundheitsdaten oder Zugehörigkeiten („ist im Betriebsrat“) – werden bewusst nicht automatisch geschwärzt.
- **Deutsch** in Oberfläche, Anwenderhilfe, Dokumentation und Commit-Nachrichten.

## Entwicklungsumgebung (Windows)

Voraussetzung: **Python 3.14** (3.12/3.13 gehen auch) mit py-Launcher, [python.org](https://www.python.org/downloads/).

```bat
git clone https://github.com/T3chSynergy/pii-redact.git
cd pii-redact
git switch dev
setup.bat      :: legt .venv an, installiert Pakete und das deutsche spaCy-Modell
run.bat        :: startet die Oberfläche
```

Für den Modus „Gründlich“ das Transformer-Modell `davlan-xlmr-ner.zip` vom aktuellen
[Release](https://github.com/T3chSynergy/pii-redact/releases) nach `models\ner\` entpacken (ergibt
`models\ner\davlan-xlmr-ner\`). Ohne Modell läuft das Programm im Modus „Schnell“.

Auf anderen Systemen (z. B. für Tests unter Linux/macOS):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m spacy download de_core_news_md
```

## Tests

```bash
pytest
```

Die Oberflächentests laufen ohne Bildschirm (sie setzen `QT_QPA_PLATFORM=offscreen` selbst). Zwei Tests
werden je nach Umgebung übersprungen – das ist normal.

Den fertigen Programmordner baut `build.bat` (Windows); Details in [packaging/README.md](packaging/README.md).

## Ablauf für Änderungen

1. Bei größeren Änderungen zuerst ein **Issue** eröffnen und die Idee abstimmen.
2. Vom Branch **`dev`** abzweigen – `main` enthält nur veröffentlichte Versionen.
3. Änderung umsetzen, **Tests ergänzen** und `pytest` laufen lassen.
4. Bei sichtbaren Änderungen einen Eintrag im [CHANGELOG.md](CHANGELOG.md) unter „Unveröffentlicht“ ergänzen.
   Ändert sich die Bedienung, auch die Anwenderhilfe anpassen (`src/pii_redact/resources/hilfe.html`).
5. **Pull Request gegen `dev`** stellen und die Vorlage ausfüllen. GitHub prüft den PR automatisch
   (Tests auf Windows, Lint, bekannte Sicherheitslücken); gemergt wird nur mit grünem Haken. PRs gegen
   `main` werden automatisch abgelehnt (roter Haken „Ziel-Branch“) – oben „Edit“ → base: `dev` wählen.

## Wo liegt was?

| Bereich | Ort |
|---|---|
| Erkennung (deutsche Muster, Presidio, Modi) | `src/pii_redact/core/recognizers_de.py`, `core/analyzer.py` |
| Datenarten, Anzeigenamen, Platzhalter | `src/pii_redact/core/entities.py` |
| Laden, Schwärzen, PDF-Kontrolle | `src/pii_redact/core/loaders.py`, `core/redactor.py` |
| Ordner-Bearbeitung | `src/pii_redact/core/batch.py` |
| KI-Nachprüfung | `src/pii_redact/core/llm_review.py`, `ui/ki_panel.py` |
| Oberfläche (PySide6) | `src/pii_redact/ui/` |
| Werkzeuge (Modell-Umwandlung, Vergleiche) | `tools/` |

Mehr zum Aufbau: Abschnitt „Aufbau“ in der [README](README.md).

**Neuer Erkenner?** Bitte mit Tests für Treffer *und* für typische Fehlalarme (z. B. Rechnungsnummern, Datumsangaben,
Beträge) – ein Erkenner, der zu viel schwärzt, macht Dokumente unbrauchbar.

## Lizenz

pii-redact steht unter der **GNU AGPL-3.0-or-later** (siehe [LICENSE](LICENSE)). Mit einem Pull Request
erklärst du dich einverstanden, dass dein Beitrag unter derselben Lizenz veröffentlicht wird.
