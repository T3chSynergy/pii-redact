"""Vorgaben und Modelle der IT gelten nur aus geschützten Dateien (``machine_trust``, ``settings_store``).

Unter Windows dürfen einfache Benutzer in ``%ProgramData%`` Ordner und Dateien anlegen – ohne Prüfung könnte jeder,
der sich am selben Rechner anmeldet, „Vorgaben der IT“ für alle anderen ablegen.

* Ohne Betriebssystem-Rechte (überall): Verdrahtung – ungeschützte Vorgaben werden übergangen und gemeldet.
* Linux/macOS als root (Cloud): echte Besitzer- und Rechteprüfung mit ``chown``/``chmod``.
* Windows (CI, Administrator): echte Zugriffslisten mit einem eigens angelegten einfachen Konto (``net user``) und
  ``icacls``. In der CI schlagen diese Tests fehl statt übersprungen zu werden, wenn Rechte fehlen.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import spacy

from pii_redact import cli, machine_trust, paths, settings_store
from pii_redact.core import Settings
from pii_redact.machine_trust import read_protected, replaceable_problem, tree_problem
from pii_redact.settings_store import TrustWarning, load_settings

WINDOWS = sys.platform == "win32"
IN_CI = bool(os.environ.get("CI"))
needs_model = pytest.mark.skipif(not spacy.util.is_package("de_core_news_md"), reason="spaCy-Modell fehlt")
POLICY = {"threshold": 0.9, "entities": ["EMAIL_ADDRESS"], "allow_list": ["Erika Beispiel"],
          "locked": ["threshold", "entities"]}


def _isolate(monkeypatch, tmp_path, defaults: Path, models_problem: str | None = None) -> None:
    monkeypatch.setattr(settings_store, "defaults_path", lambda: defaults)
    monkeypatch.setattr(settings_store, "machine_models_problem", lambda: models_problem)
    monkeypatch.setattr(settings_store, "machine_config_dir", lambda: tmp_path / "maschine")


# ------------------------------------------------------------------ Verdrahtung (ohne echte Rechte)
def test_unprotected_defaults_are_ignored_and_reported(tmp_path, monkeypatch):
    defaults = tmp_path / "defaults.json"
    defaults.write_text(json.dumps(POLICY), encoding="utf-8")
    _isolate(monkeypatch, tmp_path, defaults)
    reason = "gehört PC\\mallory, nicht Administratoren oder SYSTEM"
    monkeypatch.setattr(settings_store, "read_protected", lambda p: (None, reason))
    s, warnings = load_settings(tmp_path / "settings.json")
    assert (s.threshold, s.entities, s.locked, s.org_allow_list) == (Settings().threshold, Settings().entities, [], [])
    assert [w.short for w in warnings] == ["Vorgaben der IT nicht übernommen"]
    assert str(defaults) in warnings[0].detail and reason in warnings[0].detail

    monkeypatch.setattr(settings_store, "read_protected", lambda p: (p.read_bytes(), None))
    s, warnings = load_settings(tmp_path / "settings.json")
    assert (s.threshold, s.entities, s.locked, s.org_allow_list) == (0.9, ["EMAIL_ADDRESS"], ["threshold", "entities"],
                                                                     ["Erika Beispiel"])
    assert warnings == []


def test_missing_defaults_give_no_warning(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, tmp_path / "gibt-es-nicht.json")
    assert read_protected(tmp_path / "gibt-es-nicht.json") == (None, None)
    s, warnings = load_settings(tmp_path / "settings.json")
    assert s == Settings() and warnings == []


def test_protected_defaults_keep_invalid_value_handling(tmp_path, monkeypatch):
    """Geschützte Vorgaben laufen durch dieselbe Prüfung der Werte wie bisher (falsche Art, Bereich)."""
    defaults = tmp_path / "defaults.json"
    defaults.write_bytes(b'\xef\xbb\xbf{"threshold": 45, "ocr": false}')          # mit BOM, Schwelle ungültig
    _isolate(monkeypatch, tmp_path, defaults)
    monkeypatch.setattr(settings_store, "read_protected", lambda p: (p.read_bytes(), None))
    s, _warnings = load_settings(tmp_path / "settings.json")
    assert s.threshold == Settings().threshold and s.ocr is False and s.ignored == ["threshold (defaults.json)"]


def test_unprotected_models_are_not_searched(tmp_path, monkeypatch):
    base = tmp_path / "maschine"
    (base / "models" / "ner" / "fremd").mkdir(parents=True)
    monkeypatch.setattr(paths, "machine_config_dir", lambda: base)
    monkeypatch.setattr(paths, "replaceable_problem", lambda folder: None)
    monkeypatch.setattr(paths, "tree_problem", lambda root: f"{root / 'ner' / 'fremd'}: gehört PC\\mallory")
    assert base / "models" not in paths.model_dirs()
    monkeypatch.setattr(settings_store, "defaults_path", lambda: tmp_path / "gibt-es-nicht.json")
    monkeypatch.setattr(settings_store, "machine_config_dir", lambda: base)
    _s, warnings = load_settings(tmp_path / "settings.json")
    assert [w.short for w in warnings] == ["Modelle der IT nicht genutzt"] and "mallory" in warnings[0].detail

    monkeypatch.setattr(paths, "replaceable_problem", lambda folder: "PC\\mallory darf ändern")
    monkeypatch.setattr(paths, "tree_problem", lambda root: None)
    assert base / "models" not in paths.model_dirs()                 # übergeordneter Ordner austauschbar
    monkeypatch.setattr(paths, "replaceable_problem", lambda folder: None)
    assert base / "models" in paths.model_dirs()


@needs_model
def test_cli_reports_unprotected_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_stored_settings", lambda path: (Settings(), [TrustWarning("kurz", "ausführlich")]))
    with pytest.raises(SystemExit):
        cli.main(["--version"])                                       # ohne Einstellungen: keine Meldung
    assert "ausführlich" not in capsys.readouterr().err
    (tmp_path / "leer.txt").write_text("Nichts Besonderes.\n", encoding="utf-8")
    cli.main([str(tmp_path / "leer.txt"), "--schnell", "--nur-anzeigen"])
    assert "⚠ ausführlich" in capsys.readouterr().err


def test_window_shows_unprotected_defaults(tmp_path, monkeypatch, caplog):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pii_redact.ui import main_window
    from pii_redact.ui.notices import help_anchor

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    warning = TrustWarning("Vorgaben der IT nicht übernommen", "Die zentralen Vorgaben C:\\x gelten nicht <weil>.")
    monkeypatch.setattr(main_window, "load_settings", lambda path: (Settings(), [warning]))
    _app = QApplication.instance() or QApplication([])
    with caplog.at_level("WARNING", logger="pii_redact.ui.main_window"):
        w = main_window.MainWindow()
    try:
        assert warning.detail in caplog.text
        assert "Vorgaben der IT nicht übernommen" in w.welcome_label.text()
        assert "&lt;weil&gt;" in w.welcome_label.text()                 # Pfade und Gründe maskiert
        w._show_notices(None)
        assert [n.short for n in w.notice_bar.notices] == [warning.short]
        assert help_anchor(w.notice_bar.notices[0]) == "it-vorgaben"
    finally:
        w.close()


def test_ki_consent_is_asked_again_for_another_server(window, monkeypatch, tmp_path):
    from test_ui import _open

    from pii_redact.core import llm_review
    from pii_redact.ui import dialogs

    source = tmp_path / "brief.txt"
    source.write_text("Frau Erika Beispiel wohnt in Musterstadt.\n", encoding="utf-8")
    _open(window, source)
    window.settings.llm_enabled, window.settings.llm_model = True, "test/modell"
    window.settings.llm_url = "https://llm.intern.example/v1"
    asked = []                                                        # jede Rückfrage wird abgelehnt
    monkeypatch.setattr(dialogs, "ask_yes_no", lambda *a, **k: asked.append(a[2]) and False)
    monkeypatch.setattr(llm_review, "review", lambda *a, **k: pytest.fail("ohne Zustimmung gesendet"))
    window.start_ki_review()
    assert len(asked) == 1 and "llm.intern.example" in asked[0]
    window._ki_confirmed_host = "llm.intern.example"                  # wie nach „Ja“ – dann ein anderer Server
    window.settings.llm_url = "https://anderer-server.example/v1"
    window.start_ki_review()
    assert len(asked) == 2 and "anderer-server.example" in asked[1] and window._ki_session is None


# ------------------------------------------------------------------ Linux/macOS: echte Rechte (als root)
needs_root = pytest.mark.skipif(WINDOWS or not hasattr(os, "geteuid") or os.geteuid() != 0,
                                reason="braucht root unter Linux/macOS")


@needs_root
def test_posix_file_owner_and_mode(tmp_path):
    f = tmp_path / "defaults.json"
    f.write_text("{}", encoding="utf-8")
    os.chmod(f, 0o644)
    assert read_protected(f) == (b"{}", None)
    os.chown(f, 12345, -1)
    data, problem = read_protected(f)
    assert data is None and "12345" in problem
    os.chown(f, 0, -1)
    os.chmod(f, 0o664)
    assert read_protected(f)[1] == "Gruppe oder andere dürfen schreiben"


@needs_root
def test_posix_model_tree(tmp_path):
    base = tmp_path / "pii-redact"
    model = base / "models" / "ner" / "modell"
    model.mkdir(parents=True)
    for d in (base, base / "models", base / "models" / "ner", model):
        os.chmod(d, 0o755)
    (model / "model.onnx").write_bytes(b"x")
    os.chmod(model / "model.onnx", 0o644)
    assert tree_problem(base / "models") is None and replaceable_problem(base) is None
    planted = model / "tokenizer.json"
    planted.write_text("{}", encoding="utf-8")
    os.chown(planted, 12345, -1)
    assert tree_problem(base / "models") == f"{planted}: gehört nicht root, sondern Benutzerkennung 12345"
    planted.unlink()
    os.chmod(base / "models" / "ner", 0o777)
    assert str(base / "models" / "ner") in tree_problem(base / "models")
    os.chmod(base, 0o777)
    assert replaceable_problem(base) == "Gruppe oder andere dürfen schreiben"


# ------------------------------------------------------------------ Windows: echte Zugriffslisten
needs_windows = pytest.mark.skipif(not WINDOWS, reason="Zugriffslisten nur unter Windows")
SYSTEM, ADMINS, USERS = "*S-1-5-18", "*S-1-5-32-544", "*S-1-5-32-545"


def _run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, errors="replace")
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout


def _protect(path: Path) -> None:
    """Genau die Befehle der IT-Anleitung: Vererbung aus, SYSTEM und Administratoren Vollzugriff, Benutzer lesen –
    bei Ordnern vererbt auf alles darin (ohne ``/T``: Windows überträgt geerbte Rechte selbst; mit ``/T`` blieben
    Dateien ganz ohne Rechte zurück) –, dann Besitzer Administratoren, bei Ordnern für alles darin."""
    if path.is_dir():
        _run("icacls", str(path), "/inheritance:r", "/grant:r", f"{SYSTEM}:(OI)(CI)F", f"{ADMINS}:(OI)(CI)F",
             f"{USERS}:(OI)(CI)RX")
        _run("icacls", str(path), "/setowner", ADMINS, "/T")
    else:
        _run("icacls", str(path), "/inheritance:r", "/grant:r", f"{SYSTEM}:F", f"{ADMINS}:F", f"{USERS}:RX")
        _run("icacls", str(path), "/setowner", ADMINS)


@pytest.fixture()
def admin():
    if not WINDOWS:
        pytest.skip("nur Windows")
    import ctypes

    if not ctypes.windll.shell32.IsUserAnAdmin():
        if IN_CI:
            pytest.fail("Die Prüfung der Zugriffslisten braucht in der CI Administratorrechte")
        pytest.skip("braucht Administratorrechte")


@pytest.fixture()
def plain_user(admin):
    """Eigens angelegtes einfaches Konto (kein Administrator) – so sähe ein Angreifer auf dem Rechner aus."""
    name = "piitest" + secrets.token_hex(3)
    # höchstens 14 Zeichen – sonst fragt „net user“ nach (Kompatibilität mit alten Windows-Versionen)
    _run("net", "user", name, "Pw" + secrets.token_hex(5) + "!9", "/add")
    yield name
    subprocess.run(["net", "user", name, "/delete"], capture_output=True)


@pytest.fixture()
def program_data_dir(admin):
    """Ordner unter dem echten ProgramData – erbt dessen Rechte wie ein von der IT angelegter Ordner."""
    d = machine_trust.program_data() / f"pii-redact-test-{secrets.token_hex(3)}"
    d.mkdir()
    yield d
    _run("icacls", str(d), "/reset", "/T")
    shutil.rmtree(d, ignore_errors=True)


@needs_windows
def test_windows_program_data_location(admin):
    assert machine_trust.program_data() == Path(os.environ["PROGRAMDATA"])
    assert paths.machine_config_dir() == Path(os.environ["PROGRAMDATA"]) / "pii-redact"


@needs_windows
def test_windows_users_may_create_in_program_data(admin):
    """Ausgangslage, die der Sicherheitsprüfung zugrunde liegt: Einfache Benutzer dürfen in ProgramData anlegen.
    Ändert Microsoft das, fällt es hier auf."""
    problem = machine_trust.path_problem(machine_trust.program_data(), rights=machine_trust._ADD)
    assert problem and problem.endswith("darf ändern"), problem


@needs_windows
def test_windows_file_checks(tmp_path, plain_user):
    f = tmp_path / "defaults.json"
    f.write_text('{"threshold": 0.9}', encoding="utf-8")
    _protect(f)
    assert read_protected(f) == (b'{"threshold": 0.9}', None)

    _run("icacls", str(f), "/grant", f"{USERS}:(M)")                  # Benutzer dürfen ändern
    data, problem = read_protected(f)
    assert data is None and problem.endswith("darf ändern"), problem
    _protect(f)

    _run("icacls", str(f), "/setowner", plain_user)                   # Datei gehört einem einfachen Konto
    data, problem = read_protected(f)
    assert data is None and plain_user.lower() in problem.lower() and "nicht Administratoren" in problem, problem

    _protect(f)
    _run("icacls", str(f), "/grant", f"{plain_user}:(W)")
    assert plain_user.lower() in read_protected(f)[1].lower()


@needs_windows
def test_windows_usual_it_setup_without_false_alarm(program_data_dir):
    """Üblicher Weg der IT ohne eigene Rechte: Ordner unter ProgramData anlegen und ``defaults.json`` als
    Administrator hineinkopieren. Die Datei ist dann geschützt (Benutzer dürfen sie nicht ändern) – keine Warnung.
    Der Modellordner dagegen nicht: Darin dürfen Benutzer nach den geerbten Rechten Dateien anlegen."""
    f = program_data_dir / "defaults.json"
    f.write_text("{}", encoding="utf-8")
    owner = _run("icacls", str(f))
    assert read_protected(f) == (b"{}", None), owner
    assert replaceable_problem(program_data_dir) is None, _run("icacls", str(program_data_dir))

    model = program_data_dir / "models" / "ner" / "modell"
    model.mkdir(parents=True)
    (model / "model.onnx").write_bytes(b"x")
    problem = tree_problem(program_data_dir / "models")
    assert problem and problem.endswith("darf ändern"), problem
    _protect(program_data_dir)                                         # wie in der IT-Anleitung
    assert tree_problem(program_data_dir / "models") is None, _run("icacls", str(model))


@needs_windows
def test_windows_planted_model_is_found(program_data_dir, plain_user):
    model = program_data_dir / "models" / "ner" / "modell"
    model.mkdir(parents=True)
    (model / "model.onnx").write_bytes(b"x")
    _protect(program_data_dir)
    planted = model / "tokenizer.json"
    planted.write_text("{}", encoding="utf-8")
    _run("icacls", str(planted), "/setowner", plain_user)
    problem = tree_problem(program_data_dir / "models")
    assert problem.startswith(str(planted)) and plain_user.lower() in problem.lower(), problem


@needs_windows
@needs_model
def test_windows_cli_with_real_program_data(admin, plain_user, tmp_path, monkeypatch, capsys):
    """Ende zu Ende mit dem echten ``C:\\ProgramData\\pii-redact``: untergeschobene Vorgaben gelten nicht und
    werden gemeldet; dieselbe Datei, geschützt, gilt. Ein vorhandener Ordner wird nie angefasst."""
    base = paths.machine_config_dir()
    if base.exists():
        if IN_CI:
            pytest.fail(f"{base} gibt es in der CI schon")
        pytest.skip(f"{base} gibt es auf diesem Rechner – wird nicht verändert")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    text = tmp_path / "brief.txt"
    text.write_text("Frau Erika Mustermann schreibt an erika@firma.example.\n", encoding="utf-8")
    base.mkdir()
    try:
        defaults = base / "defaults.json"
        defaults.write_text(json.dumps({"entities": ["EMAIL_ADDRESS"], "locked": ["entities"]}), encoding="utf-8")
        _run("icacls", str(defaults), "/setowner", plain_user)
        cli.main([str(text), "--schnell", "--nur-anzeigen"])
        out = capsys.readouterr()
        assert "gelten nicht" in out.err and str(defaults) in out.err
        assert re.search(r"Person", out.out)                           # Vorgabe (nur E-Mail) galt nicht

        _protect(base)
        cli.main([str(text), "--schnell", "--nur-anzeigen"])
        out = capsys.readouterr()
        assert "gelten nicht" not in out.err and not re.search(r"Person", out.out), (out.out, out.err)
        assert load_settings()[0].locked == ["entities"]
    finally:
        _run("icacls", str(base), "/reset", "/T")
        shutil.rmtree(base, ignore_errors=True)
