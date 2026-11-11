"""Erkennung personenbezogener Daten mit Presidio + spaCy (Deutsch)."""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING

from .hyphenation import dehyphenate, join_hyphenated
from .models import AnalysisMode, Finding, Settings
from .recognizers_de import LANG, build_recognizers

if TYPE_CHECKING:  # nur für die Typprüfung – Presidio wird erst beim Laden der Engine importiert
    from pathlib import Path

    from presidio_analyzer import AnalyzerEngine

log = logging.getLogger(__name__)

#: Maximale Länge eines Analyseblocks (spaCy-Limit liegt bei 1 Mio. Zeichen).
CHUNK_SIZE = 40_000
#: Kleinere Blöcke im gründlichen Modus → feinere Fortschrittsanzeige.
CHUNK_SIZE_THOROUGH = 6_000

ProgressFn = Callable[[int, int], None]


class ModelMissingError(RuntimeError):
    def __init__(self, model: str):
        super().__init__(
            f"Das spaCy-Sprachmodell „{model}“ wurde nicht gefunden.\n\n"
            f"Entwicklung (im Projekt-venv):\n    python -m spacy download {model}\n\n"
            f"Installierte Version: Das Modell gehört ins Programmpaket (models\\spacy) – "
            f"bitte an die IT wenden."
        )
        self.model = model


class ThoroughModelMissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Für den Modus „Gründlich“ ist kein Transformer-Modell installiert.\n\n"
            "Entwicklung: tools\\modelle_testen.bat ausführen (lädt und wandelt das Modell um).\n"
            "Installierte Version: Modell-Paket von der IT anfordern – oder Modus „Schnell“ verwenden."
        )


class PiiAnalyzer:
    """Kapselt die Presidio-AnalyzerEngine. Das Laden der Modelle dauert einige Sekunden,
    daher wird die Engine erst beim ersten Aufruf erzeugt und dann wiederverwendet.

    Modus „schnell“: spaCy-NER + Muster. Modus „gründlich“: zusätzlich Transformer (ONNX)."""

    def __init__(self, model_name: str = "de_core_news_md", mode: str = AnalysisMode.FAST, ner_model: str = ""):
        self.model_name = model_name
        self.mode = mode
        self.ner_model = ner_model
        self._engine: AnalyzerEngine | None = None
        self.ner_model_dir: Path | None = None

    def matches(self, settings: Settings) -> bool:
        return (self.model_name, self.mode, self.ner_model) == (
            settings.spacy_model, settings.analysis_mode, settings.ner_model
        )

    @classmethod
    def for_settings(cls, settings: Settings) -> PiiAnalyzer:
        return cls(settings.spacy_model, settings.analysis_mode, settings.ner_model)

    # ------------------------------------------------------------------ Engine
    @property
    def loaded(self) -> bool:
        return self._engine is not None

    def load(self) -> None:
        if self._engine is not None:
            return
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        from ..paths import find_ner_model, find_spacy_model

        spacy_ref = find_spacy_model(self.model_name)
        if spacy_ref is None:
            raise ModelMissingError(self.model_name)

        recognizers = build_recognizers()
        if self.mode == AnalysisMode.THOROUGH:
            model_dir = find_ner_model(self.ner_model)
            if model_dir is None:
                raise ThoroughModelMissingError()
            from .onnx_recognizer import OnnxNerRecognizer

            recognizers.append(OnnxNerRecognizer(model_dir, supported_language=LANG))
            self.ner_model_dir = model_dir

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": LANG, "model_name": spacy_ref}],
                "ner_model_configuration": {
                    "model_to_presidio_entity_mapping": {
                        "PER": "PERSON",
                        "LOC": "LOCATION",
                        "ORG": "ORGANIZATION",
                    },
                    "labels_to_ignore": ["MISC"],
                },
            }
        )
        nlp_engine = provider.create_engine()
        registry = RecognizerRegistry(supported_languages=[LANG])
        for rec in recognizers:
            registry.add_recognizer(rec)
        self._engine = AnalyzerEngine(
            registry=registry, nlp_engine=nlp_engine, supported_languages=[LANG]
        )
        log.info("Presidio-Engine geladen: %s, Modus %s", self.model_name, self.mode)

    # ------------------------------------------------------------------ Analyse
    def analyze(
        self,
        text: str,
        settings: Settings,
        progress: ProgressFn | None = None,
    ) -> list[Finding]:
        self.load()
        # Silbentrennung am Zeilenende aufheben („Bergen-⏎thaler“): Erkennung auf dem zusammengefügten Text,
        # die Funde werden am Ende auf den Originaltext zurückgerechnet und decken dann beide Zeilen ab.
        original = text
        text, hmap = dehyphenate(plain_spaces(original))
        entities = [e for e in settings.entities if e != "CUSTOM"]
        raw = self._run(text, entities, settings.threshold, progress)
        raw = clean_ner(raw, text)
        raw.extend(find_terms(text, settings.all_deny))
        raw.extend(self._cross_line(text, entities, settings.threshold, raw))
        raw.extend(self._upper_case(text, entities, settings.threshold, raw))
        findings = apply_allow_list(merge_overlaps(trim(raw, text), text), settings.all_allow)
        for f in findings:
            if hmap:
                f.start, f.end = hmap.span(f.start, f.end)
            f.text = original[f.start:f.end]   # Wortlaut des Dokuments (mit Trennung und Sonder-Leerzeichen)
        return findings

    def _run(self, text: str, entities: list[str], threshold: float,
             progress: ProgressFn | None = None) -> list[Finding]:
        """Presidio-Analyse in Blöcken; Positionen bezogen auf ``text``."""
        engine = self._engine
        assert engine is not None, "erst load() aufrufen"
        raw: list[Finding] = []
        size = CHUNK_SIZE if self.mode == AnalysisMode.FAST else CHUNK_SIZE_THOROUGH
        chunks = list(_chunks(text, size))
        for i, (c_start, c_end) in enumerate(chunks):
            if progress:
                progress(i, len(chunks))
            chunk = text[c_start:c_end]
            if not chunk.strip() or not entities:
                continue
            results = engine.analyze(text=chunk, language=LANG, entities=entities, score_threshold=threshold)
            for r in results:
                s, e = r.start + c_start, r.end + c_start
                rec = (r.recognition_metadata or {}).get("recognizer_name", "")
                raw.append(Finding(s, e, r.entity_type, text[s:e], round(r.score, 2), recognizer=rec))
        if progress:
            progress(len(chunks), len(chunks))
        return raw

    def _upper_case(self, text: str, entities: list[str], threshold: float, found: list[Finding]) -> list[Finding]:
        """Namen und Orte in Großbuchstaben („SCHNEIDER, ANNA“, „80331 MÜNCHEN“ – Formulare, Adressfelder).

        Die Namenserkennung und die Muster für Anschriften stützen sich auf die übliche Schreibweise. Zeilen mit
        Wörtern in Großbuchstaben werden deshalb zusätzlich in normaler Schreibweise geprüft („Schneider, Anna“).
        Übernommen wird nur, was mindestens ein solches Wort enthält und noch nicht gefunden war – und nur von
        Erkennern, die auf die Schreibweise angewiesen sind (Namenserkennung, Anschrift, Name nach Anrede);
        Nummern, E-Mail usw. findet schon der normale Durchgang."""
        probe_text, upper = normal_case(text)
        if not upper:
            return []
        # nur die betroffenen Zeilen prüfen (Absatz als Trenner, damit nichts über Zeilen hinweg passt)
        lines, parts, starts, pos = [], [], [], 0
        for m in re.finditer(r"[^\n]+", text):
            if any(m.start() <= s < m.end() for s, _e in upper):
                lines.append((m.start(), m.end()))
                starts.append(pos)
                parts.append(probe_text[m.start():m.end()])
                pos += m.end() - m.start() + 2
        covered = bytearray(len(text))
        for f in found:
            covered[f.start:f.end] = b"\x01" * (f.end - f.start)
        out = []
        probe = "\n\n".join(parts)
        for f in clean_ner(self._run(probe, entities, threshold), probe):
            k = bisect.bisect_right(starts, f.start) - 1
            a, b = lines[k]
            s, e = f.start - starts[k] + a, f.end - starts[k] + a
            if f.recognizer not in CASE_RECOGNIZERS:
                continue
            if f.recognizer in NER_RECOGNIZERS and any(c.isdigit() for c in f.text):
                # Namen und Orte enthalten keine Ziffern („Iban DE89 …“ hält die Namenserkennung für einen Ort)
                continue
            if e > b or not any(s < ue and us < e for us, ue in upper):
                continue
            if all(covered[i] or text[i].isspace() for i in range(s, e)):
                continue
            out.append(Finding(s, e, f.entity_type, text[s:e], f.score, recognizer=f.recognizer))
        return out

    def _cross_line(self, text: str, entities: list[str], threshold: float, found: list[Finding]) -> list[Finding]:
        """Nummern über eine Zeilengrenze hinweg (IBAN, Telefon …, z. B. „DE89 3704 0044⏎0532 0130 00“).

        Wo eine Zeile mit einer Ziffer bzw. einem Buchstaben endet und die nächste mit einer Ziffer beginnt,
        werden beide Zeilen zusätzlich mit Leerzeichen statt Umbruch geprüft. Übernommen werden nur Treffer der
        Muster-Erkenner (nicht der Namenserkennung), die über den Umbruch reichen und Zeichen abdecken, die
        noch kein Muster-Fund enthält – so bleiben z. B. Adressblöcke (Straße⏎PLZ Ort) wie bisher zwei Funde.
        Treffer der Namenserkennung zählen dabei nicht („IBAN DE89 …“ hält spaCy mitunter für einen Ort)."""
        ranges = _number_break_ranges(text)
        if not ranges:
            return []
        # alle Zeilenpaare in einen Prüftext; Absatz als Trenner, damit nichts über Paare hinweg passt
        parts, starts, pos = [], [], 0
        for a, b, joints in ranges:
            seg = list(text[a:b])
            for j in joints:
                seg[j - a] = " "
            starts.append(pos)
            parts.append("".join(seg))
            pos += b - a + 2
        probe = "\n\n".join(parts)
        covered = bytearray(len(text))
        for f in found:
            if f.recognizer not in NER_RECOGNIZERS:
                covered[f.start:f.end] = b"\x01" * (f.end - f.start)
        out = []
        for f in self._run(probe, entities, threshold):
            if f.recognizer in NER_RECOGNIZERS:
                continue
            k = bisect.bisect_right(starts, f.start) - 1
            a, b, joints = ranges[k]
            off = a - starts[k]
            s, e = f.start + off, f.end + off
            if e > b or not any(s <= j < e for j in joints):
                continue
            if all(covered[i] or text[i].isspace() for i in range(s, e)):
                continue
            out.append(Finding(s, e, f.entity_type, text[s:e], f.score, recognizer=f.recognizer))
        return out


# ---------------------------------------------------------------------- Helfer
# Leerzeichen-Varianten, wie sie Satzprogramme und Textverarbeitung setzen: geschützt (U+00A0), schmal
# geschützt (U+202F), Ziffernbreite (U+2007) und die festen Breiten U+2000–U+200A. Die Muster für IBAN,
# Telefon usw. kennen nur das normale Leerzeichen („DE89 3704 …“ mit geschützten Leerzeichen blieb unerkannt).
_SPACES = dict.fromkeys([0x00A0, 0x202F, 0x2007, *range(0x2000, 0x200B)], " ")


#: Wörter in Großbuchstaben (ab drei Buchstaben, auch mit Bindestrich: „MÜLLER-LÜDENSCHEID“)
_UPPER_WORD = re.compile(r"(?<![^\W\d_])[A-ZÄÖÜ][A-ZÄÖÜẞß]{2,}(?:-[A-ZÄÖÜ][A-ZÄÖÜẞß]+)*(?![^\W\d_])")


def normal_case(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Wörter in Großbuchstaben in normaler Schreibweise („SCHNEIDER“ → „Schneider“) – Zeichen für Zeichen,
    alle Positionen bleiben gleich. Rückgabe: Text und die Bereiche der geänderten Wörter."""
    chars = None
    spans = []
    for m in _UPPER_WORD.finditer(text):
        if chars is None:
            chars = list(text)
        spans.append((m.start(), m.end()))
        start = True
        for i in range(m.start(), m.end()):
            if chars[i] == "-":
                start = True
                continue
            if not start:
                low = chars[i].lower()
                chars[i] = low if len(low) == 1 else chars[i]
            start = False
    return ("".join(chars) if chars else text), spans


def plain_spaces(text: str) -> str:
    """Sonder-Leerzeichen durch normale ersetzen – Zeichen für Zeichen, alle Positionen bleiben gleich."""
    return text.translate(_SPACES)


# Zeilenende mit Ziffer/Buchstabe, nächste Zeile (im selben Absatz) beginnt mit einer Ziffer, „+“ (Vorwahl)
# oder einem einzelnen Großbuchstaben vor einer Ziffer (Rentenversicherungsnummer „65 170839⏎J 00 3“)
_NUMBER_BREAK = re.compile(r"(?<=[0-9A-Za-z])[ \t]*\n(?=[ \t]*(?:[0-9+]|[A-Z][ \t]?\d))")


def _number_break_ranges(text: str) -> list[tuple[int, int, list[int]]]:
    """Bereiche (Anfang der ersten bis Ende der letzten Zeile) um Zeilenumbrüche, über die eine Nummer
    laufen könnte, samt Positionen dieser Umbrüche. Aufeinanderfolgende Paare werden zusammengefasst."""
    out: list[tuple[int, int, list[int]]] = []
    for m in _NUMBER_BREAK.finditer(text):
        nl = m.end() - 1
        a = text.rfind("\n", 0, nl) + 1
        b = text.find("\n", nl + 1)
        b = len(text) if b == -1 else b
        if out and a < out[-1][1]:
            out[-1] = (out[-1][0], b, out[-1][2] + [nl])
        else:
            out.append((a, b, [nl]))
    return out
def _chunks(text: str, size: int) -> Iterable[tuple[int, int]]:
    """Zerlegt Text an Absatz-/Zeilengrenzen in Blöcke von höchstens ``size`` Zeichen."""
    n = len(text)
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = text.rfind("\n\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind("\n", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        yield start, end
        start = end


NER_RECOGNIZERS = {"SpacyRecognizer", "OnnxNerRecognizer"}
#: Erkenner, die auf Groß-/Kleinschreibung angewiesen sind – nur sie zählen im Durchgang für Großschrift
CASE_RECOGNIZERS = NER_RECOGNIZERS | {"DeAddressRecognizer", "DeTitledPersonRecognizer"}

# Typische Feldbezeichnungen, die spaCy gern als Name/Ort mitnimmt („Berlin\nTelefon“).
_NER_STOPWORDS = {
    "telefon", "tel", "mobil", "handy", "fax", "e-mail", "email", "mail", "adresse", "anschrift",
    "datum", "ort", "name", "vorname", "nachname", "iban", "bic", "straße", "strasse", "plz",
    "betreff", "anlage", "anlagen", "seite", "herr", "frau", "hallo", "liebe", "lieber",
    "sehr", "geehrte", "geehrter", "mit", "freundlichen", "grüßen", "grüße", "gruß", "viele",
    "beste", "herzliche", "steuer-id", "personalnr", "personalnummer", "kundennr", "kundennummer", "postfach",
    # Abkürzungen, die in normaler Schreibweise („Dsgvo“) wie Namen aussehen – siehe PiiAnalyzer._upper_case
    "geb", "geb.", "dr.", "prof.", "tel.", "mailto", "dsgvo", "bdsg", "bgb", "sgb", "stgb", "hgb", "ihk", "hwk",
    "aok", "tüv", "adac", "drk", "dhl", "ups", "sepa", "gmbh",
}
_NER_SPLIT = re.compile(r"[\n\[\]()<>|*_#=/\\@:;]+")


def clean_ner(findings: list[Finding], text: str) -> list[Finding]:
    """Bereinigt NER-Treffer: spaCy fasst manchmal über Zeilenumbrüche oder Markdown-Syntax
    hinweg zusammen („Petra Hoffmann](mailto:…“). Solche Treffer werden zerlegt; übrig bleiben
    nur namensartige Teile (Großbuchstabe am Anfang, keine Feldbezeichnung)."""
    out: list[Finding] = []
    for f in findings:
        if f.recognizer not in NER_RECOGNIZERS:
            out.append(f)
            continue
        pos = f.start
        for part in _NER_SPLIT.split(f.text):
            idx = text.find(part, pos, f.end) if part else -1
            if idx == -1:
                continue
            pos = idx + len(part)
            stripped = part.strip(_TRIM_CHARS)
            if len(stripped) < 2 or not stripped[0].isupper() or stripped.casefold() in _NER_STOPWORDS:
                continue
            out.append(Finding(idx, idx + len(part), f.entity_type, part, f.score, recognizer=f.recognizer))
    return out


_TRIM_CHARS =" \t\n\r\f\v,;:·•\"'„“”»«()[]"


def trim(findings: list[Finding], text: str) -> list[Finding]:
    """Entfernt Leerraum/Satzzeichen an den Rändern eines Fundes."""
    out = []
    for f in findings:
        s, e = f.start, f.end
        while s < e and text[s] in _TRIM_CHARS:
            s += 1
        while e > s and text[e - 1] in _TRIM_CHARS:
            e -= 1
        if e > s:
            f.start, f.end, f.text = s, e, text[s:e]
            out.append(f)
    return out


def merge_overlaps(findings: list[Finding], text: str) -> list[Finding]:
    """Überlappende Funde zu einem zusammenfassen (Vereinigung der Bereiche).

    Für die Schwärzung zählt maximale Abdeckung: Aus „10115“ (PLZ) und „Berlin“ (Ort) wird
    ein Fund „10115 Berlin“. Der Typ stammt bevorzugt von einem musterbasierten Erkenner
    (spezifischer als die NER), sonst vom Fund mit dem höchsten Score."""
    if not findings:
        return []
    items = sorted(findings, key=lambda f: (f.start, -f.end))
    merged: list[list[Finding]] = [[items[0]]]
    cur_end = items[0].end
    for f in items[1:]:
        if f.start < cur_end:
            merged[-1].append(f)
            cur_end = max(cur_end, f.end)
        else:
            merged.append([f])
            cur_end = f.end
    out = []
    for group in merged:
        specific = [f for f in group if f.recognizer not in NER_RECOGNIZERS] or group
        best = max(specific, key=lambda f: (f.score, f.end - f.start))
        s = min(f.start for f in group)
        e = max(f.end for f in group)
        recs = sorted({f.recognizer for f in group if f.recognizer})
        out.append(
            Finding(
                s,
                e,
                best.entity_type,
                text[s:e],
                best.score,
                source=best.source,
                recognizer=", ".join(recs),
            )
        )
    return out


def find_terms(text: str, terms: Iterable[str], entity_type: str = "CUSTOM") -> list[Finding]:
    """Alle Vorkommen der Begriffe (ohne Groß-/Kleinschreibung, ganze Wörter) – auch über eine
    Silbentrennung am Zeilenende hinweg („Bergen-⏎thaler“ für „Bergenthaler“)."""
    joined, hmap = dehyphenate(plain_spaces(text))
    out = []
    for term in terms:
        term = join_hyphenated(plain_spaces(term).strip())
        if not term:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        for m in pattern.finditer(joined):
            s, e = hmap.span(m.start(), m.end()) if hmap else (m.start(), m.end())
            out.append(Finding(s, e, entity_type, text[s:e], 1.0, recognizer="Sperrliste"))
    return out


_SALUTATIONS = {"herr", "herrn", "frau", "dr", "prof", "med"}


def word_pattern(text: str) -> re.Pattern | None:
    """Suchmuster für den Text eines Fundes: Leerraum beliebig, und nicht als Teil eines längeren Wortes bzw.
    einer längeren Zahl („Ende“ nicht in „Wochenende“, „4711“ nicht in „14711“ – wohl aber in „akte_ende.pdf“).
    None bei Texten unter drei Zeichen."""
    # Satzzeichen am Rand gehören nicht zum gesuchten Text („KAYA.“ am Satzende → „kaya“ im Dateinamen)
    # Anrede und Titel gehören nicht zum Namen („Herrn Kaya“ → „kaya“ im Dateinamen)
    words = _name_words(text)
    if not words or len("".join(words)) < 3:
        return None
    core = r"\s*".join(re.escape(w) for w in words)
    first, last = words[0][0], words[-1][-1]
    before = r"(?<![^\W\d_])" if first.isalpha() else r"(?<!\d)" if first.isdigit() else ""
    after = r"(?![^\W\d_])" if last.isalpha() else r"(?!\d)" if last.isdigit() else ""
    return re.compile(before + core + after, re.IGNORECASE)


#: Herkunft der Vorschläge aus der Kontrolle vor dem Speichern (wie „ki“: nicht aktiviert, bleiben bei neuer Analyse)
CHECK_SOURCE = "kontrolle"


def _name_words(text: str) -> list[str]:
    """Wörter eines Fundtextes ohne Satzzeichen am Rand und ohne Anrede/Titel am Anfang."""
    words = join_hyphenated(plain_spaces(text)).strip(_TRIM_CHARS + ".!?").split()
    while len(words) > 1 and words[0].casefold().rstrip(".") in _SALUTATIONS:
        words = words[1:]
    return words


def surname(text: str) -> str | None:
    """Nachname eines mehrteiligen Personennamens: das letzte Wort („Max Mustermann“, „Herr Dr. Max Mustermann“),
    bei „Mustermann, Max“ das Wort vor dem Komma. None bei einem einzelnen Wort, bei weniger als drei Buchstaben
    und bei Wörtern mit Ziffern."""
    words = _name_words(text)
    if len(words) < 2:
        return None
    with_comma = [w for w in words[:-1] if w.endswith(",")]
    name = (with_comma[0] if with_comma else words[-1]).strip(_TRIM_CHARS + ".!?")
    if len(name) < 3 or not all(c.isalpha() or c in "-'’" for c in name) or not name[0].isalpha():
        return None
    return name


#: Wörter, nach denen ein großgeschriebenes Wort ein gewöhnliches Hauptwort ist und kein allein stehender Nachname
_NOUN_MARKERS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines", "kein", "keine",
    "keinen", "im", "am", "vom", "zum", "zur", "beim", "ins", "ans", "aufs", "mein", "meine", "dein", "deine", "sein",
    "seine", "ihr", "ihre", "unser", "unsere", "dieser", "diese", "dieses", "diesen", "diesem", "jeder", "jede",
    "jedes", "jeden", "jedem",
})

#: Zeichen, die ein kleingeschriebenes Wort als Teil eines Dateinamens, einer Adresse oder Kennung ausweisen
_JOINERS = "_-./\\@"


def _surname_hit(text: str, start: int, end: int) -> bool:
    """Zählt die Fundstelle eines allein stehenden Nachnamens? Groß geschrieben immer („Mustermann“,
    „MUSTERMANN“). Klein geschrieben nur als Teil eines Dateinamens o. Ä. („antrag_mustermann.pdf“) – im Fließtext
    wäre es ein gewöhnliches Wort („klein“, „jung“, „kurz“ sind auch Nachnamen)."""
    if text[start].isupper():
        # „im Winter“, „der Fuchs“: nach Artikel oder Verhältniswort steht ein gewöhnliches Hauptwort
        previous = re.search(r"([^\W\d_]+)\s+$", text[max(0, start - 20):start])
        return not (previous and previous.group(1).casefold() in _NOUN_MARKERS)
    before = text[start - 1] if start else " "
    after = text[end] if end < len(text) else " "
    return before in _JOINERS or before.isdigit() or (after in _JOINERS and end + 1 < len(text)
                                                       and text[end + 1].isalnum()) or after.isdigit()


def further_occurrences(text: str, findings: list[Finding]) -> list[Finding]:
    """Stellen, an denen der Text eines aktiven Fundes noch einmal steht, ohne selbst als Fund markiert zu sein –
    etwa ein Name im Dateinamen („antrag_kaya.pdf“) oder in anderer Groß-/Kleinschreibung. Sie blieben im Ergebnis
    lesbar. Bei mehrteiligen Personennamen zählt auch der Nachname allein („Max Mustermann“ geschwärzt, später
    „Herr Mustermann“ nicht erkannt). Rückgabe: nicht aktivierte Vorschläge (Herkunft „kontrolle“) mit der
    Datenart des Fundes.

    Stellen, die ein vorhandener Fund berührt, zählen nicht: aktive werden geschwärzt, abgewählte sind eine
    bewusste Entscheidung (auch ein früher abgelehnter Vorschlag). Nicht gesucht wird Text, der nur beim
    Schwärzen eines ganzen Bereichs mit erfasst wurde (``Finding.area_text``)."""
    spots = _FreeSpots(text, findings)
    active = [f for f in sorted(findings, key=lambda f: f.start) if f.active and f.area is None and not f.area_text]
    seen: set[str] = set()
    out: list[Finding] = []
    for f, pattern in _whole_texts(active, seen):       # 1. der ganze Wortlaut
        out += spots.find(pattern, f.entity_type, "Kontrolle: weiteres Vorkommen eines geschwärzten Textes")
    for f, pattern in _surnames(active, seen):          # 2. danach der Nachname allein (der ganze Name hat Vorrang)
        out += spots.find(pattern, f.entity_type, "Kontrolle: Nachname eines geschwärzten Namens steht allein",
                          _surname_hit)
    return sorted(out, key=lambda f: f.start)


class _FreeSpots:
    """Sucht Muster an Stellen des Dokumenttexts, die noch kein Fund berührt. Jede gefundene Stelle gilt danach
    als vergeben – ein späteres Muster (etwa der Nachname allein) trifft sie nicht noch einmal."""

    def __init__(self, text: str, findings: list[Finding]) -> None:
        self.text = text
        self.marked = bytearray(len(text))
        for f in findings:
            if f.area is None:
                self.marked[f.start:f.end] = b"\x01" * (f.end - f.start)
        self.joined, self.hmap = dehyphenate(plain_spaces(text))

    def find(self, pattern: re.Pattern, entity_type: str, why: str,
             accept: Callable[[str, int, int], bool] | None = None) -> list[Finding]:
        """Vorschläge für alle freien Fundstellen des Musters; ``accept(text, start, end)`` kann Stellen
        ausschließen."""
        out = []
        for m in pattern.finditer(self.joined):
            if accept and not accept(self.joined, m.start(), m.end()):
                continue
            s, e = self.hmap.span(m.start(), m.end()) if self.hmap else (m.start(), m.end())
            if any(self.marked[s:e]):
                continue
            self.marked[s:e] = b"\x01" * (e - s)
            out.append(Finding(s, e, entity_type, self.text[s:e], 1.0, source=CHECK_SOURCE, active=False,
                               recognizer=why))
        return out


def _whole_texts(active: list[Finding], seen: set[str]) -> Iterator[tuple[Finding, re.Pattern]]:
    """Je unterschiedlichem Fundtext einmal: Fund und Suchmuster für den ganzen Wortlaut."""
    for f in active:
        key = re.sub(r"\s+", "", join_hyphenated(plain_spaces(f.text))).casefold()
        if key in seen:
            continue
        seen.add(key)
        pattern = word_pattern(f.text)
        if pattern is not None:
            yield f, pattern


def _surnames(active: list[Finding], seen: set[str]) -> Iterator[tuple[Finding, re.Pattern]]:
    """Je Nachname eines mehrteiligen Personennamens einmal: Fund und Suchmuster – sofern der Nachname nicht
    schon als ganzer Fundtext gesucht wurde."""
    for f in active:
        name = surname(f.text) if f.entity_type == "PERSON" else None
        if name is None or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        pattern = word_pattern(name)
        if pattern is not None:
            yield f, pattern


def apply_allow_list(findings: list[Finding], allow_list: Iterable[str]) -> list[Finding]:
    allowed = {join_hyphenated(plain_spaces(a).strip()).casefold() for a in allow_list if a.strip()}
    if not allowed:
        return findings
    return [f for f in findings if join_hyphenated(plain_spaces(f.text).strip()).casefold() not in allowed]
