"""Presidio-Erkenner für das ONNX-Transformer-Modell (Modus „Gründlich“)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from presidio_analyzer import EntityRecognizer, RecognizerResult

from .transformer_ner import OnnxNerModel

if TYPE_CHECKING:  # nur für die Typprüfung
    from presidio_analyzer.nlp_engine import NlpArtifacts

log = logging.getLogger(__name__)

RECOGNIZER_NAME = "OnnxNerRecognizer"


class OnnxNerRecognizer(EntityRecognizer):
    """Presidio-Erkenner auf Basis von :class:`OnnxNerModel`."""

    def __init__(self, model_dir: str | Path, supported_language: str = "de"):
        self._model_dir = Path(model_dir)
        self.model: OnnxNerModel | None = None
        super().__init__(
            supported_entities=["PERSON", "LOCATION", "ORGANIZATION"],
            supported_language=supported_language,
            name=RECOGNIZER_NAME,
        )

    def load(self) -> None:
        self.model = OnnxNerModel(self._model_dir)
        log.info("ONNX-NER-Modell geladen: %s", self._model_dir.name)

    def analyze(self, text: str, entities: list[str],
                nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        wanted = set(entities or self.supported_entities)
        results = []
        assert self.model is not None, "load() läuft im Konstruktor der Basisklasse"
        for span in self.model.predict(text):
            if span.entity not in wanted:
                continue
            results.append(
                RecognizerResult(
                    entity_type=span.entity,
                    start=span.start,
                    end=span.end,
                    score=round(span.score * 0.95, 3),
                    # Presidio: None ist der Vorgabewert, im Typ aber nicht genannt; der Typ von
                    # ``name`` ergibt sich erst im Konstruktor der Basisklasse
                    analysis_explanation=None,  # type: ignore[arg-type]
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,  # type: ignore[has-type]
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )
        return results
