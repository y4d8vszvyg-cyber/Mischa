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
    3. Pro Kunde ein personalisiertes Anschreiben / Video-Skript aus einer
       Textvorlage erzeugen
    4. Pro Kunde einen QR-Code (PNG) erzeugen, der auf eine personalisierte
       Video-Landingpage zeigt
    5. Ergebnisse in ./ausgabe/ ablegen + Uebersicht als CSV schreiben

Benoetigt: qrcode[pil], requests  (siehe requirements.txt)
Aufruf:    python3 prototyp_pipeline_v2.py
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import qrcode
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# --- Konfiguration -----------------------------------------------------------

SKRIPT_ORDNER = Path(__file__).resolve().parent
EINGABE_CSV = SKRIPT_ORDNER / "beispiel_kunden.csv"
AUSGABE_ORDNER = SKRIPT_ORDNER / "ausgabe"
QR_ORDNER = AUSGABE_ORDNER / "qr_codes"
SKRIPT_TEXT_ORDNER = AUSGABE_ORDNER / "anschreiben"
UEBERSICHT_CSV = AUSGABE_ORDNER / "versand_uebersicht.csv"

# Kunde bekommt Post, wenn die Police in den naechsten N Tagen ablaeuft.
VORLAUF_TAGE = 60

# Versionskennzeichen in der URL (Cache-Buster). Bei jeder Aenderung an der
# Landingpage hochzaehlen, damit gescannte QR-Codes die frische Seite laden
# und nicht die im Browser zwischengespeicherte alte Version.
SEITEN_VERSION = "13"

# Anzahl der PBKDF2-Iterationen (muss mit dem Wert in der Landingpage
# uebereinstimmen). Hoeher = sicherer, aber langsamer beim Entsperren.
PBKDF2_ITER = 200000

# Basis-URL der gehosteten Landingpage: kostenlose GitHub-Pages-Adresse
# (gehoert GitHub, braucht keine eigene Domain/DNS). Funktioniert erst, wenn
# unter Settings -> Pages KEINE Custom Domain mehr eingetragen ist.
# Kundendaten werden als Query-Parameter angehaengt.
VIDEO_BASIS_URL = "https://y4d8vszvyg-cyber.github.io/Mischa/"

# Name des Absenders fuer die Anschreiben (Platzhalter).
ABSENDER = "Beispiel Versicherung AG"

# Bezugsdatum ("heute").
HEUTE = date(2026, 7, 22)

# --- KI-Video-Anbieter waehlen -----------------------------------------------
# "heygen"    = Talking-Avatar, spricht den Kunden persoenlich an (empfohlen)
# "higgsfield"= cineastischer Motion-Clip (spricht keinen Text)
# None        = aus
# Wird erst wirksam, wenn zusaetzlich der passende API-Key gesetzt ist.
VIDEO_PROVIDER = "heygen"

# --- HeyGen (Talking-Avatar) -------------------------------------------------
# Zum Scharfschalten:
#   1. VIDEO_PROVIDER = "heygen" (oben)
#   2. API-Key als Umgebungsvariable setzen:  export HEYGEN_API_KEY=dein_key
#   3. Avatar- und Voice-ID (deutsche Stimme) aus deinem HeyGen-Konto eintragen
# Der Key steht bewusst NICHT im Code, sondern in der Umgebungsvariable.
HEYGEN_API_KEY = os.environ.get("HEYGEN_API_KEY", "")
HEYGEN_AVATAR_ID = "DEIN_AVATAR_ID"   # HeyGen -> Avatars -> ID kopieren
HEYGEN_VOICE_ID = "DEIN_VOICE_ID"     # HeyGen -> Voices (deutsch) -> ID kopieren
HEYGEN_MAX_WARTEN = 180               # Sekunden, die wir aufs Rendern warten

# --- Higgsfield (optional) ---------------------------------------------------
# Key als Umgebungsvariable setzen:  export HIGGSFIELD_API_KEY=dein_key
# WICHTIG: Endpunkt-Pfade und JSON-Feldnamen unten mit der aktuellen
# Higgsfield-API-Doku abgleichen - sie sind als Ausgangspunkt gesetzt.
HIGGSFIELD_API_KEY = os.environ.get("HIGGSFIELD_API_KEY", "")
HIGGSFIELD_BASE = "https://api.higgsfield.ai"
HIGGSFIELD_MAX_WARTEN = 300           # Sekunden fuers Rendern (Motion dauert laenger)
HIGGSFIELD_BILD_URL = ""              # optionales Start-/Referenzbild (leer = keins)


# --- Hilfsfunktionen ---------------------------------------------------------

def parse_datum(wert: str):
    try:
        return datetime.strptime(wert.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def jahre_vertragstreue(kunde: dict) -> int:
    try:
        return max(0, HEUTE.year - int(kunde.get("vertragsbeginn", "")))
    except ValueError:
        return 0


def _b64url(rohbytes: bytes) -> str:
    """Base64url ohne Padding (URL-tauglich)."""
    return base64.urlsafe_b64encode(rohbytes).decode("ascii").rstrip("=")


def normalisiere_geburtsdatum(wert: str) -> str:
    """Geburtsdatum auf die kanonische Form TTMMJJJJ bringen (Schluessel-Basis).

    Muss identisch zur Funktion normGeb() in der Landingpage sein.
    Akzeptiert TT.MM.JJJJ ebenso wie JJJJ-MM-TT.
    """
    gruppen = re.findall(r"\d+", wert or "")
    if len(gruppen) == 3:
        if len(gruppen[0]) == 4:            # JJJJ-MM-TT
            jahr, monat, tag = gruppen
        else:                                # TT.MM.JJJJ
            tag, monat, jahr = gruppen
        return f"{int(tag):02d}{int(monat):02d}{int(jahr):04d}"
    return "".join(gruppen)


def video_url(kunde: dict) -> str:
    """Personalisierte URL mit AES-GCM-verschluesselten Kundendaten.

    Der Schluessel wird per PBKDF2 aus dem Geburtsdatum abgeleitet. In der URL
    steht nur der verschluesselte Block (salt/iv/daten) - ohne korrektes
    Geburtsdatum sind die persoenlichen Daten nicht lesbar.
    """
    daten = {
        "a": kunde.get("anrede", ""),
        "n": kunde.get("nachname", ""),
        "p": kunde.get("versicherungsart", ""),
        "b": kunde.get("vertragsbeginn", ""),
        "e": kunde.get("ablaufdatum", ""),
        "j": str(jahre_vertragstreue(kunde)),
        "s": kunde.get("schadensfaelle", ""),
        "r": kunde.get("rabatt_prozent", ""),
        "pn": kunde.get("police_nr", ""),
        "v": kunde.get("video_url", "").strip(),
    }
    geburtsdatum = normalisiere_geburtsdatum(kunde.get("geburtsdatum", ""))

    salt = os.urandom(16)
    iv = os.urandom(12)
    schluessel = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITER
    ).derive(geburtsdatum.encode("utf-8"))
    klartext = json.dumps(daten, ensure_ascii=False).encode("utf-8")
    geheim = AESGCM(schluessel).encrypt(iv, klartext, None)

    params = {
        "ver": SEITEN_VERSION,       # Cache-Buster
        "s": _b64url(salt),          # Salt fuer PBKDF2
        "iv": _b64url(iv),           # AES-GCM IV
        "d": _b64url(geheim),        # verschluesselte Daten + Auth-Tag
    }
    return f"{VIDEO_BASIS_URL}?{urlencode(params)}"


# --- Anschreiben / Video-Skript ---------------------------------------------

def erzeuge_anschreiben(kunde: dict, tage_bis_ablauf: int) -> str:
    """Personalisiertes Anschreiben / Video-Skript aus einer Textvorlage."""
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


# --- (Optional) KI-Video automatisch erzeugen (HeyGen) -----------------------

def heygen_skript(kunde: dict, tage_bis_ablauf: int) -> str:
    """Kurzer Sprech-Text, den der Avatar im Video sagt (pro Kunde personalisiert)."""
    anrede = kunde.get("anrede", "")
    nachname = kunde.get("nachname", "")
    produkt = kunde.get("versicherungsart", "Versicherung")
    return (
        f"Hallo {anrede} {nachname}, schoen, dass wir Sie erreichen. "
        f"Ihre {produkt} laeuft in {tage_bis_ablauf} Tagen aus. "
        f"Wir wuerden uns freuen, Sie weiter zu begleiten - eine Verlaengerung "
        f"ist mit einem Klick erledigt. Vielen Dank fuer Ihr Vertrauen."
    )


def heygen_video_erzeugen(skript: str) -> str | None:
    """Erzeugt ein HeyGen-Video aus dem Sprech-Skript und liefert die mp4-URL.

    Ablauf: Auftrag senden -> auf Fertigstellung warten (Polling) -> URL holen.
    Gibt None zurueck, wenn HeyGen nicht aktiv/konfiguriert ist oder ein Fehler
    auftritt. Genaue Feld-/Endpunktnamen ggf. mit der aktuellen HeyGen-API-Doku
    abgleichen: https://docs.heygen.com
    """
    if not HEYGEN_API_KEY:
        return None

    import time
    import requests

    kopf = {"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "application/json"}
    auftrag = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": HEYGEN_AVATAR_ID,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": skript,
                    "voice_id": HEYGEN_VOICE_ID,
                },
            }
        ],
        "dimension": {"width": 1280, "height": 720},
    }

    try:
        antwort = requests.post(
            "https://api.heygen.com/v2/video/generate",
            json=auftrag, headers=kopf, timeout=30,
        )
        antwort.raise_for_status()
        video_id = antwort.json()["data"]["video_id"]

        # Auf Fertigstellung warten (Polling).
        gewartet = 0
        while gewartet < HEYGEN_MAX_WARTEN:
            status = requests.get(
                "https://api.heygen.com/v1/video_status.get",
                params={"video_id": video_id}, headers=kopf, timeout=30,
            )
            status.raise_for_status()
            d = status.json()["data"]
            if d.get("status") == "completed":
                return d.get("video_url")
            if d.get("status") == "failed":
                print(f"  ! HeyGen-Rendering fehlgeschlagen: {d.get('error')}",
                      file=sys.stderr)
                return None
            time.sleep(10)
            gewartet += 10
        print("  ! HeyGen-Video nicht rechtzeitig fertig", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ! HeyGen-Fehler ({e.__class__.__name__}): {e}", file=sys.stderr)
        return None


# --- (Optional) KI-Video automatisch erzeugen (Higgsfield) -------------------

def higgsfield_prompt(kunde: dict, tage_bis_ablauf: int) -> str:
    """Prompt fuer das Higgsfield-Video (Motion/Cineastik) pro Kunde.

    Higgsfield spricht keinen Text; die Personalisierung passiert visuell/inhaltlich
    ueber den Prompt (und optional ein Referenzbild).
    """
    produkt = kunde.get("versicherungsart", "Versicherung")
    return (
        f"Warmer, cineastischer Kurzclip fuer eine Versicherungs-Botschaft zu "
        f"'{produkt}'. Ruhige Kamerafahrt, freundliche Herbststimmung, "
        f"vertrauensvoll und hochwertig."
    )


def higgsfield_video_erzeugen(prompt: str) -> str | None:
    """Erzeugt ein Higgsfield-Video aus dem Prompt und liefert die Video-URL.

    Ablauf wie ueberall: Auftrag anlegen -> auf Fertigstellung warten (Polling)
    -> URL holen. Gibt None zurueck, wenn nicht aktiv/konfiguriert oder Fehler.

    >>> ANPASSEN <<< Endpunkt-Pfade und JSON-Feldnamen mit der aktuellen
    Higgsfield-API-Doku abgleichen (Auth-Header, /generate-Pfad, Status-Feld,
    URL-Feld). Der Ablauf bleibt gleich, nur die genauen Namen koennen abweichen.
    """
    if not (VIDEO_PROVIDER == "higgsfield" and HIGGSFIELD_API_KEY):
        return None

    import time
    import requests

    kopf = {
        "Authorization": f"Bearer {HIGGSFIELD_API_KEY}",
        "Content-Type": "application/json",
    }
    auftrag = {"prompt": prompt}                 # ggf. + "duration", "model", ...
    if HIGGSFIELD_BILD_URL:
        auftrag["image_url"] = HIGGSFIELD_BILD_URL  # Bild-zu-Video (optional)

    def hole(d: dict, *pfade):
        """Erst-passenden Wert aus verschachteltem JSON holen (robust ggü. Schema)."""
        for pfad in pfade:
            akt = d
            for teil in pfad:
                akt = akt.get(teil) if isinstance(akt, dict) else None
                if akt is None:
                    break
            if akt:
                return akt
        return None

    try:
        antwort = requests.post(
            f"{HIGGSFIELD_BASE}/v1/video/generations",   # >>> Pfad ggf. anpassen
            json=auftrag, headers=kopf, timeout=30,
        )
        antwort.raise_for_status()
        job = antwort.json()
        job_id = hole(job, ("id",), ("data", "id"), ("job_id",))
        if not job_id:
            print("  ! Higgsfield: keine Job-ID in der Antwort", file=sys.stderr)
            return None

        gewartet = 0
        while gewartet < HIGGSFIELD_MAX_WARTEN:
            status = requests.get(
                f"{HIGGSFIELD_BASE}/v1/video/generations/{job_id}",  # >>> ggf. anpassen
                headers=kopf, timeout=30,
            )
            status.raise_for_status()
            d = status.json()
            zustand = hole(d, ("status",), ("data", "status"))
            if zustand in ("completed", "succeeded", "done", "finished"):
                return hole(
                    d, ("video_url",), ("output", "url"), ("data", "video_url"),
                    ("result", "url"), ("data", "output", "url"),
                )
            if zustand in ("failed", "error"):
                print(f"  ! Higgsfield-Rendering fehlgeschlagen: {d}", file=sys.stderr)
                return None
            time.sleep(10)
            gewartet += 10
        print("  ! Higgsfield-Video nicht rechtzeitig fertig", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ! Higgsfield-Fehler ({e.__class__.__name__}): {e}", file=sys.stderr)
        return None


def erzeuge_video(kunde: dict, tage_bis_ablauf: int) -> str | None:
    """Waehlt den konfigurierten Anbieter und erzeugt ein Video (oder None)."""
    if VIDEO_PROVIDER == "higgsfield":
        return higgsfield_video_erzeugen(higgsfield_prompt(kunde, tage_bis_ablauf))
    if VIDEO_PROVIDER == "heygen":
        return heygen_video_erzeugen(heygen_skript(kunde, tage_bis_ablauf))
    return None


def video_provider_bereit() -> bool:
    """True, wenn ein Anbieter gewaehlt UND sein API-Key gesetzt ist."""
    if VIDEO_PROVIDER == "higgsfield":
        return bool(HIGGSFIELD_API_KEY)
    if VIDEO_PROVIDER == "heygen":
        return bool(HEYGEN_API_KEY)
    return False


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

        # Optional: KI-Video automatisch erzeugen (Anbieter via VIDEO_PROVIDER),
        # falls aktiviert und noch kein Video in der CSV hinterlegt ist.
        if video_provider_bereit() and not kunde.get("video_url", "").strip():
            print(f"  ... erzeuge {VIDEO_PROVIDER}-Video fuer #{kunde['kunden_id']} ...")
            kunde["video_url"] = erzeuge_video(kunde, tage) or ""

        url = video_url(kunde)

        text = erzeuge_anschreiben(kunde, tage)

        basis = f"{kunde['kunden_id']}_{kunde['nachname']}"
        qr_pfad = QR_ORDNER / f"{basis}.png"
        txt_pfad = SKRIPT_TEXT_ORDNER / f"{basis}.txt"

        erzeuge_qr_code(url, qr_pfad)
        txt_pfad.write_text(text + "\n", encoding="utf-8")

        print(f"  #{kunde['kunden_id']} {kunde['vorname']} {kunde['nachname']:10s} "
              f"Ablauf in {tage:3d} T  -> {qr_pfad.name}")

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
