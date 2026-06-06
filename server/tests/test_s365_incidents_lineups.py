"""365scores game-detail → canonical incidents/lineups normalization.

These are the producers the `s365-*` match-detail and `/soccer/match/{id}/lineups`
paths use. They must emit the SAME canonical shapes the BSD path does so the iOS
contract stays uniform.
"""
import soccer_source as ss


HOME_ID = 563
AWAY_ID = 569


def _game(events, members, home_lineup=None, away_lineup=None):
    return {
        "id": 4702049,
        "competitionId": 42,
        "homeCompetitor": {"id": HOME_ID, "name": "Maccabi", "lineups": home_lineup or {}},
        "awayCompetitor": {"id": AWAY_ID, "name": "Hapoel", "lineups": away_lineup or {}},
        "members": members,
        "events": events,
    }


# ── incidents ────────────────────────────────────────────────────────────────

def test_incidents_goal_and_card_with_running_score():
    members = [
        # member id 1 has a distinct athleteId → incident player_id maps to it.
        {"competitorId": HOME_ID, "id": 1, "name": "Alice Scorer", "shortName": "Scorer", "athleteId": 99},
        {"competitorId": HOME_ID, "id": 2, "name": "Bob Assist", "shortName": "Assist"},
        {"competitorId": AWAY_ID, "id": 3, "name": "Carl Booked", "shortName": "Booked"},
        {"competitorId": AWAY_ID, "id": 4, "name": "Dan Equalizer", "shortName": "Equalizer"},
    ]
    events = [
        # Home goal at 24' with an assist
        {"competitorId": HOME_ID, "gameTime": 24.0, "addedTime": 0, "order": 1,
         "playerId": 1, "extraPlayers": [2],
         "eventType": {"id": 1, "name": "Goal", "subTypeName": "Field Goal"}},
        # Away yellow card at 26'
        {"competitorId": AWAY_ID, "gameTime": 26.0, "addedTime": 0, "order": 2,
         "playerId": 3, "eventType": {"id": 2, "name": "Yellow Card"}},
        # Away goal at 41' (equalizer)
        {"competitorId": AWAY_ID, "gameTime": 41.0, "addedTime": 0, "order": 3,
         "playerId": 4, "eventType": {"id": 1, "name": "Goal", "subTypeName": "Penalty"}},
    ]
    out = ss._normalize_365_incidents(_game(events, members))
    assert len(out) == 3

    goal1, card, goal2 = out

    # Goal 1 — home, running score 1-0, scorer + assist names resolved.
    assert goal1["type"] == "goal"
    assert goal1["minute"] == 24
    assert goal1["is_home"] is True
    assert goal1["player"] == "Alice Scorer"          # FULL name (not short "Scorer")
    assert goal1["player_id"] == 99                    # athleteId, not the member id
    assert goal1["assist"] == "Bob Assist"
    assert goal1["home_score"] == 1 and goal1["away_score"] == 0

    # Card — away yellow, no running-score change.
    assert card["type"] == "card"
    assert card["card_type"] == "yellow"
    assert card["is_home"] is False
    assert card["player"] == "Carl Booked"
    assert card["player_id"] == 3                       # no athleteId → falls back to member id

    # Goal 2 — away, running score 1-1.
    assert goal2["type"] == "goal"
    assert goal2["is_home"] is False
    assert goal2["player"] == "Dan Equalizer"
    assert goal2["home_score"] == 1 and goal2["away_score"] == 1


def test_incidents_own_goal_credits_opponent():
    members = [
        {"competitorId": HOME_ID, "id": 10, "name": "Owen Goal", "shortName": "O. Goal"},
    ]
    events = [
        # Own goal by a HOME player → the AWAY side's score increments.
        {"competitorId": HOME_ID, "gameTime": 55.0, "order": 1, "playerId": 10,
         "eventType": {"id": 1, "name": "Goal", "subTypeName": "Own Goal"}},
    ]
    out = ss._normalize_365_incidents(_game(events, members))
    assert len(out) == 1
    og = out[0]
    assert og["type"] == "owngoal"
    assert og["is_home"] is True          # displayed on the scoring player's side
    assert og["home_score"] == 0 and og["away_score"] == 1   # credited to the opponent
    assert og.get("assist") is None       # own goals carry no assist


def test_incidents_substitution_in_out_direction():
    members = [
        {"competitorId": HOME_ID, "id": 20, "name": "In Coming", "shortName": "Coming"},
        {"competitorId": HOME_ID, "id": 21, "name": "Out Going", "shortName": "Going"},
    ]
    events = [
        # 365: playerId = player coming ON, extraPlayers[0] = player going OFF.
        {"competitorId": HOME_ID, "gameTime": 46.0, "order": 1, "playerId": 20,
         "extraPlayers": [21], "eventType": {"id": 1000, "name": "Substitution"}},
    ]
    out = ss._normalize_365_incidents(_game(events, members))
    assert len(out) == 1
    sub = out[0]
    assert sub["type"] == "substitution"
    assert sub["player_in"] == "In Coming" and sub["player_in_id"] == 20
    assert sub["player_out"] == "Out Going" and sub["player_out_id"] == 21


def test_incidents_chronological_replay_unordered_input():
    members = [
        {"competitorId": HOME_ID, "id": 1, "shortName": "H1"},
        {"competitorId": AWAY_ID, "id": 2, "shortName": "A1"},
    ]
    # Provided out of order — normalizer must sort by gameTime for the running score.
    events = [
        {"competitorId": AWAY_ID, "gameTime": 70.0, "order": 2, "playerId": 2,
         "eventType": {"id": 1, "name": "Goal", "subTypeName": "Field Goal"}},
        {"competitorId": HOME_ID, "gameTime": 10.0, "order": 1, "playerId": 1,
         "eventType": {"id": 1, "name": "Goal", "subTypeName": "Field Goal"}},
    ]
    out = ss._normalize_365_incidents(_game(events, members))
    assert [i["minute"] for i in out] == [10, 70]
    assert out[0]["home_score"] == 1 and out[0]["away_score"] == 0
    assert out[1]["home_score"] == 1 and out[1]["away_score"] == 1


# ── lineups ──────────────────────────────────────────────────────────────────

def _lineup_member(mid, status_text, position, ranking):
    return {
        "id": mid,
        "status": 1 if status_text == "Starting" else 2,
        "statusText": status_text,
        "position": {"name": position},
        "ranking": ranking,
    }


def test_lineups_home_away_names_positions_and_split():
    members = [
        {"competitorId": HOME_ID, "id": 1, "name": "Home Keeper", "shortName": "H.Keeper", "jerseyNumber": 1},
        {"competitorId": HOME_ID, "id": 2, "name": "Home Sub", "shortName": "H.Sub", "jerseyNumber": 17},
        {"competitorId": AWAY_ID, "id": 3, "name": "Away Striker", "shortName": "A.Striker", "jerseyNumber": 9},
    ]
    home_lineup = {
        "status": "Confirmed",
        "formation": "4-2-3-1",
        "members": [
            _lineup_member(1, "Starting", "Goalkeeper", 6.2),
            _lineup_member(2, "Bench", "Midfielder", None),
        ],
    }
    away_lineup = {
        "status": "Confirmed",
        "formation": "4-3-3",
        "members": [
            _lineup_member(3, "Starting", "Attacker", 7.4),
        ],
    }
    out = ss._normalize_365_lineups(_game([], members, home_lineup, away_lineup))
    assert out is not None
    assert out["lineup_status"] == "confirmed"
    assert out["event_id"] == 4702049

    home = out["lineups"]["home"]
    assert home["team_id"] == HOME_ID
    assert home["team_name"] == "Maccabi"
    assert home["formation"] == "4-2-3-1"
    # One starter, one substitute, names + positions + jersey resolved.
    assert len(home["players"]) == 1 and len(home["substitutes"]) == 1
    keeper = home["players"][0]
    assert keeper["id"] == 1
    assert keeper["name"] == "Home Keeper"
    assert keeper["short_name"] == "H.Keeper"
    assert keeper["position"] == "Goalkeeper"
    assert keeper["jersey_number"] == 1
    assert keeper["ai_score"] == 6.2 and keeper["rating"] == 6.2
    assert home["substitutes"][0]["name"] == "Home Sub"

    away = out["lineups"]["away"]
    assert away["team_id"] == AWAY_ID
    assert len(away["players"]) == 1
    assert away["players"][0]["name"] == "Away Striker"
    assert away["players"][0]["position"] == "Attacker"


def test_lineups_none_when_empty():
    members = []
    assert ss._normalize_365_lineups(_game([], members)) is None


# ── shotmap + xG timeline ────────────────────────────────────────────────────
import soccer_source as _ss


def _shot(comp, time, xg, side, line, outcome, body="Right Foot", xgot=0.0, pid=1):
    return {"competitorNum": comp, "time": time, "xg": xg, "xgot": xgot,
            "side": side, "line": line, "bodyPart": body, "playerId": pid,
            "outcome": {"name": outcome}}


def test_365_shotmap_maps_fields_and_coords():
    game = {"homeCompetitor": {"id": 10}, "awayCompetitor": {"id": 20},
            "chartEvents": {"events": [
                _shot(1, "6'", "0.11", 90.5, 44.6, "Missed"),
                _shot(2, "24'", "0.10", 82.2, 54.2, "Goal"),
            ]}}
    sm = _ss._normalize_365_shotmap(game)
    assert len(sm) == 2
    s0 = sm[0]
    assert s0["home"] is True and s0["type"] == "miss" and s0["xg"] == 0.11
    assert s0["min"] == 6 and s0["body"] == "Right Foot"
    # x = 100 - side (0 = attacking goal); y = lateral line
    assert s0["pos"] == {"x": 9.5, "y": 44.6}
    s1 = sm[1]
    assert s1["home"] is False and s1["type"] == "goal"


def test_365_xg_timeline_and_team_totals():
    game = {"homeCompetitor": {"id": 10}, "awayCompetitor": {"id": 20},
            "chartEvents": {"events": [
                _shot(1, "10'", "0.30", 88.0, 50.0, "Saved"),
                _shot(2, "20'", "0.20", 85.0, 50.0, "Missed"),
                _shot(1, "80'", "0.45", 92.0, 50.0, "Goal"),
            ]}}
    sm = _ss._normalize_365_shotmap(game)
    xgpm, momentum, team_xg = _ss._build_365_xg_timeline(sm)
    assert team_xg == (0.75, 0.20)                      # home 0.30+0.45, away 0.20
    assert xgpm[-1]["cum_home"] == 0.75 and xgpm[-1]["cum_away"] == 0.20
    # momentum is a signed [-1,1] series
    assert all(-1.0 <= p["v"] <= 1.0 for p in momentum)


def test_365_shotmap_empty_when_no_chart():
    assert _ss._normalize_365_shotmap({"chartEvents": {}}) == []
    assert _ss._build_365_xg_timeline([]) == ([], [], (0.0, 0.0))


# ── incident full-name + clickable-id helpers ────────────────────────────────
def test_365_member_name_and_athlete_id():
    members = {612715: {"id": 612715, "name": "Alejandro Romero",
                        "shortName": "Romero", "athleteId": 13557}}
    assert ss._365_member_name(members, 612715) == "Alejandro Romero"   # full
    assert ss._365_athlete_id(members, 612715) == 13557                  # clickable id
    assert ss._365_athlete_id(members, 999) == 999                       # fallback


def test_enrich_bsd_incident_names_from_cache():
    ss._bsd_pname_cache["22075"] = "Willy Semedo"   # pre-seed; no network
    out = ss._enrich_bsd_incident_names(
        [{"type": "goal", "player": "W. Semedo", "player_id": 22075}])
    assert out[0]["player"] == "Willy Semedo"
