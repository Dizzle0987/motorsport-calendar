from __future__ import annotations

from copy import deepcopy

from .model import Event


def merge_events(automatic: list[Event], manual: list[Event], previous: list[Event] | None = None) -> list[Event]:
    previous_by_key = {e.key: e for e in previous or []}
    merged: dict[str, Event] = {}
    for event in automatic:
        if event.enabled:
            merged[event.key] = deepcopy(event)
    for override in manual:
        if not override.enabled:
            merged.pop(override.key, None)
            continue
        merged[override.key] = deepcopy(override)
    result: list[Event] = []
    for key, event in merged.items():
        old = previous_by_key.get(key)
        if old:
            event.sequence = old.sequence + (event.material_signature() != old.material_signature())
            if old.start != event.start:
                event.notes = "; ".join(filter(None, [event.notes, f"Riprogrammata da {old.start}"]))
        result.append(event)
    return sorted(result, key=lambda e: (e.start, e.competition, e.grand_prix, e.session))


def deduplicate(events: list[Event]) -> list[Event]:
    unique: dict[str, Event] = {}
    for event in events:
        current = unique.get(event.key)
        if current is None or (not current.is_timed and event.is_timed):
            unique[event.key] = event
        elif current.material_signature() != event.material_signature():
            message = f"Conflitto con {event.source_time or event.source_sport}: {event.start}"
            if message not in current.conflicts:
                current.conflicts.append(message)
    return list(unique.values())
