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
MOTOGP_API = "https://api.pulselive.motogp.com/motogp/v1"
JOLPICA_SCHEDULE = "https://api.jolpi.ca/ergast/f1/{year}.json"

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


def _local_datetime(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ROME)
    return parsed.astimezone(ROME).isoformat(timespec="minutes")


def parse_f1_schedule_json(payload: dict[str, Any], rounds: list[dict]) -> list[Event]:
    """Parse structured Ergast-compatible session times as the F1 page fallback."""
    events: list[Event] = []
    round_rows = [rnd for rnd in rounds if rnd["competition"] == "Formula 1"]
    for race in payload.get("MRData", {}).get("RaceTable", {}).get("Races", []):
        race_date = str(race.get("date", ""))
        first_date = str(race.get("FirstPractice", {}).get("date", ""))
        rnd = next((item for item in round_rows if
                    item["start_date"][:4] == race_date[:4] and
                    (item["start_date"] == first_date or
                     abs((datetime.fromisoformat(item["start_date"]).date() -
                          datetime.fromisoformat(race_date).date()).days) <= 3)), None)
        if rnd is None:
            continue
        sessions = [
            ("FirstPractice", "FP1"), ("SecondPractice", "FP2"),
            ("ThirdPractice", "FP3"), ("SprintQualifying", "Sprint Qualifying"),
            ("SprintShootout", "Sprint Shootout"), ("Sprint", "Sprint"),
            ("Qualifying", "Qualifiche"),
        ]
        rows = [(race.get(key), name) for key, name in sessions]
        rows.append(({"date": race.get("date"), "time": race.get("time")}, "Gara"))
        source_url = JOLPICA_SCHEDULE.format(year=race_date[:4])
        for raw, session in rows:
            if not raw or not raw.get("date") or not raw.get("time"):
                continue
            events.append(Event(
                competition="Formula 1", grand_prix=rnd["grand_prix"], session=session,
                circuit=rnd["circuit"], location=rnd["location"], country=rnd["country"],
                start=_local_datetime(f"{raw['date']}T{raw['time']}"), status="programmata",
                source_sport=F1_SOURCE,
                source_sport_url=f"{F1_BASE}/en/racing/{race_date[:4]}/{rnd['slug']}",
                source_time="Jolpica/Ergast (fallback strutturato)", source_time_url=source_url,
            ))
    return events


def parse_motogp_event_json(payload: dict[str, Any], rnd: dict) -> list[Event]:
    """Parse the official MotoGP event API, keeping MotoGP race sessions only."""
    session_names = {
        "FP1": "Prove libere", "PR": "Practice", "FP2": "FP2",
        "Q1": "Q1", "Q2": "Q2", "SPR": "Sprint", "WUP": "Warm Up",
        "RAC": "Gara",
    }
    status_names = {
        "NOT-STARTED": "programmata", "CURRENT": "programmata",
        "FINISHED": "conclusa", "CANCELLED": "cancellata", "CANCELED": "cancellata",
        "POSTPONED": "rinviata",
    }
    year = str(payload.get("season", {}).get("year") or rnd["start_date"][:4])
    event_url = f"{MOTOGP_CALENDAR}/{year}/event/{payload.get('url', rnd['slug'])}/{payload.get('id', '')}"
    events: list[Event] = []
    for raw in payload.get("broadcasts", []):
        category = raw.get("category") or {}
        shortname = str(raw.get("shortname", "")).upper()
        session = session_names.get(shortname)
        if session is None and shortname.startswith("RAC"):
            session = "Gara"
        if category.get("acronym") != "MGP" or raw.get("type") != "SESSION" or session is None:
            continue
        start = raw.get("date_start")
        if not start:
            continue
        local_start = _local_datetime(start)
        local_end = _local_datetime(raw["date_end"]) if raw.get("date_end") else None
        if local_end and datetime.fromisoformat(local_end) <= datetime.fromisoformat(local_start):
            local_end = None
        events.append(Event(
            competition="MotoGP", grand_prix=rnd["grand_prix"], session=session,
            circuit=rnd["circuit"], location=rnd["location"], country=rnd["country"],
            start=local_start, end=local_end,
            status=status_names.get(str(raw.get("status", "NOT-STARTED")).upper(), "programmata"),
            source_sport=MOTOGP_SOURCE, source_sport_url=event_url,
            source_time="MotoGP.com API ufficiale", source_time_url=event_url,
        ))
    return events
