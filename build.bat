@echo off
rem ==========================================================================
rem  pii-redact - Build: EXE (PyInstaller) und MSI (WiX v5)
rem
rem  build.bat                  normaler Build (Umgebung wird wiederverwendet)
rem  build.bat /neu             Build-Umgebung .venv-build komplett neu anlegen
rem  build.bat /ohne-msi        nur den Programmordner dist\pii-redact erzeugen
rem  build.bat /ohne-gruendlich ohne Transformer-Modell (nur Modus "Schnell")
rem  build.bat /nur-msi         nur das MSI aus einem vorhandenen dist\pii-redact bauen
rem
rem  Optional vorher:  set SIGNTOOL_ARGS=...   (signiert EXE und MSI mit signtool,
rem                    Beispiele: packaging\README.md, Abschnitt "Code-Signierung")
rem
rem  Ergebnis: dist\pii-redact\           Programmordner (pii-redact.exe, pii-redact-cli.exe)
rem            dist\pii-redact-<ver>.msi  Installationspaket fuer SCCM
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "NEU="
set "OHNE_MSI="
set "NUR_MSI="
set "MSI_STATUS=nicht gebaut"
set "PII_REDACT_BUNDLE_NER=davlan-xlmr-ner"
for %%A in (%*) do (
    if /I "%%~A"=="/neu" set "NEU=1"
    if /I "%%~A"=="/ohne-msi" set "OHNE_MSI=1"
    if /I "%%~A"=="/ohne-gruendlich" set "PII_REDACT_BUNDLE_NER="
    if /I "%%~A"=="/nur-msi" set "NUR_MSI=1"
)

rem Version aus src\pii_redact\__init__.py  (__version__ = "x.y.z")
set "VERSION="
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"__version__" src\pii_redact\__init__.py') do call :setversion %%v
if not defined VERSION (
    echo FEHLER: Version in src\pii_redact\__init__.py nicht gefunden.
    goto :error
)
echo pii-redact Version %VERSION%

rem Code-Signierung (optional): signtool im PATH oder im Windows SDK suchen
set "SIGNTOOL="
if defined SIGNTOOL_ARGS call :findsigntool
if defined SIGNTOOL_ARGS if not defined SIGNTOOL (
    echo FEHLER: SIGNTOOL_ARGS ist gesetzt, aber signtool.exe wurde nicht gefunden.
    echo        Windows SDK installieren ^(Komponente "Signing Tools for Desktop Apps"^) oder signtool in den PATH aufnehmen.
    goto :error
)
if defined SIGNTOOL echo Code-Signierung mit: %SIGNTOOL%

if defined NUR_MSI (
    if not exist dist\pii-redact\pii-redact.exe (
        echo FEHLER: dist\pii-redact fehlt - erst ohne /nur-msi bauen.
        goto :error
    )
    goto :msi
)

rem ---------------------------------------------------------------- [1/6] Python
set "PY="
for %%V in (3.12 3.11) do (
    if not defined PY (
        py -%%V --version >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY set "PY=python"
echo [1/6] Python: %PY%
%PY% --version || goto :error

rem ---------------------------------------------------------------- [2/6] Build-Umgebung
if defined NEU if exist .venv-build (
    echo       Entferne alte Build-Umgebung ...
    rmdir /s /q .venv-build
)
if not exist .venv-build\Scripts\python.exe (
    echo [2/6] Lege Build-Umgebung .venv-build an ...
    %PY% -m venv .venv-build || goto :error
    .venv-build\Scripts\python -m pip install --upgrade pip >nul || goto :error
    .venv-build\Scripts\pip install -r packaging\requirements-lock.txt || goto :error
    .venv-build\Scripts\pip install "pyinstaller>=6.10,<7" || goto :error
    copy /y packaging\requirements-lock.txt .venv-build\requirements-lock.installed >nul
) else (
    rem Lock-Datei seit der letzten Installation geaendert? Dann Pakete nachziehen.
    fc /b packaging\requirements-lock.txt .venv-build\requirements-lock.installed >nul 2>nul
    if errorlevel 1 (
        echo [2/6] Paketliste geaendert - aktualisiere Build-Umgebung ...
        .venv-build\Scripts\pip install -r packaging\requirements-lock.txt || goto :error
        copy /y packaging\requirements-lock.txt .venv-build\requirements-lock.installed >nul
    ) else (
        echo [2/6] Build-Umgebung aktuell ^(komplett neu anlegen mit /neu^).
    )
)
rem Projekt selbst immer frisch (nicht editierbar) installieren
.venv-build\Scripts\pip install --no-deps --force-reinstall . >nul || goto :error

rem PyTorch/transformers duerfen nicht in der Build-Umgebung sein (sonst landen sie im Paket)
.venv-build\Scripts\python -c "import importlib.util as u,sys; bad=[m for m in ('torch','transformers') if u.find_spec(m)]; print('      FEHLER: bitte entfernen:', bad) if bad else None; sys.exit(1 if bad else 0)" || goto :error


rem ---------------------------------------------------------------- [3/6] Modelle
if defined PII_REDACT_BUNDLE_NER (
    if not exist "models\ner\%PII_REDACT_BUNDLE_NER%\model.onnx" (
        echo.
        echo FEHLER: models\ner\%PII_REDACT_BUNDLE_NER%\model.onnx fehlt.
        echo        Zuerst tools\modelle_testen.bat ausfuehren - oder mit /ohne-gruendlich bauen.
        goto :error
    )
    echo [3/6] Transformer-Modell: %PII_REDACT_BUNDLE_NER%
) else (
    echo [3/6] Ohne Transformer-Modell ^(nur Modus "Schnell"^).
)

rem ---------------------------------------------------------------- [4/6] PyInstaller
echo [4/6] PyInstaller ^(dauert einige Minuten^) ...
.venv-build\Scripts\pyinstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\pyinstaller packaging\pii-redact.spec || goto :error

rem Lizenzübersicht und -texte aller enthaltenen Komponenten erzeugen
.venv-build\Scripts\python tools\lizenzen.py dist\pii-redact || goto :error

rem Optional: Code-Signierung (Beispiele fuer SIGNTOOL_ARGS in packaging\README.md, Abschnitt Code-Signierung)
if defined SIGNTOOL (
    echo       Signiere EXE-Dateien ...
    "%SIGNTOOL%" sign %SIGNTOOL_ARGS% dist\pii-redact\pii-redact.exe dist\pii-redact\pii-redact-cli.exe || goto :error
    "%SIGNTOOL%" verify /pa /q dist\pii-redact\pii-redact.exe dist\pii-redact\pii-redact-cli.exe >nul 2>nul || echo       WARNUNG: Signatur wird auf diesem Rechner nicht als vertrauenswuerdig erkannt ^(Stammzertifikat vorhanden?^).
)

rem ---------------------------------------------------------------- [5/6] Selbsttest
echo [5/6] Selbsttest des fertigen Programms ...
dist\pii-redact\pii-redact-cli.exe --selftest || goto :error
start "" /wait dist\pii-redact\pii-redact.exe --smoke-test
if errorlevel 1 (
    echo       FEHLER: Die Oberflaeche startet nicht. Siehe %%LOCALAPPDATA%%\pii-redact\logs\pii-redact.log
    goto :error
)
echo       Oberflaeche startet: OK

rem ---------------------------------------------------------------- [6/6] MSI
:msi
if defined OHNE_MSI (
    set "MSI_STATUS=uebersprungen (/ohne-msi)"
    goto :done
)
rem WiX suchen: im PATH oder im Standardordner der dotnet-Tools (PATH ist in bereits
rem offenen Konsolen nach "dotnet tool install" oft noch nicht aktualisiert)
set "WIX="
for /f "delims=" %%w in ('where wix 2^>nul') do if not defined WIX set "WIX=%%w"
if not defined WIX if exist "%USERPROFILE%\.dotnet\tools\wix.exe" set "WIX=%USERPROFILE%\.dotnet\tools\wix.exe"
if not defined WIX (
    echo [6/6] WiX nicht gefunden - MSI wird NICHT gebaut.
    echo       Einmalig installieren ^(braucht .NET SDK 6+^), danach neue Konsole oeffnen:
    echo           dotnet tool install --global wix --version 5.0.2
    echo       Dann nur das MSI nachbauen:  build.bat /nur-msi
    set "MSI_STATUS=NICHT gebaut - WiX fehlt"
    goto :done
)
set "WIXVER="
for /f "delims=" %%w in ('call "%WIX%" --version') do if not defined WIXVER set "WIXVER=%%w"
echo [6/6] Baue MSI mit WiX %WIXVER% ...
echo %WIXVER% | findstr /b "5." >nul || echo       Hinweis: empfohlen ist WiX 5.x ^(ab v6 gilt die "Open Source Maintenance Fee"^).
if exist "dist\pii-redact-%VERSION%.msi" del /q "dist\pii-redact-%VERSION%.msi"
"%WIX%" build packaging\pii-redact.wxs -arch x64 -d Version=%VERSION% -d "SourceDir=%CD%\dist\pii-redact" -d "PackagingDir=%CD%\packaging" -o "dist\pii-redact-%VERSION%.msi" > dist\wix-build.log 2>&1
set "WIXRC=%ERRORLEVEL%"
type dist\wix-build.log
if not "%WIXRC%"=="0" (
    echo.
    echo       FEHLER beim MSI-Bau ^(Code %WIXRC%^). Die Ausgabe steht auch in dist\wix-build.log
    set "MSI_STATUS=FEHLER - siehe dist\wix-build.log"
    goto :error
)
if not exist "dist\pii-redact-%VERSION%.msi" (
    set "MSI_STATUS=FEHLER - keine MSI-Datei entstanden"
    goto :error
)
if defined SIGNTOOL (
    echo       Signiere MSI ...
    "%SIGNTOOL%" sign %SIGNTOOL_ARGS% "dist\pii-redact-%VERSION%.msi" || goto :error
)
set "MSI_STATUS=%CD%\dist\pii-redact-%VERSION%.msi"
echo       SHA-256:
certutil -hashfile "dist\pii-redact-%VERSION%.msi" SHA256 | findstr /v ":"

:done
echo.
echo ==========================================================================
echo  Fertig.
echo    Programmordner: %CD%\dist\pii-redact
echo    MSI-Paket:      %MSI_STATUS%
echo ==========================================================================
exit /b 0

:error
echo.
echo *** Build abgebrochen. Details siehe oben. ***
echo     MSI: %MSI_STATUS%
exit /b 1

:setversion
rem Hilfsroutine: entfernt Anfuehrungszeichen/Leerzeichen um die Versionsnummer
set "VERSION=%~1"
exit /b 0

:findsigntool
rem Hilfsroutine: signtool.exe im PATH, sonst neueste x64-Version aus dem Windows SDK
for /f "delims=" %%s in ('where signtool 2^>nul') do if not defined SIGNTOOL set "SIGNTOOL=%%s"
if defined SIGNTOOL exit /b 0
for /f "delims=" %%s in ('dir /b /s "%ProgramFiles(x86)%\Windows Kits\10\bin\signtool.exe" 2^>nul ^| findstr /i "x64"') do set "SIGNTOOL=%%s"
exit /b 0
