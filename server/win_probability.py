"""Implied win-probability from consensus 1X2 odds (BSD odds snapshot).

Pure functions, no I/O — fully unit-testable. Used to add a live "market
excitement" signal to the Drama Engine: a coin-flip match (~50/50) is tense, a
near-decided one is not, and a sharp swing in the favorite's probability marks a
clutch moment (a goal/red card that flipped the match).

BSD odds snapshot shape (confirmed via /events/{id}/odds/):
    {"event_id": int, "odds": {"home_win": 2.29, "draw": 2.29, "away_win": 2.5, ...}}
"""
from __future__ import annotations


def implied_probs_1x2(
    home: float | None, draw: float | None, away: float | None
) -> tuple[float, float, float] | None:
    """Return (p_home, p_draw, p_away) normalized to sum 1 with the bookmaker
    overround removed. Decimal odds in. None if any leg is missing or <= 1.0."""
    try:
        h, d, a = float(home), float(draw), float(away)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if h <= 1.0 or d <= 1.0 or a <= 1.0:
        return None
    inv_h, inv_d, inv_a = 1.0 / h, 1.0 / d, 1.0 / a
    total = inv_h + inv_d + inv_a
    if total <= 0:
        return None
    return (inv_h / total, inv_d / total, inv_a / total)


# Key spellings seen across the BSD odds snapshot and individual books.
_HOME_KEYS = ("home_win", "home", "1")
_DRAW_KEYS = ("draw", "x", "X")
_AWAY_KEYS = ("away_win", "away", "2")


def _first(d: dict, keys) -> float | None:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def win_prob_from_snapshot(odds_snap: dict | None) -> tuple[float, float, float] | None:
    """Extract (p_home, p_draw, p_away) from a BSD odds snapshot. Tolerates the
    `{"odds": {...}}` envelope, a nested `match_winner` object, and a few key
    spellings. Returns None when a valid 1X2 isn't present."""
    if not isinstance(odds_snap, dict):
        return None
    odds = odds_snap.get("odds") if isinstance(odds_snap.get("odds"), dict) else odds_snap
    if not isinstance(odds, dict):
        return None
    mw = odds.get("match_winner")
    if isinstance(mw, dict):
        return implied_probs_1x2(
            _first(mw, _HOME_KEYS), _first(mw, _DRAW_KEYS), _first(mw, _AWAY_KEYS)
        )
    return implied_probs_1x2(
        _first(odds, _HOME_KEYS), _first(odds, _DRAW_KEYS), _first(odds, _AWAY_KEYS)
    )


def lead_prob(probs: tuple[float, float, float] | None) -> float | None:
    """The favorite's win probability = max(p_home, p_away). High when decided,
    ~0.4 in a true coin-flip."""
    if not probs:
        return None
    return max(probs[0], probs[2])
