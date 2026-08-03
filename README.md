# Mischa
ronkinco

## Snack – Essen-Tracker

`food-tracker.html` ist eine eigenständige Website zum schnellen, unkomplizierten
Tracken von Mahlzeiten.

**Nutzung:** Datei einfach im Browser öffnen (Doppelklick) – fertig. Keine
Installation, kein Login, kein Server.

**Funktionen:**
- **KI-Freitexteingabe:** Einfach tippen, was du gegessen hast – z. B.
  „eine kleine Portion Nudeln", „2 Eier", „großes Glas Cola" oder „150 g Reis".
  Menge, Portionsgröße und Lebensmittel werden erkannt und die Kalorien
  automatisch geschätzt (rund 100 gängige Lebensmittel, funktioniert offline,
  ohne Konto).
- **Barcode-Scanner:** Strichcode verpackter Produkte scannen (Kamera) oder die
  Nummer manuell eingeben – die Nährwerte kommen live aus der freien
  [Open-Food-Facts](https://world.openfoodfacts.org)-Datenbank.
- **Nährwerte:** Neben Kalorien werden auch **Eiweiß, Kohlenhydrate und Fett**
  erfasst – als Tagessumme mit Energie-Verteilungsbalken, pro Mahlzeit und pro
  Eintrag ablesbar. Kommen automatisch aus dem Schätzer und dem Barcode-Import;
  bei manueller Eingabe optional selbst eintragbar.
- Mahlzeiten nach Frühstück / Mittag / Abend / Snack gruppiert
- Tagesziel mit Fortschritts-Ring und „verbleibenden" Kalorien
- Kalorien eines Eintrags per Tipp nachträglich anpassen
- Zwischen Tagen blättern; „Zuletzt gegessen"-Chips zum Ein-Tipp-Nachtragen
- Alles wird lokal im Browser gespeichert (localStorage) – keine Cloud

Optimiert für's Handy. Der Freitext-Schätzer läuft komplett offline.

**Hinweis zum Barcode-Scanner:** Der Live-Kamera-Scan braucht eine sichere
Verbindung (https). Beim reinen Öffnen per Doppelklick (`file://`) sperren
Browser die Kamera – dann einfach die Barcode-Nummer manuell eintippen (das
funktioniert überall). Für den Kamera-Scan die Datei über https bereitstellen
(z. B. GitHub Pages).
