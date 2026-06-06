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
    # Module-level state persists across tests — reset the retroactive-guard and
    # goal-attribution caches so each test starts from a clean baseline.
    sm._armed_minute.clear()
    sm._goal_state.clear()
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
    sm._evaluate_alarm(a, _match(status="live", minute=0))  # armed at kickoff → kickoff fires
    sm._evaluate_alarm(a, _match(status="live", minute=0))  # dedup → no 2nd kickoff
    sm._evaluate_alarm(a, _match(status="ht", minute=45))   # halftime
    titles = [s["title"] for s in sent]
    assert sum("Kicking Off" in t for t in titles) == 1
    assert sum("Half-Time" in t for t in titles) == 1


def test_match_moments_modular_disabled_event_is_suppressed(monkeypatch):
    sent = _wire_capture(monkeypatch)
    # Everything ON except goals → kickoff fires, the goal does NOT.
    a = _alarm("match_moments", {"enabled": True, "kickoff": True, "goals": False})
    sm._evaluate_alarm(a, _match(status="live", minute=0, hs=1, as_=0))
    titles = [s["title"] for s in sent]
    assert any("Kicking Off" in t for t in titles)
    assert not any("GOAL" in t.upper() or "גול" in t for t in titles)

    # The inverse: goals ON, kickoff OFF → goal fires, kickoff does not.
    sent2 = _wire_capture(monkeypatch)
    b = _alarm("match_moments", {"enabled": True, "kickoff": False, "goals": True}, aid=2)
    sm._evaluate_alarm(b, _match(status="live", minute=0, hs=1, as_=0))
    titles2 = [s["title"] for s in sent2]
    assert any("GOAL" in t.upper() or "גול" in t for t in titles2)
    assert not any("Kicking Off" in t for t in titles2)


# ── match_moments full bundle ───────────────────────────────────────────────
def test_match_moments_fires_full_set(monkeypatch):
    sent = _wire_capture(monkeypatch)
    alarm = _alarm("match_moments", config={"enabled": True})
    # armed at kickoff (minute 0) so kickoff + the later goals are all "fresh"
    sm._evaluate_alarm(alarm, _match(status="live", minute=0, hs=0, as_=0))
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
    sm._evaluate_alarm(alarm, _match(status="live", minute=46, hs=0, as_=0))
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
    # The event happens at the current minute (armed there), so it's not retroactive.
    red_match = _match(minute=28, incidents=[{"type": "card", "card_type": "red", "minute": 28}])
    sm._evaluate_alarm(a, red_match)
    assert any("Red Card" in s["title"] for s in sent)
    pen_match = _match(minute=54, incidents=[{"type": "penalty", "minute": 54}])
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


def test_resolve_underdog_none_for_s365(monkeypatch):
    # s365-* (non-BSD source) has no BSD prediction path
    assert ss.resolve_underdog("s365-14023956") is None


def test_fifa_rankings_alias_bridging():
    import fifa_rankings as fr
    fr._reset_for_tests()
    assert fr.rank("South Korea") == fr.rank("Korea Republic")  # alias bridge
    assert fr.rank("Iran") == fr.rank("IR Iran")
    assert fr.rank("Germany") and fr.rank("Curaçao")
    assert fr.rank("Nowhereland") is None


# ── match_moments: second-half must not fire during FIRST-HALF stoppage ───────
def _mm_second_half_only(aid=1):
    # Only the second-half moment enabled, so the test isolates it (kickoff would
    # otherwise also fire on a live tick).
    return _alarm("match_moments", {
        "enabled": True, "kickoff": False, "goals": False, "halftime": False,
        "second_half": True, "red_cards": False, "penalties": False,
        "match_end": False, "lineups": False, "reminder_minutes": 0,
    }, aid=aid)


def _match_p(period, status="live", minute=46, hs=1, as_=1):
    m = _match(status=status, minute=minute, hs=hs, as_=as_)
    m["period"] = period
    return m


def test_second_half_not_fired_in_first_half_stoppage(monkeypatch):
    # 45+1: current_minute reads 46 while period is still "1h". The old
    # `minute >= 46` fallback wrongly fired "second half underway" here.
    sent = _wire_capture(monkeypatch)
    sm._evaluate_alarm(_mm_second_half_only(), _match_p("1h", minute=46))
    sm._evaluate_alarm(_mm_second_half_only(), _match_p("1h", minute=48))  # deep 1h stoppage
    assert sent == [], f"second_half must not fire in first-half stoppage; got {sent}"


def test_second_half_not_fired_at_halftime(monkeypatch):
    sent = _wire_capture(monkeypatch)
    sm._evaluate_alarm(_mm_second_half_only(), _match_p("ht", status="ht", minute=45))
    assert sent == [], f"second_half must not fire at halftime; got {sent}"


def test_second_half_fires_in_real_second_half(monkeypatch):
    sent = _wire_capture(monkeypatch)
    sm._evaluate_alarm(_mm_second_half_only(), _match_p("2h", minute=46))
    assert len(sent) == 1, f"second_half should fire once in the 2nd half; got {sent}"


# ── goal scorer attribution + new body format ────────────────────────────────
def test_scoring_side_attribution_and_idempotent():
    sm._goal_state.clear()
    assert sm._scoring_side("m1", 0, 0) is None      # baseline — nothing to attribute
    assert sm._scoring_side("m1", 1, 0) == "home"     # home tally rose
    assert sm._scoring_side("m1", 1, 0) == "home"     # idempotent: same scoreline → cached
    assert sm._scoring_side("m1", 1, 1) == "away"     # away tally rose
    assert sm._scoring_side("m2", 2, 1) is None       # first sighting mid-score → unknown


def _goals_only_alarm(aid=1):
    return _alarm("match_moments", {
        "enabled": True, "kickoff": False, "goals": True, "halftime": False,
        "second_half": False, "red_cards": False, "penalties": False,
        "match_end": False, "lineups": False, "reminder_minutes": 0}, aid=aid)


def test_goal_body_names_only_scorer_with_real_score(monkeypatch):
    sm._goal_state.clear()
    sent = _wire_capture(monkeypatch)
    a = _goals_only_alarm()
    m0 = _match(status="live", minute=10, hs=0, as_=0); m0["id"] = "g1"; m0["period"] = "1h"
    sm._evaluate_alarm(a, m0)                          # 0-0 baseline → no goal
    m1 = _match(status="live", minute=20, hs=1, as_=0); m1["id"] = "g1"; m1["period"] = "1h"
    sm._evaluate_alarm(a, m1)                          # home scores → 1-0
    bodies = [s["body"] for s in sent]
    assert bodies == ["(1) - 0  Home"], bodies          # scorer parenthesized, only scorer named, no competition


def test_goal_scorer_from_incidents():
    match = {"incidents": [
        {"type": "goal", "home_score": 1, "away_score": 0, "player": "W. Semedo"},
        {"type": "goal", "home_score": 1, "away_score": 1, "player": "J. Doe"},
    ]}
    assert sm._goal_scorer(match, 1, 0) == "W. Semedo"
    assert sm._goal_scorer(match, 1, 1) == "J. Doe"
    assert sm._goal_scorer(match, 2, 0) is None        # incident not landed yet → omit
    assert sm._goal_scorer({}, 1, 0) is None


def test_goal_body_includes_scorer_when_incident_present(monkeypatch):
    sm._goal_state.clear()
    sent = _wire_capture(monkeypatch)
    a = _goals_only_alarm()
    m0 = _match(status="live", minute=10, hs=0, as_=0); m0["id"] = "g2"; m0["period"] = "1h"
    sm._evaluate_alarm(a, m0)                            # 0-0 baseline
    m1 = _match(status="live", minute=20, hs=1, as_=0); m1["id"] = "g2"; m1["period"] = "1h"
    m1["incidents"] = [{"type": "goal", "home_score": 1, "away_score": 0, "player": "W. Semedo"}]
    sm._evaluate_alarm(a, m1)
    assert [s["body"] for s in sent] == ["(1) - 0  Home · W. Semedo"]


def test_halftime_body_shows_score(monkeypatch):
    sent = _wire_capture(monkeypatch)
    a = _alarm("match_moments", {"enabled": True, "kickoff": False, "goals": False,
                                 "halftime": True, "second_half": False, "red_cards": False,
                                 "penalties": False, "match_end": False, "lineups": False,
                                 "reminder_minutes": 0})
    m = _match(status="ht", minute=45, hs=1, as_=0); m["id"] = "h1"; m["period"] = "ht"
    sm._evaluate_alarm(a, m)
    assert sent and sent[0]["body"] == "Half-time: Home 1-0 Away"


def test_no_retroactive_fire_when_armed_mid_match(monkeypatch):
    """Arming an alarm on an already-live match must NOT replay past moments."""
    sent = _wire_capture(monkeypatch)
    a = _alarm("match_moments", {"enabled": True, "kickoff": True, "goals": True,
                                 "halftime": True, "second_half": True, "match_end": True})
    # Armed at minute 60, already 2-1 (the 2-1 goal was scored back at minute 58).
    m = _match(status="live", minute=60, hs=2, as_=1)
    m["id"] = "ret1"; m["period"] = "2h"
    m["incidents"] = [{"type": "goal", "home_score": 2, "away_score": 1, "minute": 58, "player": "Past"}]
    sm._evaluate_alarm(a, m)
    assert sent == [], f"pre-arm moments must not fire; got {[s['body'] for s in sent]}"

    # A NEW goal at minute 70 → 3-1 DOES fire.
    m2 = _match(status="live", minute=70, hs=3, as_=1)
    m2["id"] = "ret1"; m2["period"] = "2h"
    m2["incidents"] = m["incidents"] + [{"type": "goal", "home_score": 3, "away_score": 1, "minute": 70, "player": "New"}]
    sm._evaluate_alarm(a, m2)
    assert len(sent) == 1 and "New" in sent[0]["body"], [s["body"] for s in sent]
