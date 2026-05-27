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

import re
import threading
import unicodedata
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
        comps = existing.get("competitions") or []
        if competition and competition not in comps:
            comps.append(competition)

        _SOCCER_TEAMS[key] = {
            "id":          key,
            "name":        name,
            "crest":       crest or existing.get("crest"),
            "competition": comps[0] if comps else None,
            "competitions": comps,
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


# Common club-name suffix tokens that don't distinguish one club from another.
# Prefixes like "maccabi"/"hapoel" are deliberately absent — they ARE the
# distinguishing part of Israeli/European club names.
_CLUB_SUFFIX_TOKENS = {
    "fc", "cf", "sc", "bc", "afc", "ac", "cd", "ssc", "ud", "if", "bk",
}


def _normalize_team_name(name: str) -> str:
    """Normalize a team name for cross-provider identity matching.

    lowercase → strip diacritics → strip punctuation → drop common club
    suffix tokens → collapse whitespace. Falls back to the un-filtered tokens
    if suffix-stripping would empty the name.
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    no_punct = re.sub(r"[^\w\s]", " ", lowered)
    tokens = [t for t in no_punct.split() if t]
    filtered = [t for t in tokens if t not in _CLUB_SUFFIX_TOKENS]
    return " ".join(filtered or tokens)


def _is_bsd_sourced(team: dict) -> bool:
    """BSD/bzzoiro crests are hosted on sports.bzzoiro.com."""
    return "bzzoiro" in (team.get("logo") or "")


def _primary_sort_key(team: dict):
    """Numeric ids sort ascending; non-numeric ids sort last by string."""
    tid = str(team.get("id") or "")
    try:
        return (0, int(tid), "")
    except ValueError:
        return (1, 0, tid)


def _pick_primary(group: list[dict]) -> dict:
    """Prefer a BSD-sourced row; otherwise the lowest numeric id. Deterministic."""
    bsd = [t for t in group if _is_bsd_sourced(t)]
    pool = bsd or group
    return sorted(pool, key=_primary_sort_key)[0]


def _dedupe_preserve_order(values, *, drop_falsy: bool = False) -> list:
    seen: set = set()
    out: list = []
    for v in values:
        if drop_falsy and not v:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _merge_duplicate_teams(teams: list[dict]) -> list[dict]:
    """Collapse rows that are the same club (same sport + normalized name) into
    one row carrying all provider ids and all leagues. Order preserved by first
    appearance so the later score-sort is stable.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for t in teams:
        key = (t.get("sport") or "", _normalize_team_name(t.get("name") or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)

    merged: list[dict] = []
    for key in order:
        group = groups[key]
        primary = _pick_primary(group)
        ids = _dedupe_preserve_order([primary["id"]] + [t["id"] for t in group])
        leagues = _dedupe_preserve_order(
            [primary.get("league")] + [t.get("league") for t in group] + [lg for t in group for lg in (t.get("leagues") or [])],
            drop_falsy=True,
        )
        merged.append({
            **primary,
            "ids":     ids,
            "leagues": leagues,
            "score":   max(t["score"] for t in group),
        })
    return merged


def _bsd_players(query: str, limit: int) -> list[dict]:
    """Live BSD player search for both sports → wire rows with BSD ids.

    Network call; tightly time-boxed by the clients. Returns [] on any
    failure so search degrades to teams/leagues only.
    """
    import player_source as ps
    football: list[dict] = []
    basketball: list[dict] = []
    try:
        import bzzoiro_adapter as bz
        for p in bz.player_search(query, limit=limit):
            football.append({
                "id": str(p.get("id") or ""),
                "name": p.get("name") or "",
                "team_id": str(p.get("current_team_id") or "") or None,
                "team_name": None,
                "sport": "football",
                "photo": ps.football_photo_url(p.get("id")),
            })
    except Exception:
        pass
    try:
        import bsd_basketball as bb
        for p in bb.player_search(query, limit=limit):
            team = p.get("team") or {}
            basketball.append({
                "id": str(p.get("id") or ""),
                "name": p.get("name") or "",
                "team_id": str(team.get("id") or "") or None,
                "team_name": team.get("name"),
                "sport": "basketball",
                "photo": ps.basketball_photo_url(p.get("id")),
            })
    except Exception:
        pass
    # Round-robin interleave so BOTH sports survive the caller's `[:limit]`
    # cap, then stamp descending scores so search()'s score-sort preserves
    # this balanced order (all rows would otherwise tie and re-sort by name,
    # letting one sport crowd the other out).
    out: list[dict] = []
    while football or basketball:
        if football:
            out.append(football.pop(0))
        if basketball:
            out.append(basketball.pop(0))
    for idx, row in enumerate(out):
        row["score"] = 1.0 - idx * 0.0001
    return out


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
                "leagues": t.get("competitions"),
                "logo":   t.get("crest"),
                "score":  score_t,
            })

    # ── Players (live BSD proxy, both sports) ───────────────────────────────
    players = _bsd_players(q, limit)

    # ── Leagues (static) ────────────────────────────────────────────────────
    for lg in _LEAGUES:
        score_t = _score(q, lg["name"])
        if score_t == 0:
            score_t = _score(q, lg["id"].replace("_", " "))
        if score_t > 0:
            leagues.append({**lg, "score": score_t})

    # Collapse clubs that split across providers (different id per competition)
    # into a single row carrying all ids + leagues, BEFORE the limit cap.
    teams = _merge_duplicate_teams(teams)

    # Sort by score desc, then name asc; cap each section at `limit`
    teams.sort(   key=lambda r: (-r["score"], r["name"].lower()))
    leagues.sort( key=lambda r: (-r["score"], r["name"].lower()))
    players.sort( key=lambda r: (-r["score"], r["name"].lower()))

    return {
        "teams":   teams[:limit],
        "leagues": leagues[:limit],
        "players": players[:limit],
    }
