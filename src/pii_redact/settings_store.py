"""Speicherorte der Einstellungen (plattformabhängig, ohne Qt-Abhängigkeit).

* persönlich:  %APPDATA%\\pii-redact\\settings.json
* zentral:     %ProgramData%\\pii-redact\\defaults.json  (Vorgaben der IT für neue Nutzer)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import machine_config_dir


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
