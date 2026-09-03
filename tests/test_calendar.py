from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from motorsport_calendar.broadcast import apply_broadcasts, apply_published_broadcasts, choose_broadcast
from motorsport_calendar.discovery import discover_rounds, merge_rounds
from motorsport_calendar.ics import render_calendar
from motorsport_calendar.merge import deduplicate, merge_events
from motorsport_calendar.model import Event, ROME
from motorsport_calendar.official_epg import (
    apply_epg,
    parse_orf_epg,
    parse_servus_epg,
    parse_tvheute_epg,
    parse_tvinfo_epg,
    sky_time_for_event,
)
from motorsport_calendar.parsers import (
    classify_session,
    parse_f1_schedule_html,
    parse_f1_schedule_json,
    parse_motogp_event_json,
    parse_motogp_json,
)
from motorsport_calendar.site import render_index
from motorsport_calendar.tv8 import apply_tv8_schedule
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


def test_parsing_structured_f1_session_times():
    rounds = [{
        "competition":"Formula 1", "slug":"netherlands", "grand_prix":"Dutch Grand Prix 2026",
        "circuit":"Zandvoort", "location":"Zandvoort", "country":"Netherlands",
        "start_date":"2026-08-21",
    }]
    payload = {"MRData":{"RaceTable":{"Races":[{
        "raceName":"Dutch Grand Prix", "date":"2026-08-23", "time":"13:00:00Z",
        "FirstPractice":{"date":"2026-08-21", "time":"10:30:00Z"},
        "SprintQualifying":{"date":"2026-08-21", "time":"14:30:00Z"},
        "Sprint":{"date":"2026-08-22", "time":"10:00:00Z"},
        "Qualifying":{"date":"2026-08-22", "time":"14:00:00Z"},
    }]}}}
    events = parse_f1_schedule_json(payload, rounds)
    assert [e.session for e in events] == ["FP1", "Sprint Qualifying", "Sprint", "Qualifiche", "Gara"]
    assert events[0].start == "2026-08-21T12:30+02:00"
    assert all(e.is_timed for e in events)


def test_parsing_official_motogp_event_api_and_filtering_categories():
    rnd = {
        "competition":"MotoGP", "slug":"aragon", "grand_prix":"Aragon Grand Prix 2026",
        "circuit":"MotorLand Aragón", "location":"Alcañiz", "country":"Spain",
        "start_date":"2026-08-28",
    }
    payload = {
        "id":"event-uuid", "url":"aragon", "season":{"year":2026},
        "broadcasts":[
            {"shortname":"FP1", "name":"Free Practice Nr. 1", "type":"SESSION",
             "status":"NOT-STARTED", "date_start":"2026-08-28T10:45:00+0200",
             "date_end":"2026-08-28T11:30:00+0200", "category":{"acronym":"MGP"}},
            {"shortname":"FP1", "name":"Free Practice Nr. 1", "type":"SESSION",
             "status":"NOT-STARTED", "date_start":"2026-08-28T09:50:00+0200",
             "date_end":"2026-08-28T10:30:00+0200", "category":{"acronym":"MT2"}},
            {"shortname":"PRESS", "name":"Press Conference", "type":"MEDIA",
             "status":"NOT-STARTED", "date_start":"2026-08-27T16:00:00+0200",
             "category":{"acronym":"MGP"}},
            {"shortname":"RAC2", "name":"Grand Prix", "type":"SESSION",
             "status":"FINISHED", "date_start":"2026-08-30T14:00:00+0200",
             "date_end":"2026-08-30T14:45:00+0200", "category":{"acronym":"MGP"}},
        ],
    }
    events = parse_motogp_event_json(payload, rnd)
    assert [e.session for e in events] == ["Prove libere", "Gara"]
    assert events[0].start == "2026-08-28T10:45+02:00"
    assert events[1].end == "2026-08-30T14:45+02:00"
    assert all(e.is_timed for e in events)


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


def test_unknown_austrian_broadcaster_does_not_list_both_channels():
    from motorsport_calendar.update import _tbc_broadcast
    candidate = _tbc_broadcast(event())
    assert candidate.broadcaster_at == "Da confermare"
    text = render_calendar([candidate], "Test")
    assert "Austria: Da confermare" in text.replace("\\n", "\n")
    assert "ORF" not in text and "ServusTV" not in text


def test_published_2026_broadcast_rights_are_applied_per_weekend():
    f1 = [
        event(grand_prix="Dutch Grand Prix 2026", session="FP1", start="2026-08-21T12:30+02:00"),
        event(grand_prix="Dutch Grand Prix 2026", start="2026-08-23T15:00+02:00"),
    ]
    motogp = [
        event(competition="MotoGP", grand_prix="Aragon 2026", session="Practice", start="2026-08-28T15:00+02:00"),
        event(competition="MotoGP", grand_prix="Aragon 2026", session="Sprint", start="2026-08-29T15:00+02:00"),
        event(competition="MotoGP", grand_prix="Aragon 2026", start="2026-08-30T14:00+02:00"),
    ]
    updated = apply_published_broadcasts(f1 + motogp)
    assert updated[0].broadcaster_at == "ServusTV / ServusTV On"
    assert updated[0].broadcast_time_at == "dalle 12:15"
    assert updated[0].broadcaster_it == "Sky Sport F1 / NOW"
    assert updated[0].broadcast_time_it == ""
    assert updated[2].broadcaster_at == "ServusTV On (international stream)"
    assert updated[3].broadcaster_at == "ServusTV / ServusTV On"
    assert updated[3].broadcast_time_at == "dalle 14:30"
    assert updated[4].broadcaster_it == "Sky Sport MotoGP / NOW"
    assert updated[4].broadcast_time_at == "dalle 10:20"
    assert all(item.broadcast_type_at == "diretta" for item in updated)
    assert all(item.broadcast_type_it == "diretta" for item in updated)
    rendered = render_calendar(updated, "Test").replace("\r\n ", "")
    assert "palinsesto dalle 12:15" in rendered
    assert "Italia: Sky Sport F1 / NOW (diretta) — palinsesto da confermare" in rendered


def test_official_orf_epg_sets_only_verified_programme_start():
    candidate = event(
        grand_prix="Austrian Grand Prix 2026", session="Qualifiche",
        start="2026-06-27T16:00+02:00", broadcaster_at="ORF 1 / ORF ON",
    )
    html = '''<ul><li class="broadcast" data-start-time="2026-06-27T15:25:00+02:00"
      data-end-time="2026-06-27T17:20:00+02:00"><div class="series-title">
      <a>Formel 1: Qualifying</a></div></li></ul>'''
    programmes, _ = parse_orf_epg(html)
    updated = apply_epg([candidate], programmes, "ORF", "https://tv.orf.at/")[0]
    assert updated.broadcast_time_at == "dalle 15:25"


def test_official_servus_epg_parser_and_application():
    candidate = event(
        grand_prix="Dutch Grand Prix 2026", session="Gara",
        start="2026-08-23T15:00+02:00", broadcaster_at="ServusTV / ServusTV On",
    )
    payload = r'''\"title\":\"Formel 1: GP der Niederlande - Rennen\",
      \"start_time\":\"2026-08-23T12:30:00.000Z\",
      \"end_time\":\"2026-08-23T15:30:00.000Z\"}'''
    programmes = parse_servus_epg(payload)
    updated = apply_epg([candidate], programmes, "ServusTV", "https://www.servustv.com/de/epg")[0]
    assert updated.broadcast_time_at == "dalle 14:30"


def test_tvheute_fallback_selects_servus_preview_and_orf_programme():
    servus_page = """
      ServusTV SPORT 15:30 16:00 25' Formel 1 - Pirelli Grand Prix von Italien
      FORMEL 1 Qualifying: Vorbericht ServusTV SPORT 16:00 17:00 50'
      Formel 1 - Pirelli Grand Prix von Italien FORMEL 1 Qualifying
    """
    orf_page = """
      ORF1 SPORT 12:20 13:40 80' Formel 1 Großer Preis von Spanien 2026
      FORMEL 1 F1 3.Training ORF1 SPORT 15:55 17:35 100'
      Formel 1 Großer Preis von Spanien 2026 FORMEL 1 F1 Qualifying
    """
    servus = event(
        grand_prix="Italian Grand Prix 2026", session="Qualifiche",
        start="2026-09-05T16:00+02:00", broadcaster_at="ServusTV / ServusTV On",
    )
    orf = event(
        grand_prix="Madrid Grand Prix 2026", session="FP3",
        start="2026-09-12T12:30+02:00", broadcaster_at="ORF 1 / ORF ON",
    )
    servus_rows = parse_tvheute_epg(servus_page, servus.start_dt.date(), "ServusTV")
    orf_rows = parse_tvheute_epg(orf_page, orf.start_dt.date(), "ORF1")
    apply_epg([servus], servus_rows, "ServusTV", "https://tvheute.at/servustv-programm/05-09-2026-im-tv")
    apply_epg([orf], orf_rows, "ORF", "https://tvheute.at/orf1-programm/12-09-2026-im-tv")
    assert servus.broadcast_time_at == "dalle 15:30"
    assert orf.broadcast_time_at == "dalle 12:20"


def test_tvheute_fallback_does_not_replace_primary_or_international_stream():
    rows = parse_tvheute_epg(
        "ServusTV SPORT 13:00 15:00 99' MotoGP Countdown",
        date(2026, 9, 20), "ServusTV",
    )
    primary = event(
        competition="MotoGP", session="Gara", start="2026-09-20T14:00+02:00",
        broadcaster_at="ServusTV / ServusTV On", broadcast_time_at="dalle 12:55",
    )
    stream = event(
        competition="MotoGP", session="Gara", start="2026-09-20T14:00+02:00",
        broadcaster_at="ServusTV On (international stream)",
    )
    apply_epg([primary], rows, "ServusTV", "fallback", only_missing=True)
    # The fetcher excludes streaming-only rows; model that invariant here by
    # not applying linear-TV fallback data to the streaming event.
    assert primary.broadcast_time_at == "dalle 12:55"
    assert stream.broadcast_time_at == ""


def test_tvinfo_fallback_reads_requested_date_first_column():
    page = """
      <table>
        <tr><td>Sa 5.9.</td><td>So 6.9.</td><td>Mo 7.9.</td><td>Di 8.9.</td></tr>
        <tr>
          <td>12:13 <a>Servus Wetter</a> 12:15 <a>Formel 1 - Pirelli Grand Prix von Italien 3. Freies Training Folge 13</a></td>
          <td>13:00 <a>Formel 1 - Pirelli Grand Prix von Italien Rennen: Vorbericht Folge 13</a></td>
          <td>15:00 <a>Servus um 3</a></td>
          <td>16:00 <a>Quizjagd</a></td>
        </tr>
        <tr>
          <td>15:30 <a>Formel 1 - Pirelli Grand Prix von Italien Qualifying: Vorbericht Folge 13</a></td>
          <td>15:00 <a>Formel 1 - Pirelli Grand Prix von Italien Das Rennen Folge 13</a></td>
        </tr>
      </table>
    """
    candidate = event(
        grand_prix="Italian Grand Prix 2026", session="Qualifiche",
        start="2026-09-05T16:00+02:00", broadcaster_at="ServusTV / ServusTV On",
    )
    rows = parse_tvinfo_epg(page, candidate.start_dt.date())
    apply_epg([candidate], rows, "ServusTV", "https://www.tvinfo.de/tv-programm/servustv/05.09.2026")
    assert candidate.broadcast_time_at == "dalle 15:30"
    fp3 = event(
        grand_prix="Italian Grand Prix 2026", session="FP3",
        start="2026-09-05T12:30+02:00", broadcaster_at="ServusTV / ServusTV On",
    )
    apply_epg([fp3], rows, "ServusTV", "https://www.tvinfo.de/tv-programm/servustv/05.09.2026")
    assert fp3.broadcast_time_at == "dalle 12:15"


def test_sporting_start_is_never_used_as_sky_or_servus_airtime():
    candidate = event(
        grand_prix="Spanish Grand Prix 2026", start="2026-09-27T15:00+02:00",
    )
    updated = apply_published_broadcasts([candidate])[0]
    assert updated.broadcaster_at == "ServusTV / ServusTV On"
    assert updated.broadcast_time_at == ""
    assert updated.broadcaster_it == "Sky Sport F1 / NOW"
    assert updated.broadcast_time_it == ""


def test_sky_official_f1_guide_supplies_dated_session_times():
    page = """
      Venerdì 21 agosto alle ore 12:30 la prima sessione di prove libere;
      alle ore 16:30 la qualifica Sprint.
      Sabato 22 agosto alle ore 12:00 partirà la Sprint Race, mentre alle
      ore 16:00 si terranno le qualifiche per la gara di domenica.
      Domenica 23 agosto alle ore 15:00 la gara del Gran Premio d'Olanda.
    """
    events = [
        event(session="FP1", start="2026-08-21T12:30+02:00"),
        event(session="Sprint Qualifying", start="2026-08-21T16:30+02:00"),
        event(session="Sprint", start="2026-08-22T12:00+02:00"),
        event(session="Qualifiche", start="2026-08-22T16:00+02:00"),
        event(session="Gara", start="2026-08-23T15:00+02:00"),
    ]
    assert [sky_time_for_event(item, page) for item in events] == [
        "dalle 12:30", "dalle 16:30", "dalle 12:00", "dalle 16:00", "dalle 15:00",
    ]


def test_sky_official_motogp_guide_supplies_combined_qualifying_start():
    page = """
      Venerdì 28 agosto con le prime prove libere alle ore 10:45, mentre alle
      ore 15:00 sono in programma le Pre-qualifiche. Sabato 29 agosto le
      qualifiche dalle ore 10:50, poi alle ore 15:00 scatta la Sprint Race.
      Domenica 30 agosto la gara lunga è alle ore 14:00.
    """
    events = [
        event(competition="MotoGP", session="Prove libere", start="2026-08-28T10:45+02:00"),
        event(competition="MotoGP", session="Practice", start="2026-08-28T15:00+02:00"),
        event(competition="MotoGP", session="Q1", start="2026-08-29T10:50+02:00"),
        event(competition="MotoGP", session="Q2", start="2026-08-29T11:15+02:00"),
        event(competition="MotoGP", session="Sprint", start="2026-08-29T15:00+02:00"),
        event(competition="MotoGP", session="Gara", start="2026-08-30T14:00+02:00"),
    ]
    assert [sky_time_for_event(item, page) for item in events] == [
        "dalle 10:45", "dalle 15:00", "dalle 10:50", "dalle 10:50", "dalle 15:00", "dalle 14:00",
    ]


def test_orf_rights_do_not_invent_an_airtime_from_the_session_start():
    candidate = event(
        grand_prix="Madrid Grand Prix 2026",
        start="2026-09-13T15:00+02:00",
    )
    updated = apply_published_broadcasts([candidate])[0]
    assert updated.broadcaster_at == "ORF 1 / ORF ON"
    assert updated.broadcast_type_at == "diretta"
    assert updated.broadcast_time_at == ""
    rendered = render_calendar([updated], "Test").replace("\r\n ", "")
    assert "Austria: ORF 1 / ORF ON (diretta) — palinsesto da confermare" in rendered


def test_verified_orf_weekend_uses_official_programme_start():
    candidate = event(
        grand_prix="British Grand Prix 2026",
        start="2026-07-05T16:00+02:00",
    )
    updated = apply_published_broadcasts([candidate])[0]
    assert updated.broadcaster_at == "ORF 1 / ORF ON"
    assert updated.broadcast_time_at == "dalle 15:25"
    assert updated.broadcaster_at_url == "https://tv.orf.at/stories/260705_formel1_gb100.html"


def test_live_tv8_is_preferred_over_live_pay_tv():
    choices = [
        {"name":"Sky Sport", "type":"diretta", "access":"a pagamento"},
        {"name":"NOW", "type":"diretta", "access":"a pagamento"},
        {"name":"TV8", "type":"diretta", "access":"gratuita"},
    ]
    selected = choose_broadcast(choices, "IT")
    assert selected["name"] == "TV8" and selected["type"] == "diretta"
    updated = apply_broadcasts(event(), [], [selected])
    assert updated.broadcaster_it == "TV8"


def test_live_sky_is_preferred_over_delayed_tv8():
    choices = [
        {"name":"TV8", "type":"differita", "access":"gratuita"},
        {"name":"Sky Sport", "type":"diretta", "access":"a pagamento"},
        {"name":"NOW", "type":"diretta", "access":"a pagamento"},
    ]
    selected = choose_broadcast(choices, "IT")
    assert selected["name"] == "Sky Sport" and selected["type"] == "diretta"


def test_tv8_epg_selects_live_monza_and_ignores_delayed_race():
    qualifying = event(
        grand_prix="Italian Grand Prix 2026", circuit="Monza",
        location="Monza", country="Italy", session="Qualifiche",
        start="2026-09-05T16:00+02:00",
    )
    race = event(
        grand_prix="Italian Grand Prix 2026", circuit="Monza",
        location="Monza", country="Italy", session="Gara",
        start="2026-09-06T15:00+02:00",
    )
    schedules = {
        "2026-09-05": [{
            "title":{"text":"GP Italia Qualifiche"},
            "description":{"text":"Dal circuito di Monza"},
            "badge":{"label":{"text":"16:00 - 17:15"}},
        }],
        "2026-09-06": [{
            "title":{"text":"GP Italia Gara"},
            "description":{"text":"Dal circuito di Monza"},
            "badge":{"label":{"text":"18:00 - 19:45"}},
        }],
    }
    updated = apply_tv8_schedule([qualifying, race], schedules)
    assert updated[0].broadcaster_it == "TV8"
    assert updated[0].broadcast_type_it == "diretta"
    assert updated[0].broadcast_time_it == "dalle 16:00"
    assert updated[1].broadcaster_it != "TV8"


def test_tv8_epg_recognizes_combined_live_motogp_qualifying_block():
    q2 = event(
        competition="MotoGP", grand_prix="San Marino and Rimini Riviera Grand Prix 2026",
        circuit="Misano World Circuit", location="Misano", country="Italy",
        session="Q2", start="2026-09-12T11:15+02:00",
    )
    schedules = {"2026-09-12": [{
        "title":{"text":"GP San Marino: Qualifiche"},
        "description":{"text":"Dal circuito di Misano"},
        "badge":{"label":{"text":"10:50 - 11:50"}},
    }]}
    updated = apply_tv8_schedule([q2], schedules)[0]
    assert updated.broadcaster_it == "TV8"
    assert updated.broadcast_time_it == "dalle 10:50"


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
    assert any(e.is_timed for e in events)


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


def test_site_renders_series_logos_in_header_and_event_cards(tmp_path):
    source = Path(__file__).parents[1]
    shutil.copytree(source / "templates", tmp_path / "templates")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/events.json").write_text(json.dumps({"updated_at": "2026-08-11T12:00:00+02:00"}))
    events = [
        event(start="2099-03-29T15:00"),
        event(competition="MotoGP", grand_prix="Test MotoGP", start="2099-04-01T14:00"),
    ]
    render_index(tmp_path, events)
    page = (tmp_path / "index.html").read_text()
    assert page.count('src="assets/f1-logo.png"') == 2
    assert page.count('src="assets/motogp-logo.png"') == 2
    assert 'alt="Formula 1"' in page and 'alt="MotoGP"' in page


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
