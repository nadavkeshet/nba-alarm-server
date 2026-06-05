"""Team/competition follows apply the SAME criteria as a per-match alarm to
every (or the team's) live match. These cover the config → synthesized-alarm
expansion and that the real per-match evaluators fire off those synth alarms."""
import soccer_monitor as sm


def _wire(monkeypatch):
    sent = []
    fired = set()
    monkeypatch.setattr(sm, "_send_push",
                        lambda token, title, body, *, alert_type="alarm": sent.append((token, alert_type)))
    monkeypatch.setattr(sm, "_fire_once_fn",
                        lambda key, token: (key + token not in fired) and (fired.add(key + token) or True))
    monkeypatch.setattr(sm, "_get_user_language", lambda t: "en")
    return sent


def _spec(config=None, *, alert_type="alarm", team_ids=None, threshold=7.0, notify_at_kickoff=False):
    return {
        "id": "compfollow_u_premier_league", "user_token": "u",
        "competitions": ["premier_league"], "team_ids": team_ids,
        "alert_type": alert_type, "threshold": threshold,
        "notify_at_kickoff": notify_at_kickoff, "config": config or {},
    }


def test_full_config_expands_to_every_enabled_criterion(monkeypatch):
    monkeypatch.setattr(sm.ss, "resolve_underdog",
                        lambda cid, detail=None: {"underdog_is_home": True, "underdog_name": "Home"})
    cfg = {
        "drama_radar":   {"enabled": True, "threshold": 8.0},
        "late_drama":    {"enabled": True, "max_diff": 2, "last_minutes": 20},
        "match_moments": {"enabled": True, "kickoff": True, "halftime": True,
                          "extra_time": False, "penalties": False},
        "card_penalty":  {"enabled": True, "red_card": True, "penalty": False},
        "sensation":     {"enabled": True},
    }
    alarms = sm._expand_follow_criteria(_spec(cfg), "bsd-1", {"home": {}, "away": {}})
    assert {a["alarm_type"] for a in alarms} == {
        "drama_radar", "late_drama", "match_moments", "card_penalty", "sensation"}
    # Per-match id so _fire dedup is scoped per (follow, match, criterion).
    assert all(a["id"] == "compfollow_u_premier_league_bsd-1" for a in alarms)
    assert all(a["match_id"] == "bsd-1" for a in alarms)
    assert next(a for a in alarms if a["alarm_type"] == "drama_radar")["threshold"] == 8.0
    assert next(a for a in alarms if a["alarm_type"] == "sensation")["config"]["underdog_name"] == "Home"


def test_match_moments_bundle_expands_with_reminder():
    # New bundle shape: a single {enabled, reminder_minutes} — the live evaluator
    # gates on `enabled`; the pre-match pass reads `reminder_minutes`.
    cfg = {"match_moments": {"enabled": True, "reminder_minutes": 30}}
    alarms = sm._expand_follow_criteria(_spec(cfg), "bsd-1", {})
    mm = next(a for a in alarms if a["alarm_type"] == "match_moments")
    assert mm["config"] == {"enabled": True, "reminder_minutes": 30}


def test_disabled_criteria_are_omitted():
    cfg = {"drama_radar": {"enabled": False},
           "late_drama": {"enabled": True, "max_diff": 1, "last_minutes": 15}}
    alarms = sm._expand_follow_criteria(_spec(cfg), "bsd-1", {})
    assert [a["alarm_type"] for a in alarms] == ["late_drama"]


def test_legacy_no_config_falls_back_to_drama_and_kickoff():
    alarms = sm._expand_follow_criteria(
        _spec(config=None, threshold=7.0, notify_at_kickoff=True), "bsd-1", {})
    types_ = {a["alarm_type"] for a in alarms}
    assert types_ == {"drama_radar", "match_moments"}
    assert next(a for a in alarms if a["alarm_type"] == "match_moments")["config"] == {
        "enabled": True, "reminder_minutes": 0}


def test_legacy_just_follow_threshold_99_emits_nothing():
    # "Just Follow" stores threshold 99 + no kickoff → display-only, never alerts.
    assert sm._expand_follow_criteria(
        _spec(config=None, threshold=99.0, notify_at_kickoff=False), "bsd-1", {}) == []


def test_sensation_skipped_when_no_clear_underdog(monkeypatch):
    monkeypatch.setattr(sm.ss, "resolve_underdog", lambda cid, detail=None: None)
    assert sm._expand_follow_criteria(_spec({"sensation": {"enabled": True}}), "bsd-1", {}) == []


def test_followed_late_drama_fires_through_real_evaluator(monkeypatch):
    sent = _wire(monkeypatch)
    spec = _spec({"late_drama": {"enabled": True, "max_diff": 1, "last_minutes": 15}})
    match = {"status": "2h", "minute": 88, "competition": "premier_league",
             "home": {"name": "H", "score": 1}, "away": {"name": "A", "score": 1}}
    for alarm in sm._expand_follow_criteria(spec, "bsd-1", match):
        sm._evaluate_alarm(alarm, match)
    assert len(sent) == 1  # late drama (tied, 88') fired exactly once


def test_followed_card_penalty_does_not_fire_when_quiet(monkeypatch):
    sent = _wire(monkeypatch)
    spec = _spec({"card_penalty": {"enabled": True, "red_card": True, "penalty": True}})
    match = {"status": "2h", "minute": 60, "competition": "premier_league",
             "home": {"name": "H", "score": 0}, "away": {"name": "A", "score": 0},
             "incidents": []}
    for alarm in sm._expand_follow_criteria(spec, "bsd-1", match):
        sm._evaluate_alarm(alarm, match)
    assert sent == []  # no red card / penalty incident → silent
