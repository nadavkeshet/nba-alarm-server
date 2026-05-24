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
    "nba":           103,
    "euroleague":    569,
    "eurocup":       329,
    "bcl":          6391,
    "winner_league":  47,
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
    "eurocup": {
        "id": 329,
        "name": "EuroCup",
        "country": "Europe",
        "periods": 4,
        "period_minutes": 10,
        "ot_minutes": 5,
    },
    "bcl": {
        "id": 6391,
        "name": "FIBA Champions League",
        "country": "Europe",
        "periods": 4,
        "period_minutes": 10,
        "ot_minutes": 5,
    },
    "winner_league": {
        "id": 47,
        "name": "Winner League",
        "country": "Israel",
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

# International (non-NBA) team-id → canonical abbreviation map.
# Covers Euroleague, EuroCup, FIBA BCL, and Winner League. The same 365scores
# team id resolves to the same canonical abbreviation regardless of which
# competition it appears in (Hapoel TA plays in Euroleague + EuroCup +
# Winner League under id 654 → always "HTA"). The id-keyed lookup also
# resolves the PAR collision (Partizan vs Paris Basketball).
_INTL_TEAM_ID_TO_ABBR: dict[int, str] = {
    # ── Euroleague ───────────────────────────────────────────────────────
    966:   "EFE",   # Anadolu Efes      (365: EFS)
    16001: "MON",   # AS Monaco         (365: ASM)
    543:   "LYN",   # ASVEL             (365: ASV)
    206:   "BAR",   # FC Barcelona
    8648:  "BAY",   # Bayern Munich
    6349:  "BAS",   # Baskonia          (365: BKN)
    5528:  "CZV",   # Crvena Zvezda
    83343: "DUB",   # Dubai BC
    970:   "FEN",   # Fenerbahce
    275:   "MLN",   # Olimpia Milano
    1042:  "OLY",   # Olympiacos
    1041:  "PAN",   # Panathinaikos     (365: PAO)
    55269: "PAR",   # Paris Basketball  (365: "Paris B.")
    5509:  "PAT",   # Partizan          (365: PAR — collides w/ Paris)
    204:   "RMA",   # Real Madrid
    208:   "VAL",   # Valencia Basket
    288:   "VIR",   # Virtus Bologna
    5506:  "ZAL",   # Zalgiris Kaunas

    # ── EuroCup ──────────────────────────────────────────────────────────
    17443: "BOU",   # JL Bourg
    967:   "BES",   # Besiktas
    49974: "BHK",   # Bahçeşehir Koleji
    50290: "CLJ",   # Cluj Napoca
    5520:  "BUD",   # KK Budućnost
    5510:  "CED",   # KK Cedevita Olimpija
    217:   "MAN",   # Baxi Manresa
    6951:  "RVN",   # Reyer Venezia
    969:   "TUR",   # Turk Telekom
    380:   "ULM",   # Ratiopharm Ulm

    # ── FIBA BCL ─────────────────────────────────────────────────────────
    5521:  "NYM",   # ČEZ Nymburk
    1047:  "AEK",   # AEK Athens
    27633: "PRO",   # ASP Promitheas Patras
    372:   "ALB",   # Alba Berlin
    548:   "CHO",   # Cholet
    972:   "GLT",   # Galatasaray
    212:   "GRA",   # Gran Canaria
    205:   "JOV",   # Joventut de Badalona
    9899:  "TEN",   # La Laguna Tenerife
    6384:  "TOF",   # Tofas
    210:   "UNI",   # Unicaja Málaga
    17473: "HEI",   # USC Heidelberg
    8649:  "WUR",   # Wurzburg Baskets

    # ── EuroCup (additional teams discovered from /current+/results+/fixtures) ──
    11776: "TRE",   # Dolomiti Energia Trento
    17467: "CHM",   # Bv Chemnitz 99
    11794: "LON",   # London Lions
    10590: "LKB",   # Lietkabelis
    21207: "HAM",   # Hamburg Towers
    6782:  "NEP",   # Neptūnas
    1043:  "ARI",   # Aris BSA
    12439: "SLW",   # Slask Wroclaw
    1045:  "PNI",   # Panionios

    # ── FIBA BCL (additional teams) ──
    5515:  "RYT",   # Rytas Vilnius
    542:   "LMN",   # Le Mans
    49704: "KAR",   # As Karditsas
    11781: "TRI",   # Pallacanestro Trieste
    551:   "CHL",   # Chalon/Saone
    71620: "SUB",   # KK Spartak Subotica
    9033:  "LEV",   # Levice
    978:   "MER",   # Mersin Buyuksehir
    7828:  "SZO",   # Szolnoki Olajbanyasz

    # ── Winner League (Israel) ───────────────────────────────────────────
    # MTA (631) and HTA (654) already mapped above (Euroleague block)
    634:   "JER",   # Hapoel Jerusalem
    633:   "HOL",   # Hapoel Holon
    632:   "HER",   # Bnei Herzliya
    686:   "NES",   # Ironi Ness Ziona
    635:   "RMG",   # Maccabi Ironi Ramat Gan
    10884: "GAL",   # Hapoel Galil Elyon
    641:   "ATA",   # Ironi Kiryat Ata
    27923: "BSV",   # Hapoel Be'er Sheva/Dimona
    655:   "NET",   # Elitzur Netanya
    91167: "EMK",   # Hapoel HaEmek
    640:   "RSH",   # Maccabi Rishon LeZion
    27893: "RNA",   # Maccabi Ironi Ra'anana
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
    # ── EuroCup ──
    "BOU":  ("JL Bourg",                  "Bourg"),
    "BES":  ("Besiktas",                  "Besiktas"),
    "BHK":  ("Bahçeşehir Koleji",         "Bahçeşehir"),
    "CLJ":  ("Cluj Napoca",               "Cluj"),
    "BUD":  ("KK Budućnost",              "Budućnost"),
    "CED":  ("Cedevita Olimpija",         "Cedevita"),
    "MAN":  ("Baxi Manresa",              "Manresa"),
    "RVN":  ("Reyer Venezia",             "Venezia"),
    "TUR":  ("Turk Telekom",              "Turk Telekom"),
    "ULM":  ("Ratiopharm Ulm",            "Ulm"),
    # ── FIBA BCL ──
    "NYM":  ("ČEZ Nymburk",               "Nymburk"),
    "AEK":  ("AEK Athens",                "AEK"),
    "PRO":  ("Promitheas Patras",         "Promitheas"),
    "ALB":  ("Alba Berlin",               "Alba"),
    "CHO":  ("Cholet Basket",             "Cholet"),
    "GLT":  ("Galatasaray",               "Galatasaray"),
    "GRA":  ("Gran Canaria",              "Gran Canaria"),
    "JOV":  ("Joventut Badalona",         "Joventut"),
    "TEN":  ("La Laguna Tenerife",        "Tenerife"),
    "TOF":  ("Tofas Bursa",               "Tofas"),
    "UNI":  ("Unicaja Málaga",            "Unicaja"),
    "HEI":  ("USC Heidelberg",            "Heidelberg"),
    "WUR":  ("Wurzburg Baskets",          "Wurzburg"),
    # ── EuroCup additional ──
    "TRE":  ("Dolomiti Energia Trento",   "Trento"),
    "CHM":  ("Bv Chemnitz 99",            "Chemnitz"),
    "LON":  ("London Lions",              "London"),
    "LKB":  ("Lietkabelis",               "Lietkabelis"),
    "HAM":  ("Hamburg Towers",            "Hamburg"),
    "NEP":  ("Neptūnas",                  "Neptūnas"),
    "ARI":  ("Aris BSA",                  "Aris"),
    "SLW":  ("Slask Wroclaw",             "Slask"),
    "PNI":  ("Panionios",                 "Panionios"),
    # ── BCL additional ──
    "RYT":  ("Rytas Vilnius",             "Rytas"),
    "LMN":  ("Le Mans",                   "Le Mans"),
    "KAR":  ("As Karditsas",              "Karditsas"),
    "TRI":  ("Pallacanestro Trieste",     "Trieste"),
    "CHL":  ("Chalon/Saone",              "Chalon"),
    "SUB":  ("KK Spartak Subotica",       "Subotica"),
    "LEV":  ("Levice",                    "Levice"),
    "MER":  ("Mersin Buyuksehir",         "Mersin"),
    "SZO":  ("Szolnoki Olajbanyasz",      "Szolnok"),
    # ── Winner League (Israel) ──
    # MTA, HTA already defined above (Euroleague block)
    "JER":  ("Hapoel Jerusalem",          "Jerusalem"),
    "HOL":  ("Hapoel Holon",              "Holon"),
    "HER":  ("Bnei Herzliya",             "Herzliya"),
    "NES":  ("Ironi Ness Ziona",          "Ness Ziona"),
    "RMG":  ("Maccabi Ironi Ramat Gan",   "Ramat Gan"),
    "GAL":  ("Hapoel Galil Elyon",        "Galil Elyon"),
    "ATA":  ("Ironi Kiryat Ata",          "Kiryat Ata"),
    "BSV":  ("Hapoel Be'er Sheva/Dimona", "Be'er Sheva"),
    "NET":  ("Elitzur Netanya",           "Netanya"),
    "EMK":  ("Hapoel HaEmek",             "HaEmek"),
    "RSH":  ("Maccabi Rishon LeZion",     "Rishon"),
    "RNA":  ("Maccabi Ironi Ra'anana",    "Ra'anana"),
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

def _365_team_crest_url(team_id: int | str | None) -> str:
    """Public 365scores CDN URL for a competitor logo. Free, no auth.
    Works for every competition 365scores covers (Euroleague, EuroCup,
    BCL, Israeli Winner League, etc.). NBA still comes from ESPN."""
    if not team_id:
        return ""
    return (
        "https://imagecache.365scores.com/image/upload/"
        f"d_Competitors:default1.png/Competitors/{team_id}"
    )


def _make_team_obj(comp: dict) -> dict:
    """Build ESPN-format team object from a 365scores competitor dict."""
    tid = comp.get("id", 0)
    # Non-NBA leagues: id-keyed lookup gives us a stable canonical abbr
    # regardless of which competition the game came from.
    abbr = _INTL_TEAM_ID_TO_ABBR.get(tid)
    if not abbr:
        raw_sym = comp.get("symbolicName", "???")
        abbr = _SYMBOL_NORMALIZE.get(raw_sym, raw_sym)
    display_name, short_name = _DISPLAY_NAMES.get(abbr, (comp.get("name", ""), comp.get("shortName", "")))
    return {
        "id":          str(comp.get("id", "")),
        "name":        short_name or comp.get("shortName", ""),
        "displayName": display_name or comp.get("name", ""),
        "abbreviation": abbr,
        "logo":        _365_team_crest_url(comp.get("id")),
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
_WINDOW_TTL = 55  # seconds — matches 60s poll interval


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


def next_game_start(leagues: list[str] | None = None) -> datetime | None:
    """
    Return the UTC datetime of the earliest upcoming (not yet started) game
    across the given leagues. Returns None if no future games are found in
    the 365scores rolling window.
    """
    if leagues is None:
        leagues = list(COMPETITION_IDS.keys())
    now_utc = datetime.now(timezone.utc)
    earliest: datetime | None = None
    for league in leagues:
        comp_id = COMPETITION_IDS.get(league)
        if comp_id is None:
            continue
        try:
            raw_games = _fetch_competition_window(comp_id)
        except Exception:
            continue
        for g in raw_games:
            raw = g.get("startTime", "")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            # statusGroup: 2=pre, 3=live, 4=post — only consider pre-game
            if g.get("statusGroup") != 2:
                continue
            if dt > now_utc and (earliest is None or dt < earliest):
                earliest = dt
    return earliest


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
_BOXSCORE_TTL = 55  # seconds — matches 60s poll interval


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
        log.warning("365scores: get_game_detail returned empty for game_id=%s", game_id)
        return None

    try:
        box = extract_box_score(game)
    except Exception as exc:
        log.warning("365scores: extract_box_score failed for %s: %s", game_id, exc)
        return None

    home_id = str(game.get("homeCompetitor", {}).get("id", ""))
    away_id = str(game.get("awayCompetitor", {}).get("id", ""))

    # Build lineup_member_id → athlete_id mapping.
    # game.members[].id  = lineup-specific member ID (used in lineups.members[].id)
    # game.members[].athleteId = canonical squad athlete ID (matches /squads/ endpoint)
    # The mobile app stores squad athlete IDs (from /game-summary roster), so we must
    # index the boxscore by athlete IDs for player alarm lookups to work.
    _member_to_athlete: dict[str, str] = {}
    for m in game.get("members", []):
        mid = str(m.get("id", ""))
        aid = str(m.get("athleteId", mid))  # fall back to mid if no athleteId
        if mid:
            _member_to_athlete[mid] = aid

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

            # Use athlete_id (squad ID) so it matches player IDs from /squads/ roster
            lineup_id = str(p["id"])
            athlete_id = _member_to_athlete.get(lineup_id, lineup_id)

            athletes.append({
                "athlete": {"id": athlete_id, "displayName": p["name"]},
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
    Fetch all teams for a league by walking the combined-window endpoints.
    Returns list of dicts: {id, name, abbreviation, logo, players: []}
    Uses the same /current + /results + /fixtures combination as the games
    fetcher so leagues on a multi-week break (e.g. Winner League playoffs)
    still surface their full team list.
    """
    comp_id = COMPETITION_IDS.get(league, COMPETITION_IDS["nba"])
    raw_games = _fetch_competition_window(comp_id)

    seen: dict[str, dict] = {}
    for raw in raw_games:
        for side in ("homeCompetitor", "awayCompetitor"):
            comp = raw.get(side, {}) or {}
            team_obj = _make_team_obj(comp)
            tid = team_obj["id"]
            if tid and tid not in seen:
                seen[tid] = {
                    "id":           tid,
                    "name":         team_obj["displayName"],
                    "abbreviation": team_obj["abbreviation"],
                    "logo":         team_obj["logo"],
                    "players":      [],
                }
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
            pos_id = pos.get("id", 0) if isinstance(pos, dict) else 0
            # pos_id 5 = Management/Coaching staff — exclude from player roster
            if pos_id == 5:
                continue
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
