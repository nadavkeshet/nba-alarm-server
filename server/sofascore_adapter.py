"""Sofascore live football data adapter.

We talk to Sofascore's undocumented JSON API directly via `curl_cffi`, which
impersonates a real Chrome TLS fingerprint at the network level. This bypasses
the Cloudflare bot challenge without needing a headless browser, so we stay on
a normal Python container on Railway.

What we use Sofascore for:
- Live xG (cumulative + per shot, with x/y coordinates)
- Attack Momentum graph (per-minute signed −100..+100 value)
- Full statistics, incidents, lineups

What we DON'T use Sofascore for:
- Match schedule (365scores is authoritative — see score365_soccer_adapter.py)
- Match metadata that 365scores already has

The split exists so an outage at Sofascore degrades the Drama Engine (loses
xG/momentum) but doesn't take down basic match listings or event alarms.

Resilience: if Sofascore returns 403 we rotate the TLS impersonate fingerprint;
if it returns 429 we back off exponentially. The runbook in
`clutch-alarm-v3-soccer-redesign.md` §9 documents downstream fallbacks (local
xG via `xg_fallback.py`, momentum=0 placeholder).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any

import os

from curl_cffi import requests as cf_requests

logger = logging.getLogger(__name__)

_BASE = "https://api.sofascore.com/api/v1"
_HEADERS = {
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# TLS fingerprints to rotate through on consecutive 403s. Order matters —
# `chrome120` is the most recent and usually works; the others are fallbacks.
_IMPERSONATE_ROTATION = ("chrome120", "safari17_0", "chrome", "edge101")

# Optional outbound proxy for Sofascore requests. Set SOFASCORE_PROXY_URL on
# Railway (e.g. "socks5://user:pass@host:port" or "http://host:port") when
# Railway's egress IPs are blocked by Sofascore's Cloudflare protection.
_PROXY_URL: str | None = os.environ.get("SOFASCORE_PROXY_URL")

# Per-method TTL for the in-process cache. Match state changes more often than
# meta (statistics roll up over the whole match; lineups don't change at all
# after kickoff), so we tune each one.
_TTL_SECONDS: dict[str, float] = {
    "live_events": 20.0,
    "scheduled_events": 600.0,        # match list per date is stable
    "event_detail": 25.0,
    "event_statistics": 25.0,
    "event_graph": 25.0,
    "event_shotmap": 25.0,
    "event_incidents": 20.0,
    "unique_tournament": 86400.0,     # competition metadata effectively static
    "event_lineups": 120.0,           # set ~1h pre-kickoff, frozen after
    "seasons": 86400.0,               # season list effectively static
    "standings": 600.0,               # table moves at most once per matchday
}


class SofascoreError(Exception):
    """Raised when Sofascore can't be reached after exhausting retries."""


# ---------------------------------------------------------------------------
# Cache

class _Cache:
    """Thread-safe TTL cache. Keyed by (method, *args)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple, ttl: float) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if time.monotonic() - stored_at > ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _Cache()


# ---------------------------------------------------------------------------
# HTTP

# Track which impersonate fingerprint last worked, so we don't always start
# from the top after a successful run.
_current_impersonate = _IMPERSONATE_ROTATION[0]
_impersonate_lock = threading.Lock()


def _request_json(path: str, *, max_retries: int = 3) -> dict | list:
    """Issue a GET request to Sofascore and return parsed JSON.

    On 403: rotate impersonate fingerprint and retry.
    On 429: exponential backoff + jitter.
    On other transient errors: short backoff + retry.
    Raises SofascoreError if all retries fail.
    """
    global _current_impersonate

    url = f"{_BASE}{path}"
    last_error: Exception | None = None

    for attempt in range(max_retries):
        with _impersonate_lock:
            impersonate = _current_impersonate

        try:
            proxies = {"https": _PROXY_URL, "http": _PROXY_URL} if _PROXY_URL else None
            r = cf_requests.get(url, impersonate=impersonate, timeout=20, headers=_HEADERS, proxies=proxies)
        except Exception as e:
            last_error = e
            logger.warning("sofascore network error on %s: %s", path, e)
            time.sleep(0.5 + random.random())
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                last_error = e
                logger.warning("sofascore decode error on %s: %s", path, e)
                time.sleep(0.5)
                continue

        if r.status_code == 403:
            # Rotate to the next impersonate fingerprint
            with _impersonate_lock:
                idx = _IMPERSONATE_ROTATION.index(_current_impersonate)
                _current_impersonate = _IMPERSONATE_ROTATION[(idx + 1) % len(_IMPERSONATE_ROTATION)]
                logger.warning(
                    "sofascore 403 on %s, rotating impersonate %s → %s",
                    path, impersonate, _current_impersonate,
                )
            continue

        if r.status_code == 429:
            sleep_s = (2 ** attempt) + random.random()
            logger.warning("sofascore 429 on %s, backing off %.2fs", path, sleep_s)
            time.sleep(sleep_s)
            continue

        if r.status_code == 404:
            # Not a transient error — caller asked for something that doesn't exist
            logger.info("sofascore 404 on %s", path)
            return {}

        # Any other 4xx/5xx: short retry
        last_error = SofascoreError(f"status {r.status_code} on {path}")
        logger.warning("sofascore %d on %s", r.status_code, path)
        time.sleep(0.5)

    raise SofascoreError(f"exhausted retries for {path}: {last_error}")


def _cached_get(method: str, path: str, *cache_args: Any) -> Any:
    """Cache-aware wrapper around _request_json."""
    key = (method, *cache_args)
    ttl = _TTL_SECONDS.get(method, 30.0)
    cached = _cache.get(key, ttl)
    if cached is not None:
        return cached
    data = _request_json(path)
    _cache.set(key, data)
    return data


# ---------------------------------------------------------------------------
# Public API

def live_events() -> list[dict]:
    """All football matches currently in-progress (worldwide).

    Callers typically filter the result by `tournament.uniqueTournament.id`
    against `score365_soccer_adapter.COMPETITION_IDS` to keep only in-scope
    matches.
    """
    data = _cached_get("live_events", "/sport/football/events/live")
    return data.get("events", []) if isinstance(data, dict) else []


def scheduled_events(date_iso: str) -> list[dict]:
    """Football matches scheduled on `date_iso` (YYYY-MM-DD)."""
    data = _cached_get("scheduled_events", f"/sport/football/scheduled-events/{date_iso}", date_iso)
    return data.get("events", []) if isinstance(data, dict) else []


def event_detail(event_id: int) -> dict:
    """Full event payload (teams, score, status, tournament, season)."""
    data = _cached_get("event_detail", f"/event/{event_id}", event_id)
    return data.get("event", {}) if isinstance(data, dict) else {}


def event_statistics(event_id: int) -> dict:
    """Match-level statistics including cumulative xG, shots, possession,
    big chances. Shape: { 'statistics': [ {period, groups:[{statisticsItems:[{name,home,away}]}] } ] }.
    """
    return _cached_get("event_statistics", f"/event/{event_id}/statistics", event_id)


def event_graph(event_id: int) -> dict:
    """Attack Momentum graph. Returns { 'graphPoints': [ {minute, value}, ... ] }.
    `value` is signed −100..+100 (positive toward home, negative toward away —
    confirm via empirical inspection; some matches use the opposite convention).
    """
    return _cached_get("event_graph", f"/event/{event_id}/graph", event_id)


def event_shotmap(event_id: int) -> list[dict]:
    """Per-shot data with `xg`, player coordinates, body part, situation, time."""
    data = _cached_get("event_shotmap", f"/event/{event_id}/shotmap", event_id)
    if isinstance(data, dict):
        return data.get("shotmap", []) or []
    return data if isinstance(data, list) else []


def event_incidents(event_id: int) -> list[dict]:
    """Timeline of typed events: goals, cards, subs, VAR, periods."""
    data = _cached_get("event_incidents", f"/event/{event_id}/incidents", event_id)
    if isinstance(data, dict):
        return data.get("incidents", []) or []
    return data if isinstance(data, list) else []


def unique_tournament(tournament_id: int) -> dict:
    """Tournament metadata (name, category/country, current season)."""
    data = _cached_get("unique_tournament", f"/unique-tournament/{tournament_id}", tournament_id)
    return data.get("uniqueTournament", {}) if isinstance(data, dict) else {}


def event_lineups(event_id: int) -> dict:
    """Confirmed/predicted lineups: { confirmed, home:{players, formation, ...}, away:{...} }.
    Each player entry: { player:{id,name,shortName,...}, shirtNumber, position,
    substitute, statistics:{rating} }."""
    return _cached_get("event_lineups", f"/event/{event_id}/lineups", event_id)


def seasons(unique_tournament_id: int) -> list[dict]:
    """Season list for a tournament, newest first. seasons[0] is the current one."""
    data = _cached_get("seasons", f"/unique-tournament/{unique_tournament_id}/seasons", unique_tournament_id)
    return data.get("seasons", []) if isinstance(data, dict) else []


def standings(unique_tournament_id: int, season_id: int) -> dict:
    """Total standings for a season. Shape: { standings: [ {rows: [...]} ] }.
    Multiple groups when the league splits (e.g. Israeli championship/relegation rounds)."""
    return _cached_get(
        "standings",
        f"/unique-tournament/{unique_tournament_id}/season/{season_id}/standings/total",
        unique_tournament_id, season_id,
    )


# ---------------------------------------------------------------------------
# Helpers

def find_team_xg(stats: dict) -> tuple[float, float] | None:
    """Pull (home_xg, away_xg) out of an /event/{id}/statistics response.

    Returns None if the data isn't there (early in match, or not provided for
    this competition tier).
    """
    if not stats or not isinstance(stats, dict):
        return None
    for period_group in stats.get("statistics", []):
        if period_group.get("period") != "ALL":
            continue
        for group in period_group.get("groups", []):
            for item in group.get("statisticsItems", []):
                if (item.get("name") or "").lower() in ("expected goals", "xg"):
                    try:
                        return float(item["home"]), float(item["away"])
                    except (KeyError, TypeError, ValueError):
                        return None
    return None


def graph_pressure_window(graph: dict, minutes: int = 15, ref_minute: float | None = None) -> float:
    """Mean of the last `minutes` graphPoints values.

    Positive = pressure toward the home side, negative = toward away.
    Returns 0.0 if data is missing.
    """
    points = (graph or {}).get("graphPoints", [])
    if not points:
        return 0.0
    if ref_minute is None:
        ref_minute = max(p.get("minute", 0) for p in points)
    cutoff = ref_minute - minutes
    window = [p["value"] for p in points if cutoff <= p.get("minute", 0) <= ref_minute]
    if not window:
        return 0.0
    return float(sum(window)) / float(len(window))
