"""Soccer Drama Engine — Clutch Alarm v3 (P3).

Pure scoring module. No I/O. No network. Given a normalized match payload,
returns a `DramaScore` ∈ [0, 10] plus the four component scores so the
caller can debug, log, or display them.

The formula is:

    drama = min(10, max(0, state + momentum + events) × stakes)

Components:

- **state** (0–7)   — How the live snapshot feels: score diff + lateness.
                      A 0-0 at 89' is dramatic; a 0-0 at 5' is not.
- **momentum** (0–4) — How fast the pressure is shifting. Weighted by who is
                      losing. The user's framing: *"1-0 with the trailing
                      team dominating = worth waking up; 1-0 with the
                      leading team dominating = not worth"*. That asymmetry
                      lives here.
- **events** (0–4)   — Discrete drama injectors in the last few minutes:
                      red cards, penalties awarded, late goals, big chances.
- **stakes** (0.6–2) — Multiplier from the competition and round (knockout
                      vs group, final vs quarter, World Cup vs Ligue 1).

The "state + momentum + events" sum is clamped to ≥ 0 before multiplying
because a boring blowout shouldn't get *more* dramatic because it's the
World Cup final — the stakes only amplify already-present drama.

See `tests/test_drama_engine.py` for the calibration fixtures driving these
constants. Tune by adjusting weights, then re-running the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


# ── Stakes table ─────────────────────────────────────────────────────────────
# Tuned by hand. Tournament finals get an extra round bump on top of these.

_COMPETITION_STAKES: dict[str, float] = {
    "world_cup":     1.55,
    "euros":         1.40,
    "ucl":           1.30,
    "europa_league": 1.10,
    "premier_league": 1.15,
    "la_liga":       1.05,
    "serie_a":       1.05,
    "bundesliga":    1.00,
    "ligue_1":       0.95,
    "ligat_haal":    0.85,
}

# Round-name keywords (lower-cased contains-match). Order matters — first
# match wins, so put more-specific keys above more-general ones.
_ROUND_MULTIPLIERS: list[tuple[str, float]] = [
    # Order matters — more specific keywords first, because "Quarter-final"
    # and "Semi-final" contain the substring "final".
    ("quarter",         1.10),
    ("round of 16",     1.05),
    ("knockout",        1.05),
    ("playoff",         1.05),
    ("semi",            1.25),
    ("final",           1.40),
    ("group stage",     1.00),
]


@dataclass
class DramaScore:
    """Final drama breakdown. `total` is what alarms compare against."""
    total:    float   # 0–10, what the user threshold compares against
    state:    float   # 0–7
    momentum: float   # 0–4
    events:   float   # 0–4
    stakes:   float   # 0.6–2

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point


def compute_drama(ctx: dict[str, Any]) -> DramaScore:
    """Score a single match.

    `ctx` shape (all keys optional except `home`/`away`):

        {
          "minute":      int,                 # 0–120ish
          "status":      str,                 # 'scheduled'|'live'|'ht'|'ft'|'et'|'pens'
          "competition": str,                 # slug — 'premier_league' etc.
          "round_name":  str | None,          # 'Final' / 'Group Stage' / etc.
          "is_derby":    bool,
          "is_neutral_ground": bool,
          "home": {"score": int | None, "xg": float | None, "xg_last_15": float | None},
          "away": {"score": int | None, "xg": float | None, "xg_last_15": float | None},
          "momentum_pressure": float | None,  # BSD momentum mean, [-1, 1], + = home
          "recent_events": [                  # last ~10 min of incidents
              {"type": str, "minute": int, "team": "home"|"away"|None}
          ],
        }

    Missing fields degrade gracefully — the engine never raises on a
    half-populated match.
    """
    state    = _score_state(ctx)
    momentum = _score_momentum(ctx)
    events   = _score_events(ctx)
    stakes   = _stakes_multiplier(ctx)

    # Clamp the raw sum at 7 so stakes (~0.85–2.17) always have headroom
    # to push the final number up to the 10-cap. Without this, every
    # close-late tied game in any league saturates at 10 and the league
    # signal is lost.
    raw = min(7.0, max(0.0, state + momentum + events))
    total = min(10.0, raw * stakes)
    return DramaScore(
        total=round(total, 2),
        state=round(state, 2),
        momentum=round(momentum, 2),
        events=round(events, 2),
        stakes=round(stakes, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# State — score snapshot + lateness


def _score_state(ctx: dict) -> float:
    home_sc = (ctx.get("home") or {}).get("score") or 0
    away_sc = (ctx.get("away") or {}).get("score") or 0
    diff    = abs(home_sc - away_sc)
    minute  = ctx.get("minute") or 0
    status  = (ctx.get("status") or "").lower()

    # Pre-match has no live state to speak of
    if status in {"scheduled", "postponed", "canceled"}:
        return 0.0

    # Base closeness — how tense the scoreline is on its own
    if   diff == 0: base, late_max = 3.0, 1.5
    elif diff == 1: base, late_max = 2.0, 0.8  # 1-goal late is only dramatic
                                                # if the trailer is pressuring;
                                                # we let momentum do that work
    elif diff == 2: base, late_max = 1.0, 0.3
    else:           base, late_max = 0.3, 0.0   # blowout

    # Lateness amplifier — kicks in from 60', peaks at 90'+
    if minute >= 60 and late_max > 0:
        late_factor = min(late_max, max(0.0, (minute - 60) / 20.0 * late_max))
        base *= (1.0 + late_factor)

    # Extra time and penalties are automatically high-state
    if status == "et":
        base = max(base, 5.5)
    elif status == "pens":
        base = max(base, 6.5)

    # Half-time of a close game is mildly dramatic but cooled off
    if status == "ht":
        base *= 0.6

    # Final whistle — the state moment is over; let the *events* score
    # carry any post-FT drama (e.g. a 95th-minute winner).
    if status == "ft":
        base *= 0.4

    return min(7.0, base)


# ─────────────────────────────────────────────────────────────────────────────
# Momentum — the user's "pressure vs scoreline" insight


def _score_momentum(ctx: dict) -> float:
    home = ctx.get("home") or {}
    away = ctx.get("away") or {}
    home_sc = home.get("score") or 0
    away_sc = away.get("score") or 0

    home_xg15 = home.get("xg_last_15")
    away_xg15 = away.get("xg_last_15")
    pressure  = ctx.get("momentum_pressure")  # BSD: +1 = home control, -1 = away

    # Without ANY momentum signal there's nothing to score from
    if home_xg15 is None and away_xg15 is None and pressure is None:
        return 0.0

    # Synthesize a single "trailing pressure" number ∈ [-1, +1] where
    # positive means the team behind is dominating.
    trailing_pressure = 0.0
    n_signals = 0

    if home_xg15 is not None and away_xg15 is not None:
        total_xg = home_xg15 + away_xg15
        if total_xg > 0.05:                     # ignore noise-floor xG
            if home_sc < away_sc:               # home trails
                trailing_pressure += (home_xg15 - away_xg15) / max(total_xg, 0.5)
            elif away_sc < home_sc:             # away trails
                trailing_pressure += (away_xg15 - home_xg15) / max(total_xg, 0.5)
            else:                               # tied — modest baseline so the
                                                # round-keyword/stakes tests have
                                                # headroom to distinguish leagues
                trailing_pressure += min(1.0, total_xg * 0.4)
            n_signals += 1

    if pressure is not None:
        # BSD momentum: +1 = home, -1 = away. Translate to trailing-side.
        if home_sc < away_sc:
            trailing_pressure += -pressure      # negative pressure means away (leading), so flip
        elif away_sc < home_sc:
            trailing_pressure += pressure       # positive pressure means home (trailing) winning the moment
        else:
            trailing_pressure += abs(pressure)  # tied — direction doesn't matter
        n_signals += 1

    if n_signals == 0:
        return 0.0
    trailing_pressure /= n_signals

    # Map [-1, +1] trailing pressure → momentum drama. Trailing team
    # pressure adds drama (0 → +4); leading team running up the game
    # *subtracts* drama (down to -1.5). That negative branch is what
    # makes "1-0 with the leader dominating = not worth waking up" fall
    # below the threshold even when the scoreline alone would be close.
    if trailing_pressure > 0:
        return min(4.0, 0.5 + trailing_pressure * 3.0)
    else:
        return max(-1.5, trailing_pressure * 1.5)


# ─────────────────────────────────────────────────────────────────────────────
# Events — discrete drama injectors


_EVENT_WEIGHTS: dict[str, float] = {
    "red_card":         1.6,
    "var_review":       0.6,
    "big_chance":       0.4,
    "missed_penalty":   1.2,
    # 'goal' and 'penalty_awarded' carry a lateness bonus; see _score_events.
}


def _score_events(ctx: dict) -> float:
    incidents = ctx.get("recent_events") or []
    if not incidents:
        return 0.0

    minute = ctx.get("minute") or 0
    score = 0.0

    for ev in incidents:
        ev_type = (ev.get("type") or "").lower()
        ev_min  = ev.get("minute") or minute
        late_bonus = max(0.0, (ev_min - 60) / 30.0)   # 0 at 60', 1.0 at 90'+

        if ev_type == "goal":
            # 88th-minute goals matter much more than 30th-minute ones
            score += 1.0 + late_bonus * 0.7
        elif ev_type == "penalty_awarded":
            score += 1.8 + late_bonus * 0.6
        else:
            score += _EVENT_WEIGHTS.get(ev_type, 0.0)

    return min(4.0, score)


# ─────────────────────────────────────────────────────────────────────────────
# Stakes — competition + round multiplier


def _stakes_multiplier(ctx: dict) -> float:
    comp = (ctx.get("competition") or "").lower()
    base = _COMPETITION_STAKES.get(comp, 1.0)

    # Round bump — final > semi > quarter > group. Keywords are checked
    # most-specific-first so "Quarter-final" and "Semi-final" don't
    # accidentally match the bare "final" rule.
    round_name = (ctx.get("round_name") or "").lower()
    if round_name:
        for keyword, mult in _ROUND_MULTIPLIERS:
            if keyword in round_name:
                base *= mult
                break

    # Derby bump — local rivalries carry their own weight
    if ctx.get("is_derby"):
        base *= 1.1

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Translator — takes the rich match-detail payload that
# soccer_source.get_soccer_match_detail() returns and reduces it to the slim
# ctx dict compute_drama() consumes. Still pure: no network, no I/O.


def context_from_match_detail(detail: dict) -> dict:
    """Build a Drama Engine ctx from the rich BSD/Sofascore match detail.

    Robust to missing fields — anything absent just becomes None and the
    engine handles the gap gracefully.
    """
    if not detail:
        return {"home": {}, "away": {}}

    home = (detail.get("home") or {}).copy()
    away = (detail.get("away") or {}).copy()
    minute = detail.get("minute") or 0
    status = detail.get("status")

    # ── xG window: cumulative xg at (minute-15) vs at (minute) ──────────────
    xg_per_minute = detail.get("xg_per_minute") or []
    if xg_per_minute and minute > 0:
        window_start = max(0, minute - 15)
        cum_at_start = _cum_xg_at(xg_per_minute, window_start)
        cum_at_now   = _cum_xg_at(xg_per_minute, minute)
        if cum_at_start is not None and cum_at_now is not None:
            home["xg_last_15"] = max(0.0, cum_at_now[0] - cum_at_start[0])
            away["xg_last_15"] = max(0.0, cum_at_now[1] - cum_at_start[1])

    # ── Momentum pressure: mean of last 15 min of {m, v} points ──────────────
    pressure = None
    momentum_points = detail.get("momentum") or []
    if momentum_points:
        # Tolerate Sofascore graphPoint shape ({minute, value}) too
        recent = []
        for p in momentum_points:
            m = p.get("m", p.get("minute"))
            v = p.get("v", p.get("value"))
            if m is None or v is None:
                continue
            if minute - 15 <= m <= minute:
                try:
                    recent.append(float(v))
                except (TypeError, ValueError):
                    continue
        if recent:
            pressure = sum(recent) / len(recent)
            # Sofascore graph runs 0–100 with 50 neutral; rescale to [-1, +1].
            # BSD is already roughly in that range, so the rescale is a no-op
            # when the values come in signed.
            if any(abs(v) > 1.5 for v in recent):
                pressure = (pressure - 50.0) / 50.0

    # ── Recent events: last 10 minutes of normalized incidents ──────────────
    recent_events = _normalize_recent_incidents(
        detail.get("incidents") or [],
        minute_now=minute,
        window_min=10,
    )

    return {
        "minute":             minute,
        "status":             status,
        "competition":        detail.get("competition"),
        "round_name":         detail.get("round_name"),
        "is_derby":           bool(detail.get("is_derby")),
        "is_neutral_ground":  bool(detail.get("is_neutral_ground")),
        "home":               home,
        "away":               away,
        "momentum_pressure":  pressure,
        "recent_events":      recent_events,
    }


def _cum_xg_at(timeline: list[dict], minute: float) -> tuple[float, float] | None:
    """Find the latest cum-xg entry at-or-before `minute` in BSD's xg_per_minute."""
    best = None
    for entry in timeline:
        m = entry.get("m")
        if m is None or m > minute:
            continue
        if best is None or m > (best.get("m") or -1):
            best = entry
    if best is None:
        return None
    try:
        return float(best["cum_home"]), float(best["cum_away"])
    except (KeyError, TypeError, ValueError):
        return None


# Map raw incident `type`/`incidentClass` strings to the engine's vocabulary.
_INCIDENT_TYPE_MAP: dict[str, str] = {
    "goal":            "goal",
    "owngoal":         "goal",
    "own_goal":        "goal",
    "penalty":         "penalty_awarded",
    "penalty_awarded": "penalty_awarded",
    "missedpenalty":   "missed_penalty",
    "missed_penalty":  "missed_penalty",
    "varDecision":     "var_review",
    "var":             "var_review",
    "bigchance":       "big_chance",
    "big_chance":      "big_chance",
}


def _normalize_recent_incidents(
    incidents: list[dict],
    *,
    minute_now: int,
    window_min: int,
) -> list[dict]:
    """Return [{type, minute, team}] for incidents in the last `window_min`."""
    cutoff = max(0, minute_now - window_min)
    out: list[dict] = []
    for inc in incidents:
        # BSD: {type, minute, ...}  /  Sofascore: {incidentType, time, ...}
        raw_type  = (inc.get("type") or inc.get("incidentType") or "").lower()
        card_type = (inc.get("card_type") or inc.get("incidentClass") or "").lower()
        inc_min   = inc.get("minute") or inc.get("time") or 0
        if inc_min < cutoff:
            continue

        # Red-card detection (the most important non-goal incident)
        if "card" in raw_type and "red" in card_type:
            ev_type = "red_card"
        elif raw_type == "red" or card_type == "redcard":
            ev_type = "red_card"
        else:
            ev_type = _INCIDENT_TYPE_MAP.get(raw_type)
            if ev_type is None:
                continue

        team = inc.get("team")  # BSD has 'home'/'away' here
        if isinstance(team, dict):
            team = team.get("side") or team.get("name")
        out.append({"type": ev_type, "minute": inc_min, "team": team})
    return out
