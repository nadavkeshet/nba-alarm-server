"""
Clutch Alarm – Smart Match Watch Score
========================================

Analyzes game play-by-play data and produces a 1-10 "watch score"
rating based on tension, flow/quality, and clutch performance.

Uses 365scores' SportRadar play-by-play feed (no API key required).

Category weights:
  35% - Clutch Performance (last 5 minutes drama)
  35% - Tension & Drama (lead changes, closeness, comebacks)
  30% - Flow & Quality (trading baskets, momentum runs, efficiency)
"""

import logging
import httpx
from dataclasses import dataclass, field

from score365.client import Client365
from score365.scraper import get_game_detail
from score365_adapter import fetch_games, _make_team_obj

log = logging.getLogger("nba_monitor")


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class ScoringPlay:
    period: int
    clock_seconds: float   # seconds remaining in period
    game_seconds: float    # total elapsed seconds from game start
    home_score: int
    away_score: int
    diff: int              # home - away
    team_id: str           # "1" home, "2" away (CompetitorNum)
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
    """365scores Timeline is 'MM:SS' remaining in current period."""
    try:
        parts = clock_str.split(":")
        return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        return 0.0


def _game_elapsed(period: int, clock_seconds: float, period_length_sec: int = 720) -> float:
    """Total seconds elapsed from game start. NBA periods = 720s, OT = 300s."""
    if period <= 4:
        elapsed_before = (period - 1) * period_length_sec
        return elapsed_before + (period_length_sec - clock_seconds)
    else:
        elapsed_before = 4 * period_length_sec + (period - 5) * 300
        return elapsed_before + (300 - clock_seconds)


# ── 365scores PBP fetch ──────────────────────────────────────────────────────

_SCORE_VALUES = {
    "two point made":    2,
    "three point made":  3,
    "free throw made":   1,
}


def _fetch_pbp(game_id: int) -> tuple[list[dict], dict] | None:
    """
    Fetch the SportRadar play-by-play feed for a 365scores game.
    Returns (messages, game_detail) or None on failure.
    """
    try:
        with Client365() as client:
            game = get_game_detail(client, int(game_id))
        pbp_meta = game.get("playByPlay", {})
        feed_url = pbp_meta.get("feedURL")
        if not feed_url:
            return None
        resp = httpx.get(feed_url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("Messages", [])
        return messages, game
    except Exception as exc:
        log.warning("WatchScore: 365scores PBP fetch failed for %s: %s", game_id, exc)
        return None


def _extract_scoring_plays(messages: list[dict], game: dict) -> tuple[list[ScoringPlay], dict]:
    """Convert 365scores PBP messages into ScoringPlay objects."""
    home = _make_team_obj(game.get("homeCompetitor", {}))
    away = _make_team_obj(game.get("awayCompetitor", {}))
    team_info = {
        "home_id":   home["id"],
        "away_id":   away["id"],
        "home_abbr": home["abbreviation"],
        "away_abbr": away["abbreviation"],
    }

    # Sort by message Id ascending → chronological order
    sorted_msgs = sorted(messages, key=lambda m: m.get("Id", 0))

    plays: list[ScoringPlay] = []
    for m in sorted_msgs:
        type_name = (m.get("TypeName") or "").lower()
        score_value = _SCORE_VALUES.get(type_name)
        if not score_value:
            continue

        try:
            period = int(m.get("Period", 0))
        except (ValueError, TypeError):
            continue

        clock_secs = _clock_to_seconds(m.get("Timeline", "0:00"))
        score = m.get("Score") or [0, 0]
        if not isinstance(score, list) or len(score) < 2:
            continue
        home_score, away_score = int(score[0]), int(score[1])

        comp_num = m.get("CompetitorNum")
        if comp_num == 1:
            team_id = home["id"]
        elif comp_num == 2:
            team_id = away["id"]
        else:
            team_id = ""

        plays.append(ScoringPlay(
            period=period,
            clock_seconds=clock_secs,
            game_seconds=_game_elapsed(period, clock_secs),
            home_score=home_score,
            away_score=away_score,
            diff=home_score - away_score,
            team_id=team_id,
            score_value=score_value,
            text=m.get("Comment", ""),
        ))

    return plays, team_info


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

    swap_score = min(10, lead_swaps * 0.65 + ties * 0.2)

    # 2. Closeness per quarter
    quarter_diffs: dict[int, list[int]] = {}
    for p in plays:
        quarter_diffs.setdefault(p.period, []).append(abs(p.diff))

    quarter_weights = {1: 0.10, 2: 0.15, 3: 0.25, 4: 0.35, 5: 0.15}
    weighted_closeness = 0.0
    total_weight = 0.0
    for q, diffs in quarter_diffs.items():
        if not diffs:
            continue
        sorted_d = sorted(diffs)
        median = sorted_d[len(sorted_d) // 2]
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
        home_trailed_badly = max_home_deficit >= 8
        away_trailed_badly = max_away_deficit >= 8
        came_back = (home_trailed_badly and final_diff >= -3) or \
                    (away_trailed_badly and final_diff <= 3)
        if came_back:
            comeback_score = min(10, max_deficit * 0.65)
        elif max_deficit >= 15:
            comeback_score = min(6, max_deficit * 0.3)

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

    quick_responses = 0
    total_sequences = 0
    for i in range(1, len(plays)):
        if plays[i].team_id != plays[i - 1].team_id:
            total_sequences += 1
            gap = plays[i].game_seconds - plays[i - 1].game_seconds
            if 0 < gap <= 25:
                quick_responses += 1

    trading_pct = (quick_responses / total_sequences * 100) if total_sequences > 0 else 0
    trading_score = min(10, max(0, (trading_pct - 15) / 3.5))

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
    run_score = min(10, len(big_runs) * 1.5 + (max_run / 20.0) * 4)

    total_pts = plays[-1].home_score + plays[-1].away_score if plays else 0
    game_duration_min = plays[-1].game_seconds / 60 if plays else 48
    pts_per_min = total_pts / game_duration_min if game_duration_min > 0 else 0

    pace_score = 0.0
    if pts_per_min >= 4.0:
        pace_score = min(10, (pts_per_min - 3.0) * 4)
    if pts_per_min > 6.0:
        pace_score = max(5, pace_score - (pts_per_min - 6.0) * 2)

    ft_plays = sum(1 for p in plays if "free throw" in p.text.lower())
    ft_ratio = ft_plays / len(plays) if plays else 0
    ft_penalty = 1.0
    if ft_ratio > 0.20:
        ft_penalty = max(0.6, 1.0 - (ft_ratio - 0.20) * 3)

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

    clutch_plays = [p for p in plays
                    if (p.period == 4 and p.clock_seconds <= 300) or p.period > 4]

    if not clutch_plays:
        return 0.0, {}

    clutch_diffs = [abs(p.diff) for p in clutch_plays]
    avg_clutch_diff = sum(clutch_diffs) / len(clutch_diffs)
    max_clutch_diff = max(clutch_diffs)

    if avg_clutch_diff > 15:
        return 0.0, {
            "clutch_plays": len(clutch_plays), "avg_clutch_diff": round(avg_clutch_diff, 1),
            "max_clutch_diff": max_clutch_diff, "clutch_lead_changes": 0,
            "proximity_score": 0, "density_score": 0, "lead_change_score": 0,
            "note": "blowout in clutch time",
        }

    proximity_score = max(0, 10 - avg_clutch_diff * 1.0)
    if max_clutch_diff <= 5:
        proximity_score = min(10, proximity_score + 1.5)

    density_score = min(10, len(clutch_plays) * 0.6)

    clutch_lead_changes = 0
    prev_sign = 0
    for p in clutch_plays:
        sign = 1 if p.diff > 0 else (-1 if p.diff < 0 else 0)
        if sign != 0 and sign != prev_sign and prev_sign != 0:
            clutch_lead_changes += 1
        if sign != 0:
            prev_sign = sign

    lead_change_score = min(10, clutch_lead_changes * 2.5)

    final_margin = abs(plays[-1].diff)
    margin_bonus = 0.0
    if final_margin <= 3:
        margin_bonus = 3.0
    elif final_margin <= 5:
        margin_bonus = 1.5
    elif final_margin <= 8:
        margin_bonus = 0.5

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
        prev_diff = plays[-2].diff
        curr_diff = last_play.diff
        if (prev_diff <= 0 and curr_diff > 0) or (prev_diff >= 0 and curr_diff < 0):
            return True
    return False


# ── Blowout dampener ─────────────────────────────────────────────────────────

def _blowout_multiplier(plays: list[ScoringPlay]) -> float:
    if len(plays) < 10:
        return 1.0

    second_half = [p for p in plays if p.period >= 3]
    if not second_half:
        return 1.0

    diffs = [abs(p.diff) for p in second_half]
    sorted_d = sorted(diffs)
    median_2h_diff = sorted_d[len(sorted_d) // 2]

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

def calculate_watch_score(game_id: str | int) -> WatchScoreResult | None:
    """Calculate watch score for a 365scores game ID."""
    result = _fetch_pbp(int(game_id))
    if not result:
        return None
    messages, game = result
    if not messages:
        return None

    scoring_plays, team_info = _extract_scoring_plays(messages, game)
    if len(scoring_plays) < 10:
        return None

    tension, tension_details = _calc_tension(scoring_plays)
    flow, flow_details = _calc_flow(scoring_plays)
    clutch, clutch_details = _calc_clutch(scoring_plays)

    raw_score = clutch * 0.35 + tension * 0.35 + flow * 0.30

    max_period = max(p.period for p in scoring_plays)
    is_overtime = max_period > 4

    blowout_mult = 1.0 if is_overtime else _blowout_multiplier(scoring_plays)
    raw_score *= blowout_mult

    if raw_score > 0:
        raw_score = (raw_score ** 0.78) * (10 ** 0.22)

    buzzer_beater = _detect_buzzer_beater(scoring_plays)

    final_score = raw_score
    if is_overtime:
        final_score += 1.0
        final_score = max(8.5, final_score)

    if buzzer_beater:
        final_score = max(final_score, 9.5)

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
    home_abbr: str, away_abbr: str, date_yyyymmdd: str, league: str = "nba",
) -> WatchScoreResult | None:
    """Look up a game by team abbreviations + date, then compute watch score."""
    try:
        events = fetch_games(date_yyyymmdd, league=league)
        for event in events:
            comp = (event.get("competitions") or [{}])[0]
            home, away = "", ""
            for c in comp.get("competitors", []):
                abbr = c.get("team", {}).get("abbreviation", "")
                if c.get("homeAway") == "home":
                    home = abbr
                else:
                    away = abbr
            if home == home_abbr and away == away_abbr:
                return calculate_watch_score(event["id"])
    except Exception as exc:
        log.warning("WatchScore team lookup failed: %s", exc)
    return None
