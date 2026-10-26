"""Prüft unter Windows, ob pii-redact Telemetrie-Ereignisse von onnxruntime an ETW meldet.

onnxruntime (Modus „Gründlich“, Texterkennung) meldet Nutzungsereignisse an die Ereignisablaufverfolgung von
Windows (ETW), u. a. den Dateinamen jedes geladenen Modells. pii-redact schaltet das ab (``onnx_telemetry.py``).
Dieses Skript prüft es nach:

1. ETW-Aufzeichnung für den Telemetrie-Anbieter ``Microsoft.ML.ONNXRuntime`` starten (braucht Administratorrechte).
2. **Gegenprobe:** ein kleines Python-Programm lädt ein Modell der Texterkennung *ohne* Abschaltung – seine
   Ereignisse müssen in der Aufzeichnung auftauchen, sonst zeichnet die Aufzeichnung nichts auf.
3. Die Kommandozeile von pii-redact liest ``samples/scan.pdf`` (Texterkennung) und ``samples/beispiel.txt`` im
   Modus „Gründlich“ (nur Anzeige, es entstehen keine Dateien).
4. Auswertung nur der Ereignisse dieses Prozesses: erlaubt ist ``ProcessInfo`` (beim Laden der Bibliothek, vom
   Programm aus nicht abschaltbar), alles andere – vor allem ``SessionCreation`` mit Modellname – ist ein Fehler.

Aufruf (Eingabeaufforderung als Administrator):
    tools\\etw_pruefen.bat                                          Entwicklungsumgebung (.venv)
    tools\\etw_pruefen.bat --exe dist\\pii-redact\\pii-redact-cli.exe  gebautes Programm
Rückgabewert: 0 = keine Modell-Ereignisse, 1 = Ereignisse gefunden, 2 = Prüfung nicht möglich.
Die Aufzeichnung bleibt auf dem Rechner; an Microsoft geht dadurch nichts.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = "{3a26b1ff-7484-7484-7484-15261f42614d}"   # Microsoft.ML.ONNXRuntime (Telemetrie)
ALLOWED = {"ProcessInfo"}                             # beim Laden der Bibliothek, nicht abschaltbar
SAMPLES = (ROOT / "samples" / "scan.pdf", ROOT / "samples" / "beispiel.txt")
TIMEOUT = 600

# Gegenprobe: onnxruntime ohne pii-redact laden – die Telemetrie ist dann an
_CONTROL = ("import os, sys; print(os.getpid(), flush=True); import onnxruntime as o; "
            "o.InferenceSession(sys.argv[1], providers=['CPUExecutionProvider'])")
# pii-redact-Kommandozeile aus der Entwicklungsumgebung; gibt zuerst die eigene Prozess-ID aus, denn
# .venv\Scripts\python.exe ist nur ein Starter, der den eigentlichen Python-Prozess als Kindprozess anlegt
_CLI = ("import os, sys; print(os.getpid(), flush=True); from pii_redact.cli import main; "
        "sys.exit(main(sys.argv[1:]))")


class NotPossible(RuntimeError):
    """Die Prüfung lässt sich auf diesem Rechner nicht durchführen (z. B. ohne Administratorrechte)."""


@dataclass
class Event:
    pid: int
    task: str
    model: str = ""

    def __str__(self) -> str:
        return f"{self.task or '(ohne Namen)'}{' – ' + self.model if self.model else ''}"


def parse_events(xml_file: Path) -> list[Event]:
    """Ereignisse aus der XML-Ausgabe von ``tracerpt -of XML``."""
    events = []
    for ev in ET.parse(xml_file).getroot().iterfind(".//{*}Event"):
        execution = ev.find("{*}System/{*}Execution")
        if execution is None or not execution.get("ProcessID"):
            continue
        model = next((d.text or "" for d in ev.iterfind("{*}EventData/{*}Data") if d.get("Name") == "modelFileName"),
                     "")
        events.append(Event(int(execution.get("ProcessID")), ev.findtext("{*}RenderingInfo/{*}Task") or "", model))
    return events


def violations(events: list[Event], pid: int) -> list[Event]:
    """Ereignisse des Prozesses ``pid``, die nicht hätten entstehen dürfen."""
    return [e for e in events if e.pid == pid and e.task not in ALLOWED]


def _logman(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["logman", *args], capture_output=True, text=True, errors="replace")


def _control_model() -> Path:
    import importlib.util

    spec = importlib.util.find_spec("rapidocr")
    if spec is None or not spec.submodule_search_locations:
        raise NotPossible("Gegenprobe nicht möglich: Paket rapidocr fehlt in dieser Python-Umgebung.")
    models = sorted(Path(next(iter(spec.submodule_search_locations))).rglob("*.onnx"), key=lambda p: p.stat().st_size)
    if not models:
        raise NotPossible("Gegenprobe nicht möglich: kein ONNX-Modell im Paket rapidocr gefunden.")
    return models[0]


def _run(cmd: list[str], env: dict, reports_pid: bool) -> tuple[int, subprocess.CompletedProcess]:
    # die Kommandozeile von pii-redact schreibt UTF-8 (cli.py)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                            env=dict(env, PYTHONIOENCODING="utf-8"), cwd=ROOT)
    out, err = proc.communicate(timeout=TIMEOUT)
    done = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    pid = proc.pid
    if reports_pid:
        first = out.splitlines()[0].strip() if out else ""
        if not first.isdigit():
            raise NotPossible(f"Prozess-ID nicht ermittelt (Ausgabe: {first!r}, Fehler: {err.strip()[-300:]})")
        pid = int(first)
    return pid, done


def check(exe: Path | None = None, workdir: Path | None = None, control: bool = True) -> dict:
    """Führt die Prüfung aus. Ergebnis: ``pid``, ``events`` (alle des Prozesses), ``violations``,
    ``control_events`` (Gegenprobe). Wirft ``NotPossible``, wenn die Prüfung nicht durchführbar ist."""
    if sys.platform != "win32":
        raise NotPossible("ETW gibt es nur unter Windows.")
    for sample in SAMPLES:
        if not sample.is_file():
            raise NotPossible(f"Beispieldatei fehlt: {sample}")
    work = Path(workdir or tempfile.mkdtemp(prefix="pii-etw-"))
    work.mkdir(parents=True, exist_ok=True)
    session = f"pii-redact-etw-{uuid.uuid4().hex[:8]}"
    etl, xml = work / "ort.etl", work / "ort.xml"

    started = _logman("start", session, "-p", PROVIDER, "0xffffffffffffffff", "0xff", "-o", str(etl), "-ets")
    if started.returncode != 0:
        raise NotPossible("ETW-Aufzeichnung ließ sich nicht starten – Eingabeaufforderung als Administrator "
                          f"öffnen. ({(started.stdout + started.stderr).strip()[-300:]})")
    try:
        env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
        control_pid = None
        if control:
            control_pid, done = _run([sys.executable, "-c", _CONTROL, str(_control_model())], env, reports_pid=True)
            if done.returncode != 0:
                raise NotPossible(f"Gegenprobe fehlgeschlagen: {done.stderr.strip()[-300:]}")
        # eigene Einstellungen und IT-Vorgaben außen vor lassen, damit Texterkennung und „Gründlich“ sicher laufen
        app_env = dict(env, APPDATA=str(work / "appdata"), PROGRAMDATA=str(work / "programdata"))
        args = [*map(str, SAMPLES), "--gruendlich", "--nur-anzeigen"]
        if exe:
            pid, result = _run([str(exe), *args], app_env, reports_pid=False)
        else:
            pid, result = _run([sys.executable, "-c", _CLI, *args], app_env, reports_pid=True)
    finally:
        _logman("stop", session, "-ets")

    if result.returncode == 3:
        raise NotPossible("pii-redact hat die Texterkennung nicht ausgeführt (RapidOCR fehlt?) – Prüfung unvollständig.")
    if result.returncode not in (0, 2):   # 2 = Reste im Ergebnis – bei „nur anzeigen“ ohnehin nicht relevant
        raise NotPossible(f"pii-redact ist mit Fehler {result.returncode} beendet: {result.stderr.strip()[-500:]}")
    conv = subprocess.run(["tracerpt", str(etl), "-o", str(xml), "-of", "XML", "-y"], capture_output=True,
                          text=True, errors="replace", cwd=work)
    if conv.returncode != 0 or not xml.is_file():
        raise NotPossible(f"tracerpt konnte die Aufzeichnung nicht umwandeln: {conv.stdout.strip()[-300:]}")
    events = parse_events(xml)
    own = [e for e in events if e.pid == pid]
    control_events = [e for e in events if e.pid == control_pid] if control_pid else []
    if control and not any(e.task.startswith("SessionCreation") for e in control_events):
        raise NotPossible("Gegenprobe ohne Ereignisse – die Aufzeichnung erfasst die Telemetrie von onnxruntime "
                          "auf diesem Rechner nicht, das Ergebnis wäre nicht aussagekräftig.")
    found = violations(events, pid)
    if workdir is None and not found:
        shutil.rmtree(work, ignore_errors=True)   # nichts zu sehen – Aufzeichnung nicht aufheben
    return {"pid": pid, "events": own, "violations": found, "control_events": control_events, "etl": etl}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prüft, ob pii-redact Telemetrie-Ereignisse von onnxruntime meldet "
                                            "(Windows, als Administrator).")
    p.add_argument("--exe", type=Path, help="gebautes Programm prüfen, z. B. dist\\pii-redact\\pii-redact-cli.exe")
    p.add_argument("--ohne-gegenprobe", action="store_true",
                   help="ohne Gegenprobe (dann ist „keine Ereignisse“ weniger aussagekräftig)")
    args = p.parse_args(argv)
    target = args.exe or "Kommandozeile aus der Entwicklungsumgebung"
    print(f"ETW-Prüfung der onnxruntime-Telemetrie: {target}")
    try:
        r = check(args.exe, control=not args.ohne_gegenprobe)
    except NotPossible as exc:
        print(f"Prüfung nicht möglich: {exc}")
        return 2
    if r["control_events"]:
        print(f"Gegenprobe ohne Abschaltung: {len(r['control_events'])} Ereignisse – die Aufzeichnung funktioniert.")
    print(f"Ereignisse von pii-redact (Prozess {r['pid']}): {len(r['events'])}")
    for e in r["events"]:
        print(f"  {e}{'' if e.task in ALLOWED else '   <-- nicht erlaubt'}")
    if r["violations"]:
        print("Ergebnis: FEHLER – onnxruntime meldet Modell-Ereignisse. Die Telemetrie wird zu spät oder gar nicht "
              "abgeschaltet (z. B. lädt ein Paket schon beim Start ein eigenes Modell).")
        print(f"Aufzeichnung zum Nachsehen: {r['etl']}")
        return 1
    print("Ergebnis: OK – keine Modell-Ereignisse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
