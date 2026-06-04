"""Unit tests for national_team_squad — merge/normalize/sort. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soccer_source as ss


def _row(pid, name, pos, num, club="FC X"):
    return {"player_id": pid, "name": name, "position": pos,
            "jersey_number": num, "club": club, "club_country": "Spain",
            "caps": 10, "goals": 1, "age": 25, "status": "official"}


def test_squad_enriches_club_id_and_sorts(monkeypatch):
    rows = [
        _row(3, "Striker", "FW", 9),
        _row(1, "Keeper", "GK", 1),
        _row(2, "Back", "DF", 4),
    ]
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: rows)
    monkeypatch.setattr(ss.bsd, "player_detail",
                        lambda pid: {"current_team_id": 100 + int(pid)})

    out = ss.national_team_squad(930)

    assert out["team_id"] == 930
    assert out["count"] == 3
    assert [p["position"] for p in out["players"]] == ["GK", "DF", "FW"]
    keeper = out["players"][0]
    assert keeper["current_team_id"] == 101
    assert keeper["club_crest"] == "https://sports.bzzoiro.com/img/team/101/"
    assert keeper["name"] == "Keeper"


def test_squad_degrades_when_player_detail_fails(monkeypatch):
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: [_row(1, "A", "MF", 8)])

    def boom(pid):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(ss.bsd, "player_detail", boom)

    out = ss.national_team_squad(930)
    p = out["players"][0]
    assert p["current_team_id"] is None
    assert p["club_crest"] is None
    assert p["club"] == "FC X"


def test_squad_empty_upstream(monkeypatch):
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: [])
    out = ss.national_team_squad(999)
    assert out == {"team_id": 999, "count": 0, "players": []}


def test_squad_drops_rows_with_null_player_id(monkeypatch):
    rows = [
        {"player_id": None, "name": "Mystery", "position": "FW",
         "jersey_number": 99, "club": "FC Y", "club_country": "Spain",
         "caps": 0, "goals": 0, "age": 20, "status": "official"},
        _row(5, "Real Player", "MF", 8),
    ]
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: rows)
    monkeypatch.setattr(ss.bsd, "player_detail",
                        lambda pid: {"current_team_id": 100 + int(pid)})

    out = ss.national_team_squad(930)
    # Null-player_id row is dropped at the source.
    assert out["count"] == 1
    assert [p["name"] for p in out["players"]] == ["Real Player"]
    assert all(p["player_id"] is not None for p in out["players"])


def test_squad_route(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    fake = {"team_id": 930, "count": 1, "players": [
        {"player_id": 3700, "name": "David Zima", "jersey_number": 4,
         "position": "DF", "club": "SK Slavia Praha", "club_country": "Czechia",
         "current_team_id": 120, "club_crest": "https://x/120/",
         "caps": 5, "goals": 0, "age": 25}]}
    import soccer_source
    monkeypatch.setattr(soccer_source, "national_team_squad", lambda tid: fake)

    client = TestClient(main.app)
    r = client.get("/soccer/team/930/squad")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["players"][0]["current_team_id"] == 120
