from __future__ import annotations

import html as html_module
import json
import re
import urllib.error
import urllib.request
from datetime import date
from typing import Callable, Iterable

from .parsers import F1_BASE, MOTOGP_CALENDAR

JOLPICA = "https://api.jolpi.ca/ergast/f1/{year}.json"
Fetcher = Callable[[str], str]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "MotorsportCalendar/1.1 (+https://dizzle0987.github.io/motorsport-calendar/)",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8", errors="replace")


def _date(value: object) -> str | None:
    if not value:
        return None
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(value))
    return match.group(1) if match else None


def _first(raw: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                value = value.get("name") or value.get("description") or ""
            return str(value)
    return default


def _walk(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _json_blobs(text: str) -> Iterable[object]:
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", text, re.I | re.S):
        body = html_module.unescape(match.group(1)).strip()
        if not body or body[0] not in "[{":
            continue
        try:
            yield json.loads(body)
        except json.JSONDecodeError:
            continue


def merge_rounds(existing: list[dict], discovered: list[dict]) -> list[dict]:
    """Append newly published seasons and enrich matching rounds without deletion."""
    # A provider-specific slug may change (for example ``australia`` vs
    # ``albert_park``). Competition + weekend start is the cross-source identity.
    merged = {(r["competition"], r["start_date"]): dict(r) for r in existing}
    for rnd in discovered:
        key = (rnd["competition"], rnd["start_date"])
        if key in merged:
            preserved = merged[key]
            preserved.update({k: v for k, v in rnd.items() if k != "slug" and v not in (None, "")})
            if preserved.get("sprint") and not rnd.get("sprint"):
                preserved["sprint"] = True
        else:
            merged[key] = dict(rnd)
    return sorted(merged.values(), key=lambda r: (r["start_date"], r["competition"], r["slug"]))


def parse_f1_official_calendar(text: str, year: int) -> list[dict]:
    rounds: list[dict] = []
    for blob in _json_blobs(text):
        for raw in _walk(blob):
            start = _date(_first(raw, "meetingStartDate", "startDate", "dateStart", "start_date", "date"))
            name = _first(raw, "meetingOfficialName", "officialName", "eventName", "meetingName", "name")
            circuit = _first(raw, "circuitName", "circuit", "venueName", "venue")
            slug = _first(raw, "meetingSlug", "slug", "urlSlug")
            if not start or not name or start[:4] != str(year) or "grand prix" not in name.lower():
                continue
            if not slug:
                slug = "-".join(re.findall(r"[a-z0-9]+", name.lower().replace(str(year), "")))
            rounds.append({
                "competition": "Formula 1", "slug": slug,
                "grand_prix": name if str(year) in name else f"{name} {year}",
                "circuit": circuit or "Da confermare",
                "location": _first(raw, "locality", "city", "location", default="Da confermare"),
                "country": _first(raw, "countryName", "country", default="Da confermare"),
                "start_date": start,
                "source_url": f"{F1_BASE}/en/racing/{year}/{slug}",
            })
    return merge_rounds([], rounds)


def parse_jolpica_calendar(text: str, year: int) -> list[dict]:
    payload = json.loads(text)
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    rounds: list[dict] = []
    for raw in races:
        circuit = raw.get("Circuit", {})
        location = circuit.get("Location", {})
        race_name = raw.get("raceName", "Grand Prix")
        start = _date(raw.get("FirstPractice", {}).get("date"))
        if not start:
            race_day = date.fromisoformat(raw["date"])
            start = race_day.fromordinal(race_day.toordinal() - 2).isoformat()
        slug = circuit.get("circuitId") or "-".join(re.findall(r"[a-z0-9]+", race_name.lower()))
        rounds.append({
            "competition": "Formula 1", "slug": slug,
            "grand_prix": f"{race_name} {year}", "circuit": circuit.get("circuitName", "Da confermare"),
            "location": location.get("locality", "Da confermare"),
            "country": location.get("country", "Da confermare"), "start_date": start,
            "source_url": f"https://api.jolpi.ca/ergast/f1/{year}.json",
            "sprint": bool(raw.get("Sprint")),
        })
    return rounds


def parse_motogp_official_calendar(text: str, year: int) -> list[dict]:
    rounds: list[dict] = []
    for blob in _json_blobs(text):
        for raw in _walk(blob):
            category = _first(raw, "category", "class", "discipline", default="MotoGP")
            if category and "motogp" not in category.lower().replace("™", ""):
                continue
            start = _date(_first(raw, "date_start", "startDate", "start_date", "start", "date"))
            name = _first(raw, "event_name", "eventName", "officialName", "name", "shortName")
            circuit = _first(raw, "circuit_name", "circuitName", "circuit", "venueName", "venue")
            if not start or not name or start[:4] != str(year):
                continue
            lowered = name.lower()
            if "test" in lowered or (not circuit and not any(token in lowered for token in ("grand prix", "gp", "tt"))):
                continue
            slug = _first(raw, "slug", "eventSlug", "urlSlug") or "-".join(re.findall(r"[a-z0-9]+", name.lower().replace(str(year), "")))
            rounds.append({
                "competition": "MotoGP", "slug": slug, "grand_prix": name if str(year) in name else f"{name} {year}",
                "circuit": circuit or "Da confermare",
                "location": _first(raw, "locality", "city", "location", default="Da confermare"),
                "country": _first(raw, "countryName", "country", default="Da confermare"),
                "start_date": start, "source_url": f"https://www.motogp.com/en/calendar/{year}",
            })
    return merge_rounds([], rounds)


def discover_rounds(existing: list[dict], today: date, fetcher: Fetcher = fetch_text) -> list[dict]:
    discovered: list[dict] = []
    for year in (today.year, today.year + 1):
        try:
            official_f1 = parse_f1_official_calendar(fetcher(f"{F1_BASE}/en/racing/{year}"), year)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            official_f1 = []
        if official_f1:
            discovered.extend(official_f1)
        else:
            try:
                discovered.extend(parse_jolpica_calendar(fetcher(JOLPICA.format(year=year)), year))
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
                pass
        try:
            discovered.extend(parse_motogp_official_calendar(fetcher(f"{MOTOGP_CALENDAR}/{year}"), year))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            pass
    return merge_rounds(existing, discovered)
