"""Speicherorte – funktioniert gleichermaßen im Quellcode-Betrieb und als PyInstaller-EXE.

Modelle werden in dieser Reihenfolge gesucht:
1. im Programmpaket  (<Programmordner>\\_internal\\models  bzw. im Projekt: ./models)
2. neben der EXE     (<Programmordner>\\models  – z. B. durch ein separates Modell-Paket)
3. zentral           (%ProgramData%\\pii-redact\\models)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "pii-redact"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Ordner der EXE bzw. Projektwurzel im Quellcode-Betrieb."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    """Ordner mit den mitgelieferten Daten (bei PyInstaller: _internal)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def machine_config_dir() -> Path:
    """Zentrale, von der IT verwaltete Konfiguration (für Nutzer meist nur lesbar)."""
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / APP_NAME
    return Path("/etc") / APP_NAME


def model_dirs() -> list[Path]:
    out: list[Path] = []
    # Optional: zusätzliche Modellordner per Umgebungsvariable (Tests, Sonderinstallationen)
    for extra in filter(None, os.environ.get("PII_REDACT_MODEL_DIR", "").split(os.pathsep)):
        if Path(extra).is_dir():
            out.append(Path(extra))
    for base in (resource_dir(), app_dir(), machine_config_dir()):
        d = base / "models"
        if d.is_dir() and d not in out:
            out.append(d)
    return out


# ---------------------------------------------------------------------- spaCy
def find_spacy_model(name: str) -> str | None:
    """Name eines installierten spaCy-Pakets oder Pfad zu einem mitgelieferten Modellordner."""
    for d in model_dirs():
        base = d / "spacy" / name
        if (base / "config.cfg").is_file():
            return str(base)
        # Struktur eines pip-Pakets: <name>/<name>-<version>/config.cfg
        for cfg in sorted(base.glob(f"{name}-*/config.cfg")):
            return str(cfg.parent)
    try:
        import spacy

        if spacy.util.is_package(name):
            return name
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------- Transformer (ONNX)
def list_ner_models() -> list[Path]:
    """Alle mitgelieferten ONNX-NER-Modelle (Ordner mit model.onnx + tokenizer.json)."""
    found: list[Path] = []
    for d in model_dirs():
        ner = d / "ner"
        if not ner.is_dir():
            continue
        for sub in sorted(ner.iterdir()):
            if (sub / "model.onnx").is_file() and (sub / "tokenizer.json").is_file() and (sub / "config.json").is_file():
                found.append(sub)
    return found


def find_ner_model(name: str = "") -> Path | None:
    models = list_ner_models()
    if name:
        for m in models:
            if m.name == name:
                return m
    return models[0] if models else None


def ner_model_info(model_dir: Path) -> dict:
    try:
        return json.loads((model_dir / "pii_redact_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
