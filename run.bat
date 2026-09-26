@echo off
rem Startet die Oberflaeche. Optional: Datei per Drag&Drop auf run.bat ziehen.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Bitte zuerst setup.bat ausfuehren.
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m pii_redact %*
