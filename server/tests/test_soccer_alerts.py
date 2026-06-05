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
    a = _alarm("match_moments", {"kickoff": True, "halftime": True})
    sm._evaluate_alarm(a, _match(status="live"))          # kickoff
    sm._evaluate_alarm(a, _match(status="live"))          # dedup → no 2nd kickoff
    sm._evaluate_alarm(a, _match(status="ht"))            # halftime
    titles = [s["title"] for s in sent]
    assert sum("Kicking Off" in t for t in titles) == 1
    assert sum("Half-Time" in t for t in titles) == 1


def test_match_moments_modular_disabled_event_is_suppressed(monkeypatch):
    sent = _wire_capture(monkeypatch)
    # Everything ON except goals → kickoff fires, the goal does NOT.
    a = _alarm("match_moments", {"enabled": True, "kickoff": True, "goals": False})
    sm._evaluate_alarm(a, _match(status="live", hs=1, as_=0))
    titles = [s["title"] for s in sent]
    assert any("Kicking Off" in t for t in titles)
    assert not any("GOAL" in t.upper() or "גול" in t for t in titles)

    # The inverse: goals ON, kickoff OFF → goal fires, kickoff does not.
    sent2 = _wire_capture(monkeypatch)
    b = _alarm("match_moments", {"enabled": True, "kickoff": False, "goals": True}, aid=2)
    sm._evaluate_alarm(b, _match(status="live", hs=1, as_=0))
    titles2 = [s["title"] for s in sent2]
    assert any("GOAL" in t.upper() or "גול" in t for t in titles2)
    assert not any("Kicking Off" in t for t in titles2)


# ── match_moments full bundle ───────────────────────────────────────────────
def test_match_moments_fires_full_set(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": True})
    # goal at 1-0
    sm._evaluate_alarm(alarm, _match(status="live", minute=20, hs=1, as_=0))
    # same scoreline again → no duplicate goal (and no duplicate kickoff)
    sm._evaluate_alarm(alarm, _match(status="live", minute=21, hs=1, as_=0))
    # second goal 2-0
    sm._evaluate_alarm(alarm, _match(status="live", minute=40, hs=2, as_=0))
    # half-time
    sm._evaluate_alarm(alarm, _match(status="ht", minute=45, hs=2, as_=0))
    # full-time
    sm._evaluate_alarm(alarm, _match(status="ft", minute=90, hs=2, as_=0))

    # exactly 2 goal alerts (1-0 and 2-0)
    goal_count = sum(1 for s in sent if "GOAL" in s["title"].upper() or "גול" in s["title"])
    assert goal_count == 2
    # exactly one kickoff (deduped across the two live ticks)
    assert sum(1 for s in sent if "Kicking Off" in s["title"]) == 1
    # one half-time, one full-time
    assert sum(1 for s in sent if "Half-Time" in s["title"]) == 1
    assert sum(1 for s in sent if "Full-time" in s["title"]) == 1


def test_match_moments_disabled_fires_nothing(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": False})
    sm._evaluate_alarm(alarm, _match(status="live", minute=20, hs=1, as_=0))
    sm._evaluate_alarm(alarm, _match(status="ft", minute=90, hs=2, as_=0))
    assert sent == []


def test_match_moments_second_half_fires_once(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": True})
    sm._evaluate_alarm(alarm, _match(status="live", minute=48, hs=0, as_=0))
    sm._evaluate_alarm(alarm, _match(status="live", minute=50, hs=0, as_=0))
    sh = [s for s in sent if "second half" in s["body"].lower()]
    assert len(sh) == 1


def test_match_moments_red_and_penalty_dedup(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": True})
    red_inc = {"type": "card", "card_type": "red", "minute": 55, "player": "X"}
    m = _match(status="live", minute=55, hs=0, as_=0, incidents=[red_inc])
    sm._evaluate_alarm(alarm, m)
    # same red at a later live clock → fires once (dedup by incident minute 55)
    sm._evaluate_alarm(alarm, _match(status="live", minute=60, hs=0, as_=0, incidents=[red_inc]))
    reds = [s for s in sent if "Red Card" in s["title"] or "אדום" in s["title"]]
    assert len(reds) == 1


def test_match_moments_penalty_dedup_by_incident_minute(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": True})
    pen_inc = {"type": "penalty", "minute": 33}
    sm._evaluate_alarm(alarm, _match(status="live", minute=33, hs=0, as_=0, incidents=[pen_inc]))
    # live clock advanced but the SAME penalty incident → no second penalty alert
    sm._evaluate_alarm(alarm, _match(status="live", minute=38, hs=0, as_=0, incidents=[pen_inc]))
    pens = [s for s in sent if "Penalty" in s["title"]]
    assert len(pens) == 1


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
    monkeypatch.setattr(ss, "pre_match_win_prob", lambda eid: (0.40, 0.30, 0.30))  # gap 0.10
    monkeypatch.setattr(ss, "get_soccer_match_detail",
                        lambda c: {"home": {"id": "1", "name": "Nowhereland"},
                                   "away": {"id": "2", "name": "Elsewhereia"}})  # unknown → no rank gap
    assert ss.resolve_underdog("bsd-372") is None


def test_resolve_underdog_ranking_fallback(monkeypatch):
    # Odds too even to flag an underdog, but the FIFA-ranking gap is huge
    # (Germany ~10 vs Curaçao ~82) — the fallback should still flag Curaçao.
    monkeypatch.setattr(ss, "pre_match_win_prob", lambda eid: (0.44, 0.26, 0.30))
    monkeypatch.setattr(ss, "get_soccer_match_detail",
                        lambda c: {"home": {"id": "467", "name": "Germany"},
                                   "away": {"id": "480", "name": "Curaçao"}})
    dog = ss.resolve_underdog("bsd-8295")
    assert dog is not None
    assert dog["underdog_name"] == "Curaçao"
    assert dog["underdog_is_home"] is False
    assert dog["underdog_source"] == "ranking"


def test_resolve_underdog_none_for_sofa(monkeypatch):
    # sofa-* has no BSD prediction path
    assert ss.resolve_underdog("sofa-14023956") is None


def test_fifa_rankings_alias_bridging():
    import fifa_rankings as fr
    fr._reset_for_tests()
    assert fr.rank("South Korea") == fr.rank("Korea Republic")  # alias bridge
    assert fr.rank("Iran") == fr.rank("IR Iran")
    assert fr.rank("Germany") and fr.rank("Curaçao")
    assert fr.rank("Nowhereland") is None
