from __future__ import annotations

import html
import json
from datetime import date, datetime
from pathlib import Path

from .model import Event


def render_index(root: Path, events: list[Event]) -> None:
    template = (root / "templates/index.template.html").read_text(encoding="utf-8")
    meta = json.loads((root / "data/events.json").read_text(encoding="utf-8"))
    upcoming = [e for e in events if (e.start_dt.date() if isinstance(e.start_dt, datetime) else e.start_dt) >= date.today()][:8]
    cards = "\n".join(
        f'<li><span class="tag {"f1" if e.competition == "Formula 1" else "motogp"}">{html.escape(e.competition)}</span>'
        f'<strong>{html.escape(e.grand_prix)} · {html.escape(e.session)}</strong>'
        f'<time>{html.escape(e.start.replace("T", " · "))}</time></li>' for e in upcoming
    ) or "<li>Nessun prossimo evento pubblicato.</li>"
    page = template.replace("{{UPDATED_AT}}", html.escape(meta["updated_at"]))
    page = page.replace("{{UPCOMING_EVENTS}}", cards)
    (root / "index.html").write_text(page, encoding="utf-8")
