"""Erzeugt die Testtexte unter samples/erkennung/ und ihre Soll-Funde in samples/soll_funde.json.

Die Texte stehen unten mit Markierungen; das Skript schreibt sie ohne Markierungen als .txt und trägt
die markierten Stellen (mit eindeutigem Kontext) in soll_funde.json ein. Andere Einträge der Datei
(beispiel.txt, beispiel.md, schwierig.txt) bleiben unverändert.

    [[P:…]]  Person (muss erkannt werden)
    [[L:…]]  Ort (muss erkannt werden)
    [[N:…]]  neutral – zählt weder als Treffer noch als Fehlalarm (Straßen, Firmen, Einrichtungen)

Alles Übrige darf nicht als Person/Ort erkannt werden (sonst Fehlalarm) – z. B. „der Koch“, „im Winter“,
„Rosenmontag“. Alle Namen sind erfunden, Adressen nur mit .example.

    python samples/make_erkennung.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "erkennung"
GOLD_FILE = HERE / "soll_funde.json"
TYPES = {"P": "PERSON", "L": "LOCATION", "N": "_neutral"}
MARK = re.compile(r"\[\[([PLN]):(.+?)\]\]", re.S)

TEXTE: dict[str, str] = {}

TEXTE["brief_krankenkasse.txt"] = """\
[[N:Gesundheitskasse Mittelrhein]] · [[N:Postfach 10 20 30]] · 56068 [[L:Koblenz]]

Herrn
[[P:Dieter Wallraff]]
[[N:Kastanienweg 12]]
56410 [[L:Montabaur]]

Ihr Zeichen: –
Unser Zeichen: KV-2026-004711 / [[N:Ha]]
Ansprechpartnerin: Frau [[P:Hartmann]], Durchwahl -214
E-Mail: s.hartmann@gesundheitskasse.example

[[L:Koblenz]], 21. September 2026

Antrag auf Kostenübernahme für Ihre Ehefrau [[P:Gisela Wallraff]], geb. [[P:Brandt]]

Sehr geehrter Herr [[P:Wallraff]],

vielen Dank für Ihren Antrag vom 2. September 2026. Ihre Ehefrau ist seit 2011 bei uns familienversichert.
Für die Entscheidung benötigen wir noch den Bericht der behandelnden Ärztin, Dr. [[P:Merve Aksoy]]
([[N:Praxis am Markt]], [[L:Bad Ems]]). Bitte senden Sie uns außerdem die Bescheinigung des
[[N:Pflegedienstes Sonnenschein]] zu, den Ihre Tochter [[P:Claudia]] beauftragt hat.

Die Unterlagen können Sie auch in unserer Geschäftsstelle in [[L:Lahnstein]] abgeben. Dort hilft Ihnen
Herr [[P:Okafor]] gern weiter.

Mit freundlichen Grüßen
Im Auftrag

[[P:Sandra Hartmann]]
Sachbearbeitung Leistungen
"""

TEXTE["protokoll.txt"] = """\
Protokoll der Sitzung des Elternbeirats
Datum: 15. September 2026, 19:00–21:10 Uhr
Ort: Aula der [[N:Grundschule am Lindenhof]], [[L:Fulda]]

Anwesend: Frau Dr. [[P:Engel]] (Vorsitz), Herr [[P:Petrović]], Frau [[P:Lindqvist]], Herr [[P:Brückner]],
Frau [[P:Haas]] (Protokoll)
Entschuldigt: Herr [[P:Albrecht]], Frau [[P:Nowak-Riedel]]
Gast: Herr [[P:Schmid]] (Schulleitung)

TOP 1 – Begrüßung
Die Vorsitzende begrüßt die Anwesenden. Das Protokoll der letzten Sitzung wird ohne Änderungen genehmigt.

TOP 2 – Schulfest
Hr. [[P:Petrović]] berichtet vom Schulfest. Der Erlös von 1.240 € geht an den Förderverein. Fr. [[P:Lindqvist]]
schlägt vor, im nächsten Jahr wieder den Caterer aus [[L:Hünfeld]] zu beauftragen. [[P:Brückner]] widerspricht:
Der Koch sei zwar gut, aber zu teuer gewesen.

TOP 3 – Schulweg
Mehrere Eltern aus [[L:Petersberg]] und [[L:Künzell]] beklagen fehlende Lotsen an der [[N:Leipziger Straße]].
Herr [[P:Schmid]] sagt zu, das Thema mit der Stadt zu klären. [[N:E.]] (Vorsitz) bittet um Rückmeldung bis
zum Herbst.

TOP 4 – Verschiedenes
Frau [[P:Haas]] weist darauf hin, dass [[P:Jonas]] aus der 3b nach den Herbstferien nach [[L:Kassel]] zieht.
Die nächste Sitzung findet nach den Winterferien statt.

gez. [[P:Haas]]
"""

TEXTE["dienstplan.txt"] = """\
Dienstplan Pflegestation 4 – Kalenderwoche 41

Name               | Funktion        | Mo  | Di  | Mi  | Do  | Fr  | Wohnort
[[P:Hoffmann, Birgit]]    | Stationsleitung | F   | F   | F   | –   | F   | [[L:Gießen]]
[[P:Kaya, Emre]]          | Pflegefachkraft | S   | S   | –   | N   | N   | [[L:Wetzlar]]
[[P:Lewandowska, Agata]]  | Pflegefachkraft | N   | N   | N   | –   | –   | [[L:Marburg]]
[[P:Fischer, Tim]]        | Auszubildender  | F   | F   | S   | S   | –   | [[L:Gießen]]
[[P:Mensah, Kofi]]        | Pflegehelfer    | –   | S   | S   | F   | F   | [[L:Butzbach]]
[[P:Ritter, Johanna]]     | Pflegefachkraft | S   | –   | F   | F   | S   | [[L:Lich]]

Vertretung bei Krankheit: [[P:B. Hoffmann]] → [[P:J. Ritter]]
Rufbereitschaft: [[P:Kaya]] (Mo–Mi), [[P:Lewandowska]] (Do–Fr)

Kürzel: F = Frühdienst, S = Spätdienst, N = Nachtdienst

Telefonliste
[[P:Birgit]]   0641 555-1201
[[P:Emre]]     0641 555-1202
[[P:Agata]]    0641 555-1203
[[P:Kofi]]     0641 555-1205
"""

TEXTE["mailverlauf.txt"] = """\
Von: [[P:Lukas Brenner]] <l.brenner@stadtwerke-beispiel.example>
Gesendet: Mittwoch, 30. September 2026 16:42
An: [[P:Marie Okonkwo]] <m.okonkwo@stadtwerke-beispiel.example>
Cc: [[P:Henrik Larsen]]
Betreff: AW: Zählertausch [[L:Paderborn]]

Hallo [[P:Marie]],

danke für die schnelle Antwort. Ich habe mit Herrn [[P:Strobel]] gesprochen, der Termin am Montag passt.
Kannst du [[P:Henrik]] Bescheid geben? Er fährt dann direkt von [[L:Bielefeld]] aus los.

Viele Grüße
[[P:Lukas]]

--
[[P:Lukas Brenner]]
Netzservice · [[N:Stadtwerke Beispiel GmbH]]
[[N:Am Hafen 5]], 33098 [[L:Paderborn]]

-----Ursprüngliche Nachricht-----
Von: [[P:Marie Okonkwo]]
Gesendet: Mittwoch, 30. September 2026 11:05
An: [[P:Lukas Brenner]]
Betreff: Zählertausch [[L:Paderborn]]

Hi [[P:Lukas]],

Familie [[P:Strobel]] aus [[L:Salzkotten]] hat sich wegen des Zählertauschs gemeldet. Frau [[P:Strobel]]
ist nur vormittags erreichbar. Laut [[P:Okan]] aus der Disposition wäre Montag frei.

> Bitte auch an den Ablesetermin in [[L:Delbrück]] denken.
> – [[P:H. Larsen]]

LG [[P:Marie]]
"""

TEXTE["doppeldeutig.txt"] = """\
Notizen zur Teamwoche

Im Winter fahren wir wieder ins [[N:Allgäu]]. [[P:Thomas Winter]] organisiert die Unterkunft, Frau [[P:Sommer]]
kümmert sich um die Anreise. Der Koch der Hütte kocht vegetarisch, Herr [[P:Koch]] aus der Buchhaltung
hat das schon geprüft.

Am Rosenmontag bleibt das Büro geschlossen. [[P:Rosa Fuchs]] übernimmt die Notfallnummer; der Fuchs im
Logo der Firma bleibt, wie er ist. Herr [[P:Bauer]] möchte wissen, ob der Bauer vom Hofladen wieder
liefert.

Frau [[P:Hecht]] und Herr [[P:Wolf]] stellen die neue Software „Adler 3“ vor. Der Adler im
Konferenzraum ist nur Dekoration. [[P:Klaus Engel]] schreibt das Protokoll, [[P:Paula König]] leitet die Runde.

Freitag: Ausflug nach [[L:Kempten]], am Abend Essen im [[N:Gasthof Krone]] in [[L:Immenstadt]].
Wer früher fährt, meldet sich bei [[P:Grace]].
"""

TEXTE["namensvielfalt.txt"] = """\
Anmeldungen Sprachkurs Deutsch B2 – Volkshochschule [[L:Duisburg]]

1. [[P:Zeynep Arslan-Becker]], [[L:Duisburg]]-[[L:Marxloh]]
2. [[P:Mohammed Al-Rashid]], [[L:Oberhausen]]
3. [[P:Tran Van Minh]], [[L:Mülheim an der Ruhr]]
4. [[P:Olena Kovalenko]], [[L:Duisburg]]
5. [[P:Jean-Pierre Dubois]], [[L:Moers]]
6. [[P:Aleksandra Wiśniewska]], [[L:Dinslaken]]
7. [[P:Chidi Eze]], [[L:Essen]]
8. [[P:María José García López]], [[L:Krefeld]]
9. [[P:Siobhán O'Connell]], [[L:Duisburg]]

Kursleitung: [[P:Friederike von Hagen]]
Vertretung: [[P:Ömer Çelik]]

Hinweise der Kursleitung:
Frau [[P:Arslan-Becker]] und Herr [[P:Al-Rashid]] wechseln aus dem B1-Kurs von Herrn [[P:Çelik]].
[[P:Minh]] kann am ersten Termin nicht. [[P:Olena]] fragt, ob ihr Mann [[P:Taras]] nachträglich einsteigen kann.
Herr [[P:Dubois]] wohnt erst seit Juli in [[N:Deutschland]], vorher in [[L:Lyon]].
"""


def context_for(text: str, start: int, end: int) -> str:
    """Kleinster Ausschnitt um [start, end), dessen erstes Vorkommen genau diese Stelle ist."""
    left = right = 0
    while True:
        ctx = text[start - left:end + right]
        if text.index(ctx) == start - left:
            return text[start - left:start] + "«" + text[start:end] + "»" + text[end:end + right]
        if right <= left and end + right < len(text):
            right += 1
        elif start - left > 0:
            left += 1
        else:
            right += 1


def build(marked: str) -> tuple[str, list[tuple[str, int, int]]]:
    plain, spans, pos = [], [], 0
    for m in MARK.finditer(marked):
        plain.append(marked[pos:m.start()])
        start = sum(map(len, plain))
        plain.append(m.group(2))
        spans.append((TYPES[m.group(1)], start, start + len(m.group(2))))
        pos = m.end()
    plain.append(marked[pos:])
    return "".join(plain), spans


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    gold = {k: v for k, v in gold.items() if not k.startswith("erkennung/")}
    for name, marked in TEXTE.items():
        text, spans = build(marked)
        (OUT_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        entry: dict[str, list[str]] = {"PERSON": [], "LOCATION": [], "_neutral": []}
        for typ, s, e in spans:
            entry[typ].append(context_for(text, s, e))
        gold[f"erkennung/{name}"] = {k: v for k, v in entry.items() if v}
        n = sum(1 for t, *_ in spans if t != "_neutral")
        print(f"{name}: {len(text)} Zeichen, {n} Soll-Funde")
    GOLD_FILE.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
