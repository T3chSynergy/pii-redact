# pii-redact

Lokaler Desktop-Client, der **PDF-, TXT- und Markdown-Dokumente** auf personenbezogene Daten prüft,
diese automatisch entfernt und **Original und bearbeitete Fassung nebeneinander** anzeigt. Danach kannst du
jeden Fund prüfen, abwählen, umtypisieren oder eigene Stellen ergänzen und das Ergebnis exportieren.

Alles läuft **offline auf dem eigenen Rechner** – es werden keine Dokumentinhalte übertragen.

## Funktionen

| Bereich | Umfang |
|---|---|
| Formate | PDF, TXT, Markdown – Kodierung UTF-8 / Windows-1252 wird erkannt; gescannte PDF-Seiten ohne Textebene werden per **Texterkennung (OCR)** gelesen (Rückfallebene, deutlich gekennzeichnet) |
| Erkennung | [Microsoft Presidio](https://github.com/microsoft/presidio) + spaCy `de_core_news_md`; Modus **Gründlich** zusätzlich mit Transformer-Modell (ONNX, ohne PyTorch) |
| Datenarten | Personen, Orte, Adressen (Straße + Nr., PLZ + Ort), E-Mail, Telefon (DE/AT/CH), Geburtsdatum*, IBAN, Kreditkarte, IP, Steuer-ID, Steuernummer, Rentenversicherungs-, Krankenversicherten-, Ausweis-, Pass-, Führerscheinnummer, Kfz-Kennzeichen, Arztnummer u. a. |
| Ersetzung | `[PERSON]` · nummeriert `[PERSON_1]` (gleicher Wert = gleiche Nummer) · Schwärzung `████` |
| PDF | **echte Schwärzung** (Text bzw. Bildpunkte werden physisch entfernt, nicht nur überdeckt), zusätzlich Kommentare, Lesezeichen, Metadaten, Links, Anhänge, JavaScript und unsichtbarer Text gelöscht, Formularfelder in festen Inhalt umgewandelt; anschließende Kontrolle auf Reste (bei OCR-Seiten erneut per OCR) |
| Ordner | ganze Ordnerstrukturen mit Arbeitsliste, „Geprüft & weiter“, Fortsetzen über mehrere Tage, CSV-Protokoll ohne Klartext |
| Nachbearbeitung | Fund an/aus, Typ ändern, Text markieren → schwärzen, alle Vorkommen, Bereich im PDF aufziehen, **freie Bereiche schwärzen** (Unterschrift, Foto, Handschrift – auch ohne Text), Ausnahme- und Sperrliste, Rückgängig/Wiederholen, freie Textbearbeitung |

\* Datumsangaben werden nur mit Kontext („geboren“, „geb.“, „Geburtsdatum“ …) geschwärzt, damit nicht jedes Rechnungsdatum verschwindet.

## Installation (Windows)

Voraussetzung: **Python 3.12–3.14** (empfohlen 3.14, [python.org](https://www.python.org/downloads/)).

Quellcode aus dem GitHub-Repository [github.com/T3chSynergy/pii-redact](https://github.com/T3chSynergy/pii-redact) holen – z. B. mit GitHub Desktop
(„Clone repository“) oder:

```bat
git clone https://github.com/T3chSynergy/pii-redact.git
```

Das **Transformer-Modell** für den Modus „Gründlich“ (≈ 280 MB) liegt **nicht** im Repository, sondern als
`davlan-xlmr-ner.zip` am jeweiligen **Release** (GitHub → Releases). Entpacken nach
`models\ner\davlan-xlmr-ner\` (siehe [packaging/README.md](packaging/README.md), „Transformer-Modell“).
Ohne Modell läuft das Programm im Modus „Schnell“.

```bat
setup.bat      :: einmalig – legt .venv an, installiert Pakete und das deutsche Sprachmodell
run.bat        :: startet die Oberfläche (Datei kann auch auf run.bat gezogen werden)
```

Manuell / andere Systeme:

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m spacy download de_core_news_md
python -m pii_redact [datei]
```

## Ordner bearbeiten (viele Dateien, auch über mehrere Tage)

**Datei → Ordner bearbeiten …** (Strg+Umschalt+O) oder einen Ordner ins Fenster ziehen.

1. Quellordner und Zielordner wählen (Vorschlag: `<Ordner>_geschwaerzt`), „Unterordner einbeziehen“ ist an.
2. Links erscheint die **Arbeitsliste**; alle Dateien werden im Hintergrund nacheinander analysiert.
   Die geöffnete Datei wird immer zuerst analysiert.
3. Jede Datei wie gewohnt prüfen und mit **„Geprüft ✓ & weiter“** (Strg+Enter) bestätigen: Sie wird sofort
   in den Zielordner exportiert (gleiche Ordnerstruktur) und die nächste offene Datei öffnet sich.
   Vor/zurück: Alt+Links / Alt+Rechts. Wird eine bereits geprüfte Datei nachträglich geändert, muss sie erneut
   bestätigt werden.
4. **Unterbrechen jederzeit:** Der Stand wird laufend gespeichert. Weitermachen über
   *Datei → Ordner-Arbeit fortsetzen …* oder *Zuletzt bearbeitete Ordner*.
5. Optional **„Ungeprüfte trotzdem exportieren …“**: schwärzt den Rest automatisch – im Protokoll ausdrücklich
   als „automatisch exportiert (nicht geprüft)“ vermerkt. Solche Dateien können später noch geprüft werden.

Im Zielordner:

| Datei | Inhalt |
|---|---|
| `…_geschwaerzt.pdf`, `…_anonymisiert.txt/.md` | bearbeitete Dateien, Ordnerstruktur wie im Quellordner |
| `pii-redact-protokoll.csv` | je Datei: Status, geprüft von/am, Anzahl Funde je Datenart, manuelle Änderungen, Analyse-Modus, SHA-256 des Originals, Hinweise – **ohne** die gefundenen Daten selbst (öffnet direkt in Excel) |
| `pii-redact-arbeitsstand.json` | Arbeitsstand zum Fortsetzen – enthält nur Textpositionen, **keinen Klartext** |

Ändert sich ein Original nach der Analyse (Prüfsumme), verliert es seinen Prüfstatus und wird neu analysiert.
Fehlende Dateien werden markiert. Die freie Textbearbeitung ist in der Ordner-Bearbeitung ausgeblendet, damit
jede Änderung nachvollziehbar über die Fundliste läuft.

Per Kommandozeile (vollautomatisch, nicht geprüft; bereits geprüfte Dateien bleiben unangetastet):

```bash
pii-redact-cli C:\Akten -o C:\Akten_geschwaerzt [--gruendlich] [--ohne-unterordner]
```

## Modus „Schnell“ und „Gründlich“

**Standard ist „Gründlich“.**

| | Gründlich (Standard) | Schnell |
|---|---|---|
| Erkennung | spaCy + Muster, zusätzlich Transformer-Modell (Davlan XLM-R, ONNX int8) für Personen/Orte/Organisationen | spaCy + Muster |
| Personen / Orte gefunden (Benchmark, kleine Testmenge) | 100 % / 100 % | 80 % / 67 % |
| Tempo | ca. 113 ms / 1000 Zeichen (≈ 0,3–1 s pro Seite) | ca. 34 ms / 1000 Zeichen |
| Arbeitsspeicher (zusätzlich) | ca. 1 GB | ca. 0,4 GB |
| Voraussetzung | Modellordner unter `models/ner/<name>` | – |

Umschalten in der Werkzeugleiste (*Analyse*) oder in den Einstellungen; zentral per `defaults.json`
(`"analysis_mode": "schnell"` z. B. für Rechner mit wenig Speicher). Ist kein Transformer-Modell installiert,
ist „Gründlich“ ausgegraut und das Programm verwendet automatisch „Schnell“ (ohne die gewünschte Einstellung zu
überschreiben). Der Client lädt **nie** etwas aus dem Internet.

### Modelle vergleichen und umwandeln (Entwicklung, einmalig mit Internet)

```bat
tools\modelle_testen.bat
```

Legt eine separate Werkzeug-Umgebung `.venv-tools` an (PyTorch CPU, transformers, GLiNER – ca. 3–4 GB),
wandelt `Davlan/xlm-roberta-base-ner-hrl` und `fhswf/bert_de_ner` nach ONNX (8-Bit) um und vergleicht
spaCy, die Originalmodelle, GLiNER und die ONNX-Fassungen. Ergebnis: `benchmark_ergebnis.md`
(Tempo, Arbeitsspeicher, Trefferquote gegen `samples/soll_funde.json`).

Einzeln:

```bash
python tools/convert_model.py Davlan/xlm-roberta-base-ner-hrl --name davlan-xlmr-ner
python tools/benchmark_models.py --nur spacy:de_core_news_md onnx:all app:schnell app:gruendlich
```

`convert_model.py` prüft am Ende, ob das ONNX-Modell dieselben Entitäten liefert wie das Original
(auch im Stapelbetrieb mit Auffüllung). Modelle landen in `models/ner/` (nicht im Git).

Modelle werden gesucht in: `models/` im Programmpaket → `models/` neben der EXE →
`%ProgramData%\pii-redact\models` (sowie zusätzlich `PII_REDACT_MODEL_DIR`).

## Zentrale Vorgaben (Softwareverteilung)

Die IT kann `C:\ProgramData\pii-redact\defaults.json` ablegen (Vorlage: `deploy/defaults.example.json`):
Standard-Modus, Schwelle, Ersetzung usw. gelten als Grundeinstellung; persönliche Einstellungen überschreiben sie.
`allow_list`/`deny_list` der Organisation gelten **immer zusätzlich** zu den persönlichen Listen.
Unter `"locked"` aufgeführte Schlüssel legt die IT fest (in den Einstellungen ausgegraut).

### KI-Nachprüfung (optional, ab 0.5.0)

Ein Sprachmodell bewertet auf Knopfdruck das **geschwärzte** Ergebnis (Restrisiko gering/mittel/hoch, Hinweise
auf übersehene oder indirekt identifizierende Angaben). Gesendet wird nur der Text mit nummerierten Platzhaltern
an einen OpenAI-kompatiblen Server (`llm_url`, `llm_model`, Schlüssel über `PII_REDACT_LLM_KEY`). Hinweise, die
wörtlich im Text stehen, werden zu nicht aktivierten Vorschlägen (Quelle „KI“). Code: `core/llm_review.py`,
`ui/ki_panel.py`; Anforderungen für die IT: `packaging/README.md`.

**Modelle vergleichen:** `tools\ki_vergleich.bat --modell anbieter/modell-a --modell anbieter/modell-b`
(Server und Schlüssel wie in den Einstellungen oder `--url`; `--wiederholungen 3` prüft die Beständigkeit).
Schickt sechs frei erfundene, bereits geschwärzte Testfälle (`tools/ki_testfaelle.json`) an jedes Modell und
zählt: Restrisiko im erwarteten Bereich, erwartete Stellen gefunden (z. B. Funktion + Abteilung, übersehene
Personalnummer, verschleierte E-Mail), Fehlalarme bei harmlosen Stellen, Zitate ohne Beleg, Fehler, Zeit.
Bericht: `ki_vergleich_<Datum>.md` (nicht im Repository). Eigene Testfälle im selben Format mit `--faelle`.

## Bedienung

1. **Öffnen** (Strg+O) oder Datei ins Fenster ziehen → Analyse startet automatisch im Hintergrund.
2. Angezeigt wird die **bearbeitete Fassung** (Platzhalter bzw. Schwärzungsbalken). Mit der Maus über einem
   Platzhalter zeigt ein Tooltip, was dahinter steht; abgewählte Funde bleiben grau gestrichelt erkennbar.
   **Original anzeigen** (Werkzeugleiste, Strg+Umschalt+V) blendet das Originaldokument links daneben ein –
   standardmäßig aus, die Einstellung wird gemerkt. Bei PDFs gibt es die Reiter *Seiten* und *Text*.
3. **Nachbearbeiten**
   - Fundliste rechts: Häkchen = wird geschwärzt; Doppelklick auf *Typ* zum Ändern; Filter nach Text/Typ.
   - Klick auf eine Markierung springt zum Fund in der Liste und umgekehrt.
   - Rechtsklick auf einen Fund: *Nicht schwärzen*, *Typ ändern*, *Alle Vorkommen …*, *Immer ignorieren*.
   - Text markieren (in der bearbeiteten Fassung oder im Original) → Rechtsklick oder **Strg+R** *Schwärzen als …*
     (Strg+Umschalt+R: alle Vorkommen).
   - In der PDF-Seitenansicht mit der Maus einen **Bereich aufziehen**, um den enthaltenen Text zu schwärzen.
   - **Frei bearbeiten** (rechts oben): den anonymisierten Text direkt ändern (wirkt auf den Text-Export).
   - Strg+Z / Strg+Y: Rückgängig / Wiederholen.
4. **Exportieren**: PDF → *Als geschwärztes PDF* (Strg+S) oder *Als Text*; TXT/MD → gleiches Format.
   Das Original wird nie überschrieben; vorgeschlagen wird `<name>_geschwaerzt.pdf` bzw. `<name>_anonymisiert.md`.

**Anwenderhilfe:** *Hilfe → Anwenderhilfe* (F1) – ausführliche Anleitung für Nutzer mit Inhaltsverzeichnis und Suche; über „Im Browser öffnen“ auch druckbar. Quelle: `src/pii_redact/resources/hilfe.html` (die Tabelle der Datenarten wird aus dem Programm eingesetzt).

Einstellungen (Strg+,): Mindest-Score, Modellgröße, gesuchte Datenarten, Ausnahmeliste (nie schwärzen),
Sperrliste (immer schwärzen). Gespeichert unter `%APPDATA%\pii-redact\settings.json`.

### Kommandozeile

```bash
pii-redact-cli brief.pdf                     # → brief_geschwaerzt.pdf
pii-redact-cli notizen.md --modus nummeriert # → notizen_anonymisiert.md
pii-redact-cli *.txt --nur-anzeigen          # nur Funde auflisten
pii-redact-cli brief.pdf --gruendlich        # mit Transformer-Modell
pii-redact-cli --selftest                    # Installation prüfen (Modelle, beide Modi)
```

## Aufbau

```
src/pii_redact/
├── core/                 # ohne GUI – nutzbar in CLI und Tests
│   ├── loaders.py        # TXT/MD/PDF laden; PDF zeichenweise mit Bounding-Box, Kommentare/Formulare bereinigen
│   ├── ocr.py            # Texterkennung für Scan-Seiten (RapidOCR/PP-OCRv6 über onnxruntime, offline)
│   ├── recognizers_de.py # deutsche Erkenner (Presidio-Standard + eigene Muster)
│   ├── analyzer.py       # Presidio-Engine, Bereinigung, Zusammenführen, Listen, Modi
│   ├── transformer_ner.py# ONNX-Inferenz: Tokenizer, Fenster, BIO-Aggregation (ohne PyTorch)
│   ├── onnx_recognizer.py# Presidio-Erkenner für das ONNX-Modell
│   ├── batch.py          # Ordner: Arbeitsstand (ohne Klartext), Änderungserkennung, Export, Protokoll
│   ├── redactor.py       # Text-Ersetzung mit Offset-Abbildung, PDF-Schwärzung, Kontrolle
│   ├── entities.py       # Anzeigenamen, Platzhalter, Farben
│   └── models.py         # Finding, Settings
├── ui/                   # PySide6
│   ├── main_window.py    # Vergleichsansicht, Menüs, Export
│   ├── session.py        # Dokumentzustand, Nachbearbeitung, Undo/Redo
│   ├── batch_panel.py    # Ordner-Dialog, Arbeitsliste, Prüf-Leiste
│   ├── help_window.py    # Anwenderhilfe (F1)
│   ├── findings_panel.py # Fundliste
│   ├── text_view.py      # Textansicht mit Hervorhebungen
│   ├── pdf_view.py       # PDF-Seiten (lazy gerendert) mit Overlays
│   ├── settings_dialog.py
│   └── worker.py         # Analyse-Warteschlange im Hintergrund (interaktiv vorrangig)
├── resources/hilfe.html  # Text der Anwenderhilfe
├── paths.py              # Modell-/Konfigurationspfade (Quellcode und EXE)
├── cli.py
└── app.py
tools/                    # nur Entwicklung: Modell-Umwandlung, Vergleich, Test-Mini-Modell
packaging/                # Build: PyInstaller-Spec, Lock-Datei, Icon, Lizenztexte, IT-Anleitung
deploy/                   # Vorlagen für die Softwareverteilung
```

**Grundprinzip:** Die Liste der Funde (Start/Ende im extrahierten Text) ist die einzige Wahrheit.
Anonymisierter Text, PDF-Vorschau und PDF-Export werden daraus berechnet. Für PDFs kennt jeder
Textindex seine Position auf der Seite – so wird jeder Fund exakt auf Schwärzungsrechtecke abgebildet.

## Build und Verteilung

```bat
build.bat
```

erzeugt `dist\pii-redact\` (Programmordner mit EXE-Dateien, ohne Python lauffähig) und denselben Ordner als
`dist\pii-redact-<version>.zip`. Das ZIP geht an die IT, die daraus mit eigenen Werkzeugen das
Installationspaket erstellt. Details für die IT – Voraussetzungen (Python 3.14), Optionen, Verteilung, Updates,
zentrale Vorgaben: **[packaging/README.md](packaging/README.md)**.

## Tests

```bash
pytest            # Kernlogik, ONNX-Aggregation, Oberflächen-Rauchtest (offscreen)
                  # mit .venv-tools zusätzlich: komplette ONNX-Kette mit Zufalls-Mini-Modell
```

`samples/` enthält fiktive Beispieldokumente. Neu erzeugen: `samples/make_sample_pdf.py` (beispiel.pdf),
`samples/make_extras_pdf.py` (kommentare.pdf, formular.pdf), `samples/make_scan_pdf.py` (scan.pdf,
mail_mit_scan.pdf – ohne Textebene, für die Texterkennung).

## Grenzen (Stand 0.3)

- **Gescannte Seiten**: Texterkennung nur als Rückfallebene (Dokumente sollten vorher durch die zentrale OCR
  laufen). Deutlich höhere Fehlerquote – Seiten werden gekennzeichnet, der Export verlangt eine Bestätigung,
  automatischer Ordner-Export lässt solche Dateien aus. Handschrift, Unterschriften, Fotos: nicht erkannt.
- Automatische Erkennung ist **nie vollständig**: Namen ohne Kontext, ungewöhnliche Schreibweisen oder
  getrennte Wörter am Zeilenende können fehlen. Ergebnis immer prüfen.
- Die PDF-Vorschau rechts ist ein Overlay; die echte Entfernung passiert beim Export (danach Kontrolle auf Reste).
- Freie Textbearbeitung wirkt nur auf den Text-Export, nicht auf das PDF.

## Ideen für später

DOCX-Unterstützung · Pseudonymisierungs-Schlüssel (reversibel, verschlüsselt)

## Lizenz

pii-redact ist freie Software: Sie können es unter den Bedingungen der **GNU Affero General Public License**,
Version 3 oder (nach Ihrer Wahl) jeder späteren Version, weitergeben und/oder verändern (`AGPL-3.0-or-later`,
siehe [`LICENSE`](LICENSE)). Das Programm wird ohne jede Gewährleistung bereitgestellt. Die AGPL passt zu PyMuPDF, das ebenfalls unter der AGPL-3.0 steht. Wer das Programm
(auch als fertig gebauten Programmordner) weitergibt, muss den Quellcode unter derselben Lizenz zugänglich machen.

Mitgelieferte Komponenten behalten ihre eigenen Lizenzen (MIT, BSD, Apache-2.0, LGPL-3.0 für Qt u. a.); der
Build erzeugt `LIZENZEN.txt` und `LIZENZTEXTE.txt` im Programmordner. Das Transformer-Modell
(`davlan-xlmr-ner`, Release-Anhang) steht unter der **Academic Free License 3.0** (`AFL-3.0`,
Text: `packaging/lizenztexte/AFL-3.0.txt`); das Basismodell XLM-RoBERTa unter MIT.
