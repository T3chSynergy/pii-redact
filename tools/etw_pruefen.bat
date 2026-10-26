@echo off
rem Prueft, ob pii-redact Telemetrie-Ereignisse von onnxruntime an ETW meldet (Eingabeaufforderung als Administrator).
rem   tools\etw_pruefen.bat                                          Entwicklungsumgebung (.venv)
rem   tools\etw_pruefen.bat --exe dist\pii-redact\pii-redact-cli.exe  gebautes Programm
rem Rueckgabe: 0 = keine Modell-Ereignisse, 1 = Ereignisse gefunden, 2 = Pruefung nicht moeglich.
setlocal
cd /d "%~dp0\.."
rem Python aus der Entwicklungsumgebung, sonst aus der Build-Umgebung (build.bat)
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=.venv-build\Scripts\python.exe"
if not exist "%PY%" (
    echo Bitte zuerst setup.bat oder build.bat ausfuehren.
    exit /b 2
)
"%PY%" tools\etw_pruefen.py %*
