"""In-process free-text search across teams, leagues, and players.

The app's redesign needs a single endpoint that returns matching teams,
leagues, and players for a query like "real madrid" → Real Madrid CF +
La Liga. We don't have a search engine, so we score in-memory against
the data we already have: the basketball roster cache (teams + players)
and the soccer team accumulator (seeded as soccer matches are fetched).

Pure in-memory. No DB persistence. Restart resets the soccer team
accumulator — the next batch of /soccer/games fetches re-seeds it.
"""

from __future__ import annotations

import threading
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Soccer team accumulator — populated by soccer_source as matches are fetched.

_SOCCER_TEAMS: dict[str, dict] = {}
_SOCCER_TEAMS_LOCK = threading.Lock()


def remember_soccer_team(team_id: str | int, name: str, *, crest: str | None = None,
                        competition: str | None = None) -> None:
    """Remember a soccer team so /search can return it.

    Called from soccer_source whenever a match payload yields a new team.
    Idempotent — duplicate calls just refresh metadata.
    """
    if team_id is None or not name:
        return
    key = str(team_id)
    with _SOCCER_TEAMS_LOCK:
        existing = _SOCCER_TEAMS.get(key) or {}
        _SOCCER_TEAMS[key] = {
            "id":          key,
            "name":        name,
            "crest":       crest or existing.get("crest"),
            "competition": competition or existing.get("competition"),
            "sport":       "football",
        }


def remember_soccer_teams_from_match(match: dict) -> None:
    """Convenience: extract home/away teams from a canonical soccer match dict."""
    if not match:
        return
    comp = match.get("competition")
    for side in ("home", "away"):
        team = match.get(side) or {}
        remember_soccer_team(
            team.get("id"),
            team.get("name") or "",
            crest=team.get("crest"),
            competition=comp,
        )


def soccer_teams_snapshot() -> list[dict]:
    with _SOCCER_TEAMS_LOCK:
        return list(_SOCCER_TEAMS.values())


# ─────────────────────────────────────────────────────────────────────────────
# Scoring


def _score(query_lower: str, target: str) -> float:
    """Lightweight similarity score in [0, 1].

    Higher is better. Returns 0 when there is no match at all.
    Heuristic: prefix match > word-start match > substring match.
    """
    if not target:
        return 0.0
    t = target.lower()
    if t == query_lower:
        return 1.0
    if t.startswith(query_lower):
        return 0.9
    # Word-start match (any word in target starts with query)
    for word in t.split():
        if word.startswith(query_lower):
            return 0.7
    if query_lower in t:
        return 0.5
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Static league catalog — mirrors the iOS LeagueCatalog so search results can
# return the same slugs the rest of the app uses.

_LEAGUES: list[dict] = [
    # Basketball
    {"id": "nba",            "name": "NBA",                       "sport": "basketball"},
    {"id": "euroleague",     "name": "EuroLeague",                "sport": "basketball"},
    {"id": "eurocup",        "name": "EuroCup",                   "sport": "basketball"},
    {"id": "bcl",            "name": "Basketball Champions League","sport": "basketball"},
    {"id": "winner_league",  "name": "Israeli Winner League",     "sport": "basketball"},
    # Football
    {"id": "premier_league", "name": "Premier League",            "sport": "football"},
    {"id": "ucl",            "name": "Champions League",          "sport": "football"},
    {"id": "la_liga",        "name": "La Liga",                   "sport": "football"},
    {"id": "serie_a",        "name": "Serie A",                   "sport": "football"},
    {"id": "bundesliga",     "name": "Bundesliga",                "sport": "football"},
    {"id": "ligue_1",        "name": "Ligue 1",                   "sport": "football"},
    {"id": "europa_league",  "name": "Europa League",             "sport": "football"},
    {"id": "world_cup",      "name": "FIFA World Cup",            "sport": "football"},
    {"id": "euros",          "name": "UEFA Euros",                "sport": "football"},
    {"id": "ligat_haal",     "name": "Israeli Ligat Ha'Al",       "sport": "football"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Public search API


def search(query: str, limit: int = 10) -> dict[str, list[dict]]:
    """Return up to `limit` results in each of teams / leagues / players.

    Output shape:
        {
          "teams":   [{id, name, sport, league?, logo?, score}],
          "leagues": [{id, name, sport, score}],
          "players": [{id, name, team_id, team_name?, sport, score}],
        }
    """
    q = (query or "").strip().lower()
    if not q or len(q) < 1:
        return {"teams": [], "leagues": [], "players": []}

    teams: list[dict] = []
    leagues: list[dict] = []
    players: list[dict] = []

    # ── Basketball teams + players (from the roster cache) ──────────────────
    try:
        from monitor import get_all_teams as _bb_teams
        for t in _bb_teams() or []:
            score_t = _score(q, t.get("name") or "")
            if score_t == 0:
                score_t = _score(q, t.get("abbreviation") or "")
            if score_t > 0:
                teams.append({
                    "id":     str(t.get("id") or ""),
                    "name":   t.get("name") or "",
                    "sport":  "basketball",
                    "league": (t.get("players") or [{}])[0].get("league") if t.get("players") else None,
                    "logo":   t.get("logo") or None,
                    "score":  score_t,
                })
            # Players in this team
            for p in (t.get("players") or []):
                pname = p.get("name") or ""
                ps = _score(q, pname)
                if ps > 0:
                    players.append({
                        "id":        str(p.get("id") or p.get("player_id") or ""),
                        "name":      pname,
                        "team_id":   str(t.get("id") or ""),
                        "team_name": t.get("name") or "",
                        "sport":     "basketball",
                        "score":     ps,
                    })
    except Exception:
        # Roster cache may not be ready on cold start — search degrades gracefully
        pass

    # ── Soccer teams (from accumulator) ─────────────────────────────────────
    for t in soccer_teams_snapshot():
        score_t = _score(q, t.get("name") or "")
        if score_t > 0:
            teams.append({
                "id":     t["id"],
                "name":   t["name"],
                "sport":  "football",
                "league": t.get("competition"),
                "logo":   t.get("crest"),
                "score":  score_t,
            })

    # ── Leagues (static) ────────────────────────────────────────────────────
    for lg in _LEAGUES:
        score_t = _score(q, lg["name"])
        if score_t == 0:
            score_t = _score(q, lg["id"].replace("_", " "))
        if score_t > 0:
            leagues.append({**lg, "score": score_t})

    # Sort by score desc, then name asc; cap each section at `limit`
    teams.sort(   key=lambda r: (-r["score"], r["name"].lower()))
    leagues.sort( key=lambda r: (-r["score"], r["name"].lower()))
    players.sort( key=lambda r: (-r["score"], r["name"].lower()))

    return {
        "teams":   teams[:limit],
        "leagues": leagues[:limit],
        "players": players[:limit],
    }
