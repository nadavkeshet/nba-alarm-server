"""Basketball source router — mirrors soccer_source.py's cascade pattern.

Public surface:
  - get_basketball_games(league_slug, date_iso=None, tz=None) -> list[dict]
  - get_basketball_event_detail(canonical_id) -> dict | None  (full summary payload)
  - get_basketball_team_stats(canonical_id) -> dict | None
  - get_basketball_box_score(canonical_id) -> dict | None

Source cascade per league:
  - BSD-supported leagues (NBA, EuroLeague, Liga ACB, Lega A, BBL, Eurocup):
      BSD primary → 365scores fallback
  - 365-only leagues (Israeli Winner League, FIBA BCL):
      365scores only

All methods return dicts in a single canonical "Game" shape compatible
with what the existing /games endpoint already produces (ESPN-like
event objects). That keeps `monitor.py`, the iOS app, and the alarm
engine unchanged while we shift sources underneath.

Canonical id format: 'bsd-7074' (BSD) or 's365-2123' (365scores).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import bsd_basketball as bsd
import score365_adapter as s365

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical IDs


def canonical_id(source: str, native_id: int | str) -> str:
    return f"{source}-{native_id}"


def parse_canonical_id(cid: str) -> tuple[str, str]:
    if "-" not in cid:
        raise ValueError(f"bad canonical id: {cid!r}")
    source, native = cid.split("-", 1)
    return source, native


# ---------------------------------------------------------------------------
# Public read API


def get_basketball_games(
    league_slug: str,
    date_iso: str | None = None,
    tz: str | None = None,
) -> list[dict]:
    """All basketball games in a league on a given date.

    `date_iso` is YYYY-MM-DD; defaults to today in `tz` (UTC if not given).
    `tz` is an IANA TZ used to anchor the calendar-day filter so the user's
    notion of "today" matches what's shown.
    """
    anchor_tz = _resolve_tz(tz)
    if date_iso is None:
        date_iso = datetime.now(anchor_tz).date().isoformat()

    games: list[dict] = []

    # BSD primary if covered. Query a ±1 day window because BSD filters by
    # UTC event_date but we want games landing on `date_iso` in the user's
    # TZ — late local-night games on the requested day can spill into the
    # next UTC day (or vice versa). The dedupe filter narrows back to the
    # target day after.
    if bsd.is_supported(league_slug):
        try:
            league_id = bsd.slug_to_league_id(league_slug)
            from_iso, to_iso = _bsd_query_window(date_iso)
            events = bsd.league_events(league_id, date_from=from_iso, date_to=to_iso)
            games = [_from_bsd_event(e, slug=league_slug) for e in events]
            if games:
                logger.info("bsd basketball served %s/%s with %d games (raw, pre-filter)",
                            league_slug, date_iso, len(games))
        except Exception as exc:
            logger.warning("bsd basketball failed for %s/%s: %s",
                           league_slug, date_iso, exc)

    # 365scores fallback (covers everything BSD doesn't, and rescues BSD failures)
    if not games:
        try:
            # 365 expects YYYYMMDD form; the adapter normalizes either shape.
            raw = s365.fetch_games(date_iso.replace("-", ""), league=league_slug)
            games = [_from_365_event(e, slug=league_slug) for e in raw]
            if games:
                logger.info("365scores basketball served %s/%s with %d games",
                            league_slug, date_iso, len(games))
        except Exception as exc:
            logger.warning("365scores basketball failed for %s/%s: %s",
                           league_slug, date_iso, exc)

    games = _enforce_date_and_dedupe(games, date_iso, anchor_tz)
    return games


def get_basketball_event_detail(canonical: str) -> dict | None:
    """Full event payload (score header + quarter-by-quarter + prediction)."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        return bsd.event_detail(int(native))
    if source == "s365":
        # 365scores doesn't expose a parallel detail endpoint we use; the
        # game shape from fetch_games already carries score + status.
        # Caller can resolve via /games and pull what they need.
        return None
    return None


def get_basketball_team_stats(canonical: str) -> dict | None:
    """Comprehensive team stats grouped by category. BSD only."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        return bsd.event_team_stats(int(native))
    return None


def get_basketball_box_score(canonical: str) -> dict | None:
    """Per-player stats for both teams. BSD only."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        return bsd.event_box_score(int(native))
    return None


# ---------------------------------------------------------------------------
# Date filter & dedupe (same shape as soccer_source's helper)


_DEFAULT_TZ = ZoneInfo("UTC")


def _bsd_query_window(date_iso: str) -> tuple[str, str]:
    """Return (date_from, date_to) covering date_iso ±1 day, ISO yyyy-mm-dd.
    Used to capture games whose UTC event_date lands one day off from the
    user-TZ calendar day they were requested for."""
    from datetime import date, timedelta
    d = date.fromisoformat(date_iso)
    return ((d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat())


def _resolve_tz(tz: str | None) -> ZoneInfo:
    if not tz:
        return _DEFAULT_TZ
    try:
        return ZoneInfo(tz)
    except Exception:
        logger.warning("invalid tz %r — falling back to UTC", tz)
        return _DEFAULT_TZ


def _tipoff_local_date(tipoff: str | None, tz: ZoneInfo = _DEFAULT_TZ) -> str | None:
    if not tipoff:
        return None
    try:
        if tipoff.endswith("Z"):
            dt = datetime.fromisoformat(tipoff[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(tipoff)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date().isoformat()
    except Exception:
        return None


def _enforce_date_and_dedupe(games: list[dict], date_iso: str, tz: ZoneInfo) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for g in games:
        cid = g.get("id")
        if not cid or cid in seen:
            continue
        # BSD basketball event_date is ISO UTC; 365 gives startTime ISO too.
        tipoff = g.get("date") or g.get("event_date")
        local_date = _tipoff_local_date(tipoff, tz)
        if local_date is not None and local_date != date_iso:
            continue
        seen.add(cid)
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# Normalizers — emit the ESPN-like game shape the rest of the app already speaks


def _from_bsd_event(event: dict, *, slug: str) -> dict:
    """BSD basketball event → ESPN-compatible game dict.

    The downstream consumer is `GameParser.parse` on iOS, which expects
    ESPN's `competitions[].competitors[].team.{id,abbreviation,displayName}`
    shape. We translate.
    """
    eid = event.get("id")
    bsd_status = (event.get("status") or "").lower()
    home_team = event.get("home_team", {}) or {}
    away_team = event.get("away_team", {}) or {}

    espn_state, espn_detail, period = _bsd_status_to_espn(bsd_status)

    home_competitor = {
        "homeAway": "home",
        "score": str(event.get("home_score") or 0),
        "team": {
            "id": str(home_team.get("id") or ""),
            "abbreviation": home_team.get("short_name") or home_team.get("name", "")[:3].upper(),
            "displayName": home_team.get("name", ""),
            "name": home_team.get("short_name", "") or home_team.get("name", ""),
            "logo": _basketball_logo_url(slug, home_team.get("name"), home_team.get("id")),
        },
    }
    away_competitor = {
        "homeAway": "away",
        "score": str(event.get("away_score") or 0),
        "team": {
            "id": str(away_team.get("id") or ""),
            "abbreviation": away_team.get("short_name") or away_team.get("name", "")[:3].upper(),
            "displayName": away_team.get("name", ""),
            "name": away_team.get("short_name", "") or away_team.get("name", ""),
            "logo": _basketball_logo_url(slug, away_team.get("name"), away_team.get("id")),
        },
    }

    return {
        "id": canonical_id("bsd", eid),
        "source": "bsd",
        "source_event_id": eid,
        "league": slug,
        "shortName": f"{away_competitor['team']['abbreviation']} @ {home_competitor['team']['abbreviation']}",
        "name": f"{away_team.get('name', '')} at {home_team.get('name', '')}",
        "date": event.get("event_date"),
        "status": {
            "type": {"state": espn_state, "detail": espn_detail},
            "period": period,
            "displayClock": "",
        },
        "competitions": [{
            "status": {
                "type": {"state": espn_state, "detail": espn_detail},
                "period": period,
                "displayClock": "",
            },
            "competitors": [home_competitor, away_competitor],
        }],
    }


def _bsd_team_logo_url(team_id: int | str | None) -> str:
    if not team_id:
        return ""
    return f"https://sports.bzzoiro.com/img/team/{team_id}/"


# ---------------------------------------------------------------------------
# 365scores logo enrichment for BSD events.
#
# Logos across the app come from 365scores. For BSD-served leagues we
# build a per-league `team_name → 365_team_id` index by scanning a few
# recent dates of 365 fixtures, then swap the BSD `/img/team/{id}/` URL
# for `imagecache.365scores.com/.../Competitors/{365_id}` when emitting.
#
# Leagues 365 doesn't cover (Liga ACB, Lega A, Germany BBL) keep BSD
# crests as a fallback so the user still sees an icon.

_365_LOGO_BASE = (
    "https://imagecache.365scores.com/image/upload/"
    "d_Competitors:default1.png/Competitors/"
)

_365_index_lock = threading.Lock()
_365_team_index: dict[str, dict[str, int]] = {}
_365_index_fetched_at: dict[str, float] = {}
_365_INDEX_TTL = 24 * 3600  # 24 h


def _normalize_team_name(name: str) -> str:
    return name.lower().strip().replace(".", "").replace("'", "")


def _build_365_team_index_for(league_slug: str) -> dict[str, int]:
    """Walk a handful of recent dates of 365 fixtures for `league_slug` and
    collect every (team_name → 365_team_id) we see. Cached per-league.

    365 only covers basketball leagues we've mapped in `score365_adapter.LEAGUES`
    (NBA, Euroleague, EuroCup, BCL, Winner League). Other slugs short-circuit.
    """
    if league_slug not in s365.LEAGUES:
        return {}

    index: dict[str, int] = {}
    comp_id = s365.COMPETITION_IDS.get(league_slug)
    if comp_id is None:
        return index

    def absorb_team(team: dict | None) -> None:
        if not team:
            return
        try:
            tid_int = int(team.get("id") or 0)
        except (TypeError, ValueError):
            return
        if not tid_int:
            return
        for name_key in (team.get("name"), team.get("shortName"), team.get("longName")):
            if not name_key:
                continue
            index[_normalize_team_name(name_key)] = tid_int

    # Pass 1: standings — gives us every team in the league regardless
    # of whether they're currently playing. Critical for NBA: eliminated
    # teams aren't in the rolling games window during playoffs.
    try:
        with s365.Client365() as client:
            data = client.get("/standings/", params={"competitions": str(comp_id)})
        for stage in data.get("standings", []) or []:
            for row in stage.get("rows", []) or []:
                absorb_team(row.get("competitor"))
    except Exception as exc:
        logger.debug("365 standings fetch failed for %s: %s", league_slug, exc)

    # Pass 2: rolling games window — fills in any teams missing from
    # standings (e.g. cup competitions where /standings/ might be sparse).
    try:
        raw_games = s365._fetch_competition_window(comp_id)
        for g in raw_games:
            absorb_team(g.get("homeCompetitor"))
            absorb_team(g.get("awayCompetitor"))
    except Exception as exc:
        logger.debug("365 window fetch failed for %s: %s", league_slug, exc)

    return index


def _get_365_team_index(league_slug: str) -> dict[str, int]:
    now = time.monotonic()
    with _365_index_lock:
        cached = _365_team_index.get(league_slug)
        fetched_at = _365_index_fetched_at.get(league_slug, 0.0)
        if cached is not None and (now - fetched_at) < _365_INDEX_TTL:
            return cached
    # Build outside the lock; updates are commutative.
    fresh = _build_365_team_index_for(league_slug)
    with _365_index_lock:
        _365_team_index[league_slug] = fresh
        _365_index_fetched_at[league_slug] = now
    logger.info("built 365 logo index for %s: %d teams", league_slug, len(fresh))
    return fresh


def _365_team_logo_url(league_slug: str, team_name: str | None) -> str:
    """365 CDN URL for a team in this league, looked up by name. Empty
    string when no match — caller should fall back to a BSD URL or skip."""
    if not team_name:
        return ""
    index = _get_365_team_index(league_slug)
    if not index:
        return ""
    tid = index.get(_normalize_team_name(team_name))
    if not tid:
        return ""
    return f"{_365_LOGO_BASE}{tid}"


def _basketball_logo_url(league_slug: str, team_name: str | None, bsd_id: int | str | None) -> str:
    """Resolve the best logo URL for a basketball team. Prefers 365's
    CDN (all logos come from one source for visual consistency), falls
    back to BSD's `/img/team/{id}/` for leagues 365 doesn't cover or
    when name-matching can't find a 365 id (e.g., a newly-promoted club)."""
    url = _365_team_logo_url(league_slug, team_name)
    if url:
        return url
    return _bsd_team_logo_url(bsd_id)


def _bsd_status_to_espn(status: str) -> tuple[str, str, int]:
    """BSD basketball status → ESPN's (state, detail, period) tuple.

    BSD uses: 'notstarted' | 'inprogress' | 'finished' | 'postponed' | 'cancelled'.
    """
    if status == "finished":
        return "post", "Final", 4
    if status == "inprogress":
        return "in", "In Progress", 0
    if status == "postponed":
        return "pre", "Postponed", 0
    if status == "cancelled":
        return "pre", "Cancelled", 0
    return "pre", "Scheduled", 0


def _from_365_event(event: dict, *, slug: str) -> dict:
    """365scores basketball event → ESPN-shape. score365_adapter.fetch_games
    already returns ESPN-shaped dicts — we just stamp the canonical id and
    league slug. The original `id` (numeric string) becomes our `s365-<id>`."""
    raw_id = event.get("id")
    event = dict(event)  # shallow copy so we don't mutate the adapter's cache
    event["id"] = canonical_id("s365", raw_id) if raw_id else canonical_id("s365", "0")
    event["source"] = "score365"
    event["source_event_id"] = raw_id
    event["league"] = slug
    return event
