"""
Clutch Alarm – Smart Match Watch Score
========================================

Analyzes NBA game play-by-play data and produces a 1-10 "watch score"
rating based on tension, flow/quality, and clutch performance.

Uses ESPN's free play-by-play endpoint (unauthenticated) as API-Basketball
does not provide PBP data.

Category weights:
  35% - Clutch Performance (last 5 minutes drama)
  35% - Tension & Drama (lead changes, closeness, comebacks)
  30% - Flow & Quality (trading baskets, momentum runs, efficiency)
"""

import logging
import math
import requests
from dataclasses import dataclass, field

log = logging.getLogger("nba_monitor")

ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ScoringPlay:
    period: int
    clock_seconds: float   # seconds remaining in period
    game_seconds: float    # total elapsed seconds from game start
    home_score: int
    away_score: int
    diff: int              # home - away
    team_id: str
    score_value: int
    text: str


@dataclass
class WatchScoreResult:
    score: float
    tension_score: float
    flow_score: float
    clutch_score: float
    details: dict = field(default_factory=dict)
    buzzer_beater: bool = False
    overtime: bool = False


# ── Clock parsing ────────────────────────────────────────────────────────────

def _clock_to_seconds(clock_str: str) -> float:
    try:
        parts = clock_str.split(":")
        return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        return 0.0


def _game_elapsed(period: int, clock_seconds: float) -> float:
    if period <= 4:
        elapsed_before = (period - 1) * 720
        return elapsed_before + (720 - clock_seconds)
    else:
        elapsed_before = 4 * 720 + (period - 5) * 300
        return elapsed_before + (300 - clock_seconds)


# ── ESPN data fetch ──────────────────────────────────────────────────────────

def _fetch_espn_summary(espn_game_id: str) -> dict | None:
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={"event": espn_game_id}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("WatchScore: ESPN fetch failed for %s: %s", espn_game_id, exc)
        return None


def _extract_scoring_plays(summary: dict) -> tuple[list[ScoringPlay], dict]:
    plays = summary.get("plays", [])
    header = summary.get("header", {})
    comps = header.get("competitions", [{}])[0].get("competitors", [])
    home_id, away_id, home_abbr, away_abbr = "", "", "", ""
    for c in comps:
        t = c.get("team", {})
        if c.get("homeAway") == "home":
            home_id, home_abbr = str(t.get("id", "")), t.get("abbreviation", "")
        else:
            away_id, away_abbr = str(t.get("id", "")), t.get("abbreviation", "")

    scoring_plays: list[ScoringPlay] = []
    for p in plays:
        if not p.get("scoringPlay"):
            continue
        period = p.get("period", {}).get("number", 0)
        clock_secs = _clock_to_seconds(p.get("clock", {}).get("displayValue", "0:00"))
        scoring_plays.append(ScoringPlay(
            period=period,
            clock_seconds=clock_secs,
            game_seconds=_game_elapsed(period, clock_secs),
            home_score=p.get("homeScore", 0),
            away_score=p.get("awayScore", 0),
            diff=p.get("homeScore", 0) - p.get("awayScore", 0),
            team_id=str(p.get("team", {}).get("id", "")),
            score_value=p.get("scoreValue", 0),
            text=p.get("text", ""),
        ))

    return scoring_plays, {
        "home_id": home_id, "away_id": away_id,
        "home_abbr": home_abbr, "away_abbr": away_abbr,
    }


# ── Category A: Tension & Drama (35%) ────────────────────────────────────────

def _calc_tension(plays: list[ScoringPlay]) -> tuple[float, dict]:
    if len(plays) < 5:
        return 0.0, {}

    # 1. Lead swaps & ties
    lead_swaps = 0
    ties = 0
    prev_leader = 0
    for p in plays:
        if p.diff == 0:
            ties += 1
            if prev_leader != 0:
                lead_swaps += 1
            prev_leader = 0
        elif p.diff > 0:
            if prev_leader == -1:
                lead_swaps += 1
            prev_leader = 1
        else:
            if prev_leader == 1:
                lead_swaps += 1
            prev_leader = -1

    # 15+ lead swaps = 10, scale linearly
    swap_score = min(10, lead_swaps * 0.65 + ties * 0.2)

    # 2. Closeness: how tight was the game through each quarter?
    # Split into quarters, measure median diff per quarter
    quarter_diffs: dict[int, list[int]] = {}
    for p in plays:
        quarter_diffs.setdefault(p.period, []).append(abs(p.diff))

    # Weighted closeness: later quarters matter more
    quarter_weights = {1: 0.10, 2: 0.15, 3: 0.25, 4: 0.35, 5: 0.15}
    weighted_closeness = 0.0
    total_weight = 0.0
    for q, diffs in quarter_diffs.items():
        if not diffs:
            continue
        sorted_d = sorted(diffs)
        median = sorted_d[len(sorted_d) // 2]
        # median 0 = 10, median 15+ = 0
        q_score = max(0, 10 - median * 0.65)
        w = quarter_weights.get(q, 0.15)
        weighted_closeness += q_score * w
        total_weight += w

    closeness_score = weighted_closeness / total_weight if total_weight > 0 else 0

    # 3. Comeback factor
    max_home_deficit = 0
    max_away_deficit = 0
    for p in plays:
        if p.diff < 0:
            max_home_deficit = max(max_home_deficit, abs(p.diff))
        elif p.diff > 0:
            max_away_deficit = max(max_away_deficit, p.diff)

    final_diff = plays[-1].diff
    comeback_score = 0.0
    max_deficit = max(max_home_deficit, max_away_deficit)
    if max_deficit >= 8:
        # Successful comeback: trailing team ended up winning or within 3
        home_trailed_badly = max_home_deficit >= 8
        away_trailed_badly = max_away_deficit >= 8
        came_back = (home_trailed_badly and final_diff >= -3) or \
                    (away_trailed_badly and final_diff <= 3)
        if came_back:
            comeback_score = min(10, max_deficit * 0.65)
        elif max_deficit >= 15:
            comeback_score = min(6, max_deficit * 0.3)

    # Combined: 30% swaps, 35% closeness, 35% comeback
    tension = swap_score * 0.30 + closeness_score * 0.35 + comeback_score * 0.35

    details = {
        "lead_swaps": lead_swaps, "ties": ties,
        "swap_score": round(swap_score, 1),
        "closeness_score": round(closeness_score, 1),
        "max_deficit": max_deficit,
        "comeback_score": round(comeback_score, 1),
    }
    return min(10, tension), details


# ── Category B: Flow & Quality (30%) ─────────────────────────────────────────

def _calc_flow(plays: list[ScoringPlay]) -> tuple[float, dict]:
    if len(plays) < 5:
        return 0.0, {}

    # 1. Trading baskets: quick responses (within 25 sec)
    quick_responses = 0
    total_sequences = 0
    for i in range(1, len(plays)):
        if plays[i].team_id != plays[i - 1].team_id:
            total_sequences += 1
            gap = plays[i].game_seconds - plays[i - 1].game_seconds
            if 0 < gap <= 25:
                quick_responses += 1

    trading_pct = (quick_responses / total_sequences * 100) if total_sequences > 0 else 0
    # 50%+ = 10, 15% = 0
    trading_score = min(10, max(0, (trading_pct - 15) / 3.5))

    # 2. Momentum runs
    runs: list[int] = []
    current_run = 0
    current_team = ""
    for p in plays:
        if p.team_id == current_team:
            current_run += p.score_value
        else:
            if current_run > 0:
                runs.append(current_run)
            current_run = p.score_value
            current_team = p.team_id
    if current_run > 0:
        runs.append(current_run)

    big_runs = [r for r in runs if r >= 8]
    max_run = max(runs) if runs else 0
    # Big runs add excitement but cap the score
    run_score = min(10, len(big_runs) * 1.5 + (max_run / 20.0) * 4)

    # 3. Scoring pace & flow
    total_pts = plays[-1].home_score + plays[-1].away_score if plays else 0
    game_duration_min = plays[-1].game_seconds / 60 if plays else 48
    pts_per_min = total_pts / game_duration_min if game_duration_min > 0 else 0

    # Good NBA pace: 4.5-5.5 pts/min. Too low = boring, too high = no defense
    pace_score = 0.0
    if pts_per_min >= 4.0:
        pace_score = min(10, (pts_per_min - 3.0) * 4)
    if pts_per_min > 6.0:
        pace_score = max(5, pace_score - (pts_per_min - 6.0) * 2)  # slight penalty for no defense

    # Penalty for excessive free throws
    ft_plays = sum(1 for p in plays if "free throw" in p.text.lower())
    ft_ratio = ft_plays / len(plays) if plays else 0
    ft_penalty = 1.0
    if ft_ratio > 0.20:
        ft_penalty = max(0.6, 1.0 - (ft_ratio - 0.20) * 3)

    # Combined: 40% trading, 25% runs, 35% pace
    flow = (trading_score * 0.40 + run_score * 0.25 + pace_score * 0.35) * ft_penalty

    details = {
        "quick_responses": quick_responses, "total_sequences": total_sequences,
        "trading_pct": round(trading_pct, 1),
        "trading_score": round(trading_score, 1),
        "big_runs": len(big_runs), "max_run": max_run,
        "run_score": round(run_score, 1),
        "pts_per_min": round(pts_per_min, 2),
        "pace_score": round(pace_score, 1),
        "ft_penalty": round(ft_penalty, 2),
    }
    return min(10, flow), details


# ── Category C: Clutch Performance (35%) ──────────────────────────────────────

def _calc_clutch(plays: list[ScoringPlay]) -> tuple[float, dict]:
    if len(plays) < 5:
        return 0.0, {}

    max_period = max(p.period for p in plays)

    # Clutch = last 5 min of Q4 + all OT
    clutch_plays = [p for p in plays
                    if (p.period == 4 and p.clock_seconds <= 300) or p.period > 4]

    if not clutch_plays:
        return 0.0, {}

    # 1. Proximity: how close was the score during clutch time?
    clutch_diffs = [abs(p.diff) for p in clutch_plays]
    avg_clutch_diff = sum(clutch_diffs) / len(clutch_diffs)
    max_clutch_diff = max(clutch_diffs)

    # A game that's 20+ pts apart in clutch time has zero clutch value
    if avg_clutch_diff > 15:
        return 0.0, {
            "clutch_plays": len(clutch_plays), "avg_clutch_diff": round(avg_clutch_diff, 1),
            "max_clutch_diff": max_clutch_diff, "clutch_lead_changes": 0,
            "proximity_score": 0, "density_score": 0, "lead_change_score": 0,
            "note": "blowout in clutch time",
        }

    # avg 0-1 = 10, avg 5 = 5, avg 10 = 0
    proximity_score = max(0, 10 - avg_clutch_diff * 1.0)
    if max_clutch_diff <= 5:
        proximity_score = min(10, proximity_score + 1.5)

    # 2. Clutch density: scoring plays in clutch time (only meaningful if close)
    density_score = min(10, len(clutch_plays) * 0.6)

    # 3. Lead changes in clutch
    clutch_lead_changes = 0
    prev_sign = 0
    for p in clutch_plays:
        sign = 1 if p.diff > 0 else (-1 if p.diff < 0 else 0)
        if sign != 0 and sign != prev_sign and prev_sign != 0:
            clutch_lead_changes += 1
        if sign != 0:
            prev_sign = sign

    # 4+ clutch lead changes = incredible
    lead_change_score = min(10, clutch_lead_changes * 2.5)

    # 4. Final margin tightness bonus
    final_margin = abs(plays[-1].diff)
    margin_bonus = 0.0
    if final_margin <= 3:
        margin_bonus = 3.0
    elif final_margin <= 5:
        margin_bonus = 1.5
    elif final_margin <= 8:
        margin_bonus = 0.5

    # Combined: 35% proximity, 20% density, 30% lead changes, 15% margin
    clutch = (proximity_score * 0.35 + density_score * 0.20 +
              lead_change_score * 0.30 + margin_bonus * 0.15 / 0.15)

    # Scale margin_bonus properly
    clutch = proximity_score * 0.35 + density_score * 0.20 + \
             lead_change_score * 0.30 + min(10, margin_bonus * 3.3) * 0.15

    details = {
        "clutch_plays": len(clutch_plays),
        "avg_clutch_diff": round(avg_clutch_diff, 1),
        "max_clutch_diff": max_clutch_diff,
        "clutch_lead_changes": clutch_lead_changes,
        "proximity_score": round(proximity_score, 1),
        "density_score": round(density_score, 1),
        "lead_change_score": round(lead_change_score, 1),
        "final_margin": final_margin,
    }
    return min(10, clutch), details


# ── Buzzer Beater Detection ──────────────────────────────────────────────────

def _detect_buzzer_beater(plays: list[ScoringPlay]) -> bool:
    if len(plays) < 2:
        return False
    max_period = max(p.period for p in plays)
    last_play = plays[-1]
    if last_play.clock_seconds <= 3.0 and last_play.period == max_period:
        if len(plays) >= 2:
            prev_diff = plays[-2].diff
            curr_diff = last_play.diff
            if (prev_diff <= 0 and curr_diff > 0) or (prev_diff >= 0 and curr_diff < 0):
                return True
    return False


# ── Blowout dampener ─────────────────────────────────────────────────────────

def _blowout_multiplier(plays: list[ScoringPlay]) -> float:
    """
    Returns a multiplier 0.0-1.0 that dampens the score for blowouts.
    A game where one team led by 20+ for most of the second half is not
    exciting regardless of individual flow or early drama.
    """
    if len(plays) < 10:
        return 1.0

    # Check second half plays (Q3+Q4)
    second_half = [p for p in plays if p.period >= 3]
    if not second_half:
        return 1.0

    diffs = [abs(p.diff) for p in second_half]
    avg_2h_diff = sum(diffs) / len(diffs)
    sorted_d = sorted(diffs)
    median_2h_diff = sorted_d[len(sorted_d) // 2]

    # If median second half diff is 20+ → total blowout → scale to 40%
    # If 15+ → scale to 60%
    # If 10+ → scale to 80%
    if median_2h_diff >= 20:
        return 0.40
    elif median_2h_diff >= 15:
        return 0.55
    elif median_2h_diff >= 12:
        return 0.70
    elif median_2h_diff >= 10:
        return 0.85
    return 1.0


# ── Main scoring engine ──────────────────────────────────────────────────────

def calculate_watch_score(espn_game_id: str) -> WatchScoreResult | None:
    summary = _fetch_espn_summary(espn_game_id)
    if not summary:
        return None

    if not summary.get("plays"):
        return None

    scoring_plays, team_info = _extract_scoring_plays(summary)
    if len(scoring_plays) < 10:
        return None

    tension, tension_details = _calc_tension(scoring_plays)
    flow, flow_details = _calc_flow(scoring_plays)
    clutch, clutch_details = _calc_clutch(scoring_plays)

    # Weighted average: 35% clutch, 35% tension, 30% flow
    raw_score = clutch * 0.35 + tension * 0.35 + flow * 0.30

    # Determine OT before dampener — a game that reached OT can't be a blowout
    max_period = max(p.period for p in scoring_plays)
    is_overtime = max_period > 4

    # Apply blowout dampener — even if flow was good, a 30-pt game is boring
    # Skip for OT games: if it went to OT it was close, dampener would be wrong
    blowout_mult = 1.0 if is_overtime else _blowout_multiplier(scoring_plays)
    raw_score *= blowout_mult

    # Apply upper-stretch curve: push good games into 8-9 range
    # while keeping blowouts low. Formula: x^0.78 * 10^0.22
    if raw_score > 0:
        raw_score = (raw_score ** 0.78) * (10 ** 0.22)

    # Bonuses
    buzzer_beater = _detect_buzzer_beater(scoring_plays)

    final_score = raw_score

    # OT bonus: +1.0, then enforce minimum of 8.0 for any OT game
    if is_overtime:
        final_score += 1.0
        final_score = max(8.0, final_score)

    # Buzzer beater: minimum 9.5
    if buzzer_beater:
        final_score = max(final_score, 9.5)

    # Floor: even worst games get 1.0
    final_score = max(1.0, min(10.0, round(final_score, 1)))

    return WatchScoreResult(
        score=final_score,
        tension_score=round(tension, 1),
        flow_score=round(flow, 1),
        clutch_score=round(clutch, 1),
        details={
            "tension": tension_details,
            "flow": flow_details,
            "clutch": clutch_details,
            "teams": team_info,
            "total_scoring_plays": len(scoring_plays),
            "final_score_home": scoring_plays[-1].home_score,
            "final_score_away": scoring_plays[-1].away_score,
            "blowout_multiplier": round(blowout_mult, 2),
        },
        buzzer_beater=buzzer_beater,
        overtime=is_overtime,
    )


# ── Convenience: score by team abbreviations ─────────────────────────────────

def calculate_watch_score_by_teams(
    home_abbr: str, away_abbr: str, date_yyyymmdd: str,
) -> WatchScoreResult | None:
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        resp = requests.get(url, params={"dates": date_yyyymmdd}, timeout=10)
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            event_home, event_away = "", ""
            for c in comp.get("competitors", []):
                abbr = c.get("team", {}).get("abbreviation", "")
                if c.get("homeAway") == "home":
                    event_home = abbr
                else:
                    event_away = abbr
            if event_home == home_abbr and event_away == away_abbr:
                return calculate_watch_score(str(event["id"]))
    except Exception as exc:
        log.warning("WatchScore team lookup failed: %s", exc)
    return None
