# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Rezept für pii-redact.

Erzeugt einen Ordner dist/pii-redact/ mit
    pii-redact.exe       Oberfläche (ohne Konsolenfenster)
    pii-redact-cli.exe   Kommandozeile (inkl. --selftest)
    _internal/           Python, Bibliotheken, Modelle, Hilfe

Aufruf (aus dem Projektordner, normalerweise über build.bat):
    pyinstaller --noconfirm --clean packaging/pii-redact.spec

Umgebungsvariablen (optional):
    PII_REDACT_MODELS_DIR   Ordner mit ner/<modell> (Standard: ./models)
    PII_REDACT_BUNDLE_NER   einzupackende NER-Modelle, kommagetrennt (Standard: davlan-xlmr-ner;
                            leer = ohne Modus „Gründlich“)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 – von PyInstaller gesetzt
SRC = ROOT / "src"
PKG = ROOT / "packaging"
sys.path.insert(0, str(SRC))

from pii_redact import __version__  # noqa: E402

MODELS = Path(os.environ.get("PII_REDACT_MODELS_DIR", ROOT / "models"))
NER = [n.strip() for n in os.environ.get("PII_REDACT_BUNDLE_NER", "davlan-xlmr-ner").split(",") if n.strip()]

datas, binaries, hiddenimports = [], [], []

# --- Bibliotheken, die Teile erst zur Laufzeit laden (Registries, Konfigurationsdateien, Länderdaten)
for pkg in ("spacy", "thinc", "spacy_legacy", "spacy_loggers", "srsly", "catalogue", "confection", "weasel",
            "presidio_analyzer", "phonenumbers", "tldextract", "blis", "cymem", "preshed", "murmurhash"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# spaCy findet Komponenten über „entry points“ → Paket-Metadaten mitnehmen
for dist in ("spacy", "thinc", "spacy-legacy", "spacy-loggers", "presidio-analyzer", "confection", "weasel",
             "srsly", "catalogue"):
    datas += copy_metadata(dist)
datas += collect_data_files("onnxruntime")

# --- Texterkennung (RapidOCR): Konfiguration + ONNX-Modelle liegen im Paket; nur der onnxruntime-Zweig
_OCR_SKIP = (".pytorch", ".paddle", ".openvino", ".mnn", ".tensorrt")
try:
    import rapidocr  # noqa: F401,E402
except ImportError:
    raise SystemExit(
        "\nRapidOCR (Texterkennung) fehlt in der Build-Umgebung – die Paketliste ist veraltet.\n"
        "Bitte build.bat /neu ausführen.\n"
    )
datas += collect_data_files("rapidocr")
hiddenimports += collect_submodules("rapidocr", filter=lambda n: not any(x in n for x in _OCR_SKIP))

# --- deutsches spaCy-Modell → models/spacy/de_core_news_md (wird von paths.find_spacy_model gefunden)
import de_core_news_md  # noqa: E402

datas.append((str(Path(de_core_news_md.__file__).parent), "models/spacy/de_core_news_md"))

# --- Transformer-Modell(e) für „Gründlich“
for name in NER:
    model_dir = MODELS / "ner" / name
    if not (model_dir / "model.onnx").is_file():
        raise SystemExit(
            f"\nNER-Modell fehlt: {model_dir}\n"
            "Erst tools\\modelle_testen.bat bzw. tools/convert_model.py ausführen – oder mit "
            "PII_REDACT_BUNDLE_NER= (leer) ohne Modus „Gründlich“ bauen.\n"
        )
    datas.append((str(model_dir), f"models/ner/{name}"))

# --- Anwenderhilfe, Symbol
datas.append((str(SRC / "pii_redact" / "resources"), "pii_redact/resources"))

# Diese Pakete dürfen NICHT ins Paket: groß und unnötig. Presidio würde torch/transformers sonst
# automatisch mitladen, sobald sie in der Build-Umgebung installiert sind.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "transformers", "tensorflow", "keras", "jax", "flax",
    "gliner", "onnx", "onnxscript", "sentencepiece", "stanza", "flair", "sklearn", "scipy", "pandas",
    "matplotlib", "IPython", "jupyter", "notebook", "pytest", "tkinter", "_tkinter",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    # RapidOCR: nur der onnxruntime-Zweig wird genutzt; die übrigen Rechen-Backends werden erst bei Bedarf
    # importiert und würden sonst mit eingepackt (u. a. SyntaxWarning aus dem PyTorch-Zweig)
    "rapidocr.inference_engine.pytorch", "rapidocr.inference_engine.paddle", "rapidocr.inference_engine.openvino",
    "rapidocr.inference_engine.tensorrt", "rapidocr.inference_engine.mnn",
]

ICON = str(PKG / "pii-redact.ico")


def version_resource(name, description):
    """Windows-Versionsinfo (Eigenschaften → Details) für die EXE-Dateien."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(int(x) for x in (__version__.split(".") + ["0"] * 4)[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040704B0", [
                StringStruct("CompanyName", "pii-redact"),
                StringStruct("FileDescription", description),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", name),
                StringStruct("OriginalFilename", f"{name}.exe"),
                StringStruct("ProductName", "pii-redact"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0407, 1200])]),
        ],
    )


def analysis(script):
    return Analysis(  # noqa: F821
        [str(PKG / script)],
        pathex=[str(SRC)],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports + ["pii_redact.core.onnx_recognizer", "pii_redact.core.transformer_ner",
                                       "pii_redact.core.ocr"],
        excludes=EXCLUDES,
        noarchive=False,
    )


a_gui = analysis("entry_gui.py")
a_cli = analysis("entry_cli.py")

# OpenCV bringt eine Video-Bibliothek (FFmpeg, ~25 MB) mit, die die Texterkennung nicht braucht.
for _a in (a_gui, a_cli):
    _a.binaries = [b for b in _a.binaries if "opencv_videoio_ffmpeg" not in b[0]]

exe_gui = EXE(  # noqa: F821
    PYZ(a_gui.pure),  # noqa: F821
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="pii-redact",
    console=False,
    icon=ICON,
    version=version_resource("pii-redact", "pii-redact – personenbezogene Daten schwärzen"),
    upx=False,
)
exe_cli = EXE(  # noqa: F821
    PYZ(a_cli.pure),  # noqa: F821
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="pii-redact-cli",
    console=True,
    icon=ICON,
    version=version_resource("pii-redact-cli", "pii-redact – Kommandozeile"),
    upx=False,
)
COLLECT(  # noqa: F821
    exe_gui, a_gui.binaries, a_gui.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    name="pii-redact",
    upx=False,
)
