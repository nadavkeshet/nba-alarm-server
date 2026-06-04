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
import drama_engine
import win_probability
from score365_soccer_adapter import (
    BSD_COMPETITION_IDS,
    PRIMARY_SOURCE,
    SOFASCORE_COMPETITION_IDS,
    _COMPETITION_MAP,
)

logger = logging.getLogger(__name__)

# ONE shared, bounded pool for match-detail fan-out (BSD: 3 calls, Sofa: 5).
# A per-request pool spawned 3-5 fresh threads on every Match Summary open, so a
# burst of users opening sheets multiplied threads on top of the request pool
# (the same explosion that _FEED_POOL/_WATCH_POOL in main.py were created to fix).
# All submitted tasks are leaf HTTP calls (no re-submission to this pool), so a
# bounded shared pool can't deadlock. Results are always gathered inside the
# `with nullcontext(_DETAIL_POOL)` block, so the pool is reused, never shut down.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from contextlib import nullcontext as _nullcontext

_DETAIL_POOL = _ThreadPoolExecutor(max_workers=8, thread_name_prefix="soccerdetail")

# canonical_id -> peak Drama Engine score seen for the match. Populated when the
# Drama Meter / timeline is computed (main.py), so a finished match's feed badge
# reflects how dramatic it actually got — not the FT-damped live state. Permanent
# for finished matches (FT scores don't change).
_peak_drama_cache: dict[str, float] = {}


def record_peak_drama(canonical_id: str, value: float) -> None:
    """Remember the highest Drama Engine total seen for a match (called by the
    /soccer/drama endpoints). Best-effort, monotonic."""
    try:
        prev = _peak_drama_cache.get(canonical_id)
        if prev is None or value > prev:
            _peak_drama_cache[canonical_id] = float(value)
    except Exception:
        pass


def pre_match_win_prob(eid: int) -> tuple[float, float, float] | None:
    """(p_home, p_draw, p_away) in 0–1 from the BSD pre-match prediction — the
    baseline for the Drama Engine's upset signal. None on any failure."""
    try:
        pred = bsd.event_prediction(int(eid)) or {}
        mr = (pred.get("markets") or {}).get("match_result") or {}
        ph, pd, pa = mr.get("prob_home"), mr.get("prob_draw"), mr.get("prob_away")
        if ph is None or pd is None or pa is None:
            return None
        total = float(ph) + float(pd) + float(pa)
        if total <= 0:
            return None
        return (float(ph) / total, float(pd) / total, float(pa) / total)
    except Exception:
        return None


# Clear-underdog thresholds — a single source of truth for the /prediction
# endpoint and Sensation Watch alarm creation. A match has a "clear underdog"
# when the favourite is meaningfully ahead AND the underdog is a longshot.
_UNDERDOG_MIN_GAP = 0.20
_UNDERDOG_MAX_PROB = 0.38
# FIFA-ranking fallback: BSD's pre-match model badly underrates obvious
# favourites for some minnow fixtures (it gave Germany only 44% vs Curaçao).
# When the odds are too even to flag an underdog, fall back to the world
# ranking gap so a clear mismatch on paper still counts.
_RANK_GAP = 25


def _underdog_payload(side: dict, *, is_home: bool, win_prob: float | None, source: str) -> dict:
    return {
        "underdog_is_home": is_home,
        "underdog_team_id": str(side.get("id") or ""),
        "underdog_name": side.get("name") or "",
        "underdog_win_prob": round(win_prob, 4) if win_prob is not None else None,
        "underdog_source": source,
    }


def resolve_underdog(canonical: str, *, detail: dict | None = None,
                     pm: tuple[float, float, float] | None = None) -> dict | None:
    """Identify the clear underdog of a match. Tries the BSD pre-match odds
    first; if those are too even, falls back to the FIFA world-ranking gap.
    Returns None when neither signal flags a clear underdog. Callers may pass an
    already-fetched `detail` / `pm` to avoid re-fetching. BSD-only.
    """
    try:
        source, native = parse_canonical_id(canonical)
    except ValueError:
        return None
    if source != "bsd":
        return None
    try:
        eid = int(native)
    except (TypeError, ValueError):
        return None
    if pm is None:
        pm = pre_match_win_prob(eid)
    if detail is None:
        detail = get_soccer_match_detail(canonical) or {}
    home = detail.get("home") or {}
    away = detail.get("away") or {}

    # 1. Odds-based clear underdog.
    if pm:
        p_home, _p_draw, p_away = pm
        underdog_is_home = p_home < p_away
        dog_prob = min(p_home, p_away)
        fav_prob = max(p_home, p_away)
        if (fav_prob - dog_prob) >= _UNDERDOG_MIN_GAP and dog_prob <= _UNDERDOG_MAX_PROB:
            dog = home if underdog_is_home else away
            return _underdog_payload(dog, is_home=underdog_is_home, win_prob=dog_prob, source="odds")

    # 2. FIFA-ranking fallback for lopsided fixtures the odds model underrates.
    import fifa_rankings
    rh = fifa_rankings.rank(home.get("name"))
    ra = fifa_rankings.rank(away.get("name"))
    if rh is not None and ra is not None and abs(rh - ra) >= _RANK_GAP:
        underdog_is_home = rh > ra  # higher rank number = lower-ranked = underdog
        dog = home if underdog_is_home else away
        dog_prob = None
        if pm:
            dog_prob = pm[0] if underdog_is_home else pm[2]
        return _underdog_payload(dog, is_home=underdog_is_home, win_prob=dog_prob, source="ranking")

    return None


def wc_group_standings(slug: str = "world_cup") -> list[list[dict]] | None:
    """All groups' live standings for a grouped competition (the World Cup) as a
    list of groups, each a list of normalized rows {team_id, position, points,
    played, gf, ga, gd}. Powers the Drama Engine's best-thirds qualification
    jeopardy. None on any failure / tolerant of the raw BSD group shape."""
    try:
        comp_id = BSD_COMPETITION_IDS.get(slug)
        if comp_id is None:
            return None
        raw = bsd.league_standings(comp_id) or {}
        raw_groups = raw.get("groups") or raw.get("standings") or []
        out: list[list[dict]] = []
        for grp in raw_groups:
            rows_raw = (grp.get("rows") or grp.get("standings") or grp.get("table")
                        or grp.get("teams") or []) if isinstance(grp, dict) else grp
            rows: list[dict] = []
            for r in rows_raw or []:
                team = r.get("team") or {}
                tid = r.get("team_id") or team.get("id")
                gf = _to_int(r.get("gf") or r.get("scores_for") or r.get("goals_for")) or 0
                ga = _to_int(r.get("ga") or r.get("scores_against") or r.get("goals_against")) or 0
                rows.append({
                    "team_id":  str(tid) if tid is not None else "",
                    "position": _to_int(r.get("position") or r.get("rank")) or 0,
                    "points":   _to_int(r.get("points") or r.get("pts")) or 0,
                    "played":   _to_int(r.get("played") or r.get("matches") or r.get("games")) or 0,
                    "gf": gf, "ga": ga,
                    "gd": _to_int(r.get("gd") or r.get("goal_difference")) or (gf - ga),
                })
            if rows:
                out.append(rows)
        return out or None
    except Exception:
        return None

# Module-level cache: canonical_id -> (home_pens, away_pens)
# Populated when a match detail is fetched and penalty kicks are found in the shotmap.
# The list endpoint reads this cache so matches show penalty scores even without a detail fetch.
_pens_cache: dict[str, tuple[int, int]] = {}


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


_WATCH_THRESHOLD = 6.0  # parity with the iOS badge (CompactMatchRow shows >= 6)


def _to_float(v) -> float | None:
    try:
        if isinstance(v, dict):          # REST stats wrap xg as {"actual": ...}
            v = v.get("actual")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _soccer_watch_score(
    source: str,
    eid: int | None,
    status: str,
    minute: int | None,
    home_score: int | None,
    away_score: int | None,
    *,
    slug: str | None = None,
    round_name: str | None = None,
    is_derby: bool = False,
    home_score_ht: int | None = None,
    away_score_ht: int | None = None,
    home_xg: float | None = None,
    away_xg: float | None = None,
) -> float | None:
    """0–10 watch score for a match card — the Drama Engine total, so the feed
    badge agrees with the live Drama Meter.

    - LIVE BSD: build a drama context from the in-memory WS snapshot (xG,
      dangerous-attack momentum proxy, odds win-probability + swing) — cheap, no
      REST. - FINISHED BSD: reuse the cached PEAK drama (how dramatic it actually
      got) when the match has been scored, else a lightweight finished-quality
      formula. - Everything else: lightweight. None below the badge threshold.
    """
    try:
        if home_score is None and away_score is None:
            return None
        st = (status or "").lower()
        is_live = st in ("live", "ht", "et", "pens")
        is_ft   = st in ("ft", "aet")
        # The watch score now shows on EVERY match (both sports) — no must-watch
        # threshold; any started match returns its drama value, low or high.

        # Live BSD with a WS snapshot → full drama (same math as the Drama Meter)
        if source == "bsd" and eid is not None and is_live:
            ctx = _live_drama_ctx(eid, st, minute, home_score, away_score,
                                  slug, round_name, is_derby)
            if ctx is not None:
                return drama_engine.compute_drama(ctx).total

        # Finished BSD → cached peak drama if we've ever scored this match
        if source == "bsd" and is_ft and eid is not None:
            peak = _peak_drama_cache.get(f"bsd-{eid}")
            if peak is not None:
                return peak

        # Lightweight (never None — every started match gets a value).
        return _lightweight_drama(
            st, minute, home_score, away_score,
            home_score_ht, away_score_ht, home_xg, away_xg,
            slug, round_name, is_derby, never_none=True,
        )
    except Exception:
        return None


def _live_drama_ctx(eid, status, minute, hs, aw, slug, round_name, is_derby) -> dict | None:
    """Drama context from the in-memory WS snapshots (no REST). None when the
    event isn't subscribed / has no live data — caller falls back."""
    try:
        import bsd_websocket as _ws
        client = _ws.client()
        snap  = client.get_event_snapshot(eid)
        odds  = client.get_odds_snapshot(eid)
        swing = client.get_winprob_swing(eid)
    except Exception:
        snap, odds, swing = None, None, None
    if not snap and odds is None and swing is None:
        return None

    home: dict = {"score": hs}
    away: dict = {"score": aw}
    pressure = None
    if snap:
        h_side = snap.get("home") or {}
        a_side = snap.get("away") or {}
        home["xg"] = _to_float(h_side.get("xg"))
        away["xg"] = _to_float(a_side.get("xg"))
        home["big_chances"] = h_side.get("big_chances")
        away["big_chances"] = a_side.get("big_chances")
        h_dng = _to_float(h_side.get("dangerous_attack_pct")) or 0.0
        a_dng = _to_float(a_side.get("dangerous_attack_pct")) or 0.0
        if (h_dng + a_dng) > 0:
            pressure = (h_dng - a_dng) / (h_dng + a_dng)   # [-1,1], + = home pressure
    return {
        "minute": minute or 0, "status": status, "competition": slug,
        "round_name": round_name, "is_derby": is_derby,
        "home": home, "away": away,
        "momentum_pressure": pressure, "recent_events": [],
        "win_prob": win_probability.win_prob_from_snapshot(odds),
        "win_prob_swing": swing,
    }


def _lightweight_drama(status, minute, hs, aw, hs_ht, aw_ht, h_xg, a_xg,
                       slug, round_name, is_derby, *, never_none=False) -> float | None:
    """Cheap score from data already on the event object. For FINISHED matches
    this rates 'how good was it to watch' (goals + closeness + comeback + xG,
    scaled by stakes) rather than the FT-damped live state. For LIVE-without-WS
    it leans on the engine's live state model. When `never_none` it always
    returns a value (so every match shows a score)."""
    if hs is None and aw is None:
        return None
    hs = hs or 0
    aw = aw or 0
    is_ft = status in ("ft", "aet")
    stakes = drama_engine._stakes_multiplier(
        {"competition": slug, "round_name": round_name, "is_derby": is_derby}
    )

    if is_ft:
        diff = abs(hs - aw)
        total_goals = hs + aw
        if total_goals == 0:
            # 0-0 FT — a low watch but every match still shows a value.
            return round(0.6 * stakes, 2) if never_none else None
        closeness = {0: 1.0, 1: 0.7, 2: 0.4}.get(diff, 0.15)
        goals = min(1.0, total_goals / 4.0)
        base = (0.55 * closeness + 0.45 * goals) * 7.0   # 0–7
        if hs_ht is not None and aw_ht is not None:       # comeback win
            ht = (hs_ht > aw_ht) - (hs_ht < aw_ht)
            ft = (hs > aw) - (hs < aw)
            if ht and ft and ht != ft:
                base = min(7.0, base + 1.2)
        if h_xg is not None and a_xg is not None and (h_xg + a_xg) >= 3.0:
            base = min(7.0, base + 0.6)        # both ends created lots of chances
        return round(min(10.0, base * stakes), 2)

    # Live without a WS snapshot → engine's live state model (no odds/momentum).
    ctx = {
        "minute": minute or 0, "status": status, "competition": slug,
        "round_name": round_name, "is_derby": is_derby,
        "home": {"score": hs, "xg": h_xg}, "away": {"score": aw, "xg": a_xg},
        "momentum_pressure": None, "recent_events": [],
        "win_prob": None, "win_prob_swing": None,
    }
    return round(drama_engine.compute_drama(ctx).total, 2)


def _from_bsd_event(event: dict, *, slug: str | None = None) -> dict:
    """Normalize a BSD /events/{id}/ payload (or list item) into SoccerMatch shape."""
    eid = event.get("id")
    status, period = _normalize_status_bsd(event.get("status"), event.get("period"))
    cid = canonical_id("bsd", eid)
    cached_pens = _pens_cache.get(cid)
    return {
        "id": cid,
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
            "score_pens": cached_pens[0] if cached_pens else event.get("home_score_pens"),
            "xg": None,
        },
        "away": {
            "id": str(event.get("away_team_id") or ""),
            "name": event.get("away_team", ""),
            "short_name": "",
            "crest": _bsd_team_image_url(event.get("away_team_id")) if event.get("away_team_id") else None,
            "score": event.get("away_score"),
            "score_ht": event.get("away_score_ht"),
            "score_pens": cached_pens[1] if cached_pens else event.get("away_score_pens"),
            "xg": None,
        },
        "venue": (event.get("venue_id") and {"id": event["venue_id"]}) or None,
        "is_derby": bool(event.get("is_local_derby")),
        "is_neutral_ground": bool(event.get("is_neutral_ground")),
        "round_number": event.get("round_number"),
        "round_name": event.get("round_name"),
        "watch_score": _soccer_watch_score(
            "bsd", eid, status, event.get("current_minute"),
            event.get("home_score"), event.get("away_score"),
            slug=slug, round_name=event.get("round_name"),
            is_derby=bool(event.get("is_local_derby")),
            home_score_ht=event.get("home_score_ht"),
            away_score_ht=event.get("away_score_ht"),
        ),
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
            "score_pens": (event.get("homeScore", {}) or {}).get("penalties"),
            "xg": None,
        },
        "away": {
            "id": str(away.get("id") or ""),
            "name": away.get("name", ""),
            "short_name": away.get("shortName", "") or away.get("nameCode", ""),
            "crest": f"https://api.sofascore.app/api/v1/team/{away.get('id')}/image" if away.get("id") else None,
            "score": (event.get("awayScore", {}) or {}).get("current"),
            "score_ht": (event.get("awayScore", {}) or {}).get("period1"),
            "score_pens": (event.get("awayScore", {}) or {}).get("penalties"),
            "xg": None,
        },
        "venue": None,  # Sofascore venue lives elsewhere; skip for now
        "is_derby": False,
        "is_neutral_ground": False,
        "round_number": (event.get("roundInfo", {}) or {}).get("round"),
        "round_name": (event.get("roundInfo", {}) or {}).get("name"),
        "watch_score": _soccer_watch_score(
            "sofascore", eid, status, _sofa_minute_from_time(event),
            (event.get("homeScore", {}) or {}).get("current"),
            (event.get("awayScore", {}) or {}).get("current"),
            slug=slug,
            round_name=(event.get("roundInfo", {}) or {}).get("name"),
            home_score_ht=(event.get("homeScore", {}) or {}).get("period1"),
            away_score_ht=(event.get("awayScore", {}) or {}).get("period1"),
        ),
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
    """All matches in a league on a given date. See `get_soccer_matches_status`;
    this thin wrapper drops the trustworthiness flag for callers (e.g.
    /soccer/games) that just want the list and never cache it."""
    return get_soccer_matches_status(league_slug, date_iso, tz)[0]


def get_soccer_matches_status(
    league_slug: str,
    date_iso: str | None = None,
    tz: str | None = None,
) -> tuple[list[dict], bool]:
    """All matches in a league on a given date, plus a `trustworthy` flag.

    Returns `(matches, trustworthy)`. `trustworthy` is False when EVERY source
    raised an exception (e.g. an upstream 429/timeout/egress block) so no source
    ever confirmed the schedule — in that case an empty `matches` means "we don't
    know", NOT "no games". The feed uses this to avoid caching a transient
    upstream failure as a sticky 15-minute "no games" (which is what made future
    fixtures like the World Cup vanish from /feed while /soccer/games still
    served them). When at least one source replies cleanly — even with zero
    games — the empty result is real and `trustworthy` is True.

    `league_slug` must be one of the keys in PRIMARY_SOURCE.
    `date_iso` is YYYY-MM-DD; defaults to today in `tz` (UTC if no tz given).
    `tz` is an IANA timezone identifier — kickoffs are projected into this TZ
    before being compared to `date_iso`, so the user's notion of "today"
    matches what they see in the UI. Falls back to UTC if invalid.

    Source chain (cascades on either exception OR empty result):
        1. BSD           (primary for most competitions, incl. the World Cup)
        2. 365scores     (free fallback; covers all 10 competitions and is
                         reachable from Railway egress IPs that Sofascore
                         occasionally blocks)
    """
    if league_slug not in PRIMARY_SOURCE:
        logger.warning("get_soccer_matches: unknown league %r", league_slug)
        return [], True  # a genuinely unknown league is a definitive empty

    anchor_tz = _resolve_tz(tz)

    if date_iso is None:
        date_iso = datetime.now(anchor_tz).date().isoformat()

    # Sofascore was removed from the cascade: it block-403s from Railway's
    # egress IPs, so on a cold feed fan-out every miss burned a slow
    # fingerprint-rotation through a dead source and stalled the schedule. BSD
    # (primary for most competitions, incl. the World Cup) plus 365scores
    # (covers all competitions) are sufficient.
    sources: list[tuple[str, callable]] = []  # type: ignore[type-arg]
    if league_slug in BSD_COMPETITION_IDS:
        sources.append(("bsd", _fetch_bsd))
    sources.append(("score365", _fetch_365))

    any_source_replied = False  # at least one source returned without raising
    for name, fetcher in sources:
        try:
            matches = fetcher(league_slug, date_iso)
        except Exception as exc:
            logger.warning("soccer source %s errored for %s/%s: %s", name, league_slug, date_iso, exc)
            continue
        any_source_replied = True
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
                return matches, True
        logger.info("soccer source %s returned 0 for %s/%s — trying next", name, league_slug, date_iso)

    if any_source_replied:
        logger.info("all soccer sources empty for %s/%s (confirmed)", league_slug, date_iso)
    else:
        logger.warning("all soccer sources ERRORED for %s/%s — empty is untrusted", league_slug, date_iso)
    return [], any_source_replied


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
    # 365scores expects DD/MM/YYYY
    y, m, d = date_iso.split("-")
    mdy = f"{d}/{m}/{y}"
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
        "watch_score": _soccer_watch_score(
            "s365", None, status, int(game.get("gameTime")) if isinstance(game.get("gameTime"), (int, float)) else None,
            int(home.get("score")) if isinstance(home.get("score"), (int, float)) and home.get("score") is not None and home.get("score") >= 0 else None,
            int(away.get("score")) if isinstance(away.get("score"), (int, float)) and away.get("score") is not None and away.get("score") >= 0 else None,
            slug=slug, round_name=game.get("roundName"),
        ),
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


# ---------------------------------------------------------------------------
# WebSocket live data overlay
# ---------------------------------------------------------------------------

# Canonical WS-side field → (canonical stats key, coerce type)
_WS_STAT_MAP: list[tuple[str, str, type]] = [
    ("xg",                      "expected_goals",           float),
    ("total_shots",             "total_shots",              int),
    ("shots_on_target",         "shots_on_target",          int),
    ("shots_off_target",        "shots_off_target",         int),
    ("shots_inside_box",        "shots_inside_box",         int),
    ("blocked_shots",           "blocked_shots",            int),
    ("hit_woodwork",            "hit_woodwork",             int),
    ("big_chances",             "big_chances",              int),
    ("big_chances_missed",      "big_chances_missed",       int),
    ("big_saves",               "big_saves",                int),
    ("corner_kicks",            "corner_kicks",             int),
    ("fouls",                   "fouls",                    int),
    ("yellow_cards",            "yellow_cards",             int),
    ("red_cards",               "red_cards",                int),
    ("offsides",                "offsides",                 int),
    ("pass_accuracy_pct",       "pass_accuracy_pct",        float),
    ("touches_in_penalty_area", "touches_in_penalty_area",  int),
    ("goalkeeper_saves",        "goalkeeper_saves",         int),
    ("total_saves",             "total_saves",              int),
    ("interceptions",           "interceptions",            int),
    ("clearances",              "clearances",               int),
    ("duels",                   "duels",                    int),
    ("aerial_duels",            "aerial_duels",             int),
    ("dangerous_attack_pct",    "dangerous_attack_pct",     float),
    ("attack_pct",              "attack_pct",               float),
    ("ball_safe_pct",           "ball_safe_pct",            float),
]


def _ws_side_to_stats(ws_side: dict) -> dict:
    """Map one WS team-side snapshot to the canonical per-team stats shape."""
    s: dict = {}
    for ws_key, canon_key, typ in _WS_STAT_MAP:
        v = ws_side.get(ws_key)
        if v is None:
            continue
        try:
            s[canon_key] = typ(v)
        except (TypeError, ValueError):
            pass
    # accurate_passes: WS sends an {value, total, pct} object
    ap = ws_side.get("accurate_passes")
    if isinstance(ap, dict):
        if ap.get("value") is not None:
            try:
                s["accurate_passes"] = int(ap["value"])
            except (TypeError, ValueError):
                pass
        if ap.get("total") is not None:
            try:
                s["total_passes"] = int(ap["total"])
            except (TypeError, ValueError):
                pass
        if ap.get("pct") is not None:
            try:
                s["pass_accuracy_pct"] = float(ap["pct"])
            except (TypeError, ValueError):
                pass
    elif isinstance(ap, (int, float)):
        s["accurate_passes"] = int(ap)
    return s


def _ws_overlay(out: dict, event_id: int) -> None:
    """Overlay in-memory WebSocket snapshots onto a canonical BSD match detail.

    Enriches score/minute, 40+ per-team stats, ball position (livedata at
    ~5 s cadence), and consensus odds — all without a REST round-trip.
    Completely non-destructive: no-ops when the WS singleton is not running
    or has no snapshot for this event (match not live or not yet subscribed).
    """
    try:
        import bsd_websocket as _ws
        ws = _ws.client()
    except Exception:
        return

    # ── event snapshot: score, time, 40+ stats ───────────────────────────
    event_snap = ws.get_event_snapshot(event_id)
    if event_snap:
        score = event_snap.get("score") or {}
        if isinstance(score.get("home"), (int, float)):
            out.setdefault("home", {})["score"] = int(score["home"])
        if isinstance(score.get("away"), (int, float)):
            out.setdefault("away", {})["score"] = int(score["away"])

        time_obj = event_snap.get("time") or {}
        if isinstance(time_obj.get("minute"), (int, float)):
            out["minute"] = int(time_obj["minute"])

        curr_stats: dict = out.get("stats") or {}
        for side in ("home", "away"):
            ws_side = event_snap.get(side) or {}
            if not isinstance(ws_side, dict):
                continue
            merged = {**curr_stats.get(side, {}), **_ws_side_to_stats(ws_side)}
            curr_stats[side] = merged
            # xg also lives on the team side object used by the score header
            if isinstance(ws_side.get("xg"), (int, float)):
                out.setdefault(side, {})["xg"] = float(ws_side["xg"])
        out["stats"] = curr_stats

    # ── livedata snapshot: ball position + situation (~5 s cadence) ───────
    ld = ws.get_livedata_snapshot(event_id)
    if ld:
        coords = ld.get("coordinates") or []
        first  = coords[0] if coords else {}
        out["livedata"] = {
            "side":      ld.get("side"),
            "situation": ld.get("situation"),
            "x":         first.get("x"),
            "y":         first.get("y"),
            "uts":       ld.get("uts"),
        }

    # ── odds snapshot: consensus across 11–12 bookmakers (~30 s) ─────────
    odds = ws.get_odds_snapshot(event_id)
    if odds:
        out["odds"] = odds.get("odds") or odds


def build_live_snapshot(canonical: str) -> dict:
    """Build the lightweight WS-only live-state response for /soccer/live/*.

    Returns {ws_active: false} when no snapshot is available (no REST calls
    are ever made).  On-demand subscribes the event so the next poll may
    already find data.
    """
    if not canonical.startswith("bsd-"):
        return {"ws_active": False}
    try:
        event_id = int(canonical[4:])
    except ValueError:
        return {"ws_active": False}

    try:
        import bsd_websocket as _ws
        ws = _ws.client()
    except Exception:
        return {"ws_active": False}

    # Eagerly subscribe so the next poll cycle finds data even if the
    # 30-second sub-manager loop hasn't run yet. Guarded: this polling
    # endpoint must never 500 — a failed WS read just means "no snapshot yet".
    try:
        if event_id not in ws.desired_subscriptions():
            ws.subscribe(event_id)
        event_snap = ws.get_event_snapshot(event_id)
        ld         = ws.get_livedata_snapshot(event_id)
        odds       = ws.get_odds_snapshot(event_id)
    except Exception:
        return {"ws_active": False}

    if not event_snap and not ld and not odds:
        return {"ws_active": False}

    out: dict = {"ws_active": True, "event_id": event_id}

    if event_snap:
        out["score"]  = event_snap.get("score")
        out["minute"] = (event_snap.get("time") or {}).get("minute")
        out["status"] = (event_snap.get("time") or {}).get("status")
        stats: dict = {}
        for side in ("home", "away"):
            ws_side = event_snap.get(side) or {}
            if isinstance(ws_side, dict):
                stats[side] = _ws_side_to_stats(ws_side)
        if stats:
            out["stats"] = stats

    if ld:
        coords = ld.get("coordinates") or []
        first  = coords[0] if coords else {}
        out["livedata"] = {
            "side":      ld.get("side"),
            "situation": ld.get("situation"),
            "x":         first.get("x"),
            "y":         first.get("y"),
            "uts":       ld.get("uts"),
        }

    if odds:
        out["odds"] = odds.get("odds") or odds

    return out


# ---------------------------------------------------------------------------

def get_soccer_match_detail(canonical: str) -> dict | None:
    """Full match payload including xG, momentum, shotmap, incidents.

    This is the expensive call. Fans the three upstream HTTP requests
    (event_detail, event_stats, event_incidents) out in parallel — they
    are independent, so doing them sequentially was adding 400–1000 ms of
    latency to every Match Summary open.
    """
    source, native = parse_canonical_id(canonical)
    eid = int(native)
    if source == "bsd":
        # Fan out the three upstream calls. Adapter has its own LRU + TTL
        # cache, so a warm match returns near-instantly here.
        with _nullcontext(_DETAIL_POOL) as pool:
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
            # If BSD didn't provide penalty scores, derive them from shootout kicks in the shotmap.
            # Penalty shootout starts after 120 min of ET; kicks appear at minute >= 121.
            if out["home"].get("score_pens") is None and out.get("shotmap"):
                home_pens = sum(
                    1 for s in out["shotmap"]
                    if s.get("home") is True and s.get("type") == "goal" and (s.get("min") or 0) >= 121
                )
                away_pens = sum(
                    1 for s in out["shotmap"]
                    if s.get("home") is False and s.get("type") == "goal" and (s.get("min") or 0) >= 121
                )
                if home_pens > 0 or away_pens > 0:
                    out["home"]["score_pens"] = home_pens
                    out["away"]["score_pens"] = away_pens
                    _pens_cache[canonical] = (home_pens, away_pens)
            # Overlay in-memory WS snapshots (score/stats/livedata/odds) on
            # top of the REST payload — fresher data, no extra round-trip.
            # Fully guarded: a WS-snapshot read must never 500 the match detail.
            try:
                _ws_overlay(out, eid)
            except Exception as exc:
                logger.warning("ws overlay failed for %s: %s", canonical, exc)
            return out

    if source == "sofa":
        # Sofascore — five independent calls; parallelize. Per-call failures
        # are isolated so one upstream hiccup doesn't 500 the endpoint.
        with _nullcontext(_DETAIL_POOL) as pool:
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
            # Stats — normalize the full per-team table (this was missing, so
            # the iOS Statistics tab was empty for every Sofascore match) and
            # pull xG out of either the event detail or the stats block.
            try:
                stats = f_stats.result() or {}
                xg = sofa.find_team_xg(stats)
                if xg:
                    out["home"]["xg"] = xg[0]
                    out["away"]["xg"] = xg[1]
                norm_stats = _normalize_sofa_statistics(stats)
                if norm_stats:
                    out["stats"] = norm_stats
                    if not xg:
                        hxg = (norm_stats.get("home") or {}).get("expected_goals")
                        axg = (norm_stats.get("away") or {}).get("expected_goals")
                        if hxg is not None:
                            out["home"]["xg"] = hxg
                        if axg is not None:
                            out["away"]["xg"] = axg
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

    if source == "s365":
        from score365_soccer_adapter import soccer_game_detail, league_slug_for_competition_id
        try:
            raw = soccer_game_detail(int(native))
            if not raw:
                return None
            competition_id = raw.get("competitionId")
            slug = league_slug_for_competition_id(competition_id) if competition_id else None
            out = _from_365_game(raw, slug=slug)
            out["incidents"] = []
            out["shotmap"] = []
            out["xg_per_minute"] = []
            out["momentum"] = []
            return out
        except Exception as exc:
            logger.warning("s365 game_detail failed for %s: %s", canonical, exc)
            return None

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


# Sofascore statistics item `key` → canonical TeamStats field. Sofascore keys
# are quirky ("totalShotsOnGoal" actually means *total* shots). Mapped from a
# real Ligat ha'Al payload — these are the fields the iOS Statistics tab reads.
_SOFA_STAT_INT_KEYS = {
    "totalShotsOnGoal":      "total_shots",
    "shotsOnGoal":           "shots_on_target",
    "blockedScoringAttempt": "blocked_shots",
    "bigChanceCreated":      "big_chances",
    "bigChanceScored":       "big_chances_scored",
    "bigChanceMissed":       "big_chances_missed",
    "cornerKicks":           "corner_kicks",
    "fouls":                 "fouls",
    "offsides":              "offsides",
    "accuratePasses":        "accurate_passes",
    "passes":                "total_passes",
    "yellowCards":           "yellow_cards",
    "redCards":              "red_cards",
}


def _sofa_stat_int(value: Any) -> int | None:
    """Parse a Sofascore stat to int. Handles '21', '467', '149/178 (84%)'
    (takes the numerator) and '68%'."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "/" in s:                       # "149/178 (84%)" → numerator
        s = s.split("/", 1)[0]
    s = s.replace("%", "").strip()
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _sofa_stat_pct(value: Any) -> float | None:
    """Parse '68%' → 68.0."""
    if value is None:
        return None
    s = str(value).replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _sofa_stat_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _normalize_sofa_statistics(raw: dict | None) -> dict | None:
    """Translate a Sofascore /statistics response into the canonical
    `{home, away}` TeamStats shape iOS expects (uses the ALL-period group).
    Returns None when there's nothing usable so the caller can omit the key."""
    if not isinstance(raw, dict):
        return None
    home: dict[str, Any] = {}
    away: dict[str, Any] = {}
    found = False
    for period_group in raw.get("statistics", []):
        if period_group.get("period") != "ALL":
            continue
        for group in period_group.get("groups", []):
            for item in group.get("statisticsItems", []):
                key = item.get("key")
                if key == "ballPossession":
                    home["ball_possession"] = _sofa_stat_pct(item.get("home"))
                    away["ball_possession"] = _sofa_stat_pct(item.get("away"))
                    found = True
                elif key in ("expectedGoals", "xg"):
                    home["expected_goals"] = _sofa_stat_float(item.get("home"))
                    away["expected_goals"] = _sofa_stat_float(item.get("away"))
                    found = True
                elif key in _SOFA_STAT_INT_KEYS:
                    field = _SOFA_STAT_INT_KEYS[key]
                    home[field] = _sofa_stat_int(item.get("home"))
                    away[field] = _sofa_stat_int(item.get("away"))
                    found = True
    if not found:
        return None
    for side in (home, away):
        acc, tot = side.get("accurate_passes"), side.get("total_passes")
        if acc is not None and tot:
            side["pass_accuracy_pct"] = round(acc / tot * 100, 1)
    return {"home": home, "away": away}


# ─────────────────────────────────────────────────────────────────────────────
# Sofascore lineups + standings (the BSD-only endpoints 404'd / returned
# "unavailable" for Sofascore-primary leagues like the Israeli Premier League).


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _normalize_sofa_lineup_side(side: dict) -> dict:
    """Sofascore lineup side → canonical iOS LineupTeam shape. Splits starters
    from substitutes via each entry's `substitute` flag."""
    entries = side.get("players") or []
    starters: list[dict] = []
    subs: list[dict] = []
    team_id = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        p = e.get("player") or {}
        if not team_id and e.get("teamId"):
            team_id = e.get("teamId")
        rating = (e.get("statistics") or {}).get("rating")
        try:
            rating = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None
        pid = p.get("id") or 0
        norm = {
            "id":            pid,
            "name":          p.get("name") or "",
            "short_name":    p.get("shortName"),
            "position":      e.get("position") or p.get("position"),
            "jersey_number": _to_int(e.get("shirtNumber") or e.get("jerseyNumber") or p.get("jerseyNumber")),
            "ai_score":      rating,
            "photo_url":     f"https://api.sofascore.app/api/v1/player/{pid}/image" if pid else None,
        }
        (subs if e.get("substitute") else starters).append(norm)
    return {
        "team_id":     team_id,
        "team_name":   "",                  # iOS reads team names from match detail
        "formation":   side.get("formation"),
        "confidence":  None,
        "players":     starters,
        "substitutes": subs,
    }


def get_sofa_lineups(event_id: int) -> dict | None:
    """Confirmed/predicted lineups for a Sofascore match, in the canonical
    `{lineup_status, lineups:{home,away}, event_id}` shape iOS expects."""
    try:
        raw = sofa.event_lineups(event_id)
    except Exception as exc:
        logger.warning("sofa lineups failed for %s: %s", event_id, exc)
        return None
    if not isinstance(raw, dict) or not (raw.get("home") or raw.get("away")):
        return None
    return {
        "event_id":      event_id,
        "lineup_status": "confirmed" if raw.get("confirmed") else "predicted",
        "beta":          False,
        "lineups": {
            "home": _normalize_sofa_lineup_side(raw.get("home") or {}),
            "away": _normalize_sofa_lineup_side(raw.get("away") or {}),
        },
    }


def get_sofa_standings(slug: str) -> dict | None:
    """League table for a Sofascore-primary competition slug, in the canonical
    LeagueStandings shape (matching `_normalize_soccer_standings`).

    Sofascore returns split competitions (e.g. the Israeli Ligat Ha'al, which
    splits into Championship + Relegation rounds after the regular season) as
    MULTIPLE groups. We must NOT flatten them into one list — overlapping
    positions (1-6, then 1-8, …) made iOS render a garbled fragment. Instead we
    emit a proper `groups[]` array (one mini-table per group), exactly like the
    BSD grouped path the client already knows how to draw. For a single-table
    league we emit a flat `standings[]`.
    """
    utid = SOFASCORE_COMPETITION_IDS.get(slug)
    if not utid:
        return None
    try:
        season_list = sofa.seasons(utid)
        if not season_list:
            return None
        season_id = season_list[0].get("id")
        season_name = season_list[0].get("name") or ""
        raw = sofa.standings(utid, season_id)
    except Exception as exc:
        logger.warning("sofa standings failed for %s: %s", slug, exc)
        return None
    groups = raw.get("standings") if isinstance(raw, dict) else None
    if not groups:
        return None

    def _rows(grp: dict) -> list[dict]:
        out: list[dict] = []
        for row in (grp.get("rows") or []):
            team = row.get("team") or {}
            tid = team.get("id") or 0
            gf = _to_int(row.get("scoresFor")) or 0
            ga = _to_int(row.get("scoresAgainst")) or 0
            out.append({
                "position":   _to_int(row.get("position")) or 0,
                "team_id":    tid,
                "team_name":  team.get("name") or "",
                "team_crest": f"https://api.sofascore.app/api/v1/team/{tid}/image" if tid else None,
                "played":     _to_int(row.get("matches")) or 0,
                "won":        _to_int(row.get("wins")) or 0,
                "drawn":      _to_int(row.get("draws")) or 0,
                "lost":       _to_int(row.get("losses")) or 0,
                "gf": gf, "ga": ga, "gd": gf - ga,
                "pts":        _to_int(row.get("points")) or 0,
                "form":       None,
                "live":       None,
            })
        return out

    # When a league splits into playoff rounds, those "Round" groups ARE the
    # live standings; the full regular-season table Sofascore also returns is
    # redundant, so prefer the round groups when present.
    round_groups = [g for g in groups if "round" in (g.get("name") or "").lower()]
    use_groups = round_groups or groups

    base = {"league_id": utid, "season": {"id": season_id or 0, "name": season_name}}

    if len(use_groups) > 1:
        out_groups = [
            {"name": g.get("name") or "", "standings": _rows(g)}
            for g in use_groups
        ]
        out_groups = [g for g in out_groups if g["standings"]]
        if not out_groups:
            return None
        return {**base, "grouped": True, "standings": [], "groups": out_groups}

    rows = _rows(use_groups[0]) if use_groups else []
    if not rows:
        return None
    return {**base, "grouped": False, "standings": rows}


# ─────────────────────────────────────────────────────────────────────────────
# Team full-season matches. One call yields a team's whole season across all
# competitions. BSD for the leagues it covers (the app already holds BSD team
# ids there); Sofascore only for sofa-primary leagues (e.g. Israeli) where BSD
# coverage is thin.


# Position display order for national-team squads (GK → DF → MF → FW).
_SQUAD_POS_ORDER = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}


def national_team_squad(team_id: int | str) -> dict:
    """Official World Cup squad for one national team, each player merged to
    the club team they currently play for.

    Shape: {team_id, count, players: [{player_id, name, jersey_number,
    position, club, club_country, current_team_id, club_crest, caps, goals,
    age}]}. The club id comes from bsd.player_detail(player_id).current_team_id
    — the same resolver the favorites national-team flow uses. A failed
    player_detail degrades that row to current_team_id=None (club shown as a
    plain name, never an error)."""
    rows = bsd.worldcup_squad(team_id) or []
    if not rows:
        return {"team_id": team_id, "count": 0, "players": []}

    def _enrich(row: dict) -> dict:
        pid = row.get("player_id")
        club_id = None
        club_crest = None
        if pid is not None:
            try:
                det = bsd.player_detail(pid) or {}
                club_id = det.get("current_team_id")
                if club_id is not None:
                    club_crest = _bsd_team_image_url(club_id)
            except Exception as exc:
                logger.warning("squad: player_detail(%s) failed: %s", pid, exc)
        return {
            "player_id":       pid,
            "name":            row.get("name"),
            "jersey_number":   row.get("jersey_number"),
            "position":        row.get("position"),
            "club":            row.get("club"),
            "club_country":    row.get("club_country"),
            "current_team_id": club_id,
            "club_crest":      club_crest,
            "caps":            row.get("caps"),
            "goals":           row.get("goals"),
            "age":             row.get("age"),
        }

    with _nullcontext(_DETAIL_POOL) as pool:
        players = list(pool.map(_enrich, rows))

    players.sort(key=lambda p: (
        _SQUAD_POS_ORDER.get((p.get("position") or "").upper(), 9),
        p.get("jersey_number") if p.get("jersey_number") is not None else 999,
    ))
    return {"team_id": team_id, "count": len(players), "players": players}


def get_team_season(team_id: str, league_slug: str | None = None, name: str | None = None) -> list[dict]:
    source = PRIMARY_SOURCE.get(league_slug or "", "bsd")
    if source == "sofascore":
        return _sofa_team_season(team_id, name)
    return _bsd_team_season(team_id)


def _season_window() -> tuple[str, str]:
    """A rolling window that always spans the current season (Aug–Jun) plus a
    little slack for early/late fixtures, regardless of when it's called."""
    from datetime import date, timedelta
    today = date.today()
    return (today - timedelta(days=330)).isoformat(), (today + timedelta(days=75)).isoformat()


def _bsd_team_season(team_id: str) -> list[dict]:
    try:
        tid = int(team_id)
    except (TypeError, ValueError):
        return []
    date_from, date_to = _season_window()
    try:
        events = bsd.team_events(tid, date_from=date_from, date_to=date_to, limit=200)
    except Exception as exc:
        logger.warning("bsd team season failed for %s: %s", team_id, exc)
        return []
    return _finalize_team_matches([_from_bsd_event(e) for e in events])


def _sofa_team_season(team_id: str, name: str | None) -> list[dict]:
    sofa_id = _resolve_sofa_team_id(team_id, name)
    if not sofa_id:
        return []
    raw: list[dict] = []
    for page in range(0, 2):       # upcoming
        evs = sofa.team_events(sofa_id, upcoming=True, page=page)
        if not evs:
            break
        raw += evs
    for page in range(0, 4):       # played — ~120 matches covers a full season
        evs = sofa.team_events(sofa_id, upcoming=False, page=page)
        if not evs:
            break
        raw += evs
    season_start, _ = _season_window()   # keep this season only (team-events pages back further)
    out = []
    for e in raw:
        m = _from_sofa_event(e)
        if not m.get("competition"):
            tour = e.get("tournament", {}) or {}
            m["competition"] = (tour.get("uniqueTournament", {}) or {}).get("name") or tour.get("name") or "other"
        ko = m.get("kickoff")
        if ko and ko[:10] < season_start:   # ISO date prefix compares lexically
            continue
        out.append(m)
    return _finalize_team_matches(out)


def _resolve_sofa_team_id(team_id: str, name: str | None):
    if team_id and str(team_id).isdigit():
        try:
            if sofa.team_events(int(team_id), upcoming=False, page=0):
                return int(team_id)
        except Exception:
            pass
    if name:
        teams = sofa.search_teams(name, sport="football")
        if teams:
            return teams[0].get("id")
    return None


def _finalize_team_matches(matches: list[dict]) -> list[dict]:
    """Dedupe, guarantee a non-empty competition (iOS requires it), sort by kickoff."""
    seen: set = set()
    out: list[dict] = []
    for m in matches:
        if not m.get("competition"):
            m["competition"] = "other"
        mid = m.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    out.sort(key=lambda m: m.get("kickoff") or "")
    return out
