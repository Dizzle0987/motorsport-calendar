from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from .model import Event, ROME

ORF_EPG = "https://tv.orf.at/program/orf1/index.html"
SERVUS_EPG = "https://www.servustv.com/de/epg"
TVHEUTE_BASE = "https://tvheute.at"
TVINFO_BASE = "https://www.tvinfo.de/tv-programm"
SKY_F1 = "https://programmi.sky.it/sport/motori/formula-1"
SKY_MOTOGP = "https://programmi.sky.it/sport/motori/motogp"

# Stable official Programmi Sky pages. Their contents are refreshed for the
# current edition, so the same URLs remain useful in later seasons.
SKY_GUIDES = {
    "Formula 1": {
        "dutch": f"{SKY_F1}/dove-vedere-f1-olanda-programmazione",
        "netherlands": f"{SKY_F1}/dove-vedere-f1-olanda-programmazione",
        "italian": f"{SKY_F1}/dove-vedere-f1-italia-programmazione",
        "monza": f"{SKY_F1}/dove-vedere-f1-italia-programmazione",
        "madrid": f"{SKY_F1}/dove-vedere-f1-madrid-programmazione",
        "azerbaijan": f"{SKY_F1}/dove-vedere-f1-azerbaijan-programmazione",
        "singapore": f"{SKY_F1}/dove-vedere-f1-singapore-programmazione",
        "united states": f"{SKY_F1}/dove-vedere-f1-stati-uniti-programmazione",
        "mexico": f"{SKY_F1}/dove-vedere-f1-messico-programmazione",
    },
    "MotoGP": {
        "aragon": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-aragon",
        "san marino": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-san-marino",
        "austria": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-austria",
        "japan": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-giappone",
        "indonesia": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-indonesia",
        "australia": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-australia",
        "malaysia": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-malesia",
        "portugal": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-portogallo",
        "valencia": f"{SKY_MOTOGP}/dove-vedere-orari-motogp-gp-valencia",
    },
}

ITALIAN_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

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


def apply_epg(
    events: list[Event], programmes: list[dict], broadcaster: str, source: str,
    *, only_missing: bool = False,
) -> list[Event]:
    for event in events:
        if not event.is_timed or broadcaster.casefold() not in event.broadcaster_at.casefold():
            continue
        if only_missing and event.broadcast_time_at:
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
                candidates.append((start, row.get("source", source)))
        if candidates:
            start, selected_source = min(candidates, key=lambda item: item[0])
            event.broadcast_time_at = f"dalle {start:%H:%M}"
            event.broadcaster_at_url = selected_source
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


TVHEUTE_CATEGORIES = (
    "SPORT", "INFO", "SHOW", "SERIE", "DOKU", "FILM", "KIDS",
    "MAGAZIN", "UNTERHALTUNG", "NACHRICHTEN",
)


def parse_tvheute_epg(text: str, event_date: date, channel: str) -> list[dict]:
    """Parse one dated TVHeute channel page into local EPG intervals."""
    page = visible_text(text)
    categories = "|".join(TVHEUTE_CATEGORIES)
    channel_pattern = re.escape(channel)
    pattern = re.compile(
        rf"(?:^|\s){channel_pattern}\s+(?:(?:{categories})\s+)?"
        rf"(?P<start>\d{{2}}:\d{{2}})\s+(?P<end>\d{{2}}:\d{{2}})\s+"
        rf"\d+'\s+(?P<title>.*?)"
        rf"(?=(?:\s{channel_pattern}\s+(?:(?:{categories})\s+)?\d{{2}}:\d{{2}})|\Z)",
        re.I | re.S,
    )
    rows: list[dict] = []
    for match in pattern.finditer(page):
        start = datetime.combine(event_date, datetime.strptime(match["start"], "%H:%M").time(), ROME)
        end = datetime.combine(event_date, datetime.strptime(match["end"], "%H:%M").time(), ROME)
        if end <= start:
            end += timedelta(days=1)
        rows.append({
            "title": " ".join(match["title"].split()),
            "start": start.isoformat(),
            "end": end.isoformat(),
        })
    return rows


def fetch_tvheute_epg(events: list[Event], today: date, broadcaster: str) -> tuple[list[dict], str]:
    """Fetch the dated Austrian TV guide used only as a resilient fallback."""
    if broadcaster == "ORF":
        channel, slug = "ORF1", "orf1-programm"
    else:
        channel, slug = "ServusTV", "servustv-programm"
    dates = sorted({
        event.start_dt.date() for event in events
        if event.is_timed
        and today <= event.start_dt.date() <= today + timedelta(days=21)
        and broadcaster.casefold() in event.broadcaster_at.casefold()
        and "international stream" not in event.broadcaster_at.casefold()
        and not event.broadcast_time_at
    })
    rows: list[dict] = []
    for event_date in dates:
        url = f"{TVHEUTE_BASE}/{slug}/{event_date:%d-%m-%Y}-im-tv"
        try:
            parsed = parse_tvheute_epg(_get(url), event_date, channel)
            for row in parsed:
                row["source"] = url
            rows.extend(parsed)
        except (OSError, ValueError):
            continue
    return rows, f"{TVHEUTE_BASE}/{slug}/heute-im-tv"


class _TvInfoParser(HTMLParser):
    """Collect table rows; TVinfo places each of four dates in one column."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and data.strip():
            self.cell.append(data.strip())


def parse_tvinfo_epg(text: str, event_date: date) -> list[dict]:
    """Read the first (requested-date) column of a TVinfo channel page."""
    parser = _TvInfoParser()
    parser.feed(text)
    rows: list[dict] = []
    for table_row in parser.rows:
        if not table_row:
            continue
        cell = table_row[0]
        match = re.match(r"^(?P<start>\d{1,2}:\d{2})\s+(?P<title>.+)$", cell, re.S)
        if not match:
            continue
        start = datetime.combine(event_date, datetime.strptime(match["start"], "%H:%M").time(), ROME)
        rows.append({
            "title": match["title"],
            "start": start.isoformat(),
            # A listing page does not expose the end consistently. Three hours
            # safely covers the pre-show plus session matching window.
            "end": (start + timedelta(hours=3)).isoformat(),
        })
    return rows


def fetch_tvinfo_epg(events: list[Event], today: date, broadcaster: str) -> tuple[list[dict], str]:
    """Fetch server-rendered Austrian listings, one requested date at a time."""
    slug = "orf1" if broadcaster == "ORF" else "servustv"
    dates = sorted({
        event.start_dt.date() for event in events
        if event.is_timed
        and today <= event.start_dt.date() <= today + timedelta(days=21)
        and broadcaster.casefold() in event.broadcaster_at.casefold()
        and "international stream" not in event.broadcaster_at.casefold()
        and not event.broadcast_time_at
    })
    rows: list[dict] = []
    for event_date in dates:
        url = f"{TVINFO_BASE}/{slug}/{event_date:%d.%m.%Y}"
        try:
            parsed = parse_tvinfo_epg(_get(url), event_date)
            for row in parsed:
                row["source"] = url
            rows.extend(parsed)
        except (OSError, ValueError):
            continue
    return rows, f"{TVINFO_BASE}/{slug}"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def visible_text(text: str) -> str:
    if "<" not in text:
        return " ".join(text.split())
    parser = _VisibleTextParser()
    parser.feed(text)
    return " ".join(parser.parts)


def _sky_session_matches(event: Event, context: str) -> bool:
    value = context.casefold()
    if event.session == "Sprint Qualifying":
        return "sprint" in value and "qual" in value
    if event.session == "Sprint":
        return "sprint" in value and "qual" not in value
    if event.session in {"Qualifiche", "Q1", "Q2"}:
        return "qualific" in value and "sprint" not in value
    if event.session == "Gara":
        return any(x in value for x in ("gara lunga", "la gara", "gran premio"))
    if event.session in {"FP1", "Prove libere"}:
        return any(x in value for x in ("prima sessione di prove", "prime prove libere", "prove libere 1"))
    if event.session == "Practice":
        return "pre-qualific" in value
    if event.session in {"FP2", "FP3"}:
        return event.session.casefold() in value or f"prove libere {event.session[-1]}" in value
    return False


def sky_time_for_event(event: Event, page: str) -> str:
    """Read a session time only from the dated official Sky guide text."""
    text = visible_text(page)
    lowered = text.casefold()
    day_pattern = re.compile(
        r"(?:venerd[iì]|sabato|domenica)\s+(\d{1,2})(?:\s+([a-zà]+))?",
        re.I,
    )
    markers = list(day_pattern.finditer(lowered))
    for index, marker in enumerate(markers):
        day = int(marker.group(1))
        month_name = (marker.group(2) or "").casefold()
        month = ITALIAN_MONTHS.get(month_name, event.start_dt.month)
        if day != event.start_dt.day or month != event.start_dt.month:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else min(len(lowered), marker.end() + 700)
        segment = lowered[marker.end():end]
        time_matches = list(re.finditer(r"(?:ore|alle|dalle)\s+(\d{1,2})(?:[:.]([0-5]\d))?", segment))
        for time_index, time_match in enumerate(time_matches):
            previous_end = time_matches[time_index - 1].end() if time_index else 0
            next_start = time_matches[time_index + 1].start() if time_index + 1 < len(time_matches) else len(segment)
            before = segment[max(previous_end, time_match.start() - 85):time_match.start()]
            after = segment[time_match.end():min(next_start, time_match.end() + 105)]
            if (_sky_session_matches(event, after)
                    or _sky_session_matches(event, before)
                    or _sky_session_matches(event, f"{before} {after}")):
                return f"dalle {int(time_match.group(1)):02d}:{time_match.group(2) or '00'}"
    return ""


def sky_guide_for_event(event: Event) -> str:
    value = f"{event.grand_prix} {event.circuit} {event.location}".casefold()
    for token, url in SKY_GUIDES.get(event.competition, {}).items():
        if token in value:
            return url
    return ""


def apply_sky_guides(events: list[Event], today: date) -> list[Event]:
    pages: dict[str, str] = {}
    for event in events:
        if not event.is_timed or not (today <= event.start_dt.date() <= today + timedelta(days=21)):
            continue
        if "Sky Sport" not in event.broadcaster_it:
            continue
        url = sky_guide_for_event(event)
        if not url:
            continue
        if url not in pages:
            try:
                pages[url] = _get(url)
            except OSError:
                pages[url] = ""
        programme_time = sky_time_for_event(event, pages[url])
        if programme_time:
            event.broadcast_time_it = programme_time
            event.broadcaster_it_url = url
    return events


def apply_official_epgs(events: list[Event], today: date) -> list[Event]:
    try:
        apply_epg(events, fetch_orf_epg(events, today), "ORF", ORF_EPG)
    except (OSError, ValueError):
        pass
    try:
        apply_epg(events, fetch_servus_epg(), "ServusTV", SERVUS_EPG)
    except (OSError, ValueError):
        pass
    # The broadcasters' own pages remain authoritative. The server-rendered
    # TVinfo grid is the first fallback; TVHeute remains a second independent
    # fallback. Both are queried only for still-empty linear-TV airtimes.
    for broadcaster in ("ORF", "ServusTV"):
        fallback, source = fetch_tvinfo_epg(events, today, broadcaster)
        apply_epg(events, fallback, broadcaster, source, only_missing=True)
        fallback, source = fetch_tvheute_epg(events, today, broadcaster)
        apply_epg(events, fallback, broadcaster, source, only_missing=True)
    apply_sky_guides(events, today)
    return events
