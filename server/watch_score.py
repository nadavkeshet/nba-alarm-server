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


def _fetch_game_detail(game_id: int) -> dict | None:
    """Fetch a 365scores game detail (the parent of /playByPlay/, stages, etc.)."""
    try:
        with Client365() as client:
            return get_game_detail(client, int(game_id))
    except Exception as exc:
        log.warning("WatchScore: 365scores game-detail fetch failed for %s: %s", game_id, exc)
        return None


def _fetch_pbp_messages(game: dict) -> list[dict] | None:
    """
    Try to fetch SportRadar play-by-play messages for a game detail.
    Returns None if the feed isn't available (most non-NBA leagues, e.g.
    Euroleague, do not have a SportRadar PBP feed).
    """
    pbp_meta = game.get("playByPlay") or {}
    feed_url = pbp_meta.get("feedURL")
    if not feed_url:
        return None
    try:
        resp = httpx.get(feed_url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("Messages", [])
    except Exception as exc:
        log.warning("WatchScore: PBP feed fetch failed for %s: %s", game.get("id"), exc)
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


# ── Stages fallback (used when PBP unavailable, e.g. Euroleague) ─────────────

def _stage_period(name: str, idx: int) -> int | None:
    """Map a 365scores stage name to a period number, or None if not in regulation/OT."""
    n = (name or "").upper().strip()
    if n in ("Q1", "1ST"):  return 1
    if n in ("Q2", "2ND"):  return 2
    if n in ("Q3", "3RD"):  return 3
    if n in ("Q4", "4TH"):  return 4
    if n.startswith("OT"):
        try:
            return 4 + int(n[2:] or "1")
        except ValueError:
            return 5
    return None


def _synthesize_plays_from_stages(game: dict, period_length_sec: int) -> tuple[list[ScoringPlay], dict]:
    """
    Build coarse "scoring plays" from per-quarter stage data.
    Each quarter contributes a single end-of-period synthetic play whose
    cumulative score is the running total. This gives the tension/clutch
    calculators enough signal to rate the game even without per-play data.
    """
    home = _make_team_obj(game.get("homeCompetitor", {}))
    away = _make_team_obj(game.get("awayCompetitor", {}))
    team_info = {
        "home_id":   home["id"],
        "away_id":   away["id"],
        "home_abbr": home["abbreviation"],
        "away_abbr": away["abbreviation"],
    }

    home_running, away_running = 0, 0
    plays: list[ScoringPlay] = []

    for idx, stage in enumerate(game.get("stages", [])):
        period = _stage_period(stage.get("name", ""), idx)
        if period is None:
            continue
        h_q = stage.get("homeCompetitorScore")
        a_q = stage.get("awayCompetitorScore")
        if h_q is None or a_q is None:
            continue

        # Stage scores are per-quarter deltas, not running totals.
        home_running += int(h_q)
        away_running += int(a_q)

        # Synthetic play at end of period (clock = 0)
        plen = period_length_sec if period <= 4 else 300
        plays.append(ScoringPlay(
            period=period,
            clock_seconds=0.0,
            game_seconds=_game_elapsed(period, 0.0, period_length_sec),
            home_score=home_running,
            away_score=away_running,
            diff=home_running - away_running,
            team_id=home["id"] if int(h_q) >= int(a_q) else away["id"],
            score_value=int(h_q) + int(a_q),
            text=f"End of {stage.get('name','?')}",
        ))

    return plays, team_info


def _score_from_stages(plays: list[ScoringPlay]) -> tuple[float, float, float, float, dict]:
    """
    Coarse watch score from end-of-quarter synthetic plays.
    No lead-change density, no clutch lead changes — just tension from per-
    quarter margins, a tightness bonus for the final, and an OT boost.
    """
    if not plays:
        return 0.0, 0.0, 0.0, 0.0, {}

    diffs = [abs(p.diff) for p in plays]
    final_margin = abs(plays[-1].diff)

    # Tension: how close was the game on average across quarters
    avg_diff = sum(diffs) / len(diffs)
    tension = max(0.0, 10.0 - avg_diff * 0.55)

    # Closeness of late quarters carries more weight
    late = [p for p in plays if p.period >= 3]
    late_avg = sum(abs(p.diff) for p in late) / len(late) if late else avg_diff
    clutch = max(0.0, 10.0 - late_avg * 0.7)
    if final_margin <= 3:
        clutch = min(10.0, clutch + 2.0)
    elif final_margin <= 6:
        clutch = min(10.0, clutch + 1.0)

    # Flow: balanced quarter scoring (no quarter dominated by one team)
    quarter_pts = [p.score_value for p in plays if p.period <= 4]
    if quarter_pts:
        avg_q_pts = sum(quarter_pts) / len(quarter_pts)
        max_q_pts = max(quarter_pts)
        flow = max(0.0, 10.0 - (max_q_pts - avg_q_pts) * 0.3)
    else:
        flow = 5.0

    # Final weighted score: 40% tension, 40% clutch, 20% flow
    raw = tension * 0.40 + clutch * 0.40 + flow * 0.20

    details = {
        "avg_diff": round(avg_diff, 1),
        "late_avg_diff": round(late_avg, 1),
        "final_margin": final_margin,
        "quarters": len(plays),
    }
    return raw, tension, flow, clutch, details


# ── Main scoring engine ──────────────────────────────────────────────────────

_FIBA_LEAGUES = {"euroleague", "eurocup", "bcl", "winner_league"}


def _period_length_for_league(league: str) -> int:
    """Return regulation period length in seconds. NBA = 12min, FIBA = 10min."""
    return 600 if league in _FIBA_LEAGUES else 720


def calculate_watch_score(game_id: str | int, league: str = "nba") -> WatchScoreResult | None:
    """
    Calculate watch score for a 365scores game ID.
    Uses per-play SportRadar PBP if available (NBA), otherwise falls back to
    coarse per-quarter stage data (Euroleague et al.).

    For NBA games involving the Portland Trail Blazers, the score gets a
    "Deni boost" — Israeli audience cares disproportionately when Avdija has
    a strong night, so the rating is bumped accordingly.
    """
    game = _fetch_game_detail(int(game_id))
    if not game:
        return None

    period_length = _period_length_for_league(league)
    messages = _fetch_pbp_messages(game)

    if messages:
        result = _score_from_pbp(messages, game, period_length)
    else:
        result = _score_from_stages_only(game, period_length)

    if result is None:
        return None

    if league == "nba":
        boost, boost_details = _compute_avdija_boost(int(game_id), result)
        if boost > 0:
            result.score = max(1.0, min(10.0, round(result.score + boost, 1)))
        if boost_details:
            result.details["avdija_boost"] = boost_details

    return result


# ── Deni Avdija boost ────────────────────────────────────────────────────────
# Portland Trail Blazers id in 365scores. Avdija is matched by name in the
# boxscore so any future trade just needs the team id to be updated here.
_PORTLAND_TEAM_ID = "1324"
_DENI_NAME_PATTERNS = ("deni avdija", "avdija")
_AVDIJA_BOOST_MAX = 2.5
# 365scores boxscore stats column order — must match server/main.py.
_AVDIJA_STAT_NAMES = ["MIN", "FG", "3PT", "FT", "REB", "AST",
                      "STL", "BLK", "TO", "PF", "PTS", "3PM"]


def _compute_avdija_boost(game_id: int, result: WatchScoreResult) -> tuple[float, dict]:
    """
    Returns (boost_in_points, debug_details). Boost is added to final_score.
    Capped at _AVDIJA_BOOST_MAX so a monster night can't dominate the score
    completely. Returns (0, {}) when not a Portland game or Deni didn't play.
    """
    team_info = result.details.get("teams", {})
    home_id = str(team_info.get("home_id") or "")
    away_id = str(team_info.get("away_id") or "")

    if _PORTLAND_TEAM_ID not in (home_id, away_id):
        return 0.0, {}

    try:
        from score365_adapter import fetch_player_boxscore
        bs = fetch_player_boxscore(game_id) or {}
    except Exception as exc:
        log.debug("Avdija boost: boxscore fetch failed for %s: %s", game_id, exc)
        return 0.0, {}

    deni_stats: dict | None = None
    deni_team_id: str = ""
    for section in bs.get("boxscore", {}).get("players", []) or []:
        team_id = str(section.get("team", {}).get("id", ""))
        athletes = section.get("statistics", [{}])[0].get("athletes", []) or []
        for ath in athletes:
            name = (ath.get("athlete", {}).get("displayName") or "").lower()
            if any(p in name for p in _DENI_NAME_PATTERNS):
                vals = ath.get("stats", []) or []
                if len(vals) == len(_AVDIJA_STAT_NAMES):
                    deni_stats = dict(zip(_AVDIJA_STAT_NAMES, vals))
                deni_team_id = team_id
                break
        if deni_stats is not None:
            break

    if deni_stats is None:
        return 0.0, {"reason": "not_in_boxscore"}

    def _num(v) -> float:
        s = str(v).strip()
        if not s or s == "-":
            return 0.0
        # Shooting splits like "5-9" → use the made portion.
        if "-" in s and ":" not in s:
            s = s.split("-", 1)[0]
        try:
            return float(s)
        except ValueError:
            return 0.0

    pts = _num(deni_stats.get("PTS"))
    reb = _num(deni_stats.get("REB"))
    ast = _num(deni_stats.get("AST"))
    stl = _num(deni_stats.get("STL"))
    blk = _num(deni_stats.get("BLK"))

    min_str = str(deni_stats.get("MIN", "")).strip()
    if min_str in ("", "-", "0", "0:00"):
        return 0.0, {"reason": "did_not_play"}

    boost = 0.0

    # Scoring — the headline number. 10 PTS = 0, scales linearly.
    # 20 PTS → +0.7, 30 PTS → +1.4, capped at +1.5.
    if pts >= 10:
        boost += min(1.5, (pts - 10) / 10 * 0.7)

    # Playmaking/glass — REB + AST combined so a balanced line still rewards.
    ra = reb + ast
    if ra >= 7:
        boost += min(0.6, (ra - 7) / 10 * 0.5)

    # Defensive impact.
    boost += min(0.4, (stl + blk) * 0.1)

    # Double-double / triple-double bonus (most exciting outcomes).
    cats = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if cats >= 3:
        boost += 1.5   # triple-double
    elif cats == 2:
        boost += 0.5   # double-double

    # Did Portland win? Biggest mood-shifter for an Israeli viewer.
    home_final = int(result.details.get("final_score_home") or 0)
    away_final = int(result.details.get("final_score_away") or 0)
    portland_won = False
    if deni_team_id == _PORTLAND_TEAM_ID:
        if home_id == _PORTLAND_TEAM_ID:
            portland_won = home_final > away_final
        elif away_id == _PORTLAND_TEAM_ID:
            portland_won = away_final > home_final
    if portland_won:
        boost += 0.8

    boost = min(_AVDIJA_BOOST_MAX, round(boost, 2))

    return boost, {
        "applied": boost > 0,
        "pts": pts, "reb": reb, "ast": ast, "stl": stl, "blk": blk,
        "min": min_str,
        "fg":   str(deni_stats.get("FG", "")),
        "3pt":  str(deni_stats.get("3PT", "")),
        "team_won": portland_won,
        "double_categories": cats,
        "boost": boost,
    }


def _score_from_pbp(messages: list[dict], game: dict, period_length: int) -> WatchScoreResult | None:
    scoring_plays, team_info = _extract_scoring_plays(messages, game)
    if len(scoring_plays) < 10:
        return None

    # Recompute game_seconds with the league-specific period length
    for p in scoring_plays:
        p.game_seconds = _game_elapsed(p.period, p.clock_seconds, period_length)

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
            "source": "pbp",
        },
        buzzer_beater=buzzer_beater,
        overtime=is_overtime,
    )


def _score_from_stages_only(game: dict, period_length: int) -> WatchScoreResult | None:
    """
    Stage-based fallback for leagues without per-play feeds (Euroleague).
    Coarser than the PBP path but still produces a meaningful 1-10 rating
    from per-quarter score data.
    """
    plays, team_info = _synthesize_plays_from_stages(game, period_length)
    if not plays:
        return None

    raw, tension, flow, clutch, details = _score_from_stages(plays)

    max_period = max(p.period for p in plays)
    is_overtime = max_period > 4

    if raw > 0:
        raw = (raw ** 0.78) * (10 ** 0.22)

    final_score = raw
    if is_overtime:
        final_score += 1.0
        final_score = max(8.5, final_score)

    final_score = max(1.0, min(10.0, round(final_score, 1)))

    return WatchScoreResult(
        score=final_score,
        tension_score=round(tension, 1),
        flow_score=round(flow, 1),
        clutch_score=round(clutch, 1),
        details={
            **details,
            "teams": team_info,
            "final_score_home": plays[-1].home_score,
            "final_score_away": plays[-1].away_score,
            "source": "stages",
        },
        buzzer_beater=False,  # not detectable without per-play data
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
                return calculate_watch_score(event["id"], league=league)
    except Exception as exc:
        log.warning("WatchScore team lookup failed: %s", exc)
    return None


# ── BSD-native scoring (live + final, all basketball leagues) ─────────────────

def _bb_period_num(key: str) -> int | None:
    k = (key or "").lower()
    if k.startswith("period"):
        try:
            return int(k[6:])
        except ValueError:
            return None
    if k.startswith("overtime"):
        try:
            return 4 + int(k[8:] or "1")
        except ValueError:
            return 5
    return None


def _bb_synthesize_plays_from_periods(detail: dict, period_length_sec: int):
    """Build coarse end-of-quarter ScoringPlays from BSD `periods_score`
    (per-quarter deltas; -1 sentinels = unplayed). Works live — completed
    quarters are final and the in-progress quarter's partial is already summed
    into the live total."""
    ps = detail.get("periods_score") or {}
    items: list[tuple[int, int, int]] = []
    for key, val in ps.items():
        p = _bb_period_num(key)
        if p is None or not isinstance(val, (list, tuple)) or len(val) < 2:
            continue
        h, a = val[0], val[1]
        if h is None or a is None or h < 0 or a < 0:   # -1 = quarter not played yet
            continue
        items.append((p, int(h), int(a)))
    items.sort(key=lambda x: x[0])

    home_id = str((detail.get("home_team") or {}).get("id") or "home")
    away_id = str((detail.get("away_team") or {}).get("id") or "away")
    home_running = away_running = 0
    plays: list[ScoringPlay] = []
    for (p, hq, aq) in items:
        home_running += hq
        away_running += aq
        plays.append(ScoringPlay(
            period=p, clock_seconds=0.0,
            game_seconds=_game_elapsed(p, 0.0, period_length_sec),
            home_score=home_running, away_score=away_running,
            diff=home_running - away_running,
            team_id=home_id if hq >= aq else away_id,
            score_value=hq + aq, text=f"End of period {p}",
        ))

    # Safety net: if periods_score lags the live total, append the live state.
    lh, la = detail.get("home_score"), detail.get("away_score")
    if lh is not None and la is not None and (int(lh), int(la)) != (home_running, away_running):
        cur_p = (items[-1][0] if items else 0) + 1
        plays.append(ScoringPlay(
            period=cur_p, clock_seconds=0.0,
            game_seconds=_game_elapsed(cur_p, 0.0, period_length_sec),
            home_score=int(lh), away_score=int(la), diff=int(lh) - int(la),
            team_id=home_id, score_value=0, text="live total",
        ))

    team_info = {
        "home_id": home_id, "away_id": away_id,
        "home_abbr": (detail.get("home_team") or {}).get("short_name") or "",
        "away_abbr": (detail.get("away_team") or {}).get("short_name") or "",
    }
    return plays, team_info


def _bb_find_stat_item(team_stats: dict, key_substr: str) -> dict | None:
    try:
        scopes = team_stats.get("statistics") or []
        for g in (scopes[0].get("groups") or []):
            for it in (g.get("statisticsItems") or []):
                if key_substr in (it.get("key") or ""):
                    return it
    except Exception:
        pass
    return None


def _bb_efficiency_factor(team_stats: dict | None) -> float:
    """0.9–1.1 from field-goal efficiency. A close game where both teams shoot
    well is a better watch than both bricking. Neutral (1.0) on missing data."""
    try:
        it = _bb_find_stat_item(team_stats or {}, "fieldGoals")
        if not it:
            return 1.0
        pcts = []
        if it.get("homeTotal"):
            pcts.append(it["homeValue"] / it["homeTotal"])
        if it.get("awayTotal"):
            pcts.append(it["awayValue"] / it["awayTotal"])
        if not pcts:
            return 1.0
        avg = sum(pcts) / len(pcts)                    # ~0.45 typical
        return max(0.9, min(1.1, 1.0 + (avg - 0.45) * 1.6))
    except Exception:
        return 1.0


def _bb_standout_boost(box_score: dict | None) -> float:
    """0–0.6 when a player goes off (40+ pts / triple-double / near it) on either
    team — a star night makes a game must-watch. Neutral (0) on missing data."""
    if not box_score:
        return 0.0
    best = 0.0
    try:
        for side in ("home_box", "away_box"):
            for p in (box_score.get(side) or []):
                pts = p.get("points") or 0
                cats = sum(1 for v in (p.get("points"), p.get("rebounds"),
                                       p.get("assists"), p.get("steals"),
                                       p.get("blocks")) if (v or 0) >= 10)
                s = 0.0
                if pts >= 40:
                    s = 0.5
                elif pts >= 30:
                    s = 0.3
                if cats >= 3:
                    s = max(s, 0.6)        # triple-double
                elif cats == 2:
                    s = max(s, 0.2)
                best = max(best, s)
    except Exception:
        return 0.0
    return min(0.6, best)


def _compute_avdija_boost_bsd(detail: dict, box_score: dict | None) -> tuple[float, dict]:
    """Portland/Deni-Avdija boost from the BSD box score — same point formula and
    +2.5 cap as the 365 path, matched by name (BSD ids differ from 365)."""
    if not box_score:
        return 0.0, {}
    home_name = ((detail.get("home_team") or {}).get("name") or "").lower()
    away_name = ((detail.get("away_team") or {}).get("name") or "").lower()
    def _is_pdx(n: str) -> bool:
        return "portland" in n or "trail blazers" in n
    if not (_is_pdx(home_name) or _is_pdx(away_name)):
        return 0.0, {}

    deni = None
    for side in ("home_box", "away_box"):
        for p in (box_score.get(side) or []):
            if any(pat in (p.get("name") or "").lower() for pat in _DENI_NAME_PATTERNS):
                deni = p
                break
        if deni:
            break
    if deni is None:
        return 0.0, {"reason": "not_in_boxscore"}
    if (deni.get("minutes") or 0) <= 0:
        return 0.0, {"reason": "did_not_play"}

    pts = deni.get("points") or 0
    reb = deni.get("rebounds") or 0
    ast = deni.get("assists") or 0
    stl = deni.get("steals") or 0
    blk = deni.get("blocks") or 0

    boost = 0.0
    if pts >= 10:
        boost += min(1.5, (pts - 10) / 10 * 0.7)
    ra = reb + ast
    if ra >= 7:
        boost += min(0.6, (ra - 7) / 10 * 0.5)
    boost += min(0.4, (stl + blk) * 0.1)
    cats = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if cats >= 3:
        boost += 1.5
    elif cats == 2:
        boost += 0.5

    hs = detail.get("home_score") or 0
    as_ = detail.get("away_score") or 0
    portland_won = (_is_pdx(home_name) and hs > as_) or (_is_pdx(away_name) and as_ > hs)
    if portland_won:
        boost += 0.8

    boost = min(_AVDIJA_BOOST_MAX, round(boost, 2))
    return boost, {"applied": boost > 0, "pts": pts, "team_won": portland_won, "double_categories": cats}


def _bb_safe(fn, event_id):
    try:
        return fn(int(event_id)) or {}
    except Exception:
        return {}


def calculate_watch_score_bsd(bsd_event_id: int, league: str = "nba") -> WatchScoreResult | None:
    """Watch score driven directly from a BSD basketball event — works LIVE
    (in-progress) and final, across every league (periods_score is universal).
    Enriches with BSD team-stats (shooting efficiency) + box-score (standout
    performances) and keeps the Portland/Avdija boost on finals."""
    try:
        import bsd_basketball as _bb
    except Exception:
        return None
    detail = _bb_safe(_bb.event_detail, bsd_event_id)
    if not detail:
        return None
    status = (detail.get("status") or "").lower()
    is_live = status == "inprogress"
    is_final = status == "finished"
    if not (is_live or is_final):
        return None

    period_length = _period_length_for_league(league)
    plays, team_info = _bb_synthesize_plays_from_periods(detail, period_length)
    if not plays:
        return None

    raw, tension, flow, clutch, details = _score_from_stages(plays)
    max_period = max(p.period for p in plays)
    is_overtime = max_period > 4
    if raw > 0:
        raw = (raw ** 0.78) * (10 ** 0.22)
    final_score = raw

    # Enrichment (both live & final), neutral on any missing data.
    eff = _bb_efficiency_factor(_bb_safe(_bb.event_team_stats, bsd_event_id))
    final_score *= eff
    diff_now = abs(plays[-1].diff)
    standout = 0.0
    if diff_now <= 25:                       # skip box fetch for decided games
        standout = _bb_standout_boost(_bb_safe(_bb.event_box_score, bsd_event_id))
        final_score += standout

    avdija = 0.0
    if is_live:
        # Tight + late climbs; an early blowout stays low. No final-only bonuses.
        closeness_now = max(0.0, 1.0 - diff_now / 12.0)
        frac_elapsed = min(1.0, max_period / 4.0)
        final_score *= (1.0 + 0.5 * closeness_now * frac_elapsed)
    else:
        if is_overtime:
            final_score += 1.0
            final_score = max(8.5, final_score)
        avdija, _ = _compute_avdija_boost_bsd(detail, _bb_safe(_bb.event_box_score, bsd_event_id))
        final_score += avdija

    final_score = max(1.0, min(10.0, round(final_score, 1)))
    return WatchScoreResult(
        score=final_score,
        tension_score=round(tension, 1),
        flow_score=round(flow, 1),
        clutch_score=round(clutch, 1),
        details={
            **details, "teams": team_info, "source": "bsd", "live": is_live,
            "efficiency": round(eff, 3), "standout": round(standout, 2),
            "avdija": round(avdija, 2),
            "final_score_home": plays[-1].home_score,
            "final_score_away": plays[-1].away_score,
        },
        buzzer_beater=False,
        overtime=is_overtime,
    )
