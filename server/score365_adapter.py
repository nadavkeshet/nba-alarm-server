"""
Clutch Alarm – 365Scores Adapter
==================================

Single source of truth for all basketball data. Fetches from 365scores.com
(no API key required) and transforms responses into the ESPN-compatible
format that main.py, monitor.py, epg.py expect.

Supports NBA and Euroleague via competition IDs.
"""

import time
import logging
import threading
from datetime import datetime, timedelta, timezone

from score365.client import Client365
from score365.scraper import get_games_by_date, get_game_detail, extract_box_score, extract_players

log = logging.getLogger("nba_monitor")

# ── Competition IDs on 365scores ─────────────────────────────────────────────

COMPETITION_IDS = {
    "nba": 103,
    "euroleague": 569,
}

# ── League configs ───────────────────────────────────────────────────────────

LEAGUES = {
    "nba": {
        "id": 103,
        "name": "NBA",
        "country": "USA",
        "periods": 4,
        "period_minutes": 12,
        "ot_minutes": 5,
    },
    "euroleague": {
        "id": 569,
        "name": "Euroleague",
        "country": "Europe",
        "periods": 4,
        "period_minutes": 10,
        "ot_minutes": 5,
    },
}

# ── Abbreviation normalisation ─────────────────────────────────────────────────
# 365scores uses slightly different abbreviations for some NBA teams.
# Map them to what epg.py / i18n.py / the mobile app expect.

_SYMBOL_NORMALIZE: dict[str, str] = {
    # NBA
    "NOP": "NO",     # New Orleans Pelicans
    "NYK": "NY",     # New York Knicks
    "SAS": "SA",     # San Antonio Spurs
    "UTA": "UTAH",   # Utah Jazz
    "WAS": "WSH",    # Washington Wizards
    "GSW": "GS",     # Golden State Warriors
}

# Euroleague: 365scores uses different abbreviations than the rest of the
# server (which inherited the API-Basketball naming). Map per-team-id to
# avoid the PAR collision (Partizan vs Paris Basketball).
_EUROLEAGUE_TEAM_ID_TO_ABBR: dict[int, str] = {
    966:   "EFE",   # Anadolu Efes      (365: EFS)
    16001: "MON",   # AS Monaco         (365: ASM)
    543:   "LYN",   # ASVEL             (365: ASV)
    8648:  "BAY",   # Bayern Munich
    6349:  "BAS",   # Baskonia          (365: BKN)
    5528:  "CZV",   # Crvena Zvezda
    83343: "DUB",   # Dubai BC          (365: ?)
    970:   "FEN",   # Fenerbahce
    275:   "MLN",   # Olimpia Milano
    631:   "MTA",   # Maccabi Tel Aviv
    1042:  "OLY",   # Olympiacos
    1041:  "PAN",   # Panathinaikos     (365: PAO)
    5509:  "PAT",   # Partizan          (365: PAR)
    204:   "RMA",   # Real Madrid
    288:   "VIR",   # Virtus Bologna
    # The following teams are expected but their 365 IDs are still TBD —
    # they'll fall back to whatever symbolicName 365 returns.
    # 0: "BAR",  Barcelona
    # 0: "HTA",  Hapoel Tel Aviv
    # 0: "PAR",  Paris Basketball
    # 0: "VAL",  Valencia
    # 0: "ZAL",  Zalgiris
}

# Full display names keyed by our canonical abbreviation
_DISPLAY_NAMES: dict[str, tuple[str, str]] = {
    # abbr: (displayName, shortName)
    "ATL":  ("Atlanta Hawks",             "Hawks"),
    "BOS":  ("Boston Celtics",            "Celtics"),
    "BKN":  ("Brooklyn Nets",             "Nets"),
    "CHA":  ("Charlotte Hornets",         "Hornets"),
    "CHI":  ("Chicago Bulls",             "Bulls"),
    "CLE":  ("Cleveland Cavaliers",       "Cavaliers"),
    "DAL":  ("Dallas Mavericks",          "Mavericks"),
    "DEN":  ("Denver Nuggets",            "Nuggets"),
    "DET":  ("Detroit Pistons",           "Pistons"),
    "GS":   ("Golden State Warriors",     "Warriors"),
    "HOU":  ("Houston Rockets",           "Rockets"),
    "IND":  ("Indiana Pacers",            "Pacers"),
    "LAC":  ("LA Clippers",               "Clippers"),
    "LAL":  ("Los Angeles Lakers",        "Lakers"),
    "MEM":  ("Memphis Grizzlies",         "Grizzlies"),
    "MIA":  ("Miami Heat",                "Heat"),
    "MIL":  ("Milwaukee Bucks",           "Bucks"),
    "MIN":  ("Minnesota Timberwolves",    "Timberwolves"),
    "NO":   ("New Orleans Pelicans",      "Pelicans"),
    "NY":   ("New York Knicks",           "Knicks"),
    "OKC":  ("Oklahoma City Thunder",     "Thunder"),
    "ORL":  ("Orlando Magic",             "Magic"),
    "PHI":  ("Philadelphia 76ers",        "76ers"),
    "PHX":  ("Phoenix Suns",              "Suns"),
    "POR":  ("Portland Trail Blazers",    "Trail Blazers"),
    "SAC":  ("Sacramento Kings",          "Kings"),
    "SA":   ("San Antonio Spurs",         "Spurs"),
    "TOR":  ("Toronto Raptors",           "Raptors"),
    "UTAH": ("Utah Jazz",                 "Jazz"),
    "WSH":  ("Washington Wizards",        "Wizards"),
    # Euroleague
    "EFE":  ("Anadolu Efes",              "Efes"),
    "BAR":  ("FC Barcelona",              "Barcelona"),
    "BAS":  ("Baskonia",                  "Baskonia"),
    "BAY":  ("Bayern Munich",             "Bayern"),
    "CZV":  ("Crvena Zvezda",             "Zvezda"),
    "DUB":  ("Dubai BC",                  "Dubai"),
    "FEN":  ("Fenerbahce",                "Fenerbahce"),
    "HTA":  ("Hapoel Tel Aviv",           "Hapoel TA"),
    "LYN":  ("LDLC ASVEL Villeurbanne",   "ASVEL"),
    "MTA":  ("Maccabi Tel Aviv",          "Maccabi TA"),
    "MON":  ("AS Monaco",                 "Monaco"),
    "MLN":  ("Olimpia Milano",            "Milano"),
    "OLY":  ("Olympiacos",                "Olympiacos"),
    "PAN":  ("Panathinaikos",             "Panathinaikos"),
    "PAR":  ("Paris Basketball",          "Paris"),
    "PAT":  ("Partizan",                  "Partizan"),
    "RMA":  ("Real Madrid",               "Real Madrid"),
    "VAL":  ("Valencia Basket",           "Valencia"),
    "VIR":  ("Virtus Bologna",            "Virtus"),
    "ZAL":  ("Zalgiris Kaunas",           "Zalgiris"),
}

# 365scores NBA team IDs (canonical abbr → 365scores ID).
# Discovered by scanning recent games — covers all 30 NBA teams.
_365_TEAM_IDS: dict[str, int] = {
    "ATL": 1307, "BOS": 1300, "BKN": 1308, "CHA": 1311, "CHI": 1310,
    "CLE": 1303, "DAL": 1321, "DEN": 1323, "DET": 1301, "GS":  1322,
    "HOU": 1319, "IND": 1309, "LAC": 1326, "LAL": 1317, "MEM": 1327,
    "MIA": 1314, "MIL": 1312, "MIN": 1328, "NO":  1315, "NY":  1313,
    "OKC": 1330, "ORL": 1302, "PHI": 1306, "PHX": 1320, "POR": 1324,
    "SAC": 1325, "SA":  1316, "TOR": 1305, "UTAH": 1318, "WSH": 1304,
}

# Legacy → 365scores team ID migration map.
# Keys cover both ESPN team IDs (e.g. "1" = ATL) and API-Basketball IDs
# (e.g. "132" = ATL) that may exist in old DB rows from earlier deployments.
_ESPN_TEAM_IDS: dict[str, str] = {
    "1": "ATL", "2": "BOS", "17": "BKN", "30": "CHA", "4": "CHI",
    "5": "CLE", "6": "DAL", "7": "DEN", "8": "DET", "9": "GS",
    "10": "HOU", "11": "IND", "12": "LAC", "13": "LAL", "29": "MEM",
    "14": "MIA", "15": "MIL", "16": "MIN", "3": "NO", "18": "NY",
    "25": "OKC", "19": "ORL", "20": "PHI", "21": "PHX", "22": "POR",
    "23": "SAC", "24": "SA", "28": "TOR", "26": "UTAH", "27": "WSH",
}
_APIBB_TEAM_IDS: dict[str, str] = {
    "132": "ATL", "133": "BOS", "134": "BKN", "135": "CHA", "136": "CHI",
    "137": "CLE", "138": "DAL", "139": "DEN", "140": "DET", "141": "GS",
    "142": "HOU", "143": "IND", "144": "LAC", "145": "LAL", "146": "MEM",
    "147": "MIA", "148": "MIL", "149": "MIN", "150": "NO", "151": "NY",
    "152": "OKC", "153": "ORL", "154": "PHI", "155": "PHX", "156": "POR",
    "157": "SAC", "158": "SA", "159": "TOR", "160": "UTAH", "161": "WSH",
}
LEGACY_TO_365_TEAM_ID: dict[str, str] = {
    **{eid: str(_365_TEAM_IDS[abbr]) for eid, abbr in _ESPN_TEAM_IDS.items()},
    **{aid: str(_365_TEAM_IDS[abbr]) for aid, abbr in _APIBB_TEAM_IDS.items()},
}

# ── Date helpers ─────────────────────────────────────────────────────────────


def _to_mdy(date_str: str) -> str:
    """Convert YYYYMMDD or YYYY-MM-DD to MM/DD/YYYY for 365scores API."""
    date_str = date_str.strip()
    if len(date_str) == 8 and "-" not in date_str:
        return f"{date_str[4:6]}/{date_str[6:8]}/{date_str[:4]}"
    if len(date_str) == 10 and date_str[4] == "-":
        parts = date_str.split("-")
        return f"{parts[1]}/{parts[2]}/{parts[0]}"
    return date_str  # already in MM/DD/YYYY


# ── Status translation ────────────────────────────────────────────────────────

_QUARTER_DETAIL = {"Q1": "1st Quarter", "Q2": "2nd Quarter", "Q3": "3rd Quarter", "Q4": "4th Quarter"}
_QUARTER_PERIOD = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}


def _translate_status(game: dict) -> dict:
    """
    Convert 365scores status fields to ESPN status format.

    365scores:
      statusGroup: 2=pre, 3=live, 4=post
      statusText:  "Scheduled" | "Q1"-"Q4" | "Halftime" | "Overtime" | "Final" | …
      gameTimeDisplay: "MM:SS" remaining in quarter (empty when not live)

    ESPN format:
      {"type": {"state": "pre|in|post", "detail": "..."}, "period": int, "displayClock": ""}
    """
    status_group = game.get("statusGroup", 2)
    status_text  = game.get("statusText", "Scheduled")
    display_clock = game.get("gameTimeDisplay", "")

    if status_group == 2:
        return {"type": {"state": "pre", "detail": "Scheduled"}, "period": 0, "displayClock": ""}

    if status_group == 4:
        detail = "Final/OT" if "OT" in status_text else "Final"
        return {"type": {"state": "post", "detail": detail}, "period": 4, "displayClock": "0:00"}

    # status_group == 3 → live
    if status_text in _QUARTER_DETAIL:
        return {
            "type": {"state": "in", "detail": _QUARTER_DETAIL[status_text]},
            "period": _QUARTER_PERIOD[status_text],
            "displayClock": display_clock,
        }
    if status_text == "Halftime":
        return {"type": {"state": "in", "detail": "Halftime"}, "period": 2, "displayClock": ""}
    if "OT" in status_text or "Overtime" in status_text.lower():
        return {"type": {"state": "in", "detail": "Overtime"}, "period": 5, "displayClock": display_clock}
    # Break / unknown live state
    return {"type": {"state": "in", "detail": status_text}, "period": 0, "displayClock": display_clock}


# ── Team helper ───────────────────────────────────────────────────────────────

def _make_team_obj(comp: dict) -> dict:
    """Build ESPN-format team object from a 365scores competitor dict."""
    tid = comp.get("id", 0)
    # Euroleague: use team-id mapping (avoids the PAR conflict)
    abbr = _EUROLEAGUE_TEAM_ID_TO_ABBR.get(tid)
    if not abbr:
        raw_sym = comp.get("symbolicName", "???")
        abbr = _SYMBOL_NORMALIZE.get(raw_sym, raw_sym)
    display_name, short_name = _DISPLAY_NAMES.get(abbr, (comp.get("name", ""), comp.get("shortName", "")))
    return {
        "id":          str(comp.get("id", "")),
        "name":        short_name or comp.get("shortName", ""),
        "displayName": display_name or comp.get("name", ""),
        "abbreviation": abbr,
        "logo":        "",   # 365scores serves images via CDN but we don't need the URL
    }


# ── Games (scoreboard) ────────────────────────────────────────────────────────

def _normalize_yyyymmdd(date_str: str) -> str:
    """Accept YYYYMMDD or YYYY-MM-DD, return YYYYMMDD."""
    return date_str.strip().replace("-", "")


def _eastern_today_yyyymmdd() -> str:
    """Current US Eastern date as YYYYMMDD."""
    return (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y%m%d")


# Combined-window cache: 365scores' /games/allscores/ silently drops NBA, and
# the competition-filtered endpoints (/current/, /results/, /fixtures/) ignore
# date params and each return only a rolling ~5-day window. Workaround: hit
# all three once per league, dedupe, then filter by date in code. Cache the
# combined result briefly so concurrent monitor threads share one HTTP burst.
_window_cache: dict[int, tuple[float, list[dict]]] = {}
_window_lock = threading.Lock()
_WINDOW_TTL = 30  # seconds


def _fetch_competition_window(competition_id: int) -> list[dict]:
    """
    Return all games 365scores currently knows about for the given competition.
    Combines /current/, /results/, /fixtures/ — together this covers roughly
    last 5 days through next 5 days. Cached for _WINDOW_TTL seconds.
    """
    now = time.time()
    with _window_lock:
        cached = _window_cache.get(competition_id)
        if cached and now - cached[0] < _WINDOW_TTL:
            return cached[1]

    seen: dict[int, dict] = {}
    with Client365() as client:
        for endpoint in ("/games/current/", "/games/results/", "/games/fixtures/"):
            try:
                data = client.get(endpoint, params={"competitions": str(competition_id)})
                for g in data.get("games", []):
                    gid = g.get("id")
                    if gid is not None and gid not in seen:
                        seen[gid] = g
            except Exception as exc:
                log.debug("365scores: %s failed for %s: %s", endpoint, competition_id, exc)

    games = list(seen.values())
    with _window_lock:
        _window_cache[competition_id] = (now, games)
    return games


def _game_eastern_date(game: dict) -> str:
    """
    Convert a game's startTime to YYYYMMDD in US Eastern time, matching the
    server's "game date" convention (a 7pm PT tip on day X is still day X).
    """
    raw = game.get("startTime", "")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    eastern = dt.astimezone(timezone(timedelta(hours=-5)))
    return eastern.strftime("%Y%m%d")


def fetch_games(date_str: str, league: str = "nba") -> list[dict]:
    """
    Fetch games for a date and league, return in ESPN event format.
    date_str can be YYYYMMDD or YYYY-MM-DD.

    Note: 365scores only exposes a rolling ~10-day window via the
    competition-filtered endpoints, so dates more than ~5 days from "now"
    will return an empty list.
    """
    comp_id = COMPETITION_IDS.get(league, COMPETITION_IDS["nba"])
    target = _normalize_yyyymmdd(date_str)
    try:
        raw_games = _fetch_competition_window(comp_id)
    except Exception as exc:
        log.warning("365scores: fetch_games failed for %s/%s: %s", league, date_str, exc)
        return []

    matching = [g for g in raw_games if _game_eastern_date(g) == target]
    return [_transform_game(g, league=league) for g in matching]


def _transform_game(game: dict, league: str = "nba") -> dict:
    """Transform one 365scores game dict into ESPN event format."""
    game_id = str(game["id"])
    home_comp = game.get("homeCompetitor", {})
    away_comp = game.get("awayCompetitor", {})

    home_team = _make_team_obj(home_comp)
    away_team = _make_team_obj(away_comp)

    home_s = home_comp.get("score", -1)
    away_s = away_comp.get("score", -1)
    home_score = str(int(home_s)) if isinstance(home_s, (int, float)) and home_s >= 0 else "0"
    away_score = str(int(away_s)) if isinstance(away_s, (int, float)) and away_s >= 0 else "0"

    status = _translate_status(game)
    short_name = f"{away_team['abbreviation']} @ {home_team['abbreviation']}"
    full_name  = f"{away_team['displayName']} at {home_team['displayName']}"

    return {
        "id":        game_id,
        "date":      game.get("startTime", ""),
        "name":      full_name,
        "shortName": short_name,
        "league":    league,
        "status":    status,
        "competitions": [{
            "status": status,
            "competitors": [
                {"homeAway": "home", "score": home_score, "team": home_team},
                {"homeAway": "away", "score": away_score, "team": away_team},
            ],
        }],
    }


# ── Player boxscore ───────────────────────────────────────────────────────────

_boxscore_cache: dict[str, tuple[float, dict]] = {}
_boxscore_lock = threading.Lock()
_BOXSCORE_TTL = 9  # seconds


def fetch_player_boxscore(game_id: str) -> dict | None:
    """
    Fetch player statistics for a game in ESPN boxscore format.
    Cached for TTL seconds so multiple monitoring threads share one HTTP call.

    365scores provides steals, blocks, and turnovers (unlike API-Basketball).
    """
    with _boxscore_lock:
        cached = _boxscore_cache.get(game_id)
        if cached and time.time() - cached[0] < _BOXSCORE_TTL:
            return cached[1]

    try:
        with Client365() as client:
            game = get_game_detail(client, int(game_id))
    except Exception as exc:
        log.warning("365scores: game detail fetch failed for %s: %s", game_id, exc)
        return None

    if not game:
        return None

    try:
        box = extract_box_score(game)
    except Exception as exc:
        log.warning("365scores: extract_box_score failed for %s: %s", game_id, exc)
        return None

    home_id = str(game.get("homeCompetitor", {}).get("id", ""))
    away_id = str(game.get("awayCompetitor", {}).get("id", ""))

    stat_names = ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PF", "PTS", "3PM"]
    players_sections = []

    for side, team_id in [("home", home_id), ("away", away_id)]:
        athletes = []
        for p in box.get(side, {}).get("players", []):
            stats = p.get("stats", {})
            if not stats:
                continue

            three_pt = str(stats.get("3PT", "0/0"))
            three_made = three_pt.split("/")[0] if "/" in three_pt else "0"

            stat_vals = [
                str(stats.get("Min", "0:00")),
                str(stats.get("FG", "0/0")).replace("/", "-"),
                str(stats.get("3PT", "0/0")).replace("/", "-"),
                str(stats.get("FT", "0/0")).replace("/", "-"),
                str(stats.get("Reb", "0")),
                str(stats.get("Ast", "0")),
                str(stats.get("Stl", "0")),
                str(stats.get("Blk", "0")),
                str(stats.get("TO", "0")),
                str(stats.get("PF", "0")),
                str(stats.get("Pts", "0")),
                three_made,
            ]

            athletes.append({
                "athlete": {"id": str(p["id"]), "displayName": p["name"]},
                "stats": stat_vals,
                "starter": p.get("status", "").lower() == "starting",
            })

        players_sections.append({
            "team": {"id": team_id},
            "statistics": [{"names": stat_names, "athletes": athletes}],
        })

    result = {"boxscore": {"players": players_sections}}

    with _boxscore_lock:
        _boxscore_cache[game_id] = (time.time(), result)

    return result


# ── Teams & Rosters ───────────────────────────────────────────────────────────

def fetch_all_teams(league: str = "nba") -> list[dict]:
    """
    Fetch all teams for a league by scanning recent games (past 7 days).
    Returns list of dicts: {id, name, abbreviation, logo, players: []}
    """
    comp_id = COMPETITION_IDS.get(league, COMPETITION_IDS["nba"])
    seen: dict[str, dict] = {}  # team_id → team dict

    today = datetime.now(timezone.utc) - timedelta(hours=5)  # Eastern time
    for days_back in range(0, 8):
        date = today - timedelta(days=days_back)
        date_str = date.strftime("%Y%m%d")
        try:
            events = fetch_games(date_str, league=league)
            for event in events:
                for comp_entry in event.get("competitions", []):
                    for competitor in comp_entry.get("competitors", []):
                        team = competitor.get("team", {})
                        tid = team.get("id", "")
                        if tid and tid not in seen:
                            seen[tid] = {
                                "id":           tid,
                                "name":         team.get("displayName", team.get("name", "")),
                                "abbreviation": team.get("abbreviation", "???"),
                                "logo":         team.get("logo", ""),
                                "players":      [],
                            }
        except Exception as exc:
            log.debug("fetch_all_teams: error for %s: %s", date_str, exc)

        if len(seen) >= (30 if league == "nba" else 18):
            break  # found all expected teams

    return list(seen.values())


def fetch_team_players(team_id: int | str, league: str = "nba") -> list[dict]:
    """
    Fetch players for a team from the 365scores /squads/ endpoint.
    Returns list: [{id, name, jersey, position}]
    """
    try:
        with Client365() as client:
            data = client.get("/squads/", params={"competitors": str(team_id)})
        squads = data.get("squads", [])
        if not squads:
            return []
        athletes = squads[0].get("athletes", [])
        result = []
        for a in athletes:
            pos = a.get("position", {})
            pos_abbr = pos.get("shortName", "") if isinstance(pos, dict) else ""
            jersey = a.get("jerseyNum")
            result.append({
                "id":       str(a.get("id", "")),
                "name":     a.get("name", "?"),
                "jersey":   str(jersey) if jersey is not None else "",
                "position": pos_abbr,
            })
        return result
    except Exception as exc:
        log.debug("365scores: squad fetch failed for team %s: %s", team_id, exc)
        return []


# ── Validation ────────────────────────────────────────────────────────────────

def validate_game_event(event: dict) -> list[str]:
    """Return list of missing/unexpected fields for a single game event."""
    issues = []
    if not event.get("id"):
        issues.append("missing id")
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        issues.append("competitors < 2")
    else:
        for c in competitors:
            if not c.get("team", {}).get("abbreviation"):
                issues.append("missing team abbreviation")
            if c.get("score") is None:
                issues.append("missing score")
    state = event.get("status", {}).get("type", {}).get("state")
    if state not in {"pre", "in", "post"}:
        issues.append(f"unexpected state: {state!r}")
    return issues
