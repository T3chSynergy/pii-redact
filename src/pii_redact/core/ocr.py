"""Texterkennung (OCR) als Rückfallebene für gescannte PDF-Seiten.

Normalerweise laufen Dokumente vorher durch eine zentrale OCR und haben eine Textebene. Diese
Stufe greift nur bei Seiten, die daran vorbeigelaufen sind (z. B. eingescannte Mail-Anhänge).

Technik: RapidOCR mit PaddleOCR-Modellen (PP-OCRv6, mehrsprachig inkl. Umlaute) über
onnxruntime – komplett offline, die Modelle liegen im Python-Paket ``rapidocr``.

OCR-Ergebnisse sind naturgemäß fehleranfälliger als eine echte Textebene. Alle Stellen werden
deshalb gekennzeichnet (``CharBox.ocr``), und die Oberfläche fordert eine gründliche Prüfung.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

import pymupdf as fitz

#: Auflösung, mit der Seiten für die Texterkennung gerendert werden.
OCR_DPI = 200
#: Wörter unter dieser Erkennungssicherheit gelten als „unsicher“.
LOW_CONFIDENCE = 0.80
#: Seite wird per OCR gelesen, wenn Bilder mindestens diesen Anteil bedecken …
IMAGE_COVERAGE = 0.5
#: … und höchstens so viele Zeichen echten Text trägt (z. B. Kopfzeile einer gedruckten Mail).
MAX_TEXT_CHARS = 200


class OcrUnavailableError(RuntimeError):
    pass


@dataclass
class OcrWord:
    text: str
    score: float
    rect: tuple[float, float, float, float]   # PDF-Koordinaten wie bei der Textextraktion


_engine = None
_lock = threading.Lock()
_cache: OrderedDict[tuple[str, int], list[list[OcrWord]]] = OrderedDict()
_CACHE_PAGES = 64


def is_available() -> bool:
    try:
        import rapidocr  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr import RapidOCR
        except Exception as exc:  # noqa: BLE001
            raise OcrUnavailableError(f"Texterkennung (RapidOCR) nicht verfügbar: {exc}") from exc
        _engine = RapidOCR(params={
            "Global.log_level": "error",
            # Die Lageerkennung je Zeile dreht lange Zeilen gelegentlich fälschlich um 180° –
            # Seitendrehungen kommen über die PDF-Seitendrehung ohnehin richtig an.
            "Global.use_cls": False,
            "Global.max_side_len": 4000,
        })
    return _engine


def needs_ocr(page: fitz.Page, text_chars: int) -> bool:
    """Soll die Seite per OCR gelesen werden?

    * Seite ohne Text, aber mit Bild(ern) → ja
    * Bilder bedecken den Großteil der Seite und es gibt nur wenig Text (Kopf-/Fußzeile,
      Seitenzahl, Stempel einer gedruckten Mail) → ja
    Seiten, die bereits eine (auch unsichtbare) OCR-Textebene haben, tragen viel Text → nein."""
    if text_chars > MAX_TEXT_CHARS:
        return False
    infos = page.get_image_info()
    if not infos:
        return False
    if text_chars == 0:
        return True
    area = abs(page.rect)
    if not area:
        return False
    covered = 0.0
    for info in infos:
        r = fitz.Rect(info["bbox"]) & page.rect
        if not r.is_empty:
            covered += abs(r)
    return covered / area >= IMAGE_COVERAGE


def doc_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ocr_page(page: fitz.Page, key: str | None = None) -> list[list[OcrWord]]:
    """Liest eine Seite per OCR. Rückgabe: Zeilen mit Wörtern (Koordinaten wie ``get_text``)."""
    ck = (key, page.number) if key else None
    if ck is not None:
        with _lock:
            if ck in _cache:
                _cache.move_to_end(ck)
                return _cache[ck]
    import numpy as np

    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=fitz.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1].copy()
    # Pixel → Koordinaten der (gedrehten) Seite → Koordinaten wie bei der Textextraktion
    to_page = fitz.Matrix(72 / OCR_DPI, 72 / OCR_DPI) * page.derotation_matrix
    with _lock:
        engine = _get_engine()
        result = engine(img, return_word_box=True)
    lines: list[list[OcrWord]] = []
    word_results = getattr(result, "word_results", None) or ()
    for idx, txt in enumerate(result.txts or ()):
        words = word_results[idx] if idx < len(word_results) else ()
        score = float(result.scores[idx]) if result.scores is not None else 1.0
        out: list[OcrWord] = []
        if words:
            for w_text, w_score, quad in words:
                if w_text.strip():
                    out.append(OcrWord(w_text, float(w_score), _rect(quad, to_page)))
        else:
            # Keine Wort-Positionen: Zeile gleichmäßig auf die Wörter verteilen
            box = _rect(result.boxes[idx], to_page)
            out = _split_line(txt, score, box)
        if out:
            lines.append(out)
    if ck is not None:
        with _lock:
            _cache[ck] = lines
            while len(_cache) > _CACHE_PAGES:
                _cache.popitem(last=False)
    return lines


def ocr_page_text(page: fitz.Page, key: str | None = None) -> str:
    return "\n".join(" ".join(w.text for w in line) for line in ocr_page(page, key))


def _rect(quad, m: fitz.Matrix) -> tuple[float, float, float, float]:
    pts = [fitz.Point(float(x), float(y)) * m for x, y in quad]
    r = fitz.Rect(pts[0], pts[0])
    for p in pts[1:]:
        r |= p
    return (r.x0, r.y0, r.x1, r.y1)


def _split_line(text: str, score: float, box: tuple[float, float, float, float]) -> list[OcrWord]:
    x0, y0, x1, y1 = box
    n = max(len(text), 1)
    out, pos = [], 0
    for part in text.split(" "):
        if part:
            a = x0 + (x1 - x0) * pos / n
            b = x0 + (x1 - x0) * (pos + len(part)) / n
            out.append(OcrWord(part, score, (a, y0, b, y1)))
        pos += len(part) + 1
    return out


def selftest() -> str:
    """Kurzer Funktionstest (für --selftest): rendert Text als Bild und liest ihn zurück."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=120)
    page.insert_text((20, 60), "Prüfung Größe 12345", fontsize=22, fontname="helv")
    pix = page.get_pixmap(dpi=150)
    img_doc = fitz.open()
    p2 = img_doc.new_page(width=400, height=120)
    p2.insert_image(p2.rect, pixmap=pix)
    text = ocr_page_text(p2)
    doc.close()
    img_doc.close()
    return text
