"""Bzzoiro Sports Data (BSD) v2 client — primary football source.

BSD is an official, free, indie-friendly football API that exposes everything
we need for the Drama Engine in a single well-documented JSON shape:

- Live xG (cumulative + per-shot with coordinates)
- Per-minute Attack Momentum
- Per-minute cumulative xG timeline
- Full shotmap (location, body part, situation, xGOT)
- Big chances, dangerous attacks, possession, shots
- Confirmed/predicted lineups with formation and AI confidence
- Incident timeline (goals, cards, subs, VAR)
- Pre-match ML predictions (CatBoost)
- Standings with xG totals

BSD is the **primary** football source for v3. Sofascore stays as the
secondary for the two competitions BSD doesn't cover (Euros, Ligat Ha'al)
and as fallback when BSD has an outage.

Auth: `Authorization: Token <BZZOIRO_API_TOKEN>` from env var.
Docs: https://sports.bzzoiro.com/docs/v2/
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://sports.bzzoiro.com/api/v2"


def _token() -> str:
    """Read the API token from env. Raises on missing only when actually called,
    so module import is safe without the env var set."""
    tok = os.environ.get("BZZOIRO_API_TOKEN", "").strip()
    if not tok:
        raise BzzoiroError(
            "BZZOIRO_API_TOKEN env var is not set. "
            "Get a token at https://sports.bzzoiro.com/ and set it on Railway."
        )
    return tok


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Token {_token()}",
        "Accept": "application/json",
    }


# Per-method TTL. BSD already Redis-caches some endpoints server-side (live
# 30s, odds 3min, broadcasts 1hr, social 15min) — our cache layers on top so
# multiple consumers within the same poll cycle don't double-fetch.
_TTL_SECONDS: dict[str, float] = {
    "league_standings": 300.0,
    "live_events":     20.0,
    "league_events":   60.0,    # listings; minute-fresh is plenty
    "event_detail":    30.0,
    # Stats / incidents are the heaviest BSD calls and dominate Match
    # Summary load time. Bumped to 120s — for live games that's still
    # fresh enough for the UI; for finished games the data never changes.
    "event_stats":     120.0,
    "event_incidents": 120.0,
    "event_lineups":   300.0,   # lineups don't change mid-match
    "event_prediction": 600.0,  # pre-match; ten minutes is fine
    "league_detail":   3600.0,
    "player_search":      60.0,
    "player_detail":      3600.0,
    "player_career":      3600.0,
    "player_match_stats": 300.0,
    "event":              120.0,
    "team":               86400.0,
    "team_events":        600.0,   # a team's season fixtures move at most daily
}


class BzzoiroError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cache (mirrors sofascore_adapter._Cache; deliberately not shared so the two
# can have different TTLs without coordination)

class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple, ttl: float) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            t, v = entry
            if time.monotonic() - t > ttl:
                self._data.pop(key, None)
                return None
            return v

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)


_cache = _Cache()


# ---------------------------------------------------------------------------
# HTTP

def _request_json(path: str, params: dict[str, Any] | None = None, *, max_retries: int = 3) -> Any:
    url = f"{_BASE}{path}"
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), headers=_headers()) as c:
                r = c.get(url, params=params or {})
        except httpx.TimeoutException as e:
            # Don't retry a timeout — it just waits the full timeout again and
            # stacks into 30s+ match-detail hangs. Give up fast.
            last_error = e
            break
        except Exception as e:
            last_error = e
            time.sleep(0.5 + random.random())
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                last_error = e
                time.sleep(0.5)
                continue

        if r.status_code == 429:
            sleep_s = (2 ** attempt) + random.random()
            logger.warning("bzzoiro 429 on %s, backing off %.2fs", path, sleep_s)
            time.sleep(sleep_s)
            continue

        if r.status_code in (401, 403):
            # Auth failure — don't retry, raise immediately
            raise BzzoiroError(f"auth failure {r.status_code} on {path}: {r.text[:200]}")

        if r.status_code == 404:
            return None

        last_error = BzzoiroError(f"status {r.status_code} on {path}: {r.text[:200]}")
        logger.warning("bzzoiro %d on %s", r.status_code, path)
        time.sleep(0.5)

    raise BzzoiroError(f"exhausted retries for {path}: {last_error}")


def _cached_get(method: str, path: str, params: dict[str, Any] | None = None, *cache_args: Any) -> Any:
    key = (method, path, tuple(sorted((params or {}).items())), *cache_args)
    ttl = _TTL_SECONDS.get(method, 30.0)
    cached = _cache.get(key, ttl)
    if cached is not None:
        return cached
    data = _request_json(path, params)
    _cache.set(key, data)
    return data


# ---------------------------------------------------------------------------
# Public API


def live_events(league_id: int | None = None) -> list[dict]:
    """All football matches currently in the live window (kickoff−5 min through
    ~10 min post-whistle). Optionally scope to one league."""
    params = {}
    if league_id is not None:
        params["league_id"] = league_id
    data = _cached_get("live_events", "/events/live/", params)
    if isinstance(data, dict):
        return data.get("events") or data.get("results") or []
    return data or []


def league_events(
    league_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Matches for one league within a date range.

    `status` is BSD enum: 'notstarted' | 'inprogress' | 'finished' | 'postponed' | 'cancelled'.
    Dates are ISO 8601 (YYYY-MM-DD).
    """
    params: dict[str, Any] = {"league_id": league_id, "limit": limit}
    if date_from: params["date_from"] = date_from
    if date_to:   params["date_to"]   = date_to
    if status:    params["status"]    = status
    data = _cached_get("league_events", "/events/", params)
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def team_events(
    team_id: int | str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """A team's matches (home or away) across all competitions in a date range.
    Returns the same flat event shape as `league_events`, so the existing
    normalizer consumes it unchanged — one call yields a full season."""
    params: dict[str, Any] = {"team_id": team_id, "limit": limit}
    if date_from: params["date_from"] = date_from
    if date_to:   params["date_to"]   = date_to
    if status:    params["status"]    = status
    data = _cached_get("team_events", "/events/", params, "team_events", team_id, date_from, date_to)
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def event_detail(event_id: int) -> dict | None:
    """Full event payload (teams, score, status, league, season, venue, weather,
    pitch condition, attendance, derby flag, etc.)."""
    return _cached_get("event_detail", f"/events/{event_id}/", None, event_id)


def player_search(name: str, *, limit: int = 10) -> list[dict]:
    data = _cached_get("player_search", "/players/", {"name": name, "limit": limit})
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []


def player_detail(player_id: int | str) -> dict | None:
    return _cached_get("player_detail", f"/players/{player_id}/")


def player_career(player_id: int | str) -> dict | None:
    return _cached_get("player_career", f"/players/{player_id}/career/")


def player_match_stats(player_id: int | str, *, limit: int = 10) -> list[dict]:
    data = _cached_get("player_match_stats", f"/players/{player_id}/stats/", {"limit": limit})
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []


def event(event_id: int | str) -> dict | None:
    return _cached_get("event", f"/events/{event_id}/")


def team(team_id: int | str) -> dict | None:
    return _cached_get("team", f"/teams/{team_id}/")


def event_stats(event_id: int) -> dict | None:
    """Per-team statistics + sub-arrays.

    Top-level keys (verified live 2026-05-18):
      'event_id', 'stats', 'shotmap', 'momentum', 'average_positions', 'xg_per_minute'

    stats = { 'home': {...}, 'away': {...}, 'first_half': {...}, 'second_half': {...} }
      Each side has: expected_goals, xg, total_shots, shots_on_target,
      big_chances, big_chances_missed, big_chances_scored, ball_possession,
      dangerous_attack, dangerous_attack_pct, attack, ball_safe, touches_in_penalty_area,
      pass_accuracy_pct, total_saves, goals_prevented, (+ ~40 other counters)

    shotmap = [ {gm, xg, gml, min, pos, sit, body, home, type, xgot, added, player_id}, ... ]
    momentum = [ {m: minute, v: value}, ... ]   # signed pressure index per minute
    xg_per_minute = [ {m, xg_home, xg_away, cum_home, cum_away}, ... ]
    """
    return _cached_get("event_stats", f"/events/{event_id}/stats/", None, event_id)


def event_incidents(event_id: int) -> list[dict]:
    """Chronological play-by-play: goals, cards, subs, VAR, periods, injury time."""
    data = _cached_get("event_incidents", f"/events/{event_id}/incidents/", None, event_id)
    if isinstance(data, dict):
        return data.get("results", data.get("incidents", [])) or []
    return data or []


def event_lineups(event_id: int) -> dict | None:
    """Lineup state. Returns:
      { event_id, lineup_status: 'confirmed'|'predicted'|'unavailable',
        beta, lineups: {home, away}, unavailable_players, updated_at }
    Each lineup side: { team_id, team_name, formation, confidence, players, substitutes }
    """
    return _cached_get("event_lineups", f"/events/{event_id}/lineups/", None, event_id)


def event_prediction(event_id: int) -> dict | None:
    """Pre-match CatBoost ML prediction. Returns 404 if no prediction exists.

    Shape: { id, created_at, event, markets, recommendations, model }
    markets: match_result, expected_goals, over_under, btts, score
    """
    return _cached_get("event_prediction", f"/events/{event_id}/prediction/", None, event_id)


def league_detail(league_id: int) -> dict | None:
    return _cached_get("league_detail", f"/leagues/{league_id}/", None, league_id)


def league_standings(league_id: int) -> dict | None:
    return _cached_get("league_standings", f"/leagues/{league_id}/standings/", None, league_id)


# ---------------------------------------------------------------------------
# Drama Engine helpers (sport-aware)

def get_team_xg(stats: dict) -> tuple[float, float] | None:
    """Extract cumulative (home_xg, away_xg) from /events/{id}/stats/ payload.
    Returns None if the data isn't there (early in match)."""
    if not stats or "stats" not in stats:
        return None
    home = stats["stats"].get("home", {})
    away = stats["stats"].get("away", {})
    try:
        return float(home["expected_goals"]), float(away["expected_goals"])
    except (KeyError, TypeError, ValueError):
        return None


def momentum_pressure_window(
    stats: dict,
    *,
    window_minutes: int = 15,
    ref_minute: float | None = None,
) -> float:
    """Mean of the last `window_minutes` momentum points.

    BSD's momentum sign convention is positive = home dominance, negative = away.
    Returns 0.0 if data is missing.
    """
    if not stats:
        return 0.0
    points = stats.get("momentum", []) or []
    if not points:
        return 0.0
    if ref_minute is None:
        ref_minute = max(p.get("m", 0) for p in points)
    cutoff = ref_minute - window_minutes
    window = [p["v"] for p in points if cutoff <= p.get("m", 0) <= ref_minute]
    if not window:
        return 0.0
    return float(sum(window)) / float(len(window))


def cum_xg_at_minute(stats: dict, minute: float) -> tuple[float, float] | None:
    """Cumulative (home_xg, away_xg) at a given match minute.

    Useful for computing "xG-last-15-min" by subtracting cum_xg(75) from cum_xg(90).
    """
    timeline = (stats or {}).get("xg_per_minute", []) or []
    if not timeline:
        return None
    # Find the latest entry at or before `minute`
    best = None
    for entry in timeline:
        m = entry.get("m")
        if m is None or m > minute:
            continue
        if best is None or m > best.get("m", -1):
            best = entry
    if best is None:
        return None
    try:
        return float(best["cum_home"]), float(best["cum_away"])
    except (KeyError, TypeError, ValueError):
        return None


def xg_in_window(stats: dict, start_minute: float, end_minute: float) -> tuple[float, float] | None:
    """xG accumulated in [start_minute, end_minute] window, per team."""
    start = cum_xg_at_minute(stats, start_minute)
    end = cum_xg_at_minute(stats, end_minute)
    if start is None or end is None:
        return None
    return end[0] - start[0], end[1] - start[1]
