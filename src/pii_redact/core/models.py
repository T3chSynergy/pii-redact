"""Datenmodelle: Fund (Finding) und Einstellungen."""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .entities import DEFAULT_ENTITIES

_ids = itertools.count(1)


@dataclass
class Finding:
    """Ein erkannter (oder manuell markierter) Textabschnitt mit personenbezogenen Daten.

    ``start``/``end`` sind Zeichen-Offsets (Python-Indizes) im extrahierten Dokumenttext.

    Sonderfall **Bereich** (``area`` gesetzt): frei gezogenes Rechteck auf einer PDF-Seite
    (Unterschrift, Foto, Handschrift …), unabhängig vom Text. ``area`` = (Seite, x0, y0, x1, y1) in
    PDF-Koordinaten; ``start == end`` ist dann nur ein Ankerpunkt (Seitenanfang) für die Sortierung.
    """

    start: int
    end: int
    entity_type: str
    text: str
    score: float = 1.0
    source: str = "auto"      # "auto" | "manuell"
    active: bool = True
    recognizer: str = ""
    original_type: str = ""   # ursprünglicher Typ, falls in der Nachbearbeitung geändert
    area: tuple[int, float, float, float, float] | None = None
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def is_area(self) -> bool:
        return self.area is not None

    @property
    def key(self) -> tuple[int, int]:
        return (self.start, self.end)

    def overlaps(self, other: "Finding") -> bool:
        if self.area is not None or other.area is not None:
            return False
        return self.start < other.end and other.start < self.end


class ReplaceMode:
    PLACEHOLDER = "platzhalter"   # [PERSON]
    NUMBERED = "nummeriert"       # [PERSON_1] – gleiche Werte bekommen gleiche Nummer
    BLACKOUT = "schwaerzen"       # ██████ (gleiche Länge)

    LABELS = {
        PLACEHOLDER: "Platzhalter  [PERSON]",
        NUMBERED: "Nummeriert  [PERSON_1]",
        BLACKOUT: "Schwärzen  ████",
    }


class AnalysisMode:
    FAST = "schnell"          # spaCy + Muster
    THOROUGH = "gruendlich"   # zusätzlich Transformer-Modell (ONNX)

    LABELS = {FAST: "Schnell", THOROUGH: "Gründlich"}


@dataclass
class Settings:
    threshold: float = 0.45
    replace_mode: str = ReplaceMode.PLACEHOLDER
    entities: list[str] = field(default_factory=lambda: list(DEFAULT_ENTITIES))
    allow_list: list[str] = field(default_factory=list)   # nie schwärzen
    deny_list: list[str] = field(default_factory=list)    # immer schwärzen
    spacy_model: str = "de_core_news_md"
    analysis_mode: str = AnalysisMode.THOROUGH   # fehlt das Transformer-Modell, wird „Schnell“ verwendet
    ner_model: str = ""           # Ordnername unter models/ner (leer = erstes gefundenes)
    pdf_labels: bool = True       # Platzhalter im PDF-Schwärzungsbalken anzeigen
    ocr: bool = True              # gescannte PDF-Seiten per Texterkennung lesen (Rückfallebene)
    sync_scroll: bool = True
    zoom_mode: str = "breite"     # PDF-Ansicht: "breite" (Seitenbreite), "seite" (ganze Seite), "fest" (zoom_percent)
    zoom_percent: int = 100       # bei "fest": 100 % = echte Papiergröße
    show_original: bool = False   # Original neben der bearbeiteten Fassung anzeigen
    compact_notices: bool = False  # Hinweise nur als Zähler in der Statusleiste und an den Seiten (Vielnutzer)
    recent_batches: list[str] = field(default_factory=list)   # Zielordner zuletzt bearbeiteter Ordner
    # Zentrale Listen der Organisation (aus defaults.json, werden nicht im Nutzerprofil gespeichert)
    org_allow_list: list[str] = field(default_factory=list)
    org_deny_list: list[str] = field(default_factory=list)
    #: Von der IT festgelegte Einstellungen (defaults.json, Schlüssel "locked"): Wert aus defaults.json gilt,
    #: persönliche Änderung ist gesperrt.
    locked: list[str] = field(default_factory=list)

    _NOT_SAVED = ("org_allow_list", "org_deny_list", "locked")

    @property
    def all_allow(self) -> list[str]:
        return self.org_allow_list + self.allow_list

    @property
    def all_deny(self) -> list[str]:
        return self.org_deny_list + self.deny_list

    @classmethod
    def load(cls, path: Path, defaults_path: Path | None = None) -> "Settings":
        """Zentrale Vorgaben (defaults.json) als Grundlage, persönliche Einstellungen darüber.
        Ausnahme-/Sperrlisten der Organisation gelten immer zusätzlich; unter ``locked`` aufgeführte
        Einstellungen kommen immer aus defaults.json."""
        base = _read_json(defaults_path) if defaults_path else {}
        user = _read_json(path)
        locked = [k for k in base.get("locked", []) if isinstance(k, str) and k in base]
        data = {k: v for k, v in base.items() if k not in ("allow_list", "deny_list", "locked")}
        data.update({k: v for k, v in user.items() if k not in locked and k not in cls._NOT_SAVED})
        data["locked"] = locked
        data["org_allow_list"] = list(base.get("allow_list", []))
        data["org_deny_list"] = list(base.get("deny_list", []))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k not in self._NOT_SAVED}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
