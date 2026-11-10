# Änderungen

## Unveröffentlicht

**Kontrolle vor dem Speichern**
- **Nachname allein:** Ist ein Personenname aus mehreren Wörtern geschwärzt („Max Mustermann“), schlägt die
  Kontrolle jetzt auch Stellen vor, an denen nur der Nachname steht („Mustermann zahlt …“,
  „antrag_mustermann.pdf“). Bisher suchte sie nur den ganzen Wortlaut. Weil viele Nachnamen auch gewöhnliche
  Wörter sind, zählt der Nachname nur groß geschrieben und nicht nach einem Artikel oder „im“/„am“ („im Winter“,
  „der Koch“), klein geschrieben nur in Dateinamen und Kennungen. Wie alle Vorschläge der Kontrolle: ohne
  Häkchen in der Fundliste, Rückfrage vor dem Speichern; automatischer Export und Kommandozeile halten die Datei
  zurück (Rückgabewert 3). Hilfe: „Kontrolle vor dem Speichern“.

**Kleine Korrekturen**
- **KI-Nachprüfung folgt keinen Weiterleitungen mehr:** Antwortete der eingestellte Server mit einer
  Weiterleitung (301/302/303), ging die Anfrage bisher an die neue Adresse – auch an einen anderen Server, samt
  API-Schlüssel. Jetzt bricht die Anfrage mit einer Meldung ab, die das Ziel nennt; die Server-Adresse ist dann
  in den Einstellungen anzupassen.
- **Text-Export und Einstellungen werden sicher geschrieben:** erst in eine Datei `<Name>.tmp`, dann umbenannt
  (wie schon beim PDF-Export). Bricht das Schreiben ab – Datenträger voll, Netzlaufwerk getrennt –, bleibt die
  bisherige Datei unverändert, und unter dem Zielnamen liegt keine halbe Datei.
- **Einstellungsdatei mit unpassenden Werten:** In einer von Hand bearbeiteten `settings.json` oder
  `defaults.json` werden Werte der falschen Art (etwa Text statt Zahl) übergangen; dafür gilt die Vorgabe der IT
  bzw. die Voreinstellung. Bisher konnte das Programm damit später an unerwarteter Stelle abbrechen.
- **Text-Export: verständliche Meldung, wenn das Speichern scheitert** (Ziel schreibgeschützt, Netzlaufwerk
  getrennt, Datenträger voll). Bisher erschien das allgemeine Fenster „Unerwarteter Fehler“; beim PDF-Export gab
  es die Meldung „Export fehlgeschlagen“ schon. Verloren ging dabei nichts.
- **Kommandozeile:** `--schwelle` nimmt nur noch Werte von 0 bis 1 an (sonst Aufruffehler, Rückgabewert 1).

## 0.8.5 – 10.11.2026

**Kontrolle nach dem Export**
- **Jede geschwärzte Stelle wird einzeln geprüft:** Die Kontrolle sieht jetzt an jeder Schwärzung nach, ob dort
  noch Text steht. Bisher suchte sie nur den Wortlaut im ganzen Dokument – und übersprang einen Text ganz, sobald
  er an anderer Stelle bewusst abgewählt war.
- **Weniger Fehlalarme:** Ein geschwärzter Begriff gilt nicht mehr als „noch vorhanden“, nur weil er Teil eines
  längeren Wortes ist („Ende“ in „Wochenende“, „4711“ in „14711“). Weiterhin gemeldet wird er als eigenes Wort an
  einer nicht markierten Stelle, etwa in einem Dateinamen („akte_ende.pdf“); bewusst abgewählte Vorkommen zählen
  dabei nicht.
- Die Meldungen sagen, was gefunden wurde: „an der geschwärzten Stelle noch vorhanden“ oder „an anderer Stelle
  noch lesbar“, jeweils mit Seite. Erklärung in der Hilfe unter „Häufige Fragen“.
- **Kontrolle vor dem Speichern:** Steht ein geschwärzter Text noch an einer nicht markierten Stelle (etwa ein
  Name im Dateinamen „antrag_mustermann.pdf“ oder in Großbuchstaben), fragt das Programm jetzt **vor** dem
  Speichern nach, statt danach zu warnen. Die Stellen kommen als Vorschläge in die Fundliste (Herkunft
  „Kontrolle“, ohne Häkchen), der erste wird gleich gezeigt. „Zurück zur Prüfung“ speichert nichts, „Trotzdem
  speichern“ lässt sie stehen; ein Vorschlag ohne Häkchen gilt als entschieden. Gilt für PDF- und Text-Export
  und für „Geprüft & weiter“ im Ordner.
- **Ohne Rückfrage wird zurückgehalten:** Der automatische Export („Ungeprüfte Dateien exportieren“) und die
  Kommandozeile können nicht nachfragen. Dateien, in denen ein geschwärzter Text noch an anderer Stelle steht,
  werden dort nicht mehr exportiert, sondern bleiben zur Prüfung stehen – wie Dateien mit Texterkennung
  (Vermerk im Protokoll, Kommandozeile: Rückgabewert 3). Beim Öffnen in der Oberfläche stehen die Vorschläge
  der Kontrolle schon in der Fundliste.

**Kommandozeile**
- **Fehlendes Modell für „Gründlich“ fällt auf:** Ist „Gründlich“ eingestellt und das Transformer-Modell fehlt,
  wird wie bisher mit „Schnell“ bearbeitet – aber mit dem neuen **Rückgabewert 4** statt 0. Wer „Schnell“ will,
  wählt es mit `--schnell` oder in den Einstellungen (dann 0). Reihenfolge bei mehreren Dateien: 2 vor 1 vor 3
  vor 4 vor 0.
- **Rückgabewert 3** bedeutet jetzt allgemein „nicht automatisch exportiert, bitte in der Oberfläche prüfen“:
  Texterkennung oder ein geschwärzter Text, der noch an anderer Stelle steht.
- **Einzelne Dateien mit Texterkennung** werden nicht mehr automatisch exportiert (Rückgabewert 3), wie schon im
  Ordnerlauf. `--nur-anzeigen` und `--ohne-ocr` arbeiten wie bisher.

**Erkennung**
- **Namen und Orte in Großbuchstaben:** In Formularen und Adressfeldern stehen Namen oft in Großbuchstaben
  („SCHNEIDER, ANNA“, „80331 MÜNCHEN“) – die Erkennung fand sie bisher kaum. Zeilen mit solchen Wörtern werden
  jetzt zusätzlich in normaler Schreibweise geprüft. Gemessen an den Testtexten, wenn Namen und Orte dort in
  Großbuchstaben stehen: „Gründlich“ Personen 78 % → 95 %, Orte 79 % → 98 %; „Schnell“ Personen 12 % → 65 %,
  Orte 21 % → 69 %. Bei normal geschriebenen Texten ändert sich nichts (dieselben Funde, keine zusätzlichen
  Fehlalarme). In Texten, die ganz in Großbuchstaben stehen, kommen einzelne Fehlalarme hinzu (gewöhnliche
  Wörter wie „AULA“ als Ort; 5 in den Testtexten mit rund 3000 Wörtern).
- Gängige Abkürzungen (DSGVO, BGB, IHK, AOK, TÜV, SEPA …) gelten nicht als Name oder Ort.
- **Geschützte und schmale Leerzeichen:** IBAN, Telefonnummern und andere Nummern wurden nicht erkannt, wenn
  statt normaler Leerzeichen geschützte standen („DE89 3704 …“ aus Satzprogrammen oder der Textverarbeitung).
  Solche Leerzeichen zählen jetzt wie normale – auch in Sperr- und Ausnahmeliste.
- **Geburtsdatum mit Stern:** „*17.11.1984“ und „† 12.01.2020“ werden ohne Kontextwort als Datum erkannt
  (nicht bei Markdown-Hervorhebung wie „*12.03.2024*“).
- **Postfach:** „Postfach 10 20 30“ wird als Adresse erkannt. Die Wörter „Postfach“, „Personalnr“ und „Kundennr“
  allein gelten nicht mehr als Ort.

**Fehlerbehebung**
- **Kommandozeile:** Ein defektes oder leeres PDF beendete den ganzen Lauf mit einer Fehlerausgabe des
  Programms; im Ordnerlauf blieben die folgenden Dateien unbearbeitet. Jetzt wird die Datei gemeldet
  („beschädigt oder kein gültiges PDF“, Rückgabewert 1) und der Lauf geht weiter.
- **Protokoll:** Dateinamen, die mit `=`, `+`, `-` oder `@` beginnen, las Excel im Protokoll als Formel. Solche
  Zellen beginnen jetzt mit einem Hochkomma und bleiben Text.

**Oberfläche**
- **Schaltflächen durchgehend deutsch:** Rückfragen und Dialoge zeigten teils englische Schaltflächen („Cancel“,
  „Yes“/„No“). Sie sind jetzt deutsch beschriftet; zusätzlich lädt das Programm die deutsche Übersetzung für
  alles, was Qt selbst beschriftet (z. B. Kontextmenüs der Textfelder).
- „Ungeprüfte Dateien exportieren“: Die Rückfrage nennt die Zahl jetzt als „Bis zu …“, weil die Kontrolle
  einzelne Dateien danach noch zurückhalten kann.

**KI-Nachprüfung**
- Die Einstellungen warnen bei einer unverschlüsselten Server-Adresse (`http://`, außer zum eigenen Rechner).

**Intern**
- GitHub Actions sind in den Workflows per Commit-SHA festgelegt; `.github/dependabot.yml` hält sie mit
  Pull Requests gegen `dev` aktuell (nur Actions, keine Python-Pakete).
- `pyproject.toml`: Python ab 3.12 (getestet werden 3.12 bis 3.14).

## 0.8.4 – 10.11.2026

**Sicherheit des Ergebnisses**
- **Nicht angezeigter Inhalt wird entfernt:** Ein PDF kann Inhalt tragen, den kein PDF-Programm anzeigt – im
  abgeschnittenen Teil einer zugeschnittenen Seite, neben dem Blatt oder auf einer ausgeblendeten Ebene. Bisher
  wurde er weder geprüft noch entfernt: Er blieb im Ergebnis und ließ sich wieder hervorholen, die Kontrolle
  bemerkte das nicht. pii-redact entfernt ihn jetzt schon beim Öffnen, wie Kommentare und Lesezeichen. Die Seite
  sieht aus wie vorher (Zuschnitt und Drehung bleiben); der Hinweis-Zähler nennt die Seiten.
  - Außerhalb des Seitenbereichs: Text, Bilder und ganz außerhalb liegende Linien/Flächen werden entfernt. Ragt
    ein Bild über den Rand, werden seine Bildpunkte außerhalb geleert. Linien und Flächen, die über den Rand
    ragen (Hintergründe, Rahmen), bleiben unverändert.
  - Ausgeblendete Ebenen: Text, Bilder, Zeichnungen und eingebettete Seiten der Ebene werden aus dem Seiteninhalt
    entfernt, danach werden alle Ebenen aufgelöst.
  - Jeder Schritt wird nachgeprüft. Gelingt das Entfernen nicht nachweislich (z. B. Ebenen mit zusammengesetzter
    Sichtbarkeit), wird der Inhalt stattdessen eingeblendet und geprüft – mit kritischem Hinweis.
- Die Kontrolle nach dem Export meldet zusätzlich Ebenen und Inhalt außerhalb des sichtbaren Seitenbereichs.
- Hilfe: neuer Abschnitt „Bei PDFs auch den Reiter Text lesen“ – unsichtbarer Text auf der Seite (weiß auf weiß,
  verdeckt, winzig) wird wie jeder Text geprüft und geschwärzt; was die Erkennung dort übersieht, zeigt nur die
  Textansicht.

**Fehlerbehebung**
- **Enger Zeilenabstand:** Beim Schwärzen einer Textstelle verschwand bei engem Zeilenabstand auch Text aus der
  Zeile darüber und darunter – ohne Hinweis, die Vorschau zeigte es nicht. Der Text wird jetzt mit einem schmalen
  Band durch die Zeilenmitte entfernt, das nur die eigene Zeile trifft; der Balken und das Entfernen der
  Bildpunkte (gescannte Seiten) bleiben in voller Höhe. Die Ergebnisse aller Beispieldateien sind unverändert
  (Text und Seitenbild, beide Modi).
- Auf Seiten mit frei gezogenen Bereichen entfernen nur noch die Bereiche selbst berührte Vektorgrafik, nicht
  mehr die Textstellen derselben Seite (wie auf allen anderen Seiten).

**Intern**
- Neues Skript `tools/make_icon.py`: zeichnet das Programmsymbol (`packaging/pii-redact.ico`,
  `src/pii_redact/resources/icon.png`) aus Grundformen – pixelgenau wie die bisherigen Dateien. Damit ist
  dokumentiert, dass das Symbol selbst erstellt ist, und es lässt sich leicht anpassen. Ein Test prüft, dass Skript
  und Dateien übereinstimmen.

## 0.8.3 – 26.10.2026

**Datenschutz**
- **Telemetrie von onnxruntime abgeschaltet:** Die Bibliothek für den Modus „Gründlich“ und die Texterkennung
  (onnxruntime von Microsoft) protokollierte unter Windows Nutzungsereignisse, u. a. den Dateinamen des Modells,
  Hardwaredaten und Fehlermeldungen (keine Dokumentinhalte). Windows kann sie je nach Diagnosedaten-Einstellung an
  Microsoft übermitteln. pii-redact schaltet sie jetzt vor dem ersten Modell ab; unter Linux/macOS ist zusätzlich
  der eigene Sende-Client von onnxruntime aus (keine Geräte-ID, kein Versand). Hinweise für die IT in
  `packaging/README.md`.

**Sicherheit**
- `filelock` 4.0.3 → 4.2.4: behebt drei Lücken in den Sperrdateien (CVE-2026-112907, CVE-2026-112913,
  CVE-2026-112940). pii-redact nutzt `filelock` nicht selbst; es kommt indirekt über `tldextract` und
  `huggingface-hub` mit.

**Abhängigkeiten**
- Gebündeltes Update: spaCy 3.8.17, Presidio 2.2.365, onnxruntime 1.31.0, tokenizers 0.23.3, pydantic 2.14.1,
  PySide6 6.11.3, opencv 4.14, omegaconf 2.4.0 (braucht `antlr4-python3-runtime` nicht mehr, das Paket entfällt)
  sowie kleinere Updates (u. a. certifi, charset-normalizer, cloudpathlib, phonenumbers, regex, shapely,
  smart-open, srsly, tldextract, wrapt). Die Erkennung bleibt unverändert: alle Beispieldateien liefern in beiden
  Modi dieselben Funde wie vorher.

**Intern**
- Neues Prüfskript `tools\etw_pruefen.bat` (als Administrator): zeichnet die Telemetrie-Ereignisse von
  onnxruntime lokal auf, lässt pii-redact eine Scan-PDF und einen Text im Modus „Gründlich“ verarbeiten und meldet,
  ob Modell-Ereignisse entstanden sind – für die Entwicklungsumgebung oder das gebaute Programm
  (`--exe dist\pii-redact\pii-redact-cli.exe`), mit Gegenprobe. Derselbe Ablauf läuft als Test in der Windows-CI.
- Obergrenzen in `pyproject.toml` und `requirements.txt` für PyMuPDF (`< 2`), PySide6 (`< 6.12`), rapidocr
  (`< 3.10`) und huggingface-hub (`< 2`): `setup.bat` installiert nach diesen Bereichen und hätte sonst
  ungetestete neue Versionen geholt (PyMuPDF 2.0, PySide6 6.12 mit WebEngine, rapidocr 3.10 mit opencv 5).
  Der Build mit der Lock-Datei ändert sich nicht.
- Mindestversionen für `pillow` (13.0.0), `certifi` (2026.10.19) und `filelock` (4.2.1) auch in `pyproject.toml`
  und `requirements.txt`: schützt Entwicklungsumgebungen und lässt Dependabot den Abhängigkeitsgraphen neu
  einlesen (die Meldungen zu den in 0.8.1 behobenen Lücken blieben sonst offen).

## 0.8.2 – 21.10.2026

**Fehlerbehebung**
- **Silbentrennung am Zeilenende:** Wurde ein Name getrennt („Bergen-⏎thaler“), blieb der Wortteil in der zweiten
  Zeile ungeschwärzt – die Kontrolle nach dem Export bemerkte das nicht. Die Erkennung arbeitet jetzt auf dem
  zusammengefügten Wort und schwärzt beide Teile. Davon profitieren auch Sperrliste, Ausnahmeliste, „Alle
  Vorkommen“ und die nummerierte Ersetzung („Bergen-⏎thaler“ und „Bergenthaler“ erhalten dieselbe Nummer).
  Echte Bindestriche vor einem Großbuchstaben (Doppelname „Schneider-⏎Kowalczyk“) bleiben unverändert.
  Neues Beispiel `samples/trennung.pdf`.
- **Nummern über zwei Zeilen:** Eine am Zeilenende umbrochene IBAN („DE89 3704 0044⏎0532 0130 00“) wurde nicht
  erkannt – die erste Hälfte blieb lesbar, die zweite galt als Telefonnummer. Wo eine Zeile mit einer Ziffer
  endet und die nächste mit einer Ziffer beginnt, prüfen die Muster für IBAN, Telefon, Kreditkarte usw. jetzt
  zusätzlich beide Zeilen zusammen. Übernommen wird nur, was über den Umbruch reicht und noch nicht erkannt war –
  Adressblöcke (Straße⏎PLZ Ort) und Zahlen wie Aktenzeichen oder Seitenzahlen bleiben unverändert.

## 0.8.1 – 21.10.2026

**Sicherheit**
- Pillow 12.3.0 → 13.0.0 (8 Sicherheitslücken beim Einlesen von TIFF, GIF, PPM, EPS und beim Anhängen an PDFs).
  pii-redact nutzt Pillow nur indirekt über die Texterkennung und übergibt dort fertige Bildpunkte, keine Dateien –
  die Lücken waren daher praktisch nicht erreichbar. Pillow 12 erhält keine Korrekturen mehr.
- certifi 2026.7.22 → 2026.10.19 (Zertifikatsliste ohne die kompromittierte Stelle „emSign ECC Root CA – G3“;
  betrifft nur Verbindungen der optionalen KI-Nachprüfung).

**Dokumentation**
- README: Für Windows steht jetzt zuerst der Download der fertigen Version aus dem Release (Python und alle
  Modelle enthalten) – mit Hinweisen zur Windows-Warnung, zu Updates und zur Prüfsumme; die Installation aus dem
  Quellcode folgt danach als Weg für die Entwicklung.

## 0.8.0 – 03.10.2026

> **Hinweis für Skripte mit `pii-redact-cli`:** Vorhandene Ergebnisdateien werden nicht mehr still ersetzt –
> bei wiederholten Läufen `--ueberschreiben` angeben. Die Ausgabe enthält keinen Klartext mehr.

**Barrierefreiheit (erste Verbesserungen)**
- Unter der Fundliste stehen jetzt dauerhaft alle Angaben zum gewählten Fund (Originaltext, Status,
  Texterkennung, Quelle) – bisher nur im Tooltip beim Überfahren mit der Maus.
- Tastatur: Textcursor auf einem Fund wählt ihn aus; Menütaste bzw. Umschalt+F10 öffnet das Kontextmenü an der
  Cursorposition; Leertaste schaltet gewählte Funde an/aus, F2 ändert den Typ.
- Hinweisleiste mit Knopf „Details …“ (vollständige Hinweise); Links in der Hinweisliste per Tastatur bedienbar.
- Vorlesbare Namen und Beschreibungen für Bildschirmleser (Ansichten, Fundliste, Filter, Zoom, Hinweise).
- Bildschirmleser sagen in der Fundliste zu jedem Fund, ob er geschwärzt wird, und kündigen nach der Leertaste
  den neuen Status an.
- Tastaturfokus in der Fundliste landet auf der Spalte „Text“, sodass zuerst der Fund vorgelesen wird.
- Alle Menübefehle haben unterstrichene Buchstaben (z. B. Alt, D, P für PDF-Export);
  die Hilfe erklärt, wie Menü und Werkzeugleiste ohne Maus erreichbar sind.
- Hilfe: neuer Abschnitt „Bedienung ohne Maus und mit Bildschirmleser“ (Kapitel 11).

**Kommandozeile (`pii-redact-cli`)**
- Gibt nur noch Anzahl und Datenart der Funde aus – kein Klartext mehr in Ausgaben, die in Log-Dateien landen
  können. Klartext nur mit `--nur-anzeigen` oder dem neuen `--details`.
- Überschreibt vorhandene Ergebnisdateien nicht mehr still (neu: `--ueberschreiben`); Textdateien werden wie in der
  Oberfläche erst vollständig geschrieben und dann umbenannt.
- Rückgabewert: Bei mehreren Dateien gilt der schwerwiegendste Wert (Reste im Ergebnis vor Fehler); falsche
  Aufrufparameter liefern 1 statt 2 (2 bedeutet „Reste im Ergebnis“).
- Neu: `--version`, `--einstellungen DATEI` (z. B. für Dienstkonten); Warnungen in der Fehlerausgabe.

**Hilfe und README**
- Messwerte zu „Schnell“/„Gründlich“ aktualisiert (neue Testtexte), Kommandozeile mit allen Optionen und
  Rückgabewerten, Fundliste per Tastatur, Einstellung „spaCy-Modell“ beschrieben.

**Intern**
- Sechs neue erfundene Testtexte (Brief, Protokoll, Dienstplan, E-Mail-Verlauf, doppeldeutige Namen,
  Namensvielfalt) mit rund 120 erwarteten Namen und Orten; ein Test prüft die Erkennungsquote, damit
  Paket-Updates die Erkennung nicht unbemerkt verschlechtern.
- Modellvergleich (`tools/modelle_testen.bat`) zusätzlich mit Davlan large und GLiNER2-PII.
- PDF-Laden aufgeteilt (Textebene, Texterkennung, Hinweise in eigenen Funktionen) – Ergebnis unverändert.

## 0.7.2 – 02.10.2026

- Releases: Das Programm-ZIP (`pii-redact-<version>.zip` mit `.sha256`) baut GitHub jetzt automatisch beim
  Veröffentlichen eines Releases; bei öffentlichem Repository mit Herkunftsnachweis. `build.bat` versteht
  dafür `PII_REDACT_PYTHON` (fester Python-Aufruf).
- Beim Öffnen von Word-, Excel- oder PowerPoint-Dateien nennt die Fehlermeldung jetzt den Weg über
  *Speichern als PDF*. Neuer Abschnitt in der Hilfe (Häufige Fragen) und in der README, warum pii-redact sich
  bewusst auf PDF konzentriert.
- Intern: automatische Prüfung jedes Pull Requests (Tests auf Windows, Lint, bekannte Sicherheitslücken),
  Pull Requests gegen `main` nur von `dev`; Regeln zum Umgang mit Abhängigkeiten.

## 0.7.1 – 02.10.2026

- Neu: `SECURITY.md` – wie Sicherheitslücken (z. B. Reste im geschwärzten PDF) vertraulich gemeldet werden.
- Neu: Issue-Vorlagen (`.github/ISSUE_TEMPLATE/`) für Fehler, Erkennung und Verbesserungsvorschläge – mit
  Hinweis, keine echten Daten anzuhängen; Sicherheitslücken verweisen auf die vertrauliche Meldung.
- Neu: Vorlage für Pull Requests (`.github/pull_request_template.md`) – Ziel-Branch `dev`, Checkliste mit
  Datenschutz, Tests und Lizenz.
- Neu: `CONTRIBUTING.md` – Entwicklungsumgebung, Tests, Ablauf (PR gegen `dev`) und Grundregeln des Projekts.
- Geändert: Auch der PDF-Export einer einzelnen Datei (Oberfläche und `pii-redact-cli datei.pdf`) schreibt das
  Ergebnis erst in eine `.tmp`-Datei und benennt sie danach um – wie schon die Ordner-Bearbeitung. Bricht der
  Export ab, bleibt keine halbe PDF unter dem Zielnamen liegen.
- Intern: Hauptfenster in Teil-Module aufgeteilt (`ui/window_*.py`), PDF-Export an einer Stelle gebündelt,
  Aufräumarbeiten; Tests geben Fenster und Modelle nach jedem Test frei (deutlich weniger Speicherbedarf).

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
