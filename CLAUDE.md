# pii-redact – Projektstand für Claude

Lokaler Desktop-Client (Python 3.14, PySide6), der PDF/TXT/Markdown auf personenbezogene Daten prüft und
schwärzt – offline, nur Deutsch. Überblick und Bedienung: `README.md`, Änderungen: `CHANGELOG.md`,
IT-Anleitung: `packaging/README.md`.

**Stand:** v0.6.0 (Build erzeugt ZIP statt MSI; davor 0.5.0: KI-Nachprüfung + Modellvergleich), Lizenz
AGPL-3.0-or-later. 78 Tests (Stand 28.09.2026). v0.3.0 läuft auf dem Test-PC; 0.5.0 als MSI gebaut.

## Arbeitsweise
- Sprache mit dem Nutzer und in Code/Doku/Commits: **Deutsch**.
- Commits nur auf Zuruf. Git-Autor im Repo: `uncurious866`.
- **Branches:** Standard-Branch auf GitHub bleibt `main` (= letzte veröffentlichte Version für Besucher),
  entwickelt wird auf **`dev`**. Neue Sitzungen starten aber von `main` – deshalb **zu Beginn jeder Sitzung**:
  `git fetch origin dev` und den Sitzungs-Branch `claude/…` auf `origin/dev` aufsetzen, bevor etwas geändert
  wird. Weicht die `CLAUDE.md` in `dev` von der in `main` ab (`git diff origin/main origin/dev -- CLAUDE.md`),
  danach die `CLAUDE.md` aus `dev` erneut lesen – sie ist der aktuelle Stand.
  Pull Requests immer gegen **`dev`** (nicht `main`); Änderungen an der `CLAUDE.md` ebenso.
  Release: Version in `__init__.py` + `CHANGELOG.md` (PR gegen `dev`), dann PR `dev → main` mergen.
  Danach `dev` wiederherstellen (GitHub löscht ihn beim Merge automatisch, „Automatically delete head
  branches“ ist an): `git push origin <Merge-Commit von main>:refs/heads/dev` – so stehen `dev` und `main`
  gleich. **Tag und GitHub-Release legt der Nutzer an** (Claude kann in der Cloud keine Tags pushen – 403 –
  und keine Releases anlegen): Beschreibung aus `CHANGELOG.md` zum Einfügen liefern; Anhänge
  `pii-redact-<version>.zip` (Build auf Windows) und `davlan-xlmr-ner.zip`.
- Entwicklung unter Windows (`setup.bat`, `run.bat`, `build.bat`). In einer Claude-Cloud-Sitzung lassen sich
  Python-Kern und Tests prüfen (`pip install -e ".[dev]"`, `python -m spacy download de_core_news_md`,
  `pytest`), aber keine Windows-Builds. Hugging Face ist aus der Cloud gesperrt → Modell-Download/-Umwandlung
  nur auf dem Rechner des Nutzers.
- Das Repository soll öffentlich werden: keine persönlichen Daten, Pfade, Geheimnisse oder echten Namen/Adressen
  einchecken – nur erfundene Beispiele und `.example`-Adressen.
- **Laufende .bat-Dateien nie überschreiben.** Vor Löschaktionen nachfragen.

## Entscheidungen
- **Lizenz:** AGPL-3.0-or-later (passend zu PyMuPDF). Angegeben in `LICENSE`, `pyproject.toml`, README,
  `about.py` (`LICENSE`, `SOURCE_URL` → „Über pii-redact“ zeigt Quellcode-Link), Hilfe Kap. 15,
  `tools/lizenzen.py` (erzeugt `LIZENZEN.txt` + `LIZENZTEXTE.txt` beim Build). Transformer-Modell (Davlan) bleibt
  **AFL-3.0** (eigenständige Datendatei; Text `packaging/lizenztexte/AFL-3.0.txt`, im Modellordner
  `LICENSE-AFL-3.0.txt`; `tools/convert_model.py` legt ihn bei). Einordnung soll noch rechtlich bestätigt werden.
- Erkennung: Presidio 2.2.364 + spaCy `de_core_news_md` 3.8.0; Modus **Gründlich** (Standard) zusätzlich
  Davlan XLM-R als ONNX int8 – **ohne PyTorch**.
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

## Beispiele
`samples/`: beispiel.pdf/.txt/.md, schwierig.txt, kommentare.pdf, formular.pdf, scan.pdf, mail_mit_scan.pdf,
unterschrift.pdf (Generatoren `make_*.py`) – alles erfunden.

## Wichtige Erkenntnisse
- Presidio importiert torch/transformers automatisch, wenn installiert → Build-Umgebung ohne torch.
- Lizenztexte lassen sich in der Cloud aus dem npm-Paket `spdx-license-list` holen.
- PyMuPDF `scrub()` entfernt keine Kommentare/Lesezeichen; `reset_fields` löscht Formularwerte.
- PyMuPDF-Schwärzung: `LINE_ART_REMOVE_IF_COVERED` greift bei Bézierkurven oft nicht → TOUCHED +
  Kollateral-Wiederherstellung.
- Qt: `adjustSize()` auf verborgenem Scroll-Container liefert veraltete Größe → `PdfPagesView._relayout`.
- PySide6: `QMenu.exec` in Tests nicht monkeypatchbar → Menüs über `build_*_menu`.
- `Settings.save` speichert alle Felder → IT-Vorgaben wirken nur bis zur ersten persönlichen Einstellung
  (daher `locked`).
- Manche LLM-Server kürzen zu lange Anfragen stillschweigend → Prüfkennung.
- KI-Tests gegen lokalen Fake-Server; Proxy-Variablen entfernen; Tools-Skripte per importlib → vorher in
  `sys.modules` eintragen.
- Werkzeugleiste bei 1500 px mit Überlauf (»), bei 1920 px ok.

## Offene Schritte
1. Repo öffentlich stellen; Release v0.6.0 anlegen (ZIP aus `build.bat` + `davlan-xlmr-ner.zip`);
   `tools\ki_vergleich.bat` mit den Kandidatenmodellen laufen lassen.
2. ZIP-Build einmal auf Windows ausprobieren (`build.bat`, `build.bat /ohne-zip`).
3. IT: Signierweg, erster signierter Build, Verteilung; KI-Zugang über LLM-Portal/-Proxy, Modell per
   `ki_vergleich` wählen, `defaults.json` mit Sperre, Datenschutzfreigabe.
4. Rechtlich bestätigen lassen: Mitlieferung des AFL-3.0-Modells neben dem AGPL-Programm.
5. Später bei Bedarf: DOCX, Sperre gegen gleichzeitige Bearbeitung desselben Zielordners.
