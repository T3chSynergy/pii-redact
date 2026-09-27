"""Erzeugt LIZENZEN.txt und LIZENZTEXTE.txt für den Programmordner.

* LIZENZEN.txt    – wesentliche Komponenten (aus pii_redact.about) + Liste ALLER installierten
                    Python-Pakete der Build-Umgebung mit Version und Lizenz
* LIZENZTEXTE.txt – die Lizenztexte (LICENSE/COPYING/NOTICE) aller Pakete, dazu Python selbst

Aufruf (build.bat, mit der Python-Umgebung, aus der gebaut wird):
    .venv-build\\Scripts\\python tools\\lizenzen.py dist\\pii-redact
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata as md
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pii_redact import __version__  # noqa: E402
from pii_redact.about import components_text  # noqa: E402

# Werkzeuge der Build-Umgebung, die nicht im Programmpaket landen
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
        "pywin32-ctypes", "macholib", "pytest", "pluggy", "iniconfig", "pii-redact"}
LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)[^/]*$|(^|/)licen[cs]es/[^/]+$", re.I)
STANDARD_TEXTS = ROOT / "packaging" / "lizenztexte"

# Pakete, deren Wheel keinen Lizenztext mitliefert: Standardtext + Copyright-Vermerk des Projekts
FALLBACK = {
    "presidio-analyzer": (["MIT"], "Copyright (c) Microsoft Corporation."),
    "tokenizers": (["Apache-2.0"], "Copyright 2020 The HuggingFace Inc. team"),
    "rapidocr": (["Apache-2.0"], "Copyright RapidAI (RapidOCR); Modelle: Copyright PaddlePaddle Authors"),
    "flatbuffers": (["Apache-2.0"], "Copyright 2014 Google Inc."),
    "antlr4-python3-runtime": (["BSD-3-Clause"], "Copyright (c) 2012-2017 The ANTLR Project. All rights reserved."),
    "pyside6": (["LGPL-3.0", "GPL-3.0"], "Copyright (C) The Qt Company Ltd. and other contributors."),
    "pyside6-essentials": (["LGPL-3.0", "GPL-3.0"], "Copyright (C) The Qt Company Ltd. and other contributors."),
    "pyside6-addons": (["LGPL-3.0", "GPL-3.0"], "Copyright (C) The Qt Company Ltd. and other contributors."),
    "shiboken6": (["LGPL-3.0", "GPL-3.0"], "Copyright (C) The Qt Company Ltd. and other contributors."),
}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def fallback_texts(dist: md.Distribution) -> list[tuple[str, str]]:
    entry = FALLBACK.get(normalized(dist.metadata["Name"]))
    if not entry:
        return []
    ids, copyright_line = entry
    out = []
    for lic_id in ids:
        f = STANDARD_TEXTS / f"{lic_id}.txt"
        if f.is_file():
            out.append((f"Standardtext {lic_id}", f"{copyright_line}\n\n{f.read_text(encoding='utf-8').strip()}"))
    return out


def license_of(dist: md.Distribution) -> str:
    m = dist.metadata
    expr = (m.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (m.get("License") or "").strip()
    if lic and len(lic) < 90 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        return ", ".join(classifiers)
    return "siehe Lizenztext" if lic else "unbekannt"


def license_texts(dist: md.Distribution) -> list[tuple[str, str]]:
    out = []
    for f in dist.files or []:
        if LICENSE_FILE.search(str(f).replace("\\", "/")):
            try:
                text = Path(f.locate()).read_text(encoding="utf-8", errors="replace")
            except (OSError, AttributeError, ValueError):
                continue
            if text.strip():
                out.append((str(f), text.strip()))
    if not out:
        lic = (dist.metadata.get("License") or "").strip()
        if "\n" in lic:  # vollständiger Lizenztext im Metadatenfeld
            out.append(("METADATA: License", lic))
    return out or fallback_texts(dist)


def distributions() -> list[md.Distribution]:
    seen, out = set(), []
    for d in md.distributions():
        name = (d.metadata.get("Name") or "").strip()
        key = normalized(name)
        if not name or key in SKIP or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return sorted(out, key=lambda d: d.metadata["Name"].lower())


def main(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    dists = distributions()
    today = dt.date.today().isoformat()
    head = f"pii-redact {__version__} – enthaltene Komponenten und Lizenzen (erzeugt {today})"

    lines = [head, "=" * len(head), "",
             "pii-redact selbst: freie Software unter der GNU Affero General Public License, Version 3 oder später",
             "(AGPL-3.0-or-later), Quellcode: https://github.com/T3chSynergy/pii-redact –",
             "ohne jede Gewährleistung. Lizenztext: am Anfang von LIZENZTEXTE.txt.", "",
             "Transformer-Modell „Davlan/xlm-roberta-base-ner-hrl“ (Modus „Gründlich“, falls mitinstalliert):",
             "Academic Free License v3.0 (AFL-3.0), Lizenztext in LIZENZTEXTE.txt; Basismodell XLM-RoBERTa: MIT.", "",
             "WESENTLICHE KOMPONENTEN", "", components_text(), "",
             "ALLE ENTHALTENEN PYTHON-PAKETE", "",
             f"  {'Paket':32s} {'Version':14s} Lizenz",
             f"  {'-' * 32} {'-' * 14} {'-' * 30}"]
    lines.append(f"  {'Python':32s} {sys.version.split()[0]:14s} PSF-2.0")
    for d in dists:
        lines.append(f"  {d.metadata['Name']:32s} {d.version:14s} {license_of(d)}")
    lines += ["", "Die Lizenztexte stehen in LIZENZTEXTE.txt."]
    (target / "LIZENZEN.txt").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    texts = [f"{head} – Lizenztexte", ""]
    own = ROOT / "LICENSE"
    if own.is_file():
        texts += ["#" * 78, f"# pii-redact {__version__} – GNU AGPL-3.0-or-later", "#" * 78, "",
                  own.read_text(encoding="utf-8").strip(), ""]
    afl = STANDARD_TEXTS / "AFL-3.0.txt"
    if afl.is_file():
        texts += ["#" * 78, "# Transformer-Modell Davlan/xlm-roberta-base-ner-hrl – AFL-3.0",
                  "# https://huggingface.co/Davlan/xlm-roberta-base-ner-hrl", "#" * 78, "",
                  afl.read_text(encoding="utf-8").strip(), ""]
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    if py_license.is_file():
        texts += ["#" * 78, f"# Python {sys.version.split()[0]}", "#" * 78, "",
                  py_license.read_text(encoding="utf-8", errors="replace").strip(), ""]
    missing = []
    for d in dists:
        found = license_texts(d)
        if not found:
            missing.append(d.metadata["Name"])
            continue
        texts += ["#" * 78, f"# {d.metadata['Name']} {d.version} – {license_of(d)}", "#" * 78]
        for fname, text in found:
            texts += [f"--- {fname} ---", text, ""]
    if missing:
        texts += ["#" * 78, "# Ohne mitgelieferten Lizenztext (Lizenz siehe LIZENZEN.txt bzw. Projektseite):",
                  "#   " + ", ".join(missing)]
    (target / "LIZENZTEXTE.txt").write_text("\n".join(texts) + "\n", encoding="utf-8-sig")
    print(f"      LIZENZEN.txt / LIZENZTEXTE.txt: {len(dists)} Pakete"
          + (f", ohne Lizenztext: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
