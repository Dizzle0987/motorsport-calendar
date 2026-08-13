from __future__ import annotations

from collections import defaultdict

from .model import Event

AT_PRIORITY = ("ORF 1", "ORF ON", "ServusTV", "ServusTV On")
IT_FREE = ("TV8",)
IT_PAID = ("Sky Sport", "NOW")

SERVUS_F1_2026 = "https://www.servustv.com/de/content/artikel/PN5TIBU059T7L8L/formel-1-2026-die-live-rennen-bei-servustv-und-servustv-on"
ORF_F1_RIGHTS = "https://der.orf.at/unternehmen/aktuell/formel1_rechte100.html"
SERVUS_MOTOGP_2026 = "https://www.servustv.com/de/content/artikel/PNF9DHWJSIDAC7G/motogp-2026-alle-live-rennen-bei-servustv-und-servustv-on"
SKY_F1_2026 = "https://sport.sky.it/formula-1/calendario"
SKY_MOTOGP_2026 = "https://sport.sky.it/motogp/calendario"

# Official 2026 allocation. Every other 2026 F1 weekend is live on ORF.
SERVUS_F1_2026_RACE_DATES = {
    "2026-03-08", "2026-03-29", "2026-05-24", "2026-06-28",
    "2026-07-19", "2026-08-23", "2026-09-06", "2026-09-27",
    "2026-10-04", "2026-10-25", "2026-11-08", "2026-11-29",
}


def _rank(name: str, order: tuple[str, ...]) -> int:
    return next((i for i, token in enumerate(order) if token.lower() in name.lower()), len(order))


def choose_broadcast(candidates: list[dict], country: str) -> dict | None:
    if not candidates:
        return None
    if country == "AT":
        return min(candidates, key=lambda x: _rank(x.get("name", ""), AT_PRIORITY))
    return min(candidates, key=lambda x: (
        0 if x.get("access") == "gratuita" else 1,
        _rank(x.get("name", ""), IT_FREE + IT_PAID),
        0 if x.get("type") == "diretta" else 1,
    ))


def apply_broadcasts(event: Event, austria: list[dict], italy: list[dict]) -> Event:
    at = choose_broadcast(austria, "AT")
    it = choose_broadcast(italy, "IT")
    if at:
        event.broadcaster_at = at["name"]
        event.broadcaster_at_url = at.get("url", "")
        event.broadcast_type_at = at.get("type", "da confermare")
        event.broadcast_time_at = at.get("time", "")
    if it:
        event.broadcaster_it = it["name"]
        event.broadcaster_it_url = it.get("url", "")
        event.broadcast_type_it = it.get("type", "da confermare")
        event.broadcast_time_it = it.get("time", "")
    return event


def apply_published_broadcasts(events: list[Event]) -> list[Event]:
    """Apply season schedules already published by the official broadcasters.

    This is deliberately conservative: free Italian TV is only selected by a
    session-specific override; Sky/NOW remains the verified live fallback.
    """
    weekends: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        weekends[(event.competition, event.grand_prix)].append(event)

    for (competition, _), weekend in weekends.items():
        race_date = max(
            value.date() if hasattr(value, "hour") else value
            for value in (event.start_dt for event in weekend)
        )
        for event in weekend:
            session_time = event.start_dt.strftime("%H:%M") if event.is_timed else ""
            event_date = event.start_dt.date() if event.is_timed else event.start_dt
            if competition == "Formula 1":
                if race_date.year == 2026:
                    if race_date.isoformat() in SERVUS_F1_2026_RACE_DATES:
                        event.broadcaster_at = "ServusTV / ServusTV On"
                        event.broadcaster_at_url = SERVUS_F1_2026
                    else:
                        event.broadcaster_at = "ORF 1 / ORF ON"
                        event.broadcaster_at_url = ORF_F1_RIGHTS
                    event.broadcast_type_at = "diretta"
                    event.broadcast_time_at = session_time
                    if race_date.isoformat() == "2026-08-23":
                        event.broadcast_time_at = {
                            "2026-08-21": "dalle 12:15",
                            "2026-08-22": "dalle 11:30",
                            "2026-08-23": "dalle 13:00",
                        }.get(event_date.isoformat(), session_time)
                event.broadcaster_it = "Sky Sport F1 / NOW"
                event.broadcaster_it_url = SKY_F1_2026
                event.broadcast_type_it = "diretta"
                event.broadcast_time_it = session_time
            elif competition == "MotoGP":
                if race_date.year == 2026:
                    if event.session in {"Q1", "Q2", "Sprint", "Gara"}:
                        event.broadcaster_at = "ServusTV / ServusTV On"
                    else:
                        event.broadcaster_at = "ServusTV On (international stream)"
                    event.broadcaster_at_url = SERVUS_MOTOGP_2026
                    event.broadcast_type_at = "diretta"
                    event.broadcast_time_at = session_time
                    if race_date.isoformat() == "2026-08-30":
                        event.broadcast_time_at = {
                            "Q1": "dalle 10:40", "Q2": "dalle 10:40",
                            "Sprint": "dalle 14:30", "Gara": "dalle 10:20",
                        }.get(event.session, session_time)
                event.broadcaster_it = "Sky Sport MotoGP / NOW"
                event.broadcaster_it_url = SKY_MOTOGP_2026
                event.broadcast_type_it = "diretta"
                event.broadcast_time_it = session_time
    return events
