"""Erkennung personenbezogener Daten mit Presidio + spaCy (Deutsch)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

from .models import AnalysisMode, Finding, Settings
from .recognizers_de import LANG, build_recognizers

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
    def __init__(self):
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
        self._engine = None
        self.ner_model_dir = None

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
        entities = [e for e in settings.entities if e != "CUSTOM"]
        raw: list[Finding] = []
        size = CHUNK_SIZE if self.mode == AnalysisMode.FAST else CHUNK_SIZE_THOROUGH
        chunks = list(_chunks(text, size))
        for i, (c_start, c_end) in enumerate(chunks):
            if progress:
                progress(i, len(chunks))
            chunk = text[c_start:c_end]
            if not chunk.strip() or not entities:
                continue
            results = self._engine.analyze(
                text=chunk,
                language=LANG,
                entities=entities,
                score_threshold=settings.threshold,
            )
            for r in results:
                s, e = r.start + c_start, r.end + c_start
                rec = (r.recognition_metadata or {}).get("recognizer_name", "")
                raw.append(Finding(s, e, r.entity_type, text[s:e], round(r.score, 2), recognizer=rec))
        if progress:
            progress(len(chunks), len(chunks))

        raw = clean_ner(raw, text)
        raw.extend(find_terms(text, settings.all_deny))
        findings = merge_overlaps(trim(raw, text), text)
        return apply_allow_list(findings, settings.all_allow)


# ---------------------------------------------------------------------- Helfer
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

# Typische Feldbezeichnungen, die spaCy gern als Name/Ort mitnimmt („Berlin\nTelefon“).
_NER_STOPWORDS = {
    "telefon", "tel", "mobil", "handy", "fax", "e-mail", "email", "mail", "adresse", "anschrift",
    "datum", "ort", "name", "vorname", "nachname", "iban", "bic", "straße", "strasse", "plz",
    "betreff", "anlage", "anlagen", "seite", "herr", "frau", "hallo", "liebe", "lieber",
    "sehr", "geehrte", "geehrter", "mit", "freundlichen", "grüßen", "grüße", "gruß", "viele",
    "beste", "herzliche", "steuer-id",
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
    """Alle Vorkommen der Begriffe (ohne Groß-/Kleinschreibung, ganze Wörter)."""
    out = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        for m in pattern.finditer(text):
            out.append(Finding(m.start(), m.end(), entity_type, m.group(0), 1.0, recognizer="Sperrliste"))
    return out


def apply_allow_list(findings: list[Finding], allow_list: Iterable[str]) -> list[Finding]:
    allowed = {a.strip().casefold() for a in allow_list if a.strip()}
    if not allowed:
        return findings
    return [f for f in findings if f.text.strip().casefold() not in allowed]
