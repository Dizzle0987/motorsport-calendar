from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from .model import Event, ROME

F1_SOURCE = "Formula1.com"
F1_BASE = "https://www.formula1.com"
MOTOGP_SOURCE = "MotoGP.com"
MOTOGP_CALENDAR = "https://www.motogp.com/en/calendar"

SESSION_ALIASES = {
    "practice 1": "FP1", "free practice 1": "FP1", "fp1": "FP1",
    "practice 2": "FP2", "free practice 2": "FP2", "fp2": "FP2",
    "practice 3": "FP3", "free practice 3": "FP3", "fp3": "FP3",
    "practice": "Practice", "free practice": "Prove libere",
    "qualifying": "Qualifiche", "q1": "Q1", "q2": "Q2",
    "sprint qualifying": "Sprint Qualifying", "sprint shootout": "Sprint Shootout",
    "sprint": "Sprint", "warm up": "Warm Up", "warm-up": "Warm Up",
    "race": "Gara",
}


def classify_session(value: str) -> str:
    normalized = " ".join(value.lower().replace("™", "").split())
    return SESSION_ALIASES.get(normalized, value.strip())


def parse_f1_schedule_html(text: str, *, year: int, slug: str) -> list[Event]:
    """Parse the official Formula1.com race page's visible schedule.

    Formula1.com displays times in the viewer timezone. The updater requests the page
    with Europe/Rome preferences; fixtures verify the deliberately small parser.
    """
    clean = html.unescape(re.sub(r"<[^>]+>", " ", text))
    clean = " ".join(clean.split())
    title_match = re.search(r"(FORMULA 1 .+? GRAND PRIX .+?\d{4})", clean, re.I)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()
    location_match = re.search(r"GRAND PRIX \d{4}\s+([A-Z][A-Z ]+?)\s+Schedule", clean)
    country = location_match.group(1).title() if location_match else slug.replace("-", " ").title()
    pattern = re.compile(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
        r"(Practice 1|Practice 2|Practice 3|Sprint Qualifying|Sprint Shootout|Sprint|Qualifying|Race)\s+"
        r"(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?",
        re.I,
    )
    events: list[Event] = []
    for day, month, session, start_time, end_time in pattern.findall(clean):
        start = datetime.strptime(f"{year} {month} {day} {start_time}", "%Y %b %d %H:%M").replace(tzinfo=timezone.utc).astimezone(ROME)
        end = datetime.strptime(f"{year} {month} {day} {end_time}", "%Y %b %d %H:%M").replace(tzinfo=timezone.utc).astimezone(ROME) if end_time else None
        events.append(Event(
            competition="Formula 1", grand_prix=title, session=classify_session(session),
            circuit=country, location=country, country=country,
            start=start.isoformat(timespec="minutes"),
            end=end.isoformat(timespec="minutes") if end else None,
            source_sport=F1_SOURCE, source_sport_url=f"{F1_BASE}/en/racing/{year}/{slug}",
            source_time=F1_SOURCE, source_time_url=f"{F1_BASE}/en/racing/{year}/{slug}",
        ))
    return events


def parse_motogp_json(payload: dict[str, Any]) -> list[Event]:
    """Parse normalized official MotoGP JSON; ignore Moto2/Moto3/MotoE."""
    events: list[Event] = []
    for item in payload.get("sessions", payload.get("data", [])):
        category = str(item.get("category", item.get("class", "MotoGP")))
        if category.lower().replace("™", "") != "motogp":
            continue
        raw_start = item.get("start") or item.get("date")
        if not raw_start:
            continue
        start = str(raw_start).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(start)
        local = parsed.astimezone(ROME).isoformat(timespec="minutes") if parsed.tzinfo else parsed.replace(tzinfo=ROME).isoformat(timespec="minutes")
        events.append(Event(
            competition="MotoGP", grand_prix=item.get("grand_prix") or item.get("event_name") or "MotoGP",
            session=classify_session(item.get("session") or item.get("name") or "Da confermare"),
            circuit=item.get("circuit", "Da confermare"), location=item.get("location", ""),
            country=item.get("country", ""), start=local,
            end=item.get("end"), status=item.get("status", "programmata"),
            source_sport=MOTOGP_SOURCE, source_sport_url=item.get("url", MOTOGP_CALENDAR),
            source_time=MOTOGP_SOURCE, source_time_url=item.get("url", MOTOGP_CALENDAR),
        ))
    return events
