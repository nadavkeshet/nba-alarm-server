"""Soccer-side adapter over the existing 365scores client.

Mirrors the basketball functions in `score365_adapter.py` but for football
(`sport_id=1`). Loads the league IDs from `config/competition_map.json` so the
codebase has a single source of truth.

The shape of dicts returned by these functions matches what 365scores returns;
normalization to internal models happens in `monitor.py` / `main.py` consumers.
"""

import json
from pathlib import Path

from score365.client import Client365
from score365.scraper import (
    FOOTBALL_SPORT_ID,
    get_games_by_date,
    get_game_detail,
    get_all_competitions,
)

# ---------------------------------------------------------------------------
# Competition map (loaded once at import time)

_MAP_PATH = Path(__file__).resolve().parent / "config" / "competition_map.json"


def _load_map() -> dict[str, dict]:
    if not _MAP_PATH.exists():
        return {}
    return json.loads(_MAP_PATH.read_text())


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

# slug → Sofascore unique-tournament id (covers all 10; primary for the 2 BSD doesn't)
SOFASCORE_COMPETITION_IDS: dict[str, int] = {
    slug: entry["sofascore_id"]
    for slug, entry in _COMPETITION_MAP.items()
    if entry.get("sofascore_id")
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
]


# ---------------------------------------------------------------------------
# Public adapter functions

def soccer_games_by_date(start: str, end: str | None = None) -> list[dict]:
    """Get all football games on a date range.

    Dates are MM/DD/YYYY strings (per 365scores convention).
    Returns the `games` list from the response (caller can filter by
    `competitionId` against `COMPETITION_IDS`).
    """
    with Client365() as c:
        data = get_games_by_date(c, start, end, sport_id=FOOTBALL_SPORT_ID)
        return data.get("games", [])


def soccer_game_detail(game_id: int) -> dict:
    """Get full football game detail (events, lineups, stages)."""
    with Client365() as c:
        return get_game_detail(c, game_id)


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
            unique.append(r)
    return unique
