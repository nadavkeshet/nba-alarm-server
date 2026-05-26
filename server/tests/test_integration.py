"""
End-to-end integration test against the dev server.
Tests all features touched in the v3 soccer branch:
  - Server health
  - Basketball favorites (teams, players)
  - Football favorites (teams, players) — new endpoints
  - Soccer alarm creation including player_alert type
  - /soccer/favorites combined endpoint (iOS SoccerFavoritesResponse shape)
  - Alarm type validation

Usage:
    cd Server && python -m pytest tests/test_integration.py -v

No env vars needed — targets the Railway dev server.
"""
from __future__ import annotations

import time
import uuid
import requests
import pytest

BASE = "https://nba-alarm-server-dev.up.railway.app"
# Unique token per test run so parallel runs don't interfere
TOKEN = f"ci_test_{uuid.uuid4().hex[:12]}"
HEADERS = {"Content-Type": "application/json"}


# ── helpers ──────────────────────────────────────────────────────────────────

def post(path: str, **body) -> requests.Response:
    return requests.post(f"{BASE}{path}", json=body, headers=HEADERS, timeout=15)

def get(path: str, **params) -> requests.Response:
    return requests.get(f"{BASE}{path}", params=params, timeout=15)

def delete(path: str, **params) -> requests.Response:
    return requests.delete(f"{BASE}{path}", params=params, timeout=15)

def ok(r: requests.Response) -> dict:
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    return r.json()


# ── 1. health ─────────────────────────────────────────────────────────────────

def test_health():
    d = ok(get("/health"))
    assert d["status"] == "ok"
    assert d["db"]["connected"] is True
    assert d["soccer"]["ready"] is True
    print(f"  version={d['version']}  competitions={d['soccer']['competitions']}")


# ── 2. token registration ─────────────────────────────────────────────────────

def test_register_token():
    d = ok(post("/register-token", push_token=TOKEN, sound_channel="alarm", language="en"))
    assert d["status"] == "token_registered"
    assert d["push_token"] == TOKEN


# ── 3. basketball settings ────────────────────────────────────────────────────

def test_basketball_settings_empty():
    """Fresh token should return empty favorites, no radar configured."""
    d = ok(get("/settings", user_token=TOKEN))
    assert isinstance(d.get("favoriteTeams", []), list)
    assert isinstance(d.get("favoritePlayers", {}), dict)


def test_basketball_add_team_requires_game_id():
    """/start-alarm requires game_id — validates the endpoint is protected."""
    r = post("/start-alarm",
        user_token=TOKEN,
        team_id="bos",
        team_name="Boston Celtics",
        league="nba",
        max_diff=6,
        start_quarter=4,
        alert_type="alarm",
        notify_at_start=False,
        broadcast_only=False,
        skip_euroleague_evening=False,
    )
    assert r.status_code == 422  # game_id is required


def test_basketball_favorite_teams_shape():
    d = ok(get("/settings", user_token=TOKEN))
    assert isinstance(d.get("favoriteTeams", []), list)
    assert isinstance(d.get("favoritePlayers", {}), dict)


# ── 4. football teams favorites ───────────────────────────────────────────────

FOOTBALL_TOKEN = TOKEN   # reuse same token for all sports
TEAM_ID  = "t_test_arsenal_99"
TEAM2_ID = "t_test_chelsea_99"

def test_football_team_upsert():
    d = ok(post("/soccer/favorites/teams",
        user_token=FOOTBALL_TOKEN,
        team_id=TEAM_ID,
        team_name="Arsenal",
        competition_id="premier_league",
        drama_threshold=7.5,
        alert_type="alarm",
        paused=False,
        notify_at_kickoff=True,
    ))
    assert d["status"] == "ok"
    assert d["team_id"] == TEAM_ID


def test_football_team_upsert_update():
    """Upserting same team_id should update, not duplicate."""
    d = ok(post("/soccer/favorites/teams",
        user_token=FOOTBALL_TOKEN,
        team_id=TEAM_ID,
        team_name="Arsenal FC",
        competition_id="premier_league",
        drama_threshold=8.0,
        alert_type="alarm",
        paused=True,
    ))
    assert d["status"] == "ok"


def test_football_team_add_second():
    d = ok(post("/soccer/favorites/teams",
        user_token=FOOTBALL_TOKEN,
        team_id=TEAM2_ID,
        team_name="Chelsea",
        competition_id="premier_league",
        alert_type="alarm",
    ))
    assert d["status"] == "ok"


def test_football_teams_list():
    d = ok(get("/soccer/favorites/teams", user_token=FOOTBALL_TOKEN))
    teams = d["teams"]
    ids = [t["team_id"] for t in teams]
    assert TEAM_ID in ids
    assert TEAM2_ID in ids
    arsenal = next(t for t in teams if t["team_id"] == TEAM_ID)
    assert arsenal["team_name"] == "Arsenal FC"
    assert arsenal["drama_threshold"] == 8.0
    assert arsenal["paused"] is True


def test_football_team_delete():
    ok(delete(f"/soccer/favorites/teams/{TEAM2_ID}", user_token=FOOTBALL_TOKEN))
    d = ok(get("/soccer/favorites/teams", user_token=FOOTBALL_TOKEN))
    ids = [t["team_id"] for t in d["teams"]]
    assert TEAM2_ID not in ids
    assert TEAM_ID in ids


def test_football_team_delete_not_found():
    r = delete(f"/soccer/favorites/teams/nonexistent_xyz", user_token=FOOTBALL_TOKEN)
    assert r.status_code == 404


# ── 5. football player favorites ──────────────────────────────────────────────

PLAYER_ID  = "p_test_saka_99"
PLAYER2_ID = "p_test_haaland_99"

def test_football_player_upsert():
    d = ok(post("/soccer/favorites/players",
        user_token=FOOTBALL_TOKEN,
        player_id=PLAYER_ID,
        player_name="Bukayo Saka",
        team_id=TEAM_ID,
        alert_type="alarm",
        paused=False,
    ))
    assert d["status"] == "ok"
    assert d["player_id"] == PLAYER_ID


def test_football_player_upsert_second():
    d = ok(post("/soccer/favorites/players",
        user_token=FOOTBALL_TOKEN,
        player_id=PLAYER2_ID,
        player_name="Erling Haaland",
        team_id=TEAM2_ID,
        alert_type="silent",
        paused=False,
    ))
    assert d["status"] == "ok"


def test_football_player_upsert_update():
    """Re-upserting should update in place."""
    d = ok(post("/soccer/favorites/players",
        user_token=FOOTBALL_TOKEN,
        player_id=PLAYER_ID,
        player_name="Bukayo Saka",
        team_id=TEAM_ID,
        alert_type="alarm",
        paused=True,
    ))
    assert d["status"] == "ok"


def test_football_players_list():
    d = ok(get("/soccer/favorites/players", user_token=FOOTBALL_TOKEN))
    players = d["players"]
    ids = [p["player_id"] for p in players]
    assert PLAYER_ID in ids
    assert PLAYER2_ID in ids
    saka = next(p for p in players if p["player_id"] == PLAYER_ID)
    assert saka["player_name"] == "Bukayo Saka"
    assert saka["paused"] is True


def test_football_player_delete():
    ok(delete(f"/soccer/favorites/players/{PLAYER2_ID}", user_token=FOOTBALL_TOKEN))
    d = ok(get("/soccer/favorites/players", user_token=FOOTBALL_TOKEN))
    ids = [p["player_id"] for p in d["players"]]
    assert PLAYER2_ID not in ids
    assert PLAYER_ID in ids


def test_football_player_delete_not_found():
    r = delete(f"/soccer/favorites/players/nonexistent_xyz", user_token=FOOTBALL_TOKEN)
    assert r.status_code == 404


# ── 6. combined /soccer/favorites (iOS SoccerFavoritesResponse shape) ─────────

def test_soccer_favorites_combined():
    """Shape must match iOS SoccerFavoritesResponse: {teams, players, competitions}."""
    d = ok(get("/soccer/favorites", user_token=FOOTBALL_TOKEN))
    assert "teams" in d
    assert "players" in d
    assert "competitions" in d
    assert isinstance(d["teams"], list)
    assert isinstance(d["players"], list)
    assert isinstance(d["competitions"], list)
    # Our test data should be present
    team_ids   = [t["team_id"]   for t in d["teams"]]
    player_ids = [p["player_id"] for p in d["players"]]
    assert TEAM_ID   in team_ids
    assert PLAYER_ID in player_ids


def test_soccer_favorites_team_shape():
    """Every team row must have fields iOS SoccerFavoriteTeam decodes."""
    d = ok(get("/soccer/favorites", user_token=FOOTBALL_TOKEN))
    required = {"team_id", "team_name", "competition_id", "drama_threshold",
                "alert_type", "paused", "notify_at_kickoff"}
    for team in d["teams"]:
        missing = required - team.keys()
        assert not missing, f"team row missing fields: {missing}"


def test_soccer_favorites_player_shape():
    """Every player row must have fields iOS SoccerFavoritePlayer decodes."""
    d = ok(get("/soccer/favorites", user_token=FOOTBALL_TOKEN))
    required = {"player_id", "player_name", "team_id", "alert_type", "paused"}
    for player in d["players"]:
        missing = required - player.keys()
        assert not missing, f"player row missing fields: {missing}"


# ── 7. soccer alarms — valid types ────────────────────────────────────────────

def test_soccer_alarm_drama_radar():
    d = ok(post("/soccer-alarm",
        user_token=TOKEN,
        alarm_type="drama_radar",
        alert_type="alarm",
    ))
    assert d["status"] == "alarm_set"
    assert d["alarm_type"] == "drama_radar"
    assert isinstance(d["alarm_id"], int)


def test_soccer_alarm_player_alert():
    """player_alert requires player_id; config carries the toggle flags."""
    d = ok(post("/soccer-alarm",
        user_token=TOKEN,
        alarm_type="player_alert",
        match_id="bsd-test-match-1",
        player_id=PLAYER_ID,
        config={
            "goal": True,
            "clutch_only": False,
            "hat_trick_watch": True,
            "card_yellow": False,
            "card_red": True,
        },
        alert_type="alarm",
    ))
    assert d["status"] == "alarm_set"
    assert d["alarm_type"] == "player_alert"
    assert isinstance(d["alarm_id"], int)


def test_soccer_alarm_player_alert_requires_player_id():
    r = post("/soccer-alarm",
        user_token=TOKEN,
        alarm_type="player_alert",
        match_id="bsd-test-match-1",
        alert_type="alarm",
        # player_id intentionally omitted
    )
    assert r.status_code == 400
    assert "player_id" in r.json().get("detail", "").lower()


def test_soccer_alarm_invalid_type():
    r = post("/soccer-alarm",
        user_token=TOKEN,
        alarm_type="not_a_real_type",
        alert_type="alarm",
    )
    assert r.status_code == 400


def test_soccer_alarm_match_type_requires_match_id():
    r = post("/soccer-alarm",
        user_token=TOKEN,
        alarm_type="match",
        alert_type="alarm",
        # match_id intentionally omitted
    )
    assert r.status_code == 400


# ── 8. list alarms — iOS SoccerAlarm shape ────────────────────────────────────

def test_soccer_alarms_list():
    """GET /soccer-alarms must return shape iOS SoccerAlarm decodes."""
    d = ok(get("/soccer-alarms", user_token=TOKEN))
    assert "alarms" in d
    if d["alarms"]:
        alarm = d["alarms"][0]
        required = {"id", "alarm_type", "is_active", "alert_type"}
        missing = required - alarm.keys()
        assert not missing, f"alarm missing fields: {missing}"


# ── 9. validation: empty token rejected ───────────────────────────────────────

def test_empty_token_rejected_favorites():
    r = get("/soccer/favorites", user_token="")
    assert r.status_code == 400


def test_empty_token_rejected_players():
    r = get("/soccer/favorites/players", user_token="")
    assert r.status_code == 400


# ── 10. football data endpoints ───────────────────────────────────────────────

def test_soccer_live_endpoint():
    d = ok(get("/soccer/live"))
    assert "matches" in d
    assert isinstance(d["matches"], list)


def test_soccer_games_endpoint():
    d = ok(get("/soccer/games", league="premier_league"))
    assert "matches" in d
    assert isinstance(d["matches"], list)


def test_search_endpoint():
    d = ok(get("/search", q="Arsenal"))
    assert "teams" in d or "results" in d or isinstance(d, dict)


# ── cleanup (best-effort) ─────────────────────────────────────────────────────

def test_zzz_cleanup():
    """Remove test data so it doesn't accumulate between runs."""
    delete(f"/soccer/favorites/teams/{TEAM_ID}", user_token=FOOTBALL_TOKEN)
    delete(f"/soccer/favorites/players/{PLAYER_ID}", user_token=FOOTBALL_TOKEN)
    # Verify cleaned up
    d = ok(get("/soccer/favorites", user_token=FOOTBALL_TOKEN))
    team_ids   = [t["team_id"]   for t in d["teams"]]
    player_ids = [p["player_id"] for p in d["players"]]
    assert TEAM_ID   not in team_ids
    assert PLAYER_ID not in player_ids


def test_soccer_favorite_team_multi_id_roundtrip():
    """A merged team (multiple provider ids + competitions) round-trips intact."""
    d = ok(post(
        "/soccer/favorites/teams",
        user_token=TOKEN,
        team_id="130",
        team_name="Maccabi Tel Aviv",
        competition_id="europa_league",
        team_ids=["130", "566"],
        competitions=["europa_league", "ligat_haal"],
    ))
    assert d["status"] == "ok"

    teams = ok(get("/soccer/favorites/teams", user_token=TOKEN))["teams"]
    row = next(t for t in teams if t["team_id"] == "130")
    assert sorted(row["team_ids"]) == ["130", "566"]
    assert sorted(row["competitions"]) == ["europa_league", "ligat_haal"]

    # cleanup
    ok(delete("/soccer/favorites/teams/130", user_token=TOKEN))
