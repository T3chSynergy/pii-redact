"""pii-redact – personenbezogene Daten lokal erkennen und entfernen."""

import os as _os

__version__ = "0.8.3"

# onnxruntime (Modus „Gründlich“, Texterkennung) bringt Telemetrie von Microsoft mit. Unter Linux/macOS startet
# beim Laden der Bibliothek ein eigener Sende-Client – abschalten lässt er sich nur über diese Variable, gesetzt vor
# dem ersten „import onnxruntime“. Windows: siehe ``onnx_telemetry.py``.
_os.environ["ORT_DISABLE_TELEMETRY"] = "1"
