# Build und Verteilung

Diese Anleitung richtet sich an die IT. Anwender finden ihre Hilfe im Programm unter **Hilfe → Anwenderhilfe (F1)**.

## Fertige Versionen herunterladen

Jede Version gibt es fertig gebaut unter **[Releases](https://github.com/T3chSynergy/pii-redact/releases)**:
`pii-redact-<version>.zip` (Programmordner), die zugehörige `.sha256`-Prüfsumme und `davlan-xlmr-ner.zip`
(Transformer-Modell, ist im Programm-ZIP bereits enthalten). Das ZIP baut GitHub automatisch mit `build.bat`
aus genau dem Quellcode des Releases (Workflow `.github/workflows/build.yml`). Bei öffentlichem Repository
belegt ein Herkunftsnachweis, aus welchem Commit es stammt:

```bat
gh attestation verify pii-redact-<version>.zip --repo T3chSynergy/pii-redact
```

Die EXE-Dateien darin sind **nicht signiert**. Wer signieren will, baut selbst (siehe unten) oder signiert die
entpackten EXE-Dateien vor dem Verpacken (Weg C).

## Ergebnis

| Datei | Zweck |
|---|---|
| `dist\pii-redact\` | Programmordner – lauffähig ohne Installation |
| `dist\pii-redact-<version>.zip` | derselbe Ordner als ZIP (oberste Ebene `pii-redact\`) zur Weitergabe an die IT |

Das Installationspaket (z. B. MSI) erstellt die IT mit eigenen Werkzeugen aus diesem Ordner. Inhalt:

* `pii-redact.exe` – Oberfläche (Startmenü-Eintrag „pii-redact“)
* `pii-redact-cli.exe` – Kommandozeile (Stapelverarbeitung, `--selftest`)
* `_internal\` – Python, Bibliotheken, spaCy-Modell, Transformer-Modell (Modus „Gründlich“),
  Texterkennung (RapidOCR mit PP-OCRv6-Modellen, ca. 31 MB), Anwenderhilfe
* `LIZENZEN.txt`, `LIZENZTEXTE.txt` – verwendete Open-Source-Komponenten mit Lizenzen und Lizenztexten
  (beim Build von `tools\lizenzen.py` erzeugt; Übersicht auch unter *Hilfe → Über pii-redact*)

Auf den Zielrechnern ist **kein Python** nötig. Das Programm arbeitet **vollständig offline** und
baut keine Netzwerkverbindungen auf – Firewall-Regeln sind nicht erforderlich.

Größe (inkl. Texterkennung): ca. 750–950 MB entpackt, ZIP ca. 500 MB. Empfohlen: Windows 10/11 64 Bit, 8 GB RAM.

## Build-Rechner einrichten (einmalig)

1. **Windows 10/11 x64** mit **Python 3.14** (python.org, „py launcher“ mitinstallieren; 3.13 und 3.12
   funktionieren ebenfalls). Nutzt eine vorhandene Build-Umgebung `.venv-build` eine andere Python-Version,
   legt `build.bat` sie automatisch neu an.
2. Das **Transformer-Modell** muss unter `models\ner\davlan-xlmr-ner\` liegen – siehe nächster Abschnitt.
3. Beim ersten Build braucht der Rechner Internet (Python-Pakete, spaCy-Modell). Die Versionen sind in
   `packaging\requirements-lock.txt` fest vorgegeben – jeder Build ist reproduzierbar.

## Quellcode und Transformer-Modell

Der Quellcode liegt im GitHub-Repository der Organisation (https://github.com/T3chSynergy/pii-redact, Lizenz: GNU AGPL-3.0 oder später, siehe `LICENSE`). Holen z. B.
mit GitHub Desktop oder
`git clone https://github.com/T3chSynergy/pii-redact.git`.

Das **Transformer-Modell** (Modus „Gründlich“, ≈ 280 MB, Lizenz AFL-3.0) ist bewusst **nicht** im Repository
(GitHub erlaubt keine Dateien über 100 MB). Es hängt als `davlan-xlmr-ner.zip` am jeweiligen **Release**:

```powershell
# im Projektordner (PowerShell)
Expand-Archive davlan-xlmr-ner.zip -DestinationPath models\ner\
Get-FileHash models\ner\davlan-xlmr-ner\model.onnx -Algorithm SHA256
```

Die erwarteten Prüfsummen stehen in `packaging\modell.sha256`. Das ZIP erstellt der Entwickler einmalig aus
seinem Modellordner (`Compress-Archive models\ner\davlan-xlmr-ner davlan-xlmr-ner.zip`) und lädt es beim
Release hoch. Der Modellordner enthält den Lizenztext `LICENSE-AFL-3.0.txt` (Academic Free License 3.0), der
bei jeder Weitergabe mitgehen muss; im Programmpaket steht er zusätzlich in `LIZENZTEXTE.txt`. Alternativ lässt sich das Modell mit `tools\modelle_testen.bat` neu erzeugen (braucht Zugriff auf
huggingface.co; die Prüfsummen können dann abweichen).

Ohne Modell: `build.bat /ohne-gruendlich` (nur Modus „Schnell“).

## Bauen

```bat
build.bat
```

Ablauf: eigene Build-Umgebung `.venv-build` → Pakete in festen Versionen → PyInstaller →
**Selbsttest** des fertigen Programms (beide Analyse-Modi, Anwenderhilfe, Start der Oberfläche) → ZIP
(mit SHA-256-Prüfsumme in der Ausgabe). Schlägt ein Schritt fehl, bricht der Build ab.

| Option | Wirkung |
|---|---|
| `/neu` | Build-Umgebung komplett neu anlegen (nach Änderung der Lock-Datei) |
| `/ohne-zip` | nur `dist\pii-redact\` erzeugen (schneller, z. B. zum Testen) |
| `/ohne-gruendlich` | ohne Transformer-Modell (kleiner, nur Modus „Schnell“); das ZIP heißt dann `pii-redact-<version>-schnell.zip` |

`PII_REDACT_PYTHON` legt den Python-Aufruf fest, z. B. `set PII_REDACT_PYTHON=C:\Python314\python.exe` (sonst sucht
`build.bat` über den py-Launcher 3.14, 3.13, 3.12).

Gepackt wird mit dem `tar.exe` von Windows (ab Windows 10 1803), sonst mit PowerShell (`Compress-Archive`,
deutlich langsamer). Die früheren Schalter `/ohne-msi` und `/nur-msi` gibt es nicht mehr; `build.bat` bricht
dann mit einem Hinweis ab.

**Code-Signierung:** siehe nächsten Abschnitt.

## Code-Signierung

**Warum?** Unsignierte Programme, die mit PyInstaller gebaut wurden, stufen Virenscanner gern als
verdächtig ein. Außerdem lassen sich AppLocker-/WDAC-Regeln nach Herausgeber nur mit signierten Dateien
nutzen. Signiert werden `pii-redact.exe` und `pii-redact-cli.exe`; das Installationspaket signiert die IT beim
Erstellen mit ihren eigenen Werkzeugen.

Die EXE-Dateien müssen **vor** dem Verpacken signiert werden. `build.bat` signiert sie vor dem Selbsttest und
dem ZIP, wenn vor dem Aufruf die Umgebungsvariable `SIGNTOOL_ARGS` gesetzt ist.

### Voraussetzungen

| | |
|---|---|
| **Zertifikat** | Codesignatur-Zertifikat (erweiterte Schlüsselverwendung „Codesignatur“, 1.3.6.1.5.5.7.3.3). Für die **interne Verteilung** genügt ein Zertifikat der eigenen PKI (z. B. AD CS, Vorlage „Codesignatur“); die Clients müssen der Stammzertifizierungsstelle vertrauen (meist ohnehin per GPO). Ein öffentliches Zertifikat ist nur für eine Weitergabe nach außen nötig – dessen Schlüssel liegt dann vorschriftsgemäß auf einem Token/HSM. |
| **signtool.exe** | Teil des Windows SDK (Komponente „Windows SDK Signing Tools for Desktop Apps“). `build.bat` findet es im `PATH` oder automatisch unter `C:\Program Files (x86)\Windows Kits\10\bin\…\x64\`. |
| **Zeitstempel** | Empfohlen: Mit Zeitstempel (`/tr`) bleibt die Signatur gültig, auch wenn das Zertifikat später abläuft. Übertragen wird dabei nur ein Hashwert. Ohne Zeitstempel (z. B. Build-Rechner ohne Internet und ohne internen Zeitstempeldienst) `/tr … /td SHA256` weglassen – dann vor Ablauf des Zertifikats neu signieren bzw. neu bauen. |

### `SIGNTOOL_ARGS` – Beispiele

Immer in derselben Konsole setzen, in der danach `build.bat` läuft (ohne Anführungszeichen um den ganzen
Wert; Pfade mit Leerzeichen einzeln in Anführungszeichen):

| Zertifikat liegt … | Beispiel |
|---|---|
| im Zertifikatspeicher des Benutzers (auch Token/Smartcard mit Treiber) | `set SIGNTOOL_ARGS=/fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 0123456789ABCDEF0123456789ABCDEF01234567` |
| im Zertifikatspeicher des Computers | wie oben, zusätzlich `/sm` |
| als PFX-Datei | `set SIGNTOOL_ARGS=/fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f "D:\Zertifikate\codesign.pfx" /p <Kennwort>` |
| in Azure Artifact Signing (früher Trusted Signing) | siehe **Weg D** unten |

* `/sha1 …` ist der **Fingerabdruck** des Zertifikats (Zertifikate-Konsole → Zertifikat → Details →
  „Fingerabdruck“, ohne Leerzeichen). Alternativ wählt `/a` automatisch das passendste Zertifikat – nur
  sinnvoll, wenn genau ein Codesignatur-Zertifikat vorhanden ist.
* Statt DigiCert kann jeder RFC-3161-Zeitstempeldienst verwendet werden, z. B. der Ihrer Zertifizierungsstelle.
* PFX-Kennwörter mit Sonderzeichen (`% & ^ !`) machen in Batch-Dateien Probleme – besser ein Zertifikat im
  Speicher oder auf dem Token verwenden.

### Weg A: Die IT baut selbst (empfohlen, der Schlüssel verlässt die IT nicht)

1. **Build-Rechner einrichten** wie oben unter „Build-Rechner einrichten“, zusätzlich signtool (Windows SDK).
2. **Quellcode und Modell holen:** Repository klonen und das Modell aus dem Release entpacken (siehe
   „Quellcode und Transformer-Modell“). Ohne GitHub-Zugang geht es auch per Kopie vom Entwicklungsrechner:
   ```bat
   robocopy "<Quelle>\pii-redact" "D:\build\pii-redact" /E /XD .venv .venv-build .venv-tools dist build __pycache__ .git test-samples test-samples_geschwaerzt
   ```
   (dann kommt `models\ner\davlan-xlmr-ner\` gleich mit).
3. **Bauen und signieren** (Konsole im Projektordner):
   ```bat
   set SIGNTOOL_ARGS=/fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <Fingerabdruck>
   build.bat
   ```
   Der erste Build braucht Internet (Python-Pakete) und legt die Build-Umgebung an. In der Ausgabe erscheinen
   „Code-Signierung mit: …\signtool.exe“ und „Signiere EXE-Dateien …“. Schlägt das
   Signieren fehl, bricht der Build ab. Der Selbsttest läuft mit den bereits signierten Programmen.
4. **Prüfen:**
   ```bat
   signtool verify /pa /v dist\pii-redact\pii-redact.exe
   signtool verify /pa /v dist\pii-redact\pii-redact-cli.exe
   ```
   oder im Explorer: Eigenschaften → Registerkarte „Digitale Signaturen“.
5. Aus `dist\pii-redact\` bzw. dem ZIP das Installationspaket erstellen und verteilen (siehe unten).

### Weg B: Die IT stellt dem Entwickler ein Zertifikat bereit

Der Entwickler baut und signiert dann selbst mit `build.bat`. Die IT stellt bereit:

* ein **Codesignatur-Zertifikat im persönlichen Zertifikatspeicher** des Entwicklers (Registrierung über die
  Zertifikate-Konsole oder automatische Registrierung, privater Schlüssel möglichst **nicht exportierbar**),
  bzw. einen Token samt Treiber – eine PFX-Datei nur, wenn die eigenen Richtlinien das erlauben;
* den **Fingerabdruck** des Zertifikats für `/sha1`;
* die Adresse des **Zeitstempeldienstes** (oder die Freigabe, einen öffentlichen zu nutzen).

Ist das Stammzertifikat der ausstellenden Stelle auf dem Entwicklungsrechner nicht vertrauenswürdig, meldet
`build.bat` eine Warnung („Signatur wird auf diesem Rechner nicht als vertrauenswürdig erkannt“) – das
Signieren selbst funktioniert trotzdem.

### Weg C: Nachträglich signieren (ohne Zertifikat beim Entwickler)

1. Entwickler: `build.bat` – übergibt das ZIP an die IT.
2. IT: nach dem Entpacken `signtool sign <Argumente> pii-redact\pii-redact.exe pii-redact\pii-redact-cli.exe`
3. IT: Installationspaket aus dem signierten Ordner erstellen (und bei Bedarf ebenfalls signieren).

### Weg D: Azure Artifact Signing (vorhandener Azure-Tenant)

Microsoft-Signierdienst (früher „Trusted Signing“): Microsoft prüft die Organisation einmalig, verwahrt den
Schlüssel in eigenen HSMs und signiert auf Anfrage – ohne Token, ohne PFX-Datei. Kosten: Tarif *Basic* für bis zu
5 000 Signaturen im Monat (pii-redact braucht 2 pro Build). Die Zertifikate sind nur wenige Tage gültig und werden
automatisch erneuert; ein **Zeitstempel ist daher Pflicht**. Öffentlich vertrauenswürdige Zertifikate gibt es für
Organisationen u. a. in der EU. Anleitungen von Microsoft: *Quickstart: Set up Artifact Signing* und
*Set up signing integrations* (learn.microsoft.com/azure/artifact-signing).

**Einmalig in Azure (IT):**

1. Im Abonnement den Ressourcenanbieter **`Microsoft.CodeSigning`** registrieren.
2. Ein **Artifact Signing Account** anlegen (Portal: „Artifact Signing Accounts“), Region z. B. *West Europe*
   (Endpunkt `https://weu.codesigning.azure.net`), *North Europe* (`https://neu.codesigning.azure.net`) oder
   *Switzerland North* (`https://swn.codesigning.azure.net`), Tarif *Basic*.
3. **Identitätsprüfung** der Organisation im Portal (Rolle „Artifact Signing Identity Verifier“ nötig):
   *Public* für öffentlich vertrauenswürdige Signaturen, *Private* für eine nur intern vertrauenswürdige
   Signatur (z. B. für App Control/WDAC). Benötigt u. a. Firmendaten, zwei E-Mail-Adressen auf der eigenen
   Domain, aktuellen Registerauszug und die Ausweisprüfung einer verantwortlichen Person. Dauer laut Microsoft
   1–20 Werktage.
4. Ein **Zertifikatprofil** anlegen (Typ *Public Trust* bzw. *Private Trust*).
5. Wer signiert, erhält die Rolle **„Artifact Signing Certificate Profile Signer“** – der Benutzer, der
   `build.bat` ausführt, oder ein Dienstprinzipal für unbeaufsichtigte Builds.

**Einmalig auf dem Build-Rechner:**

1. Signaturwerkzeuge installieren: `winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`
   (enthält die Bibliothek `Azure.CodeSigning.Dlib.dll`; Pfad danach z. B. mit
   `where /r "%ProgramFiles%" Azure.CodeSigning.Dlib.dll` bzw. unter `%LOCALAPPDATA%` suchen). Außerdem nötig:
   .NET 8 Runtime (x64) und ein aktuelles Windows SDK mit signtool – die SDK-Version 10.0.20348 wird laut
   Microsoft **nicht** unterstützt. `build.bat` verwendet automatisch das neueste installierte SDK.
2. `packaging\artifact-signing.example.json` nach `packaging\artifact-signing.json` kopieren und Endpunkt,
   Kontoname und Profilname eintragen. Die Datei enthält keine Geheimnisse.
3. Anmeldung: Die Bibliothek nutzt die üblichen Azure-Anmeldungen der Reihe nach
   (*DefaultAzureCredential*). Am einfachsten vor dem Build **`az login`** (Azure CLI) mit dem berechtigten
   Benutzer. Für unbeaufsichtigte Builds einen Dienstprinzipal über die Umgebungsvariablen `AZURE_TENANT_ID`,
   `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` – bzw. auf einer Azure-VM eine benutzerseitig zugewiesene
   verwaltete Identität.

**Bauen:**

```bat
az login
set SIGNTOOL_ARGS=/fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "<Pfad>\x64\Azure.CodeSigning.Dlib.dll" /dmdf "%CD%\packaging\artifact-signing.json"
build.bat
```

Die Signatur trägt den von Microsoft geprüften Namen der Organisation. Bei „403 Forbidden“ passt meist der
Endpunkt nicht zur Region des Kontos oder die Rolle fehlt; schlägt signtool ohne Meldung fehl, fehlt oft die
.NET-8-Laufzeit.

### Hinweise für den Betrieb

* **AppLocker:** Herausgeberregel auf das Codesignatur-Zertifikat ist möglich.
* **WDAC im strengen Modus:** Die Bibliotheken unter `_internal\` (Python, Qt, KI-Laufzeit) sind nur
  teilweise von ihren Herstellern signiert. Dann eine Pfadregel für den Installationsordner oder
  Hash-Regeln verwenden – oder zusätzlich alle `*.dll`/`*.pyd` unter `pii-redact\_internal\` vor dem
  Verpacken signieren (Weg C, Schritt 2 entsprechend erweitern).
* **Zertifikat läuft ab:** Mit Zeitstempel bleiben bereits verteilte Versionen gültig; neue Versionen mit
  dem neuen Zertifikat bauen.

## Verteilung

Die IT erstellt aus dem Programmordner mit eigenen Werkzeugen ein Installationspaket (z. B. MSI für SCCM).
Hinweise dazu:

| Punkt | Empfehlung |
|---|---|
| Zielordner | `C:\Program Files\pii-redact\` – den **ganzen** Ordner übernehmen, die Struktur (`_internal\`) nicht verändern |
| Installation | pro Computer (für alle Benutzer), 64 Bit; kein Neustart nötig |
| Verknüpfung | Startmenü-Eintrag „pii-redact“ auf `pii-redact.exe` |
| Kommandozeile | optional den Installationsordner in den `PATH` aufnehmen (für `pii-redact-cli`) |
| Erkennungsmethode | Datei `pii-redact.exe` mit Dateiversion ≥ `<version>` (die EXE trägt die Versionsnummer aus `src\pii_redact\__init__.py`) |
| Anforderungen | Windows 10/11 64 Bit, empfohlen 8 GB RAM |

Das Programm schreibt nie in seinen Installationsordner (Einstellungen, Protokoll und Vorgaben: siehe
„Wo liegt was auf dem Client?“) und legt selbst keine Registry-Einträge an.

### Updates

1. Versionsnummer erhöhen in `src\pii_redact\__init__.py` (einzige Stelle, z. B. `0.5.0` → `0.6.0`).
2. `build.bat` → neues ZIP `pii-redact-<version>.zip`.
3. Die IT erstellt daraus ein neues Paket, das die alte Version ersetzt. Beim Aktualisieren den alten
   Programmordner vollständig entfernen, nicht nur überschreiben – sonst bleiben veraltete Bibliotheken liegen.

## Zentrale Vorgaben (optional)

Datei `C:\ProgramData\pii-redact\defaults.json` – z. B. per SCCM-Skript, Paket oder GPO verteilen.
Vorlage: `deploy\defaults.example.json`.

```json
{
  "analysis_mode": "gruendlich",
  "threshold": 0.45,
  "replace_mode": "platzhalter",
  "show_original": false,
  "allow_list": ["Musterfirma GmbH"],
  "deny_list": ["Projekt Falke"]
}
```

* Werte gelten als **Voreinstellung** für alle Nutzer; persönliche Einstellungen überschreiben sie.
* `analysis_mode`: Standard im Programm ist `"gruendlich"` (findet deutlich mehr Namen/Orte, ca. 1 GB RAM,
  ca. 0,3–1 s pro Seite). Für Rechner mit wenig Arbeitsspeicher (≤ 4 GB) `"schnell"` vorgeben (ca. 0,4 GB).
  Fehlt das Transformer-Modell (Build mit `/ohne-gruendlich`), nutzt das Programm automatisch „Schnell“.
* `allow_list` (nie schwärzen) und `deny_list` (immer schwärzen) gelten **immer zusätzlich** zu den
  persönlichen Listen.
* Weitere Schlüssel: `entities` (Liste der Datenarten), `pdf_labels`, `spacy_model`, `ner_model`,
  `ocr` (`true`/`false` – Texterkennung für gescannte Seiten, Standard `true`),
  `compact_notices` (`true` = Hinweise nur als Zähler in der Statusleiste und als Seitensymbol, ohne orange
  Hinweiszeile; Standard `false`).
* **Festlegen statt vorschlagen:** Schlüssel, die unter `"locked"` stehen, gelten immer mit dem Wert aus
  `defaults.json`; persönliche Einstellungen werden ignoriert, das Feld ist in den Einstellungen ausgegraut.
  Beispiel – Texterkennung erzwingen und kompakte Hinweise verbieten:
  `"ocr": true, "compact_notices": false, "locked": ["ocr", "compact_notices"]`.
  Ein Schlüssel wird nur gesperrt, wenn er in `defaults.json` auch einen Wert hat.

Zusätzliche Modelle können – ohne neues Installationspaket – unter `C:\ProgramData\pii-redact\models\ner\<name>\`
abgelegt werden.

## KI-Nachprüfung (optional)

Ab 0.5.0 kann ein Sprachmodell das **geschwärzte Ergebnis** eines Dokuments bewerten („Sind Personen trotzdem
erkennbar – durch übersehene Angaben oder den Zusammenhang?“). Die Funktion ist **standardmäßig aus** und
erscheint erst, wenn Server und Modell eingetragen sind. Das Programm stellt kein Modell bereit – ob und welches
LLM verwendet wird, entscheidet die IT.

**Was gesendet wird:** Nur auf Knopfdruck der Anwenderin, nur der geschwärzte Text (Platzhalter nummeriert,
z. B. `[PERSON_1]`), nie das Original, keine Bilder. Lange Dokumente werden auf mehrere Anfragen verteilt (bei
PDFs an Seitengrenzen). Da übersehene Angaben im gesendeten Text stehen können, gilt die Übertragung
datenschutzrechtlich als Übermittlung personenbezogener Daten an den Server-Betreiber.

**Anforderungen an den Server**

| Punkt | Anforderung |
|---|---|
| Schnittstelle | OpenAI-kompatibel: `POST …/chat/completions` – direkt am Modellserver oder über ein vorgeschaltetes Portal bzw. einen LLM-Proxy. `llm_url` kann die Basis-Adresse (Programm hängt `/chat/completions` an) oder der vollständige Endpunkt sein. |
| Modell | instruktionsfähig, gutes Deutsch, liefert zuverlässig JSON; Richtwert (nicht gemessen): ab ca. 27–32 Mrd. Parametern für Kontext-Einschätzungen – vor dem Einsatz mit eigenen Testdokumenten prüfen |
| Kontextfenster | mind. 16 000 Token bei der Voreinstellung `llm_max_chars` = 24 000 Zeichen (kleiner stellen, wenn das Modell weniger kann). Das Kontextfenster muss **auf dem Server tatsächlich so eingestellt** sein – manche Server kürzen zu lange Anfragen sonst stillschweigend. |
| Antwortzeit | Zeitlimit je Anfrage `llm_timeout` (Standard 180 s) |
| Datenschutz | Betrieb im eigenen Rechenzentrum oder vertraglich abgesichert (AVV); keine Speicherung/kein Training mit den Anfragen; Datenschutzfreigabe bzw. DSFA nach Hausregeln |
| Netz | Clients erreichen den Server per HTTPS; Proxy- und Zertifikatseinstellungen von Windows werden verwendet |
| Anmeldung | optional `Authorization: Bearer <Schlüssel>` |

**Einrichtung per `defaults.json`** – Vorlage: `deploy\defaults.ki.example.json`

```json
{
  "llm_enabled": true,
  "llm_url": "https://llm.intern.example/v1",
  "llm_model": "modellname-laut-server",
  "llm_timeout": 300,
  "llm_max_chars": 24000,
  "locked": ["llm_enabled", "llm_url", "llm_model", "llm_max_chars"]
}
```

> **Wichtig: sperren.** Ohne `locked` gelten die Werte nur als Voreinstellung. Das Programm speichert beim
> ersten Ändern irgendeiner Einstellung (schon beim Zoom) alle Werte ins Benutzerprofil – spätere Änderungen
> der IT (anderer Server, anderes Modell) kämen bei diesen Nutzern nicht mehr an. Gesperrte Schlüssel werden
> immer aus `defaults.json` gelesen und sind in den Einstellungen ausgegraut. Fehlt `llm_enabled` in der
> Liste, können Nutzer die Funktion für sich ausschalten.

* **API-Schlüssel** möglichst über die Umgebungsvariable `PII_REDACT_LLM_KEY` setzen (z. B. per GPO); sie hat
  Vorrang. Ein `llm_api_key` in `defaults.json` wäre für alle Nutzer des Rechners lesbar.
* Verbindung prüfen am Client unter *Einstellungen → KI-Nachprüfung → Verbindung testen*. Der Test schickt eine
  Anfrage in voller Länge (`llm_max_chars`) und prüft, ob das Modell sie vollständig gesehen hat. Schlägt er mit
  dem Hinweis auf das Kontextfenster fehl: Kontext am Server vergrößern oder `llm_max_chars` verkleinern.
* Vor dem ersten Senden nach Programmstart fragt das Programm nach und nennt Server und Modell.
* Ordner-Bearbeitung: Protokollspalte „KI-Prüfung“ (Risiko, Anzahl Hinweise, Modell, Zeitpunkt – keine Inhalte).
* **Modell auswählen:** `tools\ki_vergleich.bat --url <Server> --modell <a> --modell <b>` vergleicht Modelle
  mit sechs frei erfundenen Testfällen (Restrisiko, gefundene Stellen, Fehlalarme, Zeit) und schreibt einen
  Bericht `ki_vergleich_<Datum>.md`. Die Modelle unterscheiden sich hier deutlich – vor der Freigabe vergleichen.
* Zum Testen eignet sich auch ein Cloud-Dienst mit OpenAI-kompatibler Schnittstelle (z. B. OpenRouter,
  Basis-URL `https://openrouter.ai/api/v1`, Modellname wie dort angegeben) – dann **nur mit Beispiel- bzw.
  Testdaten**.

Das Programm schickt zusätzlich bei **jeder** Anfrage eine zweiteilige Prüfkennung (Anfang und Ende der
Anfrage) mit und verwirft Antworten, in denen sie fehlt – ein gekürzter Kontext führt so zu einer Fehlermeldung
statt zu einer scheinbar harmlosen Bewertung.

## Wo liegt was auf dem Client?

| Pfad | Inhalt |
|---|---|
| `C:\Program Files\pii-redact\` (bzw. Zielordner der IT) | Programm (schreibgeschützt für Nutzer) |
| `C:\ProgramData\pii-redact\defaults.json` | zentrale Vorgaben (optional) |
| `%APPDATA%\pii-redact\settings.json` | persönliche Einstellungen |
| `%LOCALAPPDATA%\pii-redact\logs\pii-redact.log` | Programmprotokoll für den Support (keine Dokumentinhalte) |
| Zielordner der Ordner-Bearbeitung | Ergebnisse, `pii-redact-protokoll.csv`, `pii-redact-arbeitsstand.json` |

## Prüfen einer Installation

```bat
"C:\Program Files\pii-redact\pii-redact-cli.exe" --selftest
```

zeigt Modellordner, spaCy-Modell, Transformer-Modell, Anwenderhilfe und testet beide Analyse-Modi.
