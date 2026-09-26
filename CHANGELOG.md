# Änderungen

## 0.3.0 – 26.09.2026

**Neu**
- **Bereiche frei schwärzen** (PDF): Rahmen in der Seitenansicht ziehen → „Bereich schwärzen als“
  Unterschrift, Foto/Bild, Handschrift oder Bereich – auch ohne Text darunter. Im Bereich werden beim Export
  Bildpunkte, Text und Vektorgrafik (z. B. gezeichnete Unterschriften) echt entfernt; Linien und Flächen, die
  nur hineinragen, bleiben erhalten. Bereiche erscheinen in der Fundliste, sind rückgängig machbar, werden im
  Arbeitsstand gespeichert (nur Koordinaten) und im Protokoll gezählt (Spalte „Bereiche“).
- Neues Beispiel `unterschrift.pdf` (Vektor-Unterschrift, Passfoto, handschriftliche Notiz).

## 0.2.0 – 26.09.2026

**Neu**
- **Texterkennung (OCR)** für gescannte PDF-Seiten ohne Textebene (Rückfallebene, z. B. eingescannte
  Mail-Anhänge). Automatisch aktiv, abschaltbar. Deutliche Kennzeichnung (Hinweis, Seitenrahmen, Fundliste),
  Bestätigung vor dem Export; Dateien mit OCR werden nie automatisch exportiert.
- **PDF-Bereinigung:** Kommentare, Markierungen und Lesezeichen werden entfernt, Formularfelder in festen
  Inhalt umgewandelt (Werte werden geprüft und geschwärzt). Kontrolle nach dem Export prüft zusätzlich
  Kommentare, Formularfelder, Lesezeichen, Metadaten, Links und Anhänge.
- Modus **„Gründlich“ ist Standard** (IT kann per `defaults.json` „Schnell“ vorgeben).
- **Zoom:** Seitenbreite (passt sich an), Ganze Seite, feste Werte (100 % = Papiergröße); Wahl bleibt erhalten.
- **Über pii-redact** und Hilfe-Kapitel 15 mit allen wesentlichen Open-Source-Komponenten;
  `LIZENZEN.txt` und `LIZENZTEXTE.txt` im Programmordner.
- Neue Beispiele: `kommentare.pdf`, `formular.pdf`, `scan.pdf`, `mail_mit_scan.pdf`.

**Behoben**
- PDF wurde nach dem Wechsel von einem Text-Dokument oft nicht angezeigt.
- Ausgefüllte Formularfelder verloren beim Export ihren Inhalt.

**Intern**
- Arbeitsstand der Ordner-Bearbeitung prüft, ob gespeicherte Funde noch zum Text passen.
- `build.bat` aktualisiert die Build-Umgebung automatisch bei geänderter Paketliste.

## 0.1.0 – 26.09.2026

Erste Fassung: Erkennung (Presidio, spaCy, optional Transformer-Modell als ONNX), PDF-Schwärzung,
TXT/Markdown, Nachbearbeitung, Ordner-Bearbeitung mit Protokoll, Kommandozeile, Anwenderhilfe,
PyInstaller-Build und MSI für SCCM.
