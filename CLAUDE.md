# pii-redact – Projektstand für Claude

Lokaler Desktop-Client (Python 3.14, PySide6), der PDF/TXT/Markdown auf personenbezogene Daten prüft und
schwärzt – offline, nur Deutsch. Überblick und Bedienung: `README.md`, Änderungen: `CHANGELOG.md`,
IT-Anleitung: `packaging/README.md`.

**Stand:** v0.7.2 (Office-Hinweis, CI mit automatischem Release-Build; davor 0.7.1: Refactoring Hauptfenster,
`.tmp`-Export, 0.7.0: Python 3.14, 0.6.0: ZIP statt MSI, 0.5.0: KI-Nachprüfung + Modellvergleich), Lizenz AGPL-3.0-or-later. 81 Tests (Stand 02.10.2026). `run.bat` und
`build.bat` auf Windows mit 3.14 erfolgreich getestet (02.10.2026, nach dem Refactoring). v0.3.0 läuft auf dem Test-PC.

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
  `pytest`), aber keine Windows-Builds. Hugging Face ist aus der Cloud gesperrt → Modell-Download/-Umwandlung
  nur auf dem Rechner des Nutzers.
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
- Erkennung: Presidio 2.2.364 + spaCy `de_core_news_md` 3.8.0; Modus **Gründlich** (Standard) zusätzlich
  Davlan XLM-R als ONNX int8 – **ohne PyTorch**.
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
    daraus folgt thinc < 8.4), antlr4 4.9.x (über omegaconf), huggingface-hub < 2 (über tokenizers).
  - Kritisch, nach Update genau testen: **PyMuPDF** (Schwärzung/`verify_pdf`), spaCy + Modell, Presidio,
    onnxruntime/tokenizers (Modus „Gründlich“), PySide6 (Oberfläche → Windows-Klicktest), rapidocr/opencv (OCR).
  - Ablauf: frische Umgebung → `pytest` inkl. Modell „Gründlich“ → Nutzer testet `run.bat`/`build.bat` →
    Lock-Datei neu (Anleitung im Dateikopf) → `CHANGELOG.md` → PR gegen `dev`.
  - Stand 02.10.2026: keine bekannten Lücken. Möglich, aber nicht dringend (Nutzer will warten): 7 kleine Updates
    – charset-normalizer, cloudpathlib, filelock, regex, smart-open, srsly, wrapt. Nicht möglich: numpy 2.5
    (Presidio 2.2.364 verlangt numpy < 2.5), pydantic-core 2.49 (gehört zu pydantic 2.14, nur Beta; pydantic
    2.13.5 braucht exakt 2.46.5). Prüfen, was zusammen auflösbar ist:
    `uv pip compile <Pakete> --python-version 3.14 --python-platform windows`.
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
- Lock: PyMuPDF 1.28.2, rapidocr 3.9.2, opencv-python 4.13.0.92 (< 5), omegaconf 2.3.1,
  antlr4-python3-runtime 4.9.3; kein torch/transformers.
- Selbsttest: `pii-redact-cli.exe --selftest` + `pii-redact.exe --smoke-test`.
- **CI (GitHub Actions, `.github/workflows/tests.yml`):** bei jedem PR und Push auf `dev`/`main`. Job „Tests“
  auf `windows-latest`, Python 3.14, Pakete aus der Lock-Datei, Transformer-Modell vom neuesten Release
  (`gh release download`, Prüfsummen aus `packaging/modell.sha256`, zwischengespeichert), `ruff --select F,E9`,
  `pytest`. Job „Sicherheitslücken“: `pip-audit` gegen die Lock-Datei (Ubuntu).
  `branch-guard.yml`: PR gegen `main` nur von `dev` (sonst rot). `build.yml`: `build.bat` auf Windows
  (`PII_REDACT_PYTHON=python`, `QT_QPA_PLATFORM=offscreen`) bei Release, bei PRs, die Build-Dateien ändern,
  und auf Knopfdruck; ZIP als Artefakt, bei Release als Anhang + Herkunftsnachweis (Attestation, nur bei
  öffentlichem Repo). Modell im CI immer aus dem neuesten Release, das `davlan-xlmr-ner.zip` enthält. Ergebnisse per GitHub-MCP
  (Check-Runs/Job-Logs) lesbar – bei rotem Haken selbst untersuchen. Nur PRs mit grünem Haken mergen.

## Beispiele
`samples/`: beispiel.pdf/.txt/.md, schwierig.txt, kommentare.pdf, formular.pdf, scan.pdf, mail_mit_scan.pdf,
unterschrift.pdf (Generatoren `make_*.py`) – alles erfunden.

## Wichtige Erkenntnisse
- Presidio importiert torch/transformers automatisch, wenn installiert → Build-Umgebung ohne torch.
- Lizenztexte lassen sich in der Cloud aus dem npm-Paket `spdx-license-list` holen.
- PyMuPDF `scrub()` entfernt keine Kommentare/Lesezeichen; `reset_fields` löscht Formularwerte.
- PyMuPDF-Schwärzung: `LINE_ART_REMOVE_IF_COVERED` greift bei Bézierkurven oft nicht → TOUCHED +
  Kollateral-Wiederherstellung.
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

## Offene Schritte
1. `tools\ki_vergleich.bat` mit den Kandidatenmodellen laufen lassen.
2. IT: Signierweg, erster signierter Build, Verteilung; KI-Zugang über LLM-Portal/-Proxy, Modell per
   `ki_vergleich` wählen, `defaults.json` mit Sperre, Datenschutzfreigabe.
3. Rechtlich bestätigen lassen: Mitlieferung des AFL-3.0-Modells neben dem AGPL-Programm.
4. Später bei Bedarf: Sperre gegen gleichzeitige Bearbeitung desselben Zielordners.
