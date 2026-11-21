"""Soccer-side adapter over the existing 365scores client.

Mirrors the basketball functions in `score365_adapter.py` but for football
(`sport_id=1`). Loads the league IDs from `config/competition_map.json` so the
codebase has a single source of truth.

The shape of dicts returned by these functions matches what 365scores returns;
normalization to internal models happens in `monitor.py` / `main.py` consumers.
"""

import json
import threading
import time
from datetime import date
from pathlib import Path

from score365.client import Client365
from score365.scraper import (
    FOOTBALL_SPORT_ID,
    get_games_by_date,
    get_game_detail,
    get_all_competitions,
)
import team_names_he

# ---------------------------------------------------------------------------
# Competition map (loaded once at import time)

_MAP_PATH = Path(__file__).resolve().parent / "config" / "competition_map.json"


def _load_map() -> dict[str, dict]:
    if not _MAP_PATH.exists():
        return {}
    return json.loads(_MAP_PATH.read_text(encoding="utf-8"))


_COMPETITION_MAP: dict[str, dict] = _load_map()

# slug → 365scores competition id
COMPETITION_IDS: dict[str, int] = {
    slug: entry["score365_id"]
    for slug, entry in _COMPETITION_MAP.items()
    if entry.get("score365_id")
}

# slug → BSD (bzzoiro) league id (primary source for 8/10 competitions)
BSD_COMPETITION_IDS: dict[str, int] = {
    slug: entry["bsd_id"]
    for slug, entry in _COMPETITION_MAP.items()
    if entry.get("bsd_id")
}

# slug → which source to query first
PRIMARY_SOURCE: dict[str, str] = {
    slug: entry.get("primary_source", "bsd")
    for slug, entry in _COMPETITION_MAP.items()
}

# slug → public-facing metadata mirroring the LEAGUES dict in score365_adapter.py
FOOTBALL_LEAGUES: dict[str, dict] = {
    slug: {
        "id": entry["score365_id"],
        "name_en": entry["name_en"],
        "name_he": entry["name_he"],
        "country": entry.get("country", ""),
        "sport": "football",
        "periods": 2,
        "period_minutes": 45,
        # extra minutes per side for stoppage; not actually used yet
        "stoppage_minutes": 5,
    }
    for slug, entry in _COMPETITION_MAP.items()
}

# Slugs ordered by "popularity"/launch priority. The iOS app reads this order
# when showing competitions.
DEFAULT_LEAGUE_ORDER: list[str] = [
    "premier_league",
    "ucl",
    "la_liga",
    "serie_a",
    "bundesliga",
    "ligue_1",
    "europa_league",
    "world_cup",
    "euros",
    "ligat_haal",
    "friendlies",
]


# ---------------------------------------------------------------------------
# Allscores day cache
#
# 365scores has no per-competition games endpoint — `/games/allscores/` returns
# the ENTIRE day's global football slate (~800 competitions), which we then
# filter by competitionId in Python. Without caching, a single cold `/feed`
# request asks for the same day once *per* tracked league (10x), re-downloading
# the whole world each time. We cache the raw day payload keyed by date range so
# concurrent same-date callers collapse to one upstream fetch, while distinct
# dates still run in parallel (per-key locking, not a single global lock).

_allscores_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_allscores_key_locks: dict[tuple[str, str], threading.Lock] = {}
_allscores_meta_lock = threading.Lock()
_ALLSCORES_CACHE_MAX = 40  # full-day payloads are large; cap memory footprint


def _allscores_ttl(start: str) -> float:
    """Date-aware TTL (seconds), mirroring main.py's `_feed_ttl`: live (today)
    data is volatile, past results are static, future fixtures rarely move."""
    try:
        d = date(*(int(p) for p in reversed(start.split("/"))))  # DD/MM/YYYY
    except (ValueError, TypeError):
        return 60.0  # unparseable → treat as volatile
    today = date.today()
    if d < today:
        return 6 * 3600.0   # past — static results
    if d == today:
        return 60.0         # today — live scores in flight
    return 30 * 60.0        # future — fixtures


def _allscores_key_lock(key: tuple[str, str]) -> threading.Lock:
    with _allscores_meta_lock:
        lk = _allscores_key_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _allscores_key_locks[key] = lk
        return lk


def _allscores_store(key: tuple[str, str], games: list[dict]) -> None:
    with _allscores_meta_lock:
        _allscores_cache[key] = (time.time(), games)
        if len(_allscores_cache) > _ALLSCORES_CACHE_MAX:
            # Evict the oldest-inserted entries (and their per-key locks).
            oldest = sorted(_allscores_cache, key=lambda k: _allscores_cache[k][0])
            for k in oldest[: len(_allscores_cache) - _ALLSCORES_CACHE_MAX]:
                _allscores_cache.pop(k, None)
                _allscores_key_locks.pop(k, None)


# ---------------------------------------------------------------------------
# Public adapter functions

def soccer_games_by_date(start: str, end: str | None = None) -> list[dict]:
    """Get all football games on a date range.

    Dates are DD/MM/YYYY strings — 365scores football uses day-first format,
    unlike basketball which uses MM/DD/YYYY. Callers must convert accordingly.
    Returns the `games` list from the response (caller can filter by
    `competitionId` against `COMPETITION_IDS`).

    The raw day payload is cached (see `_allscores_cache`) so the ~800-competition
    download happens once per date instead of once per tracked league.
    """
    key = (start, end or start)
    ttl = _allscores_ttl(start)

    cached = _allscores_cache.get(key)
    if cached and time.time() - cached[0] < ttl:
        return cached[1]

    # Serialize misses for this exact date so 10 concurrent leagues asking for
    # the same day trigger a single upstream fetch (the rest hit the warm entry).
    with _allscores_key_lock(key):
        cached = _allscores_cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
        with Client365() as c:
            data = get_games_by_date(c, start, end, sport_id=FOOTBALL_SPORT_ID)
            games = data.get("games", [])
        _allscores_store(key, games)
        return games


def soccer_game_detail(game_id: int) -> dict:
    """Get full football game detail (events, lineups, stages)."""
    with Client365() as c:
        return get_game_detail(c, game_id)


def soccer_game_stats(game_id: int) -> list[dict]:
    """Per-team match statistics for a football game.

    365scores serves stats from a SEPARATE endpoint (`/game/stats/`), not inline
    in `/game/`. Returns the flat `statistics` list — each item is one stat for
    one team: `{id, name, competitorId, value, valuePercentage, ...}`. Empty
    list when the game has no stats. Best-effort: caller guards exceptions."""
    with Client365() as c:
        data = c.get("/game/stats/", params={"games": str(game_id)})
    return data.get("statistics") or []


# ---------------------------------------------------------------------------
# Competition rolling-window cache (for building a team's season from 365).
#
# 365scores has no "all games for a competition this season" endpoint. The
# competition-filtered endpoints (/current/, /results/, /fixtures/) each return
# only a rolling window (roughly the last ~week through the next ~week) and
# IGNORE date params. We hit all three once per competition, dedupe by game id,
# and cache the combined list briefly so concurrent callers share one HTTP burst.

_window_cache: dict[int, tuple[float, list[dict]]] = {}
_window_lock = threading.Lock()
_WINDOW_TTL = 55  # seconds


def soccer_competition_games(competition_id: int) -> list[dict]:
    """All games 365scores currently knows about for a football competition.

    Combines /games/current/, /games/results/, /games/fixtures/ (each a rolling
    window) and dedupes by game id. Cached for _WINDOW_TTL seconds. Returns the
    raw 365 game dicts (caller filters by competitor id and normalizes)."""
    now = time.time()
    with _window_lock:
        cached = _window_cache.get(competition_id)
        if cached and now - cached[0] < _WINDOW_TTL:
            return cached[1]

    seen: dict[int, dict] = {}
    with Client365() as c:
        for endpoint in ("/games/current/", "/games/results/", "/games/fixtures/"):
            try:
                data = c.get(endpoint, params={"competitions": str(competition_id)})
                for g in data.get("games", []):
                    gid = g.get("id")
                    if gid is not None and gid not in seen:
                        seen[gid] = g
            except Exception:
                # Best-effort: a single endpoint failing still leaves the others.
                pass

    games = list(seen.values())
    with _window_lock:
        _window_cache[competition_id] = (now, games)
    return games


def soccer_competitions() -> list[dict]:
    """Get the full list of football competitions on 365scores.

    Useful for debugging or backfilling new competitions. Production code
    should use `COMPETITION_IDS` / `FOOTBALL_LEAGUES` instead.
    """
    with Client365() as c:
        return get_all_competitions(c, sport_id=FOOTBALL_SPORT_ID)


def league_slug_for_competition_id(competition_id: int) -> str | None:
    """Reverse lookup: 365scores competition id → internal slug."""
    for slug, cid in COMPETITION_IDS.items():
        if cid == competition_id:
            return slug
    return None


def is_in_scope(competition_id: int) -> bool:
    """True if this 365scores competition is one of the 10 tracked football
    competitions for v3 launch."""
    return competition_id in COMPETITION_IDS.values()


def soccer_league_standings(competition_id: int) -> list[dict]:
    """Get current standings rows for a competition.

    Returns a flat list of competitor dicts (merged across all groups/stages)
    with at minimum 'id' and 'name' keys. Useful for seeding the search index
    when no live games are available from that competition.
    """
    with Client365() as c:
        data = c.get("/standings/", params={"competitions": str(competition_id)})
    rows: list[dict] = []
    for standing in data.get("standings", []):
        for row in standing.get("rows", []):
            comp = row.get("competitor") or {}
            if comp.get("id") and comp.get("name"):
                rows.append(comp)
    # Dedupe by competitor id (same team may appear in multiple groups)
    seen: set = set()
    unique: list[dict] = []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            team_names_he.attach(r)
            unique.append(r)
    return unique


def _s365_crest(team_id) -> str | None:
    if not team_id:
        return None
    return f"https://imagecache.365scores.com/image/upload/d_Competitors:default1.png/Competitors/{team_id}"


def soccer_league_standings_table(competition_id: int) -> dict | None:
    """Full league table for a 365scores competition, in the canonical
    LeagueStandings shape iOS expects (matching `_normalize_soccer_standings`'s
    output): `{league_id, season:{id,name}, grouped, standings:[...], groups:[...]}`.

    365scores returns the table under `standings[*].rows`, each row carrying a
    `competitor` plus `gamesWon/gamesLost/gamesEven/for/against/points/position`
    and a `groupNum`. Group-based competitions (Euros groups, the Israeli
    Championship / Relegation split) name their groups in `standings[*].groups`
    (`[{num, name}]`); we emit one mini-table per group when there's more than
    one, else a flat table."""
    with Client365() as c:
        data = c.get("/standings/", params={"competitions": str(competition_id)})
    stages = data.get("standings") if isinstance(data, dict) else None
    if not stages:
        return None

    # Season label from the competition meta block (best-effort).
    season_id = 0
    season_name = ""
    for comp in data.get("competitions", []) or []:
        if comp.get("id") == competition_id:
            for s in comp.get("seasons", []) or []:
                if s.get("current") or s.get("isCurrent"):
                    season_id = s.get("num") or 0
                    season_name = s.get("name") or ""
                    break
            if not season_name:
                seasons_list = comp.get("seasons") or []
                if seasons_list:
                    season_id = seasons_list[0].get("num") or 0
                    season_name = seasons_list[0].get("name") or ""
            break

    # Prefer the current stage; otherwise the last stage 365 returned.
    stage = None
    for st in stages:
        if st.get("isCurrentStage"):
            stage = st
            break
    if stage is None:
        stage = stages[-1]

    group_names = {
        g.get("num"): g.get("name")
        for g in (stage.get("groups") or [])
        if g.get("num") is not None
    }

    def _row(r: dict) -> dict | None:
        comp = r.get("competitor") or {}
        tid = comp.get("id") or 0
        name = comp.get("name") or ""
        if not tid or not name:
            return None
        gf = int(r.get("for") or 0)
        ga = int(r.get("against") or 0)
        entry = {
            "position":   int(r.get("position") or 0),
            "team_id":    tid,
            "team_name":  name,
            "team_crest": _s365_crest(tid),
            "played":     int(r.get("gamePlayed") or 0),
            "won":        int(r.get("gamesWon") or 0),
            "drawn":      int(r.get("gamesEven") or 0),
            "lost":       int(r.get("gamesLost") or 0),
            "gf": gf, "ga": ga, "gd": gf - ga,
            "pts":        int(r.get("points") or 0),
            "form":       None,
            "live":       None,
        }
        he = team_names_he.hebrew_name(name)
        if he:
            entry["team_name_he"] = he
        return entry

    base = {"league_id": competition_id, "season": {"id": season_id, "name": season_name}}

    # Bucket rows by groupNum.
    buckets: dict[int, list[dict]] = {}
    for r in stage.get("rows", []) or []:
        gnum = r.get("groupNum") or 1
        row = _row(r)
        if row is not None:
            buckets.setdefault(gnum, []).append(row)

    if not buckets:
        return None

    if len(buckets) > 1:
        out_groups = []
        for gnum in sorted(buckets):
            rows = sorted(buckets[gnum], key=lambda x: x["position"])
            out_groups.append({"name": group_names.get(gnum) or f"Group {gnum}", "standings": rows})
        return {**base, "grouped": True, "standings": [], "groups": out_groups}

    only = next(iter(buckets.values()))
    rows = sorted(only, key=lambda x: x["position"])
    return {**base, "grouped": False, "standings": rows, "groups": []}
