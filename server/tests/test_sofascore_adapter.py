"""Live network tests for sofascore_adapter.

These tests hit the real Sofascore API. The reference event is
Manchester United 3-2 Nottingham Forest (Premier League, 2026-05-17).
If Sofascore upgrades anti-bot, these fail fast — exactly when we want to know.

Run:
    cd Server && python -m pytest tests/test_sofascore_adapter.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import sofascore_adapter as sa

# Reference match. Picked because it has stats (Premier League) and is finished
# (immutable, won't drift). Update this if Sofascore deletes the event detail
# after enough time.
REF_EVENT_ID = 14023956
REF_HOME = "Manchester United"
REF_AWAY = "Nottingham Forest"


def test_event_detail_returns_teams() -> None:
    event = sa.event_detail(REF_EVENT_ID)
    assert event, "expected non-empty event detail"
    assert event["homeTeam"]["name"] == REF_HOME
    assert event["awayTeam"]["name"] == REF_AWAY


def test_event_statistics_includes_xg() -> None:
    stats = sa.event_statistics(REF_EVENT_ID)
    assert stats, "expected non-empty statistics payload"
    xg = sa.find_team_xg(stats)
    assert xg is not None, "expected expected-goals stat to be present"
    home_xg, away_xg = xg
    assert home_xg >= 0.0 and away_xg >= 0.0
    assert home_xg + away_xg > 0.5, "expected non-trivial xG total in a real PL match"


def test_event_graph_returns_momentum_points() -> None:
    graph = sa.event_graph(REF_EVENT_ID)
    points = graph.get("graphPoints", [])
    assert len(points) >= 80, f"expected at least 80 minute-points, got {len(points)}"
    # Each point should have a numeric minute and value
    sample = points[0]
    assert isinstance(sample.get("minute"), (int, float))
    assert isinstance(sample.get("value"), (int, float))


def test_event_shotmap_has_xg_and_coordinates() -> None:
    shots = sa.event_shotmap(REF_EVENT_ID)
    assert len(shots) >= 15, f"expected >= 15 shots in a 3-2 PL match, got {len(shots)}"
    shot = shots[0]
    # xg is sometimes None for blocked shots — confirm key exists, not the value
    assert "xg" in shot
    coords = shot.get("playerCoordinates") or {}
    assert "x" in coords and "y" in coords


def test_event_incidents_returns_timeline() -> None:
    incidents = sa.event_incidents(REF_EVENT_ID)
    assert len(incidents) >= 5, "expected several incidents in a finished PL match"
    types = {inc.get("incidentType") for inc in incidents}
    # Period markers always present; goals present in a 3-2
    assert "period" in types or "goal" in types


def test_unique_tournament_resolves_premier_league() -> None:
    ut = sa.unique_tournament(17)  # Premier League canonical ID
    assert ut.get("name") == "Premier League"
    assert ut.get("category", {}).get("name") == "England"


def test_graph_pressure_window_handles_missing_data() -> None:
    assert sa.graph_pressure_window({}) == 0.0
    assert sa.graph_pressure_window({"graphPoints": []}) == 0.0


def test_graph_pressure_window_computes_mean() -> None:
    graph = {"graphPoints": [{"minute": i, "value": float(i)} for i in range(1, 91)]}
    # Window = last 15 minutes (76–90), mean = 83
    result = sa.graph_pressure_window(graph, minutes=15)
    assert 82.0 <= result <= 84.0


@pytest.mark.skip(reason="Manual probe — uncomment when running locally")
def test_live_events_smoke() -> None:
    events = sa.live_events()
    print(f"\nLive football events right now: {len(events)}")
    for e in events[:3]:
        print(f"  {e['id']} {e['homeTeam']['name']} vs {e['awayTeam']['name']}")
