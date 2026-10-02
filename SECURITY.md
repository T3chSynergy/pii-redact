# Sicherheit

pii-redact soll personenbezogene Daten zuverlässig aus Dokumenten entfernen. Fehler, durch die geschwärzte
Inhalte trotzdem erhalten bleiben oder Daten das Gerät verlassen, behandeln wir als Sicherheitslücke.

## Unterstützte Versionen

Sicherheitskorrekturen gibt es nur für die **jeweils neueste Version** (siehe
[Releases](https://github.com/T3chSynergy/pii-redact/releases)).

| Version | Unterstützt |
|---|---|
| 0.7.x | ✅ |
| ältere | ❌ |

## Was gilt als Sicherheitslücke?

Zum Beispiel:

- Geschwärzte Inhalte sind im **exportierten PDF** noch vorhanden oder auslesbar – als Text, in Bildern,
  Metadaten, Kommentaren, Lesezeichen, Formularfeldern, Anhängen oder unsichtbaren Ebenen.
- Die **Kontrolle nach dem Export** meldet ein Dokument als sauber, obwohl Reste enthalten sind.
- Der **Text-Export** (TXT/Markdown) enthält Inhalte, die als geschwärzt angezeigt wurden.
- Das Programm baut **Netzwerkverbindungen** auf, obwohl die KI-Nachprüfung ausgeschaltet ist – oder sendet bei
  der KI-Nachprüfung mehr als den geschwärzten Text (z. B. das Original).
- **Klartext** der gefundenen Daten landet im Arbeitsstand (`pii-redact-arbeitsstand.json`), im Protokoll
  (`pii-redact-protokoll.csv`) oder in der Log-Datei.
- Eine präparierte Eingabedatei führt dazu, dass Code ausgeführt oder auf fremde Dateien zugegriffen wird.

**Keine Sicherheitslücke** ist, dass die automatische Erkennung eine Angabe übersieht (z. B. einen
ungewöhnlichen Namen). Die Erkennung ist nie vollständig; das Ergebnis muss immer geprüft werden. Solche Fälle
bitte als normales [Issue](https://github.com/T3chSynergy/pii-redact/issues) melden – mit erfundenen Daten.

## Melden

Bitte **nicht** als öffentliches Issue, sondern vertraulich über
**[„Report a vulnerability“](https://github.com/T3chSynergy/pii-redact/security/advisories/new)**
(Reiter *Security* im Repository).

Hilfreich sind:

- Version von pii-redact (*Hilfe → Über pii-redact*) und Windows-Version
- Schritte zum Nachstellen und was erwartet bzw. tatsächlich passiert ist
- eine **nachgestellte Beispieldatei mit erfundenen Daten**

> **Wichtig:** Bitte **niemals echte Dokumente** oder echte personenbezogene Daten mitschicken – auch nicht
> geschwärzt. Ein Beispiel mit erfundenen Inhalten genügt, um den Fehler nachzuvollziehen.

## Ablauf

- Rückmeldung zur Meldung in der Regel **innerhalb von 14 Tagen**.
- Bestätigte Lücken werden in einer neuen Version behoben und im [CHANGELOG](CHANGELOG.md) sowie in den
  Release-Hinweisen genannt; auf Wunsch mit Nennung der meldenden Person.
- Bitte veröffentlichen Sie Details erst, wenn eine korrigierte Version verfügbar ist.

pii-redact ist ein kleines Open-Source-Projekt ohne Gewährleistung (siehe [LICENSE](LICENSE)).
