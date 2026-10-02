# Änderungen

## Unveröffentlicht

- Neu: `SECURITY.md` – wie Sicherheitslücken (z. B. Reste im geschwärzten PDF) vertraulich gemeldet werden.
- Neu: Issue-Vorlagen (`.github/ISSUE_TEMPLATE/`) für Fehler, Erkennung und Verbesserungsvorschläge – mit
  Hinweis, keine echten Daten anzuhängen; Sicherheitslücken verweisen auf die vertrauliche Meldung.
- Neu: Vorlage für Pull Requests (`.github/pull_request_template.md`) – Ziel-Branch `dev`, Checkliste mit
  Datenschutz, Tests und Lizenz.

## 0.7.0 – 30.09.2026

**Geändert – Python 3.14**
- Empfohlene Python-Version ist jetzt **3.14** (3.12 erhält nur noch Sicherheitskorrekturen, ohne neue
  Windows-Installer). `setup.bat`, `build.bat` und `tools\modelle_testen.bat` bevorzugen 3.14, dann 3.13 und
  3.12. Alle Pakete der Lock-Datei bleiben unverändert (getestet mit 3.13 und 3.14).
- `setup.bat` baut `.venv` immer frisch auf (`venv --clear`); `build.bat` legt `.venv-build` automatisch neu an,
  wenn sie mit einer anderen Python-Version erstellt wurde.
- „Über pii-redact“ und Hilfe (Komponentenliste) nennen die tatsächlich verwendete Python-Version statt fest
  „Python 3.12“.

## 0.6.0 – 28.09.2026

**Geändert – Build erzeugt ZIP statt MSI**
- `build.bat` packt den fertigen Programmordner als `dist\pii-redact-<version>.zip` (mit
  `/ohne-gruendlich`: `…-schnell.zip`). Das MSI erstellt die IT mit eigenen Werkzeugen aus diesem Ordner.
- Entfernt: WiX-Paket (`packaging/pii-redact.wxs`), die Schalter `/ohne-msi` und `/nur-msi` (Hinweis beim
  Aufruf) sowie die Voraussetzungen .NET SDK und WiX. Neu: `/ohne-zip` – nur den Programmordner bauen.
- Code-Signierung betrifft nur noch die EXE-Dateien; IT-Anleitung (`packaging/README.md`) entsprechend
  überarbeitet.

## 0.5.0 – 27.09.2026

**Lizenz**
- pii-redact steht jetzt unter der **GNU AGPL-3.0 oder später** (`LICENSE`), passend zu PyMuPDF; Quellcode:
  https://github.com/T3chSynergy/pii-redact. „Über pii-redact“,
  Hilfe (Kap. 15), README und `LIZENZTEXTE.txt` nennen die Lizenz; der AFL-3.0-Text des Transformer-Modells
  liegt bei (`packaging/lizenztexte/AFL-3.0.txt`, im Modellordner `LICENSE-AFL-3.0.txt`).

**Neu**
- **KI-Nachprüfung (optional, standardmäßig aus):** Ein Sprachmodell bewertet auf Knopfdruck (*KI-Prüfung*,
  Strg+K) das **geschwärzte** Ergebnis – Restrisiko gering/mittel/hoch, Begründung, Hinweise auf übersehene und
  indirekt identifizierende Angaben (z. B. Funktion + Abteilung). Gesendet wird nur der Text mit nummerierten
  Platzhaltern, nie das Original. Nur wörtlich belegte Hinweise werden übernommen – als nicht aktivierte
  Vorschläge (Quelle „KI“) in der Fundliste; Reiter „KI-Bewertung“ mit Sprung zur Stelle, „Alle Vorschläge
  schwärzen“, „Vorschläge verwerfen“. Rückfrage vor dem ersten Senden je Programmstart.
- OpenAI-kompatible Schnittstelle (direkt oder über LLM-Portal/-Proxy); Einstellungen im
  neuen Reiter *KI-Nachprüfung* mit Verbindungstest; Schlüssel bevorzugt über `PII_REDACT_LLM_KEY`;
  Vorgabe und Sperre per `defaults.json`.
- Neue Datenart **„Kontext (indirekt)“** – auch für manuelle Markierungen.
- Schutz gegen stillschweigend gekürzte Anfragen (z. B. zu kleines Kontextfenster am Server): zweiteilige
  Prüfkennung je Anfrage; fehlt sie in der Antwort, gibt es eine Fehlermeldung statt einer Bewertung.
  *Verbindung testen* schickt eine Anfrage in voller Länge und prüft so auch das Kontextfenster.
- Vorlage `deploy/defaults.ki.example.json` für die IT.
- Werkzeug **`tools/ki_vergleich.bat`**: vergleicht Sprachmodelle mit sechs erfundenen Testfällen (Restrisiko,
  gefundene Stellen, Fehlalarme, Zeit) und schreibt einen Bericht.
- Ordner-Bearbeitung: Protokollspalte „KI-Prüfung“ (ohne Inhalte).

## 0.4.0 – 27.09.2026

**Geändert – Hinweise stören nicht mehr bei häufiger Nutzung**
- Über dem Dokument steht nur noch eine **einzeilige, schließbare Leiste** – und nur für kritische Hinweise
  (Seiten aus der Texterkennung, Seiten ohne Text). Der volle Text steht im Tooltip.
- Neuer **Hinweis-Zähler** in der Statusleiste („ⓘ 2 Hinweise“, orange bei kritischen). Ein Klick zeigt alle
  Hinweise mit Erklärung und Sprung zur Seite.
- **Seitensymbole** in der Seitenansicht: „OCR“, „ohne Text“, „Bild“, „Formular“ (Erklärung per Tooltip).
- Bildhinweis nur noch für Bilder, die Inhalte tragen können: wiederkehrende Logos und kleine Symbole werden
  übergangen.
- Erfolgreicher PDF-Export ohne Dialog: kurze Meldung in der Statusleiste mit Link „Ordner öffnen“.
  Ein Fenster erscheint nur noch, wenn die Kontrolle Reste findet.
- Freie Bearbeitung ohne eigenen Hinweisbalken (Erklärung im Tooltip der Überschrift).
- Rückfrage vor dem Export von OCR-Seiten auf zwei Sätze gekürzt (bleibt immer aktiv).
- Neue Einstellung **„Kompakte Hinweise“** für Vielnutzer: auch die Leiste entfällt, Zähler und Seitensymbole
  bleiben.
- `defaults.json`: neuer Schlüssel **`locked`** – dort aufgeführte Einstellungen legt die IT fest
  (in den Einstellungen ausgegraut).

## 0.3.0 – 26.09.2026

**Neu**
- **Bereiche frei schwärzen** (PDF): Rahmen in der Seitenansicht ziehen → „Bereich schwärzen als“
  Unterschrift, Foto/Bild, Handschrift oder Bereich – auch ohne Text darunter. Im Bereich werden beim Export
  Bildpunkte, Text und Vektorgrafik (z. B. gezeichnete Unterschriften) echt entfernt; Linien und Flächen, die
  nur hineinragen, bleiben erhalten. Bereiche erscheinen in der Fundliste, sind rückgängig machbar, werden im
  Arbeitsstand gespeichert (nur Koordinaten) und im Protokoll gezählt (Spalte „Bereiche“).
- Neues Beispiel `unterschrift.pdf` (Vektor-Unterschrift, Passfoto, handschriftliche Notiz).

## 0.2.0 – 26.09.2026

**Neu**
- **Texterkennung (OCR)** für gescannte PDF-Seiten ohne Textebene (Rückfallebene, z. B. eingescannte
  Mail-Anhänge). Automatisch aktiv, abschaltbar. Deutliche Kennzeichnung (Hinweis, Seitenrahmen, Fundliste),
  Bestätigung vor dem Export; Dateien mit OCR werden nie automatisch exportiert.
- **PDF-Bereinigung:** Kommentare, Markierungen und Lesezeichen werden entfernt, Formularfelder in festen
  Inhalt umgewandelt (Werte werden geprüft und geschwärzt). Kontrolle nach dem Export prüft zusätzlich
  Kommentare, Formularfelder, Lesezeichen, Metadaten, Links und Anhänge.
- Modus **„Gründlich“ ist Standard** (IT kann per `defaults.json` „Schnell“ vorgeben).
- **Zoom:** Seitenbreite (passt sich an), Ganze Seite, feste Werte (100 % = Papiergröße); Wahl bleibt erhalten.
- **Über pii-redact** und Hilfe-Kapitel 15 mit allen wesentlichen Open-Source-Komponenten;
  `LIZENZEN.txt` und `LIZENZTEXTE.txt` im Programmordner.
- Neue Beispiele: `kommentare.pdf`, `formular.pdf`, `scan.pdf`, `mail_mit_scan.pdf`.

**Behoben**
- PDF wurde nach dem Wechsel von einem Text-Dokument oft nicht angezeigt.
- Ausgefüllte Formularfelder verloren beim Export ihren Inhalt.

**Intern**
- Arbeitsstand der Ordner-Bearbeitung prüft, ob gespeicherte Funde noch zum Text passen.
- `build.bat` aktualisiert die Build-Umgebung automatisch bei geänderter Paketliste.

## 0.1.0 – 26.09.2026

Erste Fassung: Erkennung (Presidio, spaCy, optional Transformer-Modell als ONNX), PDF-Schwärzung,
TXT/Markdown, Nachbearbeitung, Ordner-Bearbeitung mit Protokoll, Kommandozeile, Anwenderhilfe,
PyInstaller-Build und MSI für SCCM.
