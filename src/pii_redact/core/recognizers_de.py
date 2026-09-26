"""Erkenner-Konfiguration für deutsche Dokumente.

Nutzt die in Presidio enthaltenen deutschen Erkenner (Steuer-ID, SV-Nummer, Ausweis, Kfz …),
aktiviert die sprachunabhängigen Erkenner (E-Mail, IBAN, Telefon …) für Deutsch und ergänzt
eigene Muster für Anschriften, Geburtsdaten und Namen nach Anrede.
"""

from __future__ import annotations

import re

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer
from presidio_analyzer import predefined_recognizers as pr

LANG = "de"
_FLAGS = re.MULTILINE  # bewusst OHNE IGNORECASE: Groß-/Kleinschreibung trägt Information

_UPPER = "A-ZÄÖÜ"
_LOWER = "a-zäöüß"
_NAME_WORD = rf"[{_UPPER}][{_LOWER}]+(?:-[{_UPPER}][{_LOWER}]+)*"

# --------------------------------------------------------------------------- Anschrift
_STREET_SUFFIX = (
    r"(?i:stra(?:ß|ss)e|str\.|weg|allee|gasse|platz|ring|damm|ufer|chaussee|steig|pfad|"
    r"promenade|markt|graben|kamp|twiete|zeile|gärten|garten|anger|wall|brücke|tor)"
)
_HOUSE_NO = r"\d{1,4}(?:[ \t]?[a-zA-Z](?![\w]))?(?:[ \t]?[-–/][ \t]?\d{1,4}[a-zA-Z]?)?"


class DeAddressRecognizer(PatternRecognizer):
    """Straße + Hausnummer sowie PLZ + Ort."""

    PATTERNS = [
        Pattern(
            "Straße Hausnummer (zusammengesetzt)",
            rf"\b[{_UPPER}][\w\-äöüß]*?{_STREET_SUFFIX}[ \t]+{_HOUSE_NO}",
            0.65,
        ),
        Pattern(
            "Straße Hausnummer (zweiteilig, z. B. Berliner Straße 5)",
            rf"\b[{_UPPER}][{_LOWER}]+(?:er)?[ \t]{_STREET_SUFFIX}[ \t]+{_HOUSE_NO}",
            0.65,
        ),
        Pattern(
            "Am/An der/Im … + Hausnummer",
            rf"\b(?:Am|An der|An den|Auf dem|Auf der|Im|In der|In den|Zum|Zur|Hinter der|Unter den)"
            rf"[ \t]{_NAME_WORD}[ \t]\d{{1,3}}[a-z]?\b",
            0.3,
        ),
        Pattern(
            "PLZ + Ort",
            rf"(?<![\d.,])\b\d{{5}}[ \t]+(?!(?:Euro|EUR|Stück|Mal|Einwohner|Menschen|Personen)\b)"
            rf"{_NAME_WORD}(?:[ \t](?:am|an[ \t]der|im|in|bei|ob[ \t]der)[ \t][{_UPPER}][{_LOWER}]+)?",
            0.55,
        ),
    ]
    CONTEXT = ["anschrift", "adresse", "wohnhaft", "wohnort", "straße", "strasse", "postanschrift", "wohnt"]

    def __init__(self):
        super().__init__(
            supported_entity="DE_ADDRESS",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language=LANG,
            global_regex_flags=_FLAGS,
            name="DeAddressRecognizer",
        )


# --------------------------------------------------------------------------- Datum
_MONTHS = "Januar|Jänner|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"


class DeDateRecognizer(PatternRecognizer):
    """Deutsche Datumsformate. Niedriger Grundwert – erst Kontext wie „geboren“ hebt sie über
    die Schwelle, damit nicht jedes Rechnungsdatum geschwärzt wird."""

    PATTERNS = [
        Pattern(
            "TT.MM.JJJJ",
            r"\b(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])\.(?:19|20)?\d{2}\b",
            0.3,
        ),
        Pattern(
            "TT. Monat JJJJ",
            rf"\b(?:0?[1-9]|[12]\d|3[01])\.[ \t]?(?:{_MONTHS})[ \t](?:19|20)\d{{2}}\b",
            0.3,
        ),
        Pattern("ISO", r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b", 0.25),
    ]
    CONTEXT = ["geboren", "geb", "geburtsdatum", "geburtstag", "geburtsort", "jahrgang", "verstorben", "gestorben"]

    def __init__(self):
        super().__init__(
            supported_entity="DATE_TIME",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language=LANG,
            global_regex_flags=_FLAGS,
            name="DeDateRecognizer",
        )


# --------------------------------------------------------------------------- Namen nach Anrede
class DeTitledPersonRecognizer(PatternRecognizer):
    """„Herr Müller“, „Frau Dr. Anna Schmidt“ – ergänzt die NER, die kurze Namen oft übersieht.
    Markiert wird nur der Name, nicht die Anrede."""

    PATTERNS = [
        Pattern(
            "Anrede + Name",
            rf"(?<=\b(?:Herrn?|Frau|Hr\.|Fr\.|Dr\.|Prof\.)[ \t]+(?:(?:Dr|Prof)\.[ \t]+)*(?:med\.[ \t]+)?)"
            rf"(?!(?:Dr|Prof|med)\.)(?!Doktor\b|Professor\b|Präsident\b|Direktor\b|Vorsitzende\b)"
            rf"{_NAME_WORD}(?:[ \t]{_NAME_WORD})?",
            0.6,
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="PERSON",
            patterns=self.PATTERNS,
            supported_language=LANG,
            global_regex_flags=_FLAGS,
            name="DeTitledPersonRecognizer",
        )


# --------------------------------------------------------------------------- Telefon
class DePhoneRecognizer(pr.PhoneRecognizer):
    """Telefonnummern für DE/AT/CH – mit etwas höherem Grundwert als Presidio-Standard."""

    SCORE = 0.55

    def __init__(self):
        super().__init__(
            supported_language=LANG,
            supported_regions=("DE", "AT", "CH"),
            context=["telefon", "tel", "mobil", "handy", "fax", "rufnummer", "erreichbar", "anrufen", "festnetz"],
            name="DePhoneRecognizer",
        )

    _DATE_LIKE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")

    def analyze(self, text, entities, nlp_artifacts=None):
        results = super().analyze(text, entities, nlp_artifacts)
        # libphonenumber hält „02.09.2026“ gelegentlich für eine Nummer
        return [r for r in results if not self._DATE_LIKE.match(text[r.start:r.end].strip())]


class DeSocialSecuritySpacedRecognizer(PatternRecognizer):
    """Rentenversicherungsnummer in der üblichen Schreibweise mit Leerzeichen
    („65 170839 J 00 3“) – Presidio erkennt nur die zusammengeschriebene Form."""

    def __init__(self):
        super().__init__(
            supported_entity="DE_SOCIAL_SECURITY",
            patterns=[Pattern("RVNR mit Leerzeichen", r"\b\d{2}[ ]\d{6}[ ]?[A-Z][ ]?\d{2}[ ]?\d\b", 0.5)],
            context=["rentenversicherungsnummer", "sozialversicherungsnummer", "versicherungsnummer", "rvnr", "svnr"],
            supported_language=LANG,
            global_regex_flags=_FLAGS,
            name="DeSocialSecuritySpacedRecognizer",
        )
        self._checker = pr.DeSocialSecurityRecognizer()

    def validate_result(self, pattern_text: str):
        return self._checker.validate_result(pattern_text.replace(" ", ""))


# --------------------------------------------------------------------------- E-Mail (offline)
class OfflineEmailRecognizer(pr.EmailRecognizer):
    """Presidio prüft die Top-Level-Domain über ``tldextract``, das beim ersten Aufruf die
    Public-Suffix-Liste aus dem Internet laden will. Für eine rein lokale Anwendung
    ersetzen wir das durch eine einfache Offline-Prüfung."""

    def validate_result(self, pattern_text: str):
        return bool(re.search(r"@[\w.-]+\.[A-Za-z]{2,}$", pattern_text))


# --------------------------------------------------------------------------- Registry
def build_recognizers() -> list[EntityRecognizer]:
    """Alle Erkenner für deutsche Texte (ohne NER – die kommt aus der NLP-Engine)."""
    recs: list[EntityRecognizer] = [
        pr.SpacyRecognizer(
            supported_language=LANG,
            supported_entities=["PERSON", "LOCATION", "ORGANIZATION"],
        ),
        OfflineEmailRecognizer(supported_language=LANG, context=["email", "e-mail", "mail", "kontakt"]),
        pr.IbanRecognizer(supported_language=LANG, context=["iban", "bank", "konto", "bankverbindung", "überweisung"]),
        pr.CreditCardRecognizer(supported_language=LANG, context=["kreditkarte", "karte", "visa", "mastercard", "amex"]),
        pr.IpRecognizer(supported_language=LANG, context=["ip", "ip-adresse", "server", "rechner"]),
        pr.UrlRecognizer(supported_language=LANG, context=["url", "website", "webseite", "link", "homepage"]),
        DePhoneRecognizer(),
        DeAddressRecognizer(),
        DeDateRecognizer(),
        DeTitledPersonRecognizer(),
        DeSocialSecuritySpacedRecognizer(),
    ]
    for name in (
        "DeTaxIdRecognizer",
        "DeTaxNumberRecognizer",
        "DeVatIdRecognizer",
        "DeSocialSecurityRecognizer",
        "DeHealthInsuranceRecognizer",
        "DeIdCardRecognizer",
        "DePassportRecognizer",
        "DeFuehrerscheinRecognizer",
        "DeKfzRecognizer",
        "DePlzRecognizer",
        "DeHandelsregisterRecognizer",
        "DeLanrRecognizer",
        "DeBsnrRecognizer",
    ):
        cls = getattr(pr, name, None)  # ältere Presidio-Versionen haben nicht alle
        if cls is not None:
            recs.append(cls())
    return recs
