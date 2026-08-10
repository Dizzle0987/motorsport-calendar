from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .discovery import discover_rounds
from .ics import render_calendar
from .merge import deduplicate, merge_events
from .model import Event
from .parsers import F1_BASE, F1_SOURCE, MOTOGP_CALENDAR, MOTOGP_SOURCE, parse_f1_schedule_html

ROOT = Path(__file__).resolve().parents[1]
ORF_URL = "https://tv.orf.at/"
SERVUS_URL = "https://www.servustv.com/sport/"
TV8_URL = "https://www.tv8.it/guidatv"
SKY_URL = "https://sport.sky.it/guida-tv"


def load_events(path: Path) -> list[Event]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("events", raw) if isinstance(raw, dict) else raw
    return [Event.from_dict(item) for item in rows]


def load_round_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tbc_broadcast(event: Event) -> Event:
    event.broadcaster_at = "ORF 1 / ORF ON o ServusTV / ServusTV On — Da confermare"
    event.broadcaster_at_url = f"{ORF_URL} | {SERVUS_URL}"
    event.broadcast_type_at = "da confermare"
    event.broadcaster_it = "TV8 / Sky Sport / NOW — Da confermare"
    event.broadcaster_it_url = f"{TV8_URL} | {SKY_URL}"
    event.broadcast_type_it = "da confermare"
    return event


def events_from_rounds(rounds: list[dict], today: date | None = None) -> list[Event]:
    today = today or date.today()
    events: list[Event] = []
    for rnd in rounds:
        start = date.fromisoformat(rnd["start_date"])
        competition = rnd["competition"]
        if competition == "Formula 1":
            sessions = ([(-0, "FP1"), (0, "Sprint Qualifying"), (1, "Sprint"), (1, "Qualifiche"), (2, "Gara")]
                        if rnd.get("sprint") else [(0, "FP1"), (0, "FP2"), (1, "FP3"), (1, "Qualifiche"), (2, "Gara")])
            source, source_url = F1_SOURCE, f"{F1_BASE}/en/racing/{start.year}/{rnd['slug']}"
        else:
            sessions = [(0, "Prove libere"), (0, "Practice"), (1, "FP2"), (1, "Q1"), (1, "Q2"), (1, "Sprint"), (2, "Warm Up"), (2, "Gara")]
            source, source_url = MOTOGP_SOURCE, MOTOGP_CALENDAR
        for offset, session in sessions:
            session_date = start + timedelta(days=offset)
            status = "conclusa" if session_date < today else "da confermare"
            events.append(_tbc_broadcast(Event(
                competition=competition, grand_prix=rnd["grand_prix"], session=session,
                circuit=rnd["circuit"], location=rnd["location"], country=rnd["country"],
                start=session_date.isoformat(), status=status,
                source_sport=source, source_sport_url=source_url,
                source_time=f"{source} (solo data; ora non pubblicata)", source_time_url=source_url,
                notes="Orario non ancora verificato: evento per l'intera giornata.",
            )))
    return events


def fetch_f1_details(rounds: list[dict]) -> list[Event]:
    found: list[Event] = []
    for rnd in rounds:
        if rnd["competition"] != "Formula 1":
            continue
        if date.fromisoformat(rnd["start_date"]) < date.today() - timedelta(days=7):
            continue
        year = int(rnd["start_date"][:4])
        url = f"{F1_BASE}/en/racing/{year}/{rnd['slug']}"
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": "MotorsportCalendar/1.0 (+https://dizzle0987.github.io/motorsport-calendar/)",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(request, timeout=20) as response:
                text = response.read().decode("utf-8", errors="replace")
            parsed = parse_f1_schedule_html(text, year=year, slug=rnd["slug"])
        except (urllib.error.URLError, TimeoutError):
            continue
        if any(event.session in {"Sprint", "Sprint Qualifying", "Sprint Shootout"} for event in parsed):
            rnd["sprint"] = True
        for event in parsed:
            event.grand_prix = rnd["grand_prix"]
            event.circuit, event.location, event.country = rnd["circuit"], rnd["location"], rnd["country"]
            _tbc_broadcast(event)
        found.extend(parsed)
    return found


def validate(events: list[Event], calendars: dict[str, str]) -> None:
    if not events or not {"Formula 1", "MotoGP"}.issubset({e.competition for e in events}):
        raise ValueError("Aggiornamento incompleto: entrambe le competizioni sono obbligatorie")
    if len({e.uid for e in events}) != len(events):
        raise ValueError("UID duplicati")
    for name, content in calendars.items():
        if not content.startswith("BEGIN:VCALENDAR") or not content.endswith("END:VCALENDAR\r\n"):
            raise ValueError(f"Calendario {name} non valido")


def _atomic_write_many(files: dict[Path, str]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for target, content in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((Path(temp_name), target))
        for temp, target in staged:
            os.replace(temp, target)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)


def generate(root: Path = ROOT, *, online: bool = True, now: datetime | None = None) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    previous = load_events(root / "data/events.json")
    manual = load_events(root / "data/manual_events.json")
    round_catalog = load_round_catalog(root / "data/rounds.json")
    rounds = round_catalog["rounds"]
    if online:
        try:
            rounds = discover_rounds(rounds, now.date())
            official_f1 = fetch_f1_details(rounds)
        except (urllib.error.URLError, TimeoutError, ValueError):
            # Round-level official dates remain usable. Existing outputs are never emptied.
            official_f1 = []
    else:
        official_f1 = []
    automatic = deduplicate(official_f1 + events_from_rounds(rounds, today=now.date()))
    combined = merge_events(deduplicate(automatic), manual, previous)
    if not combined and previous:
        combined = previous
    calendars = {
        "calendar.ics": render_calendar(combined, "Motorsport Calendar — F1 + MotoGP", now),
        "f1.ics": render_calendar([e for e in combined if e.competition == "Formula 1"], "Formula 1", now),
        "motogp.ics": render_calendar([e for e in combined if e.competition == "MotoGP"], "MotoGP", now),
    }
    validate(combined, calendars)
    payload = {
        "schema_version": 1,
        "updated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "timezone": "Europe/Rome",
        "events": [event.as_dict() for event in combined],
    }
    files = {root / name: content for name, content in calendars.items()}
    files[root / "data/events.json"] = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    round_catalog["rounds"] = rounds
    round_catalog["managed_seasons"] = sorted({int(r["start_date"][:4]) for r in rounds})
    files[root / "data/rounds.json"] = json.dumps(round_catalog, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_many(files)
    return combined
