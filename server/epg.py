"""
Clutch Alarm – EPG (Electronic Program Guide) Service
=========================================================

Fetches TV channel schedules from iptv-epg.org and matches NBA games
to broadcast channels. Supports multiple countries and languages.
"""

import time
import logging
import threading
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

log = logging.getLogger("nba_epg")

# ── EPG source ────────────────────────────────────────────────────────────────

EPG_BASE_URL = "https://iptv-epg.org/files/epg-{country}.xml"

# Cache: country → { "channels": {...}, "programmes": [...], "fetched_at": float }
_epg_cache: dict[str, dict] = {}
_epg_lock = threading.Lock()
_EPG_CACHE_TTL = 3600  # 1 hour

# ── NBA team name mappings (English + Hebrew + Spanish + more) ────────────────

# Maps various team name fragments (lowercased) to ESPN team abbreviation
TEAM_NAME_MAP = {
    # English
    "hawks": "ATL", "atlanta": "ATL",
    "celtics": "BOS", "boston": "BOS",
    "nets": "BKN", "brooklyn": "BKN",
    "hornets": "CHA", "charlotte": "CHA",
    "bulls": "CHI", "chicago": "CHI",
    "cavaliers": "CLE", "cleveland": "CLE", "cavs": "CLE",
    "mavericks": "DAL", "dallas": "DAL", "mavs": "DAL",
    "nuggets": "DEN", "denver": "DEN",
    "pistons": "DET", "detroit": "DET",
    "warriors": "GS", "golden state": "GS",
    "rockets": "HOU", "houston": "HOU",
    "pacers": "IND", "indiana": "IND",
    "clippers": "LAC", "la clippers": "LAC",
    "lakers": "LAL", "la lakers": "LAL", "los angeles lakers": "LAL",
    "grizzlies": "MEM", "memphis": "MEM",
    "heat": "MIA", "miami": "MIA",
    "bucks": "MIL", "milwaukee": "MIL",
    "timberwolves": "MIN", "minnesota": "MIN", "wolves": "MIN",
    "pelicans": "NO", "new orleans": "NO",
    "knicks": "NY", "new york": "NY",
    "thunder": "OKC", "oklahoma": "OKC", "oklahoma city": "OKC",
    "magic": "ORL", "orlando": "ORL",
    "76ers": "PHI", "philadelphia": "PHI", "sixers": "PHI",
    "suns": "PHX", "phoenix": "PHX",
    "trail blazers": "POR", "blazers": "POR", "portland": "POR",
    "kings": "SAC", "sacramento": "SAC",
    "spurs": "SA", "san antonio": "SA",
    "raptors": "TOR", "toronto": "TOR",
    "jazz": "UTAH", "utah": "UTAH",
    "wizards": "WSH", "washington": "WSH",
    # Hebrew
    "הוקס": "ATL", "אטלנטה": "ATL",
    "סלטיקס": "BOS", "בוסטון": "BOS",
    "נטס": "BKN", "ברוקלין": "BKN",
    "הורנטס": "CHA", "שרלוט": "CHA",
    "בולס": "CHI", "שיקגו": "CHI",
    "קאבלירס": "CLE", "קליבלנד": "CLE",
    "מאבריקס": "DAL", "דאלאס": "DAL",
    "נאגטס": "DEN", "דנבר": "DEN",
    "פיסטונס": "DET", "דטרויט": "DET",
    "ווריירס": "GS", "ווריורס": "GS", "גולדן סטייט": "GS",
    "רוקטס": "HOU", "יוסטון": "HOU", "הוסטון": "HOU",
    "פייסרס": "IND", "אינדיאנה": "IND",
    "קליפרס": "LAC",
    "לייקרס": "LAL", "ל.א. לייקרס": "LAL", "לוס אנג'לס לייקרס": "LAL",
    "גריזליס": "MEM", "ממפיס": "MEM",
    "היט": "MIA", "מיאמי": "MIA",
    "באקס": "MIL", "מילווקי": "MIL",
    "טימברוולבס": "MIN", "מינסוטה": "MIN",
    "פליקנס": "NO", "ניו אורלינס": "NO",
    "ניקס": "NY", "ניו יורק": "NY",
    "ת'אנדר": "OKC", "תאנדר": "OKC", "אוקלהומה סיטי": "OKC", "אוקלהומה": "OKC",
    "מאג'יק": "ORL", "אורלנדו": "ORL",
    "סיקסרס": "PHI", "פילדלפיה": "PHI",
    "סאנס": "PHX", "פיניקס": "PHX", "פניקס": "PHX",
    "בלייזרס": "POR", "פורטלנד": "POR",
    "קינגס": "SAC", "סקרמנטו": "SAC",
    "ספרס": "SA", "סן אנטוניו": "SA",
    "רפטורס": "TOR", "טורנטו": "TOR",
    "ג'אז": "UTAH", "יוטה": "UTAH",
    "וויזארדס": "WSH", "וושינגטון": "WSH",
    # Spanish
    "halcones": "ATL", "toros": "CHI", "cohetes": "HOU",
    "magos": "ORL", "soles": "PHX", "reyes": "SAC",
}


def _match_teams(text: str) -> list[str]:
    """Extract NBA team abbreviations from a programme title/description."""
    text_lower = text.lower()
    found = []
    # Sort by length (longest first) to match "golden state" before "state"
    for name, abbr in sorted(TEAM_NAME_MAP.items(), key=lambda x: -len(x[0])):
        if name in text_lower and abbr not in found:
            found.append(abbr)
        if len(found) >= 2:
            break
    return found


def _is_nba_programme(title: str, desc: str) -> bool:
    """Check if a programme is an NBA game (not just NBA-related show)."""
    combined = title + " " + desc
    combined_lower = combined.lower()
    # Reject any non-basketball sport immediately
    non_nba = ["hockey", "הוקי", "nhl", "כדורגל", "soccer", "football", "nfl",
               "baseball", "mlb", "tennis", "rugby", "cricket", "golf"]
    if any(s in combined_lower for s in non_nba):
        return False
    # Must mention NBA or basketball (Hebrew: כדורסל). "ליגת" alone is too broad.
    if "nba" not in combined_lower and "כדורסל" not in combined_lower and "ליגת nba" not in combined_lower:
        return False
    # Must have at least one team name
    if not _match_teams(combined):
        return False
    # Exclude non-game content (replays, highlights, magazine shows)
    exclude = ["daily", "אקשן", "מגזין", "highlight", "recap", "הרגעים", "בד בויז", "סרט",
               "שידור חוזר", "מקוצר", "תקציר", "replay", "rebroadcast", "condensed"]
    if any(ex in combined_lower for ex in exclude):
        return False
    return True


# ── Channel name normalization + free/paid classification ─────────────────────

# Maps raw EPG channel IDs/names to clean display names + free status
# Israel Sport 5 channels
_CHANNEL_MAP_IL = {
    "5sportHD.il":  {"en": "Sport 5",        "he": "ספורט 5",          "free": True},
    "5PLUSHD.il":   {"en": "Sport 5 Plus",   "he": "ספורט 5 פלוס",    "free": True},
    "5LIVEHD.il":   {"en": "Sport 5 Live",   "he": "ספורט 5 לייב",    "free": False},
    "5STARS.il":    {"en": "Sport 5 Stars",  "he": "ספורט 5 סטארס",   "free": False},
    "5GOLD.il":     {"en": "Sport 5 Gold",   "he": "ספורט 5 גולד",    "free": False},
    "5SPORT4K.il":  {"en": "Sport 5 4K",     "he": "ספורט 5 4K",      "free": False},
    "ספורט1HD.il":  {"en": "Sport 1",        "he": "ספורט 1",          "free": False},
    "ספורט2HD.il":  {"en": "Sport 2",        "he": "ספורט 2",          "free": False},
    "ספורט3HD.il":  {"en": "Sport 3",        "he": "ספורט 3",          "free": False},
    "ספורט4HD.il":  {"en": "Sport 4",        "he": "ספורט 4",          "free": False},
    "ספורט1SD.il":  {"en": "Sport 1",        "he": "ספורט 1",          "free": False},
}

# Generic name cleaning rules (applied after map lookup)
_NAME_CLEANUP = [
    (" HD", ""), (" SD", ""), (" hd", ""), (" sd", ""),
    ("IL - ", ""), ("US - ", ""), ("GB - ", ""),
]


def _normalize_channel(channel_id: str, raw_name: str, country: str) -> tuple[str, bool]:
    """
    Returns (clean_display_name, is_free).
    Uses channel map for known channels, falls back to generic cleanup.
    """
    # Check country-specific map
    if country == "il" and channel_id in _CHANNEL_MAP_IL:
        info = _CHANNEL_MAP_IL[channel_id]
        return info.get("en", raw_name), info.get("free", False)

    # Generic cleanup
    name = raw_name
    for old, new in _NAME_CLEANUP:
        name = name.replace(old, new)
    name = name.strip()

    # Default: not free
    return name, False


def _fetch_epg(country: str) -> dict:
    """Fetch and parse EPG XML for a country. Returns parsed data."""
    url = EPG_BASE_URL.format(country=country)
    log.info("EPG: fetching %s", url)
    try:
        resp = requests.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("EPG: fetch failed for %s: %s", country, exc)
        return {"channels": {}, "programmes": []}

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        log.warning("EPG: XML parse failed for %s: %s", country, exc)
        return {"channels": {}, "programmes": []}

    # Parse channels
    channels = {}
    for ch in root.findall("channel"):
        cid = ch.get("id", "")
        name_el = ch.find("display-name")
        icon_el = ch.find("icon")
        name = (name_el.text or "") if name_el is not None else ""
        icon = (icon_el.get("src", "") if icon_el is not None else "")
        # Remove "XX - " country prefix from display name
        if name and " - " in name:
            name = name.split(" - ", 1)[1]
        # Clean and normalize channel name
        clean_name, is_free = _normalize_channel(cid, name, country)
        # Get Hebrew name if available
        he_name = ""
        if country == "il" and cid in _CHANNEL_MAP_IL:
            he_name = _CHANNEL_MAP_IL[cid].get("he", "")
        channels[cid] = {"name": clean_name, "name_he": he_name, "icon": icon, "is_free": is_free}

    # Parse NBA programmes
    programmes = []
    for prog in root.findall("programme"):
        channel_id = prog.get("channel", "")
        start_str = prog.get("start", "")
        stop_str = prog.get("stop", "")
        title_el = prog.find("title")
        desc_el = prog.find("desc")
        title = (title_el.text or "") if title_el is not None else ""
        desc = (desc_el.text or "") if desc_el is not None else ""

        if not _is_nba_programme(title, desc):
            continue

        teams = _match_teams(title + " " + desc)
        is_live = "ישיר" in title or "live" in title.lower() or "שידור ישיר" in desc

        # Parse start time
        start_dt = None
        start_utc = ""
        try:
            # Format: "20260325230000 +0000"
            dt_str = start_str.split()[0]
            tz_str = start_str.split()[1] if len(start_str.split()) > 1 else "+0000"
            dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
            tz_offset = int(tz_str[:3]) * 60 + int(tz_str[0] + tz_str[3:5])
            dt = dt.replace(tzinfo=timezone(timedelta(minutes=tz_offset)))
            start_dt = dt.astimezone(timezone.utc)
            start_utc = start_dt.isoformat()
        except (ValueError, IndexError):
            pass

        ch_info = channels.get(channel_id, {})
        programmes.append({
            "channel_id": channel_id,
            "channel_name": ch_info.get("name", ""),
            "channel_name_he": ch_info.get("name_he", ""),
            "channel_icon": ch_info.get("icon", ""),
            "is_free": ch_info.get("is_free", False),
            "title": title,
            "start_utc": start_utc,
            "start_dt": start_dt,
            "is_live": is_live,
            "teams": teams,
        })

    log.info("EPG: %s — %d channels, %d NBA programmes", country, len(channels), len(programmes))
    return {"channels": channels, "programmes": programmes}


def get_epg(country: str) -> dict:
    """Get cached EPG data for a country."""
    country = country.lower().strip()
    with _epg_lock:
        cached = _epg_cache.get(country)
        if cached and time.time() - cached.get("fetched_at", 0) < _EPG_CACHE_TTL:
            return cached

    data = _fetch_epg(country)
    data["fetched_at"] = time.time()
    with _epg_lock:
        _epg_cache[country] = data
    return data


def _parse_game_date(game_date_utc: str) -> datetime | None:
    """Parse ESPN's game date (ISO format) to a timezone-aware datetime."""
    if not game_date_utc:
        return None
    try:
        # "2026-03-25T23:00Z" or "2026-03-25T23:00:00Z"
        dt_str = game_date_utc.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return None


def get_broadcasts_for_game(game_id: str, game_name: str, home_abbr: str, away_abbr: str,
                            game_date_utc: str, country: str = "il") -> list[dict]:
    """
    Find TV broadcasts for a specific NBA game.
    Matches by team names AND time window (broadcast must start within
    1 hour before to 3 hours after game start).
    """
    epg = get_epg(country)
    programmes = epg.get("programmes", [])

    target_teams = {home_abbr.upper(), away_abbr.upper()}
    game_dt = _parse_game_date(game_date_utc)

    matches = []
    seen_channels = set()

    for prog in programmes:
        prog_teams = set(prog.get("teams", []))
        # At least one team must match
        if not prog_teams.intersection(target_teams):
            continue

        # Time filter: broadcast must start within 30 min of game time
        prog_dt = prog.get("start_dt")
        if game_dt and prog_dt:
            diff = abs((prog_dt - game_dt).total_seconds())
            if diff > 1800:  # 30 minutes
                continue

        channel_name = prog.get("channel_name", "")
        if channel_name in seen_channels:
            continue
        seen_channels.add(channel_name)

        matches.append({
            "channel_name": channel_name,
            "channel_name_he": prog.get("channel_name_he", ""),
            "channel_icon": prog.get("channel_icon", ""),
            "is_free": prog.get("is_free", False),
            "is_live": prog.get("is_live", False),
            "title": prog.get("title", ""),
            "start_utc": prog.get("start_utc", ""),
        })

    # If any match is a live broadcast, drop non-live ones (replays, condensed games)
    if any(m["is_live"] for m in matches):
        matches = [m for m in matches if m["is_live"]]

    # Sort: free first, then by start time
    matches.sort(key=lambda x: (not x["is_free"], x.get("start_utc", "")))
    return matches


def get_all_broadcasts(country: str = "il", games: list[dict] | None = None) -> dict[str, list[dict]]:
    """
    Get NBA broadcasts matched to today's actual games.
    If `games` is provided (ESPN game list), matches precisely by teams + time.
    Returns: { "game_id": [{"channel_name": ..., ...}], ... }
    """
    if games:
        return _match_broadcasts_to_games(games, country)

    # Fallback: group by team pair without game-specific matching
    epg = get_epg(country)
    programmes = epg.get("programmes", [])

    # Only include programmes from today (UTC date)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_programmes = [
        p for p in programmes
        if p.get("start_utc", "").startswith(today_str)
    ]

    by_game: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}

    for prog in today_programmes:
        teams = prog.get("teams", [])
        if len(teams) < 1:
            continue
        game_key = "_".join(sorted(teams[:2])) if len(teams) >= 2 else teams[0]

        if game_key not in by_game:
            by_game[game_key] = []
            seen[game_key] = set()

        channel_name = prog.get("channel_name", "")
        if channel_name in seen[game_key]:
            continue
        seen[game_key].add(channel_name)

        by_game[game_key].append({
            "channel_name": channel_name,
            "channel_name_he": prog.get("channel_name_he", ""),
            "channel_icon": prog.get("channel_icon", ""),
            "is_free": prog.get("is_free", False),
            "is_live": prog.get("is_live", False),
            "title": prog.get("title", ""),
            "start_utc": prog.get("start_utc", ""),
        })

    for key in by_game:
        by_game[key].sort(key=lambda x: (not x["is_free"], not x["is_live"], x.get("start_utc", "")))

    return by_game


def _match_broadcasts_to_games(games: list[dict], country: str) -> dict[str, list[dict]]:
    """Match EPG programmes to actual ESPN games by teams + time window."""
    result: dict[str, list[dict]] = {}

    for game in games:
        game_id = str(game.get("id", ""))
        comp = (game.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home_abbr = ""
        away_abbr = ""
        for c in competitors:
            team = c.get("team", {})
            abbr = team.get("abbreviation", "")
            if c.get("homeAway") == "home":
                home_abbr = abbr
            else:
                away_abbr = abbr

        game_date = game.get("date", "")
        broadcasts = get_broadcasts_for_game(
            game_id=game_id,
            game_name=game.get("shortName", ""),
            home_abbr=home_abbr,
            away_abbr=away_abbr,
            game_date_utc=game_date,
            country=country,
        )
        if broadcasts:
            result[game_id] = broadcasts

    return result
