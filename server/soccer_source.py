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
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
    if s in ("finished", "ft"):
        return "ft", "ft"
    if s in ("postponed", "cancelled"):
        return s, "pre"
    # BSD sometimes sends the period name directly as the status (e.g. "1st_half", "2nd_half")
    # instead of the generic "inprogress" envelope. Handle both shapes.
    if s in ("halftime", "ht"):
        return "ht", "ht"
    if s in ("1st_half",) or (s == "inprogress" and (p in ("1st", "1h", "1t"))):
        return "live", "1h"
    if s in ("2nd_half",) or (s == "inprogress" and (p in ("2nd", "2h", "2t"))):
        return "live", "2h"
    if s in ("inprogress", "live"):
        # Map BSD period codes
        if p in ("ht", "halftime"):
            return "ht", "ht"
        if "extra" in p or p in ("et", "et1", "et2"):
            return "et", "et"
        if "penalty" in p or p in ("pens", "shootout"):
            return "pens", "pens"
        return "live", "1h"
    if "extra" in s or s in ("et", "et1", "et2", "extra_time"):
        return "et", "et"
    if "penalt" in s or s in ("pens", "shootout", "penalty_shootout"):
        return "pens", "pens"
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
    tz: str | None = None,
) -> list[dict]:
    """All matches in a league on a given date.

    `league_slug` must be one of the keys in PRIMARY_SOURCE.
    `date_iso` is YYYY-MM-DD; defaults to today in `tz` (UTC if no tz given).
    `tz` is an IANA timezone identifier — kickoffs are projected into this TZ
    before being compared to `date_iso`, so the user's notion of "today"
    matches what they see in the UI. Falls back to UTC if invalid.

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

    anchor_tz = _resolve_tz(tz)

    if date_iso is None:
        date_iso = datetime.now(anchor_tz).date().isoformat()

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
            before = len(matches)
            matches = _enforce_date_and_dedupe(matches, date_iso, anchor_tz)
            if before != len(matches):
                logger.info(
                    "soccer source %s for %s/%s: %d → %d after date+dedup filter",
                    name, league_slug, date_iso, before, len(matches),
                )
            if matches:
                logger.info("soccer source %s served %s/%s with %d matches", name, league_slug, date_iso, len(matches))
                _seed_search_index(matches)
                return matches
        logger.info("soccer source %s returned 0 for %s/%s — trying next", name, league_slug, date_iso)

    logger.warning("all soccer sources empty for %s/%s", league_slug, date_iso)
    return []


# Sources have different day boundaries (Sofa = UTC/CET, BSD = Israel, 365 =
# its own thing). To keep the user's notion of "today" stable, we project
# every kickoff into the caller's tz before comparing it to date_iso.
# Default = UTC when no tz is supplied (e.g. backend cron jobs).
_DEFAULT_TZ = ZoneInfo("UTC")


def _resolve_tz(tz: str | None) -> ZoneInfo:
    """Return a ZoneInfo for `tz` (IANA identifier) or UTC if unparsable."""
    if not tz:
        return _DEFAULT_TZ
    try:
        return ZoneInfo(tz)
    except Exception:
        logger.warning("invalid tz %r — falling back to UTC", tz)
        return _DEFAULT_TZ


def _kickoff_local_date(kickoff: str | None, tz: ZoneInfo = _DEFAULT_TZ) -> str | None:
    """Return YYYY-MM-DD of `kickoff` projected into `tz`, or None if unparsable."""
    if not kickoff:
        return None
    try:
        # Accept both '...Z' and '+00:00' suffixes
        if kickoff.endswith("Z"):
            dt = datetime.fromisoformat(kickoff[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(kickoff)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date().isoformat()
    except Exception:
        return None


def _enforce_date_and_dedupe(
    matches: list[dict],
    date_iso: str,
    tz: ZoneInfo = _DEFAULT_TZ,
) -> list[dict]:
    """Strict filter: keep only matches whose kickoff falls on `date_iso` in
    `tz`. Also dedupe by canonical id so the same match never appears twice
    in one response.

    Matches without a parseable kickoff are kept (we'd rather show a
    possibly-misdated match than drop it — the source filtered by date too).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for m in matches:
        cid = m.get("id")
        if not cid or cid in seen:
            continue
        local_date = _kickoff_local_date(m.get("kickoff"), tz)
        if local_date is not None and local_date != date_iso:
            continue
        seen.add(cid)
        out.append(m)
    return out


def _seed_search_index(matches: list[dict]) -> None:
    """Feed every match's teams into the search index so the iOS search
    overlay can return them. Best-effort — never raises."""
    try:
        from search_index import remember_soccer_teams_from_match
        for m in matches or []:
            remember_soccer_teams_from_match(m)
    except Exception:
        pass


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

    _seed_search_index(out)
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

    This is the expensive call. Fans the three upstream HTTP requests
    (event_detail, event_stats, event_incidents) out in parallel — they
    are independent, so doing them sequentially was adding 400–1000 ms of
    latency to every Match Summary open.
    """
    from concurrent.futures import ThreadPoolExecutor
    source, native = parse_canonical_id(canonical)
    eid = int(native)
    if source == "bsd":
        # Fan out the three upstream calls. Adapter has its own LRU + TTL
        # cache, so a warm match returns near-instantly here.
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_detail    = pool.submit(bsd.event_detail, eid)
            f_stats     = pool.submit(bsd.event_stats, eid)
            f_incidents = pool.submit(bsd.event_incidents, eid)
            ev = f_detail.result()
            if not ev:
                return None
            out = _from_bsd_event(ev)
            try:
                stats = f_stats.result() or {}
                xg = bsd.get_team_xg(stats)
                if xg:
                    out["home"]["xg"] = xg[0]
                    out["away"]["xg"] = xg[1]
                out["momentum"]      = stats.get("momentum") or []
                out["xg_per_minute"] = stats.get("xg_per_minute") or []
                out["shotmap"]       = stats.get("shotmap") or []
                out["stats"]         = stats.get("stats") or {}
            except Exception as exc:
                logger.warning("bsd stats failed for %s: %s", canonical, exc)
            try:
                out["incidents"] = f_incidents.result()
            except Exception as exc:
                logger.warning("bsd incidents failed for %s: %s", canonical, exc)
            return out

    if source == "sofa":
        # Sofascore — five independent calls; parallelize. Per-call failures
        # are isolated so one upstream hiccup doesn't 500 the endpoint.
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_detail    = pool.submit(sofa.event_detail, eid)
            f_stats     = pool.submit(sofa.event_statistics, eid)
            f_graph     = pool.submit(sofa.event_graph, eid)
            f_shotmap   = pool.submit(sofa.event_shotmap, eid)
            f_incidents = pool.submit(sofa.event_incidents, eid)
            try:
                ev = f_detail.result()
            except Exception as exc:
                logger.warning("sofa event_detail failed for %s: %s", canonical, exc)
                return None
            if not ev:
                return None
            out = _from_sofa_event(ev)
            # Stats
            try:
                stats = f_stats.result() or {}
                xg = sofa.find_team_xg(stats)
                if xg:
                    out["home"]["xg"] = xg[0]
                    out["away"]["xg"] = xg[1]
            except Exception as exc:
                logger.warning("sofa stats failed for %s: %s", canonical, exc)
            # Momentum (Sofa key shape {minute, value} → canonical {m, v})
            try:
                graph = f_graph.result() or {}
                out["momentum"] = [
                    {"m": p.get("minute"), "v": p.get("value")}
                    for p in (graph.get("graphPoints") or [])
                    if p.get("minute") is not None and p.get("value") is not None
                ]
            except Exception as exc:
                logger.warning("sofa graph failed for %s: %s", canonical, exc)
                out["momentum"] = []
            # Shotmap
            try:
                out["shotmap"] = _normalize_sofa_shotmap(f_shotmap.result() or [])
            except Exception as exc:
                logger.warning("sofa shotmap failed for %s: %s", canonical, exc)
                out["shotmap"] = []
            # Incidents
            try:
                out["incidents"] = _normalize_sofa_incidents(f_incidents.result() or [])
            except Exception as exc:
                logger.warning("sofa incidents failed for %s: %s", canonical, exc)
                out["incidents"] = []
            out["xg_per_minute"] = []
            return out

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Sofascore → canonical shape normalizers
#
# BSD's per-shot and per-incident shapes are what iOS speaks. Sofascore uses
# completely different keys (camelCase, nested player objects), so we translate
# here at the API boundary so the iOS contract stays uniform regardless of
# which adapter produced the data.


def _normalize_sofa_shotmap(raw: list[dict]) -> list[dict]:
    """Translate Sofascore shotmap entries to the BSD/canonical shape iOS speaks."""
    out: list[dict] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        pos_src = s.get("playerCoordinates") or {}
        player = s.get("player") or {}
        out.append({
            "min":        s.get("time"),
            "added":      s.get("addedTime"),
            "home":       bool(s.get("isHome")) if s.get("isHome") is not None else None,
            "type":       s.get("shotType"),
            "sit":        s.get("situation"),
            "body":       s.get("bodyPart"),
            "xg":         s.get("xg"),
            "xgot":       s.get("xgot"),
            "player_id":  player.get("id"),
            "pos":        {
                "x": pos_src.get("x"),
                "y": pos_src.get("y"),
            },
            "gml":        s.get("goalMouthLocation"),
        })
    return out


def _normalize_sofa_incidents(raw: list[dict]) -> list[dict]:
    """Translate Sofascore incidents to the BSD/canonical shape iOS speaks.

    Handles: goal, card, substitution, period, injuryTime. Unknown
    `incidentType` values pass through with minimal mapping so they at
    least decode without crashing.
    """
    out: list[dict] = []
    for inc in raw:
        if not isinstance(inc, dict):
            continue
        itype = (inc.get("incidentType") or "").lower()
        iclass = (inc.get("incidentClass") or "").lower()
        minute = inc.get("time")
        is_home = bool(inc.get("isHome")) if inc.get("isHome") is not None else None
        player  = inc.get("player") or {}
        norm: dict[str, Any] = {
            "type":      itype,
            "minute":    minute,
            "is_home":   is_home,
        }
        if itype == "goal":
            # Sofa uses incidentClass = 'regular' | 'penalty' | 'owngoal' | 'header'
            # Mirror BSD: own goals get type='owngoal'
            if iclass == "owngoal":
                norm["type"] = "owngoal"
            norm["player"]    = player.get("shortName") or player.get("name")
            norm["player_id"] = player.get("id")
            # Assist: Sofascore lists it as "assist1" on the incident
            assist1 = inc.get("assist1") or {}
            if assist1:
                norm["assist"] = assist1.get("shortName") or assist1.get("name")
            norm["home_score"] = inc.get("homeScore")
            norm["away_score"] = inc.get("awayScore")
        elif itype == "card":
            norm["card_type"] = iclass or None
            norm["player"]    = player.get("shortName") or player.get("name")
            norm["player_id"] = player.get("id")
        elif itype == "substitution":
            p_in  = inc.get("playerIn")  or {}
            p_out = inc.get("playerOut") or {}
            norm["player_in"]      = p_in.get("shortName")  or p_in.get("name")
            norm["player_out"]     = p_out.get("shortName") or p_out.get("name")
            norm["player_in_id"]   = p_in.get("id")
            norm["player_out_id"]  = p_out.get("id")
        elif itype == "period":
            norm["text"]       = inc.get("text")
            norm["home_score"] = inc.get("homeScore")
            norm["away_score"] = inc.get("awayScore")
        elif itype == "injurytime":
            # BSD spells this exactly "injuryTime"; keep that vocabulary.
            norm["type"]   = "injuryTime"
            norm["length"] = inc.get("length") or inc.get("addedTime")
        # else: unknown type passes through with just type/minute/is_home
        out.append(norm)
    return out
