"""Bzzoiro Sports Data (BSD) v2 — basketball client.

Mirrors the structure of `bzzoiro_adapter.py` (which is BSD's football
client). BSD's basketball v2 covers: NBA, Euroleague, Liga ACB,
Lega A Basket, Germany BBL, Eurocup. Israeli Winner League and FIBA BCL
are NOT in BSD basketball today — those keep going through 365scores via
`basketball_source.py`'s cascade.

Auth: `Authorization: Token <BZZOIRO_API_TOKEN>` (same env var as football).
Docs: https://sports.bzzoiro.com/basketball/docs/
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

_BASE = "https://sports.bzzoiro.com/basketball/api/v2"


def _token() -> str:
    tok = os.environ.get("BZZOIRO_API_TOKEN", "").strip()
    if not tok:
        raise BsdBasketballError(
            "BZZOIRO_API_TOKEN env var is not set. "
            "Get a token at https://sports.bzzoiro.com/ and set it on Railway."
        )
    return tok


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Token {_token()}",
        "Accept": "application/json",
    }


# BSD's basketball league IDs from `/leagues/`. Keyed by our slug so the
# rest of the code can stay slug-based.
BSD_BASKETBALL_LEAGUE_IDS: dict[str, int] = {
    "nba":            1,
    "euroleague":     2,
    "liga_acb":       3,
    "lega_a":         4,
    "bbl":            5,
    "eurocup":        6,
}


_TTL_SECONDS: dict[str, float] = {
    "leagues":          3600.0,
    "events_list":      60.0,
    "event_detail":     30.0,
    "event_team_stats": 120.0,
    "event_box_score":  60.0,
    "event_pregame":    600.0,
    "league_standings": 300.0,
    "player_search": 60.0,
    "player_detail": 3600.0,
    "player_games":  300.0,
}


class BsdBasketballError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cache (per-method TTL, identical pattern to soccer adapter)

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
            with httpx.Client(timeout=20, headers=_headers()) as c:
                r = c.get(url, params=params or {})
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
            logger.warning("bsd basketball 429 on %s, backing off %.2fs", path, sleep_s)
            time.sleep(sleep_s)
            continue

        if r.status_code in (401, 403):
            raise BsdBasketballError(f"auth failure {r.status_code} on {path}: {r.text[:200]}")

        if r.status_code == 404:
            return None

        last_error = BsdBasketballError(f"status {r.status_code} on {path}: {r.text[:200]}")
        logger.warning("bsd basketball %d on %s", r.status_code, path)
        time.sleep(0.5)

    raise BsdBasketballError(f"exhausted retries for {path}: {last_error}")


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


def leagues() -> list[dict]:
    """All tracked basketball leagues. Used to verify coverage at startup."""
    data = _cached_get("leagues", "/leagues/", {"limit": 50})
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def league_events(
    league_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Events for one league within a date range.

    `status` is BSD enum: 'notstarted' | 'inprogress' | 'finished' | 'postponed' | 'cancelled'.
    Dates are ISO 8601 (YYYY-MM-DD).
    """
    params: dict[str, Any] = {"league": league_id, "limit": limit}
    if date_from: params["date_from"] = date_from
    if date_to:   params["date_to"]   = date_to
    if status:    params["status"]    = status
    data = _cached_get("events_list", "/events/", params)
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def live_events(league_id: int | None = None) -> list[dict]:
    """Currently-in-progress events. BSD doesn't expose a dedicated /live
    endpoint for basketball — we filter the listing by status."""
    params: dict[str, Any] = {"status": "inprogress", "limit": 100}
    if league_id is not None:
        params["league"] = league_id
    data = _cached_get("events_list", "/events/", params)
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def event_detail(event_id: int) -> dict | None:
    """Single event with score, periods_score (quarter-by-quarter), prediction."""
    return _cached_get("event_detail", f"/events/{event_id}/")


def player_search(query: str, *, limit: int = 10) -> list[dict]:
    data = _cached_get("player_search", "/players/", {"search": query, "limit": limit})
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []


def player_detail(player_id: int | str) -> dict | None:
    return _cached_get("player_detail", f"/players/{player_id}/")


def player_games(player_id: int | str, *, limit: int = 10) -> list[dict]:
    data = _cached_get("player_games", f"/players/{player_id}/games/", {"limit": limit})
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []


def event_team_stats(event_id: int) -> dict | None:
    """Comprehensive team stats grouped by category (Scoring, Other, etc.).

    Shape: { event_id, home_team, away_team, statistics: [{groups: [{groupName, statisticsItems: [...]}]}] }
    Items include FG/3P/FT splits with made/attempted/percent, rebounds, assists,
    steals, blocks, turnovers, fouls, etc. — everything the football-style
    stats card needs.
    """
    return _cached_get("event_team_stats", f"/events/{event_id}/team-stats/")


def event_box_score(event_id: int) -> dict | None:
    """Per-player stats for both teams.

    Shape: { event_id, home_team, away_team, home_box: [...], away_box: [...] }
    Each player carries PTS, REB (OREB+DREB), AST, STL, BLK, TO, PF, plus
    shooting splits and advanced stats (TS%, eFG%, Usage%, plus/minus, rating).
    """
    return _cached_get("event_box_score", f"/events/{event_id}/box-score/")


def event_pregame(event_id: int) -> dict | None:
    """Pre-game bundle: coaches, venue, recent form, featured players."""
    return _cached_get("event_pregame", f"/events/{event_id}/pregame/")


# ---------------------------------------------------------------------------
# Convenience helpers used by basketball_source.py

def league_standings(league_id: int) -> dict | None:
    # BSD moved standings off the per-league path; it's now a top-level
    # endpoint that takes `league` as a query param.
    return _cached_get("league_standings", "/standings/", {"league": league_id}, league_id)


def slug_to_league_id(slug: str) -> int | None:
    return BSD_BASKETBALL_LEAGUE_IDS.get(slug)


def is_supported(slug: str) -> bool:
    return slug in BSD_BASKETBALL_LEAGUE_IDS
