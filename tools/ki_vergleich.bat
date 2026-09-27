@echo off
rem Vergleicht Sprachmodelle fuer die KI-Nachpruefung mit festen, erfundenen Testfaellen.
rem Beispiel:  tools\ki_vergleich.bat --modell anbieter/modell-a --modell anbieter/modell-b
rem Server-Adresse und Schluessel wie in den Einstellungen (oder --url / Umgebungsvariable PII_REDACT_LLM_KEY).
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\python.exe (
    echo Bitte zuerst setup.bat ausfuehren.
    exit /b 1
)
.venv\Scripts\python tools\ki_vergleich.py %*
