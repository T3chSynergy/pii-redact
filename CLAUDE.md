# pii-redact – Projektstand für Claude

Lokaler Desktop-Client (Python 3.14, PySide6), der PDF/TXT/Markdown auf personenbezogene Daten prüft und
schwärzt – offline, nur Deutsch. Überblick und Bedienung: `README.md`, Änderungen: `CHANGELOG.md`,
IT-Anleitung: `packaging/README.md`.

**Stand:** v0.8.4 (nicht angezeigter PDF-Inhalt – außerhalb des Seitenbereichs, ausgeblendete Ebenen – wird entfernt;
Schwärzung trifft bei engem Zeilenabstand nur noch die eigene Zeile; Hilfe zur Textansicht); davor v0.8.3 (Telemetrie von onnxruntime abgeschaltet + ETW-Prüfung `tools\etw_pruefen.bat`, gebündeltes
Paket-Update, Sicherheitsupdate filelock, Ober-/Untergrenzen in `pyproject.toml`); v0.8.2 (Silbentrennung und
Nummern über zwei Zeilen werden vollständig geschwärzt – Anwendermeldung); v0.8.1 (Sicherheitsupdate pillow 13/certifi,
README mit Download der fertigen Version zuerst); v0.8.0 (Barrierefreiheit, Kommandozeile ohne Klartext/`--ueberschreiben`,
Testtexte + Erkennungsquote, Refactoring PDF-Laden; davor 0.7.2: Office-Hinweis, CI mit automatischem Release-Build;
0.7.1: Refactoring Hauptfenster, `.tmp`-Export, 0.7.0: Python 3.14, 0.6.0: ZIP statt MSI, 0.5.0: KI-Nachprüfung +
Modellvergleich), Lizenz AGPL-3.0-or-later. 169 Tests (Stand 10.11.2026; Stand von PR #58 vom Nutzer getestet, `build.bat` mit 0.8.4 noch nicht). `run.bat` und `build.bat` auf Windows mit
3.14 erfolgreich getestet (26.10.2026, Stand 0.8.3: Klicktest, Selbsttest, ETW-Aufzeichnung ohne Modell-Ereignisse). v0.3.0 läuft auf dem Test-PC.

## Arbeitsweise
- Sprache mit dem Nutzer und in Code/Doku/Commits: **Deutsch**.
- Commits nur auf Zuruf. Git-Autor im Repo: `uncurious866`.
- **Branches:** Standard-Branch auf GitHub bleibt `main` (= letzte veröffentlichte Version für Besucher),
  entwickelt wird auf **`dev`**. Neue Sitzungen starten aber von `main` – deshalb **zu Beginn jeder Sitzung**:
  `git fetch origin dev` und den Sitzungs-Branch `claude/…` auf `origin/dev` aufsetzen, bevor etwas geändert
  wird. Weicht die `CLAUDE.md` in `dev` von der in `main` ab (`git diff origin/main origin/dev -- CLAUDE.md`),
  danach die `CLAUDE.md` aus `dev` erneut lesen – sie ist der aktuelle Stand.
  Pull Requests immer gegen **`dev`** (nicht `main`); Änderungen an der `CLAUDE.md` ebenso.
  PR-Beschreibungen nach `.github/pull_request_template.md` gliedern (Was/Warum/Wie getestet/Checkliste).
  Issue- und PR-Vorlagen sowie `SECURITY.md`/`CONTRIBUTING.md` wirken nur aus `main` – Änderungen daran direkt per `dev → main`
  übernehmen (ohne Release).
  Release: Version in `__init__.py` + `CHANGELOG.md` + Tabelle in `SECURITY.md` (PR gegen `dev`), dann PR `dev → main` mergen.
  Danach `dev` wiederherstellen (GitHub löscht ihn beim Merge automatisch, „Automatically delete head
  branches“ ist an): `git push origin <Merge-Commit von main>:refs/heads/dev` – so stehen `dev` und `main`
  gleich. **Tag und GitHub-Release legt der Nutzer an** (Claude kann in der Cloud keine Tags pushen – 403 –
  und keine Releases anlegen): Beschreibung aus `CHANGELOG.md` zum Einfügen liefern. Die **Anhänge kommen
  automatisch**: Workflow `build.yml` baut beim Veröffentlichen auf Windows, hängt `pii-redact-<version>.zip`
  + `.sha256` an und übernimmt `davlan-xlmr-ner.zip` aus einem älteren Release, falls es fehlt. Tag muss
  `v<Version aus __init__.py>` heißen, sonst bricht der Build ab. Danach prüfen, ob die Anhänge da sind
  (`get_release_by_tag`; erster automatischer Release-Build v0.7.2 am 02.10.2026 erfolgreich, ca. 7 Minuten,
  mit Attestation).
- Entwicklung unter Windows (`setup.bat`, `run.bat`, `build.bat`). In einer Claude-Cloud-Sitzung lassen sich
  Python-Kern und Tests prüfen (`pip install -e ".[dev]"`, `python -m spacy download de_core_news_md`,
  `pytest`), aber keine Windows-Builds. Hugging Face ist aus der Cloud standardmäßig gesperrt → Modell-Download/-Umwandlung
  auf dem Rechner des Nutzers (oder nach Freigabe, siehe „Wichtige Erkenntnisse“).
- Das Repository ist **öffentlich** (seit 02.10.2026): keine persönlichen Daten, Pfade, Geheimnisse oder echten
  Namen/Adressen einchecken – nur erfundene Beispiele und `.example`-Adressen. Eingeschaltet: Dependabot-Warnungen,
  CodeQL, Secret Scanning mit Push-Schutz, Dependency Review (Anzeige im PR), Private vulnerability reporting.
- **Laufende .bat-Dateien nie überschreiben.** Vor Löschaktionen nachfragen.

## Entscheidungen
- **Lizenz:** AGPL-3.0-or-later (passend zu PyMuPDF). Angegeben in `LICENSE`, `pyproject.toml`, README,
  `about.py` (`LICENSE`, `SOURCE_URL` → „Über pii-redact“ zeigt Quellcode-Link), Hilfe Kap. 15,
  `tools/lizenzen.py` (erzeugt `LIZENZEN.txt` + `LIZENZTEXTE.txt` beim Build). Transformer-Modell (Davlan) bleibt
  **AFL-3.0** (eigenständige Datendatei; Text `packaging/lizenztexte/AFL-3.0.txt`, im Modellordner
  `LICENSE-AFL-3.0.txt`; `tools/convert_model.py` legt ihn bei). Einordnung soll noch rechtlich bestätigt werden.
- Erkennung: Presidio 2.2.365 + spaCy `de_core_news_md` 3.8.0; Modus **Gründlich** (Standard) zusätzlich
  Davlan XLM-R als ONNX int8 – **ohne PyTorch**.
  - **Modellwahl (Vergleich 03.10.2026, `tools/modelle_testen.bat`, Cloud):** Davlan base bleibt. Davlan large
    erkennt kaum mehr, ist aber ca. 3× langsamer/2× Speicher; GLiNER2-PII (fastino) und GLiNER multi PII sind
    6–7× langsamer, mit mehr Fehlalarmen. Ergebnis der App „Gründlich“: Personen 99 %, Orte 96 %.
    Neue Kandidaten: in `tools/benchmark_models.py` (`hf:`, `gliner:`, `gliner2:`) eintragen und vergleichen.
- **Formate (Entscheidung 02.10.2026): nur PDF, TXT, Markdown – keine Office-Formate** (DOCX/XLSX/PPTX).
  Grund: ein komplexes Format sicher bearbeiten statt mehrere halb. Office-Dateien haben viele versteckte
  Datenkopien (Änderungsverfolgung, Kommentare, ausgeblendeter Text, Diagramm-/Pivot-Caches, Eigenschaften);
  jedes Format bräuchte eigene Bereinigung, Kontrolle (wie `verify_pdf`) und Tests. Weg für Anwender: im
  Office-Programm als PDF speichern (Word ohne Markup) – Hinweis in Fehlermeldung (`OFFICE_HINT`), Hilfe
  (FAQ `#office`) und README. Nicht wieder vorschlagen, außer der Nutzer fragt danach.
- **Grundsatz: schlankes, lokal skalierendes Programm** (jeder PC rechnet selbst). Keine schwer abgrenzbaren
  Erkennungen (Art.-9-Daten, Zugehörigkeiten wie „er ist im Betriebsrat“).
- **KI-Nachprüfung** (optional, standardmäßig aus): LLM ist **nur Nachprüfer** des **geschwärzten** Ergebnisses
  (Restrisiko gering/mittel/hoch, Begründung, Hinweise). Gesendet wird nur `redact_text(..., NUMBERED)`, nie das
  Original; PDFs seitenweise bis `llm_max_chars` (24 000). Nur wörtlich belegte Zitate → nicht aktivierte Funde
  `source="ki"` (Datenart `CONTEXT` „Kontext (indirekt)“). Zweiteilige **Prüfkennung** erkennt gekürzte
  Anfragen; *Verbindung testen* mit Anfrage in voller Länge. Code `core/llm_review.py`, `ui/ki_panel.py`,
  Strg+K, Rückfrage einmal pro Programmstart. Einstellungen `llm_*`, Schlüssel bevorzugt `PII_REDACT_LLM_KEY`;
  Vorgabe per `defaults.json` + `locked` (Vorlage `deploy/defaults.ki.example.json`). Arbeitsstand ohne
  KI-Begründungen, Protokollspalte „KI-Prüfung“. IT-Doku dazu technikneutral (OpenAI-kompatibel).
- **Modellvergleich** `tools/ki_vergleich.py`/`.bat`: sechs erfundene Testfälle `tools/ki_testfaelle.json`;
  Bericht `ki_vergleich_<Datum>.md` (gitignored). Modellwahl ist Sache der IT.
- **PDF:** echte Schwärzung (PyMuPDF `apply_redactions` inkl. Bildpixel + `scrub`), Bereinigung (Kommentare,
  Lesezeichen löschen, Formularfelder backen), `verify_pdf` prüft alles nach. OCR (RapidOCR 3.9.2) nur als
  Rückfallebene.
- **Programmsymbol:** selbst gezeichnet aus Grundformen (blaues abgerundetes Quadrat, weißes Blatt, zwei schwarze
  Balken, zwei graue Linien; keine Schrift, keine fremde Grafik). Erzeugt mit `tools/make_icon.py` (Pillow, ohne
  Kantenglättung; ICO mit den Pillow-Standardgrößen 16–256), pixelgenau wie die Dateien vom 26.09.2026 – deren
  Entstehung war vorher nicht dokumentiert (auch im alten Chat nicht). `tests/test_icon.py` prüft Gleichheit.
  Änderungen am Symbol nur über das Skript.
- **Verteilung:** Build liefert den **Programmordner** `dist\pii-redact` (PyInstaller onedir) und daraus
  `dist\pii-redact-<version>.zip`; das Installationspaket (MSI) erstellt die IT mit eigenen Werkzeugen.
  WiX/MSI wurde aus dem Projekt entfernt.
- **Code-Signierung** (`packaging/README.md`): nur die beiden EXE-Dateien; Wege A–D, noch nicht praktisch
  getestet.
- Transformer-Modell nicht im Repo, sondern `davlan-xlmr-ner.zip` (≈ 234 MB, mit AFL-Text) am GitHub-Release;
  Prüfsummen `packaging/modell.sha256`.
- Version nur in `src/pii_redact/__init__.py`; jede Version im `CHANGELOG.md` eintragen.
- **Abhängigkeiten:** `pyproject.toml` = Bereiche (Entwicklung), `packaging/requirements-lock.txt` = exakte
  Versionen (Build, reproduzierbar). Neue Versionen nie automatisch übernehmen.
  - **Sicherheitslücken zeitnah** beheben (Dependabot-Warnungen sind eingeschaltet; Prüfung
    auch mit `pip-audit -r <Lock-Datei ohne Modell-Zeile> --no-deps --disable-pip`).
  - **Sonst gebündelt** vor einem Release bzw. alle 2–3 Monate; neue **Hauptversionen nur bewusst**, eigener PR.
  - Bewusst blockiert: opencv `< 5`, spaCy `< 4` (Modell `de_core_news_md` 3.8 passt nur zu spaCy 3.8.x;
    daraus folgt thinc < 8.4). antlr4 entfällt seit omegaconf 2.4.0 (26.10.2026).
  - **Obergrenzen in `pyproject.toml`/`requirements.txt`** (seit 26.10.2026, Gründe als Kommentar dort), weil
    `setup.bat` nach diesen Bereichen installiert, nicht nach der Lock-Datei: PyMuPDF `< 2` (Hauptversion, neue
    Pakete pymupdf-layout/pymupdf4llm, eigene ONNX-Modelle → Telemetrie, siehe „Wichtige Erkenntnisse“), PySide6 `< 6.12` (zieht WebEngine + Pdf-Module mit), rapidocr `< 3.10`
    (wechselt auf opencv-python-headless – dort greift `< 5` nicht, beide cv2-Pakete zusammen vertragen sich
    nicht), huggingface-hub `< 2` (tokenizers erlaubt 2.x seit 0.23.3). Aufheben nur bewusst, je eigener PR.
  - Kritisch, nach Update genau testen: **PyMuPDF** (Schwärzung/`verify_pdf`), spaCy + Modell, Presidio,
    onnxruntime/tokenizers (Modus „Gründlich“), PySide6 (Oberfläche → Windows-Klicktest), rapidocr/opencv (OCR).
  - Ablauf: frische Umgebung → `pytest` inkl. Modell „Gründlich“ → Funde aller Beispieldateien (beide Modi) mit
    dem alten Stand vergleichen → Nutzer testet `run.bat`/`build.bat` → Lock-Datei neu (Anleitung im Dateikopf,
    aus der Cloud per `uv pip compile`) → `CHANGELOG.md` → PR gegen `dev`.
  - 21.10.2026: Sicherheitsupdate pillow 12.3.0 → 13.0.0 (Hauptversion, 8 CVEs; kein 12.x-Fix) und certifi →
    2026.10.19. Pillow nur über rapidocr; `ocr.py` übergibt ein ndarray, Pillows Datei-Decoder laufen nie.
  - 26.10.2026: Sicherheitsupdate filelock 4.0.3 → 4.2.4 (3 CVEs, Fix ab 4.2.1); nur indirekt über
    tldextract/huggingface-hub, im Programm nicht direkt genutzt.
  - **Bei jedem Sicherheitsupdate auch die Untergrenze in `pyproject.toml` und `requirements.txt` anheben**
    (Abschnitt „Sicherheits-Untergrenzen“). Grund: Dependabot liest den Abhängigkeitsgraphen in zwei Durchläufen –
    `/packaging` (nur bei Änderung der Lock-Datei) und `/` (nur bei Änderung von pyproject.toml/requirements*.txt
    in der Wurzel, liest dabei aber **auch** die Lock-Datei). Ändert sich nur die Lock-Datei, behält `/` den alten
    Stand und erzeugt weiter Meldungen (so geschehen nach 0.8.1: 10 falsche Alarme zu pillow/certifi). Zusätzlicher
    Nutzen: `setup.bat` installiert nach `pyproject.toml`, nicht nach der Lock-Datei. Prüfen: SBOM
    `gh api repos/T3chSynergy/pii-redact/dependency-graph/sbom` (je Paket nur noch eine Version).
  - Stand 26.10.2026: keine bekannten Lücken. Gebündeltes Update erledigt (kleine Updates, spaCy 3.8.17/thinc
    8.3.14, Presidio 2.2.365, onnxruntime 1.31, tokenizers 0.23.3, pydantic 2.14.1, omegaconf 2.4.0, PySide6 6.11.3,
    opencv 4.14; Funde aller Beispieldateien unverändert). Später einzeln: PyMuPDF 2, PySide6 6.12, rapidocr 3.10,
    huggingface-hub 2. Nicht möglich: numpy 2.5 und preshed ≥ 3.0.14 (Presidio 2.2.365
    verlangt numpy < 2.5, preshed < 3.0.14). Prüfen, was zusammen auflösbar ist:
    `uv pip compile pyproject.toml --python-version 3.14 --python-platform windows` und mit der Lock-Datei vergleichen.
- **`SECURITY.md`:** Meldungen über GitHubs „Private vulnerability reporting“ (muss in den Repo-Einstellungen
  eingeschaltet sein), Antwort in der Regel binnen 14 Tagen, nur neueste Version unterstützt – bei jedem
  Release die Versionstabelle anpassen (`0.x.x`).

## Build
- `build.bat [/neu] [/ohne-zip] [/ohne-gruendlich]`; baut die Umgebung `.venv-build` neu, wenn sich
  `packaging/requirements-lock.txt` geändert hat; erzeugt Lizenzdateien; optional Signieren über
  `SIGNTOOL_ARGS`; packt am Ende per `%SystemRoot%\System32\tar.exe -a` (Rückfall: `Compress-Archive`) das ZIP
  (`…-schnell.zip` bei `/ohne-gruendlich`). `/ohne-msi` und `/nur-msi` brechen mit Hinweis ab.
- Python: bevorzugt 3.14, dann 3.13, 3.12 (`setup.bat`, `build.bat`, `tools\modelle_testen.bat`). Lock-Datei
  läuft unverändert auf 3.12–3.14 (Tests 30.09.2026). `setup.bat` baut `.venv` immer neu (`--clear`, sonst bleiben
  cp312-Pakete liegen); `build.bat` legt `.venv-build` bei anderer Python-Version neu an. In der Cloud: `uv python install 3.14` (vorinstalliertes uv kennt nur rc2).
- Lock: PyMuPDF 1.28.2, rapidocr 3.9.2, opencv-python 4.14.0.94 (< 5), omegaconf 2.4.0 (ohne antlr4);
  kein torch/transformers.
- Selbsttest: `pii-redact-cli.exe --selftest` + `pii-redact.exe --smoke-test`.
- **Kommandozeile** (`cli.py`, echte Konsole ohne Qt): Ausgabe ohne Klartext (nur Anzahl/Datenart; Klartext nur
  `--nur-anzeigen`/`--details`), kein stilles Überschreiben (`--ueberschreiben`), Rückgabewert = schwerwiegendster
  (2 Reste > 1 Fehler > 3 nicht automatisch exportiert, in der Oberfläche prüfen – Texterkennung oder Kontrolle
  vor dem Speichern, einzelne Dateien wie Ordner > 4 „Schnell“ statt „Gründlich“, weil
  das Modell fehlt > 0), Aufruffehler = 1, `--einstellungen`, `--version`. Tests: `tests/test_cli.py`.
- **CI (GitHub Actions, `.github/workflows/tests.yml`):** bei jedem PR und Push auf `dev`/`main`. Job „Tests“
  auf `windows-latest`, Python 3.14, Pakete aus der Lock-Datei, Transformer-Modell vom neuesten Release
  (`gh release download`, Prüfsummen aus `packaging/modell.sha256`, zwischengespeichert), `ruff --select F,E9`,
  `pytest`. Actions per Commit-SHA mit Versionskommentar; `.github/dependabot.yml` (nur `github-actions`, monatlich,
  PRs gegen `dev`; wirkt aus `main`) – beim Aktualisieren SHA und Kommentar zusammen ändern. Job „Sicherheitslücken“: `pip-audit` gegen die Lock-Datei (Ubuntu).
  `branch-guard.yml`: PR gegen `main` nur von `dev` (sonst rot). `build.yml`: `build.bat` auf Windows
  (`PII_REDACT_PYTHON=python`, `QT_QPA_PLATFORM=offscreen`) bei Release, bei PRs, die Build-Dateien ändern,
  und auf Knopfdruck; ZIP als Artefakt, bei Release als Anhang + Herkunftsnachweis (Attestation, nur bei
  öffentlichem Repo). Modell im CI immer aus dem neuesten Release, das `davlan-xlmr-ner.zip` enthält. Ergebnisse per GitHub-MCP
  (Check-Runs/Job-Logs) lesbar – bei rotem Haken selbst untersuchen. Nur PRs mit grünem Haken mergen.

## Beispiele
`samples/`: beispiel.pdf/.txt/.md, schwierig.txt, kommentare.pdf, formular.pdf, scan.pdf, mail_mit_scan.pdf,
unterschrift.pdf, trennung.pdf (Silbentrennung, Nummern über zwei Zeilen; Generatoren `make_*.py`) – alles erfunden. Testtexte zur Namenserkennung:
`erkennung/*.txt` (6 Stück), markiert in `make_erkennung.py`, das auch `soll_funde.json` (≈ 150 Soll-Funde)
aktualisiert; `tests/test_erkennung.py` prüft Mindestquoten (Schnell/Gründlich).

## Wichtige Erkenntnisse
- Presidio importiert torch/transformers automatisch, wenn installiert → Build-Umgebung ohne torch.
- Lizenztexte lassen sich in der Cloud aus dem npm-Paket `spdx-license-list` holen.
- PyMuPDF `scrub()` entfernt keine Kommentare/Lesezeichen; `reset_fields` löscht Formularwerte.
- PyMuPDF-Schwärzung: `LINE_ART_REMOVE_IF_COVERED` greift bei Bézierkurven oft nicht → TOUCHED +
  Kollateral-Wiederherstellung.
- **Nicht angezeigter PDF-Inhalt** (gefunden 10.11.2026, `core/pdf_hidden.py`): `get_text` liest weder Inhalt
  außerhalb des Seitenbereichs (CropBox, auch außerhalb der MediaBox) noch ausgeblendete Ebenen (OCG) – beides
  blieb ungeprüft im Ergebnis. **Entscheidung des Nutzers: entfernen statt einblenden** (wie Metadaten/Lesezeichen;
  das Ergebnis enthält, was die Ansicht zeigt). `remove_hidden_content` läuft beim Laden nach `strip_pdf_extras`:
  - Außerhalb: `get_bboxlog`/`get_texttrace` liefern auch nicht Angezeigtes (ungedrehte Koordinaten → vorher
    `set_rotation(0)`). Entfernt mit vier Schwärzungsrechtecken rund um `page.rect` (Annotationen außerhalb der
    Seite funktionieren), Grafik nur `REMOVE_IF_COVERED`, Bilder `IMAGE_PIXELS`; Zuschnitt bleibt.
  - Ebenen: PyMuPDF kann Ebenen-Inhalt nicht löschen → `_LayerStripper` zerlegt die Inhaltsströme (`_instructions`)
    und entfernt zwischen `/OC … BDC` und `EMC` sowie bei XObjects mit `/OC` nur die zeichnenden Anweisungen;
    danach `OCProperties` löschen (wirkt erst nach `tobytes` + Öffnen). Prüfung: `_snapshot` (Zeichenschritte +
    Seitenbild) muss gleich dem Stand vorher sein.
  - Rückfall, wenn die Prüfung scheitert: einblenden (Ebenen sichtbar bzw. CropBox = MediaBox per `xref_set_key`;
    `set_cropbox` scheitert bei versetztem Nullpunkt) + kritischer Hinweis `layers_shown`/`cropped`/`outside_left`.
    OCMD (zusammengesetzte Sichtbarkeit) geht immer in den Rückfall – MuPDF wertet sie anders aus als die Norm.
  - Bewusst **nicht**: unsichtbaren Text *auf* der Seite (weiß auf weiß, verdeckt) erkennen oder entfernen, auch
    kein Hinweis dazu (unzuverlässig) – er wird wie normaler Text geprüft; Hilfe `#textansicht` erklärt es.
  - `verify_pdf` meldet Ebenen und Inhalt außerhalb (`has_outside_content(leftover=True)`).
- **Kontrolle `verify_pdf`** (seit 10.11.2026): (1) Aufbau (Kommentare, Metadaten, Ebenen …), (2) mit `doc`: je
  Rechteck des Schwärzungsplans darf nur die Balkenbeschriftung stehen (Zeichenmitte im Rechteck; OCR-Seiten:
  erneut gelesene Wörter im Rechteck, die dem Fund gleichen) → „an der geschwärzten Stelle noch vorhanden“,
  (3) Wortlaut aktiver Funde im übrigen Ergebnis, als eigenes Wort/eigene Zahl (`_word_pattern`), öfter als
  abgewählt → „an anderer Stelle noch lesbar“. (3) ist gewollt: `samples/mail_mit_scan.pdf` meldet so „Kaya“
  (steht noch im Dateinamen `antrag_kaya.pdf`) und „Anwohnerparkausweis“ (einmal fälschlich als Person
  geschwärzt, einmal nicht). Ohne `doc` läuft nur (1) und (3).
- **Kontrolle vor dem Speichern** (seit 10.11.2026, Wunsch des Nutzers): `analyzer.further_occurrences` sucht den
  Wortlaut aktiver Funde im Dokumenttext an Stellen, die kein Fund berührt → nicht aktivierte Vorschläge
  `source="kontrolle"` (bleiben wie `ki` bei neuer Analyse erhalten; ein abgewählter Vorschlag ist eine
  Entscheidung und wird nicht erneut vorgeschlagen). `ExportMixin._confirm_further` (PDF-/Text-Export,
  „Geprüft & weiter“): Vorschläge in die Fundliste, ersten wählen, Rückfrage `_ask_further` (in Tests ersetzen).
  Nach „Trotzdem speichern“ blendet die Oberfläche die Meldungen „an anderer Stelle noch lesbar“ der Kontrolle
  nach dem Export aus. **Ohne Rückfrage wird zurückgehalten** (Entscheidung des Nutzers): `batch.held_for_check`
  in `window_batch._export_auto` und `cli` – nicht exportieren, Vermerk `CHECK_NOT_AUTO`, Vorschläge im
  Arbeitsstand, Rückgabewert 3; auch ein vorhandener, unbestätigter Vorschlag hält zurück. Anrede/Titel und
  Satzzeichen am Rand zählen bei der Suche nicht (`word_pattern`).
- **Schwärzung in drei Durchgängen** (`redact_pdf`, seit 10.11.2026): MuPDF entfernt jedes Zeichen, dessen Rahmen
  (ohne ca. 10 % oben/unten, gemessen mit 1.28.2) das Rechteck überlappt; die Rahmen reichen über Ober-/Unterlänge
  und ragen bei engem Zeilenabstand in die Nachbarzeilen. Deshalb: (1) Text mit schmalem Band entfernen
  (`text_removal_rects`: mittlere 30 % der Zeichenhöhe, `TEXT_INSET`; ohne gemeinsames Band – gemischte Größen,
  senkrechter Text – je Zeichen ein Band; OCR-Stellen volle Höhe), (2) frei gezogene Bereiche wie bisher inkl.
  Vektorgrafik, (3) Balken/Beschriftung in voller Höhe mit `text=PDF_REDACT_TEXT_NONE` und Bildpunkt-Entfernung.
  Vorschau und `pdf_redaction_plan` unverändert. Nach PyMuPDF-Updates: `test_tight_line_spacing_…` beachten.
- **Sonder-Leerzeichen** (seit 10.11.2026): `analyzer.plain_spaces` ersetzt U+00A0, U+202F, U+2007 und
  U+2000–U+200A längenneutral durch normale Leerzeichen – in `analyze()`, `find_terms()` und der Ausnahmeliste.
  `f.text` wird am Ende immer aus dem Original gesetzt (Wortlaut des Dokuments).
- Hauptfenster aufgeteilt: `ui/main_window.py` + Mixins `ui/window_{export,edit,view,ki,batch}.py`. Tests, die
  Funktionen eines Teils ersetzen (monkeypatch), müssen das Modul des Mixins patchen, nicht `main_window`.
- Qt: `adjustSize()` auf verborgenem Scroll-Container liefert veraltete Größe → `PdfPagesView._relayout`.
- PySide6: `QMenu.exec` in Tests nicht monkeypatchbar → Menüs über `build_*_menu`.
- `Settings.save` speichert alle Felder → IT-Vorgaben wirken nur bis zur ersten persönlichen Einstellung
  (daher `locked`).
- Manche LLM-Server kürzen zu lange Anfragen stillschweigend → Prüfkennung.
- KI-Tests gegen lokalen Fake-Server; Proxy-Variablen entfernen; Tools-Skripte per importlib → vorher in
  `sys.modules` eintragen.
- Werkzeugleiste bei 1500 px mit Überlauf (»), bei 1920 px ok.
- Barrierefreiheit (seit PR #32): Detailzeile unter der Fundliste, Tastaturbedienung (Leertaste, F2, Menütaste,
  Tab verlässt die Tabelle), Namen für Bildschirmleser. Grenze: freie Bereiche im PDF nur mit der Maus.
  Mit Windows-Sprachausgabe vom Nutzer getestet (03.10.2026); Test mit NVDA bzw. durch geübte Nutzer steht aus.
- **Silbentrennung** (`core/hyphenation.py`, seit 21.10.2026): `analyze()` und `find_terms()` arbeiten auf dem Text
  ohne „-⏎“ vor Kleinbuchstaben und rechnen Funde per `HyphenMap` zurück (Fund deckt beide Zeilen ab, `f.text` =
  Original inkl. Umbruch). Vergleiche (Ausnahmeliste, nummeriert, „gleicher Text“) über `join_hyphenated`.
- **Nummern über Zeilengrenzen** (`PiiAnalyzer._cross_line`, seit 21.10.2026): Zeilenpaare „Ziffer/Buchstabe⏎
  Ziffer|+|Großbuchstabe+Ziffer“ werden als ein Prüftext (Umbruch → Leerzeichen, Paare durch Absatz getrennt)
  zusätzlich analysiert; übernommen nur Muster-Treffer (keine NER), die über den Umbruch reichen und noch nicht
  von einem Muster-Fund abgedeckte Zeichen enthalten (Adressblöcke bleiben zwei Funde). Nicht abgedeckt: Zahl ohne
  Leerzeichen mitten umbrochen (Steuer-ID „86095⏎742719“) – kommt bei Fließtext praktisch nicht vor.
- **Telemetrie von onnxruntime** (gefunden 26.10.2026): Windows-Wheels protokollieren über ETW (ProcessInfo beim
  Laden; SessionCreation u. a. mit `modelFileName`, RuntimeError mit Meldung), Linux-Wheels (mind. 1.30) haben einen
  eigenen Sende-Client an `mobile.events.data.microsoft.com` mit Geräte-ID unter
  `~/.cache/Microsoft/DeveloperTools/.onnxruntime`. Abschalten: `ORT_DISABLE_TELEMETRY=1` vor dem ersten Import
  (`pii_redact/__init__.py`, wirkt nur unter Linux/macOS) und `disable_telemetry_events()` vor jedem Modell
  (`onnx_telemetry.py`, Windows; die Lade-Ereignisse ProcessInfo, RegisterEpLibraryStart/-End und DriverInfo
  bleiben – ohne Pfade/Modellnamen, Liste `ALLOWED` in `tools/etw_pruefen.py`). Tests: `tests/test_telemetry.py`.
  Bei onnxruntime-Updates die Bibliotheken nach `events.data.microsoft.com`/`ORT_DISABLE_TELEMETRY` durchsuchen.
  **Prüfen unter Windows:** `tools\etw_pruefen.bat [--exe dist\pii-redact\pii-redact-cli.exe]` (Administrator;
  ETW-Anbieter `{3a26b1ff-7484-7484-7484-15261f42614d}`, logman + tracerpt, Gegenprobe ohne Abschaltung); in der CI
  `tests/test_telemetry.py::test_windows_etw_no_model_events` (schlägt dort fehl statt zu überspringen).
  Vom Nutzer bestätigt (26.10.2026): 0.8.2 meldet Texterkennung + `model.onnx`, `dev` nichts mehr.
  **PyMuPDF 2** lädt beim Start eigene Layout-Modelle über onnxruntime (`layout_rf…`, `feature_imf1`,
  `table_grid_model…`, aufgefallen in einer `.venv` von vor den Obergrenzen) – vor der Abschaltung. Beim Update auf
  PyMuPDF 2 muss `onnx_telemetry.disable()` vor `import pymupdf` laufen; der ETW-Test fällt sonst rot aus.
- Hugging Face und download.pytorch.org sind nur erreichbar, wenn der Nutzer sie in der Cloud-Umgebung
  freigegeben hat (Network access → Custom); sonst Modellvergleich nur auf seinem Rechner.

## Offene Schritte
1. `tools\ki_vergleich.bat` mit den Kandidatenmodellen laufen lassen.
2. IT: Signierweg, erster signierter Build, Verteilung; KI-Zugang über LLM-Portal/-Proxy, Modell per
   `ki_vergleich` wählen, `defaults.json` mit Sperre, Datenschutzfreigabe.
3. Rechtlich bestätigen lassen: Mitlieferung des AFL-3.0-Modells neben dem AGPL-Programm.
4. Später bei Bedarf: Sperre gegen gleichzeitige Bearbeitung desselben Zielordners.
