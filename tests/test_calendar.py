from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from motorsport_calendar.broadcast import apply_broadcasts, choose_broadcast
from motorsport_calendar.discovery import discover_rounds, merge_rounds
from motorsport_calendar.ics import render_calendar
from motorsport_calendar.merge import deduplicate, merge_events
from motorsport_calendar.model import Event, ROME
from motorsport_calendar.parsers import classify_session, parse_f1_schedule_html, parse_motogp_json
from motorsport_calendar.update import (
    competitions_with_current_season,
    current_and_future_events,
    current_and_future_rounds,
    events_from_rounds,
    generate,
    validate,
)


def event(**changes) -> Event:
    values = dict(
        competition="Formula 1", grand_prix="Test GP", session="Gara",
        circuit="Test Circuit", location="Roma", country="Italia",
        start="2026-03-29T15:00", source_sport="Official", source_sport_url="https://example.com",
        source_time="Official", source_time_url="https://example.com",
    )
    values.update(changes)
    return Event(**values)


def test_parsing_formula_1_fixture():
    text = (Path(__file__).parent / "fixtures/f1.html").read_text()
    events = parse_f1_schedule_html(text, year=2026, slug="netherlands")
    assert [e.session for e in events] == ["FP1", "Sprint Qualifying", "Sprint", "Qualifiche", "Gara"]
    assert events[0].start == "2026-08-21T12:30+02:00"


def test_parsing_motogp_and_filters_lower_classes():
    payload = {"sessions": [
        {"category":"MotoGP", "grand_prix":"Aragon GP", "session":"Q1", "start":"2026-08-29T08:50:00Z", "circuit":"MotorLand"},
        {"category":"Moto2", "grand_prix":"Aragon GP", "session":"Race", "start":"2026-08-30T10:00:00Z"},
    ]}
    events = parse_motogp_json(payload)
    assert len(events) == 1 and events[0].competition == "MotoGP" and events[0].session == "Q1"


@pytest.mark.parametrize("raw,expected", [
    ("Practice 1", "FP1"), ("Free Practice", "Prove libere"), ("Q2", "Q2"),
    ("Sprint Shootout", "Sprint Shootout"), ("Sprint", "Sprint"), ("Race", "Gara"),
])
def test_session_distinction(raw, expected):
    assert classify_session(raw) == expected


def test_europe_rome_dst():
    winter = datetime(2026, 1, 10, 12, tzinfo=ROME)
    summer = datetime(2026, 7, 10, 12, tzinfo=ROME)
    assert winter.utcoffset().total_seconds() == 3600
    assert summer.utcoffset().total_seconds() == 7200


def test_uid_stable_when_moved_by_months():
    before = event(start="2026-03-01T15:00")
    after = event(start="2026-11-01T15:00")
    assert before.uid == after.uid


def test_sequence_increments_for_material_change_and_records_move():
    before = event(sequence=4)
    after = event(start="2026-06-21T16:00")
    merged = merge_events([after], [], [before])[0]
    assert merged.sequence == 5 and "Riprogrammata" in merged.notes


def test_sequence_does_not_increment_for_source_only_change():
    before = event(sequence=2, source_time="A")
    after = event(source_time="B")
    assert merge_events([after], [], [before])[0].sequence == 2


def test_deduplication_and_conflict_tracking():
    a, b = event(source_time="A"), event(start="2026-03-29T16:00", source_time="B")
    result = deduplicate([a, b])
    assert len(result) == 1 and result[0].conflicts


def test_postponed_cancelled_and_tbc_rendering():
    values = [event(status="rinviata"), event(session="FP1", status="cancellata"), event(session="FP2", start="2026-04-01", status="da confermare")]
    text = render_calendar(values, "Test", datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert "RINVIATA" in text and "CANCELLATA" in text and "TBC" in text
    assert "DTSTART;VALUE=DATE:20260401" in text


def test_confirmed_round_date_does_not_mark_whole_event_tbc():
    rounds = [{
        "competition":"Formula 1", "slug":"italy", "grand_prix":"Italian GP",
        "circuit":"Monza", "location":"Monza", "country":"Italy", "start_date":"2026-09-04",
    }]
    generated = events_from_rounds(rounds, today=datetime(2026, 8, 11).date())
    assert generated and all(e.status == "programmata" for e in generated)
    text = render_calendar(generated, "Test", datetime(2026, 8, 11, tzinfo=timezone.utc))
    assert "SUMMARY:TBC" not in text
    assert "Orario: Da confermare" in text.replace("\\n", "\n")


def test_manual_override_and_disable():
    auto = [event(), event(session="FP1")]
    override = event(start="2026-04-01T10:00", broadcaster_it="TV8", broadcast_type_it="diretta")
    disabled = event(session="FP1", enabled=False)
    merged = merge_events(auto, [override, disabled])
    assert len(merged) == 1 and merged[0].start == "2026-04-01T10:00" and merged[0].broadcaster_it == "TV8"


def test_orf_and_servus_priority():
    choices = [{"name":"ServusTV", "type":"diretta"}, {"name":"ORF 1", "type":"diretta"}]
    assert choose_broadcast(choices, "AT")["name"] == "ORF 1"
    assert choose_broadcast([choices[0]], "AT")["name"] == "ServusTV"


def test_tv8_direct_not_confused_with_delayed_and_free_preferred():
    choices = [
        {"name":"Sky Sport", "type":"diretta", "access":"a pagamento"},
        {"name":"TV8", "type":"differita", "access":"gratuita"},
    ]
    selected = choose_broadcast(choices, "IT")
    assert selected["name"] == "TV8" and selected["type"] == "differita"
    updated = apply_broadcasts(event(), [], [selected])
    assert updated.broadcast_type_it == "differita"


def test_validation_rejects_empty_or_one_series():
    with pytest.raises(ValueError):
        validate([], {})
    with pytest.raises(ValueError):
        validate([event()], {"x":"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"})


def test_source_fallback_keeps_official_round_dates(tmp_path, monkeypatch):
    import urllib.error
    from motorsport_calendar import update
    source = Path(__file__).parents[1]
    shutil.copytree(source / "data", tmp_path / "data")
    monkeypatch.setattr(update, "discover_rounds", lambda rounds, today: rounds)
    monkeypatch.setattr(update, "fetch_f1_details", lambda rounds: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    events = generate(tmp_path, online=True, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
    assert any(e.competition == "Formula 1" for e in events)
    assert any(e.competition == "MotoGP" for e in events)


def test_protection_of_last_valid_calendar(tmp_path, monkeypatch):
    from motorsport_calendar import update
    old = event()
    monkeypatch.setattr(update, "events_from_rounds", lambda rounds, today=None: [])
    monkeypatch.setattr(update, "load_events", lambda path: [old] if path.name == "events.json" else [])
    monkeypatch.setattr(update, "load_round_catalog", lambda path: {"rounds": []})
    with pytest.raises(ValueError):  # refuses to replace valid output with incomplete data
        generate(tmp_path, online=False)


def test_generates_three_ics_and_events_json(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "data", tmp_path / "data")
    events = generate(tmp_path, online=False, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
    assert events
    for name in ("calendar.ics", "f1.ics", "motogp.ics"):
        text = (tmp_path / name).read_text()
        assert text.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in text
    payload = json.loads((tmp_path / "data/events.json").read_text())
    assert payload["timezone"] == "Europe/Rome"
    assert any(e["competition"] == "Formula 1" for e in payload["events"])
    assert any(e["competition"] == "MotoGP" for e in payload["events"])


def test_main_race_has_two_alarms_other_session_one():
    text = render_calendar([event(), event(session="FP1")], "Test")
    assert text.count("BEGIN:VALARM") == 3


def test_future_season_is_discovered_and_old_season_is_preserved():
    existing = [{
        "competition":"Formula 1", "slug":"italy", "grand_prix":"Italian Grand Prix 2026",
        "circuit":"Monza", "location":"Monza", "country":"Italy", "start_date":"2026-09-04"
    }]
    jolpica_2027 = {
        "MRData":{"RaceTable":{"Races":[{
            "raceName":"Australian Grand Prix", "date":"2027-03-07",
            "FirstPractice":{"date":"2027-03-05"},
            "Circuit":{"circuitId":"albert_park", "circuitName":"Albert Park Grand Prix Circuit",
                       "Location":{"locality":"Melbourne", "country":"Australia"}}
        }]}}
    }
    motogp_2027 = """<script type="application/json">{"events":[{
      "category":"MotoGP", "eventName":"Grand Prix of Thailand", "startDate":"2027-02-26",
      "circuitName":"Chang International Circuit", "city":"Buriram", "countryName":"Thailand",
      "slug":"thailand"}]}</script>"""

    def fetcher(url: str) -> str:
        if "jolpi" in url and "2027" in url:
            return json.dumps(jolpica_2027)
        if "motogp.com" in url and "2027" in url:
            return motogp_2027
        if "jolpi" in url:
            return json.dumps({"MRData":{"RaceTable":{"Races":[]}}})
        return "<html></html>"

    result = discover_rounds(existing, datetime(2026, 8, 11).date(), fetcher)
    assert any(r["start_date"].startswith("2026") for r in result)
    assert any(r["competition"] == "Formula 1" and r["start_date"].startswith("2027") for r in result)
    assert any(r["competition"] == "MotoGP" and r["start_date"].startswith("2027") for r in result)
    assert not any("test" in r["grand_prix"].lower() for r in result)


def test_round_merge_never_deletes_and_enriches_existing():
    old = [{"competition":"MotoGP", "slug":"italy", "grand_prix":"Italian GP 2026",
            "circuit":"TBC", "location":"Scarperia", "country":"Italy", "start_date":"2026-05-29"}]
    new = [{"competition":"MotoGP", "slug":"italy", "grand_prix":"Italian GP 2026",
            "circuit":"Mugello", "location":"Scarperia", "country":"Italy", "start_date":"2026-05-29"},
           {"competition":"MotoGP", "slug":"italy", "grand_prix":"Italian GP 2027",
            "circuit":"Mugello", "location":"Scarperia", "country":"Italy", "start_date":"2027-06-04"}]
    merged = merge_rounds(old, new)
    assert len(merged) == 2
    assert next(r for r in merged if r["start_date"].startswith("2026"))["circuit"] == "Mugello"


def test_round_merge_deduplicates_provider_specific_slugs():
    official = [{"competition":"Formula 1", "slug":"australia", "grand_prix":"Australian GP 2027",
                 "circuit":"Albert Park", "location":"Melbourne", "country":"Australia", "start_date":"2027-03-05"}]
    fallback = [{"competition":"Formula 1", "slug":"albert_park", "grand_prix":"Australian GP 2027",
                 "circuit":"Albert Park", "location":"Melbourne", "country":"Australia", "start_date":"2027-03-05"}]
    merged = merge_rounds(official, fallback)
    assert len(merged) == 1
    assert merged[0]["slug"] == "australia"


def test_round_merge_tolerates_thursday_friday_weekend_boundaries():
    official = [{"competition":"Formula 1", "slug":"las-vegas", "grand_prix":"Las Vegas GP 2026",
                 "circuit":"Las Vegas Strip", "location":"Las Vegas", "country":"USA", "start_date":"2026-11-19"}]
    fallback = [{"competition":"Formula 1", "slug":"vegas", "grand_prix":"Las Vegas GP 2026",
                 "circuit":"Las Vegas Strip", "location":"Las Vegas", "country":"USA", "start_date":"2026-11-20"}]
    merged = merge_rounds(official, fallback)
    assert len(merged) == 1
    assert merged[0]["start_date"] == "2026-11-19"


def test_generation_is_idempotent_when_events_do_not_change(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "data", tmp_path / "data")
    generate(tmp_path, online=False, now=datetime(2026, 8, 11, 8, tzinfo=timezone.utc))
    tracked = ("calendar.ics", "f1.ics", "motogp.ics", "data/events.json", "data/rounds.json")
    before = {name: (tmp_path / name).read_bytes() for name in tracked}
    generate(tmp_path, online=False, now=datetime(2026, 8, 11, 14, tzinfo=timezone.utc))
    after = {name: (tmp_path / name).read_bytes() for name in tracked}
    assert before == after


def test_completed_seasons_are_removed_at_new_year():
    rounds = [
        {"competition":"Formula 1", "slug":"old", "start_date":"2026-12-04"},
        {"competition":"Formula 1", "slug":"current", "start_date":"2027-03-05"},
        {"competition":"MotoGP", "slug":"future", "start_date":"2028-02-25"},
    ]
    kept_rounds = current_and_future_rounds(rounds, datetime(2027, 1, 1).date())
    assert [r["slug"] for r in kept_rounds] == ["current", "future"]
    kept_events = current_and_future_events([
        event(start="2026-12-04T15:00"), event(start="2027-03-07T15:00", grand_prix="Current GP")
    ], datetime(2027, 1, 1).date())
    assert len(kept_events) == 1 and kept_events[0].grand_prix == "Current GP"


def test_old_season_is_retained_until_replacement_is_available():
    rounds = [
        {"competition":"Formula 1", "slug":"old-f1", "start_date":"2026-12-04"},
        {"competition":"MotoGP", "slug":"current-motogp", "start_date":"2027-02-26"},
    ]
    active = competitions_with_current_season(rounds, datetime(2027, 1, 1).date())
    assert active == {"MotoGP"}


def test_generation_prunes_previous_year_when_new_seasons_exist(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "data", tmp_path / "data")
    catalog_path = tmp_path / "data/rounds.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["rounds"].extend([
        {
            "competition":"Formula 1", "slug":"australia-2027", "grand_prix":"Australian GP 2027",
            "circuit":"Albert Park", "location":"Melbourne", "country":"Australia",
            "start_date":"2027-03-05",
        },
        {
            "competition":"MotoGP", "slug":"thailand-2027", "grand_prix":"Thailand GP 2027",
            "circuit":"Chang International Circuit", "location":"Buriram", "country":"Thailand",
            "start_date":"2027-02-26",
        },
    ])
    catalog_path.write_text(json.dumps(catalog))

    generated = generate(tmp_path, online=False, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert {e.competition for e in generated} == {"Formula 1", "MotoGP"}
    assert all(e.start_dt.year >= 2027 for e in generated)
    saved_rounds = json.loads(catalog_path.read_text())["rounds"]
    assert all(int(r["start_date"][:4]) >= 2027 for r in saved_rounds)


def test_generation_keeps_last_valid_seasons_until_new_ones_exist(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "data", tmp_path / "data")
    generated = generate(tmp_path, online=False, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert {e.competition for e in generated} == {"Formula 1", "MotoGP"}
    assert any(e.start_dt.year == 2026 for e in generated)
