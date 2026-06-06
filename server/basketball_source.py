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
import team_names_he

logger = logging.getLogger(__name__)


def _attach_espn_team_he(team: dict | None) -> None:
    """Stamp Hebrew onto an ESPN-shape team dict in place. The full English
    name lives in `displayName` (the `name` field carries the short name), so
    resolve off `displayName` and emit both `displayName_he` and `name_he`
    (mirroring each English key). No-op on a lookup miss."""
    if not isinstance(team, dict):
        return
    he = team_names_he.hebrew_name(team.get("displayName") or team.get("name"))
    if he:
        team["displayName_he"] = he
        team["name_he"] = he


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
    """All basketball games in a league on a given date. Thin wrapper over
    `get_basketball_games_status` for callers that don't cache the result."""
    return get_basketball_games_status(league_slug, date_iso, tz)[0]


def get_basketball_games_status(
    league_slug: str,
    date_iso: str | None = None,
    tz: str | None = None,
) -> tuple[list[dict], bool]:
    """All basketball games in a league on a given date, plus a `trustworthy`
    flag (False when every source raised, so an empty result means "unknown",
    not "no games"). The feed uses the flag to avoid caching a transient
    upstream failure as a sticky "no games" — see `soccer_source` for the
    same rationale.

    `date_iso` is YYYY-MM-DD; defaults to today in `tz` (UTC if not given).
    `tz` is an IANA TZ used to anchor the calendar-day filter so the user's
    notion of "today" matches what's shown.
    """
    anchor_tz = _resolve_tz(tz)
    if date_iso is None:
        date_iso = datetime.now(anchor_tz).date().isoformat()

    games: list[dict] = []
    any_source_replied = False  # the source returned without raising

    # 365scores is the SOLE basketball source. BSD basketball was disconnected
    # (the bsd_basketball / bsd_websocket modules remain on disk but are no
    # longer wired into any basketball read path). Every league we surface —
    # nba, euroleague, eurocup, bcl, winner_league — is covered by 365, and 365
    # ids match the id space used by /search and stored favorites.
    try:
        # 365 buckets each game by its US-Eastern date, but the user's "today"
        # is in `anchor_tz` — a late-night game can sit one Eastern day off from
        # the local day (e.g. a 00:30 UTC tip-off is "yesterday" in Eastern but
        # "today" in Israel). Fetch a ±1 day Eastern window and let
        # `_enforce_date_and_dedupe` narrow back to the user's local calendar day.
        d = date.fromisoformat(date_iso)
        raw: list[dict] = []
        for off in (-1, 0, 1):
            ymd = (d + timedelta(days=off)).strftime("%Y%m%d")
            raw.extend(s365.fetch_games(ymd, league=league_slug))
        games = [_from_365_event(e, slug=league_slug) for e in raw]
        any_source_replied = True
        if games:
            logger.info("365scores basketball served %s/%s with %d games (window, pre-filter)",
                        league_slug, date_iso, len(games))
    except Exception as exc:
        logger.warning("365scores basketball failed for %s/%s: %s",
                       league_slug, date_iso, exc)

    games = _enforce_date_and_dedupe(games, date_iso, anchor_tz)
    # Drop cancelled / postponed events so they don't pollute the schedule.
    games = [
        g for g in games
        if g.get("status", {}).get("type", {}).get("detail") not in ("Cancelled", "Postponed")
    ]
    return games, any_source_replied


def get_basketball_event_detail(canonical: str) -> dict | None:
    """Full event payload (score header + quarter-by-quarter + prediction).
    BSD natively; 365scores (Winner League / BCL) built to the same shape."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        detail = bsd.event_detail(int(native))
    elif source == "s365":
        detail = _s365_event_detail(native)
    else:
        return None
    if isinstance(detail, dict):
        team_names_he.attach(detail.get("home_team"))
        team_names_he.attach(detail.get("away_team"))
    return detail


def get_basketball_team_stats(canonical: str) -> dict | None:
    """Comprehensive team stats grouped by category. BSD natively; for
    365scores we aggregate the box score into the same shape so the Team Stats
    tab is populated for Winner League / BCL too."""
    source, native = parse_canonical_id(canonical)
    if source == "bsd":
        stats = bsd.event_team_stats(int(native))
    elif source == "s365":
        stats = _s365_team_stats(native)
    else:
        return None
    if isinstance(stats, dict):
        team_names_he.attach(stats.get("home_team"))
        team_names_he.attach(stats.get("away_team"))
    return stats


def get_basketball_box_score(canonical: str) -> dict | None:
    """Per-player stats for both teams (BSD + 365scores).

    BSD has no player images, so we stamp a best-effort 365scores headshot
    onto each player (resolved by team-scoped name match). 365-sourced games
    use the athlete id directly. Nil photos are fine — iOS shows initials.
    """
    source, native = parse_canonical_id(canonical)
    if source == "s365":
        box = _s365_box_score(native)
    elif source == "bsd":
        box = bsd.event_box_score(int(native))
        if isinstance(box, dict):
            for team_key, box_key in (("home_team", "home_box"), ("away_team", "away_box")):
                team = box.get(team_key) or {}
                team_name = team.get("name") if isinstance(team, dict) else None
                for player in box.get(box_key) or []:
                    if isinstance(player, dict) and not player.get("photo"):
                        player["photo"] = athlete_365_photo_url(team_name, player.get("name"))
    else:
        return None
    if isinstance(box, dict):
        team_names_he.attach(box.get("home_team"))
        team_names_he.attach(box.get("away_team"))
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
    team = {
        "id": tid,
        "name": comp.get("name"),
        "short_name": comp.get("symbolicName") or comp.get("shortName"),
        "country_code": None,
        "logo": s365._365_team_crest_url(tid) if tid else None,
    }
    team_names_he.attach(team)
    return team


def _s365_status_to_bsd(game: dict) -> str:
    """Normalize a 365scores game status to BSD basketball's vocabulary
    ('notstarted'/'inprogress'/'finished'/'cancelled'/'postponed') so the
    summary's `detail.status` is identical across sources. iOS classifies
    live/final off this string; before, s365 emitted display text ("Q3",
    "Final") that the live/final checks (and the status label) couldn't read.
    365's `statusGroup` is the structured signal: 2=pre, 3=live, 4=post."""
    txt = (game.get("statusText") or game.get("shortStatusText") or "").lower()
    if "cancel" in txt:
        return "cancelled"
    if "postpon" in txt:
        return "postponed"
    grp = game.get("statusGroup")
    if grp == 2:
        return "notstarted"
    if grp == 3:
        return "inprogress"
    if grp == 4:
        return "finished"
    return game.get("statusText") or game.get("shortStatusText") or ""


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
        "status":      _s365_status_to_bsd(game),
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
            # 365 returns only the CURRENT stage by default — for split leagues
            # (e.g. the Israeli Winner League's relegation/championship playoffs at
            # season's end) that's a 4-team playoff group, not the league table. So
            # explicitly request the regular-season stage (stageNum=1) of the
            # current season, which holds the full table; fall back to the default
            # current stage if that lookup fails.
            season_num = None
            try:
                comp_data = client.get("/competitions/", params={"competitions": str(comp_id)})
                season_num = ((comp_data or {}).get("competitions") or [{}])[0].get("currentSeasonNum")
            except Exception:
                pass
            data = None
            if season_num is not None:
                try:
                    full = client.get("/standings/", params={
                        "competitions": str(comp_id),
                        "seasonNum": str(season_num),
                        "stageNum": "1",
                    })
                    if ((full or {}).get("standings") or [{}])[0].get("rows"):
                        data = full
                except Exception:
                    pass
            if data is None:
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
        team_obj = {"id": comp.get("id") or 0,
                    "name": comp.get("name") or "",
                    "short_name": comp.get("symbolicName"),
                    "logo": (f"{_365_LOGO_BASE}{comp.get('id')}" if comp.get("id") else "")}
        team_names_he.attach(team_obj)
        rows_out.append({
            "team": team_obj,
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


# 365scores basketball team-stat NAME → totals key. The official /game/stats/
# endpoint carries AUTHORITATIVE team totals (incl. team rebounds not credited
# to any player), and is populated even while the box-score lineups still lag —
# so the Team Stats tab isn't empty mid-game or just after the final whistle.
_S365_BB_STAT_NAME_MAP = {
    "field goals":     "fg",   "3 pointers":     "tp",  "three pointers": "tp",
    "free throws":     "ft",
    "rebounds":        "reb",  "off. rebounds":  "orb", "offensive rebounds": "orb",
    "def. rebounds":   "drb",  "defensive rebounds": "drb",
    "assists":         "ast",  "steals":         "stl", "blocks": "blk",
    "turnovers":       "to",   "personal fouls": "pf",  "fouls": "pf",
}


def _s365_team_totals_direct(native: str, game: dict) -> tuple[dict, dict] | None:
    """Authoritative team totals from 365's /game/stats/ endpoint, in the same
    {pts,reb,orb,...,fgm,fga,...} shape the box-score aggregation produces.
    Points come from the game header (the endpoint omits them). Returns None
    when the endpoint has no usable stats so the caller can fall back."""
    try:
        from score365.client import Client365
        with Client365() as c:
            data = c.get("/game/stats/", params={"games": str(native)})
    except Exception as exc:
        logger.warning("s365 /game/stats/ failed for %s: %s", native, exc)
        return None
    stats = (data or {}).get("statistics") or []
    if not stats:
        return None
    hid = str((game.get("homeCompetitor") or {}).get("id"))
    aid = str((game.get("awayCompetitor") or {}).get("id"))
    keys = ("pts", "reb", "orb", "drb", "ast", "stl", "blk", "to", "pf",
            "fgm", "fga", "tpm", "tpa", "ftm", "fta")
    h = {k: 0 for k in keys}
    a = {k: 0 for k in keys}
    found = False
    for s in stats:
        cid = str(s.get("competitorId"))
        side = h if cid == hid else (a if cid == aid else None)
        if side is None:
            continue
        field = _S365_BB_STAT_NAME_MAP.get(str(s.get("name") or "").strip().lower())
        if not field:
            continue
        raw = str(s.get("value") or "")
        if field in ("fg", "tp", "ft"):                 # "33/56 (58.9%)"
            made, att = _s365_made_att(raw.split("(", 1)[0].strip())
            if made is not None:
                pre = {"fg": "fg", "tp": "tp", "ft": "ft"}[field]
                side[f"{pre}m"] = made
                side[f"{pre}a"] = att or 0
                found = True
        else:
            v = _s365_int(raw)
            if v is not None:
                side[field] = v
                found = True
    if not found:
        return None
    # Points aren't in /game/stats/ — take them from the score header.
    h["pts"] = _s365_int((game.get("homeCompetitor") or {}).get("score")) or 0
    a["pts"] = _s365_int((game.get("awayCompetitor") or {}).get("score")) or 0
    return h, a


def _s365_team_stats(native: str) -> dict | None:
    """Comprehensive 365scores team stats, shaped like BSD's team-stats payload.
    Prefers the authoritative /game/stats/ endpoint; falls back to aggregating
    the box score when that endpoint is empty. Nested group/item keys are
    camelCase to match the iOS contract (the BSD source emits them that way)."""
    game = _s365_game_detail(native)
    if not game:
        return None

    totals = _s365_team_totals_direct(native, game)
    if totals is not None:
        h, a = totals
    else:
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


def _filter_completed_series_games(games: list[dict], league_id: int, date_iso: str) -> list[dict]:
    """Drop notstarted games whose playoff series is already decided.

    Fetches finished games from the past 14 days for this league and counts
    wins per team pair. If either team has reached 4 wins the upcoming game
    is a phantom (series over) and is removed — even before BSD marks it
    'cancelled'. The 60-second BSD events_list cache keeps this cheap.
    """
    pre_games = [g for g in games if g.get("status", {}).get("type", {}).get("state") == "pre"]
    if not pre_games:
        return games

    try:
        from datetime import date as _date, timedelta
        d = _date.fromisoformat(date_iso)
        past_from = (d - timedelta(days=14)).isoformat()
        past_events = bsd.league_events(
            league_id,
            date_from=past_from,
            date_to=date_iso,
            status="finished",
            limit=200,
        ) or []
    except Exception as exc:
        logger.warning("series-check: could not fetch past games for league %s: %s", league_id, exc)
        return games

    # Count wins per team pair over the past 14 days
    series_wins: dict[frozenset, dict[str, int]] = {}
    for ev in past_events:
        home = ev.get("home_team") or {}
        away = ev.get("away_team") or {}
        hid = str(home.get("id") or "")
        aid = str(away.get("id") or "")
        if not hid or not aid:
            continue
        hs = ev.get("home_score") or 0
        as_ = ev.get("away_score") or 0
        if hs == as_:
            continue
        winner = hid if hs > as_ else aid
        pair: frozenset = frozenset({hid, aid})
        series_wins.setdefault(pair, {})
        series_wins[pair][winner] = series_wins[pair].get(winner, 0) + 1

    def _is_phantom(g: dict) -> bool:
        if g.get("status", {}).get("type", {}).get("state") != "pre":
            return False
        comps = (g.get("competitions") or [{}])[0]
        ids = {
            c.get("team", {}).get("id", "")
            for c in (comps.get("competitors") or [])
        }
        ids.discard("")
        if len(ids) != 2:
            return False
        wins = series_wins.get(frozenset(ids), {})
        return any(w >= 4 for w in wins.values())

    filtered = [g for g in games if not _is_phantom(g)]
    n_dropped = len(games) - len(filtered)
    if n_dropped:
        logger.info("series-check: dropped %d phantom game(s) for league_id=%s on %s",
                    n_dropped, league_id, date_iso)
    return filtered


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


def _abbr_from_365_roster(league_slug: str, team_name: str | None) -> str | None:
    """Look up the 365scores-normalized abbreviation for a BSD team name.

    Uses the 365 team-index (name → 365_team_id, same index built for logo
    enrichment) and then reads the abbreviation out of the in-memory roster
    cache so the abbreviation in the BSD game response always matches what
    the mobile roster picker sees.
    """
    if not team_name:
        return None
    index = _get_365_team_index(league_slug)  # normalized_name → 365_team_id (int)
    if not index:
        return None
    target = _normalize_team_name(team_name)
    tid: int | None = index.get(target)
    if tid is None:
        # fuzzy prefix match (handles city/suffix differences between sources)
        best_len = 0
        for key, kid in index.items():
            if (key.startswith(target) or target.startswith(key)) and len(key) > best_len:
                tid = kid
                best_len = len(key)
    if not tid:
        return None
    try:
        from monitor import _ROSTER_CACHE, _CACHE_LOCK  # late import to avoid circular
        with _CACHE_LOCK:
            entry = _ROSTER_CACHE.get(str(tid))
        return entry.get("abbreviation") if entry else None
    except Exception:
        return None


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

    # Resolve abbreviations through the 365scores roster so they always
    # match what the mobile player picker finds in /all-teams-and-players.
    # BSD short_name may differ from 365's normalized abbreviation (e.g. BSD
    # returns "GSW" while 365 roster has "GS"). Fall back gracefully.
    home_abbr = (
        _abbr_from_365_roster(slug, home_team.get("name"))
        or home_team.get("short_name")
        or (home_team.get("name") or "")[:3].upper()
    )
    away_abbr = (
        _abbr_from_365_roster(slug, away_team.get("name"))
        or away_team.get("short_name")
        or (away_team.get("name") or "")[:3].upper()
    )

    home_competitor = {
        "homeAway": "home",
        "score": str(event.get("home_score") or 0),
        "team": {
            "id": str(home_team.get("id") or ""),
            "abbreviation": home_abbr,
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
            "abbreviation": away_abbr,
            "displayName": away_team.get("name", ""),
            "name": away_team.get("short_name", "") or away_team.get("name", ""),
            "logo": _basketball_logo_url(slug, away_team.get("name"), away_team.get("id")),
        },
    }
    _attach_espn_team_he(home_competitor["team"])
    _attach_espn_team_he(away_competitor["team"])

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


# ---------------------------------------------------------------------------
# 365scores basketball PLAYER endpoints (search + bio).
#
# 365's public web API exposes athlete search (/search/) and athlete bio
# (/athletes/) but NOT a per-player game log or season-average endpoint. So the
# player screen shows bio (+ current team) while the recent-games list is empty
# and season averages are omitted — the accepted tradeoff of moving basketball
# off BSD (which did expose those) onto 365. Athlete ids here are the SAME 365
# ids the box score emits, so a tapped box-score / search row resolves cleanly.


def s365_player_search(query: str, limit: int = 10) -> list[dict]:
    """365scores basketball athlete search → rows shaped for search_index:
    {id, name, team_id, team_name, photo}."""
    try:
        from score365.client import Client365
        with Client365() as client:
            data = client.get("/search/", params={"query": query, "sports": "2"})
    except Exception as exc:
        logger.warning("s365 player search failed for %r: %s", query, exc)
        return []
    comps = {str(c.get("id")): c.get("name") for c in (data.get("competitors") or [])}
    out: list[dict] = []
    for a in data.get("athletes") or []:
        if a.get("sportId") != 2:          # basketball only
            continue
        cid = str(a.get("clubId") or "")
        aid = a.get("id")
        out.append({
            "id": str(aid or ""),
            "name": a.get("name") or "",
            "team_id": cid or None,
            "team_name": comps.get(cid),
            "photo": f"{_365_ATHLETE_BASE}{aid}" if aid else None,
        })
        if len(out) >= limit:
            break
    return out


def s365_player_detail(athlete_id: str | int) -> dict | None:
    """365scores athlete bio, shaped for `player_source.normalize_basketball_detail`.
    `season_avg` is empty (365 exposes no per-player averages). Returns None for
    an unknown athlete."""
    try:
        from score365.client import Client365
        with Client365() as client:
            data = client.get("/athletes/", params={"athletes": str(athlete_id)})
    except Exception as exc:
        logger.warning("s365 player detail failed for %s: %s", athlete_id, exc)
        return None
    aths = data.get("athletes") or []
    if not aths:
        return None
    a = aths[0]
    comps = {str(c.get("id")): c.get("name") for c in (data.get("competitors") or [])}
    pos = a.get("position") or {}
    cid = str(a.get("clubId") or "")
    aid = a.get("id")
    return {
        "id": aid,
        "name": a.get("name") or "",
        "team": {"name": comps.get(cid)},
        "position": pos.get("name") if isinstance(pos, dict) else None,
        "jersey_number": a.get("jerseyNumber"),
        "height_cm": None,
        "country": a.get("nationalityName"),
        "season_avg": {},
        # 365 athlete headshot; the endpoint prefers this over the default URL.
        "photo_365": f"{_365_ATHLETE_BASE}{aid}" if aid else None,
    }


def _bsd_status_to_espn(status: str) -> tuple[str, str, int]:
    """BSD basketball status → ESPN's (state, detail, period) tuple.

    The live BSD basketball API emits: 'scheduled' | 'live' | 'finished' |
    'postponed' | 'cancelled'. (The legacy 'notstarted'/'inprogress' spellings
    are still honored defensively — `_s365_status_to_bsd` produces them for the
    365scores summary path.) Mapping 'live' is what keeps an in-progress game
    from showing in the schedule feed as "Scheduled" with a frozen score.
    """
    if status == "finished":
        return "post", "Final", 4
    if status in ("live", "inprogress"):
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
    for comp in (event.get("competitions") or []):
        for c in (comp.get("competitors") or []):
            _attach_espn_team_he(c.get("team"))
    return event
