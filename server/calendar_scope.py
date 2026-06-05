"""Enumerate matches for a calendar subscription scope (both sports).

Given a subscription `scope` (world_cup | all | custom | favorites) and its
params, produce a deduped, window-filtered, kickoff-sorted list of match dicts
spanning BOTH soccer and basketball. Every returned dict is tagged with
`_sport` ("soccer" | "basketball") so the ICS writer can branch on shape:
soccer dicts use `kickoff` / `home`/`away`, basketball dicts use `date` /
`competitions[].competitors[]`.

The match fetchers (`soccer_source` / `basketball_source`) are per-date, so we
loop a [-7, +120] day window and concatenate. To avoid fetching the same league
window twice within a single render (e.g. two NBA favorite teams both needing
the NBA window), the public `collect_matches` builds a fresh per-render `cache`
dict and threads it through the helpers. We deliberately DON'T use a
module-level `lru_cache`: that would serve stale schedules across requests; the
calendar endpoint's own 5-minute response cache is the real TTL.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import soccer_source
import basketball_source

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("nba_monitor")

# Per-(league, day) source fetches are blocking HTTP. A cold render of a
# multi-league window (e.g. the "all" scope, or a basketball team with no known
# league that scans every bball league) is hundreds of sequential fetches and
# can take ~minute — long enough that iOS Calendar's subscribe times out (it
# surfaces as a misleading "insecure connection"). Fan the per-day fetches out
# across a shared thread pool so a window resolves in a few seconds. The source
# adapters are thread-safe (lock-guarded caches + thread-safe httpx clients).
_FETCH_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cal-fetch")

# Future-only window: today through +60 days. Past/finished games are excluded
# (a subscription calendar is about upcoming fixtures), and +60d keeps the
# "all" scope's per-(league, day) fan-out manageable.
DATE_BACK = 0
DATE_FWD = 60

# Hardcoded fallbacks used only if the catalog file/attr can't be read.
_SOCCER_SLUGS_FALLBACK = [
    "premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1",
    "ucl", "europa_league", "world_cup", "euros", "ligat_haal",
]
_BBALL_SLUGS_FALLBACK = ["nba", "euroleague", "eurocup", "bcl", "winner_league"]


# ── catalogs ────────────────────────────────────────────────────────────────

def _all_soccer_slugs() -> list[str]:
    """Soccer league slugs = the keys of config/competition_map.json."""
    try:
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "config", "competition_map.json")
        with open(path) as f:
            return list(json.load(f).keys())
    except Exception as exc:  # pragma: no cover - fallback path
        logger.warning("calendar_scope: soccer catalog unreadable (%s); using fallback", exc)
        return list(_SOCCER_SLUGS_FALLBACK)


def _all_bball_slugs() -> list[str]:
    """Basketball league slugs = the keys of score365_adapter.LEAGUES."""
    try:
        import score365_adapter
        return list(score365_adapter.LEAGUES.keys())
    except Exception as exc:  # pragma: no cover - fallback path
        logger.warning("calendar_scope: bball catalog unreadable (%s); using fallback", exc)
        return list(_BBALL_SLUGS_FALLBACK)


# ── window ──────────────────────────────────────────────────────────────────

def _today() -> "datetime.date":
    return datetime.now(timezone.utc).date()


def _window_dates() -> list[str]:
    """YYYY-MM-DD strings for each day in [-DATE_BACK, +DATE_FWD] vs today (UTC)."""
    today = _today()
    return [
        (today + timedelta(days=d)).isoformat()
        for d in range(-DATE_BACK, DATE_FWD + 1)
    ]


def _tag(matches: list[dict], sport: str) -> list[dict]:
    """Shallow-copy each match and stamp `_sport` so _finalize/ICS can branch."""
    out = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        mm = dict(m)
        mm["_sport"] = sport
        out.append(mm)
    return out


# ── per-league / per-team windows (memoized via the per-render cache) ─────────

def _fetch_window(fetcher, slug: str, label: str) -> list[dict]:
    """Fan the per-day fetches for one league window out across the pool, in
    parallel. `fetcher(slug, date_iso)` is a blocking source call."""
    dates = _window_dates()

    def _one(date_iso: str) -> list[dict]:
        try:
            return fetcher(slug, date_iso=date_iso) or []
        except Exception as exc:
            logger.warning("calendar_scope: %s %s/%s failed: %s", label, slug, date_iso, exc)
            return []

    out: list[dict] = []
    for res in _FETCH_POOL.map(_one, dates):
        out += res
    return out


def _soccer_league_window(slug: str, cache: dict) -> list[dict]:
    key = ("soccer_league", slug)
    if key in cache:
        return cache[key]
    tagged = _tag(_fetch_window(soccer_source.get_soccer_matches, slug, "soccer"), "soccer")
    cache[key] = tagged
    return tagged


def _bball_league_window(slug: str, cache: dict) -> list[dict]:
    key = ("bball_league", slug)
    if key in cache:
        return cache[key]
    tagged = _tag(_fetch_window(basketball_source.get_basketball_games, slug, "bball"), "basketball")
    cache[key] = tagged
    return tagged


def _soccer_team_matches(team_id, cache: dict, league: str | None = None) -> list[dict]:
    key = ("soccer_team", str(team_id), league or "")
    if key in cache:
        return cache[key]
    # Try the (BSD/Sofa) team-season first, passing the league so non-BSD teams
    # route to the correct provider.
    try:
        out = soccer_source.get_team_season(str(team_id), league_slug=league) or []
    except Exception as exc:
        logger.warning("calendar_scope: soccer team %s season failed: %s", team_id, exc)
        out = []

    if not out and league:
        # BSD/Sofa had nothing (e.g. an Israeli / 365-covered team). The league
        # window already cascades to 365scores — filter it to this team's matches.
        # _soccer_league_window returns already-tagged ("soccer") dicts.
        tid = str(team_id)
        filtered = [
            m for m in _soccer_league_window(league, cache)
            if tid in (
                str((m.get("home") or {}).get("id")),
                str((m.get("away") or {}).get("id")),
            )
        ]
        cache[key] = filtered
        return filtered

    tagged = _tag(out, "soccer")
    cache[key] = tagged
    return tagged


def _bball_competitor_ids(game: dict) -> set[str]:
    ids: set[str] = set()
    for comp in game.get("competitions") or []:
        for c in comp.get("competitors") or []:
            tid = (c.get("team") or {}).get("id")
            if tid is not None:
                ids.add(str(tid))
    return ids


def _bball_team_matches(team_id, league, cache: dict) -> list[dict]:
    """Games in `league`'s window whose competitors include `team_id`."""
    if team_id is None or not league:
        return []
    games = _bball_league_window(league, cache)
    tid = str(team_id)
    return [g for g in games if tid in _bball_competitor_ids(g)]


def _team_matches(ids, leagues, sport: str, cache: dict) -> list[dict]:
    """Matches for a team identified by its FULL provider id-set, found by
    scanning its leagues' windows (already _sport-tagged + 365-cascaded) and
    keeping any match whose team ids intersect `ids`. Basketball with no known
    leagues scans ALL basketball leagues (search gives bball teams no league)."""
    idset = {str(i) for i in (ids or []) if i is not None and str(i).strip()}
    if not idset:
        return []
    out: list[dict] = []
    if sport == "basketball":
        slugs = [l for l in (leagues or []) if l] or _all_bball_slugs()
        for slug in slugs:
            for g in _bball_league_window(slug, cache):
                if _bball_competitor_ids(g) & idset:
                    out.append(g)
    else:  # soccer
        for slug in (leagues or []):
            if not slug:
                continue
            for m in _soccer_league_window(slug, cache):
                h = str((m.get("home") or {}).get("id"))
                a = str((m.get("away") or {}).get("id"))
                if h in idset or a in idset:
                    out.append(m)
    return out


# ── favorites ─────────────────────────────────────────────────────────────────

def _alt_ids(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _favorites_matches(session, params: dict, user_token: str, cache: dict) -> list[dict]:
    from main import (
        SoccerFavoriteTeamDB,
        SoccerFavoriteCompetitionDB,
        SoccerFavoritePlayerDB,
        FavoriteTeamDB,
        FavoriteLeagueDB,
    )

    raw: list[dict] = []

    # Soccer favorite teams — matched by the club's FULL provider id-set
    # (primary + alternate provider ids for the SAME club) across all of the
    # team's competitions' windows.
    try:
        rows = session.query(SoccerFavoriteTeamDB).filter_by(user_token=user_token).all()
        for r in rows:
            ids = [r.team_id] + _alt_ids(r.alt_team_ids)
            leagues = [s for s in (r.competitions or "").split(",") if s.strip()]
            raw += _team_matches(ids, leagues, "soccer", cache)
    except Exception as exc:
        logger.warning("calendar_scope: soccer fav teams query failed: %s", exc)

    # Soccer favorite competitions.
    try:
        rows = session.query(SoccerFavoriteCompetitionDB).filter_by(user_token=user_token).all()
        for r in rows:
            if r.competition_id:
                raw += _soccer_league_window(r.competition_id, cache)
    except Exception as exc:
        logger.warning("calendar_scope: soccer fav competitions query failed: %s", exc)

    # Basketball favorite teams.
    try:
        rows = session.query(FavoriteTeamDB).filter_by(user_token=user_token).all()
        for r in rows:
            raw += _team_matches([r.team_id], [r.league] if r.league else [], "basketball", cache)
    except Exception as exc:
        logger.warning("calendar_scope: bball fav teams query failed: %s", exc)

    # Basketball favorite leagues.
    try:
        rows = session.query(FavoriteLeagueDB).filter_by(user_token=user_token).all()
        for r in rows:
            if r.league:
                raw += _bball_league_window(r.league, cache)
    except Exception as exc:
        logger.warning("calendar_scope: bball fav leagues query failed: %s", exc)

    # Soccer favorite players → their club + national-team games.
    if params.get("include_players"):
        try:
            rows = session.query(SoccerFavoritePlayerDB).filter_by(user_token=user_token).all()
            for r in rows:
                for tid in [r.team_id, *_alt_ids(r.alt_team_ids)]:
                    if tid:
                        raw += _soccer_team_matches(tid, cache)
        except Exception as exc:
            logger.warning("calendar_scope: soccer fav players query failed: %s", exc)

    return raw


# ── assembly ──────────────────────────────────────────────────────────────────

def _finalize(raw: list[dict]) -> list[dict]:
    """Dedup by id, drop matches outside the [-7, +120] day window, sort by
    kickoff/date ascending. `_sport` is already stamped by the helpers."""
    today = _today()
    lo = (today - timedelta(days=DATE_BACK)).isoformat()
    hi = (today + timedelta(days=DATE_FWD)).isoformat()

    seen: set = set()
    out: list[dict] = []
    for m in raw:
        mid = m.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        ko = m.get("kickoff") or m.get("date")
        if not ko:
            continue
        if not (lo <= ko[:10] <= hi):
            continue
        out.append(m)
    out.sort(key=lambda m: (m.get("kickoff") or m.get("date") or ""))
    return out


def collect_matches(session, scope: str, params: dict, user_token: str) -> list[dict]:
    """Return a deduped, window-filtered, kickoff-sorted list of `_sport`-tagged
    match dicts for the given subscription scope.

    scope ∈ {world_cup, all, custom, favorites}. `params` is the subscription's
    config (custom league/team lists, include_players flag). `session` is a
    SQLAlchemy session used only for the `favorites` scope.
    """
    params = params or {}
    cache: dict = {}
    raw: list[dict] = []

    if scope == "world_cup":
        raw += _soccer_league_window("world_cup", cache)

    elif scope == "all":
        for s in _all_soccer_slugs():
            raw += _soccer_league_window(s, cache)
        for s in _all_bball_slugs():
            raw += _bball_league_window(s, cache)

    elif scope == "custom":
        for slug in params.get("custom_soccer_league_slugs", []) or []:
            raw += _soccer_league_window(slug, cache)
        for slug in params.get("custom_bball_league_slugs", []) or []:
            raw += _bball_league_window(slug, cache)
        for t in params.get("custom_soccer_teams", []) or []:
            ids = t.get("ids") or ([t["id"]] if t.get("id") else [])
            leagues = t.get("leagues") or ([t["league"]] if t.get("league") else [])
            raw += _team_matches(ids, leagues, "soccer", cache)
        for t in params.get("custom_bball_teams", []) or []:
            ids = t.get("ids") or ([t["id"]] if t.get("id") else [])
            leagues = t.get("leagues") or ([t["league"]] if t.get("league") else [])
            raw += _team_matches(ids, leagues, "basketball", cache)
        # Legacy single-id key (pre-v3): best-effort via team-season.
        for tid in params.get("custom_soccer_team_ids", []) or []:
            raw += _soccer_team_matches(tid, cache, league=None)

    elif scope == "favorites":
        raw += _favorites_matches(session, params, user_token, cache)

    else:
        logger.warning("calendar_scope: unknown scope %r", scope)

    return _finalize(raw)
