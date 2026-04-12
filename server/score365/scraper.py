"""All scraping functions for 365Scores basketball data."""

from score365.client import Client365

BASKETBALL_SPORT_ID = 2


def get_games_by_date(client: Client365, start: str, end: str | None = None) -> dict:
    """Get all basketball games for a date range.

    Dates in MM/DD/YYYY format. Returns full response with 'games' and 'competitions'.
    """
    return client.get("/games/allscores/", params={
        "sports": str(BASKETBALL_SPORT_ID),
        "startDate": start,
        "endDate": end or start,
    })


def get_game_detail(client: Client365, game_id: int) -> dict:
    """Get full game detail including box score, lineups, quarter scores."""
    data = client.get("/game/", params={"gameId": str(game_id)})
    return data.get("game", data)


def get_all_competitions(client: Client365) -> list[dict]:
    """Get ALL basketball competitions (215+)."""
    data = client.get("/competitions/", params={"sports": str(BASKETBALL_SPORT_ID)})
    return data.get("competitions", [])


def extract_box_score(game: dict) -> dict:
    """Extract structured box score from a game detail response."""
    name_map = {}
    for m in game.get("members", []):
        name_map[m["id"]] = m.get("name", "?")

    # Map stat order to standard English keys (order is consistent across languages)
    STAT_ORDER_MAP = {
        1: "Min", 2: "Pts", 3: "Reb", 4: "Ast", 5: "Stl", 6: "Blk",
        7: "FG", 8: "3PT", 9: "FT", 10: "OR", 11: "DR", 12: "TO",
        13: "PF", 14: "+/-",
    }

    def _parse_side(comp: dict) -> dict:
        lineups = comp.get("lineups", {})
        members = lineups.get("members", [])
        players = []
        for m in members:
            pid = m["id"]
            stat_dict = {}
            for s in m.get("stats", []):
                key = STAT_ORDER_MAP.get(s.get("order"), s.get("shortName", "?"))
                stat_dict[key] = s["value"]
            players.append({
                "id": pid,
                "name": name_map.get(pid, "?"),
                "status": m.get("statusText", ""),
                "position": m.get("position", {}).get("shortName", ""),
                "stats": stat_dict,
            })
        return {
            "name": comp.get("name", "?"),
            "score": comp.get("score"),
            "players": players,
        }

    quarters = []
    for st in game.get("stages", []):
        quarters.append({
            "name": st.get("name", "?"),
            "home": st.get("homeCompetitorScore"),
            "away": st.get("awayCompetitorScore"),
        })

    return {
        "home": _parse_side(game.get("homeCompetitor", {})),
        "away": _parse_side(game.get("awayCompetitor", {})),
        "quarters": quarters,
        "venue": game.get("venue", {}).get("name", ""),
    }


def extract_teams(game: dict) -> list[dict]:
    """Extract team info (id, name, record) from a game detail."""
    teams = []
    for side in ("homeCompetitor", "awayCompetitor"):
        comp = game.get(side, {})
        if not comp.get("id"):
            continue
        record = comp.get("record", {})
        teams.append({
            "id": comp["id"],
            "name": comp.get("name", "?"),
            "short_name": comp.get("shortName"),
            "competition_id": game.get("competitionId"),
            "wins": record.get("wins"),
            "losses": record.get("loses"),
        })
    return teams


def extract_players(game: dict) -> list[dict]:
    """Extract player roster from a game detail. Returns players with team_id."""
    players = []
    members = game.get("members", [])
    member_map = {m["id"]: m for m in members}

    for side in ("homeCompetitor", "awayCompetitor"):
        comp = game.get(side, {})
        team_id = comp.get("id")
        lineups = comp.get("lineups", {})
        for lm in lineups.get("members", []):
            pid = lm["id"]
            info = member_map.get(pid, {})
            players.append({
                "id": pid,
                "athleteId": info.get("athleteId"),
                "name": info.get("name", "?"),
                "shortName": info.get("shortName"),
                "jerseyNumber": info.get("jerseyNumber"),
                "team_id": team_id,
            })
    return players
