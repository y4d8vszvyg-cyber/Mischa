"""
Prototyp: Retention-Video-Pipeline für Versicherungen
======================================================

Ablauf pro Kunde:
  1. Kundendaten einlesen (aus CSV/Bestandssystem-Export)
  2. Personalisiertes Sprechertext-Skript per Claude API erzeugen
  3. Skript an eine Avatar-Video-API übergeben (HeyGen/Synthesia/D-ID) -> Video-URL
  4. Individuellen Landingpage-Link erzeugen + QR-Code generieren
  5. Ergebnis (Video-URL, QR-Code-Datei, Link) für den Druck-Feed exportieren

Benötigte Pakete (lokal installieren):
    pip install anthropic qrcode[pil] requests

Umgebungsvariablen:
    ANTHROPIC_API_KEY   - für die Skript-Generierung
    VIDEO_API_KEY       - für den gewählten Video-Avatar-Anbieter
    VIDEO_API_ENDPOINT  - z.B. https://api.heygen.com/v2/video/generate

Hinweis: Der Video-API-Aufruf ist als Stub für einen Anbieter wie HeyGen/
Synthesia/D-ID ausgelegt. Der exakte Request-Body unterscheidet sich je
Anbieter - siehe deren API-Dokumentation. Hier wird das Interface gezeigt,
das die Pipeline erwartet (Text rein, Video-URL raus).
"""

import os
import csv
import json
import time
import secrets
import dataclasses
from pathlib import Path
from typing import Optional

# --- Optionale Abhängigkeiten: sauber failen, falls nicht installiert ---
try:
    import qrcode
except ImportError:
    qrcode = None

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import requests
except ImportError:
    requests = None


OUTPUT_DIR = Path("./output")
BASE_LANDINGPAGE_URL = "https://retention.beispiel-agentur.de/v"


@dataclasses.dataclass
class Kunde:
    kunden_id: str
    anrede: str
    nachname: str
    vertragsbeginn_jahr: int
    vertragsart: str
    schadensfaelle: int
    neuer_beitrag_rabatt_prozent: int
    vertragsablauf: str  # ISO-Datum, z.B. "2026-09-30"


def lade_kunden_aus_csv(pfad: str) -> list[Kunde]:
    """Liest den Bestandsdaten-Export des Versicherers ein.

    Erwartetes CSV-Format (Spalten):
    kunden_id,anrede,nachname,vertragsbeginn_jahr,vertragsart,
    schadensfaelle,neuer_beitrag_rabatt_prozent,vertragsablauf
    """
    kunden = []
    with open(pfad, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kunden.append(Kunde(
                kunden_id=row["kunden_id"],
                anrede=row["anrede"],
                nachname=row["nachname"],
                vertragsbeginn_jahr=int(row["vertragsbeginn_jahr"]),
                vertragsart=row["vertragsart"],
                schadensfaelle=int(row["schadensfaelle"]),
                neuer_beitrag_rabatt_prozent=int(row["neuer_beitrag_rabatt_prozent"]),
                vertragsablauf=row["vertragsablauf"],
            ))
    return kunden


def erzeuge_sprechertext(kunde: Kunde, jahr_aktuell: int = 2026) -> str:
    """Erzeugt das personalisierte Sprechertext-Skript per Claude API.

    Wichtig: Zahlen (Laufzeit, Rabatt) werden NICHT vom Modell erfunden,
    sondern aus den Kundendaten vorgegeben - das Modell formuliert nur
    den Rahmentext. Das verhindert falsche Zusagen im Video.
    """
    laufzeit_jahre = jahr_aktuell - kunde.vertragsbeginn_jahr

    if anthropic is None:
        raise RuntimeError(
            "Paket 'anthropic' nicht installiert. "
            "Installieren mit: pip install anthropic"
        )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = (
        "Du schreibst kurze, warme Sprechertexte (max. 90 Wörter) für ein "
        "personalisiertes Kundenvideo einer Versicherung. Der Text wird von "
        "einem Avatar vorgelesen. Ton: dankbar, persönlich, nicht aufdringlich. "
        "Nenne NUR die Zahlen, die dir explizit gegeben werden. Erfinde keine "
        "Konditionen, Beträge oder Zusagen. Kein Fließtext-Anhang, nur der "
        "reine Sprechertext."
    )

    user_prompt = (
        f"Kunde: {kunde.anrede} {kunde.nachname}\n"
        f"Vertragsart: {kunde.vertragsart}\n"
        f"Laufzeit bisher: {laufzeit_jahre} Jahre\n"
        f"Schadensfälle in dieser Zeit: {kunde.schadensfaelle}\n"
        f"Rabatt bei Verlängerung: {kunde.neuer_beitrag_rabatt_prozent}%\n"
        f"Vertragsablauf: {kunde.vertragsablauf}\n\n"
        "Schreibe den Sprechertext für das Dankesvideo."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


def pruefe_skript_gegen_quelldaten(skript: str, kunde: Kunde) -> list[str]:
    """Automatisierte Kontrollregel vor Freigabe.

    Einfache Heuristik fürs Prototyping: prüft, ob im Skript genannte
    Prozentzahlen mit den Quelldaten übereinstimmen. In Produktion würde
    man das um eine vollständige Zahlen-/Fakten-Extraktion erweitern.
    """
    warnungen = []
    erwarteter_rabatt = f"{kunde.neuer_beitrag_rabatt_prozent}%"
    if "%" in skript and erwarteter_rabatt not in skript:
        warnungen.append(
            f"Skript enthält eine Prozentangabe, die nicht dem "
            f"hinterlegten Rabatt ({erwarteter_rabatt}) entspricht - manuell prüfen."
        )
    return warnungen


def erzeuge_video(skript: str, kunde: Kunde) -> str:
    """Stub für die Anbindung an eine Avatar-Video-API.

    Beispielhaft im Stil einer HeyGen-artigen API. Der tatsächliche
    Request-Body muss an den gewählten Anbieter angepasst werden
    (HeyGen, Synthesia, D-ID haben je eigene Schemas).
    """
    if requests is None:
        raise RuntimeError("Paket 'requests' nicht installiert.")

    api_key = os.environ.get("VIDEO_API_KEY")
    endpoint = os.environ.get("VIDEO_API_ENDPOINT")
    if not api_key or not endpoint:
        # Für Demo-/Trockenlauf ohne echten Video-API-Zugang:
        return f"https://video-cdn.beispiel.de/mock/{kunde.kunden_id}.mp4"

    payload = {
        "avatar_id": "nordlicht-marken-avatar-01",
        "voice_id": "de-DE-standard-warm",
        "input_text": skript,
        "callback_ref": kunde.kunden_id,
    }
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    job = resp.json()

    # Viele Avatar-APIs sind asynchron: Job wird gestartet, dann gepollt.
    video_url = job.get("video_url")
    job_id = job.get("job_id")
    while not video_url and job_id:
        time.sleep(5)
        status = requests.get(f"{endpoint}/{job_id}", headers=headers, timeout=30).json()
        if status.get("status") == "completed":
            video_url = status["video_url"]
        elif status.get("status") == "failed":
            raise RuntimeError(f"Video-Erzeugung fehlgeschlagen für {kunde.kunden_id}")

    return video_url


def erzeuge_landingpage_link_und_qr(kunde: Kunde) -> tuple[str, Path]:
    """Erzeugt einen nicht erratbaren Token-Link + QR-Code-Datei."""
    token = secrets.token_urlsafe(16)
    link = f"{BASE_LANDINGPAGE_URL}/{token}"

    OUTPUT_DIR.mkdir(exist_ok=True)
    qr_pfad = OUTPUT_DIR / f"qr_{kunde.kunden_id}.png"

    if qrcode is None:
        raise RuntimeError(
            "Paket 'qrcode' nicht installiert. "
            "Installieren mit: pip install qrcode[pil]"
        )

    img = qrcode.make(link)
    img.save(qr_pfad)
    return link, qr_pfad


def verarbeite_kampagne(csv_pfad: str, dry_run: bool = True) -> list[dict]:
    """Verarbeitet alle Kunden einer Kampagne und gibt den Druck-Feed zurück.

    dry_run=True: nutzt Mock-Videos statt echter API-Aufrufe (zum Testen
    der Pipeline-Logik ohne Kosten/Zugangsdaten).
    """
    ergebnisse = []
    kunden = lade_kunden_aus_csv(csv_pfad)

    for kunde in kunden:
        print(f"Verarbeite {kunde.kunden_id} ({kunde.nachname}) ...")

        if dry_run:
            skript = (
                f"Lieber {kunde.anrede} {kunde.nachname}, seit "
                f"{2026 - kunde.vertragsbeginn_jahr} Jahren begleiten wir Sie nun "
                f"[Platzhalter-Skript im Dry-Run-Modus]."
            )
        else:
            skript = erzeuge_sprechertext(kunde)

        warnungen = pruefe_skript_gegen_quelldaten(skript, kunde)
        for w in warnungen:
            print(f"  WARNUNG: {w}")

        video_url = f"https://video-cdn.beispiel.de/mock/{kunde.kunden_id}.mp4" if dry_run else erzeuge_video(skript, kunde)
        link, qr_pfad = erzeuge_landingpage_link_und_qr(kunde)

        ergebnisse.append({
            "kunden_id": kunde.kunden_id,
            "nachname": kunde.nachname,
            "skript": skript,
            "video_url": video_url,
            "landingpage_link": link,
            "qr_code_datei": str(qr_pfad),
            "warnungen": warnungen,
        })

    # Druck-Feed exportieren (Grundlage für den Serienbrief)
    feed_pfad = OUTPUT_DIR / "druck_feed.json"
    with open(feed_pfad, "w", encoding="utf-8") as f:
        json.dump(ergebnisse, f, ensure_ascii=False, indent=2)

    print(f"\nFertig. {len(ergebnisse)} Kunden verarbeitet.")
    print(f"Druck-Feed geschrieben nach: {feed_pfad}")
    return ergebnisse


if __name__ == "__main__":
    # Beispielaufruf mit der beiliegenden Beispiel-CSV im Dry-Run-Modus
    # (kein API-Key nötig, um die Pipeline-Logik zu testen)
    verarbeite_kampagne("beispiel_kunden.csv", dry_run=True)
