from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
VALID_STATUSES = {"programmata", "da confermare", "rinviata", "cancellata", "conclusa"}


def normalize_token(value: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in value).split())


@dataclass
class Event:
    competition: str
    grand_prix: str
    session: str
    circuit: str
    location: str
    country: str
    start: str
    end: str | None = None
    status: str = "programmata"
    source_sport: str = ""
    source_sport_url: str = ""
    source_time: str = ""
    source_time_url: str = ""
    broadcaster_at: str = "Da confermare"
    broadcaster_at_url: str = ""
    broadcast_type_at: str = "da confermare"
    broadcast_time_at: str = ""
    broadcaster_it: str = "Da confermare"
    broadcaster_it_url: str = ""
    broadcast_type_it: str = "da confermare"
    broadcast_time_it: str = ""
    conflicts: list[str] = field(default_factory=list)
    notes: str = ""
    sequence: int = 0
    enabled: bool = True
    stable_key: str | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Stato non valido: {self.status}")
        if not self.start:
            raise ValueError("start è obbligatorio")

    @property
    def key(self) -> str:
        return self.stable_key or ":".join(
            normalize_token(v) for v in (self.competition, self.grand_prix, self.session)
        )

    @property
    def uid(self) -> str:
        digest = sha256(self.key.encode()).hexdigest()[:24]
        return f"{digest}@motorsport-calendar"

    @property
    def is_timed(self) -> bool:
        return "T" in self.start

    @property
    def start_dt(self) -> datetime | date:
        if self.is_timed:
            value = datetime.fromisoformat(self.start)
            return value.replace(tzinfo=ROME) if value.tzinfo is None else value.astimezone(ROME)
        return date.fromisoformat(self.start)

    @property
    def end_dt(self) -> datetime | date:
        if self.end:
            if "T" in self.end:
                value = datetime.fromisoformat(self.end)
                return value.replace(tzinfo=ROME) if value.tzinfo is None else value.astimezone(ROME)
            return date.fromisoformat(self.end)
        start = self.start_dt
        return start + (timedelta(hours=1) if isinstance(start, datetime) else timedelta(days=1))

    def material_signature(self) -> tuple[Any, ...]:
        return (
            self.start, self.end, self.circuit, self.location, self.country, self.status,
            self.broadcaster_at, self.broadcast_type_at, self.broadcast_time_at,
            self.broadcaster_it, self.broadcast_type_it, self.broadcast_time_it,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["uid"] = self.uid
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        accepted = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in raw.items() if k in accepted})
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
VALID_STATUSES = {"programmata", "da confermare", "rinviata", "cancellata", "conclusa"}


def normalize_token(value: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in value).split())


@dataclass
class Event:
    competition: str
    grand_prix: str
    session: str
    circuit: str
    location: str
    country: str
    start: str
    end: str | None = None
    status: str = "programmata"
    source_sport: str = ""
    source_sport_url: str = ""
    source_time: str = ""
    source_time_url: str = ""
    broadcaster_at: str = "Da confermare"
    broadcaster_at_url: str = ""
    broadcast_type_at: str = "da confermare"
    broadcaster_it: str = "Da confermare"
    broadcaster_it_url: str = ""
    broadcast_type_it: str = "da confermare"
    conflicts: list[str] = field(default_factory=list)
    notes: str = ""
    sequence: int = 0
    enabled: bool = True
    stable_key: str | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Stato non valido: {self.status}")
        if not self.start:
            raise ValueError("start è obbligatorio")

    @property
    def key(self) -> str:
        return self.stable_key or ":".join(
            normalize_token(v) for v in (self.competition, self.grand_prix, self.session)
        )

    @property
    def uid(self) -> str:
        digest = sha256(self.key.encode()).hexdigest()[:24]
        return f"{digest}@motorsport-calendar"

    @property
    def is_timed(self) -> bool:
        return "T" in self.start

    @property
    def start_dt(self) -> datetime | date:
        if self.is_timed:
            value = datetime.fromisoformat(self.start)
            return value.replace(tzinfo=ROME) if value.tzinfo is None else value.astimezone(ROME)
        return date.fromisoformat(self.start)

    @property
    def end_dt(self) -> datetime | date:
        if self.end:
            if "T" in self.end:
                value = datetime.fromisoformat(self.end)
                return value.replace(tzinfo=ROME) if value.tzinfo is None else value.astimezone(ROME)
            return date.fromisoformat(self.end)
        start = self.start_dt
        return start + (timedelta(hours=1) if isinstance(start, datetime) else timedelta(days=1))

    def material_signature(self) -> tuple[Any, ...]:
        return (
            self.start, self.end, self.circuit, self.location, self.country, self.status,
            self.broadcaster_at, self.broadcast_type_at, self.broadcaster_it,
            self.broadcast_type_it,
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["uid"] = self.uid
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        accepted = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in raw.items() if k in accepted})
