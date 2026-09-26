"""Frei gezogene Bereiche (Unterschrift, Foto, Handschrift) im PDF."""

import os
from pathlib import Path

import pymupdf
import pytest

from pii_redact.core import Finding, ReplaceMode, load_document, redact_pdf, redact_text, verify_pdf
from pii_redact.core.batch import finding_stats, findings_from_compact, findings_to_compact

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SIG = (0, 55, 335, 270, 390)      # Unterschrift in samples/unterschrift.pdf
FOTO = (0, 435, 95, 535, 225)     # Passfoto
HAND = (0, 145, 455, 270, 480)    # handschriftliche Notiz


def _area(area, etype="AREA", active=True) -> Finding:
    return Finding(0, 0, etype, "", source="manuell", recognizer="Bereich", area=area, active=active)


def _drawings(page, rect):
    r = pymupdf.Rect(rect)
    return [d for d in page.get_drawings() if d["rect"].intersects(r) and d.get("fill") != (0.0, 0.0, 0.0)]


def test_area_removes_vector_signature_and_image_pixels():
    doc = load_document(SAMPLES / "unterschrift.pdf")
    findings = [_area(SIG, "AREA_SIGNATURE"), _area(FOTO, "AREA_IMAGE"), _area(HAND, "AREA_HANDWRITING", active=False)]
    data = redact_pdf(doc, findings, ReplaceMode.PLACEHOLDER)
    assert verify_pdf(data, findings) == []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        page = pdf[0]
        assert _drawings(page, SIG[1:]) == []                 # gezeichnete Unterschrift weg
        assert len(_drawings(page, HAND[1:])) == 1            # abgewählter Bereich bleibt
        assert page.get_images() == []                        # vollständig bedecktes Foto ist ganz entfernt
        text = page.get_text()
        assert "[UNTERSCHRIFT]" in text and "[FOTO]" in text
        assert "Bewerbung als Sachbearbeiterin" in text        # Rest bleibt


def test_area_keeps_graphics_that_only_touch(tmp_path):
    src = pymupdf.open(SAMPLES / "unterschrift.pdf")
    page = src[0]
    shape = page.new_shape()                                    # Tabellenrahmen um die Unterschrift
    shape.draw_rect(pymupdf.Rect(40, 320, 300, 420))
    shape.draw_line(pymupdf.Point(40, 370), pymupdf.Point(300, 370))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    path = tmp_path / "tabelle.pdf"
    src.save(path)
    doc = load_document(path)
    data = redact_pdf(doc, [_area(SIG, "AREA_SIGNATURE")], ReplaceMode.BLACKOUT)
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        rects = [tuple(round(v) for v in d["rect"]) for d in pdf[0].get_drawings()]
    assert (40, 320, 300, 420) in rects                         # Tabelle wiederhergestellt
    assert (62, 343, 212, 381) not in rects                     # Unterschrift weg


def test_areas_do_not_touch_text_and_roundtrip():
    doc = load_document(SAMPLES / "unterschrift.pdf")
    a = _area(SIG, "AREA_SIGNATURE")
    assert redact_text(doc.text, [a], ReplaceMode.PLACEHOLDER).text == doc.text   # Text unverändert
    compact = findings_to_compact([a])
    assert compact[0]["ar"] == [0, 55.0, 335.0, 270.0, 390.0]
    (back,) = findings_from_compact(compact, doc.text)
    assert back.area == a.area and back.entity_type == "AREA_SIGNATURE" and back.text == ""
    st = finding_stats([a, _area(FOTO, active=False)])
    assert st["areas"] == 1 and st["added"] == 0


@pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM", "offscreen") != "offscreen", reason="nur offscreen")
def test_session_area_with_text_and_menu(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication, QMenu

    QApplication.instance() or QApplication([])
    from pii_redact.core import Settings
    from pii_redact.ui.main_window import MainWindow
    from pii_redact.ui.session import DocumentSession

    doc = load_document(SAMPLES / "unterschrift.pdf")
    s = DocumentSession(doc, Settings())
    start = doc.text.index("Carla Neumann")
    added = s.add_area(0, (55, 388, 140, 402), "AREA_SIGNATURE", [(start, start + 13)])
    assert added[0].is_area and added[1].text == "Carla Neumann"
    assert "Carla" not in s.output_text()                      # Text im Bereich auch im Text-Export weg
    s.undo()
    assert s.findings == []                                     # ein Rückgängig-Schritt für beides
    s.redo()
    assert len(s.findings) == 2 and s.same_text_ids(added[0].id) == [added[0].id]

    # Menü nach Rahmen ohne Text: bietet „Bereich schwärzen als“ an
    w = MainWindow()
    try:
        w.open_file(SAMPLES / "unterschrift.pdf")
        menu = w.build_area_menu(0, pymupdf.Rect(*FOTO[1:]), [])
        sub = next(m for m in menu.findChildren(QMenu) if m.title() == "Bereich schwärzen als")
        assert [a.text() for a in sub.actions()] == ["Bereich", "Unterschrift", "Foto/Bild", "Handschrift"]
        sub.actions()[2].trigger()                               # „Foto/Bild“
        assert any(f.is_area and f.entity_type == "AREA_IMAGE" for f in w.session.findings)
        import time

        t = time.time()
        while (w.runner.busy or w.findings.model.rowCount() == 0) and time.time() - t < 60:
            QApplication.processEvents()
            time.sleep(0.05)
        assert w.findings.model.rowCount() >= 1
        assert any(f.is_area for f in w.session.findings)       # bleibt nach der Analyse erhalten
    finally:
        if w.session:
            w.session.dirty = False
        w.close()
