"""Unit tests for player BSD->wire normalizers (pure functions)."""
from __future__ import annotations

import player_source as ps


def test_basketball_detail_maps_season_avg():
    raw = {
        "id": 521, "name": "Bronny James", "position": "G", "jersey_number": "9",
        "height_cm": 188, "country": "USA",
        "team": {"id": 13, "name": "Los Angeles Lakers"},
        "season_avg": {"games": 75, "points": 2.6, "rebounds": 0.6, "assists": 1.0,
                       "steals": 0.4, "blocks": 0.1, "field_goal_pct": 37.7, "usage_pct": 15.3},
    }
    out = ps.normalize_basketball_detail(raw, player_id="521")
    assert out["id"] == "521"
    assert out["sport"] == "basketball"
    assert out["team_name"] == "Los Angeles Lakers"
    assert out["jersey"] == "9"
    assert out["season"]["ppg"] == 2.6
    assert out["season"]["fg_pct"] == 37.7
    assert out["photo"].endswith("/basketball/img/player/521/")


def test_basketball_games_maps_rows_and_caps_limit():
    raw = {"results": [
        {"event_date": "2026-05-10T00:30:00+00:00", "opponent": "OKC", "is_home": True,
         "minutes": 3.6, "points": 2, "rebounds": 0, "assists": 0, "rating": None},
        {"event_date": "2026-05-08T01:30:00+00:00", "opponent": "OKC", "is_home": False,
         "minutes": 2.2, "points": 0, "rebounds": 0, "assists": 1, "rating": None},
    ]}
    out = ps.normalize_basketball_games(raw, limit=1)
    assert len(out) == 1
    assert out[0] == {"date": "2026-05-10T00:30:00+00:00", "opponent": "OKC",
                      "is_home": True, "min": 3.6, "pts": 2, "reb": 0, "ast": 0, "rating": None}


def test_soccer_detail_uses_latest_career_season():
    detail = {"id": 9063, "name": "Lionel Messi", "position": "F",
              "jersey_number": 10, "nationality": "Argentina"}
    career = {"seasons": [
        {"matches": 35, "goals": 35, "assists": 23, "avg_rating": 8.57},
        {"matches": 14, "goals": 13, "assists": 7, "avg_rating": 8.67},
    ]}
    out = ps.normalize_soccer_detail(detail, career, player_id="9063", team_name="Inter Miami CF")
    assert out["sport"] == "football"
    assert out["team_name"] == "Inter Miami CF"
    assert out["country"] == "Argentina"
    assert out["jersey"] == "10"
    assert out["season"] == {"apps": 35, "goals": 35, "assists": 23, "avg_rating": 8.57}
    assert out["photo"].endswith("/img/player/9063/")


def test_soccer_stats_resolves_opponent_from_events():
    stats = {"results": [
        {"event_id": 5131, "team_id": 293, "minutes_played": 73, "rating": 8.7,
         "goals": 0, "goal_assist": 2},
    ]}
    events = {5131: {"event_date": "2026-05-24T23:00:00+00:00",
                     "home_team": "Inter Miami CF", "away_team": "Philadelphia Union",
                     "home_team_id": 293, "away_team_id": 283}}
    out = ps.normalize_soccer_stats(stats, events, limit=10)
    assert len(out) == 1
    assert out[0]["opponent"] == "Philadelphia Union"
    assert out[0]["is_home"] is True
    assert out[0]["minutes"] == 73
    assert out[0]["assists"] == 2
    assert out[0]["rating"] == 8.7


def test_soccer_stats_opponent_when_player_is_away():
    stats = {"results": [{"event_id": 1, "team_id": 283, "minutes_played": 90,
                          "rating": 7.0, "goals": 1, "goal_assist": 0}]}
    events = {1: {"event_date": "d", "home_team": "H", "away_team": "A",
                  "home_team_id": 293, "away_team_id": 283}}
    out = ps.normalize_soccer_stats(stats, events)
    assert out[0]["opponent"] == "H"
    assert out[0]["is_home"] is False
