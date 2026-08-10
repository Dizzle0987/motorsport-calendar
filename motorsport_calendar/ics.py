from __future__ import annotations

from datetime import datetime, timezone

from .model import Event


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    raw = line.encode("utf-8")
    parts: list[bytes] = []
    while len(raw) > 75:
        cut = 75
        while cut and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(raw[:cut])
        raw = raw[cut:]
    parts.append(raw)
    return "\r\n ".join(part.decode("utf-8") for part in parts)


def _description(event: Event) -> str:
    status = event.status.upper()
    if event.status == "cancellata":
        status = "CANCELLATA"
    elif event.status == "rinviata" and not event.is_timed:
        status = "RINVIATA — DATA DA DESTINARSI"
    return "\n".join(filter(None, [
        f"Stato: {status}",
        f"Austria: {event.broadcaster_at} ({event.broadcast_type_at})",
        f"Italia: {event.broadcaster_it} ({event.broadcast_type_it})",
        "Streaming soggetto a possibili limitazioni geografiche.",
        event.notes,
        f"Fonte sportiva: {event.source_sport} {event.source_sport_url}",
        f"Fonte orario: {event.source_time} {event.source_time_url}",
        *(f"Conflitto: {c}" for c in event.conflicts),
    ]))


def render_calendar(events: list[Event], name: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "PRODID:-//Motorsport Calendar//IT", f"X-WR-CALNAME:{esc(name)}",
        "X-WR-TIMEZONE:Europe/Rome", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for event in sorted(events, key=lambda e: e.start):
        summary_prefix = ""
        if event.status == "cancellata": summary_prefix = "CANCELLATA — "
        elif event.status == "rinviata": summary_prefix = "RINVIATA — "
        elif event.status == "da confermare": summary_prefix = "TBC — "
        lines.extend(["BEGIN:VEVENT", f"UID:{event.uid}", f"DTSTAMP:{stamp}", f"SEQUENCE:{event.sequence}"])
        if event.is_timed:
            lines.append(f"DTSTART;TZID=Europe/Rome:{event.start_dt.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"DTEND;TZID=Europe/Rome:{event.end_dt.strftime('%Y%m%dT%H%M%S')}")
        else:
            lines.append(f"DTSTART;VALUE=DATE:{event.start_dt.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{event.end_dt.strftime('%Y%m%d')}")
        lines.extend([
            f"SUMMARY:{esc(summary_prefix + event.competition + ' — ' + event.grand_prix + ' — ' + event.session)}",
            f"LOCATION:{esc(', '.join(filter(None, [event.circuit, event.location, event.country])))}",
            f"DESCRIPTION:{esc(_description(event))}",
            "BEGIN:VALARM", "TRIGGER:-PT2H30M", "ACTION:DISPLAY", "DESCRIPTION:Motorsport tra 2 ore e 30 minuti", "END:VALARM",
        ])
        if event.session.lower() == "gara":
            lines.extend(["BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", "DESCRIPTION:Gara domani", "END:VALARM"])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
