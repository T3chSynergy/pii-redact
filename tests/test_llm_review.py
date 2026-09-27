"""KI-Nachprüfung mit einem lokalen, OpenAI-kompatiblen Testserver (kein Internet, kein echtes Modell)."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pii_redact.core import Finding, Settings, load_document
from pii_redact.core import llm_review as lr
from pii_redact.core.batch import findings_from_compact, findings_to_compact

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

TEXT = ("Protokoll der Sitzung vom 3. März\n\n"
        "Frau Lena Krüger, Schwerbehindertenvertreterin im Einkauf am Standort Nord, berichtete.\n"
        "Rückfragen an Herrn Olaf Brandt, Personalnummer 48213.\n")


class FakeLLM(BaseHTTPRequestHandler):
    """Antwortet je nach Pfad mit einer festen Bewertung; merkt sich die Anfragen."""
    requests: list = []
    answer: dict | str = {}
    status = 200
    echo_kennung = True   # False = Modell „sieht“ die Anfrage nicht vollständig (gekürzter Kontext)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if FakeLLM.status != 200:
            self.send_response(FakeLLM.status)
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "nope"}}')
            return
        answer = FakeLLM.answer
        if isinstance(answer, dict):
            msgs = " ".join(m["content"] for m in body["messages"])
            k = re.findall(r"Prüfkennung Teil [12]: (\w+)", msgs)
            answer = dict(answer, kennung="".join(k) if FakeLLM.echo_kennung else k[0] if k else "")
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):
        pass


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.delenv(lr.KEY_ENV, raising=False)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    FakeLLM.requests, FakeLLM.status, FakeLLM.echo_kennung = [], 200, True
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def _settings(url, **kw):
    s = Settings(llm_enabled=True, llm_url=url, llm_model="test/modell", llm_api_key="geheim", **kw)
    return s


def _doc(tmp_path):
    p = tmp_path / "protokoll.txt"
    p.write_text(TEXT, encoding="utf-8")
    doc = load_document(p)
    # „Lena Krüger“ und „Olaf Brandt“ sind geschwärzt, die Personalnummer wurde übersehen
    f = [Finding(TEXT.index("Lena"), TEXT.index("Lena") + 11, "PERSON", "Lena Krüger"),
         Finding(TEXT.index("Olaf"), TEXT.index("Olaf") + 11, "PERSON", "Olaf Brandt")]
    return doc, f


def test_only_redacted_text_is_sent_and_hints_map_to_original(server, tmp_path):
    doc, findings = _doc(tmp_path)
    FakeLLM.answer = {
        "risiko": "hoch",
        "begruendung": "Funktion und Standort identifizieren die Person.",
        "hinweise": [
            {"zitat": "[PERSON_1], Schwerbehindertenvertreterin im Einkauf am Standort Nord",
             "art": "KONTEXT", "begruendung": "Einmalige Funktion"},
            {"zitat": "Personalnummer 48213", "art": "NUMMER", "begruendung": "Personalnummer"},
            {"zitat": "Dr. Erfunden", "art": "PERSON", "begruendung": "halluziniert"},
            {"zitat": "[PERSON_2]", "art": "PERSON", "begruendung": "nur Platzhalter"},
        ],
    }
    res = lr.review(doc, findings, _settings(server))
    sent = FakeLLM.requests[0]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == "Bearer geheim"
    user = sent["body"]["messages"][1]["content"]
    assert "Lena" not in user and "Olaf" not in user and "[PERSON_1]" in user and "[PERSON_2]" in user
    assert sent["body"]["model"] == "test/modell" and sent["body"]["temperature"] == 0

    assert res.risk == "hoch" and res.host == f"127.0.0.1:{server.rsplit(':', 1)[1].split('/')[0]}"
    by_quote = {h.quote: h for h in res.hints}
    ctx = by_quote["[PERSON_1], Schwerbehindertenvertreterin im Einkauf am Standort Nord"]
    assert [TEXT[s:e] for s, e in ctx.spans] == ["Schwerbehindertenvertreterin im Einkauf am Standort Nord"]
    assert ctx.entity_type == "CONTEXT"
    assert [TEXT[s:e] for s, e in by_quote["Personalnummer 48213"].spans] == ["Personalnummer 48213"]
    assert not by_quote["Dr. Erfunden"].found and not by_quote["[PERSON_2]"].found
    assert res.unmatched == 2

    sugg = lr.suggestions(res, findings, doc.text)
    assert {f.text for f in sugg} == {"Schwerbehindertenvertreterin im Einkauf am Standort Nord", "Personalnummer 48213"}
    assert all(f.source == "ki" and not f.active and f.recognizer.startswith("KI-Nachprüfung") for f in sugg)
    # bleiben im Arbeitsstand erhalten
    back = findings_from_compact(findings_to_compact(sugg), doc.text)
    assert {(f.source, f.active) for f in back} == {("ki", False)}


def test_whitespace_tolerant_quotes_and_code_fences(server, tmp_path):
    doc, findings = _doc(tmp_path)
    FakeLLM.answer = {"risiko": "mittel", "begruendung": "x", "hinweise": [
        {"zitat": "im   Einkauf\nam Standort", "art": "KONTEXT", "begruendung": ""}]}
    res = lr.review(doc, findings, _settings(server))
    assert res.risk == "mittel" and [TEXT[s:e] for s, e in res.hints[0].spans] == ["im Einkauf am Standort"]


def test_errors_are_readable(server, tmp_path):
    doc, findings = _doc(tmp_path)
    FakeLLM.status = 401
    with pytest.raises(lr.LlmError, match="401.*API-Schlüssel"):
        lr.review(doc, findings, _settings(server))
    FakeLLM.status = 200
    FakeLLM.answer = "Ich kann dabei leider nicht helfen."
    with pytest.raises(lr.LlmError, match="kein JSON"):
        lr.review(doc, findings, _settings(server))
    with pytest.raises(lr.LlmError, match="nicht erreichbar"):
        lr.chat(_settings("http://127.0.0.1:9/v1", llm_timeout=5), [{"role": "user", "content": "x"}])
    with pytest.raises(lr.LlmError, match="nicht eingerichtet"):
        lr.review(doc, findings, Settings())


def test_env_key_wins_and_full_endpoint_accepted(server, monkeypatch, tmp_path):
    monkeypatch.setenv(lr.KEY_ENV, "aus-umgebung")
    FakeLLM.answer = {"risiko": "gering", "begruendung": "", "hinweise": []}
    msg = lr.test_connection(_settings(server + "/chat/completions", llm_max_chars=8000))
    assert "Verbindung in Ordnung" in msg and "8.000 Zeichen" in msg
    assert FakeLLM.requests[0]["auth"] == "Bearer aus-umgebung"
    assert FakeLLM.requests[0]["path"] == "/v1/chat/completions"
    # der Test schickt eine Anfrage in voller Länge – prüft so auch das Kontextfenster
    assert len(FakeLLM.requests[0]["body"]["messages"][1]["content"]) > 8000


def test_truncated_context_is_detected(server, tmp_path):
    doc, findings = _doc(tmp_path)
    FakeLLM.answer = {"risiko": "gering", "begruendung": "alles gut", "hinweise": []}
    FakeLLM.echo_kennung = False  # Server hat das Ende der Anfrage abgeschnitten
    with pytest.raises(lr.LlmError, match="Kontextfenster"):
        lr.review(doc, findings, _settings(server))
    with pytest.raises(lr.LlmError, match="Kontextfenster"):
        lr.test_connection(_settings(server))


def test_pdf_is_split_at_page_boundaries(server):
    doc = load_document(SAMPLES / "beispiel.pdf")
    FakeLLM.answer = {"risiko": "gering", "begruendung": "ok", "hinweise": []}
    small = max(len(doc.text) // 2, 2000)
    res = lr.review(doc, [], _settings(server, llm_max_chars=small))
    assert res.chunks == len(FakeLLM.requests) >= 1
    if doc.page_count > 1 and len(doc.text) > small:
        assert res.chunks > 1 and "Seite" in FakeLLM.requests[0]["body"]["messages"][1]["content"]
    assert res.risk == "gering"


def test_chunks_cover_text_without_gaps(tmp_path):
    p = tmp_path / "lang.txt"
    p.write_text(("Absatz mit Text. " * 40 + "\n\n") * 30, encoding="utf-8")
    doc = load_document(p)
    red = lr.reviewed_text(doc, [])
    parts = lr.chunks(doc, red, 2000)
    assert parts[0][0] == 0 and parts[-1][1] == len(red.text)
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))
    assert all(e - s <= 2000 for s, e, _ in parts)


def test_context_is_not_a_searchable_type():
    from pii_redact.core.entities import DEFAULT_ENTITIES, info

    assert info("CONTEXT").label == "Kontext (indirekt)" and not info("CONTEXT").searchable
    assert "CONTEXT" not in DEFAULT_ENTITIES


# ---------------------------------------------------------------- Oberfläche
def test_ui_review_flow(server, tmp_path, monkeypatch):
    import os
    import re
    import time

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.instance() or QApplication([])
    from pii_redact.ui.main_window import MainWindow

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    w = MainWindow()
    try:
        assert not w.actions_["ki_review"].isVisible()  # nicht eingerichtet → unsichtbar
        w.settings.llm_enabled, w.settings.llm_url, w.settings.llm_model = True, server, "test/modell"
        w.show()
        p = tmp_path / "protokoll.txt"
        p.write_text(TEXT, encoding="utf-8")
        w.open_file(p)
        t = time.time()
        while w.runner.busy and time.time() - t < 120:
            wait(100)
        assert w.actions_["ki_review"].isVisible() and w.actions_["ki_review"].isEnabled()
        red = w.session.redacted().text
        quote = re.search(r"Schwerbehindertenvertreterin[^\n\[]*", red).group(0).strip(" .,")
        FakeLLM.answer = {"risiko": "hoch", "begruendung": "Funktion identifiziert die Person.",
                          "hinweise": [{"zitat": quote, "art": "KONTEXT", "begruendung": "einmalige Funktion"}]}
        before = len(w.session.findings)
        w.start_ki_review()
        t = time.time()
        while w._ki_session is not None and time.time() - t < 30:
            wait(50)
        assert asked and "127.0.0.1" in asked[0]
        sent = FakeLLM.requests[-1]["body"]["messages"][1]["content"]
        assert "Lena" not in sent and "Krüger" not in sent
        ki = [f for f in w.session.findings if f.source == "ki"]
        assert len(w.session.findings) == before + 1 and len(ki) == 1 and not ki[0].active
        assert ki[0].entity_type == "CONTEXT" and ki[0].text.startswith("Schwerbehindertenvertreterin")
        assert "HOCH" in w.ki_panel.head.text() and w.ki_panel.list.count() == 1
        # Klick auf den Hinweis wählt den Vorschlag aus
        w.ki_panel.list.itemClicked.emit(w.ki_panel.list.item(0))
        assert w.selected_ids == {ki[0].id}
        w.ki_panel.all_btn.click()
        assert ki[0].active and "[KONTEXT" not in red and "[KONTEXT]" in w.session.redacted().text
        w.session.undo()
        w.ki_panel.discard_btn.click()
        assert not [f for f in w.session.findings if f.source == "ki"]
        # zweite Prüfung: keine erneute Rückfrage
        w.start_ki_review()
        t = time.time()
        while w._ki_session is not None and time.time() - t < 30:
            wait(50)
        assert len(asked) == 1
    finally:
        if w.session:
            w.session.dirty = False
        w.close()


def test_parse_json_tolerates_fences_and_thinking():
    raw = '<think>erst überlegen {x}</think>\n```json\n{"risiko": "hoch", "hinweise": []}\n```'
    assert lr.parse_json(raw)["risiko"] == "hoch"
