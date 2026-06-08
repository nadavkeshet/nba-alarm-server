"""Soccer source router — sits on top of bzzoiro_adapter + 365scores.

Public surface:
  - get_soccer_matches(league_slug, date_iso=None) -> list[dict]
  - get_live_matches(league_slugs=None) -> list[dict]
  - get_soccer_match_state(canonical_id) -> dict | None
  - get_soccer_match_detail(canonical_id) -> dict | None  (includes xG/momentum)

Every method returns dicts in a single canonical shape (see SoccerMatch below)
regardless of which source provided the data. Source routing is automatic:
  - PRIMARY_SOURCE table in competition_map.json controls primary
  - Adapter raises / returns empty → automatic failover to the secondary
  - All cache layers live inside the adapters themselves

There is ONE cascade everywhere: BSD (where supported) → 365scores. The two
leagues 365 is primary for (euros, ligat_haal) have no BSD id, so they resolve
straight to 365.

The "canonical_id" returned is a string like 'bsd-372' or 's365-4702049'.
This is the ID iOS and the alarm engine use everywhere — it's source-agnostic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import functools

import bzzoiro_adapter as bsd
import drama_engine
import win_probability
import team_names_he


def _with_hebrew(fn):
    """Stamp `name_he` onto the home/away teams of a normalized match dict, so
    the iOS app can show Hebrew team names without shipping the big name file."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        out = fn(*args, **kwargs)
        if isinstance(out, dict):
            team_names_he.attach(out.get("home"))
            team_names_he.attach(out.get("away"))
        return out
    return wrapper
from score365_soccer_adapter import (
    BSD_COMPETITION_IDS,
    PRIMARY_SOURCE,
    _COMPETITION_MAP,
)

logger = logging.getLogger(__name__)

# ONE shared, bounded pool for match-detail fan-out (BSD: 3 calls, 365: 2).
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


def enrich_drama_ctx(ctx: dict, canonical_id: str, detail: dict | None = None) -> None:
    """Overlay EVERY live drama signal onto a drama ctx, so all consumers — the
    radar that fires alerts, the live Drama Meter on the card, and the breakdown
    endpoint — score off IDENTICAL data and show the same number. Previously the
    surfaces diverged: the radar had the live momentum + near-miss/saves thrill
    but not the pre-match upset; the endpoint had the inverse. bsd-* only for the
    WS parts; best-effort (a miss just leaves defaults)."""
    if canonical_id.startswith("bsd-") and canonical_id[4:].isdigit():
        try:
            import bsd_websocket as _ws
            eid = int(canonical_id[4:])
            swing = _ws.get_winprob_swing(eid)
            if swing is not None:
                ctx["win_prob_swing"] = swing
            sig = drama_engine.live_signals_from_ws_event(_ws.get_live_event_state(eid))
            for side in ("home", "away"):
                for k, v in (sig.get(side) or {}).items():
                    if v is not None:
                        ctx.setdefault(side, {})[k] = v
            blended = drama_engine.blend_pressure(sig.get("momentum_pressure"), _ws.get_attack_surge(eid))
            if blended is not None:
                ctx["momentum_pressure"] = blended
            pm = pre_match_win_prob(eid)
            if pm is not None:
                ctx["pre_match_win_prob"] = pm
        except Exception:
            pass
    if detail and (detail.get("competition") or "").lower() == "world_cup" \
            and "group" in (detail.get("round_name") or "").lower():
        try:
            groups = wc_group_standings("world_cup")
            if groups:
                ctx["wc_groups"] = groups
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


def get_soccer_broadcasts(canonical: str, *, country_code: str | None = None) -> list[dict]:
    """Where-to-watch for a match: TV channels carrying it, filtered to a
    territory (ISO-2) when given. BSD-only — s365 ids and missing data return [].
    De-duplicated by (channel, country); each row is
    {channel_name, channel_link, country_code, scheduled_start_time}.
    """
    try:
        source, native = parse_canonical_id(canonical)
    except ValueError:
        return []
    if source != "bsd":
        return []
    try:
        eid = int(native)
    except (TypeError, ValueError):
        return []
    try:
        rows = bsd.event_broadcasts(eid, country_code=country_code) or []
    except Exception as exc:
        logger.warning("broadcasts fetch failed for %s: %s", canonical, exc)
        return []
    out: list[dict] = []
    seen: set = set()
    for r in rows:
        name = (r.get("channel_name") or "").strip()
        if not name:
            continue
        key = (name, r.get("country_code"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "channel_name": name,
            "channel_link": (r.get("channel_link") or "").strip() or None,
            "country_code": r.get("country_code"),
            "scheduled_start_time": r.get("scheduled_start_time"),
        })
    return out


def wc_group_standings(slug: str = "world_cup") -> list[list[dict]] | None:
    """All groups' live standings for a grouped competition (the World Championship) as a
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
    if source not in ("bsd", "s365"):
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
        surge = client.get_attack_surge(eid)
    except Exception:
        snap, odds, swing, surge = None, None, None, None
    if not snap and odds is None and swing is None:
        return None

    home: dict = {"score": hs}
    away: dict = {"score": aw}
    # Live per-team stats live under snap['stats']['home'|'away']; the shared
    # helper reads the correct path. (This previously read snap['home'], which is
    # only team identity — id/name/short_name — so big_chances/pressure were
    # silently always None and this whole WS enrichment was a no-op.)
    sig = drama_engine.live_signals_from_ws_event(snap)
    for k, v in (sig.get("home") or {}).items():
        if v is not None:
            home[k] = v
    for k, v in (sig.get("away") or {}).items():
        if v is not None:
            away[k] = v
    return {
        "minute": minute or 0, "status": status, "competition": slug,
        "round_name": round_name, "is_derby": is_derby,
        "home": home, "away": away,
        # Blend the cumulative dangerous-attack asymmetry with the live
        # situation-stream surge (more responsive) into one pressure value.
        "momentum_pressure": drama_engine.blend_pressure(sig.get("momentum_pressure"), surge),
        "recent_events": [],
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


@_with_hebrew
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


def _bsd_league_to_slug(league_id: int | None) -> str | None:
    if league_id is None:
        return None
    for slug, lid in BSD_COMPETITION_IDS.items():
        if lid == league_id:
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
    fixtures like the World Championship vanish from /feed while /soccer/games still
    served them). When at least one source replies cleanly — even with zero
    games — the empty result is real and `trustworthy` is True.

    `league_slug` must be one of the keys in PRIMARY_SOURCE.
    `date_iso` is YYYY-MM-DD; defaults to today in `tz` (UTC if no tz given).
    `tz` is an IANA timezone identifier — kickoffs are projected into this TZ
    before being compared to `date_iso`, so the user's notion of "today"
    matches what they see in the UI. Falls back to UTC if invalid.

    Source chain (cascades on either exception OR empty result):
        1. BSD           (primary for most competitions, incl. the World Championship)
        2. 365scores     (covers all 10 competitions; primary for euros &
                         ligat_haal, which have no BSD id)
    """
    if league_slug not in PRIMARY_SOURCE:
        logger.warning("get_soccer_matches: unknown league %r", league_slug)
        return [], True  # a genuinely unknown league is a definitive empty

    anchor_tz = _resolve_tz(tz)

    if date_iso is None:
        date_iso = datetime.now(anchor_tz).date().isoformat()

    # ONE cascade: BSD (where it has a competition id) → 365scores. The two
    # 365-primary leagues (euros, ligat_haal) have no BSD id, so they go straight
    # to 365. 365 also covers everything as a fallback for the BSD-primary ones.
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


# Sources have different day boundaries (BSD = Israel, 365 =
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

    Matches without a parseable kickoff are dropped — keeping them caused
    every stale/misdated match (e.g. old WC editions with null kickoffs) to
    appear on every requested date.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for m in matches:
        cid = m.get("id")
        if not cid or cid in seen:
            continue
        local_date = _kickoff_local_date(m.get("kickoff"), tz)
        if local_date != date_iso:   # None != date_iso → also drops null-kickoff matches
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
    # Widen by ±1 day. BSD filters by UTC date, but a kickoff near midnight UTC
    # falls on a different LOCAL date (e.g. 22:00 UTC = next-day 01:00 in Israel),
    # and _enforce_date_and_dedupe narrows by local date. Without the window a
    # boundary fixture vanishes entirely — fetched on the UTC day but dropped by
    # the local-date filter, and never fetched on the local day (e.g. Brazil–Egypt
    # 22:00 UTC was missing from the friendlies feed).
    d0 = datetime.fromisoformat(date_iso).date()
    lo = (d0 - timedelta(days=1)).isoformat()
    hi = (d0 + timedelta(days=1)).isoformat()
    events = bsd.league_events(league_id=league_id, date_from=lo, date_to=hi, limit=200)
    return [_from_bsd_event(e, slug=slug) for e in events]


def _fetch_365(slug: str, date_iso: str) -> list[dict]:
    """365scores fetch. Covers all 10 launch competitions — the primary source
    for euros & ligat_haal (no BSD id), and the fallback for the BSD-primary
    ones when BSD comes up empty."""
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


@_with_hebrew
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


_LIVE_STATUSES = {"live", "ht", "et", "pens"}


def get_live_matches(league_slugs: list[str] | None = None) -> list[dict]:
    """All currently-live matches across the requested leagues (defaults to all in scope).

    Per competition, the live source is its primary: BSD for the BSD-primary
    leagues, 365scores for euros & ligat_haal. No merging across providers.
    """
    if league_slugs is None:
        league_slugs = list(PRIMARY_SOURCE.keys())

    out: list[dict] = []

    # BSD live — covers the BSD-primary competitions in a single call
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

    # 365 live — covers Euros + Ligat Ha'al. 365 has no "live only" endpoint,
    # so we pull today's games for those competitions and keep the live ones.
    s365_slugs = [s for s in league_slugs if PRIMARY_SOURCE.get(s) == "score365"]
    if s365_slugs:
        today = datetime.now(_DEFAULT_TZ).date().isoformat()
        for slug in s365_slugs:
            try:
                for m in _fetch_365(slug, today):
                    if (m.get("status") or "").lower() in _LIVE_STATUSES:
                        out.append(m)
            except Exception as exc:
                logger.warning("365 live fetch failed for %s: %s", slug, exc)

    _seed_search_index(out)
    return out


def get_soccer_match_state(canonical: str) -> dict | None:
    """Minimal match state (score, status, minute). Cheap call — used by the radar loop."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        ev = bsd.event_detail(int(native))
        return _from_bsd_event(ev) if ev else None
    if source == "s365":
        from score365_soccer_adapter import (
            soccer_game_detail, league_slug_for_competition_id,
        )
        raw = soccer_game_detail(int(native))
        if not raw:
            return None
        slug = league_slug_for_competition_id(raw.get("competitionId")) if raw.get("competitionId") else None
        return _from_365_game(raw, slug=slug)
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
        # During a break (half-time, ET interval) the WS clock counts the BREAK
        # elapsed (0,1,2…), NOT the match minute — overwriting with it would report
        # "minute 11" at half-time and trip the drama earliness damper. Keep the
        # REST minute (45) in that case.
        ws_tstatus = str(time_obj.get("status") or "").lower()
        _on_break = ws_tstatus in ("halftime", "ht", "break", "paused", "interrupted", "et_break")
        if isinstance(time_obj.get("minute"), (int, float)) and not _on_break:
            out["minute"] = int(time_obj["minute"])

        # Per-team stats live under event_snap['stats'][side] (event_snap[side]
        # is only team identity), so read the stats block — not the team dict.
        ws_stats = event_snap.get("stats") or {}
        curr_stats: dict = out.get("stats") or {}
        for side in ("home", "away"):
            ws_side = ws_stats.get(side) or {}
            if not isinstance(ws_side, dict) or not ws_side:
                continue
            merged = {**curr_stats.get(side, {}), **_ws_side_to_stats(ws_side)}
            curr_stats[side] = merged
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

    # ── WS situations → incidents ────────────────────────────────────────
    # A penalty AWARDED and a red card live in the WS situation stream
    # (`penalty_rewarded`, `red_card`) but are NOT in the REST incidents — so the
    # alert evaluators (which read `incidents`) would miss them entirely. Surface
    # them here, stamped at the current minute, so those alerts fire on the live
    # stream. Deduped against any REST incident already at that minute.
    minute = out.get("minute")
    if isinstance(minute, (int, float)):
        try:
            recents = ws.recent_situations(event_id, window_s=150.0)
        except Exception:
            recents = []
        if recents:
            incidents = out.setdefault("incidents", [])

            def _has(kind: str) -> bool:
                return any(isinstance(i, dict) and i.get("type") == kind
                           and abs(int(i.get("minute") or 0) - int(minute)) <= 1
                           for i in incidents)

            pen = next(((sd, s) for sd, s in recents
                        if "penalty" in s and ("reward" in s or "award" in s)), None)
            if pen and not _has("penalty"):
                incidents.append({"type": "penalty", "minute": int(minute),
                                  "is_home": pen[0] == "home", "_source": "ws"})
            red = next(((sd, s) for sd, s in recents
                        if "red_card" in s or ("red" in s and "card" in s)), None)
            if red and not _has("card"):
                incidents.append({"type": "card", "card_type": "red", "minute": int(minute),
                                  "is_home": red[0] == "home", "player": None, "_source": "ws"})


_WS_PERIOD_INT: dict[str, int] = {
    "1": 1, "1t": 1, "1st": 1, "1st_half": 1,
    "2": 2, "2t": 2, "2nd": 2, "2nd_half": 2,
    "3": 3, "et1": 3, "et_1": 3, "et 1": 3,
    "4": 4, "et2": 4, "et_2": 4, "et 2": 4,
}


def _ws_period_int(raw) -> int | None:
    """Normalize the WS `time.period` (int `1` or code like `"2T"`) to a plain
    1–4 the app can use for the clock's period offset. None when unknown."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if 1 <= raw <= 4 else None
    return _WS_PERIOD_INT.get(str(raw or "").strip().lower())


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
        time_obj = event_snap.get("time") or {}
        out["score"]  = event_snap.get("score")
        out["minute"] = time_obj.get("minute")
        out["status"] = time_obj.get("status")
        # Clock anchor: the app ticks a second-accurate clock LOCALLY from the
        # timestamp the current period kicked off — no per-second polling. The WS
        # `period` is sometimes an int (1) and sometimes a code ("2T"); normalize
        # to a plain 1–4 so the client's Int? decode never breaks.
        out["period"]                = _ws_period_int(time_obj.get("period"))
        out["period_started_at_uts"] = time_obj.get("period_started_at_uts")
        out["injury_time"]           = time_obj.get("injury_time")
        out["second"]                = time_obj.get("second")
        # Per-team stats live under event_snap['stats'][side] (event_snap[side]
        # is only team identity), so read the stats block — not the team dict.
        ws_stats = event_snap.get("stats") or {}
        stats: dict = {}
        for side in ("home", "away"):
            ws_side = ws_stats.get(side) or {}
            if isinstance(ws_side, dict) and ws_side:
                stats[side] = _ws_side_to_stats(ws_side)
        if stats:
            out["stats"] = stats

    # Live momentum pressure ∈ [-1, +1] (+ = home pressing) — the same blended
    # dangerous-attack + situation-surge signal the Drama Engine scores on,
    # surfaced here for the live momentum bar.
    try:
        sig = drama_engine.live_signals_from_ws_event(event_snap)
        mp = drama_engine.blend_pressure(sig.get("momentum_pressure"),
                                         ws.get_attack_surge(event_id))
        if mp is not None:
            out["momentum_pressure"] = round(mp, 4)
    except Exception:
        pass

    if ld:
        coords = ld.get("coordinates") or []
        first  = coords[0] if coords else {}
        out["livedata"] = {
            "side":       ld.get("side"),
            "situation":  ld.get("situation"),
            "x":          first.get("x"),
            "y":          first.get("y"),
            "uts":        ld.get("uts"),
            "commentary": ld.get("commentary"),
        }

    if odds:
        out["odds"] = odds.get("odds") or odds
        # Live 1X2 win probability + favorite-flip swing, for the win-prob bar.
        try:
            wp = win_probability.win_prob_from_snapshot(odds)
            if wp:
                out["win_prob"] = [round(wp[0], 4), round(wp[1], 4), round(wp[2], 4)]
            sw = ws.get_winprob_swing(event_id)
            if sw is not None:
                out["win_prob_swing"] = round(sw, 4)
        except Exception:
            pass

    return out


# ---------------------------------------------------------------------------

# ── Incident scorer enrichment: full names + clickable ids ───────────────────
# BSD incidents (and lineups) carry only the SHORT name ("W. Semedo"); only the
# per-player detail has the full "Willy Semedo". Resolve + cache it so the goal /
# card timeline shows full names. 365 incidents already get the full name + the
# 365 athlete id (see _365_member_name / _365_athlete_id).

_bsd_pname_cache: dict[str, str] = {}


def _bsd_player_full_name(player_id) -> str | None:
    if player_id is None:
        return None
    key = str(player_id)
    if key in _bsd_pname_cache:
        return _bsd_pname_cache[key]
    try:
        d = bsd.player_detail(player_id) or {}
        name = d.get("name")
        if name:
            _bsd_pname_cache[key] = name
        return name
    except Exception:
        return None


def _enrich_bsd_incident_names(incidents: list[dict]) -> list[dict]:
    """Replace short incident names with full ones (cached + parallel fetch).
    Best-effort: a player that can't be resolved keeps the short name."""
    if not incidents:
        return incidents
    pending = {
        inc.get(k)
        for inc in incidents if isinstance(inc, dict)
        for k in ("player_id", "player_in_id", "player_out_id")
        if inc.get(k) is not None and str(inc.get(k)) not in _bsd_pname_cache
    }
    if pending:
        try:
            with _nullcontext(_DETAIL_POOL) as pool:
                list(pool.map(_bsd_player_full_name, pending))   # warms the cache
        except Exception:
            for pid in pending:
                _bsd_player_full_name(pid)
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        for id_key, name_key in (("player_id", "player"),
                                 ("player_in_id", "player_in"),
                                 ("player_out_id", "player_out")):
            pid = inc.get(id_key)
            if pid is not None:
                full = _bsd_pname_cache.get(str(pid))
                if full:
                    inc[name_key] = full
    return incidents


_365_ATHLETE_IMG = "https://imagecache.365scores.com/image/upload/Athletes/"


def s365_soccer_player(athlete_id) -> dict | None:
    """365scores soccer athlete bio for /soccer/player when the id isn't a BSD
    one (a 365-sourced match's scorer, e.g. friendlies). Bio + current club; 365
    exposes no season stats here, so those are null."""
    try:
        from score365.client import Client365
        with Client365() as c:
            data = c.get("/athletes/", params={"athletes": str(athlete_id)})
    except Exception as exc:
        logger.warning("s365 soccer player failed for %s: %s", athlete_id, exc)
        return None
    aths = (data or {}).get("athletes") or []
    if not aths:
        return None
    a = aths[0]
    comps = {str(c.get("id")): c.get("name") for c in (data.get("competitors") or [])}
    pos = a.get("position") or {}
    cid = str(a.get("clubId") or "")
    aid = a.get("id")
    return {
        "id": str(aid),
        "name": a.get("name") or "",
        "sport": "football",
        "team_name": comps.get(cid),
        "current_team_id": None, "club_crest": None,
        "national_team_id": None, "national_team_name": None, "national_team_crest": None,
        "position": pos.get("name") if isinstance(pos, dict) else None,
        "jersey": None,
        "country": a.get("nationalityName"),
        "photo": f"{_365_ATHLETE_IMG}{aid}",
        "season": {"apps": None, "goals": None, "assists": None, "avg_rating": None},
    }


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
                out["incidents"] = _enrich_bsd_incident_names(f_incidents.result())
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

    if source == "s365":
        from score365_soccer_adapter import (
            soccer_game_detail, soccer_game_stats, league_slug_for_competition_id,
        )
        try:
            raw = soccer_game_detail(int(native))
            if not raw:
                return None
            competition_id = raw.get("competitionId")
            slug = league_slug_for_competition_id(competition_id) if competition_id else None
            out = _from_365_game(raw, slug=slug)
            # Incidents (goals/cards/subs/woodwork) come inline in the 365 game
            # detail's `events` list — replay them for the running score.
            try:
                out["incidents"] = _normalize_365_incidents(raw)
            except Exception as exc:
                logger.warning("s365 incidents failed for %s: %s", canonical, exc)
                out["incidents"] = []
            # Shotmap + per-shot xG come inline in 365's `chartEvents` — this
            # powers the pitch shotmap, the xG race, per-team xG, and (via the
            # cumulative series) the retroactive drama timeline's xg_last_15.
            shot_xg: tuple[float, float] | None = None
            try:
                out["shotmap"] = _normalize_365_shotmap(raw)
                xg_per_minute, momentum, team_xg = _build_365_xg_timeline(out["shotmap"])
                out["xg_per_minute"] = xg_per_minute
                out["momentum"] = momentum
                if out["shotmap"]:
                    shot_xg = team_xg
                    out["home"]["xg"] = team_xg[0]
                    out["away"]["xg"] = team_xg[1]
            except Exception as exc:
                logger.warning("s365 shotmap failed for %s: %s", canonical, exc)
                out["shotmap"], out["xg_per_minute"], out["momentum"] = [], [], []
            # Team statistics live behind a separate 365 endpoint — normalize the
            # full per-team table so the iOS Statistics tab is as complete as a
            # BSD match instead of nearly empty. Best-effort.
            try:
                if raw.get("hasStats"):
                    raw_stats = soccer_game_stats(int(native))
                    hid = (raw.get("homeCompetitor") or {}).get("id")
                    aid = (raw.get("awayCompetitor") or {}).get("id")
                    norm = _normalize_365_statistics(raw_stats, hid, aid)
                    if norm:
                        out["stats"] = norm
                        # Prefer the shot-sum xG (matches the shotmap); fall back
                        # to the stats-table expected_goals only when no shots.
                        if shot_xg is None:
                            hxg = (norm.get("home") or {}).get("expected_goals")
                            axg = (norm.get("away") or {}).get("expected_goals")
                            if hxg is not None:
                                out["home"]["xg"] = hxg
                            if axg is not None:
                                out["away"]["xg"] = axg
            except Exception as exc:
                logger.warning("s365 stats failed for %s: %s", canonical, exc)
            return out
        except Exception as exc:
            logger.warning("s365 game_detail failed for %s: %s", canonical, exc)
            return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 365scores → canonical shape normalizers
#
# BSD's per-incident and per-lineup shapes are what iOS speaks. 365scores uses
# its own `events` / `members` / `lineups` structures, so we translate here at
# the API boundary so the iOS contract stays uniform regardless of which adapter
# produced the data.


def _365_members_index(game: dict) -> dict[int, dict]:
    """id → top-level member dict (the name/jersey source). Mirrors the
    basketball score365 member→athlete mapping (extract_box_score / extract_players)."""
    return {m.get("id"): m for m in (game.get("members") or []) if m.get("id") is not None}


def _365_member_name(members: dict[int, dict], pid: int | None) -> str | None:
    m = members.get(pid) if pid is not None else None
    if not m:
        return None
    # Prefer the FULL name ("Alejandro Romero") over the short form ("Romero")
    # for incident display.
    return m.get("name") or m.get("shortName")


def _365_athlete_id(members: dict[int, dict], pid: int | None):
    """The 365 ATHLETE id for a lineup member — the id /soccer/player and the
    box score / search use. The incident's `playerId` is a per-match MEMBER id,
    which won't resolve a player page; map it to the athleteId so the scorer is
    tappable. Falls back to the member id when no athleteId is present."""
    m = members.get(pid) if pid is not None else None
    if not m:
        return pid
    return m.get("athleteId") or pid


# 365 goal eventType.subTypeName → canonical goal type. Own goals must credit
# the OTHER side when replaying the running score.
_365_CARD_TYPE = {
    "yellow card": "yellow",
    "red card":    "red",
    "second yellow card": "yellow-red",
}


def _normalize_365_incidents(game: dict) -> list[dict]:
    """Translate a 365scores game-detail `events` list into the BSD/canonical
    incident shape iOS speaks (same fields `_from_bsd_event`/the BSD incident
    feed emit).

    Emits, sorted chronologically: goals (`type` 'goal'/'owngoal', with a
    replayed running `home_score`/`away_score`), cards (`type` 'card',
    `card_type` 'yellow'/'red'/'yellow-red'), and substitutions (`type`
    'substitution', `player_in`/`player_out` + ids). 365 marks the scoring
    player's side via `competitorId`; an own goal credits the opposing team.
    """
    members = _365_members_index(game)
    home_id = (game.get("homeCompetitor") or {}).get("id")
    away_id = (game.get("awayCompetitor") or {}).get("id")

    events = [e for e in (game.get("events") or []) if isinstance(e, dict)]
    # Chronological replay for the running score (gameTime + order as tiebreak).
    events.sort(key=lambda e: (
        e.get("gameTime") if isinstance(e.get("gameTime"), (int, float)) else 0.0,
        e.get("addedTime") or 0,
        e.get("order") or 0,
    ))

    out: list[dict] = []
    hs = aw = 0
    for e in events:
        et = e.get("eventType") or {}
        name = (et.get("name") or "").strip().lower()
        sub_name = (et.get("subTypeName") or "").strip().lower()
        comp_id = e.get("competitorId")
        is_home = (comp_id == home_id) if home_id is not None else None
        minute = int(e["gameTime"]) if isinstance(e.get("gameTime"), (int, float)) else None
        added = e.get("addedTime") or None
        pid = e.get("playerId")

        if et.get("id") == 1 or name == "goal":
            is_own = sub_name == "own goal"
            # Running score: regular goal credits the scorer's side; an own goal
            # credits the OPPOSING side.
            credit_home = (is_home and not is_own) or ((is_home is False) and is_own)
            if credit_home:
                hs += 1
            else:
                aw += 1
            norm: dict[str, Any] = {
                "type":       "owngoal" if is_own else "goal",
                "minute":     minute,
                "added_time": added,
                "is_home":    is_home,
                "player":     _365_member_name(members, pid),
                "player_id":  _365_athlete_id(members, pid),
                "assist":     None,
                "home_score": hs,
                "away_score": aw,
            }
            extras = e.get("extraPlayers") or []
            if extras and not is_own:
                norm["assist"] = _365_member_name(members, extras[0])
            out.append(norm)

        elif et.get("id") in (2, 3) or "card" in name:
            card_type = _365_CARD_TYPE.get(name)
            if card_type is None:
                card_type = "red" if "red" in name else "yellow"
            out.append({
                "type":       "card",
                "minute":     minute,
                "added_time": added,
                "is_home":    is_home,
                "card_type":  card_type,
                "player":     _365_member_name(members, pid),
                "player_id":  _365_athlete_id(members, pid),
            })

        elif et.get("id") == 1000 or name == "substitution":
            # 365: playerId is the player coming ON (was on the bench),
            # extraPlayers[0] is the player going OFF (was Starting).
            extras = e.get("extraPlayers") or []
            out_id = extras[0] if extras else None
            out.append({
                "type":          "substitution",
                "minute":        minute,
                "added_time":    added,
                "is_home":       is_home,
                "player_in":     _365_member_name(members, pid),
                "player_out":    _365_member_name(members, out_id),
                "player_in_id":  _365_athlete_id(members, pid),
                "player_out_id": _365_athlete_id(members, out_id),
            })
        # Woodwork and other minor event types are intentionally dropped — iOS
        # only renders goals, cards and subs in the timeline.

    return out


# 365 lineup statusText → canonical position bucket. iOS shows the raw position
# string, so we pass through 365's `position.name` (e.g. "Goalkeeper").
def _normalize_365_lineups(game: dict) -> dict | None:
    """Translate the 365scores game-detail lineups into the canonical
    `{event_id, lineup_status, beta, lineups:{home, away}}` shape iOS expects
    (the same shape the BSD lineups path produces).

    Each lineup member's `id` maps back to a top-level `members` entry for the
    name + jersey number; `ranking` is the per-player score (ai_score), and
    `statusText`/`status` splits starters from substitutes."""
    members = _365_members_index(game)

    def _side(comp: dict) -> dict:
        lineups = comp.get("lineups") or {}
        entries = lineups.get("members") or []
        starters: list[dict] = []
        subs: list[dict] = []
        for lm in entries:
            if not isinstance(lm, dict):
                continue
            status_text = (lm.get("statusText") or "").strip().lower()
            status_code = lm.get("status")
            is_starter = status_text == "starting" or status_code == 1
            is_sub = status_text in ("bench", "substitute") or status_code == 2
            # 365 lists coaching staff in the lineup too (statusText
            # "Management", status 4). Keep only actual players.
            if not is_starter and not is_sub:
                continue
            pid = lm.get("id")
            base = members.get(pid) or {}
            ranking = lm.get("ranking")
            try:
                ai_score = float(ranking) if ranking is not None else None
            except (TypeError, ValueError):
                ai_score = None
            position = (lm.get("position") or {}).get("name")
            norm = {
                "id":            pid or 0,
                "name":          base.get("name") or "",
                "short_name":    base.get("shortName"),
                "position":      position,
                "jersey_number": _to_int(base.get("jerseyNumber")),
                "ai_score":      ai_score,
                "rating":        ai_score,
            }
            (subs if is_sub else starters).append(norm)
        return {
            "team_id":     comp.get("id") or 0,
            "team_name":   comp.get("name") or "",
            "formation":   lineups.get("formation"),
            "confidence":  None,
            "players":     starters,
            "substitutes": subs,
        }

    home_comp = game.get("homeCompetitor") or {}
    away_comp = game.get("awayCompetitor") or {}
    home_lu = (home_comp.get("lineups") or {}).get("members") or []
    away_lu = (away_comp.get("lineups") or {}).get("members") or []
    if not home_lu and not away_lu:
        return None

    # 365 lineup `status`: "Confirmed" vs predicted/probable.
    status_raw = ((home_comp.get("lineups") or {}).get("status")
                  or (away_comp.get("lineups") or {}).get("status") or "")
    lineup_status = "confirmed" if str(status_raw).strip().lower() == "confirmed" else "predicted"

    return {
        "event_id":      game.get("id"),
        "lineup_status": lineup_status,
        "beta":          False,
        "lineups": {
            "home": _side(home_comp),
            "away": _side(away_comp),
        },
    }


def get_365_lineups(event_id: int) -> dict | None:
    """Confirmed/predicted lineups for a 365scores match (fetches the game
    detail and normalizes), in the canonical
    `{event_id, lineup_status, beta, lineups:{home,away}}` shape iOS expects."""
    from score365_soccer_adapter import soccer_game_detail
    try:
        game = soccer_game_detail(int(event_id))
    except Exception as exc:
        logger.warning("365 lineups failed for %s: %s", event_id, exc)
        return None
    return _normalize_365_lineups(game or {})


# 365 shot `outcome.name` → canonical shot type iOS speaks ('goal'/'miss'/'save'
# /'block'). 365 has no separate woodwork bucket in this enum → treat as a miss.
_365_SHOT_OUTCOME = {
    "goal": "goal", "own goal": "goal",
    "saved": "save", "blocked": "block",
    "missed": "miss", "post": "miss", "woodwork": "miss",
}


def _365_shot_minute(t: Any) -> int | None:
    """'6\\'' → 6, '90 + 1\\'' → 90, '45' → 45 (leading integer minute)."""
    if t is None:
        return None
    digits = ""
    for ch in str(t).strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _normalize_365_shotmap(game: dict) -> list[dict]:
    """Translate 365's `chartEvents.events` into the canonical shotmap shape iOS
    speaks (same fields the BSD shotmap emits): one entry per shot with per-shot
    xG / xGOT, outcome, body part, shooter, and a pitch `pos` {x,y} in 0–100.

    365 coords: `side` = % up the pitch toward the ATTACKING goal (100 = goal
    line), `line` = lateral position (50 = centre). iOS expects `pos.x` measured
    from the attacking goal (0 = goal line) — so x = 100 - side — and `pos.y` =
    lateral. iOS mirrors x per home/away to share one pitch."""
    ce = (game.get("chartEvents") or {}).get("events") or []
    home_num = 1  # 365 competitorNum: 1 = home, 2 = away
    out: list[dict] = []
    for s in ce:
        if not isinstance(s, dict):
            continue
        comp = s.get("competitorNum")
        is_home = (comp == home_num) if comp in (1, 2) else None
        outcome_name = ((s.get("outcome") or {}).get("name") or "").strip().lower()
        side = _stat_float(s.get("side"))
        line = _stat_float(s.get("line"))
        pos = None
        if side is not None and line is not None:
            pos = {"x": round(max(0.0, min(100.0, 100.0 - side)), 1),
                   "y": round(max(0.0, min(100.0, line)), 1)}
        out.append({
            "min":       _365_shot_minute(s.get("time")),
            "home":      is_home,
            "type":      _365_SHOT_OUTCOME.get(outcome_name, "miss"),
            "xg":        _stat_float(s.get("xg")),
            "xgot":      _stat_float(s.get("xgot")),
            "body":      s.get("bodyPart"),
            "sit":       None,                 # 365 doesn't expose shot situation
            "player_id": s.get("playerId"),
            "pos":       pos,
        })
    out.sort(key=lambda z: (z.get("min") if z.get("min") is not None else 0))
    return out


def _build_365_xg_timeline(shotmap: list[dict]) -> tuple[list[dict], list[dict], tuple[float, float]]:
    """From a normalized shotmap derive: (xg_per_minute cumulative series,
    momentum pressure series, (home_total_xg, away_total_xg)).

    - xg_per_minute: `[{m, cum_home, cum_away}]` (the shape the iOS xG-race reads).
    - momentum: `[{m, v}]`, v ∈ [-1,1] (+ = home) — a rolling 5-min xG-share
      pressure proxy, since 365 has no native momentum graph. Honest best-effort.
    - team xG: the cumulative sum per side (matches the shotmap exactly)."""
    per_min: dict[int, list[float]] = {}
    for s in shotmap:
        m = s.get("min")
        if m is None:
            continue
        xg = s.get("xg") or 0.0
        slot = per_min.setdefault(int(m), [0.0, 0.0])
        if s.get("home") is True:
            slot[0] += xg
        elif s.get("home") is False:
            slot[1] += xg

    xg_per_minute: list[dict] = []
    cum_h = cum_a = 0.0
    for m in sorted(per_min):
        cum_h += per_min[m][0]
        cum_a += per_min[m][1]
        xg_per_minute.append({"m": float(m), "cum_home": round(cum_h, 3), "cum_away": round(cum_a, 3)})

    momentum: list[dict] = []
    if per_min:
        last = max(per_min)
        for m in range(0, last + 1):
            wh = sum(per_min.get(k, (0.0, 0.0))[0] for k in range(max(0, m - 4), m + 1))
            wa = sum(per_min.get(k, (0.0, 0.0))[1] for k in range(max(0, m - 4), m + 1))
            tot = wh + wa
            momentum.append({"m": float(m), "v": round((wh - wa) / tot, 3) if tot > 0 else 0.0})

    return xg_per_minute, momentum, (round(cum_h, 2), round(cum_a, 2))


# Generic numeric stat-value parsers (used by _normalize_365_statistics). They
# handle the quirky 365 stat-value formats: '21', '149/178 (84%)' (numerator),
# '68%'.
def _stat_int(value: Any) -> int | None:
    """Parse a stat value to int. Handles '21', '467', '149/178 (84%)'
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


def _stat_pct(value: Any) -> float | None:
    """Parse '68%' → 68.0."""
    if value is None:
        return None
    s = str(value).replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _stat_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


# 365scores football stat NAME (English, lower-cased) → canonical TeamStats
# field + value kind. Mapped by name (stable in English) rather than numeric id,
# since the id set varies by league and some ids overlap across stat types.
_S365_STAT_NAME_MAP: dict[str, tuple[str, str]] = {
    "ball possession":          ("ball_possession",        "pct"),
    "possession":               ("ball_possession",        "pct"),
    "expected goals":           ("expected_goals",         "float"),
    "expected goals (xg)":      ("expected_goals",         "float"),
    "total shots":              ("total_shots",            "int"),
    "shots":                    ("total_shots",            "int"),
    "shots on target":          ("shots_on_target",        "int"),
    "shots on goal":            ("shots_on_target",        "int"),
    "blocked shots":            ("blocked_shots",          "int"),
    "big chances":              ("big_chances",            "int"),
    "big chances created":      ("big_chances",            "int"),
    "big chances missed":       ("big_chances_missed",     "int"),
    "big chances scored":       ("big_chances_scored",     "int"),
    "corners":                  ("corner_kicks",           "int"),
    "corner kicks":             ("corner_kicks",           "int"),
    "fouls":                    ("fouls",                  "int"),
    "offsides":                 ("offsides",               "int"),
    "yellow cards":             ("yellow_cards",           "int"),
    "red cards":                ("red_cards",              "int"),
    "accurate passes":          ("accurate_passes",        "int"),
    "passes completed":         ("accurate_passes",        "int"),
    "total passes":             ("total_passes",           "int"),
    "passes":                   ("total_passes",           "int"),
    "goalkeeper saves":         ("goalkeeper_saves",       "int"),
    "saves":                    ("goalkeeper_saves",       "int"),
    "touches in penalty area":  ("touches_in_penalty_area", "int"),
    "touches in opposition box": ("touches_in_penalty_area", "int"),
}


def _normalize_365_statistics(stats: list, home_id, away_id) -> dict | None:
    """Translate a 365scores `/game/stats/` statistics list into the canonical
    `{home, away}` TeamStats shape iOS expects. Each 365 item carries a
    `competitorId`, so we bucket per side. Returns None when nothing usable."""
    if not stats:
        return None
    home: dict[str, Any] = {}
    away: dict[str, Any] = {}
    # Captured for the total_shots fallback (inside + outside the box) when the
    # league doesn't expose a single "Total Shots" stat.
    box = {"home": [None, None], "away": [None, None]}  # [inside, outside]
    found = False
    hid, aid = str(home_id), str(away_id)
    for s in stats:
        if not isinstance(s, dict):
            continue
        cid = str(s.get("competitorId"))
        if cid == hid:
            side, sk = home, "home"
        elif cid == aid:
            side, sk = away, "away"
        else:
            continue
        name = str(s.get("name") or "").strip().lower()
        val = s.get("value")
        if name == "shots inside the box":
            box[sk][0] = _stat_int(val)
        elif name == "shots outside the box":
            box[sk][1] = _stat_int(val)
        mapping = _S365_STAT_NAME_MAP.get(name)
        if not mapping:
            continue
        field, kind = mapping
        parsed = (_stat_pct(val) if kind == "pct"
                  else _stat_float(val) if kind == "float"
                  else _stat_int(val))
        if parsed is not None:
            side[field] = parsed
            found = True
    # total_shots fallback = inside + outside, when not given directly.
    for sk, side in (("home", home), ("away", away)):
        if side.get("total_shots") is None and any(v is not None for v in box[sk]):
            side["total_shots"] = (box[sk][0] or 0) + (box[sk][1] or 0)
            found = True
    if not found:
        return None
    for side in (home, away):
        acc, tot = side.get("accurate_passes"), side.get("total_passes")
        if acc is not None and tot:
            side["pass_accuracy_pct"] = round(acc / tot * 100, 1)
    return {"home": home, "away": away}


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Team full-season matches. One call yields a team's whole season across all
# competitions. BSD for the leagues it covers (the app already holds BSD team
# ids there); 365scores for the 365-primary leagues (euros, ligat_haal) where
# there is no BSD id.


# Position display order for national-team squads (GK → DF → MF → FW).
_SQUAD_POS_ORDER = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}


def national_team_squad(team_id: int | str) -> dict:
    """Official World Championship squad for one national team, each player merged to
    the club team they currently play for.

    Shape: {team_id, count, players: [{player_id, name, jersey_number,
    position, club, club_country, current_team_id, club_crest, caps, goals,
    age}]}. The club id comes from bsd.player_detail(player_id).current_team_id
    — the same resolver the favorites national-team flow uses. A failed
    player_detail degrades that row to current_team_id=None (club shown as a
    plain name, never an error)."""
    rows = bsd.worldcup_squad(team_id) or []
    # A row with no player_id can't be merged to a club or opened as a player
    # page — and the iOS client decodes player_id as a non-optional Int, so a
    # null one would fail the whole squad. Drop them at the source.
    rows = [r for r in rows if r.get("player_id") is not None]
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

    # Exceptions are absorbed inside _enrich, so pool.map never raises here.
    with _nullcontext(_DETAIL_POOL) as pool:
        players = list(pool.map(_enrich, rows))

    players.sort(key=lambda p: (
        _SQUAD_POS_ORDER.get((p.get("position") or "").upper(), 9),
        p.get("jersey_number") if p.get("jersey_number") is not None else 999,
    ))
    return {"team_id": team_id, "count": len(players), "players": players}


def get_team_season(team_id: str, league_slug: str | None = None, name: str | None = None) -> list[dict]:
    source = PRIMARY_SOURCE.get(league_slug or "", "bsd")
    if source == "score365":
        return _s365_team_season(team_id, league_slug)
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


def _s365_team_season(team_id: str, league_slug: str | None) -> list[dict]:
    """A 365-primary team's season: the competition's rolling 365 window
    (current/results/fixtures), filtered to games this team plays in, returned
    as canonical s365 matches. 365 has no per-team season endpoint, so we filter
    the competition slate by competitor id."""
    from score365_soccer_adapter import (
        soccer_competition_games, COMPETITION_IDS as S365_IDS,
        league_slug_for_competition_id,
    )
    competition_id = S365_IDS.get(league_slug or "")
    if competition_id is None:
        return []
    try:
        tid = str(int(team_id))
    except (TypeError, ValueError):
        return []
    try:
        games = soccer_competition_games(competition_id)
    except Exception as exc:
        logger.warning("365 team season failed for %s/%s: %s", team_id, league_slug, exc)
        return []
    out: list[dict] = []
    for g in games:
        hid = str((g.get("homeCompetitor") or {}).get("id") or "")
        aid = str((g.get("awayCompetitor") or {}).get("id") or "")
        if tid not in (hid, aid):
            continue
        slug = league_slug or league_slug_for_competition_id(g.get("competitionId"))
        out.append(_from_365_game(g, slug=slug))
    return _finalize_team_matches(out)


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
