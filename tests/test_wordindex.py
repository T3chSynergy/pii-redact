"""Wortverzeichnis für die Suche nach Fundtexten (``analyzer.WordIndex``).

Die Kontrolle vor dem Speichern und die Kontrolle nach dem Export suchen den Wortlaut jedes geschwärzten Textes
im Dokument. Das Verzeichnis muss dabei genau das finden, was die Suche mit dem Muster (``word_pattern``) im
ganzen Text findet – nur ohne für jeden Fundtext den ganzen Text zu durchsuchen."""

import random
import re
import sys
from pathlib import Path

import pytest

from pii_redact.core import Finding
from pii_redact.core.analyzer import _IOTA, _LETTER_RUN, WordIndex, _fold, further_occurrences, word_pattern
from pii_redact.core.redactor import _PageSearch

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _reference(text: str, term: str) -> list[tuple[int, int]]:
    """Die Suche ohne Verzeichnis: das Muster über den ganzen Text."""
    pattern = word_pattern(term)
    return [] if pattern is None else [m.span() for m in pattern.finditer(text)]


def _found(text: str, term: str) -> list[str]:
    spans = [m.span() for m in WordIndex(text).finditer(term)]
    assert spans == _reference(text, term)
    return [text[s:e] for s, e in spans]


# ---------------------------------------------------------------- einzelne Fälle
def test_whole_words_and_numbers_only():
    text = "Am Ende der Woche, nicht am Wochenende – siehe akte_ende.pdf. Nr. 4711, nicht 14711 und nicht 47110."
    assert _found(text, "Ende") == ["Ende", "ende"]
    assert _found(text, "4711") == ["4711"]


def test_name_with_any_whitespace_and_in_addresses():
    text = "Max Mustermann wohnt hier.\nMAX\n MUSTERMANN, maxmustermann@firma.example, Maximilian Mustermann"
    assert _found(text, "Max Mustermann") == ["Max Mustermann", "MAX\n MUSTERMANN", "maxmustermann"]
    assert _found(text, "Herrn Max Mustermann.") == ["Max Mustermann", "MAX\n MUSTERMANN", "maxmustermann"]


def test_matches_do_not_overlap():
    assert _found("Anna Anna Anna Anna Anna", "Anna Anna") == ["Anna Anna", "Anna Anna"]
    assert _found("0130 0130 0130", "0130 0130") == ["0130 0130"]


@pytest.mark.parametrize("text, term, n", [
    ("Frau Yılmaz, FRAU YILMAZ, frau yilmaz, Frau YİLMAZ", "Yılmaz", 4),     # I, i, ı und İ gelten als gleich
    ("Frau Yılmaz, FRAU YILMAZ, frau yilmaz", "YILMAZ", 3),
    ("Lindenstraße 4, LINDENSTRAẞE 4, Lindenstrasse 4", "Lindenstraße", 2),   # ß gleicht ẞ, nicht „ss“
    ("Haus Wasſer und Haus Wasser", "Wasser", 2),                              # langes s
    ("Müller-Lüdenscheid kam, müller-lüdenscheid.pdf, Müller kam", "Müller-Lüdenscheid", 2),
    ("IBAN DE89 3704 0044 0532 0130 00, DE89370400440532013000, DE89 3704 0044 0532 0130 001",
     "DE89 3704 0044 0532 0130 00", 2),
    ("Tel. +49 30 1234567 oder +49301234567, nicht 49 30 12345678", "+49 30 1234567", 2),   # beginnt mit „+“
    ("geb. 17.11.1984, am 17.11.1984. und 117.11.1984", "17.11.1984", 2),
    ("Στην Ιθάκη, στην ιθάκη", "Ιθάκη", 2),                                    # griechisches Iota: Suche im ganzen Text
    ("ab", "ab", 0),                                                            # unter drei Zeichen: nicht gesucht
    ("", "Mustermann", 0),
])
def test_awkward_cases(text, term, n):
    assert len(_found(text, term)) == n


# ---------------------------------------------------------------- Vergleich mit der Suche im ganzen Text
def _random_terms(rng: random.Random, text: str, count: int) -> list[str]:
    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    terms = []
    for _ in range(count if words else 0):
        i = rng.randrange(len(words))
        j = min(len(words) - 1, i + rng.choice([0, 0, 0, 1, 1, 2, 3]))
        s, e = words[i][0], words[j][1]
        if e - s > 5 and rng.random() < 0.2:
            s += rng.randint(1, 2)          # mitten im Wort beginnen
        if e - s > 5 and rng.random() < 0.2:
            e -= rng.randint(1, 2)
        terms.append(text[s:e])
    return terms


def test_same_matches_as_the_pattern_in_sample_texts():
    rng = random.Random(20261111)
    files = sorted(p for p in SAMPLES.rglob("*") if p.suffix in (".txt", ".md"))
    assert len(files) >= 8
    compared = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        index = WordIndex(text)
        for term in _random_terms(rng, text, 200):
            expected = _reference(text, term)
            assert [m.span() for m in index.finditer(term)] == expected, (path.name, term)
            compared += len(expected)
    assert compared > 1000      # der Vergleich war nicht leer


def test_same_matches_as_the_pattern_in_random_texts():
    """Zufallstexte aus Zeichen, an denen die Suche ohne Groß-/Kleinschreibung heikel ist."""
    rng = random.Random(4711)
    pieces = list("abeimnorstuyzABEIMNSTYZäöüÄÖÜßẞıİſµμιΙ019._-@/+ \n") + [
        " ", "-\n", "  ", "Yılmaz", "YILMAZ", "Straße", "STRASSE", "STRAẞE", "Mustermann", "mustermann", "Max",
        "0532", "DE89", "ﬁ", "ﬀ", "ͅ"]
    compared = 0
    for _ in range(1500):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(20, 200)))
        index = WordIndex(text)
        for term in _random_terms(rng, text, 6):
            expected = _reference(text, term)
            assert [m.span() for m in index.finditer(term)] == expected, (text, term)
            compared += len(expected)
    assert compared > 5000


# ---------------------------------------------------------------- Annahmen über Groß-/Kleinschreibung
def _chars():
    return (chr(cp) for cp in range(sys.maxunicode + 1) if not 0xD800 <= cp <= 0xDFFF)


def test_key_is_coarser_than_the_pattern_ignoring_case():
    """Was der reguläre Ausdruck ohne Groß-/Kleinschreibung gleichsetzt, muss denselben Schlüssel haben – sonst
    fände das Verzeichnis eine Stelle nicht, die das Muster trifft. Geprüft für jedes Zeichen gegen seine Groß-,
    Klein- und Titelform."""
    for c in _chars():
        forms = {x for x in (c.lower(), c.upper(), c.title(), c.swapcase(), c.casefold()) if len(x) == 1 and x != c}
        if forms:
            pattern = re.compile(re.escape(c), re.IGNORECASE)
            for other in forms:
                assert not pattern.fullmatch(other) or _fold(other) == _fold(c), (c, other)


def test_letters_stay_letters_ignoring_case():
    """Ein Treffer beginnt an einem Wortanfang und bleibt im Wort: Ohne Groß-/Kleinschreibung gleicht ein
    Buchstabe nur Buchstaben. Einzige Ausnahme ist das griechische Iota (``_IOTA``) – Fundtexte, die damit
    beginnen, sucht ``WordIndex`` im ganzen Text."""
    for c in _chars():
        low = c.lower()[:1]
        assert low == c or bool(_LETTER_RUN.fullmatch(c)) == bool(_LETTER_RUN.fullmatch(low)), c
        assert not c.isalpha() or _LETTER_RUN.fullmatch(c), c
        assert c.isdecimal() == bool(re.fullmatch(r"\d", c)), c
        assert not c.isdecimal() or c.lower() == c.upper() == c, c
    casefix = pytest.importorskip("re._casefix")    # Tabelle der Regex-Bibliothek: zusätzlich gleichgesetzte Zeichen
    for low_code, others in casefix._EXTRA_CASES.items():
        group = [chr(low_code), *map(chr, others)]
        assert len({_fold(c) for c in group}) == 1, group
        assert all(_LETTER_RUN.fullmatch(c) for c in group) or set(group) <= _IOTA, group


# ---------------------------------------------------------------- Aufwand
def _long_document(pages: int = 60) -> tuple[str, list[Finding]]:
    rng = random.Random(3)
    first = ["Anna", "Max", "Zofia", "Karl", "Ayse", "Jonas", "Petra", "Ole", "Mira", "Tarek"]
    parts = ["ber", "gen", "tha", "ler", "mus", "ter", "mann", "hoff", "schnei", "der", "kay", "yil", "maz", "eger"]
    fill = ["die", "Rechnung", "wurde", "am", "Montag", "geprüft", "und", "der", "Betrag", "von", "120", "Euro"]
    names = [rng.choice(first) + " " + "".join(rng.choice(parts) for _ in range(3)).capitalize() for _ in range(150)]
    lines, findings, pos = [], [], 0
    for _ in range(pages * 45):
        line = " ".join(rng.choice(fill) for _ in range(11))
        if rng.random() < 0.18:
            name = rng.choice(names)
            if rng.random() < 0.9:          # die meisten Vorkommen sind als Fund markiert, einige nicht
                findings.append(Finding(pos, pos + len(name), "PERSON", name))
            line = name + " " + line
        lines.append(line)
        pos += len(line) + 1
    return "\n".join(lines), findings


def test_only_matching_word_starts_are_tried():
    """Je Fundtext werden nur die Stellen geprüft, an denen ein Wort mit seinem ersten Wort beginnt – nicht der
    ganze Text. (Die Suche mit dem Muster allein ginge für jeden der 150 Namen durch alle 200 000 Zeichen.)"""
    text, findings = _long_document()
    index = WordIndex(text)
    terms = sorted({f.text for f in findings})
    tried = sum(len(index._starts(term.split()[0]) or []) for term in terms)
    assert len(text) > 150_000 and len(terms) > 100
    assert tried < len(text) / 2            # statt 150 × 200 000
    for term in terms[:20]:
        assert [m.span() for m in index.finditer(term)] == _reference(text, term)
    suggestions = further_occurrences(text, findings)
    assert suggestions and all(text[f.start:f.end] == f.text and not f.active for f in suggestions)


# ---------------------------------------------------------------- Seiten des Ergebnisses (Kontrolle nach dem Export)
def test_page_search_counts_per_page_and_never_across_pages():
    pages = ["Herr Max Mustermann\nschreibt an Max", "Mustermann, Max.\nakte_mustermann.pdf  Muster-\nmann", "",
             "MAX MUSTERMANN"]
    search = _PageSearch(pages)
    # „Max“ am Ende von Seite 1 und „Mustermann“ am Anfang von Seite 2 sind kein Vorkommen von „Max Mustermann“
    assert search.hits("Max Mustermann") == [0, 3]
    assert search.hits("Mustermann") == [0, 1, 1, 1, 3]         # auch über die Silbentrennung „Muster-⏎mann“
    assert search.hits("Max") == [0, 0, 1, 3]
    assert search.hits("ab") is None                            # unter drei Zeichen: nicht gesucht
    for term in ("Max Mustermann", "Mustermann", "Max", "schreibt an", "akte_mustermann.pdf"):
        pattern = word_pattern(term)
        expected = [pno for pno, text in enumerate(search.pages) for _ in pattern.finditer(text)]
        assert search.hits(term) == expected, term
    # ein Fundtext mit dem Trennzeichen der Seiten (kommt praktisch nicht vor) wird Seite für Seite gesucht
    assert _PageSearch(["a\x00bc d", "A\x00BC"]).hits("a\x00bc") == [0, 1]
