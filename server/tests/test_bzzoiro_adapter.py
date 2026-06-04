"""Live network tests for bzzoiro_adapter.

Requires BZZOIRO_API_TOKEN env var. Tests use Newcastle 3-1 West Ham
(Premier League, 2026-05-17, event_id 372) — a finished match BSD has full
stats for.

Run:
    cd Server && BZZOIRO_API_TOKEN=<token> python -m pytest tests/test_bzzoiro_adapter.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import bzzoiro_adapter as bz

REF_EVENT_ID = 372  # Newcastle 3-1 West Ham, PL, 2026-05-17
REF_HOME = "Newcastle United"
REF_AWAY = "West Ham United"

skip_if_no_token = pytest.mark.skipif(
    not os.environ.get("BZZOIRO_API_TOKEN"),
    reason="BZZOIRO_API_TOKEN env var not set",
)


@skip_if_no_token
def test_event_detail_returns_teams() -> None:
    event = bz.event_detail(REF_EVENT_ID)
    assert event is not None
    assert event["home_team"] == REF_HOME
    assert event["away_team"] == REF_AWAY
    assert event["status"] == "finished"


@skip_if_no_token
def test_event_stats_has_xg_and_momentum() -> None:
    stats = bz.event_stats(REF_EVENT_ID)
    assert stats is not None
    # xG
    xg = bz.get_team_xg(stats)
    assert xg is not None
    home_xg, away_xg = xg
    assert home_xg > 0.5  # Newcastle dominated; xg was 1.7
    # Momentum: 92 minute-points expected
    momentum = stats.get("momentum", [])
    assert len(momentum) >= 80
    sample = momentum[0]
    assert "m" in sample and "v" in sample
    # xg_per_minute: cumulative timeline
    xg_pm = stats.get("xg_per_minute", [])
    assert len(xg_pm) >= 5
    last = xg_pm[-1]
    assert "cum_home" in last and "cum_away" in last
    # Shotmap
    shotmap = stats.get("shotmap", [])
    assert len(shotmap) >= 15
    shot = shotmap[0]
    assert "xg" in shot and "pos" in shot
    assert "x" in shot["pos"] and "y" in shot["pos"]


@skip_if_no_token
def test_event_incidents() -> None:
    incidents = bz.event_incidents(REF_EVENT_ID)
    assert isinstance(incidents, list)
    assert len(incidents) >= 5


@skip_if_no_token
def test_event_lineups_confirmed() -> None:
    lu = bz.event_lineups(REF_EVENT_ID)
    assert lu is not None
    assert lu["lineup_status"] == "confirmed"
    home = lu["lineups"]["home"]
    assert home["formation"]  # e.g. "4-2-3-1"
    assert len(home["players"]) == 11


@skip_if_no_token
def test_momentum_pressure_window_returns_finite_value() -> None:
    stats = bz.event_stats(REF_EVENT_ID)
    pressure = bz.momentum_pressure_window(stats, window_minutes=15)
    assert isinstance(pressure, float)
    assert -100.0 <= pressure <= 100.0


@skip_if_no_token
def test_xg_in_window() -> None:
    stats = bz.event_stats(REF_EVENT_ID)
    result = bz.xg_in_window(stats, start_minute=60, end_minute=90)
    assert result is not None
    home_window_xg, away_window_xg = result
    # In any 30-min window of a 3-1 PL match, both should have non-negative xG
    assert home_window_xg >= 0.0
    assert away_window_xg >= 0.0


@skip_if_no_token
def test_league_events_premier_league() -> None:
    events = bz.league_events(league_id=1, date_from="2026-05-17", date_to="2026-05-17", status="finished")
    assert len(events) >= 1
    # All should be PL
    for e in events:
        assert e["league_id"] == 1


def test_momentum_window_handles_missing_data() -> None:
    """Doesn't need network — works with empty/missing input."""
    assert bz.momentum_pressure_window({}) == 0.0
    assert bz.momentum_pressure_window({"momentum": []}) == 0.0


def test_get_team_xg_handles_missing_data() -> None:
    assert bz.get_team_xg({}) is None
    assert bz.get_team_xg({"stats": {}}) is None
    assert bz.get_team_xg({"stats": {"home": {}, "away": {}}}) is None


REF_WC_TEAM_ID = 930  # Czechia — 26 official call-ups (verified 2026-06-04)


@skip_if_no_token
def test_worldcup_squad_returns_official_callups() -> None:
    rows = bz.worldcup_squad(REF_WC_TEAM_ID)
    assert isinstance(rows, list)
    assert len(rows) == 26
    sample = rows[0]
    assert "player_id" in sample
    assert "position" in sample
    assert sample["status"] == "official"
    assert all(r["team_id"] == REF_WC_TEAM_ID for r in rows)
