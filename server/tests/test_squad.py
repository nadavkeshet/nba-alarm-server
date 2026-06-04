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
