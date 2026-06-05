"""Unit tests for _resolve_national_team_for_player precedence."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
import bzzoiro_adapter as bz
import national_team as nt


def test_prefers_bsd_direct_national_team_id(monkeypatch):
    monkeypatch.setattr(bz, "player_detail",
                        lambda pid: {"national_team_id": 485, "nationality": "France"})
    def _boom(name):
        raise AssertionError("name lookup must not run when direct id exists")
    monkeypatch.setattr(nt, "national_team_id", _boom)
    assert main._resolve_national_team_for_player("594") == "485"


def test_falls_back_to_name_lookup_when_direct_is_null(monkeypatch):
    monkeypatch.setattr(bz, "player_detail",
                        lambda pid: {"national_team_id": None, "nationality": "Uruguay"})
    monkeypatch.setattr(nt, "national_team_id",
                        lambda name: "480" if name == "Uruguay" else None)
    assert main._resolve_national_team_for_player("2743") == "480"


def test_country_id_wins(monkeypatch):
    # country_id short-circuits before any provider call
    assert main._resolve_national_team_for_player("1", country_id="999") == "999"


def test_returns_none_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(bz, "player_detail",
                        lambda pid: {"national_team_id": None, "nationality": "Atlantis"})
    monkeypatch.setattr(nt, "national_team_id", lambda name: None)
    assert main._resolve_national_team_for_player("7") is None
