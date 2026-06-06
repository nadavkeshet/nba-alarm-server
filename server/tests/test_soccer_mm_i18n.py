from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import i18n

def test_goal_body_names_scorer_with_parenthesized_score():
    # Home scored to make it 3-0: real score, scorer's tally in parens, only the
    # scoring team named, no competition.
    home = i18n.t_soccer_body_goal("Russia", "Burkina Faso", 3, 0, "home", "en")
    assert home == "(3) - 0  Russia"
    away = i18n.t_soccer_body_goal("Russia", "Burkina Faso", 3, 1, "away", "en")
    assert away == "3 - (1)  Burkina Faso"
    # Unattributed scorer → plain real score.
    assert i18n.t_soccer_body_goal("Russia", "Burkina Faso", 2, 1, None, "en") == "2 - 1"
    # No competition leaks into the body.
    assert "world_cup" not in home and "World Cup" not in home

def test_reminder_body_has_minutes():
    en = i18n.t_soccer_body_reminder("A", "B", "premier_league", 30, "en")
    assert "30" in en

def test_new_titles_present():
    for key in ("soccer_goal", "soccer_second_half", "soccer_match_end", "soccer_lineups", "soccer_reminder"):
        assert i18n.t_title(key, "en") and i18n.t_title(key, "he")
