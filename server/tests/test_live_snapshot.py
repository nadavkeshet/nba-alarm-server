"""build_live_snapshot enriches the /soccer/live payload with clock anchor,
win-prob (+swing), momentum pressure, and commentary — all off the WS cache."""
import bsd_websocket
import soccer_source as ss


class _FakeWS:
    def desired_subscriptions(self): return set()
    def subscribe(self, eid): pass
    def get_event_snapshot(self, eid):
        return {
            "home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"},
            "score": {"home": 1, "away": 1},
            "time": {"minute": 78, "second": 12, "status": "2nd_half", "period": 2,
                     "injury_time": 0, "period_started_at_uts": 1780774988},
            "stats": {"home": {"dangerous_attack_pct": 60, "big_chances": 3,
                               "shots_on_target": 7},
                      "away": {"dangerous_attack_pct": 40, "big_chances": 1,
                               "shots_on_target": 3}},
        }
    def get_livedata_snapshot(self, eid):
        return {"side": "home", "situation": "dangerous",
                "coordinates": [{"x": 80, "y": 50}], "uts": 1780776000,
                "commentary": "Home surge forward!"}
    def get_odds_snapshot(self, eid):
        return {"odds": {"match_winner": {"home": 1.8, "draw": 3.5, "away": 4.5}}}
    def get_attack_surge(self, eid, window_s=180.0): return 0.5
    def get_winprob_swing(self, eid, window_s=300.0): return 0.18


def _snap(monkeypatch):
    monkeypatch.setattr(bsd_websocket, "client", lambda: _FakeWS())
    return ss.build_live_snapshot("bsd-1")


def test_clock_anchor_fields(monkeypatch):
    s = _snap(monkeypatch)
    assert s["ws_active"] is True
    assert s["period"] == 2
    assert s["period_started_at_uts"] == 1780774988
    assert s["injury_time"] == 0
    assert s["minute"] == 78


def test_win_prob_and_swing(monkeypatch):
    s = _snap(monkeypatch)
    wp = s["win_prob"]
    assert wp and abs(sum(wp) - 1.0) < 1e-2      # normalized 1X2
    assert wp[0] > wp[2]                          # home (1.8) favored over away (4.5)
    assert s["win_prob_swing"] == 0.18


def test_momentum_pressure_blended(monkeypatch):
    s = _snap(monkeypatch)
    # dangerous asymmetry (60-40)/100 = 0.2; surge 0.5 → blend 0.4*0.2 + 0.6*0.5 = 0.38
    assert abs(s["momentum_pressure"] - 0.38) < 1e-6


def test_commentary_and_stats(monkeypatch):
    s = _snap(monkeypatch)
    assert s["livedata"]["commentary"] == "Home surge forward!"
    # stats now read the correct path → populated (was empty under the old bug)
    assert s["stats"]["home"].get("dangerous_attack_pct") == 60


def test_non_bsd_is_inactive(monkeypatch):
    assert ss.build_live_snapshot("s365-123") == {"ws_active": False}
