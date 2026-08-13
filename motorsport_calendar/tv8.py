from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

from .model import Event, ROME, normalize_token

TV8_GUIDE = "https://www.tv8.it/programmazione"
TV8_API = "https://www.tv8.it/api/programmingCarousel"
_TIME_RANGE = re.compile(r"^(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})$")
_EXCLUDED_TITLES = ("paddock", "podio", "grid", "zona-rossa", "pre-gara", "post-gara")

_GP_ALIASES = {
    "dutch": "olanda", "netherlands": "olanda",
    "italian": "italia", "italy": "italia",
    "british": "gran-bretagna", "great-britain": "gran-bretagna",
    "hungarian": "ungheria", "hungary": "ungheria",
    "spanish": "spagna", "spain": "spagna", "madrid": "spagna",
    "austrian": "austria", "austria": "austria",
    "san-marino": "san-marino", "aragon": "aragona",
    "german": "germania", "germany": "germania",
    "czech": "repubblica-ceca", "japanese": "giappone", "japan": "giappone",
    "chinese": "cina", "china": "cina", "mexico": "messico",
    "brazil": "brasile", "portuguese": "portogallo", "portugal": "portogallo",
    "malaysian": "malesia", "malaysia": "malesia",
    "indonesian": "indonesia", "indonesia": "indonesia",
    "australian": "australia", "australia": "australia",
}


def _text(program: dict, field: str) -> str:
    return str(program.get(field, {}).get("text", ""))


def _programme_range(program: dict, event_date: date) -> tuple[datetime, datetime] | None:
    label = str(program.get("badge", {}).get("label", {}).get("text", ""))
    match = _TIME_RANGE.match(label)
    if not match:
        return None
    sh, sm, eh, em = map(int, match.groups())
    start = datetime.combine(event_date, time(sh, sm), ROME)
    end = datetime.combine(event_date, time(eh, em), ROME)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _matches_grand_prix(event: Event, programme_text: str) -> bool:
    haystack = normalize_token(programme_text)
    event_text = normalize_token(" ".join((
        event.grand_prix, event.circuit, event.location, event.country,
    )))
    aliases = {
        target for source, target in _GP_ALIASES.items()
        if source in event_text
    }
    significant = {
        token for token in event_text.split("-")
        if len(token) >= 5 and token not in {
            "grand", "prix", "formula", "motogp", "circuit", "2026", "2027",
        }
    }
    return any(alias in haystack for alias in aliases) or any(
        token in haystack for token in significant
    )


def _matches_session(event: Event, title: str) -> bool:
    value = normalize_token(title)
    if any(token in value for token in _EXCLUDED_TITLES):
        return False
    if event.session == "Sprint Qualifying":
        return "qualif" in value and "sprint" in value
    if event.session == "Sprint":
        return "sprint" in value and "qualif" not in value
    if event.session in {"Qualifiche", "Q1", "Q2"}:
        return "qualif" in value and "sprint" not in value
    if event.session == "Gara":
        return "gara" in value
    return False


def apply_tv8_schedule(events: list[Event], schedules: dict[str, list[dict]]) -> list[Event]:
    """Prefer TV8 only for session-specific, simultaneous live coverage."""
    for event in events:
        if not event.is_timed:
            continue
        event_start = event.start_dt
        programmes = schedules.get(event_start.date().isoformat(), [])
        candidates: list[tuple[datetime, datetime]] = []
        for programme in programmes:
            title = _text(programme, "title")
            description = _text(programme, "description")
            if not _matches_session(event, title):
                continue
            if not _matches_grand_prix(event, f"{title} {description}"):
                continue
            interval = _programme_range(programme, event_start.date())
            if interval:
                candidates.append(interval)
        # Coverage beginning shortly before the session, or a combined
        # qualifying block already in progress at session start, is live.
        live = [
            interval for interval in candidates
            if event_start - timedelta(minutes=90) <= interval[0] <= event_start + timedelta(minutes=10)
            and interval[1] >= event_start
        ]
        if not live:
            continue
        programme_start, _ = min(live, key=lambda interval: interval[0])
        event.broadcaster_it = "TV8"
        event.broadcaster_it_url = TV8_GUIDE
        event.broadcast_type_it = "diretta"
        event.broadcast_time_it = f"dalle {programme_start.strftime('%H:%M')}"
    return events


def fetch_tv8_schedule(events: list[Event], today: date) -> dict[str, list[dict]]:
    """Fetch TV8's structured EPG for upcoming motorsport event dates."""
    dates = sorted({
        event.start_dt.date() for event in events if event.is_timed
        and today <= event.start_dt.date() <= today + timedelta(days=21)
        and event.session in {"Sprint Qualifying", "Sprint", "Qualifiche", "Q1", "Q2", "Gara"}
    })
    schedules: dict[str, list[dict]] = {}
    for event_date in dates:
        local_start = datetime.combine(event_date, time.min, ROME)
        local_end = datetime.combine(event_date, time.max, ROME)
        query = urllib.parse.urlencode({
            "from": local_start.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "to": local_end.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        })
        request = urllib.request.Request(
            f"{TV8_API}?{query}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": TV8_GUIDE},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except (OSError, ValueError):
            continue
        schedules[event_date.isoformat()] = payload.get("programs", [])
    return schedules
