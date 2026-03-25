"""
NBA Smart Alarm – EPG (Electronic Program Guide) Service
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
    # Must mention NBA
    if "nba" not in combined_lower and "ליגת" not in combined and "כדורסל" not in combined:
        return False
    # Must have at least one team name
    if not _match_teams(combined):
        return False
    # Exclude non-game content
    exclude = ["daily", "אקשן", "מגזין", "highlight", "recap", "הרגעים", "בד בויז", "סרט"]
    if any(ex in combined_lower for ex in exclude):
        return False
    return True


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
        channels[cid] = {"name": name, "icon": icon}

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
        try:
            # Format: "20260325230000 +0000"
            dt_str = start_str.split()[0]
            tz_str = start_str.split()[1] if len(start_str.split()) > 1 else "+0000"
            dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
            tz_offset = int(tz_str[:3]) * 60 + int(tz_str[0] + tz_str[3:5])
            dt = dt.replace(tzinfo=timezone(timedelta(minutes=tz_offset)))
            start_utc = dt.astimezone(timezone.utc).isoformat()
        except (ValueError, IndexError):
            start_utc = ""

        ch_info = channels.get(channel_id, {})
        programmes.append({
            "channel_id": channel_id,
            "channel_name": ch_info.get("name", ""),
            "channel_icon": ch_info.get("icon", ""),
            "title": title,
            "start_utc": start_utc,
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


def get_broadcasts_for_game(game_id: str, game_name: str, home_abbr: str, away_abbr: str,
                            game_date_utc: str, country: str = "il") -> list[dict]:
    """
    Find TV broadcasts for a specific NBA game.

    Returns a list of channels showing the game:
    [{"channel_name": "5sport HD", "channel_icon": "https://...", "is_live": true, "title": "..."}]
    """
    epg = get_epg(country)
    programmes = epg.get("programmes", [])

    # Match by team abbreviations
    target_teams = {home_abbr.upper(), away_abbr.upper()}
    matches = []
    seen_channels = set()

    for prog in programmes:
        prog_teams = set(prog.get("teams", []))
        # At least one team must match (both is better)
        if not prog_teams.intersection(target_teams):
            continue

        channel_name = prog.get("channel_name", "")
        # Deduplicate by channel (show the first/live broadcast per channel)
        if channel_name in seen_channels:
            continue
        seen_channels.add(channel_name)

        matches.append({
            "channel_name": channel_name,
            "channel_icon": prog.get("channel_icon", ""),
            "is_live": prog.get("is_live", False),
            "title": prog.get("title", ""),
            "start_utc": prog.get("start_utc", ""),
        })

    # Sort: live broadcasts first, then by channel name
    matches.sort(key=lambda x: (not x["is_live"], x["channel_name"]))
    return matches


def get_all_broadcasts(country: str = "il") -> dict[str, list[dict]]:
    """
    Get all NBA broadcasts grouped by team pair.
    Returns: { "ATL_DET": [{"channel_name": ..., ...}], ... }
    """
    epg = get_epg(country)
    programmes = epg.get("programmes", [])

    by_game: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}  # game_key → set of channel names

    for prog in programmes:
        teams = prog.get("teams", [])
        if len(teams) < 1:
            continue
        # Use sorted team abbreviations as key
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
            "channel_icon": prog.get("channel_icon", ""),
            "is_live": prog.get("is_live", False),
            "title": prog.get("title", ""),
            "start_utc": prog.get("start_utc", ""),
        })

    # Sort each game's broadcasts: live first
    for key in by_game:
        by_game[key].sort(key=lambda x: (not x["is_live"], x["channel_name"]))

    return by_game
