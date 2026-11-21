"""_check_and_fire: the per-alarm decision the poller makes every cycle.

Returns a {title, body} dict to fire, None to expire the alarm without firing,
or False to keep watching.
"""
from __future__ import annotations

import pytest

import main


def _game(gid="G1", state="in", period=4, home=100, away=97, clock="6:00"):
    return {
        "id": gid,
        "status": {"type": {"state": state}, "period": period},
        "competitions": [{
            "competitors": [{"score": str(home)}, {"score": str(away)}],
            "status": {"displayClock": clock},
        }],
    }


def _game_alarm(threshold=5, start_quarter=4, notify_at_start=False, gid="G1"):
    return main.Alarm(
        user_token="tok", target_id=gid, game_id=gid, game_name="LAL @ BOS",
        condition_type="score_diff", threshold=threshold,
        start_quarter=start_quarter, notify_at_start=notify_at_start,
    )


def _points_alarm(threshold=30, start_quarter=0, gid="G1", player="P23"):
    return main.Alarm(
        user_token="tok", target_id=player, target_name="LeBron", game_id=gid,
        game_name="LAL @ BOS", condition_type="points", threshold=threshold,
        start_quarter=start_quarter,
    )


def _summary(player="P23", **stats):
    names = ["PTS", "REB", "AST", "STL", "BLK"]
    return {"boxscore": {"players": [{"statistics": [{
        "names": names,
        "athletes": [{"athlete": {"id": player},
                      "stats": [str(stats.get(n, 0)) for n in names]}],
    }]}]}}


# ── Game alarms: "wake me if it's within N in the 4th" ──────────────────────

def test_close_game_in_target_quarter_fires():
    result = main._check_and_fire(_game_alarm(), [_game(period=4, home=100, away=97)])
    assert isinstance(result, dict) and result["title"] and result["body"]


def test_gap_exactly_at_threshold_fires():
    assert isinstance(main._check_and_fire(_game_alarm(threshold=5),
                                           [_game(home=105, away=100)]), dict)


def test_gap_above_threshold_keeps_watching():
    assert main._check_and_fire(_game_alarm(threshold=5),
                                [_game(home=110, away=100)]) is False


def test_close_game_before_target_quarter_keeps_watching():
    assert main._check_and_fire(_game_alarm(start_quarter=4),
                                [_game(period=3, home=80, away=79)]) is False


def test_game_not_started_keeps_watching():
    assert main._check_and_fire(_game_alarm(), [_game(state="pre", period=0)]) is False


def test_finished_game_expires_the_alarm():
    assert main._check_and_fire(_game_alarm(), [_game(state="post", home=101, away=100)]) is None


def test_other_games_on_the_scoreboard_are_ignored():
    close_other_game = _game(gid="G2", home=100, away=99)
    assert main._check_and_fire(_game_alarm(gid="G1"), [close_other_game]) is False


def test_missing_scores_never_fire():
    broken = _game()
    broken["competitions"][0]["competitors"] = []
    assert main._check_and_fire(_game_alarm(), [broken]) is False


@pytest.mark.parametrize("period,clock,expected_fire", [
    (4, "6:00", False),   # 4th quarter, more than 5 minutes left
    (4, "4:59", True),    # inside the last 5 minutes
    (5, "3:00", True),    # overtime always counts
])
def test_last_five_minutes_mode(period, clock, expected_fire):
    alarm = _game_alarm(start_quarter=5)   # 5 = "last 5 minutes"
    result = main._check_and_fire(alarm, [_game(period=period, clock=clock, home=100, away=98)])
    assert isinstance(result, dict) is expected_fire


def test_notify_at_start_fires_when_quarter_begins_regardless_of_gap():
    alarm = _game_alarm(threshold=3, start_quarter=4, notify_at_start=True)
    assert isinstance(main._check_and_fire(alarm, [_game(period=4, home=120, away=90)]), dict)


# ── Player alarms ───────────────────────────────────────────────────────────

def test_points_goal_reached_fires():
    result = main._check_and_fire(_points_alarm(threshold=30), [_game()], _summary(PTS=31))
    assert isinstance(result, dict) and "31" in result["body"]


def test_points_below_goal_keeps_watching():
    assert main._check_and_fire(_points_alarm(threshold=30), [_game()], _summary(PTS=12)) is False


def test_points_goal_missed_by_final_buzzer_expires():
    assert main._check_and_fire(_points_alarm(threshold=30),
                                [_game(state="post")], _summary(PTS=12)) is None


def test_early_points_window_passed_expires():
    # "30 points by the end of Q2": once Q3 starts, the window is gone.
    alarm = _points_alarm(threshold=30, start_quarter=2)
    assert main._check_and_fire(alarm, [_game(period=3)], _summary(PTS=12)) is None


def test_points_fall_back_to_scoreboard_leaders_without_boxscore():
    game = _game()
    game["competitions"][0]["leaders"] = [{
        "abbreviation": "PTS",
        "leaders": [{"athlete": {"id": "P23"}, "value": "33"}],
    }]
    assert isinstance(main._check_and_fire(_points_alarm(threshold=30), [game], None), dict)


def test_triple_double_watch_fires_with_three_stats_near_ten():
    alarm = main.Alarm(user_token="tok", target_id="P23", target_name="Jokic", game_id="G1",
                       game_name="DEN @ PHX", condition_type="triple_double", threshold=0)
    assert isinstance(main._check_and_fire(alarm, [_game()], _summary(PTS=22, REB=9, AST=8)), dict)


def test_triple_double_watch_waits_with_only_two():
    alarm = main.Alarm(user_token="tok", target_id="P23", target_name="Jokic", game_id="G1",
                       game_name="DEN @ PHX", condition_type="triple_double", threshold=0)
    assert main._check_and_fire(alarm, [_game()], _summary(PTS=22, REB=9, AST=3)) is False
