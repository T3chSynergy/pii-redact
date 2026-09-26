@echo off
rem Laedt die Kandidaten-Modelle, wandelt die klassischen NER-Modelle nach ONNX um und
rem vergleicht alles mit spaCy. Ergebnis: benchmark_ergebnis.md im Projektordner.
rem Benoetigt Internet (einmalig ca. 3-4 GB Download: PyTorch + Modelle).
setlocal
cd /d "%~dp0\.."

if not exist .venv-tools\Scripts\python.exe (
    echo [1/4] Lege Werkzeug-Umgebung .venv-tools an ...
    set "PY="
    for %%V in (3.12 3.11 3.13 3.10) do (
        if not defined PY (
            py -%%V --version >nul 2>nul && set "PY=py -%%V"
        )
    )
    if not defined PY set "PY=python"
    call %%PY%% -m venv .venv-tools || goto :error
    call .venv-tools\Scripts\python -m pip install --upgrade pip >nul
    call .venv-tools\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu || goto :error
    call .venv-tools\Scripts\pip install -r requirements-tools.txt || goto :error
    call .venv-tools\Scripts\pip install -e . || goto :error
    call .venv-tools\Scripts\python -m spacy download de_core_news_md || goto :error
) else (
    echo [1/4] Werkzeug-Umgebung vorhanden.
)

echo [2/4] Wandle Davlan/xlm-roberta-base-ner-hrl nach ONNX um (zwei Varianten) ...
.venv-tools\Scripts\python tools\convert_model.py Davlan/xlm-roberta-base-ner-hrl --name davlan-xlmr-ner --quant int8
if errorlevel 1 echo    ^> Warnung: Variante int8 nicht erzeugt - weiter mit den anderen.
.venv-tools\Scripts\python tools\convert_model.py Davlan/xlm-roberta-base-ner-hrl --name davlan-xlmr-ner-emb --quant embed
if errorlevel 1 echo    ^> Warnung: Variante embed nicht erzeugt - weiter mit den anderen.

echo [3/4] (fhswf/bert_de_ner entfaellt - im Vergleich nicht konkurrenzfaehig)

echo [4/4] Vergleiche alle Kandidaten (dauert einige Minuten) ...
.venv-tools\Scripts\python tools\benchmark_models.py || goto :error

echo.
echo Fertig. Ergebnis: benchmark_ergebnis.md
start "" notepad benchmark_ergebnis.md
pause
exit /b 0

:error
echo.
echo *** Abgebrochen. Details siehe oben. ***
pause
exit /b 1
