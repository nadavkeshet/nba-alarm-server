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
    # Drop cancelled / postponed events — series games that won't be played
    # show up as "pre" otherwise, which pollutes the schedule with phantom games.
    games = [
        g for g in games
        if g.get("status", {}).get("type", {}).get("detail") not in ("Cancelled", "Postponed")
    ]
    return games


def get_basketball_event_detail(canonical: str) -> dict | None:
    """Full event payload (score header + quarter-by-quarter + prediction).
    BSD natively; 365scores (Winner League / BCL) built to the same shape."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        return bsd.event_detail(int(native))
    if source == "s365":
        return _s365_event_detail(native)
    return None


def get_basketball_team_stats(canonical: str) -> dict | None:
    """Comprehensive team stats grouped by category. BSD natively; for
    365scores we aggregate the box score into the same shape so the Team Stats
    tab is populated for Winner League / BCL too."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        return bsd.event_team_stats(int(native))
    if source == "s365":
        return _s365_team_stats(native)
    return None


def get_basketball_box_score(canonical: str) -> dict | None:
    """Per-player stats for both teams (BSD + 365scores).

    BSD has no player images, so we stamp a best-effort 365scores headshot
    onto each player (resolved by team-scoped name match). 365-sourced games
    use the athlete id directly. Nil photos are fine — iOS shows initials.
    """
    source, native = parse_canonical_id(canonical)
    if source == "s365":
        return _s365_box_score(native)
    if source != "bsd":
        return None
    box = bsd.event_box_score(int(native))
    if not isinstance(box, dict):
        return box
    for team_key, box_key in (("home_team", "home_box"), ("away_team", "away_box")):
        team = box.get(team_key) or {}
        team_name = team.get("name") if isinstance(team, dict) else None
        for player in box.get(box_key) or []:
            if isinstance(player, dict) and not player.get("photo"):
                player["photo"] = athlete_365_photo_url(team_name, player.get("name"))
    return box


# ---------------------------------------------------------------------------
# 365scores basketball detail + box score (Winner League, BCL — leagues BSD
# doesn't cover). Built to the same shape as the BSD endpoints so the iOS
# BasketballSummaryView renders them 1:1.

_s365_game_cache: dict[str, tuple[float, dict]] = {}
_s365_game_lock = threading.Lock()
_S365_GAME_TTL = 30.0


def _s365_game_detail(native: str) -> dict | None:
    """Fetch (and briefly cache) a raw 365scores game detail so the /summary
    fan-out (detail + box score) shares a single upstream call."""
    with _s365_game_lock:
        hit = _s365_game_cache.get(native)
        if hit and time.time() - hit[0] < _S365_GAME_TTL:
            return hit[1]
    try:
        from score365.client import Client365
        from score365.scraper import get_game_detail
        with Client365() as client:
            game = get_game_detail(client, int(native))
    except Exception as exc:
        logger.warning("s365 game detail failed for %s: %s", native, exc)
        return None
    if game:
        with _s365_game_lock:
            _s365_game_cache[native] = (time.time(), game)
    return game


def _s365_made_att(value: Any) -> tuple[int | None, int | None]:
    if not value or "/" not in str(value):
        return None, None
    a, b = str(value).split("/", 1)
    try:
        return int(a), int(b)
    except ValueError:
        return None, None


def _s365_minutes(value: Any) -> float | None:
    """'21:22' → 21.37 minutes."""
    if not value:
        return None
    s = str(value)
    if ":" in s:
        m, sec = s.split(":", 1)
        try:
            return round(int(m) + int(sec) / 60.0, 2)
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _s365_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _s365_team_obj(comp: dict) -> dict:
    tid = comp.get("id")
    return {
        "id": tid,
        "name": comp.get("name"),
        "short_name": comp.get("symbolicName") or comp.get("shortName"),
        "country_code": None,
        "logo": s365._365_team_crest_url(tid) if tid else None,
    }


def _s365_event_detail(native: str) -> dict | None:
    game = _s365_game_detail(native)
    if not game:
        return None
    home = game.get("homeCompetitor", {}) or {}
    away = game.get("awayCompetitor", {}) or {}
    # `stages` carries the quarters (Q1..Q4, plus OT) and a trailing aggregate
    # stage named "Current Score"/"Score" — keep only the real periods.
    periods: dict[str, list[int]] = {}
    period_idx = 0
    for st in game.get("stages", []) or []:
        label = (st.get("shortName") or st.get("name") or "").strip().upper()
        if not (label.startswith("Q") or label.startswith("OT") or label.startswith("P")):
            continue
        h = _s365_int(st.get("homeCompetitorScore"))
        a = _s365_int(st.get("awayCompetitorScore"))
        if h is None and a is None:
            continue
        period_idx += 1
        periods[f"period{period_idx}"] = [h or 0, a or 0]
    return {
        "id":          _s365_int(game.get("id")),
        "league":      {"id": game.get("competitionId"),
                        "name": game.get("competitionDisplayName"),
                        "country": None},
        "home_team":   _s365_team_obj(home),
        "away_team":   _s365_team_obj(away),
        "event_date":  game.get("startTime"),
        "status":      game.get("statusText") or game.get("shortStatusText") or "",
        "home_score":  _s365_int(home.get("score")),
        "away_score":  _s365_int(away.get("score")),
        "round_number": game.get("roundNum"),
        "periods_score": periods or None,
        "prediction":  None,
    }


def _s365_box_player(p: dict, athlete_id: str | None) -> dict:
    stats = p.get("stats", {}) or {}
    fgm, fga = _s365_made_att(stats.get("FG"))
    tpm, tpa = _s365_made_att(stats.get("3PT"))
    ftm, fta = _s365_made_att(stats.get("FT"))
    pid = athlete_id or p.get("id")
    return {
        "player_id":             _s365_int(pid),
        "name":                  p.get("name"),
        "photo":                 f"{_365_ATHLETE_BASE}{pid}" if pid else None,
        "position":              p.get("position") or None,
        "jersey_number":         None,
        "is_starter":            (p.get("status") or "").lower() == "starting",
        "minutes":               _s365_minutes(stats.get("Min")),
        "points":                _s365_int(stats.get("Pts")),
        "rebounds":              _s365_int(stats.get("Reb")),
        "assists":               _s365_int(stats.get("Ast")),
        "offensive_rebounds":    _s365_int(stats.get("OR")),
        "defensive_rebounds":    _s365_int(stats.get("DR")),
        "steals":                _s365_int(stats.get("Stl")),
        "blocks":                _s365_int(stats.get("Blk")),
        "turnovers":             _s365_int(stats.get("TO")),
        "personal_fouls":        _s365_int(stats.get("PF")),
        "field_goals_made":      fgm,
        "field_goals_attempted": fga,
        "three_pointers_made":      tpm,
        "three_pointers_attempted": tpa,
        "free_throws_made":      ftm,
        "free_throws_attempted": fta,
        "field_goal_pct":   round(fgm / fga, 3) if fgm is not None and fga else None,
        "three_point_pct":  round(tpm / tpa, 3) if tpm is not None and tpa else None,
        "plus_minus":            _s365_int(stats.get("+/-")),
    }


def _s365_box_score(native: str) -> dict | None:
    game = _s365_game_detail(native)
    if not game:
        return None
    try:
        from score365.scraper import extract_box_score
        box = extract_box_score(game)
    except Exception as exc:
        logger.warning("s365 box score failed for %s: %s", native, exc)
        return None
    member_to_athlete: dict = {}
    for m in game.get("members", []) or []:
        mid = m.get("id")
        if mid is not None:
            member_to_athlete[mid] = m.get("athleteId") or mid

    def side_box(side_key: str) -> list[dict]:
        out = []
        for p in box.get(side_key, {}).get("players", []) or []:
            if not p.get("stats"):
                continue
            aid = member_to_athlete.get(p.get("id"))
            out.append(_s365_box_player(p, str(aid) if aid is not None else None))
        return out

    return {
        "event_id":  _s365_int(game.get("id")),
        "home_team": _s365_team_obj(game.get("homeCompetitor", {}) or {}),
        "away_team": _s365_team_obj(game.get("awayCompetitor", {}) or {}),
        "home_box":  side_box("home"),
        "away_box":  side_box("away"),
    }


def get_s365_basketball_standings(slug: str) -> dict | None:
    """365scores standings for basketball leagues BSD doesn't cover (Winner
    League, BCL, etc.) — shaped to match the iOS BasketballStandings model."""
    comp_id = s365.COMPETITION_IDS.get(slug)
    if not comp_id:
        return None
    try:
        from score365.client import Client365
        with Client365() as client:
            data = client.get("/standings/", params={"competitions": str(comp_id)})
    except Exception as exc:
        logger.warning("s365 basketball standings failed for %s: %s", slug, exc)
        return None
    stages = (data or {}).get("standings") or []
    if not stages:
        return None
    stage = stages[0]
    rows_out: list[dict] = []
    for i, row in enumerate(stage.get("rows", []) or [], start=1):
        comp = row.get("competitor") or {}
        played = _s365_int(row.get("gamePlayed"))
        wins   = _s365_int(row.get("gamesWon"))
        losses = _s365_int(row.get("gamesLost"))
        try:
            pct = float(row.get("pct")) if row.get("pct") is not None else (
                round(wins / played, 3) if played else None
            )
        except (TypeError, ValueError):
            pct = None
        rows_out.append({
            "team": {"id": comp.get("id") or 0,
                     "name": comp.get("name") or "",
                     "short_name": comp.get("symbolicName")},
            "position":       _s365_int(row.get("position")) or i,
            "matches":        played or 0,
            "wins":           wins or 0,
            "losses":         losses or 0,
            "percentage":     pct,
            "scores_for":     _s365_int(row.get("for")),
            "scores_against": _s365_int(row.get("against")),
        })
    return {
        "league":    {"id": comp_id, "name": stage.get("displayName") or slug.upper().replace("_", " ")},
        "season":    {"id": stage.get("seasonNum") or 0, "name": ""},
        "standings": rows_out,
    }


def _s365_team_stats(native: str) -> dict | None:
    """Aggregate the 365 box score into team totals, shaped like BSD's
    team-stats payload. Nested group/item keys are camelCase to match the
    iOS contract (the BSD source emits them that way)."""
    game = _s365_game_detail(native)
    if not game:
        return None
    try:
        from score365.scraper import extract_box_score
        box = extract_box_score(game)
    except Exception as exc:
        logger.warning("s365 team stats failed for %s: %s", native, exc)
        return None

    def agg(side_key: str) -> dict:
        t = {k: 0 for k in ("pts", "reb", "orb", "drb", "ast", "stl", "blk", "to", "pf",
                            "fgm", "fga", "tpm", "tpa", "ftm", "fta")}
        for p in box.get(side_key, {}).get("players", []) or []:
            s = p.get("stats", {}) or {}
            if not s:
                continue
            t["pts"] += _s365_int(s.get("Pts")) or 0
            t["reb"] += _s365_int(s.get("Reb")) or 0
            t["orb"] += _s365_int(s.get("OR")) or 0
            t["drb"] += _s365_int(s.get("DR")) or 0
            t["ast"] += _s365_int(s.get("Ast")) or 0
            t["stl"] += _s365_int(s.get("Stl")) or 0
            t["blk"] += _s365_int(s.get("Blk")) or 0
            t["to"]  += _s365_int(s.get("TO")) or 0
            t["pf"]  += _s365_int(s.get("PF")) or 0
            fgm, fga = _s365_made_att(s.get("FG")); t["fgm"] += fgm or 0; t["fga"] += fga or 0
            tpm, tpa = _s365_made_att(s.get("3PT")); t["tpm"] += tpm or 0; t["tpa"] += tpa or 0
            ftm, fta = _s365_made_att(s.get("FT")); t["ftm"] += ftm or 0; t["fta"] += fta or 0
        return t

    h, a = agg("home"), agg("away")

    def _cmp(hv: float, av: float, positive: bool) -> int:
        if hv == av:
            return 0
        better_home = hv > av if positive else hv < av
        return 1 if better_home else 2

    def item(key: str, name: str, hv: int, av: int, positive: bool = True) -> dict:
        return {"key": key, "name": name, "home": str(hv), "away": str(av),
                "homeValue": float(hv), "awayValue": float(av),
                "statisticsType": "positive" if positive else "negative",
                "compareCode": _cmp(hv, av, positive)}

    def pct_item(key: str, name: str, hm: int, ha: int, am: int, aa: int) -> dict:
        hp = round(hm / ha * 100, 1) if ha else 0.0
        ap = round(am / aa * 100, 1) if aa else 0.0
        return {"key": key, "name": name,
                "home": f"{hm}/{ha} ({hp:g}%)", "away": f"{am}/{aa} ({ap:g}%)",
                "homeValue": hp, "awayValue": ap,
                "statisticsType": "positive", "compareCode": _cmp(hp, ap, True)}

    items = [
        item("points", "Points", h["pts"], a["pts"]),
        pct_item("fieldGoals", "Field Goals", h["fgm"], h["fga"], a["fgm"], a["fga"]),
        pct_item("threePointers", "3-Pointers", h["tpm"], h["tpa"], a["tpm"], a["tpa"]),
        pct_item("freeThrows", "Free Throws", h["ftm"], h["fta"], a["ftm"], a["fta"]),
        item("rebounds", "Rebounds", h["reb"], a["reb"]),
        item("offRebounds", "Offensive Rebounds", h["orb"], a["orb"]),
        item("defRebounds", "Defensive Rebounds", h["drb"], a["drb"]),
        item("assists", "Assists", h["ast"], a["ast"]),
        item("steals", "Steals", h["stl"], a["stl"]),
        item("blocks", "Blocks", h["blk"], a["blk"]),
        item("turnovers", "Turnovers", h["to"], a["to"], positive=False),
        item("fouls", "Personal Fouls", h["pf"], a["pf"], positive=False),
    ]
    return {
        "event_id":  _s365_int(game.get("id")),
        "home_team": _s365_team_obj(game.get("homeCompetitor", {}) or {}),
        "away_team": _s365_team_obj(game.get("awayCompetitor", {}) or {}),
        "statistics": [{"groups": [{"groupName": "Team Stats", "statisticsItems": items}]}],
    }


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
    """Best-effort canonicalization so BSD ("Olympiacos BC") and 365
    ("Olympiacos") line up. Lowercases, strips diacritics + punctuation,
    then peels off common club-name suffixes/prefixes that differ across
    sources."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    for ch in ".'’`,":
        s = s.replace(ch, "")
    # Trailing club designators
    for suf in (" bc", " bsa", " baloncesto", " basket", " basketball", " olimpija"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    # Leading club designators ("BC Dubai" → "dubai")
    for pre in ("bc ", "kk ", "fc ", "cb ", "as "):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.strip()


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
    target = _normalize_team_name(team_name)
    tid = index.get(target)
    if tid is None:
        # Fuzzy fallback for city/region suffixes that differ between
        # sources, e.g. BSD "Anadolu Efes Istanbul" vs 365 "Anadolu Efes",
        # or BSD "AEK" vs 365 "AEK Athens". Pick the longest index key
        # that's a prefix of the target (or vice versa) — favors the
        # most specific match.
        best_len = 0
        for key, kid in index.items():
            if (key.startswith(target) or target.startswith(key)) and len(key) > best_len:
                tid = kid
                best_len = len(key)
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


_365_ATHLETE_BASE = "https://imagecache.365scores.com/image/upload/Athletes/"


def athlete_365_photo_url(team_name: str | None, player_name: str | None) -> str | None:
    """Best-effort 365scores headshot for a basketball player.

    BSD has no player photos, so we map a BSD player → 365 athlete id by
    name. Matching is **team-scoped**: we only look inside the roster of the
    365 team whose name matches `team_name`, so common names (five "James")
    can't resolve to the wrong face. Uses the in-memory 365 roster cache
    (`monitor.get_all_teams`) — no extra network. Returns None when there's
    no confident match (caller keeps the BSD URL / iOS shows initials).
    """
    if not player_name:
        return None
    try:
        from monitor import get_all_teams as _teams
        teams = _teams() or []
    except Exception:
        return None
    target_team = _normalize_team_name(team_name or "")
    target_player = _normalize_team_name(player_name)
    for t in teams:
        if target_team and _normalize_team_name(t.get("name") or "") != target_team:
            continue
        for p in (t.get("players") or []):
            if _normalize_team_name(p.get("name") or "") == target_player:
                pid = p.get("id") or p.get("player_id")
                if pid:
                    return f"{_365_ATHLETE_BASE}{pid}"
    return None


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
