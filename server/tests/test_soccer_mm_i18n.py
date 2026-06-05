from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import i18n

def test_goal_body_has_score_both_langs():
    en = i18n.t_soccer_body_goal("Russia", "Burkina Faso", "world_cup", 3, 0, "en")
    he = i18n.t_soccer_body_goal("Russia", "Burkina Faso", "world_cup", 3, 0, "he")
    assert "3" in en and "0" in en and "Russia" in en
    assert "3" in he and "0" in he and en != he

def test_reminder_body_has_minutes():
    en = i18n.t_soccer_body_reminder("A", "B", "premier_league", 30, "en")
    assert "30" in en

def test_new_titles_present():
    for key in ("soccer_goal", "soccer_second_half", "soccer_match_end", "soccer_lineups", "soccer_reminder"):
        assert i18n.t_title(key, "en") and i18n.t_title(key, "he")
