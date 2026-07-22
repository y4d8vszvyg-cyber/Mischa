#!/usr/bin/env python3
"""
prototyp_pipeline_v2.py

Prototyp einer einfachen Kundendaten-Pipeline.

Ablauf:
    1. CSV einlesen (beispiel_kunden.csv aus DEMSELBEN Ordner wie dieses Skript)
    2. Datensaetze bereinigen und validieren
    3. Einfaches Lead-Scoring + Segmentierung berechnen
    4. Ergebnis als aufbereitete_kunden.csv schreiben + Zusammenfassung ausgeben

Benoetigt nur die Python-Standardbibliothek (keine Installation noetig).

Aufruf:
    python3 prototyp_pipeline_v2.py
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

# --- Konfiguration -----------------------------------------------------------

# Dateien werden IMMER relativ zum Speicherort dieses Skripts gesucht,
# damit Skript und CSV im selben Ordner liegen koennen.
SKRIPT_ORDNER = Path(__file__).resolve().parent
EINGABE_CSV = SKRIPT_ORDNER / "beispiel_kunden.csv"
AUSGABE_CSV = SKRIPT_ORDNER / "aufbereitete_kunden.csv"

# Einfache, aber praktikable E-Mail-Pruefung.
EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Bezugsdatum fuer die "Aktualitaet des letzten Kontakts".
HEUTE = date(2026, 7, 22)


# --- Hilfsfunktionen ---------------------------------------------------------

def bereinige_text(wert: str) -> str:
    """Entfernt umschliessende Leerzeichen und doppelte Innen-Leerzeichen."""
    if wert is None:
        return ""
    return re.sub(r"\s+", " ", wert).strip()


def normalisiere_email(wert: str) -> str:
    """E-Mail auf Kleinbuchstaben normalisieren und getrimmt zurueckgeben."""
    return bereinige_text(wert).lower()


def ist_gueltige_email(wert: str) -> bool:
    return bool(EMAIL_MUSTER.match(wert))


def parse_umsatz(wert: str) -> float:
    """Umsatz als Zahl parsen; ungueltige/leere Werte werden zu 0.0."""
    wert = bereinige_text(wert).replace(".", "").replace(",", ".")
    try:
        return max(0.0, float(wert))
    except ValueError:
        return 0.0


def parse_datum(wert: str):
    """Datum im Format YYYY-MM-TT parsen; bei Fehler None."""
    wert = bereinige_text(wert)
    try:
        return datetime.strptime(wert, "%Y-%m-%d").date()
    except ValueError:
        return None


def tage_seit(kontakt: date | None) -> int | None:
    if kontakt is None:
        return None
    return (HEUTE - kontakt).days


# --- Scoring / Segmentierung -------------------------------------------------

def berechne_score(umsatz: float, tage: int | None, status: str,
                   email_ok: bool) -> int:
    """
    Sehr einfaches Lead-Scoring (0-100).

    - Umsatz: bis zu 50 Punkte (>= 200.000 EUR = voll)
    - Aktualitaet: bis zu 30 Punkte (Kontakt in den letzten 30 Tagen = voll)
    - Status aktiv: 15 Punkte
    - Gueltige E-Mail (erreichbar): 5 Punkte
    """
    score = 0.0

    # Umsatzanteil
    score += min(umsatz / 200_000.0, 1.0) * 50

    # Aktualitaetsanteil (linear ueber ein Jahr abfallend)
    if tage is not None:
        aktualitaet = max(0.0, 1.0 - tage / 365.0)
        score += aktualitaet * 30

    # Status
    if status == "aktiv":
        score += 15

    # Erreichbarkeit
    if email_ok:
        score += 5

    return round(min(score, 100.0))


def segment_fuer_score(score: int) -> str:
    if score >= 70:
        return "A - Hochwertig"
    if score >= 40:
        return "B - Mittel"
    return "C - Niedrig"


# --- Pipeline ----------------------------------------------------------------

def lade_kunden(pfad: Path) -> list[dict]:
    if not pfad.exists():
        raise FileNotFoundError(
            f"Eingabedatei nicht gefunden: {pfad}\n"
            f"Bitte '{pfad.name}' in denselben Ordner wie dieses Skript legen."
        )
    with pfad.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def verarbeite(rohdaten: list[dict]) -> list[dict]:
    ergebnis = []
    for zeile in rohdaten:
        email = normalisiere_email(zeile.get("email", ""))
        email_ok = ist_gueltige_email(email)
        umsatz = parse_umsatz(zeile.get("umsatz_eur", ""))
        kontakt = parse_datum(zeile.get("letzter_kontakt", ""))
        tage = tage_seit(kontakt)
        status = bereinige_text(zeile.get("status", "")).lower()

        score = berechne_score(umsatz, tage, status, email_ok)

        # Datenqualitaets-Hinweise sammeln
        hinweise = []
        if not email:
            hinweise.append("email fehlt")
        elif not email_ok:
            hinweise.append("email ungueltig")
        if not bereinige_text(zeile.get("firma", "")):
            hinweise.append("firma fehlt")
        if kontakt is None:
            hinweise.append("kontaktdatum ungueltig")

        ergebnis.append({
            "kunden_id": bereinige_text(zeile.get("kunden_id", "")),
            "vorname": bereinige_text(zeile.get("vorname", "")),
            "nachname": bereinige_text(zeile.get("nachname", "")),
            "email": email,
            "email_gueltig": "ja" if email_ok else "nein",
            "firma": bereinige_text(zeile.get("firma", "")),
            "branche": bereinige_text(zeile.get("branche", "")),
            "land": bereinige_text(zeile.get("land", "")).upper(),
            "umsatz_eur": f"{umsatz:.2f}",
            "tage_seit_kontakt": "" if tage is None else str(tage),
            "status": status,
            "score": str(score),
            "segment": segment_fuer_score(score),
            "hinweise": "; ".join(hinweise),
        })

    # Nach Score absteigend sortieren (beste Leads zuerst)
    ergebnis.sort(key=lambda d: int(d["score"]), reverse=True)
    return ergebnis


def schreibe_ergebnis(pfad: Path, daten: list[dict]) -> None:
    if not daten:
        return
    with pfad.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(daten[0].keys()))
        writer.writeheader()
        writer.writerows(daten)


def drucke_zusammenfassung(daten: list[dict]) -> None:
    gesamt = len(daten)
    segmente = {"A - Hochwertig": 0, "B - Mittel": 0, "C - Niedrig": 0}
    mit_problemen = 0
    for d in daten:
        segmente[d["segment"]] = segmente.get(d["segment"], 0) + 1
        if d["hinweise"]:
            mit_problemen += 1

    print("=" * 52)
    print("  ZUSAMMENFASSUNG")
    print("=" * 52)
    print(f"  Verarbeitete Kunden : {gesamt}")
    for name, anzahl in segmente.items():
        print(f"  Segment {name:15s}: {anzahl}")
    print(f"  Mit Datenhinweisen  : {mit_problemen}")
    print("-" * 52)
    print("  Top 3 Leads:")
    for d in daten[:3]:
        print(f"    #{d['kunden_id']:>5} {d['vorname']} {d['nachname']:12s} "
              f"Score {d['score']:>3}  ({d['segment']})")
    print("=" * 52)


def main() -> int:
    print(f"Lese Eingabe: {EINGABE_CSV}")
    try:
        rohdaten = lade_kunden(EINGABE_CSV)
    except FileNotFoundError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1

    daten = verarbeite(rohdaten)
    schreibe_ergebnis(AUSGABE_CSV, daten)
    print(f"Schreibe Ausgabe: {AUSGABE_CSV}")
    drucke_zusammenfassung(daten)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
