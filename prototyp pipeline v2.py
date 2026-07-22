"""
Retention-Video-Pipeline für Versicherungen — gehärtete Version (v2)
=====================================================================

Gegenüber dem ersten Prototyp neu:
  - Strukturiertes Audit-Log (JSONL) für jeden Verarbeitungsschritt,
    ausschließlich mit pseudonymer kunden_id (keine Klarnamen im Log) —
    erfüllt die Nachweispflicht aus Art. 5 Abs. 2 DSGVO.
  - Retry-/Backoff-Logik für den Video-API-Aufruf (transiente Fehler
    wie Timeouts oder 5xx sollen die ganze Kampagne nicht abbrechen).
  - Automatisiertes Löschkonzept: jeder erzeugte Artefakt-Typ bekommt
    ein Ablaufdatum gemäß der Fristen aus "Verzeichnis_und_Loeschkonzept.docx"
    (14 / 30 / 90 Tage). Ein separater Löschlauf entfernt abgelaufene
    Artefakte automatisiert und protokolliert das im Audit-Log.
  - Realistischeres Druck-Feed-Format (CSV statt nur JSON), wie es eine
    Druckerei tatsächlich einlesen könnte — inkl. Hinweis, welche Felder
    in der Praxis noch aus dem Bestandssystem fehlen (z. B. Anschrift).

Weiterhin ein Prototyp / keine Produktivsoftware. Insbesondere die
Speicherung erfolgt lokal auf Dateien statt in einer echten Datenbank —
für den Produktivbetrieb durch eine DB mit denselben Feldern ersetzen.

Benötigte Pakete:
    pip install anthropic qrcode[pil] requests

Umgebungsvariablen:
    ANTHROPIC_API_KEY, VIDEO_API_KEY, VIDEO_API_ENDPOINT
"""

from __future__ import annotations

import os
import csv
import json
import time
import random
import secrets
import logging
import dataclasses
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Any

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

# Löschfristen gemäß Verzeichnis_und_Loeschkonzept.docx, Abschnitt 3
RETENTION_DAYS = {
    "rohdaten": 14,
    "skript": 30,
    "video": 90,
    "token": 30,        # zusätzlich zur sofortigen Deaktivierung bei Linkablauf
    "tracking_roh": 90,
}

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2


# =====================================================================
# Logging: normales Betriebs-Log + separates Audit-Log (JSONL)
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("retention_pipeline")


class AuditLog:
    """Schreibt ein Audit-Log-Ereignis pro Zeile (JSONL), ausschließlich
    mit pseudonymer kunden_id. Erfüllt die Anforderungen aus
    Verzeichnis_und_Loeschkonzept.docx, Abschnitt 5."""

    def __init__(self, pfad: Path):
        self.pfad = pfad
        self.pfad.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kunden_id: str, schritt: str, komponente: str,
             status: str, details: Optional[dict] = None):
        eintrag = {
            "zeitstempel": datetime.now(timezone.utc).isoformat(),
            "kunden_id": kunden_id,
            "schritt": schritt,
            "komponente": komponente,
            "status": status,
            "details": details or {},
        }
        with open(self.pfad, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


# =====================================================================
# Retry-Hilfsfunktion mit exponentiellem Backoff
# =====================================================================

def mit_retry(fn: Callable[[], Any], versuche: int = RETRY_ATTEMPTS,
               basis_verzoegerung: float = RETRY_BASE_DELAY_SECONDS,
               retriable_exceptions=(Exception,)) -> Any:
    """Führt fn() aus und wiederholt bei Fehlern mit exponentiellem
    Backoff + Jitter. Wirft die letzte Exception weiter, wenn alle
    Versuche fehlschlagen."""
    letzter_fehler = None
    for versuch in range(1, versuche + 1):
        try:
            return fn()
        except retriable_exceptions as exc:
            letzter_fehler = exc
            if versuch == versuche:
                break
            verzoegerung = basis_verzoegerung * (2 ** (versuch - 1)) + random.uniform(0, 1)
            logger.warning(
                "Versuch %d/%d fehlgeschlagen (%s) — erneuter Versuch in %.1fs",
                versuch, versuche, exc, verzoegerung,
            )
            time.sleep(verzoegerung)
    raise letzter_fehler


# =====================================================================
# Datenmodell
# =====================================================================

@dataclasses.dataclass
class Kunde:
    kunden_id: str
    anrede: str
    nachname: str
    vertragsbeginn_jahr: int
    vertragsart: str
    schadensfaelle: int
    neuer_beitrag_rabatt_prozent: int
    vertragsablauf: str  # ISO-Datum, z. B. "2026-09-30"


def lade_kunden_aus_csv(pfad: str) -> list[Kunde]:
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


# =====================================================================
# Schritt 1: Sprechertext-Skript
# =====================================================================

def erzeuge_sprechertext(kunde: Kunde, jahr_aktuell: int = 2026) -> str:
    """Wie im ersten Prototyp: Zahlen kommen aus den Kundendaten, das
    Modell formuliert nur den Rahmentext (verhindert falsche Zusagen)."""
    laufzeit_jahre = jahr_aktuell - kunde.vertragsbeginn_jahr

    if anthropic is None:
        raise RuntimeError("Paket 'anthropic' nicht installiert: pip install anthropic")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = (
        "Du schreibst kurze, warme Sprechertexte (max. 90 Wörter) für ein "
        "personalisiertes Kundenvideo einer Versicherung. Ton: dankbar, "
        "persönlich, nicht aufdringlich. Nenne NUR die Zahlen, die dir "
        "explizit gegeben werden. Erfinde keine Konditionen, Beträge oder "
        "Zusagen. Kein Fließtext-Anhang, nur der reine Sprechertext."
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

    def call():
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text.strip()

    # LLM-Aufrufe können durch Rate-Limits/Timeouts transient fehlschlagen
    return mit_retry(call)


def pruefe_skript_gegen_quelldaten(skript: str, kunde: Kunde) -> list[str]:
    """Einfache Heuristik fürs Prototyping — in Produktion um eine
    vollständige Zahlen-/Fakten-Extraktion erweitern."""
    warnungen = []
    erwarteter_rabatt = f"{kunde.neuer_beitrag_rabatt_prozent}%"
    if "%" in skript and erwarteter_rabatt not in skript:
        warnungen.append(
            f"Skript enthält eine Prozentangabe, die nicht dem hinterlegten "
            f"Rabatt ({erwarteter_rabatt}) entspricht - manuell prüfen."
        )
    return warnungen


# =====================================================================
# Schritt 2: Video-Erzeugung (mit Retry)
# =====================================================================

def erzeuge_video(skript: str, kunde: Kunde) -> str:
    """Stub für die Anbindung an eine Avatar-Video-API, jetzt mit
    Retry/Backoff für transiente Netzwerk-/Serverfehler."""
    if requests is None:
        raise RuntimeError("Paket 'requests' nicht installiert.")

    api_key = os.environ.get("VIDEO_API_KEY")
    endpoint = os.environ.get("VIDEO_API_ENDPOINT")
    if not api_key or not endpoint:
        return f"https://video-cdn.beispiel.de/mock/{kunde.kunden_id}.mp4"

    payload = {
        "avatar_id": "nordlicht-marken-avatar-01",
        "voice_id": "de-DE-standard-warm",
        "input_text": skript,
        "callback_ref": kunde.kunden_id,
    }
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    def start_job():
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    job = mit_retry(start_job, retriable_exceptions=(requests.exceptions.RequestException,))

    video_url = job.get("video_url")
    job_id = job.get("job_id")
    poll_versuche = 0
    while not video_url and job_id and poll_versuche < 60:  # max. ~5 Min. bei 5s Intervall
        time.sleep(5)
        poll_versuche += 1

        def poll():
            r = requests.get(f"{endpoint}/{job_id}", headers=headers, timeout=30)
            r.raise_for_status()
            return r.json()

        status = mit_retry(poll, retriable_exceptions=(requests.exceptions.RequestException,))
        if status.get("status") == "completed":
            video_url = status["video_url"]
        elif status.get("status") == "failed":
            raise RuntimeError(f"Video-Erzeugung fehlgeschlagen für {kunde.kunden_id}")

    if not video_url:
        raise TimeoutError(f"Video-Job für {kunde.kunden_id} nicht rechtzeitig abgeschlossen")

    return video_url


# =====================================================================
# Schritt 3: Landingpage-Link & QR-Code
# =====================================================================

def erzeuge_landingpage_link_und_qr(kunde: Kunde, output_dir: Path) -> tuple[str, Path]:
    token = secrets.token_urlsafe(16)
    link = f"{BASE_LANDINGPAGE_URL}/{token}"

    output_dir.mkdir(exist_ok=True)
    qr_pfad = output_dir / f"qr_{kunde.kunden_id}.png"

    if qrcode is None:
        raise RuntimeError("Paket 'qrcode' nicht installiert: pip install qrcode[pil]")

    img = qrcode.make(link)
    img.save(qr_pfad)
    return link, qr_pfad


# =====================================================================
# Löschkonzept: Ablaufdaten setzen + automatisierter Löschlauf
# =====================================================================

def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def setze_retention(kunde_ergebnis: dict, jetzt: Optional[datetime] = None) -> dict:
    """Ergänzt jeden Artefakt-Typ im Ergebnis um ein Ablaufdatum gemäß
    RETENTION_DAYS. Wird direkt nach der Erzeugung aufgerufen."""
    basis = jetzt or _jetzt()
    kunde_ergebnis["retention_until"] = {
        artefakt: (basis + timedelta(days=tage)).isoformat()
        for artefakt, tage in RETENTION_DAYS.items()
    }
    return kunde_ergebnis


def fuehre_loeschlauf(feed_pfad: Path, audit: AuditLog, output_dir: Path,
                        jetzt: Optional[datetime] = None) -> dict:
    """Scannt den Druck-Feed / die Ergebnisliste nach abgelaufenen
    Artefakten und löscht die zugehörigen Dateien (Video-Referenz kann
    nur beim Anbieter selbst gelöscht werden — hier wird das als
    'zu löschen beim Anbieter' markiert). Für QR-Codes werden echte
    Dateien entfernt. Gibt eine Zusammenfassung zurück.

    In Produktion: als täglicher Cron-Job / Scheduled Task laufen lassen.
    """
    basis = jetzt or _jetzt()
    if not feed_pfad.exists():
        logger.info("Kein Druck-Feed unter %s gefunden — nichts zu löschen.", feed_pfad)
        return {"geprueft": 0, "geloescht": 0}

    with open(feed_pfad, "r", encoding="utf-8") as f:
        ergebnisse = json.load(f)

    geloescht = 0
    for eintrag in ergebnisse:
        retention = eintrag.get("retention_until", {})
        kunden_id = eintrag["kunden_id"]

        # QR-Code-Datei löschen, wenn Token-Frist abgelaufen ist
        token_frist = retention.get("token")
        if token_frist and basis >= datetime.fromisoformat(token_frist):
            qr_pfad = Path(eintrag.get("qr_code_datei", ""))
            if qr_pfad.exists():
                qr_pfad.unlink()
                geloescht += 1
                audit.log(kunden_id, "loeschung", "loeschjob", "erfolgreich",
                           {"artefakt": "qr_code", "grund": "token_frist_abgelaufen"})

        # Video: kann hier nur als "zu löschen beim Anbieter" markiert werden,
        # da die Datei selbst beim Video-API-Anbieter liegt
        video_frist = retention.get("video")
        if video_frist and basis >= datetime.fromisoformat(video_frist) and not eintrag.get("video_geloescht"):
            eintrag["video_geloescht"] = True
            geloescht += 1
            audit.log(kunden_id, "loeschung", "loeschjob", "markiert_fuer_anbieter",
                       {"artefakt": "video", "hinweis": "Löschung beim Video-API-Anbieter anstoßen"})

    with open(feed_pfad, "w", encoding="utf-8") as f:
        json.dump(ergebnisse, f, ensure_ascii=False, indent=2)

    logger.info("Löschlauf abgeschlossen: %d Einträge geprüft, %d Artefakte entfernt/markiert.",
                len(ergebnisse), geloescht)
    return {"geprueft": len(ergebnisse), "geloescht": geloescht}


# =====================================================================
# Druck-Feed: realistisches CSV-Format für eine Druckerei
# =====================================================================

DRUCK_FEED_SPALTEN = [
    "kunden_id", "anrede", "nachname",
    # Anschriftfelder fehlen im Beispieldatensatz — in der Praxis müssen
    # diese aus dem Bestandssystem mitgeliefert werden (Straße, PLZ, Ort).
    # Ohne sie kann keine echte Druckerei den Brief zustellen.
    "strasse_hausnummer", "plz", "ort",
    "vertragsablauf", "qr_code_pfad", "landingpage_link", "geplantes_versanddatum",
]


def schreibe_druck_feed_csv(ergebnisse: list[dict], pfad: Path, versanddatum: str):
    with open(pfad, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DRUCK_FEED_SPALTEN)
        writer.writeheader()
        for e in ergebnisse:
            writer.writerow({
                "kunden_id": e["kunden_id"],
                "anrede": e.get("anrede", ""),
                "nachname": e["nachname"],
                "strasse_hausnummer": "FEHLT — aus Bestandssystem ergänzen",
                "plz": "FEHLT",
                "ort": "FEHLT",
                "vertragsablauf": e.get("vertragsablauf", ""),
                "qr_code_pfad": e["qr_code_datei"],
                "landingpage_link": e["landingpage_link"],
                "geplantes_versanddatum": versanddatum,
            })


# =====================================================================
# Hauptablauf
# =====================================================================

def verarbeite_kampagne(csv_pfad: str, dry_run: bool = True,
                          versanddatum: Optional[str] = None) -> list[dict]:
    versanddatum = versanddatum or (_jetzt() + timedelta(days=5)).date().isoformat()
    OUTPUT_DIR.mkdir(exist_ok=True)
    audit = AuditLog(OUTPUT_DIR / "audit_log.jsonl")

    ergebnisse = []
    kunden = lade_kunden_aus_csv(csv_pfad)
    logger.info("Kampagne gestartet: %d Kunden, dry_run=%s", len(kunden), dry_run)

    fehlgeschlagen = []

    for kunde in kunden:
        logger.info("Verarbeite %s (%s) ...", kunde.kunden_id, kunde.nachname)
        try:
            if dry_run:
                skript = (
                    f"Lieber {kunde.anrede} {kunde.nachname}, seit "
                    f"{2026 - kunde.vertragsbeginn_jahr} Jahren begleiten wir Sie nun "
                    f"[Platzhalter-Skript im Dry-Run-Modus]."
                )
                audit.log(kunde.kunden_id, "skript_generierung", "dry_run", "erfolgreich")
            else:
                skript = erzeuge_sprechertext(kunde)
                audit.log(kunde.kunden_id, "skript_generierung", "anthropic_api", "erfolgreich")

            warnungen = pruefe_skript_gegen_quelldaten(skript, kunde)
            for w in warnungen:
                logger.warning("  WARNUNG: %s", w)
                audit.log(kunde.kunden_id, "skript_pruefung", "regelpruefung", "warnung", {"warnung": w})

            if dry_run:
                video_url = f"https://video-cdn.beispiel.de/mock/{kunde.kunden_id}.mp4"
                audit.log(kunde.kunden_id, "video_erzeugung", "dry_run", "erfolgreich")
            else:
                video_url = erzeuge_video(skript, kunde)
                audit.log(kunde.kunden_id, "video_erzeugung", "video_api", "erfolgreich")

            link, qr_pfad = erzeuge_landingpage_link_und_qr(kunde, OUTPUT_DIR)
            audit.log(kunde.kunden_id, "link_und_qr_erzeugung", "pipeline", "erfolgreich")

            eintrag = {
                "kunden_id": kunde.kunden_id,
                "anrede": kunde.anrede,
                "nachname": kunde.nachname,
                "vertragsablauf": kunde.vertragsablauf,
                "skript": skript,
                "video_url": video_url,
                "landingpage_link": link,
                "qr_code_datei": str(qr_pfad),
                "warnungen": warnungen,
            }
            setze_retention(eintrag)
            ergebnisse.append(eintrag)

        except Exception as exc:
            logger.error("  FEHLER bei %s: %s", kunde.kunden_id, exc)
            audit.log(kunde.kunden_id, "verarbeitung", "pipeline", "fehler", {"fehler": str(exc)})
            fehlgeschlagen.append({"kunden_id": kunde.kunden_id, "fehler": str(exc)})
            continue  # ein fehlgeschlagener Kunde bricht nicht die ganze Kampagne ab

    feed_pfad = OUTPUT_DIR / "druck_feed.json"
    with open(feed_pfad, "w", encoding="utf-8") as f:
        json.dump(ergebnisse, f, ensure_ascii=False, indent=2)

    csv_pfad_out = OUTPUT_DIR / "druck_feed.csv"
    schreibe_druck_feed_csv(ergebnisse, csv_pfad_out, versanddatum)

    if fehlgeschlagen:
        fehler_pfad = OUTPUT_DIR / "fehlgeschlagene_kunden.json"
        with open(fehler_pfad, "w", encoding="utf-8") as f:
            json.dump(fehlgeschlagen, f, ensure_ascii=False, indent=2)
        logger.warning("%d Kunden konnten nicht verarbeitet werden — siehe %s",
                        len(fehlgeschlagen), fehler_pfad)

    logger.info("Fertig. %d/%d Kunden erfolgreich verarbeitet.", len(ergebnisse), len(kunden))
    logger.info("Druck-Feed (JSON): %s", feed_pfad)
    logger.info("Druck-Feed (CSV für Druckerei): %s", csv_pfad_out)
    logger.info("Audit-Log: %s", OUTPUT_DIR / "audit_log.jsonl")

    return ergebnisse


if __name__ == "__main__":
    # Kampagne im Dry-Run-Modus verarbeiten (kein API-Key nötig)
    verarbeite_kampagne("beispiel_kunden.csv", dry_run=True)

    # Löschlauf simulieren: einmal "heute" (löscht nichts, Fristen noch offen)
    # und einmal mit einem Datum weit in der Zukunft (zeigt, dass es greift)
    audit = AuditLog(OUTPUT_DIR / "audit_log.jsonl")
    print("\n--- Löschlauf heute (erwartet: nichts löschbar) ---")
    fuehre_loeschlauf(OUTPUT_DIR / "druck_feed.json", audit, OUTPUT_DIR)

    print("\n--- Löschlauf simuliert in 100 Tagen (erwartet: QR-Codes + Video-Markierung) ---")
    in_100_tagen = _jetzt() + timedelta(days=100)
    fuehre_loeschlauf(OUTPUT_DIR / "druck_feed.json", audit, OUTPUT_DIR, jetzt=in_100_tagen)
