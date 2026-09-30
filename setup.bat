@echo off
rem Einmalige Einrichtung unter Windows: virtuelle Umgebung, Pakete, deutsches Sprachmodell.
setlocal
cd /d "%~dp0"

rem Bevorzugt Python 3.14 (sonst 3.13/3.12) ueber den py-Launcher, sonst "python" aus dem PATH
set "PY="
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V --version >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY set "PY=python"
echo Verwende: %PY%
%PY% --version || goto :error

rem Python-Version einer vorhandenen Umgebung pruefen (z. B. nach Umstieg auf eine neuere Version)
set "PYV="
set "VENVPYV="
for /f "delims=" %%a in ('%PY% -c "import sys;print(sys.version_info[0],sys.version_info[1],sep=chr(46))"') do set "PYV=%%a"
if exist .venv\Scripts\python.exe for /f "delims=" %%a in ('.venv\Scripts\python -c "import sys;print(sys.version_info[0],sys.version_info[1],sep=chr(46))"') do set "VENVPYV=%%a"
if defined VENVPYV if not "%VENVPYV%"=="%PYV%" (
    echo Vorhandene .venv nutzt Python %VENVPYV% - wird mit %PYV% neu angelegt ...
    rmdir /s /q .venv || goto :error
)

echo [1/4] Erzeuge virtuelle Umgebung .venv ...
%PY% -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error

echo [2/4] Aktualisiere pip ...
python -m pip install --upgrade pip >nul || goto :error

echo [3/4] Installiere pii-redact und Abhaengigkeiten ...
pip install -e ".[dev]" || goto :error

echo [4/4] Lade deutsches spaCy-Modell (ca. 45 MB) ...
python -m spacy download de_core_news_md || goto :error

echo.
echo Fertig. Starten mit run.bat
pause
exit /b 0

:error
echo.
echo *** Einrichtung fehlgeschlagen. Details siehe oben. ***
pause
exit /b 1
