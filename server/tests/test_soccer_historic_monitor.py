"""Monitor-side wiring for Football Historic Moments: _evaluate_historic fires
each enabled category once per (user, match, moment), respects disabled
categories, and dedups across ticks / multiple follows."""
import soccer_monitor as sm


def _wire(monkeypatch):
    sent = []
    fired = set()
    monkeypatch.setattr(sm, "_send_push",
                        lambda token, title, body, *, alert_type="alarm":
                            sent.append({"token": token, "title": title, "body": body, "alert_type": alert_type}))
    monkeypatch.setattr(sm, "_fire_once_fn",
                        lambda key, token: (key + token not in fired) and (fired.add(key + token) or True))
    monkeypatch.setattr(sm, "_get_user_language", lambda t: "en")
    return sent


def _match_with_hat_trick():
    # One player scores 3 → hat_trick; margin 3 (not a rout).
    return {
        "status": "live", "minute": 70, "competition": "premier_league",
        "home": {"id": "10", "name": "Home", "score": 3},
        "away": {"id": "20", "name": "Away", "score": 0},
        "incidents": [
            {"type": "goal", "minute": 10, "is_home": True, "player": "Ace", "player_id": 99, "home_score": 1, "away_score": 0},
            {"type": "goal", "minute": 40, "is_home": True, "player": "Ace", "player_id": 99, "home_score": 2, "away_score": 0},
            {"type": "goal", "minute": 65, "is_home": True, "player": "Ace", "player_id": 99, "home_score": 3, "away_score": 0},
        ],
    }


def test_enabled_category_fires_once_and_dedups(monkeypatch):
    sent = _wire(monkeypatch)
    cfgs = {"u1": {"alert_type": "alarm", "categories": {"hat_trick"}}}
    follows = {"u1": {"comps": {"premier_league"}, "teams": set()}}
    detail = _match_with_hat_trick()
    sm._evaluate_historic("bsd-1", detail, "10", "20", cfgs, follows)
    sm._evaluate_historic("bsd-1", detail, "10", "20", cfgs, follows)  # same tick repeat → dedup
    assert len(sent) == 1
    assert sent[0]["alert_type"] == "alarm"


def test_disabled_category_is_suppressed(monkeypatch):
    sent = _wire(monkeypatch)
    cfgs = {"u1": {"alert_type": "silent", "categories": {"rout"}}}  # rout NOT present (margin 3)
    follows = {"u1": {"comps": {"premier_league"}, "teams": set()}}
    sm._evaluate_historic("bsd-1", _match_with_hat_trick(), "10", "20", cfgs, follows)
    assert sent == []  # hat_trick present but not enabled; rout enabled but not present


def test_team_follow_matches_and_non_follower_excluded(monkeypatch):
    sent = _wire(monkeypatch)
    cfgs = {
        "follower":  {"alert_type": "silent", "categories": {"hat_trick"}},
        "stranger":  {"alert_type": "silent", "categories": {"hat_trick"}},
    }
    follows = {
        "follower": {"comps": set(), "teams": {"20"}},      # follows the away team
        "stranger": {"comps": {"la_liga"}, "teams": {"77"}},  # unrelated
    }
    sm._evaluate_historic("bsd-1", _match_with_hat_trick(), "10", "20", cfgs, follows)
    assert [s["token"] for s in sent] == ["follower"]
