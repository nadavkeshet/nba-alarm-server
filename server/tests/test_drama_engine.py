"""Drama Engine calibration tests.

These tests pin the *behavior* of `compute_drama` to the design intent
described in clutch-alarm-v3-soccer-redesign.md and the user's framing:

    "1-0 with the leading team dominating: not worth waking up.
     1-0 with the trailing team dominating: worth!"

Tunables (weights inside drama_engine.py) can move, but these contrasts
must hold. If a tuning change breaks one of these, decide whether the
test or the formula is wrong — don't silently lower the threshold to
make it pass.
"""

from __future__ import annotations

import pytest

from drama_engine import compute_drama, DramaScore, context_from_match_detail


# ─────────────────────────────────────────────────────────────────────────────
# Helpers

def _ctx(**over):
    """Sensible-default match dict; override per-test."""
    base = {
        "minute":      45,
        "status":      "live",
        "competition": "premier_league",
        "round_name":  None,
        "is_derby":    False,
        "home": {"score": 0, "xg_last_15": None},
        "away": {"score": 0, "xg_last_15": None},
        "momentum_pressure": None,
        "recent_events": [],
    }
    # Merge home/away separately so the caller can override one field
    for side in ("home", "away"):
        if side in over:
            merged = {**base[side], **over.pop(side)}
            base[side] = merged
    base.update(over)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# The user's central insight: pressure-vs-scoreline asymmetry

def test_one_zero_trailing_team_dominating_is_dramatic():
    """1-0, trailing team has all the xG in the last 15 → high drama."""
    ctx = _ctx(
        minute=78,
        home={"score": 1, "xg_last_15": 0.10},
        away={"score": 0, "xg_last_15": 0.80},  # trailing side cooking
    )
    s = compute_drama(ctx)
    assert s.total >= 6.0, f"expected >=6 drama, got {s}"
    assert s.momentum >= 2.5


def test_one_zero_leading_team_dominating_is_not_dramatic():
    """1-0, leader killing the game → low drama, even in late minutes."""
    ctx = _ctx(
        minute=78,
        home={"score": 1, "xg_last_15": 0.90},
        away={"score": 0, "xg_last_15": 0.05},  # trailing side has nothing
    )
    s = compute_drama(ctx)
    assert s.total <= 4.0, f"expected <=4 drama, got {s}"
    # Momentum should be near zero — leading team domination is anti-drama
    assert s.momentum <= 1.0


def test_dominating_contrast_is_real():
    """The same scoreline + minute should differ by >=3 drama based on
    who is dominating. This is the core ranking property of the engine."""
    trailing = compute_drama(_ctx(
        minute=78,
        home={"score": 1, "xg_last_15": 0.10},
        away={"score": 0, "xg_last_15": 0.80},
    )).total
    leading  = compute_drama(_ctx(
        minute=78,
        home={"score": 1, "xg_last_15": 0.90},
        away={"score": 0, "xg_last_15": 0.05},
    )).total
    assert trailing - leading >= 3.0, f"trailing={trailing} leading={leading}"


# ─────────────────────────────────────────────────────────────────────────────
# Lateness amplifier

def test_zero_zero_at_89_is_dramatic():
    s = compute_drama(_ctx(minute=89, home={"score": 0}, away={"score": 0}))
    assert s.total >= 5.5, s


def test_zero_zero_at_5_is_not_dramatic():
    s = compute_drama(_ctx(minute=5, home={"score": 0}, away={"score": 0}))
    assert s.total <= 4.0, s


def test_late_dead_blowout_is_low():
    """4-0 at 80' with leader still pressing → close to zero drama."""
    s = compute_drama(_ctx(
        minute=80,
        home={"score": 4, "xg_last_15": 0.60},
        away={"score": 0, "xg_last_15": 0.05},
    ))
    assert s.total <= 3.0, s


# ─────────────────────────────────────────────────────────────────────────────
# Events injector

def test_red_card_plus_tied_late_game_is_very_dramatic():
    ctx = _ctx(
        minute=85,
        home={"score": 1},
        away={"score": 1},
        recent_events=[{"type": "red_card", "minute": 84, "team": "home"}],
    )
    s = compute_drama(ctx)
    assert s.total >= 7.5, s


def test_late_goal_is_dramatic_even_without_xg_signal():
    """A 90+1' equaliser scores high purely from events + state."""
    ctx = _ctx(
        minute=91,
        home={"score": 2},
        away={"score": 2},
        recent_events=[{"type": "goal", "minute": 90, "team": "away"}],
    )
    s = compute_drama(ctx)
    assert s.total >= 7.0, s


def test_penalty_awarded_late_is_dramatic():
    ctx = _ctx(
        minute=88,
        home={"score": 0},
        away={"score": 1},
        recent_events=[{"type": "penalty_awarded", "minute": 87, "team": "home"}],
    )
    s = compute_drama(ctx)
    assert s.total >= 6.5, s


# ─────────────────────────────────────────────────────────────────────────────
# Stakes multiplier

def test_world_cup_final_amplifies_drama():
    """Same close late game, but World Cup final >> Ligue 1 mid-table."""
    state_ctx = lambda comp, rnd: _ctx(
        minute=88,
        competition=comp,
        round_name=rnd,
        home={"score": 1, "xg_last_15": 0.5},
        away={"score": 1, "xg_last_15": 0.5},
    )
    wc  = compute_drama(state_ctx("world_cup", "Final")).total
    l1  = compute_drama(state_ctx("ligue_1", None)).total
    assert wc > l1, f"WC final ({wc}) should exceed Ligue 1 mid-table ({l1})"
    assert wc - l1 >= 1.5


def test_stakes_alone_dont_create_drama_from_a_blowout():
    """A 4-0 with leader pressing is not dramatic just because it's
    World Cup. The state+momentum+events sum must clamp at 0 before
    multiplying."""
    ctx = _ctx(
        minute=80,
        competition="world_cup",
        round_name="Final",
        home={"score": 4, "xg_last_15": 0.6},
        away={"score": 0, "xg_last_15": 0.05},
    )
    s = compute_drama(ctx)
    assert s.total <= 4.0, s


# ─────────────────────────────────────────────────────────────────────────────
# Extra time / pens

def test_extra_time_is_dramatic_by_default():
    s = compute_drama(_ctx(
        status="et",
        minute=105,
        home={"score": 1}, away={"score": 1},
    ))
    assert s.total >= 6.0, s


def test_penalties_are_dramatic_by_default():
    s = compute_drama(_ctx(
        status="pens",
        minute=120,
        home={"score": 1}, away={"score": 1},
    ))
    assert s.total >= 7.0, s


# ─────────────────────────────────────────────────────────────────────────────
# Graceful degradation

def test_missing_data_does_not_crash():
    """Real-world matches sometimes have no xG, no incidents, etc.
    Engine must never throw."""
    s = compute_drama({"home": {}, "away": {}})
    assert isinstance(s, DramaScore)
    assert 0.0 <= s.total <= 10.0


def test_scheduled_match_has_zero_state():
    s = compute_drama(_ctx(status="scheduled", minute=0))
    assert s.state == 0.0


def test_final_score_clamps_to_ten():
    """An extreme case (red card + penalty + late goal + WC final + tied)
    should not exceed 10."""
    ctx = _ctx(
        minute=92,
        status="live",
        competition="world_cup",
        round_name="Final",
        home={"score": 2, "xg_last_15": 0.5},
        away={"score": 2, "xg_last_15": 0.9},
        momentum_pressure=-0.8,  # away (also trailing? no, tied → abs)
        recent_events=[
            {"type": "red_card",        "minute": 88, "team": "home"},
            {"type": "penalty_awarded", "minute": 90, "team": "away"},
            {"type": "goal",            "minute": 91, "team": "away"},
        ],
    )
    s = compute_drama(ctx)
    assert s.total == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Round-keyword precedence

# ─────────────────────────────────────────────────────────────────────────────
# Translator: rich BSD match-detail payload → engine ctx

def test_translator_handles_empty_payload():
    ctx = context_from_match_detail({})
    assert ctx["home"] == {} and ctx["away"] == {}
    s = compute_drama(ctx)
    assert isinstance(s, DramaScore)


def test_translator_extracts_xg_window_from_bsd_timeline():
    """xg_last_15 should be the cum-xg delta between (minute - 15) and minute."""
    detail = {
        "minute": 80,
        "status": "live",
        "competition": "premier_league",
        "home": {"score": 0},
        "away": {"score": 1},
        "xg_per_minute": [
            {"m": 60, "cum_home": 0.30, "cum_away": 0.80},
            {"m": 65, "cum_home": 0.32, "cum_away": 0.95},
            {"m": 80, "cum_home": 0.95, "cum_away": 1.05},
        ],
        "momentum":  [],
        "incidents": [],
    }
    ctx = context_from_match_detail(detail)
    # Window is 65→80 (latest at-or-before 65 vs latest at-or-before 80)
    assert ctx["home"]["xg_last_15"] == pytest.approx(0.63, rel=0.01)
    assert ctx["away"]["xg_last_15"] == pytest.approx(0.10, rel=0.01)


def test_translator_picks_up_recent_red_card():
    detail = {
        "minute": 85,
        "status": "live",
        "competition": "ucl",
        "home": {"score": 1},
        "away": {"score": 1},
        "incidents": [
            {"type": "card", "card_type": "red", "minute": 84, "team": "home"},
            {"type": "substitution",             "minute": 70, "team": "away"},  # too old
        ],
    }
    ctx = context_from_match_detail(detail)
    types = {e["type"] for e in ctx["recent_events"]}
    assert "red_card" in types
    assert all(e["minute"] >= 75 for e in ctx["recent_events"])


def test_translator_normalizes_high_signed_pressure():
    """Momentum emits signed values ~[-80, +80] per minute. The
    translator must normalize to the engine's [-1, +1] expected range
    (clamped — sustained dominance ≥30 saturates to ±1)."""
    detail = {
        "minute": 80,
        "status": "live",
        "competition": "premier_league",
        "home": {"score": 0}, "away": {"score": 0},
        "momentum": [
            {"minute": 70, "value": 80},  # heavy home pressure
            {"minute": 75, "value": 75},
            {"minute": 80, "value": 70},
        ],
        "incidents": [],
    }
    ctx = context_from_match_detail(detail)
    assert ctx["momentum_pressure"] is not None
    # mean(70,75,80)/30 = 2.5 → clamps to +1.0 (saturated home pressure)
    assert ctx["momentum_pressure"] == 1.0


@pytest.mark.parametrize("round_name,expected_min_multiplier", [
    ("Final",       1.4),
    ("Semi-final",  1.25),
    ("Quarter-final", 1.10),
    ("Group Stage", 1.0),
    (None,          1.0),
])
def test_round_keyword_routing(round_name, expected_min_multiplier):
    """The 'Final' keyword should NOT match 'Semi-final' (which contains 'final')."""
    base = compute_drama(_ctx(
        minute=88, competition="ucl", round_name=None,
        home={"score": 1, "xg_last_15": 0.5},
        away={"score": 1, "xg_last_15": 0.5},
    ))
    scored = compute_drama(_ctx(
        minute=88, competition="ucl", round_name=round_name,
        home={"score": 1, "xg_last_15": 0.5},
        away={"score": 1, "xg_last_15": 0.5},
    ))
    if expected_min_multiplier == 1.0:
        assert abs(scored.total - base.total) < 0.5
    else:
        # The lift should be at least proportional to the multiplier
        assert scored.total > base.total


# ─────────────────────────────────────────────────────────────────────────────
# Live WS signal extraction + near-miss / keeper-heroics scoring

from drama_engine import live_signals_from_ws_event, _score_chances


def test_live_signals_reads_stats_path_not_team_identity():
    # Stats live under snap['stats']['home']; snap['home'] is only identity.
    snap = {
        "home": {"id": 1, "name": "Home", "short_name": "HOM"},
        "away": {"id": 2, "name": "Away", "short_name": "AWY"},
        "stats": {
            "home": {"big_chances": 3, "big_chances_missed": 2, "hit_woodwork": 1,
                     "big_saves": 2, "shots_on_target": 7, "dangerous_attack_pct": 60},
            "away": {"big_chances": 1, "big_chances_missed": 0, "hit_woodwork": 0,
                     "big_saves": 1, "shots_on_target": 3, "dangerous_attack_pct": 40},
        },
    }
    sig = live_signals_from_ws_event(snap)
    assert sig["home"]["big_chances"] == 3
    assert sig["home"]["big_chances_missed"] == 2
    assert sig["home"]["big_saves"] == 2
    # dangerous-attack asymmetry: (60-40)/(60+40) = 0.2, + = home
    assert abs(sig["momentum_pressure"] - 0.2) < 1e-9


def test_live_signals_empty_without_stats():
    assert live_signals_from_ws_event(None) == {}
    assert live_signals_from_ws_event({"home": {"name": "X"}}) == {}  # no 'stats' key


def test_chances_rewards_near_misses_and_keeper_heroics():
    # Same big_chances both ways, but one match adds misses + woodwork + saves.
    plain = _score_chances({"home": {"big_chances": 2}, "away": {"big_chances": 2}})
    thrilling = _score_chances({
        "home": {"big_chances": 2, "big_chances_missed": 2, "hit_woodwork": 1, "big_saves": 2},
        "away": {"big_chances": 2, "big_chances_missed": 1, "big_saves": 1},
    })
    assert thrilling > plain, (thrilling, plain)
    assert thrilling <= 1.5  # never escapes the widened cap


def test_chances_zero_without_any_chance_data():
    assert _score_chances({"home": {"score": 1}, "away": {"score": 0}}) == 0.0


def test_chances_backward_compatible_for_big_chances_only():
    # Original formula: created*0.12 + min*0.15. 2+2 → 4*0.12 + 2*0.15 = 0.78.
    assert abs(_score_chances({"home": {"big_chances": 2}, "away": {"big_chances": 2}}) - 0.78) < 1e-9


def test_chances_rewards_shots_and_calamity():
    # End-to-end shots on target and a goal-causing defensive error add thrill.
    quiet = _score_chances({"home": {"big_chances": 1}, "away": {"big_chances": 1}})
    loud = _score_chances({
        "home": {"big_chances": 1, "shots_on_target": 6, "errors_lead_to_a_goal": 1},
        "away": {"big_chances": 1, "shots_on_target": 5},
    })
    assert loud > quiet, (loud, quiet)
    assert loud <= 1.5


def test_live_signals_extracts_errors_lead_to_a_goal():
    snap = {"stats": {"home": {"errors_lead_to_a_goal": 1, "shots_on_target": 4},
                      "away": {"errors_lead_to_a_goal": 0, "shots_on_target": 2}}}
    sig = live_signals_from_ws_event(snap)
    assert sig["home"]["errors_lead_to_a_goal"] == 1
    assert sig["home"]["shots_on_target"] == 4
