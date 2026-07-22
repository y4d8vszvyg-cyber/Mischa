#!/usr/bin/env python3
"""
prototyp_pipeline_v2.py

Prototyp einer Versicherungs-Retention-Pipeline.

Idee:
    Kurz vor Ablauf einer Police bekommt der Kunde einen QR-Code, der zu einem
    personalisierten (KI-)Video fuehrt. Ziel ist, den Kunden zum Verlaengern /
    Bleiben zu bewegen.

Was dieses Skript tut:
    1. beispiel_kunden.csv einlesen (aus DEMSELBEN Ordner wie dieses Skript)
    2. Kunden auswaehlen, deren Police in den naechsten VORLAUF_TAGE Tagen ablaeuft
    3. Pro Kunde ein personalisiertes Anschreiben / Video-Skript erzeugen
       - mit Claude (anthropic), falls ANTHROPIC_API_KEY gesetzt ist
       - sonst mit einer Textvorlage (Fallback), damit der Prototyp immer laeuft
    4. Pro Kunde einen QR-Code (PNG) erzeugen, der auf eine personalisierte
       Video-Landingpage zeigt
    5. Ergebnisse in ./ausgabe/ ablegen + Uebersicht als CSV schreiben

Benoetigt: anthropic, qrcode[pil], requests  (siehe requirements.txt)
Aufruf:    python3 prototyp_pipeline_v2.py
"""

from __future__ import annotations

import csv
import hashlib
import os
import sys
from datetime import date, datetime
from pathlib import Path

import qrcode

# --- Konfiguration -----------------------------------------------------------

SKRIPT_ORDNER = Path(__file__).resolve().parent
EINGABE_CSV = SKRIPT_ORDNER / "beispiel_kunden.csv"
AUSGABE_ORDNER = SKRIPT_ORDNER / "ausgabe"
QR_ORDNER = AUSGABE_ORDNER / "qr_codes"
SKRIPT_TEXT_ORDNER = AUSGABE_ORDNER / "anschreiben"
UEBERSICHT_CSV = AUSGABE_ORDNER / "versand_uebersicht.csv"

# Kunde bekommt Post, wenn die Police in den naechsten N Tagen ablaeuft.
VORLAUF_TAGE = 60

# Basis-URL der personalisierten Video-Landingpage. {token} wird pro Kunde ersetzt.
# In der Produktion: eigene Domain + echte Landingpage/Video-Plattform.
VIDEO_BASIS_URL = "https://retention.example-versicherung.de/v/{token}"

# Name des Absenders fuer die Anschreiben (Platzhalter).
ABSENDER = "Beispiel Versicherung AG"

# Bezugsdatum ("heute").
HEUTE = date(2026, 7, 22)

# Claude-Modell fuer die Skript-Generierung.
CLAUDE_MODELL = "claude-opus-4-8"


# --- Hilfsfunktionen ---------------------------------------------------------

def parse_datum(wert: str):
    try:
        return datetime.strptime(wert.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def video_token(kunde: dict) -> str:
    """Stabiler, nicht erratbarer Token pro Kunde/Police fuer die Video-URL."""
    roh = f"{kunde['kunden_id']}|{kunde['police_nr']}".encode("utf-8")
    return hashlib.sha256(roh).hexdigest()[:16]


def video_url(kunde: dict) -> str:
    return VIDEO_BASIS_URL.format(token=video_token(kunde))


# --- Anschreiben / Video-Skript ---------------------------------------------

def anschreiben_vorlage(kunde: dict, tage_bis_ablauf: int) -> str:
    """Fallback-Text ohne KI, damit die Pipeline immer ein Ergebnis liefert."""
    return (
        f"Guten Tag {kunde['vorname']} {kunde['nachname']},\n\n"
        f"Ihre {kunde['versicherungsart']} (Police {kunde['police_nr']}) "
        f"laeuft in {tage_bis_ablauf} Tagen am {kunde['ablaufdatum']} aus.\n\n"
        f"Wir wuerden Sie gerne weiter begleiten. In einem kurzen, persoenlichen "
        f"Video haben wir zusammengefasst, welche Vorteile Ihr Schutz Ihnen bietet "
        f"und wie einfach die Verlaengerung ist. Scannen Sie dazu einfach den "
        f"beigefuegten QR-Code.\n\n"
        f"Freundliche Gruesse\n{ABSENDER}"
    )


def anschreiben_via_claude(kunde: dict, tage_bis_ablauf: int) -> str | None:
    """
    Erzeugt ein personalisiertes Anschreiben / Video-Skript mit Claude.
    Gibt None zurueck, wenn kein API-Key vorhanden ist oder ein Fehler auftritt
    (dann greift die Vorlage).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    try:
        import anthropic
    except ImportError:
        return None

    system = (
        "Du bist Texter:in einer deutschen Versicherung und schreibst kurze, "
        "warme, seriöse Anschreiben zur Vertragsverlängerung. Kein Druck, keine "
        "übertriebenen Versprechen, DSGVO-konform, per 'Sie'. Maximal 120 Wörter. "
        "Beziehe dich darauf, dass ein persönliches Video per QR-Code bereitsteht."
    )
    prompt = (
        f"Kunde: {kunde['vorname']} {kunde['nachname']}\n"
        f"Produkt: {kunde['versicherungsart']}\n"
        f"Police: {kunde['police_nr']}\n"
        f"Ablaufdatum: {kunde['ablaufdatum']} (in {tage_bis_ablauf} Tagen)\n"
        f"Absender: {ABSENDER}\n\n"
        "Schreibe das Anschreiben. Nur der Brieftext, keine Betreffzeile."
    )

    try:
        client = anthropic.Anthropic()
        antwort = client.messages.create(
            model=CLAUDE_MODELL,
            max_tokens=400,
            output_config={"effort": "low"},  # einfacher, hochvolumiger Task
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in antwort.content if b.type == "text").strip()
    except Exception as e:  # Netzwerk/Auth/Rate-Limit -> Vorlage nutzen
        print(f"  ! Claude nicht verfuegbar ({e.__class__.__name__}), nutze Vorlage",
              file=sys.stderr)
        return None


def erzeuge_anschreiben(kunde: dict, tage_bis_ablauf: int) -> tuple[str, str]:
    """Gibt (text, quelle) zurueck; quelle ist 'claude' oder 'vorlage'."""
    text = anschreiben_via_claude(kunde, tage_bis_ablauf)
    if text:
        return text, "claude"
    return anschreiben_vorlage(kunde, tage_bis_ablauf), "vorlage"


# --- QR-Code -----------------------------------------------------------------

def erzeuge_qr_code(url: str, ziel: Path) -> None:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    bild = qr.make_image(fill_color="black", back_color="white")
    bild.save(ziel)


# --- (Optional) Video-Generierung ueber HTTP ---------------------------------

def video_anfordern(kunde: dict, skript: str) -> str | None:
    """
    STUB: Hier wuerde ein KI-Video-Dienst per HTTP (requests) angesprochen,
    der aus dem Skript ein personalisiertes Video erzeugt und eine URL liefert.

    Absichtlich nicht aktiv, weil kein echter Endpunkt konfiguriert ist.
    Beispielhafter Aufbau:

        import requests
        resp = requests.post(
            os.environ["VIDEO_API_URL"],
            headers={"Authorization": f"Bearer {os.environ['VIDEO_API_KEY']}"},
            json={"script": skript, "kunde_id": kunde["kunden_id"]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["video_url"]
    """
    return None


# --- Pipeline ----------------------------------------------------------------

def lade_kunden(pfad: Path) -> list[dict]:
    if not pfad.exists():
        raise FileNotFoundError(
            f"Eingabedatei nicht gefunden: {pfad}\n"
            f"Bitte '{pfad.name}' in denselben Ordner wie dieses Skript legen."
        )
    with pfad.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def faellige_kunden(kunden: list[dict]) -> list[dict]:
    """Kunden, deren Police in den naechsten VORLAUF_TAGE Tagen ablaeuft."""
    treffer = []
    for kunde in kunden:
        if kunde.get("status", "").strip().lower() == "gekuendigt":
            continue
        ablauf = parse_datum(kunde.get("ablaufdatum", ""))
        if ablauf is None:
            continue
        tage = (ablauf - HEUTE).days
        if 0 <= tage <= VORLAUF_TAGE:
            kunde["_tage_bis_ablauf"] = tage
            treffer.append(kunde)
    treffer.sort(key=lambda k: k["_tage_bis_ablauf"])
    return treffer


def main() -> int:
    print(f"Lese Eingabe: {EINGABE_CSV}")
    try:
        kunden = lade_kunden(EINGABE_CSV)
    except FileNotFoundError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1

    faellig = faellige_kunden(kunden)
    print(f"{len(kunden)} Kunden gelesen, {len(faellig)} faellig "
          f"(Ablauf in <= {VORLAUF_TAGE} Tagen).")

    QR_ORDNER.mkdir(parents=True, exist_ok=True)
    SKRIPT_TEXT_ORDNER.mkdir(parents=True, exist_ok=True)

    uebersicht = []
    for kunde in faellig:
        tage = kunde["_tage_bis_ablauf"]
        url = video_url(kunde)

        text, quelle = erzeuge_anschreiben(kunde, tage)

        basis = f"{kunde['kunden_id']}_{kunde['nachname']}"
        qr_pfad = QR_ORDNER / f"{basis}.png"
        txt_pfad = SKRIPT_TEXT_ORDNER / f"{basis}.txt"

        erzeuge_qr_code(url, qr_pfad)
        txt_pfad.write_text(text + "\n", encoding="utf-8")

        print(f"  #{kunde['kunden_id']} {kunde['vorname']} {kunde['nachname']:10s} "
              f"Ablauf in {tage:3d} T  [{quelle}]  -> {qr_pfad.name}")

        uebersicht.append({
            "kunden_id": kunde["kunden_id"],
            "name": f"{kunde['vorname']} {kunde['nachname']}",
            "email": kunde["email"],
            "versicherungsart": kunde["versicherungsart"],
            "police_nr": kunde["police_nr"],
            "ablaufdatum": kunde["ablaufdatum"],
            "tage_bis_ablauf": tage,
            "video_url": url,
            "qr_datei": str(qr_pfad.relative_to(SKRIPT_ORDNER)),
            "anschreiben_datei": str(txt_pfad.relative_to(SKRIPT_ORDNER)),
            "text_quelle": quelle,
        })

    if uebersicht:
        with UEBERSICHT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(uebersicht[0].keys()))
            writer.writeheader()
            writer.writerows(uebersicht)
        print(f"Uebersicht geschrieben: {UEBERSICHT_CSV}")

    print("Fertig.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
