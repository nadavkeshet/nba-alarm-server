from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime, timedelta, timezone
import soccer_monitor as sm


def _wire(monkeypatch):
    sent = []
    monkeypatch.setattr(sm, "_send_push", lambda token, title, body, *, alert_type="alarm": sent.append({"title": title, "body": body}))
    fired = set()

    def fo(key, token):
        if key in fired:
            return False
        fired.add(key)
        return True

    monkeypatch.setattr(sm, "_fire_once_fn", fo)
    monkeypatch.setattr(sm, "_get_user_language", lambda t: "en")
    return sent


def _spec(reminder=30):
    return {"id": "teamfollow_tok_130", "user_token": "tok", "competitions": ["ligat_haal"],
            "team_ids": {"130"}, "alert_type": "alarm",
            "config": {"match_moments": {"enabled": True, "reminder_minutes": reminder}}}


def _upcoming(minutes_from_now):
    ko = (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "bsd-999", "kickoff": ko, "status": "scheduled", "competition": "ligat_haal",
            "home": {"id": "130", "name": "Maccabi"}, "away": {"id": "8", "name": "X"}}


def test_reminder_fires_inside_window(monkeypatch):
    sent = _wire(monkeypatch)
    monkeypatch.setattr(sm, "_load_follow_specs", lambda: [_spec(30)])
    import soccer_source
    monkeypatch.setattr(soccer_source, "get_team_season", lambda tid, league_slug=None, name=None: [_upcoming(20)])
    sm._reset_prematch_for_tests()
    sm._prematch_pass()
    assert any("start" in s["body"].lower() or "min" in s["body"].lower() for s in sent)


def test_reminder_not_fired_outside_window(monkeypatch):
    sent = _wire(monkeypatch)
    monkeypatch.setattr(sm, "_load_follow_specs", lambda: [_spec(30)])
    import soccer_source
    monkeypatch.setattr(soccer_source, "get_team_season", lambda tid, league_slug=None, name=None: [_upcoming(120)])  # 2h out
    sm._reset_prematch_for_tests()
    sm._prematch_pass()
    assert sent == []


def test_lineups_fire_when_confirmed(monkeypatch):
    sent = _wire(monkeypatch)
    monkeypatch.setattr(sm, "_load_follow_specs", lambda: [_spec(0)])  # reminder off, test lineups only
    import soccer_source, bzzoiro_adapter
    monkeypatch.setattr(soccer_source, "get_team_season", lambda tid, league_slug=None, name=None: [_upcoming(60)])
    monkeypatch.setattr(bzzoiro_adapter, "event_lineups", lambda eid: {"lineup_status": "confirmed"})
    sm._reset_prematch_for_tests()
    sm._prematch_pass()
    assert any("lineup" in s["title"].lower() or "lineup" in s["body"].lower() or "הרכב" in s["body"] for s in sent)
