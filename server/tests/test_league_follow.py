"""Tests for league-wide follow alerts (basketball radar honoring FavoriteLeagues).

The basketball radar (`monitor._run_radar_tick_for_user`) must treat a followed
league like a favorite team applied to EVERY game in the league: fire when a game
meets the league's criteria, respect precedence (favorite team overrides league
overrides global radar), and treat a "Just Follow" (alert_type=silent) league as
display-only (no alerts).
"""
import monitor


def _nba_event(diff: int, period: int = 4, league: str = "nba",
               home_id: str = "2", away_id: str = "1") -> dict:
    home_score = 100
    away_score = 100 - diff
    return {
        "id": "401",
        "name": "Away @ Home",
        "league": league,
        "competitions": [{
            "status": {"type": {"state": "in", "detail": f"Q{period}"}, "period": period},
            "competitors": [
                {"team": {"id": away_id, "abbreviation": "AWY"}, "homeAway": "away", "score": str(away_score)},
                {"team": {"id": home_id, "abbreviation": "HOM"}, "homeAway": "home", "score": str(home_score)},
            ],
        }],
    }


def _prefs(*, leagues=None, teams=None, radar=None):
    return {
        "FavoritePlayers": {},
        "FavoriteTeams":   teams or [],
        "FavoriteLeagues": leagues or [],
        "GlobalGameRadar": radar or {"enabled": False},
        "HistoricalMode":  {"enabled": False, "categories": {}},
    }


def _run(monkeypatch, event, prefs):
    sent = []
    monkeypatch.setattr(monitor, "_send_push_notification",
                        lambda **kw: sent.append(kw))
    monkeypatch.setattr(monitor, "_fire_once", lambda key, token="": True)
    monkeypatch.setattr(monitor, "_has_manual_alarm", lambda gid: False)
    monitor._run_radar_tick_for_user("tok", "classic_alarm", prefs, [event], {})
    return sent


LEAGUE_ALARM = {"league": "nba", "max_diff": 5, "start_quarter": 4,
                "alert_type": "alarm", "notify_at_start": False}


def test_followed_league_fires_on_close_q4(monkeypatch):
    sent = _run(monkeypatch, _nba_event(diff=3), _prefs(leagues=[LEAGUE_ALARM]))
    assert len(sent) == 1
    assert sent[0]["alert_type"] == "alarm"


def test_followed_league_no_fire_on_blowout(monkeypatch):
    sent = _run(monkeypatch, _nba_event(diff=20), _prefs(leagues=[LEAGUE_ALARM]))
    assert sent == []


def test_followed_league_no_fire_before_start_quarter(monkeypatch):
    sent = _run(monkeypatch, _nba_event(diff=3, period=2), _prefs(leagues=[LEAGUE_ALARM]))
    assert sent == []


def test_just_follow_league_is_silent_no_alert(monkeypatch):
    just_follow = {**LEAGUE_ALARM, "alert_type": "silent"}
    sent = _run(monkeypatch, _nba_event(diff=3), _prefs(leagues=[just_follow]))
    assert sent == []


def test_favorite_team_overrides_followed_league(monkeypatch):
    # Team criteria is tighter (diff<=1): a diff-3 game must NOT fire even though
    # the followed league (diff<=5) would — the fav-team config wins for its game.
    team = {"team_id": "2", "team_name": "Home", "league": "nba",
            "max_diff": 1, "start_quarter": 4, "alert_type": "alarm"}
    sent = _run(monkeypatch, _nba_event(diff=3),
                _prefs(leagues=[LEAGUE_ALARM], teams=[team]))
    assert sent == []


def test_followed_league_only_applies_to_its_league(monkeypatch):
    # A followed nba league must not fire on a euroleague game.
    sent = _run(monkeypatch, _nba_event(diff=3, league="euroleague"),
                _prefs(leagues=[LEAGUE_ALARM]))
    assert sent == []
