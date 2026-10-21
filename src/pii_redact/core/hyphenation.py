"""Silbentrennung am Zeilenende („Bergen-⏎thaler“) für die Erkennung aufheben.

Die Erkennung arbeitet auf einer Fassung des Textes, in der Trennstrich und Zeilenumbruch fehlen
(„Bergenthaler“). Die Funde werden danach auf den Originaltext zurückgerechnet und decken so beide
Zeilen ab. Als Trennung gilt nur ein Strich zwischen Buchstaben am Zeilenende, auf den in der nächsten
Zeile ein Kleinbuchstabe folgt – echte Bindestriche vor einem Großbuchstaben („Schneider-⏎Kowalczyk“,
„E-⏎Mail“) bleiben erhalten.
"""

from __future__ import annotations

import bisect
import re

# Trennstrich (auch weiches Trennzeichen U+00AD oder U+2010), Zeilenende – manche PDFs legen jede Zeile in
# einen eigenen Textblock, dann folgt ein zweiter Umbruch – und der Rest des Wortes.
_SOFT_BREAK = re.compile(r"(?<=[^\W\d_])[-\u00ad\u2010][ \t]*\n(?:[ \t]*\n)?[ \t]*(?=[^\W\d_])")


class HyphenMap:
    """Rechnet Positionen im zusammengefügten Text (ohne Trennungen) auf den Originaltext zurück."""

    def __init__(self) -> None:
        self._at: list[int] = []      # Position im zusammengefügten Text, an der etwas entfernt wurde
        self._shift: list[int] = []   # bis dahin insgesamt entfernte Zeichen

    def __bool__(self) -> bool:
        return bool(self._at)

    def _add(self, at: int, removed: int) -> None:
        self._at.append(at)
        self._shift.append((self._shift[-1] if self._shift else 0) + removed)

    def pos(self, i: int) -> int:
        """Original-Position des Zeichens an Stelle ``i`` des zusammengefügten Textes."""
        k = bisect.bisect_right(self._at, i)
        return i + (self._shift[k - 1] if k else 0)

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Bereich [start, end) im zusammengefügten Text → Bereich im Original (inkl. Trennstrich und Umbruch)."""
        if end <= start:
            p = self.pos(start)
            return p, p
        return self.pos(start), self.pos(end - 1) + 1


def dehyphenate(text: str) -> tuple[str, HyphenMap]:
    """Text ohne Trennungen am Zeilenende und die Zuordnung zurück zum Original."""
    hmap = HyphenMap()
    parts: list[str] = []
    joined_len = last = 0
    for m in _SOFT_BREAK.finditer(text):
        if not text[m.end()].islower():
            continue
        parts.append(text[last:m.start()])
        joined_len += m.start() - last
        hmap._add(joined_len, m.end() - m.start())
        last = m.end()
    if not hmap:
        return text, hmap
    parts.append(text[last:])
    return "".join(parts), hmap


def join_hyphenated(value: str) -> str:
    """„Bergen-⏎thaler“ → „Bergenthaler“ (z. B. für Vergleiche und Listen)."""
    return dehyphenate(value)[0]
