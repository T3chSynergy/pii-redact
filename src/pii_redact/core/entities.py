"""Entitätstypen: Anzeigenamen, Platzhalter und Farben an einer Stelle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityInfo:
    key: str          # Presidio-Entitätsname
    label: str        # Anzeigename in der Oberfläche
    token: str        # Platzhalter im anonymisierten Text
    color: str        # Hervorhebungsfarbe (Hex)
    default_on: bool = True
    area: bool = False  # nur für frei gezogene Bereiche im PDF (nicht Teil der automatischen Suche)
    searchable: bool = True  # in den Einstellungen als „gesuchte Datenart“ wählbar


_ENTITIES: list[EntityInfo] = [
    EntityInfo("PERSON", "Person", "PERSON", "#e8590c"),
    EntityInfo("LOCATION", "Ort", "ORT", "#1c7ed6"),
    EntityInfo("ORGANIZATION", "Organisation", "ORGANISATION", "#7048e8", default_on=False),
    EntityInfo("DE_ADDRESS", "Adresse", "ADRESSE", "#1098ad"),
    EntityInfo("EMAIL_ADDRESS", "E-Mail", "E-MAIL", "#d6336c"),
    EntityInfo("PHONE_NUMBER", "Telefon", "TELEFON", "#c2255c"),
    EntityInfo("DATE_TIME", "Geburtsdatum/Datum", "DATUM", "#5c940d"),
    EntityInfo("IBAN_CODE", "IBAN", "IBAN", "#e67700"),
    EntityInfo("CREDIT_CARD", "Kreditkarte", "KREDITKARTE", "#e67700"),
    EntityInfo("IP_ADDRESS", "IP-Adresse", "IP-ADRESSE", "#495057"),
    EntityInfo("URL", "URL", "URL", "#495057", default_on=False),
    EntityInfo("DE_TAX_ID", "Steuer-ID", "STEUER-ID", "#a61e4d"),
    EntityInfo("DE_TAX_NUMBER", "Steuernummer", "STEUERNUMMER", "#a61e4d"),
    EntityInfo("DE_VAT_ID", "USt-IdNr.", "UST-ID", "#a61e4d", default_on=False),
    EntityInfo("DE_SOCIAL_SECURITY", "Sozialversicherungsnr.", "SV-NUMMER", "#a61e4d"),
    EntityInfo("DE_HEALTH_INSURANCE", "Krankenversichertennr.", "KV-NUMMER", "#a61e4d"),
    EntityInfo("DE_ID_CARD", "Personalausweisnr.", "AUSWEISNUMMER", "#a61e4d"),
    EntityInfo("DE_PASSPORT", "Reisepassnr.", "PASSNUMMER", "#a61e4d"),
    EntityInfo("DE_FUEHRERSCHEIN", "Führerscheinnr.", "FUEHRERSCHEIN", "#a61e4d"),
    EntityInfo("DE_KFZ", "Kfz-Kennzeichen", "KENNZEICHEN", "#a61e4d"),
    EntityInfo("DE_PLZ", "Postleitzahl", "PLZ", "#1098ad"),
    EntityInfo("DE_HANDELSREGISTER", "Handelsregisternr.", "HANDELSREGISTER", "#7048e8", default_on=False),
    EntityInfo("DE_LANR", "Arztnummer (LANR)", "LANR", "#a61e4d"),
    EntityInfo("DE_BSNR", "Betriebsstättennr.", "BSNR", "#a61e4d", default_on=False),
    EntityInfo("CUSTOM", "Benutzerdefiniert", "GESCHWÄRZT", "#212529"),
    # Indirekt identifizierende Angaben (Funktion + Abteilung, seltene Merkmale …) – aus der KI-Nachprüfung
    # oder manuell, nicht Teil der automatischen Suche
    EntityInfo("CONTEXT", "Kontext (indirekt)", "KONTEXT", "#862e9c", default_on=False, searchable=False),
    # Frei gezogene Bereiche (Unterschriften, Fotos, Handschrift …) – nur manuell
    EntityInfo("AREA", "Bereich", "BEREICH", "#495057", default_on=False, area=True),
    EntityInfo("AREA_SIGNATURE", "Unterschrift", "UNTERSCHRIFT", "#495057", default_on=False, area=True),
    EntityInfo("AREA_IMAGE", "Foto/Bild", "FOTO", "#495057", default_on=False, area=True),
    EntityInfo("AREA_HANDWRITING", "Handschrift", "HANDSCHRIFT", "#495057", default_on=False, area=True),
]

ENTITIES: dict[str, EntityInfo] = {e.key: e for e in _ENTITIES}

#: Entitäten, die ohne weitere Einstellungen gesucht werden.
DEFAULT_ENTITIES: list[str] = [e.key for e in _ENTITIES if e.default_on]

#: Typen für frei gezogene Bereiche (Reihenfolge = Menü)
AREA_KEYS: list[str] = [e.key for e in _ENTITIES if e.area]


def info(key: str) -> EntityInfo:
    """Liefert Infos zu einem Entitätstyp – auch für unbekannte Typen."""
    return ENTITIES.get(key) or EntityInfo(key, key.replace("_", " ").title(), key, "#868e96")


def all_keys(include_areas: bool = False) -> list[str]:
    """Alle Datenarten der Texterkennung (für Einstellungen und Menüs); Bereichstypen nur auf Wunsch."""
    return [e.key for e in _ENTITIES if include_areas or not e.area]
