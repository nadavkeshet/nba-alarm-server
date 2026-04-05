"""
Clutch Alarm – API-Basketball Adapter
======================================

Single source of truth for all basketball data. Fetches from api-sports.io
and transforms responses into the ESPN-compatible format that the rest
of the server (main.py, monitor.py, epg.py) and mobile apps expect.

Supports multiple leagues (NBA, Euroleague, etc.) via league configs.
"""

import os
import time
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger("nba_monitor")

# ── Configuration ────────────────────────────────────────────────────────────

API_BASE = "https://v1.basketball.api-sports.io"
API_KEY = os.environ.get("API_BASKETBALL_KEY", "")

# ── League configs ───────────────────────────────────────────────────────────

LEAGUES = {
    "nba": {
        "id": 12,
        "season": os.environ.get("API_BASKETBALL_SEASON", "2025-2026"),
        "name": "NBA",
        "country": "USA",
        "periods": 4,
        "period_minutes": 12,
        "ot_minutes": 5,
    },
    "euroleague": {
        "id": 120,
        "season": "2025",
        "player_season": "2025-2026",  # players endpoint uses different season format
        "name": "Euroleague",
        "country": "Europe",
        "periods": 4,
        "period_minutes": 10,
        "ot_minutes": 5,
    },
}

# Backwards compat
NBA_LEAGUE_ID = 12
SEASON = LEAGUES["nba"]["season"]

# ── Team ID → ESPN abbreviation / display name mapping ───────────────────────
# API-Basketball team IDs mapped to ESPN abbreviations used throughout
# epg.py, i18n.py, and the mobile apps.

_TEAM_MAP: dict[int, dict] = {
    132: {"abbreviation": "ATL", "displayName": "Atlanta Hawks",            "name": "Hawks"},
    133: {"abbreviation": "BOS", "displayName": "Boston Celtics",           "name": "Celtics"},
    134: {"abbreviation": "BKN", "displayName": "Brooklyn Nets",            "name": "Nets"},
    135: {"abbreviation": "CHA", "displayName": "Charlotte Hornets",        "name": "Hornets"},
    136: {"abbreviation": "CHI", "displayName": "Chicago Bulls",            "name": "Bulls"},
    137: {"abbreviation": "CLE", "displayName": "Cleveland Cavaliers",      "name": "Cavaliers"},
    138: {"abbreviation": "DAL", "displayName": "Dallas Mavericks",         "name": "Mavericks"},
    139: {"abbreviation": "DEN", "displayName": "Denver Nuggets",           "name": "Nuggets"},
    140: {"abbreviation": "DET", "displayName": "Detroit Pistons",          "name": "Pistons"},
    141: {"abbreviation": "GS",  "displayName": "Golden State Warriors",    "name": "Warriors"},
    142: {"abbreviation": "HOU", "displayName": "Houston Rockets",          "name": "Rockets"},
    143: {"abbreviation": "IND", "displayName": "Indiana Pacers",           "name": "Pacers"},
    144: {"abbreviation": "LAC", "displayName": "LA Clippers",              "name": "Clippers"},
    145: {"abbreviation": "LAL", "displayName": "Los Angeles Lakers",       "name": "Lakers"},
    146: {"abbreviation": "MEM", "displayName": "Memphis Grizzlies",        "name": "Grizzlies"},
    147: {"abbreviation": "MIA", "displayName": "Miami Heat",               "name": "Heat"},
    148: {"abbreviation": "MIL", "displayName": "Milwaukee Bucks",          "name": "Bucks"},
    149: {"abbreviation": "MIN", "displayName": "Minnesota Timberwolves",   "name": "Timberwolves"},
    150: {"abbreviation": "NO",  "displayName": "New Orleans Pelicans",     "name": "Pelicans"},
    151: {"abbreviation": "NY",  "displayName": "New York Knicks",          "name": "Knicks"},
    152: {"abbreviation": "OKC", "displayName": "Oklahoma City Thunder",    "name": "Thunder"},
    153: {"abbreviation": "ORL", "displayName": "Orlando Magic",            "name": "Magic"},
    154: {"abbreviation": "PHI", "displayName": "Philadelphia 76ers",       "name": "76ers"},
    155: {"abbreviation": "PHX", "displayName": "Phoenix Suns",             "name": "Suns"},
    156: {"abbreviation": "POR", "displayName": "Portland Trail Blazers",   "name": "Trail Blazers"},
    157: {"abbreviation": "SAC", "displayName": "Sacramento Kings",         "name": "Kings"},
    158: {"abbreviation": "SA",  "displayName": "San Antonio Spurs",        "name": "Spurs"},
    159: {"abbreviation": "TOR", "displayName": "Toronto Raptors",          "name": "Raptors"},
    160: {"abbreviation": "UTAH","displayName": "Utah Jazz",                "name": "Jazz"},
    161: {"abbreviation": "WSH", "displayName": "Washington Wizards",       "name": "Wizards"},
    # ── Euroleague ──
    1263: {"abbreviation": "EFE", "displayName": "Anadolu Efes",            "name": "Efes"},
    2329: {"abbreviation": "BAR", "displayName": "FC Barcelona",            "name": "Barcelona"},
    2331: {"abbreviation": "BAS", "displayName": "Baskonia",                "name": "Baskonia"},
     522: {"abbreviation": "BAY", "displayName": "Bayern Munich",           "name": "Bayern"},
    1065: {"abbreviation": "CZV", "displayName": "Crvena Zvezda",           "name": "Zvezda"},
    6496: {"abbreviation": "DUB", "displayName": "Dubai BC",                "name": "Dubai"},
    1270: {"abbreviation": "FEN", "displayName": "Fenerbahce",              "name": "Fenerbahce"},
     682: {"abbreviation": "HTA", "displayName": "Hapoel Tel Aviv",         "name": "Hapoel TA"},
      26: {"abbreviation": "LYN", "displayName": "LDLC ASVEL Villeurbanne", "name": "ASVEL"},
     687: {"abbreviation": "MTA", "displayName": "Maccabi Tel Aviv",        "name": "Maccabi TA"},
      16: {"abbreviation": "MON", "displayName": "AS Monaco",               "name": "Monaco"},
     722: {"abbreviation": "MLN", "displayName": "Olimpia Milano",          "name": "Milano"},
    1542: {"abbreviation": "OLY", "displayName": "Olympiacos",              "name": "Olympiacos"},
     614: {"abbreviation": "PAN", "displayName": "Panathinaikos",           "name": "Panathinaikos"},
     108: {"abbreviation": "PAR", "displayName": "Paris Basketball",        "name": "Paris"},
    1068: {"abbreviation": "PAT", "displayName": "Partizan",                "name": "Partizan"},
    2338: {"abbreviation": "RMA", "displayName": "Real Madrid",             "name": "Real Madrid"},
    2341: {"abbreviation": "VAL", "displayName": "Valencia Basket",         "name": "Valencia"},
     732: {"abbreviation": "VIR", "displayName": "Virtus Bologna",          "name": "Virtus"},
     796: {"abbreviation": "ZAL", "displayName": "Zalgiris Kaunas",         "name": "Zalgiris"},
}

# Reverse lookup: abbreviation → API-Basketball team ID
_ABBR_TO_ID: dict[str, int] = {v["abbreviation"]: k for k, v in _TEAM_MAP.items()}

# ESPN team ID → API-Basketball team ID (for DB migration of favorite_teams)
ESPN_TO_API_TEAM_ID: dict[str, str] = {
    "1":  "132",   # ATL
    "2":  "133",   # BOS
    "17": "134",   # BKN
    "30": "135",   # CHA
    "4":  "136",   # CHI
    "5":  "137",   # CLE
    "6":  "138",   # DAL
    "7":  "139",   # DEN
    "8":  "140",   # DET
    "9":  "141",   # GS
    "10": "142",   # HOU
    "11": "143",   # IND
    "12": "144",   # LAC
    "13": "145",   # LAL
    "29": "146",   # MEM
    "14": "147",   # MIA
    "15": "148",   # MIL
    "16": "149",   # MIN
    "3":  "150",   # NO
    "18": "151",   # NY
    "25": "152",   # OKC
    "19": "153",   # ORL
    "20": "154",   # PHI
    "21": "155",   # PHX
    "22": "156",   # POR
    "23": "157",   # SAC
    "24": "158",   # SA
    "28": "159",   # TOR
    "26": "160",   # UTAH
    "27": "161",   # WSH
}


def _init_check() -> None:
    if not API_KEY:
        log.warning("API_BASKETBALL_KEY not set – NBA data will be unavailable.")


_init_check()


# ── Centralized HTTP caller ──────────────────────────────────────────────────

def _api_get(endpoint: str, params: dict | None = None) -> list:
    """
    Call API-Basketball and return the `response` array.
    Logs remaining daily quota from response headers.
    """
    if not API_KEY:
        log.warning("API-Basketball: no API key – returning empty.")
        return []
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, headers={"x-apisports-key": API_KEY},
                            params=params or {}, timeout=15)
        resp.raise_for_status()
        # Log quota periodically
        remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
        log.debug("API-Basketball: %s → %s remaining reqs", endpoint, remaining)
        data = resp.json()
        errors = data.get("errors")
        if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and len(errors) > 0):
            log.error("API-Basketball errors for %s: %s", endpoint, errors)
            return []
        return data.get("response", [])
    except Exception as exc:
        log.warning("API-Basketball fetch failed for %s: %s", endpoint, exc)
        return []


# ── Date helpers ─────────────────────────────────────────────────────────────

def _normalize_date(date_str: str) -> str:
    """Accept YYYYMMDD or YYYY-MM-DD, return YYYY-MM-DD."""
    date_str = date_str.strip()
    if len(date_str) == 8 and "-" not in date_str:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def _flip_name(name: str) -> str:
    """Convert 'Last First' to 'First Last'. Handles 'A. Williams' unchanged."""
    if not name:
        return name
    parts = name.split(" ", 1)
    if len(parts) == 2 and not parts[0].endswith("."):
        return f"{parts[1]} {parts[0]}"
    return name


# ── Status translation ───────────────────────────────────────────────────────

_QUARTER_DETAILS = {
    1: "1st Quarter", 2: "2nd Quarter", 3: "3rd Quarter", 4: "4th Quarter",
}


def _translate_status(status: dict) -> dict:
    """
    Convert API-Basketball status to ESPN format.
    API-Basketball: {"long": "...", "short": "NS|Q1|Q2|Q3|Q4|HT|OT|BT|FT|AOT|POST|CANC", "timer": null}
    ESPN format:    {"type": {"state": "pre|in|post", "detail": "..."}, "period": int, "displayClock": ""}
    """
    short = (status.get("short") or "NS").upper()
    timer = status.get("timer")

    if short == "NS":
        return {
            "type": {"state": "pre", "detail": status.get("long", "Scheduled")},
            "period": 0,
            "displayClock": "",
        }
    elif short in ("Q1", "Q2", "Q3", "Q4"):
        period = int(short[1])
        detail = _QUARTER_DETAILS.get(period, f"Q{period}")
        display_clock = ""
        if timer is not None:
            try:
                display_clock = str(timer)
            except Exception:
                pass
        return {
            "type": {"state": "in", "detail": detail},
            "period": period,
            "displayClock": display_clock,
        }
    elif short == "HT":
        return {
            "type": {"state": "in", "detail": "Halftime"},
            "period": 2,
            "displayClock": "",
        }
    elif short == "OT":
        return {
            "type": {"state": "in", "detail": "Overtime"},
            "period": 5,
            "displayClock": "",
        }
    elif short == "BT":
        return {
            "type": {"state": "in", "detail": "Break"},
            "period": 0,
            "displayClock": "",
        }
    elif short in ("FT", "AOT"):
        detail = "Final" if short == "FT" else "Final/OT"
        return {
            "type": {"state": "post", "detail": detail},
            "period": 4 if short == "FT" else 5,
            "displayClock": "0:00",
        }
    elif short == "POST":
        return {
            "type": {"state": "post", "detail": "Postponed"},
            "period": 0,
            "displayClock": "",
        }
    elif short == "CANC":
        return {
            "type": {"state": "post", "detail": "Cancelled"},
            "period": 0,
            "displayClock": "",
        }
    else:
        # Unknown status — treat as in-progress to be safe
        return {
            "type": {"state": "in", "detail": status.get("long", short)},
            "period": 0,
            "displayClock": "",
        }


# ── Team helper ──────────────────────────────────────────────────────────────

def _make_team_obj(team: dict) -> dict:
    """Build ESPN-format team object from API-Basketball team dict."""
    tid = team.get("id", 0)
    mapped = _TEAM_MAP.get(tid, {})
    return {
        "id": str(tid),
        "name": mapped.get("name", team.get("name", "")),
        "displayName": mapped.get("displayName", team.get("name", "")),
        "abbreviation": mapped.get("abbreviation", "???"),
        "logo": team.get("logo", ""),
    }


# ── Games (scoreboard) ──────────────────────────────────────────────────────

def fetch_games(date_str: str, league: str = "nba") -> list[dict]:
    """
    Fetch games for a date and league, return in ESPN event format.
    date_str can be YYYYMMDD or YYYY-MM-DD.
    league: "nba", "euroleague", etc.
    """
    cfg = LEAGUES.get(league, LEAGUES["nba"])
    date_api = _normalize_date(date_str)
    games = _api_get("games", {
        "date": date_api,
        "league": cfg["id"],
        "season": cfg["season"],
    })
    return [_transform_game(g, league=league) for g in games]


def _transform_game(game: dict, league: str = "nba") -> dict:
    """Transform one API-Basketball game into ESPN event format."""
    game_id = str(game.get("id", ""))
    date_str = game.get("date", "")

    # Teams
    teams = game.get("teams", {})
    home_raw = teams.get("home", {})
    away_raw = teams.get("away", {})
    home_team = _make_team_obj(home_raw)
    away_team = _make_team_obj(away_raw)

    # Scores
    scores = game.get("scores", {})
    home_total = scores.get("home", {}).get("total")
    away_total = scores.get("away", {}).get("total")
    home_score = str(home_total) if home_total is not None else "0"
    away_score = str(away_total) if away_total is not None else "0"

    # Status
    status = _translate_status(game.get("status", {}))

    # Name
    short_name = f"{away_team['abbreviation']} @ {home_team['abbreviation']}"
    full_name = f"{away_team['displayName']} at {home_team['displayName']}"

    return {
        "id": game_id,
        "date": date_str,
        "name": full_name,
        "shortName": short_name,
        "league": league,
        "status": status,
        "competitions": [{
            "status": status,
            "competitors": [
                {
                    "homeAway": "home",
                    "score": home_score,
                    "team": home_team,
                },
                {
                    "homeAway": "away",
                    "score": away_score,
                    "team": away_team,
                },
            ],
        }],
    }


# ── Player boxscore ─────────────────────────────────────────────────────────

_boxscore_cache: dict[str, tuple[float, dict]] = {}
_boxscore_lock = threading.Lock()
_BOXSCORE_TTL = 9  # seconds (slightly less than poll interval)


def fetch_player_boxscore(game_id: str) -> dict | None:
    """
    Fetch player statistics for a game and return in ESPN boxscore format.
    Cached for 55s so poller and radar share one HTTP call.

    Note: API-Basketball does NOT provide steals/blocks/turnovers per player.
    Those fields are set to 0. Triple-double detection uses PTS/REB/AST only.
    """
    # Check cache
    with _boxscore_lock:
        cached = _boxscore_cache.get(game_id)
        if cached and time.time() - cached[0] < _BOXSCORE_TTL:
            return cached[1]

    players = _api_get("games/statistics/players", {"id": game_id})
    if not players:
        return None

    # Group players by team
    teams_data: dict[str, list] = {}
    for p in players:
        team_id = str(p.get("team", {}).get("id", ""))
        if team_id not in teams_data:
            teams_data[team_id] = []
        teams_data[team_id].append(p)

    # Build ESPN boxscore format
    stat_names = ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PF", "PTS", "3PM"]
    players_sections = []

    for team_id, team_players in teams_data.items():
        athletes = []
        for p in team_players:
            player_info = p.get("player", {})
            raw_name = player_info.get("name", "?")
            display_name = _flip_name(raw_name)

            # Extract stats
            pts = p.get("points", 0) or 0
            reb = (p.get("rebounds") or {}).get("total", 0) or 0
            ast = p.get("assists", 0) or 0
            minutes = p.get("minutes", "0:00") or "0:00"

            fg_made = (p.get("field_goals") or {}).get("total", 0) or 0
            fg_att = (p.get("field_goals") or {}).get("attempts", 0) or 0
            tp_made = (p.get("threepoint_goals") or {}).get("total", 0) or 0
            tp_att = (p.get("threepoint_goals") or {}).get("attempts", 0) or 0
            ft_made = (p.get("freethrows_goals") or {}).get("total", 0) or 0
            ft_att = (p.get("freethrows_goals") or {}).get("attempts", 0) or 0

            # STL, BLK, TO, PF not available from API-Basketball
            stats = [
                minutes,                          # MIN
                f"{fg_made}-{fg_att}",            # FG
                f"{tp_made}-{tp_att}",            # 3PT
                f"{ft_made}-{ft_att}",            # FT
                str(reb),                          # REB
                str(ast),                          # AST
                "0",                               # STL (unavailable)
                "0",                               # BLK (unavailable)
                "0",                               # TO (unavailable)
                "0",                               # PF (unavailable)
                str(pts),                          # PTS
                str(tp_made),                      # 3PM
            ]

            athletes.append({
                "athlete": {
                    "id": str(player_info.get("id", "")),
                    "displayName": display_name,
                },
                "stats": stats,
                "starter": p.get("type") == "starters",
            })

        players_sections.append({
            "team": {"id": team_id},
            "statistics": [{
                "names": stat_names,
                "athletes": athletes,
            }],
        })

    result = {"boxscore": {"players": players_sections}}

    # Cache
    with _boxscore_lock:
        _boxscore_cache[game_id] = (time.time(), result)

    return result


# ── Teams & Rosters ──────────────────────────────────────────────────────────

def fetch_all_teams(league: str = "nba") -> list[dict]:
    """
    Fetch all teams for a league from API-Basketball.
    Returns list of dicts matching the _ROSTER_CACHE schema:
    {id, name, abbreviation, logo, players: []}
    """
    cfg = LEAGUES.get(league, LEAGUES["nba"])
    teams = _api_get("teams", {"league": cfg["id"], "season": cfg["season"]})
    result = []
    for t in teams:
        tid = t.get("id", 0)
        if tid not in _TEAM_MAP:
            continue  # skip non-NBA teams (e.g. Team World = 1414)
        mapped = _TEAM_MAP[tid]
        result.append({
            "id": str(tid),
            "name": mapped["displayName"],
            "abbreviation": mapped["abbreviation"],
            "logo": t.get("logo", ""),
            "players": [],  # populated separately via fetch_team_players
        })
    return result


def fetch_team_players(team_id: int | str, league: str = "nba") -> list[dict]:
    """
    Fetch roster for a team. Returns list matching the player schema:
    {id, name, jersey, position}
    Uses player_season if defined (Euroleague uses different season format for players).
    """
    cfg = LEAGUES.get(league, LEAGUES["nba"])
    season = cfg.get("player_season", cfg["season"])
    players = _api_get("players", {
        "team": int(team_id),
        "season": season,
    })
    result = []
    for p in players:
        position = p.get("position") or ""
        if not position:
            continue
        number = p.get("number")
        raw_name = p.get("name", "?")
        display_name = _flip_name(raw_name)
        pos_map = {"Guard": "G", "Forward": "F", "Center": "C",
                   "Guard-Forward": "G-F", "Forward-Guard": "F-G",
                   "Forward-Center": "F-C", "Center-Forward": "C-F"}
        pos_abbr = pos_map.get(position, position)

        result.append({
            "id": str(p.get("id", "")),
            "name": display_name,
            "jersey": str(number) if number is not None else "",
            "position": pos_abbr,
        })
    return result


# ── Player ID lookup (for migration) ────────────────────────────────────────

def search_player_id(player_name: str) -> str | None:
    """
    Search for a player by name and return their API-Basketball ID.
    Used for migrating ESPN player IDs to API-Basketball IDs.
    """
    # Try search endpoint
    players = _api_get("players", {"search": player_name, "league": NBA_LEAGUE_ID, "season": SEASON})
    if not players:
        # Try with just last name
        parts = player_name.strip().split()
        if len(parts) > 1:
            players = _api_get("players", {"search": parts[-1], "league": NBA_LEAGUE_ID, "season": SEASON})
    if players:
        # Find best match
        name_lower = player_name.lower()
        for p in players:
            api_name = _flip_name(p.get("name", "")).lower()
            if api_name == name_lower:
                return str(p["id"])
        # If no exact match, return first result
        return str(players[0]["id"])
    return None


# ── Validate adapter output ─────────────────────────────────────────────────

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
