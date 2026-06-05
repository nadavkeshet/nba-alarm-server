"""_normalize_bsd_player must keep shooting splits + plus/minus.

Both the BSD raw box score and the 365 normalizer (`_s365_box_player`) emit the
same snake_case keys — field_goals_made/attempted, three_pointers_made/attempted,
free_throws_made/attempted, plus_minus — which the box-score normalizer used to
strip, so the iOS summary showed "-" for FG / 3P / +/-.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main


def _raw(**over):
    # Shape verified live from BSD event 7105 (Wembanyama) AND matches the
    # 365 `_s365_box_player` output keys.
    base = {
        "name": "Victor Wembanyama", "player_id": 18, "jersey_number": 1,
        "position": "FC", "is_starter": True, "minutes": 37.8,
        "points": 26, "rebounds": 12, "assists": 2, "steals": 1, "blocks": 3,
        "turnovers": 6, "personal_fouls": 2,
        "field_goals_made": 6, "field_goals_attempted": 21,
        "three_pointers_made": 2, "three_pointers_attempted": 9,
        "free_throws_made": 12, "free_throws_attempted": 13,
        "plus_minus": -3,
    }
    base.update(over)
    return base


def test_normalize_keeps_fg_3p_plusminus():
    out = main._normalize_bsd_player(_raw())
    # the previously-dropped fields
    assert out["field_goals_made"] == 6
    assert out["field_goals_attempted"] == 21
    assert out["three_pointers_made"] == 2
    assert out["three_pointers_attempted"] == 9
    assert out["free_throws_made"] == 12
    assert out["free_throws_attempted"] == 13
    assert out["plus_minus"] == -3


def test_normalize_still_maps_basics():
    out = main._normalize_bsd_player(_raw())
    assert out["PTS"] == 26 and out["REB"] == 12 and out["AST"] == 2
    assert out["player_name"] == "Victor Wembanyama"
    assert out["minutes"] == "37"  # float → int string, unchanged behavior


def test_normalize_missing_shooting_is_none_not_crash():
    out = main._normalize_bsd_player({"name": "X", "player_id": 9, "points": 0})
    assert out["field_goals_made"] is None
    assert out["plus_minus"] is None
    assert out["PTS"] == 0
