"""Speicherorte der Einstellungen (plattformabhängig, ohne Qt-Abhängigkeit).

* persönlich:  %APPDATA%\\pii-redact\\settings.json
* zentral:     %ProgramData%\\pii-redact\\defaults.json  (Vorgaben der IT für neue Nutzer) – gilt nur, wenn die
  Datei geschützt ist (siehe ``machine_trust``)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .core.models import Settings, json_object
from .machine_trust import HINT, read_protected
from .paths import machine_config_dir, machine_models_problem


@dataclass(frozen=True)
class TrustWarning:
    """Datei der IT, die nicht gilt, weil sie nicht geschützt ist. ``short`` für Hinweisleiste und Startseite,
    ``detail`` mit Pfad und Grund für Tooltip, Programmprotokoll und Kommandozeile."""
    short: str
    detail: str


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pii-redact" / "settings.json"


def defaults_path() -> Path:
    return machine_config_dir() / "defaults.json"


def load_settings(path: Path | None = None) -> tuple[Settings, list[TrustWarning]]:
    """Persönliche Einstellungen (``path``, sonst ``settings_path()``) über den Vorgaben der IT.

    Vorgaben und Modellordner der IT gelten nur, wenn sie geschützt sind; sonst gelten die eigenen Einstellungen
    bzw. die Voreinstellungen des Programms, und die zweite Rückgabe nennt, was übergangen wurde."""
    warnings: list[TrustWarning] = []
    dpath = defaults_path()
    raw, problem = read_protected(dpath)
    if problem:
        warnings.append(TrustWarning(
            "Vorgaben der IT nicht übernommen",
            f"Die zentralen Vorgaben {dpath} gelten nicht, weil die Datei nicht geschützt ist: {problem}. Es gelten "
            f"die eigenen Einstellungen bzw. die Voreinstellungen des Programms. Bitte der IT melden. {HINT}"))
    settings = Settings.load(path or settings_path(), dpath, defaults=json_object(raw) if raw else {})
    models = machine_models_problem()
    if models:
        warnings.append(TrustWarning(
            "Modelle der IT nicht genutzt",
            f"Modelle aus {machine_config_dir() / 'models'} werden nicht geladen, weil der Ordner nicht geschützt "
            f"ist – {models}. Bitte der IT melden. {HINT}"))
    return settings, warnings
