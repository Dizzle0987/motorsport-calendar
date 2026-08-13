from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

from .model import Event, ROME

ORF_EPG = "https://tv.orf.at/program/orf1/index.html"
SERVUS_EPG = "https://www.servustv.com/de/epg"
SKY_F1 = "https://programmi.sky.it/sport/motori/formula-1"
SKY_MOTOGP = "https://programmi.sky.it/sport/motori/motogp"

HEADERS = {
    "User-Agent": "MotorsportCalendar/1.0 (+https://dizzle0987.github.io/motorsport-calendar/)",
    "Accept-Language": "it-IT,it;q=0.9,de;q=0.8,en;q=0.7",
}


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8", errors="replace")


class _OrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.programmes: list[dict] = []
        self.day_links: dict[str, str] = {}
        self.current: dict | None = None
        self.capture_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        href = values.get("href", "") or ""
        match = re.search(r"day-(\d{2})-(\d{2})-(\d{4})", href)
        if tag == "a" and match:
            self.day_links[f"{match[3]}-{match[2]}-{match[1]}"] = urllib.parse.urljoin(ORF_EPG, href)
        classes = (values.get("class", "") or "").split()
        if tag == "li" and "broadcast" in classes and values.get("data-start-time"):
            self.current = {
                "start": values["data-start-time"],
                "end": values.get("data-end-time", ""),
                "title": "",
            }
        if self.current is not None and tag == "div" and ({"series-title", "episode-title"} & set(classes)):
            self.capture_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self.capture_title = False
        if tag == "li" and self.current is not None:
            self.programmes.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.capture_title and data.strip():
            self.current["title"] += " " + data.strip()


def parse_orf_epg(text: str) -> tuple[list[dict], dict[str, str]]:
    parser = _OrfParser()
    parser.feed(text)
    return parser.programmes, parser.day_links


def parse_servus_epg(text: str) -> list[dict]:
    """Extract the official server-rendered ServusTV EPG cards."""
    # Next.js serialises the cards inside RSC strings. Limiting every match to
    # one object prevents a title from being paired with a later programme.
    pattern = re.compile(
        r'\\"title\\":\\"(?P<title>(?:[^\\]|\\.)*?)\\"(?:(?!\},\{).)*?'
        r'\\"start_time\\":\\"(?P<start>[^\\"]+)\\"(?:(?!\},\{).)*?'
        r'\\"end_time\\":\\"(?P<end>[^\\"]+)\\"', re.S,
    )
    rows: list[dict] = []
    for match in pattern.finditer(text):
        try:
            title = json.loads('"' + match["title"] + '"')
        except json.JSONDecodeError:
            title = html.unescape(match["title"].replace('\\"', '"'))
        rows.append({"title": title, "start": match["start"], "end": match["end"]})
    return rows


def _session_matches(event: Event, title: str) -> bool:
    value = title.casefold()
    if event.competition == "Formula 1" and not any(x in value for x in ("formel 1", "formula 1", "f1")):
        return False
    if event.competition == "MotoGP" and "motogp" not in value:
        return False
    if event.session == "Sprint Qualifying":
        return "sprint" in value and any(x in value for x in ("qual", "shootout"))
    if event.session == "Sprint":
        return "sprint" in value and not any(x in value for x in ("qual", "shootout"))
    if event.session in {"Qualifiche", "Q1", "Q2"}:
        return any(x in value for x in ("qual", "q1", "q2")) and "sprint" not in value
    if event.session == "Gara":
        return any(x in value for x in ("rennen", "gara", "race", "grand prix", "gp "))
    if event.session in {"FP1", "FP2", "FP3", "Prove libere", "Practice"}:
        return any(x in value for x in ("training", "practice", "prove libere", event.session.casefold()))
    return False


def apply_epg(events: list[Event], programmes: list[dict], broadcaster: str, source: str) -> list[Event]:
    for event in events:
        if not event.is_timed or broadcaster.casefold() not in event.broadcaster_at.casefold():
            continue
        candidates = []
        for row in programmes:
            if not _session_matches(event, row.get("title", "")):
                continue
            try:
                start = datetime.fromisoformat(row["start"].replace("Z", "+00:00")).astimezone(ROME)
                end = datetime.fromisoformat(row["end"].replace("Z", "+00:00")).astimezone(ROME)
            except (KeyError, ValueError):
                continue
            if start.date() == event.start_dt.date() and event.start_dt - timedelta(hours=2) <= start <= event.start_dt + timedelta(minutes=10) and end >= event.start_dt:
                candidates.append(start)
        if candidates:
            start = min(candidates)
            event.broadcast_time_at = f"dalle {start:%H:%M}"
            event.broadcaster_at_url = source
    return events


def fetch_orf_epg(events: list[Event], today: date) -> list[dict]:
    relevant = {e.start_dt.date().isoformat() for e in events if e.is_timed and "ORF" in e.broadcaster_at and today <= e.start_dt.date() <= today + timedelta(days=21)}
    index = _get(ORF_EPG)
    current, links = parse_orf_epg(index)
    rows = list(current)
    for day in sorted(relevant):
        if day in links:
            parsed, _ = parse_orf_epg(_get(links[day]))
            rows.extend(parsed)
    return rows


def fetch_servus_epg() -> list[dict]:
    return parse_servus_epg(_get(SERVUS_EPG))


def check_sky_official_pages(events: list[Event]) -> None:
    """Check both official Sky programme pages without inventing an airtime.

    Sky/NOW rights remain assigned by the official seasonal calendar. These
    pages are fetched on every run; exact airtimes are only filled by a
    machine-verifiable EPG source (currently TV8 for the free alternative).
    """
    if any(e.competition == "Formula 1" for e in events):
        _get(SKY_F1)
    if any(e.competition == "MotoGP" for e in events):
        _get(SKY_MOTOGP)


def apply_official_epgs(events: list[Event], today: date) -> list[Event]:
    try:
        apply_epg(events, fetch_orf_epg(events, today), "ORF", ORF_EPG)
    except (OSError, ValueError):
        pass
    try:
        apply_epg(events, fetch_servus_epg(), "ServusTV", SERVUS_EPG)
    except (OSError, ValueError):
        pass
    try:
        check_sky_official_pages(events)
    except (OSError, ValueError):
        pass
    return events
