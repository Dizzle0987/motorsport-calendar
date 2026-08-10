from __future__ import annotations

from .model import Event

AT_PRIORITY = ("ORF 1", "ORF ON", "ServusTV", "ServusTV On")
IT_FREE = ("TV8",)
IT_PAID = ("Sky Sport", "NOW")


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
    if it:
        event.broadcaster_it = it["name"]
        event.broadcaster_it_url = it.get("url", "")
        event.broadcast_type_it = it.get("type", "da confermare")
    return event
