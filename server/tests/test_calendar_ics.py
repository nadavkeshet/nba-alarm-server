from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calendar_ics as ci


def _soccer(comp="premier_league", home="Arsenal", away="Chelsea"):
    return {"_sport": "soccer", "id": "bsd-1", "competition": comp,
            "kickoff": "2026-06-12T18:00:00Z", "status": "notstarted",
            "home": {"name": home}, "away": {"name": away}, "venue": {"id": 9}}


def _bball():
    return {"_sport": "basketball", "id": "bsd-bb-9", "league": "nba",
            "date": "2026-06-12T23:30:00Z",
            "status": {"type": {"detail": "Scheduled"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": "New York Knicks"}},
                {"homeAway": "away", "team": {"displayName": "Boston Celtics"}}]}]}


def test_structure_and_crlf():
    out = ci.build_calendar([_soccer()], cal_name="Test")
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.rstrip().endswith("END:VCALENDAR")
    assert out.count("BEGIN:VEVENT") == 1
    assert "\r\n" in out and out.endswith("\r\n")


def test_dtstart_dtend_utc():
    out = ci.build_calendar([_soccer()])
    assert "DTSTART:20260612T180000Z" in out
    assert "DTEND:20260612T195500Z" in out          # +115 min
    assert "UID:bsd-1@clutchalarm.com" in out


def test_bball_event():
    out = ci.build_calendar([_bball()])
    assert "DTSTART:20260612T233000Z" in out
    assert "DTEND:20260613T020000Z" in out          # +150 min crosses midnight
    assert "SUMMARY:Boston Celtics @ New York Knicks" in out
    assert "NBA" in out


def test_world_cup_flags_in_title():
    out = ci.build_calendar([_soccer(comp="world_cup", home="France", away="Argentina")])
    assert "🇫🇷 France vs Argentina 🇦🇷" in out


def test_escaping_special_chars():
    out = ci.build_calendar([_soccer(home="A; B, C")])
    assert "A\\; B\\, C" in out


def test_fold_long_multibyte_summary():
    long_name = "🇫🇷 " + "Réal Madríd " * 8                # forces folding past 75 octets w/ multibyte
    out = ci.build_calendar([_soccer(comp="world_cup", home=long_name, away="X")])
    # Every physical line must be <= 75 octets; continuation lines start with a space.
    for line in out.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, repr(line)


def test_parse_utc_offsets():
    assert ci._parse_utc("2026-06-12T18:00:00+00:00") is not None
    assert ci._parse_utc("2026-06-12T18:00:00Z") is not None
    assert ci._parse_utc(None) is None
    assert ci._parse_utc("garbage") is None


def test_skips_unparseable_and_unknown_sport():
    bad = {"_sport": "soccer", "id": "x", "kickoff": None}
    unknown = {"_sport": "tennis", "id": "y"}
    out = ci.build_calendar([bad, unknown, _soccer()])
    assert out.count("BEGIN:VEVENT") == 1
