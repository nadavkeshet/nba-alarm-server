"""Soccer source router — sits on top of bzzoiro_adapter + sofascore_adapter.

Public surface:
  - get_soccer_matches(league_slug, date_iso=None) -> list[dict]
  - get_live_matches(league_slugs=None) -> list[dict]
  - get_soccer_match_state(canonical_id) -> dict | None
  - get_soccer_match_detail(canonical_id) -> dict | None  (includes xG/momentum)

Every method returns dicts in a single canonical shape (see SoccerMatch below)
regardless of which source provided the data. Source routing is automatic:
  - PRIMARY_SOURCE table in competition_map.json controls primary
  - Adapter raises → automatic failover to the secondary
  - All cache layers live inside the adapters themselves

The "canonical_id" returned is a string like 'bsd-372' or 'sofa-14023956'.
This is the ID iOS and the alarm engine use everywhere — it's source-agnostic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import bzzoiro_adapter as bsd
import sofascore_adapter as sofa
from score365_soccer_adapter import (
    BSD_COMPETITION_IDS,
    PRIMARY_SOURCE,
    SOFASCORE_COMPETITION_IDS,
    _COMPETITION_MAP,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical ID helpers — source-tagged so we always know which adapter to use


def canonical_id(source: str, native_id: int | str) -> str:
    """e.g. canonical_id('bsd', 372) -> 'bsd-372'"""
    return f"{source}-{native_id}"


def parse_canonical_id(cid: str) -> tuple[str, str]:
    """'bsd-372' -> ('bsd', '372'). Raises ValueError if malformed."""
    if "-" not in cid:
        raise ValueError(f"bad canonical id: {cid!r}")
    source, native = cid.split("-", 1)
    if source not in ("bsd", "sofa", "s365"):
        raise ValueError(f"unknown source in canonical id: {cid!r}")
    return source, native


# ---------------------------------------------------------------------------
# Normalization


def _normalize_status_bsd(raw_status: str | None, period: str | None) -> tuple[str, str]:
    """BSD enum → our (status, period) pair."""
    s = (raw_status or "").lower()
    p = (period or "").lower()
    if s in ("notstarted", "scheduled", "pre"):
        return "scheduled", "pre"
    if s == "finished" or s == "ft":
        return "ft", "ft"
    if s in ("postponed", "cancelled"):
        return s, "pre"
    if s in ("inprogress", "live"):
        # Map BSD period codes
        if p in ("ht", "halftime"):
            return "ht", "ht"
        if "extra" in p or p in ("et", "et1", "et2"):
            return "et", "et"
        if "penalty" in p or p in ("pens", "shootout"):
            return "pens", "pens"
        if p == "1st" or p == "1h":
            return "live", "1h"
        if p == "2nd" or p == "2h":
            return "live", "2h"
        return "live", "1h"
    return s or "scheduled", p or "pre"


def _normalize_status_sofa(status: dict | None) -> tuple[str, str]:
    """Sofascore status dict → our (status, period) pair."""
    s = (status or {}).get("type", "")
    desc = (status or {}).get("description", "").lower()
    if s == "notstarted":
        return "scheduled", "pre"
    if s == "finished":
        return "ft", "ft"
    if s in ("postponed", "canceled"):
        return s, "pre"
    if s == "inprogress":
        if "halftime" in desc or desc == "ht":
            return "ht", "ht"
        if "extra" in desc:
            return "et", "et"
        if "penalt" in desc:
            return "pens", "pens"
        if "1st" in desc or "first" in desc:
            return "live", "1h"
        if "2nd" in desc or "second" in desc:
            return "live", "2h"
        return "live", "1h"
    return s or "scheduled", "pre"


def _bsd_team_image_url(team_id: int) -> str:
    return f"https://sports.bzzoiro.com/img/team/{team_id}/"


def _from_bsd_event(event: dict, *, slug: str | None = None) -> dict:
    """Normalize a BSD /events/{id}/ payload (or list item) into SoccerMatch shape."""
    eid = event.get("id")
    status, period = _normalize_status_bsd(event.get("status"), event.get("period"))
    return {
        "id": canonical_id("bsd", eid),
        "source": "bsd",
        "source_event_id": eid,
        "competition": slug or _bsd_league_to_slug(event.get("league_id")),
        "competition_id_native": event.get("league_id"),
        "kickoff": event.get("event_date"),
        "status": status,
        "period": period,
        "minute": event.get("current_minute"),
        "home": {
            "id": str(event.get("home_team_id") or ""),
            "name": event.get("home_team", ""),
            "short_name": "",
            "crest": _bsd_team_image_url(event.get("home_team_id")) if event.get("home_team_id") else None,
            "score": event.get("home_score"),
            "score_ht": event.get("home_score_ht"),
            "xg": None,
        },
        "away": {
            "id": str(event.get("away_team_id") or ""),
            "name": event.get("away_team", ""),
            "short_name": "",
            "crest": _bsd_team_image_url(event.get("away_team_id")) if event.get("away_team_id") else None,
            "score": event.get("away_score"),
            "score_ht": event.get("away_score_ht"),
            "xg": None,
        },
        "venue": (event.get("venue_id") and {"id": event["venue_id"]}) or None,
        "is_derby": bool(event.get("is_local_derby")),
        "is_neutral_ground": bool(event.get("is_neutral_ground")),
        "round_number": event.get("round_number"),
        "round_name": event.get("round_name"),
    }


def _from_sofa_event(event: dict, *, slug: str | None = None) -> dict:
    """Normalize a Sofascore scheduled-event or event payload into SoccerMatch shape."""
    eid = event.get("id")
    status, period = _normalize_status_sofa(event.get("status"))
    home = event.get("homeTeam", {}) or {}
    away = event.get("awayTeam", {}) or {}
    return {
        "id": canonical_id("sofa", eid),
        "source": "sofascore",
        "source_event_id": eid,
        "competition": slug or _sofa_tournament_to_slug(
            (event.get("tournament", {}) or {}).get("uniqueTournament", {}).get("id")
        ),
        "competition_id_native": (event.get("tournament", {}) or {}).get("uniqueTournament", {}).get("id"),
        "kickoff": _iso_from_unix(event.get("startTimestamp")),
        "status": status,
        "period": period,
        "minute": _sofa_minute_from_time(event),
        "home": {
            "id": str(home.get("id") or ""),
            "name": home.get("name", ""),
            "short_name": home.get("shortName", "") or home.get("nameCode", ""),
            "crest": f"https://api.sofascore.app/api/v1/team/{home.get('id')}/image" if home.get("id") else None,
            "score": (event.get("homeScore", {}) or {}).get("current"),
            "score_ht": (event.get("homeScore", {}) or {}).get("period1"),
            "xg": None,
        },
        "away": {
            "id": str(away.get("id") or ""),
            "name": away.get("name", ""),
            "short_name": away.get("shortName", "") or away.get("nameCode", ""),
            "crest": f"https://api.sofascore.app/api/v1/team/{away.get('id')}/image" if away.get("id") else None,
            "score": (event.get("awayScore", {}) or {}).get("current"),
            "score_ht": (event.get("awayScore", {}) or {}).get("period1"),
            "xg": None,
        },
        "venue": None,  # Sofascore venue lives elsewhere; skip for now
        "is_derby": False,
        "is_neutral_ground": False,
        "round_number": (event.get("roundInfo", {}) or {}).get("round"),
        "round_name": (event.get("roundInfo", {}) or {}).get("name"),
    }


def _iso_from_unix(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _sofa_minute_from_time(event: dict) -> int | None:
    time = event.get("time", {}) or {}
    # Sofascore returns currentPeriodStartTimestamp + period; for simplicity,
    # use the "currentPeriodStart" or fall back to None. The radar will fetch
    # the live event detail for accurate minute when it matters.
    return time.get("currentMinute") or None


def _bsd_league_to_slug(league_id: int | None) -> str | None:
    if league_id is None:
        return None
    for slug, lid in BSD_COMPETITION_IDS.items():
        if lid == league_id:
            return slug
    return None


def _sofa_tournament_to_slug(tournament_id: int | None) -> str | None:
    if tournament_id is None:
        return None
    for slug, tid in SOFASCORE_COMPETITION_IDS.items():
        if tid == tournament_id:
            return slug
    return None


# ---------------------------------------------------------------------------
# Public read API


def get_soccer_matches(
    league_slug: str,
    date_iso: str | None = None,
) -> list[dict]:
    """All matches in a league on a given date.

    `league_slug` must be one of the keys in PRIMARY_SOURCE.
    `date_iso` is YYYY-MM-DD; defaults to today (UTC).

    Source chain (cascades on either exception OR empty result):
        1. primary       (from PRIMARY_SOURCE — bsd or sofascore)
        2. the other unofficial source (bsd ↔ sofascore)
        3. 365scores     (free fallback; covers all 10 competitions and is
                         reachable from Railway egress IPs that Sofascore
                         occasionally blocks)
    """
    if league_slug not in PRIMARY_SOURCE:
        logger.warning("get_soccer_matches: unknown league %r", league_slug)
        return []

    if date_iso is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()

    primary = PRIMARY_SOURCE[league_slug]

    # Build the order of sources to try: primary, then the other, then 365scores
    sources: list[tuple[str, callable]] = []  # type: ignore[type-arg]
    if primary == "bsd":
        if league_slug in BSD_COMPETITION_IDS:
            sources.append(("bsd", _fetch_bsd))
        if league_slug in SOFASCORE_COMPETITION_IDS:
            sources.append(("sofascore", _fetch_sofa))
    else:
        if league_slug in SOFASCORE_COMPETITION_IDS:
            sources.append(("sofascore", _fetch_sofa))
        if league_slug in BSD_COMPETITION_IDS:
            sources.append(("bsd", _fetch_bsd))
    sources.append(("score365", _fetch_365))

    for name, fetcher in sources:
        try:
            matches = fetcher(league_slug, date_iso)
        except Exception as exc:
            logger.warning("soccer source %s errored for %s/%s: %s", name, league_slug, date_iso, exc)
            continue
        if matches:
            logger.info("soccer source %s served %s/%s with %d matches", name, league_slug, date_iso, len(matches))
            return matches
        logger.info("soccer source %s returned 0 for %s/%s — trying next", name, league_slug, date_iso)

    logger.warning("all soccer sources empty for %s/%s", league_slug, date_iso)
    return []


def _fetch_bsd(slug: str, date_iso: str) -> list[dict]:
    league_id = BSD_COMPETITION_IDS[slug]
    events = bsd.league_events(league_id=league_id, date_from=date_iso, date_to=date_iso, limit=200)
    return [_from_bsd_event(e, slug=slug) for e in events]


def _fetch_sofa(slug: str, date_iso: str) -> list[dict]:
    tournament_id = SOFASCORE_COMPETITION_IDS[slug]
    events = sofa.scheduled_events(date_iso)
    # Filter to the target tournament
    matched = [
        e for e in events
        if (e.get("tournament", {}) or {}).get("uniqueTournament", {}).get("id") == tournament_id
    ]
    return [_from_sofa_event(e, slug=slug) for e in matched]


def _fetch_365(slug: str, date_iso: str) -> list[dict]:
    """Free fallback via the existing 365scores client. Covers all 10 launch
    competitions. Used when both BSD and Sofascore come up empty (e.g. when
    Sofascore blocks Railway's egress IP)."""
    from score365_soccer_adapter import soccer_games_by_date, COMPETITION_IDS as S365_IDS
    competition_id = S365_IDS.get(slug)
    if competition_id is None:
        return []
    # 365scores wants MM/DD/YYYY
    y, m, d = date_iso.split("-")
    mdy = f"{m}/{d}/{y}"
    raw = soccer_games_by_date(mdy)
    matched = [g for g in raw if g.get("competitionId") == competition_id]
    return [_from_365_game(g, slug=slug) for g in matched]


def _from_365_game(game: dict, *, slug: str | None = None) -> dict:
    """Normalize a 365scores football game dict into SoccerMatch shape."""
    gid = game.get("id")
    home = game.get("homeCompetitor", {}) or {}
    away = game.get("awayCompetitor", {}) or {}
    status_group = game.get("statusGroup")
    status_text = (game.get("shortStatusText") or "").lower()
    # Heuristic status normalization for 365scores
    if status_group == 2 or status_text in ("sched.", "scheduled"):
        status, period = "scheduled", "pre"
    elif status_group == 4 or status_text == "ended":
        status, period = "ft", "ft"
    elif status_text == "ht":
        status, period = "ht", "ht"
    elif status_text == "post.":
        status, period = "postponed", "pre"
    else:
        status, period = "live", "1h"

    def _crest(team_id):
        if not team_id:
            return None
        return f"https://imagecache.365scores.com/image/upload/d_Competitors:default1.png/Competitors/{team_id}"

    return {
        "id": canonical_id("s365", gid),
        "source": "score365",
        "source_event_id": gid,
        "competition": slug,
        "competition_id_native": game.get("competitionId"),
        "kickoff": game.get("startTime"),
        "status": status,
        "period": period,
        "minute": int(game.get("gameTime")) if isinstance(game.get("gameTime"), (int, float)) else None,
        "home": {
            "id": str(home.get("id") or ""),
            "name": home.get("name", ""),
            "short_name": home.get("symbolicName", "") or home.get("shortName", ""),
            "crest": _crest(home.get("id")),
            "score": int(home.get("score")) if isinstance(home.get("score"), (int, float)) and home.get("score") is not None and home.get("score") >= 0 else None,
            "score_ht": None,
            "xg": None,
        },
        "away": {
            "id": str(away.get("id") or ""),
            "name": away.get("name", ""),
            "short_name": away.get("symbolicName", "") or away.get("shortName", ""),
            "crest": _crest(away.get("id")),
            "score": int(away.get("score")) if isinstance(away.get("score"), (int, float)) and away.get("score") is not None and away.get("score") >= 0 else None,
            "score_ht": None,
            "xg": None,
        },
        "venue": ({"name": (game.get("venue") or {}).get("name")} if game.get("venue") else None),
        "is_derby": False,
        "is_neutral_ground": False,
        "round_number": game.get("roundNum"),
        "round_name": game.get("roundName"),
    }


def get_live_matches(league_slugs: list[str] | None = None) -> list[dict]:
    """All currently-live matches across the requested leagues (defaults to all in scope).

    Combines BSD and Sofascore; deduplicates by competition (we trust each
    competition's primary source — no merging across providers).
    """
    if league_slugs is None:
        league_slugs = list(PRIMARY_SOURCE.keys())

    out: list[dict] = []

    # BSD live — covers 8 competitions in a single call
    bsd_slugs = [s for s in league_slugs if PRIMARY_SOURCE.get(s) == "bsd"]
    if bsd_slugs:
        try:
            events = bsd.live_events()
            for e in events:
                slug = _bsd_league_to_slug(e.get("league_id"))
                if slug in bsd_slugs:
                    out.append(_from_bsd_event(e, slug=slug))
        except Exception as exc:
            logger.warning("BSD live_events failed: %s", exc)

    # Sofascore live — covers Euros + Ligat Ha'al
    sofa_slugs = [s for s in league_slugs if PRIMARY_SOURCE.get(s) == "sofascore"]
    if sofa_slugs:
        sofa_tids = {SOFASCORE_COMPETITION_IDS[s] for s in sofa_slugs if s in SOFASCORE_COMPETITION_IDS}
        try:
            events = sofa.live_events()
            for e in events:
                tid = (e.get("tournament", {}) or {}).get("uniqueTournament", {}).get("id")
                if tid in sofa_tids:
                    out.append(_from_sofa_event(e))
        except Exception as exc:
            logger.warning("Sofascore live_events failed: %s", exc)

    return out


def get_soccer_match_state(canonical: str) -> dict | None:
    """Minimal match state (score, status, minute). Cheap call — used by the radar loop."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        ev = bsd.event_detail(int(native))
        return _from_bsd_event(ev) if ev else None
    if source == "sofa":
        ev = sofa.event_detail(int(native))
        return _from_sofa_event(ev) if ev else None
    return None


def get_soccer_match_detail(canonical: str) -> dict | None:
    """Full match payload including xG, momentum, shotmap, incidents.

    This is the expensive call. Use in the per-alarm watcher or Drama Engine
    cycle — not in the radar polling loop.
    """
    source, native = parse_canonical_id(canonical)
    eid = int(native)
    if source == "bsd":
        ev = bsd.event_detail(eid)
        if not ev:
            return None
        out = _from_bsd_event(ev)
        try:
            stats = bsd.event_stats(eid) or {}
            xg = bsd.get_team_xg(stats)
            if xg:
                out["home"]["xg"] = xg[0]
                out["away"]["xg"] = xg[1]
            out["momentum"] = stats.get("momentum") or []
            out["xg_per_minute"] = stats.get("xg_per_minute") or []
            out["shotmap"] = stats.get("shotmap") or []
            out["stats"] = stats.get("stats") or {}
        except Exception as exc:
            logger.warning("bsd stats failed for %s: %s", canonical, exc)
        try:
            out["incidents"] = bsd.event_incidents(eid)
        except Exception as exc:
            logger.warning("bsd incidents failed for %s: %s", canonical, exc)
        return out

    if source == "sofa":
        ev = sofa.event_detail(eid)
        if not ev:
            return None
        out = _from_sofa_event(ev)
        try:
            stats = sofa.event_statistics(eid) or {}
            xg = sofa.find_team_xg(stats)
            if xg:
                out["home"]["xg"] = xg[0]
                out["away"]["xg"] = xg[1]
            graph = sofa.event_graph(eid) or {}
            out["momentum"] = graph.get("graphPoints") or []
            out["shotmap"] = sofa.event_shotmap(eid)
            out["incidents"] = sofa.event_incidents(eid)
            # xg_per_minute isn't a direct Sofascore field — derive from shotmap if needed
            out["xg_per_minute"] = []
        except Exception as exc:
            logger.warning("sofa stats failed for %s: %s", canonical, exc)
        return out

    return None
