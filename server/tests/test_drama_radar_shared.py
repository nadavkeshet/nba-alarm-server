"""The competition drama-radar must compute the Drama Engine score ONCE per
match and share it across every follower — not recompute (and re-write the DB
snapshot) per subscriber. These guard that optimization + its correctness."""
import soccer_monitor as sm


class _Score:
    def __init__(self, total):
        self.total = total
        self.state = self.momentum = self.events = self.stakes = 0.0


def _wire(monkeypatch):
    sent = []
    fired = set()
    monkeypatch.setattr(sm, "_send_push",
                        lambda token, title, body, *, alert_type="alarm": sent.append((token, alert_type)))
    monkeypatch.setattr(sm, "_fire_once_fn",
                        lambda key, token: (key + token not in fired) and (fired.add(key + token) or True))
    monkeypatch.setattr(sm, "_get_user_language", lambda t: "en")
    monkeypatch.setattr(sm, "_db_engine", None)
    monkeypatch.setattr(sm, "_alarm_model", None)
    return sent


def _live_match(total_hint=8.0):
    return {"id": "bsd-1", "status": "live", "minute": 80, "competition": "premier_league",
            "home": {"name": "Home", "score": 2}, "away": {"name": "Away", "score": 2}}


def test_score_computed_once_for_many_followers(monkeypatch):
    sent = _wire(monkeypatch)
    calls = {"compute": 0, "snapshot": 0}
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail", lambda d: {})
    monkeypatch.setattr(sm.drama_engine, "compute_drama",
                        lambda ctx: (calls.__setitem__("compute", calls["compute"] + 1), _Score(8.0))[1])
    monkeypatch.setattr(sm, "_log_drama_snapshot",
                        lambda *a: calls.__setitem__("snapshot", calls["snapshot"] + 1))

    match = _live_match()
    score = sm._match_drama(match)                 # comp-radar pass: compute ONCE
    followers = [
        {"id": 1, "user_token": "u1", "threshold": 6.0, "alert_type": "alarm"},  # fires
        {"id": 2, "user_token": "u2", "threshold": 7.5, "alert_type": "alarm"},  # fires
        {"id": 3, "user_token": "u3", "threshold": 9.0, "alert_type": "alarm"},  # below
    ]
    for f in followers:
        sm._evaluate_drama_radar(f, match, score=score)

    assert calls["compute"] == 1     # computed exactly once for all 3 followers
    assert calls["snapshot"] == 1    # one DB snapshot, not one per subscriber
    assert len(sent) == 2            # only the two whose threshold the score met


def test_match_drama_none_when_not_live(monkeypatch):
    monkeypatch.setattr(sm.drama_engine, "compute_drama", lambda ctx: _Score(9.0))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    assert sm._match_drama({"status": "scheduled", "id": "x"}) is None
    assert sm._match_drama({"status": "ft", "id": "x"}) is None


def test_evaluate_without_precomputed_still_computes(monkeypatch):
    sent = _wire(monkeypatch)
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail", lambda d: {})
    monkeypatch.setattr(sm.drama_engine, "compute_drama", lambda ctx: _Score(8.5))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    match = _live_match()
    sm._evaluate_drama_radar({"id": 9, "user_token": "u", "threshold": 7.0, "alert_type": "alarm"}, match)
    assert len(sent) == 1


# ── win-probability swing injection (live WS → drama ctx) ─────────────────────
def test_match_drama_injects_winprob_swing_for_bsd(monkeypatch):
    """_match_drama must pull the live WS win-prob swing into the ctx (the REST
    detail leaves it None by design). Without this, drama_radar alarms never see
    a favorite-flip spike."""
    import bsd_websocket
    captured = {}
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail",
                        lambda d: {"win_prob_swing": None})
    monkeypatch.setattr(sm.drama_engine, "compute_drama",
                        lambda ctx: captured.update(ctx) or _Score(8.0))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    monkeypatch.setattr(bsd_websocket, "get_winprob_swing", lambda eid, **k: 0.42)
    sm._match_drama({"id": "bsd-9096", "status": "live", "minute": 80})
    assert captured.get("win_prob_swing") == 0.42


def test_match_drama_no_swing_injection_for_s365(monkeypatch):
    """s365-* matches have no BSD WS feed — swing stays None, never crashes."""
    import bsd_websocket
    captured = {}
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail",
                        lambda d: {"win_prob_swing": None})
    monkeypatch.setattr(sm.drama_engine, "compute_drama",
                        lambda ctx: captured.update(ctx) or _Score(8.0))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    def _boom(eid, **k):  # would fail on a 365 id; must not be called
        raise AssertionError("get_winprob_swing called for s365 id")
    monkeypatch.setattr(bsd_websocket, "get_winprob_swing", _boom)
    sm._match_drama({"id": "s365-4671395", "status": "live", "minute": 80})
    assert captured.get("win_prob_swing") is None


def test_match_drama_overlays_live_ws_stats(monkeypatch):
    """_match_drama must overlay the live WS event stats (near-miss / saves /
    dangerous-attack pressure) onto the scoring ctx for bsd-* matches."""
    import bsd_websocket
    captured = {}
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail",
                        lambda d: {"home": {"score": 1}, "away": {"score": 1},
                                   "momentum_pressure": None, "win_prob_swing": None})
    monkeypatch.setattr(sm.drama_engine, "compute_drama",
                        lambda ctx: captured.update(ctx) or _Score(8.0))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    monkeypatch.setattr(bsd_websocket, "get_winprob_swing", lambda eid, **k: 0.3)
    monkeypatch.setattr(bsd_websocket, "get_live_event_state", lambda eid: {
        "home": {"id": 1, "name": "H"},
        "stats": {"home": {"big_chances_missed": 2, "big_saves": 1, "dangerous_attack_pct": 70},
                  "away": {"big_chances_missed": 0, "big_saves": 0, "dangerous_attack_pct": 30}},
    })
    sm._match_drama({"id": "bsd-9096", "status": "live", "minute": 80})
    assert captured["home"]["big_chances_missed"] == 2
    assert captured["home"]["big_saves"] == 1
    assert captured["win_prob_swing"] == 0.3
    # dangerous-attack pressure (70-30)/100 = 0.4 overrides the None REST value
    assert abs(captured["momentum_pressure"] - 0.4) < 1e-9


def test_match_drama_blends_attack_surge_into_momentum(monkeypatch):
    """The live situation-stream surge blends with dangerous-attack pressure."""
    import bsd_websocket
    captured = {}
    monkeypatch.setattr(sm.drama_engine, "context_from_match_detail",
                        lambda d: {"home": {"score": 0}, "away": {"score": 1},
                                   "momentum_pressure": None, "win_prob_swing": None})
    monkeypatch.setattr(sm.drama_engine, "compute_drama",
                        lambda ctx: captured.update(ctx) or _Score(8.0))
    monkeypatch.setattr(sm, "_log_drama_snapshot", lambda *a: None)
    monkeypatch.setattr(bsd_websocket, "get_winprob_swing", lambda eid, **k: None)
    monkeypatch.setattr(bsd_websocket, "get_attack_surge", lambda eid, **k: 0.9)
    monkeypatch.setattr(bsd_websocket, "get_live_event_state", lambda eid: {
        "stats": {"home": {"dangerous_attack_pct": 70}, "away": {"dangerous_attack_pct": 30}}})
    sm._match_drama({"id": "bsd-1", "status": "live", "minute": 85})
    # blend(dangerous=0.4, surge=0.9) = 0.4*0.4 + 0.6*0.9 = 0.70
    assert abs(captured["momentum_pressure"] - 0.70) < 1e-9
