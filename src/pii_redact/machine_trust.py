"""Vorgaben der IT und zusätzliche Modelle nur aus geschützten Dateien.

Unter ``%ProgramData%\\pii-redact`` liegen die zentralen Vorgaben (``defaults.json`` mit ``locked`` und den Listen
der Organisation) und zusätzliche Modelle. Unter Windows dürfen einfache Benutzer in ``%ProgramData%``
standardmäßig Ordner und Dateien anlegen. Ohne Prüfung könnte deshalb jeder, der sich am selben Rechner anmeldet –
etwa auf einem Terminalserver –, eigene „Vorgaben der IT“ ablegen: mit gesperrten Einstellungen und einer
Ausnahmeliste für alle anderen, oder mit einem eigenen Server für die KI-Nachprüfung.

Deshalb gilt eine Datei aus diesem Ordner nur, wenn sie **geschützt** ist:

* Windows: Sie gehört SYSTEM, der Gruppe Administratoren oder TrustedInstaller, und kein anderes Konto darf sie
  ändern, löschen, umbenennen oder ihre Rechte ändern – bei Ordnern auch nichts darin anlegen.
* Linux/macOS: Sie gehört root, und Gruppe und andere dürfen nicht schreiben.

Sonst wird sie übergangen; Kommandozeile und Oberfläche warnen (Text für die IT). Ob ein Ordner selbst geschützt
ist, spielt für ``defaults.json`` keine Rolle: Eine Datei, die einem Administrator gehört, kann ein einfacher
Benutzer nicht ändern, und eine ausgetauschte Datei gehört dem, der sie angelegt hat. Geprüft wird die geöffnete
Datei selbst, dann wird aus derselben geöffneten Datei gelesen – so lässt sie sich zwischen Prüfen und Lesen nicht
austauschen.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

#: Konten, denen die Dateien der IT gehören dürfen und die sie ändern dürfen (Windows, als SID)
TRUSTED_SIDS = {
    "S-1-5-18": "SYSTEM",
    "S-1-5-32-544": "Administratoren",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464": "TrustedInstaller",
}
#: Platzhalter, die für den Besitzer stehen (dürfen Rechte haben, wenn der Besitzer vertrauenswürdig ist)
_OWNER_PLACEHOLDERS = {"S-1-3-0", "S-1-3-4"}           # ERSTELLER-BESITZER, OWNER RIGHTS

# Zugriffsrechte, mit denen sich eine Datei oder ein Ordner verändern ließe
_ADD = 0x2 | 0x4                       # Datei anlegen/Daten schreiben, Ordner anlegen/Daten anhängen
_REPLACE = (0x40                       # Unterordner und Dateien löschen
            | 0x10000                  # löschen (auch umbenennen)
            | 0x40000 | 0x80000         # Rechte ändern, Besitz übernehmen
            | 0x10000000 | 0x40000000) # alle Rechte, allgemeines Schreibrecht
WRITE_RIGHTS = _ADD | _REPLACE

HINT = ("Die IT sollte den Ordner so anlegen, dass nur Administratoren schreiben dürfen – siehe IT-Anleitung, "
        "Abschnitt „Zentrale Vorgaben“.")


def read_protected(path: Path) -> tuple[bytes | None, str | None]:
    """Inhalt einer Datei der IT, wenn sie geschützt ist. Rückgabe ``(Inhalt, None)``; ``(None, None)``, wenn es
    die Datei nicht gibt; ``(None, Grund)``, wenn sie nicht geschützt ist (der Grund nennt den Besitzer bzw. das
    Konto mit Schreibrecht)."""
    try:
        f = open(path, "rb")   # noqa: SIM115 – Prüfung und Lesen an derselben geöffneten Datei
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"nicht lesbar ({exc.strerror or exc})"
    with f:
        problem = _problem_of_open_file(f.fileno(), path)
        if problem:
            return None, problem
        return f.read(), None


def tree_problem(root: Path) -> str | None:
    """None, wenn der Ordner und alles darin geschützt ist; sonst der erste Grund samt Pfad. Jeder Eintrag wird
    schon beim Auflisten geprüft – ein Unterordner, der sich nicht lesen lässt, wird so nicht übergangen."""
    problem = path_problem(root)
    if problem:
        return f"{root}: {problem}"
    unreadable: list[str] = []
    for here, dirs, files in os.walk(root, onerror=lambda exc: unreadable.append(f"{exc.filename}: nicht lesbar")):
        dirs.sort()
        for name in sorted(dirs + files):
            p = Path(here) / name
            problem = path_problem(p)
            if problem:
                return f"{p}: {problem}"
        if unreadable:
            return unreadable[0]
    return unreadable[0] if unreadable else None


def replaceable_problem(folder: Path) -> str | None:
    """Kann ein anderes Konto Einträge in diesem Ordner löschen, umbenennen oder seine Rechte ändern? Anlegen
    darf es darin etwas (das betrifft vorhandene Einträge nicht). None = nein."""
    return path_problem(folder, rights=_REPLACE)


def path_problem(path: Path, rights: int = WRITE_RIGHTS) -> str | None:
    """None, wenn Datei oder Ordner geschützt ist; sonst der Grund."""
    if sys.platform == "win32":
        return _windows_checked(str(path), handle=None, rights=rights)
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"nicht lesbar ({exc.strerror or exc})"
    return _posix_problem(st)


def _problem_of_open_file(fd: int, path: Path) -> str | None:
    if sys.platform == "win32":
        import msvcrt

        return _windows_checked(str(path), handle=msvcrt.get_osfhandle(fd), rights=WRITE_RIGHTS)
    return _posix_problem(os.fstat(fd))


# ---------------------------------------------------------------------- Linux/macOS
def _posix_problem(st: os.stat_result) -> str | None:
    if st.st_uid != 0:
        return f"gehört nicht root, sondern Benutzerkennung {st.st_uid}"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "Gruppe oder andere dürfen schreiben"
    return None


# ---------------------------------------------------------------------- Windows
def _windows_checked(path: str, handle: int | None, rights: int) -> str | None:
    try:
        return _windows_problem(path, handle, rights)
    except Exception as exc:  # noqa: BLE001 – lieber übergehen als ungeprüft übernehmen
        return f"Rechte nicht prüfbar ({type(exc).__name__}: {exc})"


def _windows_problem(path: str, handle: int | None, rights: int) -> str | None:
    """Besitzer und Zugriffsliste prüfen – über den Pfad oder, wenn angegeben, über die geöffnete Datei."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    out = ctypes.POINTER(ctypes.c_void_p)
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    what = 0x1 | 0x4                                     # Besitzer und Zugriffsliste
    if handle is None:
        get = advapi32.GetNamedSecurityInfoW
        get.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, out, out, out, out, out]
        target: object = path
    else:
        get = advapi32.GetSecurityInfo
        get.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, out, out, out, out, out]
        target = wintypes.HANDLE(handle)
    get.restype = wintypes.DWORD
    error = get(target, 1, what, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error:
        return f"Rechte nicht lesbar (Windows-Fehler {error})"
    try:
        owner_sid = _sid_string(advapi32, kernel32, owner)
        if owner_sid not in TRUSTED_SIDS:
            return f"gehört {_account(advapi32, owner) or owner_sid}, nicht Administratoren oder SYSTEM"
        if not dacl.value:
            return "ohne Zugriffsbeschränkung (jeder darf alles)"
        for sid, mask in _allowing_aces(advapi32, dacl):
            sid_text = _sid_string(advapi32, kernel32, sid)
            if mask & rights and sid_text not in TRUSTED_SIDS and sid_text not in _OWNER_PLACEHOLDERS:
                return f"{_account(advapi32, sid) or sid_text} darf ändern"
        return None
    except OSError as exc:              # lieber übergehen als ungeprüft übernehmen
        return f"Rechte nicht lesbar ({exc})"
    finally:
        kernel32.LocalFree(descriptor)


def _allowing_aces(advapi32, dacl):
    """(SID, Rechte) aller Einträge der Zugriffsliste, die Rechte gewähren und für dieses Objekt selbst gelten."""
    import ctypes
    from ctypes import wintypes

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]

    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    info = AclSizeInformation()
    if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):   # AclSizeInformation
        raise OSError(ctypes.get_last_error(), "Zugriffsliste nicht lesbar")
    for i in range(info.AceCount):
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, i, ctypes.byref(ace)):
            raise OSError(ctypes.get_last_error(), "Eintrag der Zugriffsliste nicht lesbar")
        base = ace.value
        ace_type = ctypes.c_ubyte.from_address(base).value
        flags = ctypes.c_ubyte.from_address(base + 1).value
        if flags & 0x08:                    # nur zum Vererben, gilt nicht für dieses Objekt
            continue
        mask = ctypes.c_uint32.from_address(base + 4).value
        if ace_type in (0x0, 0x9):          # erlauben (auch mit Bedingung)
            yield ctypes.c_void_p(base + 8), mask
        elif ace_type in (0x5, 0xB):        # erlauben für Objekttypen: SID nach Kennzeichen und GUIDs
            kinds = ctypes.c_uint32.from_address(base + 8).value
            offset = 12 + (16 if kinds & 1 else 0) + (16 if kinds & 2 else 0)
            yield ctypes.c_void_p(base + offset), mask
        # Verbote und alles andere gewähren nichts


def _sid_string(advapi32, kernel32, sid) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise OSError(ctypes.get_last_error(), "SID nicht lesbar")
    try:
        return text.value or ""
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _account(advapi32, sid) -> str:
    """Kontoname zu einer SID (für die Warnung) – leer, wenn er sich nicht ermitteln lässt."""
    import ctypes
    from ctypes import wintypes

    advapi32.LookupAccountSidW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_int)]
    name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
    n_name, n_domain, use = wintypes.DWORD(256), wintypes.DWORD(256), ctypes.c_int()
    if not advapi32.LookupAccountSidW(None, sid, name, ctypes.byref(n_name), domain, ctypes.byref(n_domain),
                                      ctypes.byref(use)):
        return ""
    return f"{domain.value}\\{name.value}" if domain.value else name.value


def program_data() -> Path:
    """Ordner ``ProgramData`` laut Windows (``SHGetKnownFolderPath``) – nicht aus der Umgebungsvariablen, die jeder
    Prozess selbst setzen kann. Rückfall: ``%PROGRAMDATA%`` bzw. ``C:\\ProgramData``."""
    fallback = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    if sys.platform != "win32":
        return fallback
    import ctypes
    import uuid
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID("62AB5D82-FDC1-4DC3-A9DD-070D1D495D97")      # FOLDERID_ProgramData
    guid = Guid(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    try:
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(Guid), wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        result = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result)) != 0:
            return fallback
        try:
            return Path(result.value) if result.value else fallback
        finally:
            ole32.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))
    except (OSError, AttributeError):
        return fallback
