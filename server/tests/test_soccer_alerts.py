"""Tests for the modular soccer alert evaluators + underdog resolution."""
import soccer_monitor as sm
import soccer_source as ss


def _wire_capture(monkeypatch):
    """Wire the monitor to capture pushes and dedup per (key, token), like prod."""
    sent = []
    fired = set()

    def fake_push(token, title, body, *, alert_type="alarm"):
        sent.append({"token": token, "title": title, "body": body, "alert_type": alert_type})

    def fake_fire_once(key, token):
        if key in fired:
            return False
        fired.add(key)
        return True

    monkeypatch.setattr(sm, "_send_push", fake_push)
    monkeypatch.setattr(sm, "_fire_once_fn", fake_fire_once)
    monkeypatch.setattr(sm, "_get_user_language", lambda t: "en")
    monkeypatch.setattr(sm, "_db_engine", None)
    monkeypatch.setattr(sm, "_alarm_model", None)
    return sent


def _alarm(alarm_type, config=None, aid=1):
    return {"id": aid, "user_token": "tok", "alarm_type": alarm_type,
            "config": config or {}, "alert_type": "alarm"}


def _match(status="live", minute=10, hs=0, as_=0, incidents=None):
    return {"status": status, "minute": minute, "competition": "premier_league",
            "home": {"name": "Home", "score": hs}, "away": {"name": "Away", "score": as_},
            "incidents": incidents or []}


# ── late_drama ──────────────────────────────────────────────────────────────
def test_late_drama_defaults_match_legacy(monkeypatch):
    sent = _wire_capture(monkeypatch)
    # default = last 15 (min_minute 75), diff <= 1
    sm._evaluate_alarm(_alarm("late_drama"), _match(minute=74, hs=1, as_=1))
    assert sent == []  # before minute 75
    sm._evaluate_alarm(_alarm("late_drama"), _match(minute=80, hs=2, as_=1))
    assert len(sent) == 1


def test_late_drama_config_diff_zero_means_tied_only(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("late_drama", {"max_diff": 0, "last_minutes": 20})  # min_minute 70
    sm._evaluate_alarm(a, _match(minute=72, hs=1, as_=0))  # diff 1 > 0 → no
    assert sent == []
    sm._evaluate_alarm(a, _match(minute=72, hs=1, as_=1))  # tied → fire
    assert len(sent) == 1


# ── match_moments ───────────────────────────────────────────────────────────
def test_match_moments_fires_each_enabled_once(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("match_moments", {"kickoff": True, "halftime": True, "extra_time": False, "penalties": False})
    sm._evaluate_alarm(a, _match(status="live"))          # kickoff
    sm._evaluate_alarm(a, _match(status="live"))          # dedup → no 2nd kickoff
    sm._evaluate_alarm(a, _match(status="ht"))            # halftime
    titles = [s["title"] for s in sent]
    assert sum("Kicking Off" in t for t in titles) == 1
    assert sum("Half-Time" in t for t in titles) == 1
    # extra_time disabled → never fires even at et
    sm._evaluate_alarm(a, _match(status="et"))
    assert not any("Extra Time" in t for t in [s["title"] for s in sent])


# ── card_penalty ────────────────────────────────────────────────────────────
def test_card_penalty_fires_on_any_red_and_penalty(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("card_penalty", {"red_card": True, "penalty": True})
    red_match = _match(minute=30, incidents=[{"type": "card", "card_type": "red", "minute": 28}])
    sm._evaluate_alarm(a, red_match)
    assert any("Red Card" in s["title"] for s in sent)
    pen_match = _match(minute=55, incidents=[{"type": "penalty", "minute": 54}])
    sm._evaluate_alarm(a, pen_match)
    assert any("Penalty" in s["title"] for s in sent)


def test_card_penalty_respects_flags(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("card_penalty", {"red_card": False, "penalty": True})
    sm._evaluate_alarm(a, _match(incidents=[{"type": "card", "card_type": "red", "minute": 10}]))
    assert sent == []  # red disabled


# ── sensation ───────────────────────────────────────────────────────────────
def test_sensation_fires_only_when_configured_underdog_leads(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("sensation", {"underdog_is_home": False, "underdog_name": "Brentford"})
    sm._evaluate_alarm(a, _match(hs=1, as_=0))   # home (favorite) leads → no
    assert sent == []
    sm._evaluate_alarm(a, _match(hs=0, as_=1))   # away (underdog) leads → fire
    assert len(sent) == 1
    assert "Brentford" in sent[0]["body"]


# ── resolve_underdog ────────────────────────────────────────────────────────
def test_resolve_underdog_clear(monkeypatch):
    monkeypatch.setattr(ss, "pre_match_win_prob", lambda eid: (0.62, 0.23, 0.15))
    monkeypatch.setattr(ss, "get_soccer_match_detail",
                        lambda c: {"home": {"id": "1", "name": "Liverpool"},
                                   "away": {"id": "2", "name": "Brentford"}})
    dog = ss.resolve_underdog("bsd-372")
    assert dog["underdog_is_home"] is False
    assert dog["underdog_name"] == "Brentford"


def test_resolve_underdog_none_when_even(monkeypatch):
    monkeypatch.setattr(ss, "pre_match_win_prob", lambda eid: (0.45, 0.30, 0.25))  # gap 0.20 < 0.25
    assert ss.resolve_underdog("bsd-372") is None


def test_resolve_underdog_none_for_sofa(monkeypatch):
    # sofa-* has no BSD prediction path
    assert ss.resolve_underdog("sofa-14023956") is None
