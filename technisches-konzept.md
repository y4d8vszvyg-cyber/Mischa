# Technisches Konzept: KI-gestützte Retention-Kampagne für Versicherungen

## 1. Grundidee

Ca. 90–120 Tage vor Vertragsablauf erhält der Kunde einen physischen Brief mit persönlichem QR-Code. Der Scan führt auf eine Landingpage mit einem individuell erzeugten Video, das dankt, den Vertragsverlauf würdigt und zur Verlängerung motiviert.

## 2. Datenfluss (High-Level)

```
Versicherer (Bestandssystem)
      │  Export: Vertragsdaten der Kunden mit anstehendem Ablauf
      ▼
[1] Datenübergabe (verschlüsselt, SFTP oder API)
      │
      ▼
[2] Segmentierung & Anreicherung
      │  (Laufzeit, Beitragshistorie, Schadensquote, Zielangebot)
      ▼
[3] Skript-Generierung (LLM)
      │  personalisierter Text je Kunde, auf Basis von Freigabe-Templates
      ▼
[4] Video-Erzeugung (Avatar-API)
      │  Sprachsynthese + Lippensynchronisation auf Marken-Avatar
      ▼
[5] QR-Code & Landingpage-Erzeugung
      │  individueller Link/Zugangscode pro Kunde
      ▼
[6] Druck-Feed an Druckerei
      │  Serienbrief inkl. QR-Code
      ▼
[7] Tracking-Dashboard
      (Öffnungsrate, Video-Completion, Klickrate „Verlängern")
```

## 3. Komponenten im Detail

**a) Datenübergabe**
- Format: strukturierte Datei (CSV/JSON) oder direkte API-Anbindung an das Bestandssystem des Versicherers
- Enthält nur die für die Personalisierung nötigen Felder (Vorname, Anrede, Vertragsbeginn, Vertragsart, ggf. Beitragsentwicklung) – keine Gesundheits- oder Bonitätsdaten
- Übertragung ausschließlich verschlüsselt, im Rahmen eines Auftragsverarbeitungsvertrags (AVV)

**b) Skript-Generierung**
- Ein LLM (z. B. Claude) erzeugt aus freigegebenen Textbausteinen und den Kundendaten ein individuelles Sprechertext-Skript
- Wichtig: keine frei improvisierten Aussagen zu Konditionen – Zahlen/Beträge kommen aus festen Feldern, das LLM formuliert nur den Rahmentext
- Jedes Skript durchläuft eine automatisierte Prüfregel (verbotene Begriffe, Zahlen-Validierung gegen Quelldaten) vor Freigabe

**c) Video-Erzeugung**
- Nutzung einer Avatar-/Video-API (z. B. HeyGen, Synthesia, D-ID) statt Eigenentwicklung eines Video-Modells – deutlich schneller marktreif
- Ein Marken-Avatar (Gesicht des Versicherers oder neutraler Sprecher) wird einmalig produziert, danach pro Kunde nur der Text ausgetauscht
- Batch-Verarbeitung: Videos werden vorab für die gesamte Kampagne erzeugt, nicht live bei Zugriff

**d) Landingpage & QR-Code**
- Pro Kunde ein eindeutiger, nicht erratbarer Link (Token), keine fortlaufenden IDs in der URL
- Zugriff idealerweise mit zusätzlichem Faktor (z. B. Geburtsdatum-Abfrage) je nach Schutzbedarf der angezeigten Daten
- Landingpage responsive, für Smartphone-Nutzung nach QR-Scan optimiert

**e) Druck-Integration**
- Automatisierter Feed an die Druckerei des Versicherers (analog zum Pilotprojekt der Versicherungskammer Bayern: Datensatz wird um QR-Code/Link erweitert und zu festem Termin übermittelt)

**f) Tracking**
- Dashboard mit: Anzahl versendeter Briefe, Scan-/Öffnungsrate, Video-Completion-Rate, Klicks auf „Verlängern", tatsächliche Verlängerungsquote
- A/B-Testing-fähig: personalisiertes Video vs. Standardbrief als Kontrollgruppe

## 4. Datenschutz & Compliance (zentral, nicht nachgelagert)

- AVV mit jedem Versicherer vor Projektstart
- Datensparsamkeit: nur Felder verarbeiten, die für die Personalisierung nötig sind
- Löschkonzept: Kundendaten und generierte Videos nach Kampagnenende/Fristablauf löschen
- Keine Weitergabe der Daten an Subunternehmer (Avatar-API-Anbieter) ohne eigene AVV-Kette – bei Nutzung von US-Anbietern (HeyGen, Synthesia) EU-Datentransfer-Mechanismus prüfen (SCCs)
- Kennzeichnung, dass es sich um ein KI-generiertes Video handelt (Transparenzpflicht, auch mit Blick auf den EU AI Act)
- Abstimmung mit dem Datenschutzbeauftragten des Versicherers vor Pilotstart

## 5. Empfohlener Tech-Stack für den Start (Agentur-Modell)

| Baustein | Empfehlung | Begründung |
|---|---|---|
| Skript-Generierung | Claude API | zuverlässige, kontrollierbare Textgenerierung mit Templates |
| Video-Avatar | HeyGen oder Synthesia (API) | marktreif, keine eigene Video-KI nötig |
| Landingpage | einfaches Next.js/HTML-Template, pro Kampagne angepasst | schnell, kein Overhead einer vollen Plattform |
| QR-Code-Erzeugung | Standard-Bibliothek (z. B. `qrcode` in Python) | trivial, keine externe Abhängigkeit |
| Tracking | leichtgewichtiges Dashboard (z. B. Metabase auf eigener DB) | ausreichend für Agentur-Reporting, kein Eigenbau nötig |
| Datenhaltung | EU-Hosting (z. B. AWS eu-central-1) | DSGVO-Anforderungen der Versicherer |

Für den Agentur-Start lohnt es sich **nicht**, alles selbst zu bauen – die Wertschöpfung liegt in Konzept, Personalisierungslogik, Compliance-Sauberkeit und Projektumsetzung, nicht im Nachbau bestehender Video-APIs.
