"""Telemetrie von onnxruntime abschalten.

onnxruntime meldet Nutzungsdaten an Microsoft:

* **Windows:** Ereignisse über die Ereignisablaufverfolgung von Windows (ETW), u. a. Dateiname und Kennwerte des
  Modells, Hardware und Fehlermeldungen. Ob Windows sie weitergibt, hängt von den Diagnosedaten-Einstellungen
  des PCs ab. ``disable_telemetry_events()`` schaltet sie ab – nur nicht das eine Ereignis beim Laden der
  Bibliothek (Version, Prozessor, Arbeitsspeicher).
* **Linux/macOS:** eigener Sende-Client (Geräte-ID im Cache-Ordner, Versand an Microsoft). Ihn schaltet nur
  ``ORT_DISABLE_TELEMETRY`` ab, gesetzt vor dem ersten Import – das erledigt ``pii_redact/__init__.py``.

``disable()`` wird vor jedem Modell aufgerufen (Modus „Gründlich“, Texterkennung).
"""

import os


def disable() -> None:
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime as ort

    off = getattr(ort, "disable_telemetry_events", None)
    if off is not None:
        off()
