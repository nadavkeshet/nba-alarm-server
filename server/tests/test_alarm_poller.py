"""One cycle of _alarm_poller against a real (in-memory SQLite) alarms table.

The core scaling property: scoreboards are fetched once per (date, league),
not once per alarm, no matter how many users watch the same games.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import main
import monitor


class _StopPoller(BaseException):
    """Raised from the poller's sleep to end the loop after one cycle."""


def _game(gid, state="in", period=4, home=100, away=97):
    return {
        "id": gid,
        "status": {"type": {"state": state}, "period": period},
        "competitions": [{
            "competitors": [{"score": str(home)}, {"score": str(away)}],
            "status": {"displayClock": "6:00"},
        }],
    }


def _eastern(days_offset=0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=5) + timedelta(days=days_offset)
    return dt.strftime("%Y%m%d")


@pytest.fixture
def poller(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    main._Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)

    env = {"scoreboards": {}, "fetches": [], "summary_fetches": [], "pushes": []}

    def fake_scoreboard(date_str, league):
        env["fetches"].append((date_str, league))
        return env["scoreboards"].get(league, [])

    def fake_summary(game_id):
        env["summary_fetches"].append(game_id)
        return None

    monkeypatch.setattr(main, "get_games_for_date", fake_scoreboard)
    monkeypatch.setattr(main, "_fetch_game_summary", fake_summary)
    monkeypatch.setattr(main, "_send_push_notification", lambda **kw: env["pushes"].append(kw))
    monkeypatch.setattr(main, "_get_user_language_from_db", lambda token: "en")
    monkeypatch.setattr(main, "next_game_start", lambda leagues=None: None)

    monkeypatch.setattr(monitor, "_FIRED_ALERTS", set())
    monkeypatch.setattr(monitor, "_DB_CHECK_FIRED_FN", None)
    monkeypatch.setattr(monitor, "_DB_PERSIST_FIRED_FN", None)

    async def stop_after_cycle(_seconds):
        raise _StopPoller

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_cycle)

    def add(n=1, *, game="G1", league="nba", threshold=5, condition="score_diff",
            date=None, user_prefix="user"):
        with Session(engine) as session:
            for i in range(n):
                session.add(main.Alarm(
                    user_token=f"{user_prefix}{league}{game}{i}", target_id=game, game_id=game,
                    game_name=game, condition_type=condition, threshold=threshold,
                    start_quarter=4, league=league,
                    game_date=date if date is not None else _eastern(),
                    is_active=True,
                ))
            session.commit()

    def run_cycle():
        with pytest.raises(_StopPoller):
            asyncio.run(main._alarm_poller())

    def active_count(**filters):
        with Session(engine) as session:
            q = session.query(main.Alarm).filter(main.Alarm.is_active.is_(True))
            for col, val in filters.items():
                q = q.filter(getattr(main.Alarm, col) == val)
            return q.count()

    env.update(add=add, run_cycle=run_cycle, active_count=active_count)
    return env


def test_one_scoreboard_fetch_per_league_not_per_alarm(poller):
    poller["scoreboards"] = {
        "nba": [_game("G1", home=100, away=97), _game("G2", home=110, away=90)],
        "euroleague": [_game("E1", home=80, away=70)],
    }
    poller["add"](50, game="G1")                     # 50 users on one game
    poller["add"](10, game="G2")                     # a different game, same league
    poller["add"](5, game="E1", league="euroleague")

    poller["run_cycle"]()

    assert sorted(poller["fetches"]) == sorted([(_eastern(), "nba"), (_eastern(), "euroleague")])


def test_close_game_alarms_fire_and_deactivate(poller):
    poller["scoreboards"] = {"nba": [_game("G1", home=100, away=97),     # gap 3: fire
                                     _game("G2", home=110, away=90)]}    # gap 20: wait
    poller["add"](3, game="G1")
    poller["add"](2, game="G2")

    poller["run_cycle"]()

    assert len(poller["pushes"]) == 3
    assert poller["active_count"](game_id="G1") == 0     # fired, so retired
    assert poller["active_count"](game_id="G2") == 2     # still watching


def test_fired_alarms_do_not_fire_again_next_cycle(poller):
    poller["scoreboards"] = {"nba": [_game("G1", home=100, away=97)]}
    poller["add"](3, game="G1")

    poller["run_cycle"]()
    poller["run_cycle"]()

    assert len(poller["pushes"]) == 3


def test_finished_game_expires_alarms_without_firing(poller):
    poller["scoreboards"] = {"nba": [_game("G1", state="post", home=130, away=100)]}
    poller["add"](2, game="G1")

    poller["run_cycle"]()

    assert poller["pushes"] == []
    assert poller["active_count"]() == 0


def test_stale_and_future_alarms_are_not_fetched(poller):
    poller["add"](1, game="OLD", date=_eastern(-3))     # game was days ago
    poller["add"](1, game="NEXT", date=_eastern(+2))    # game is in two days

    poller["run_cycle"]()

    assert poller["fetches"] == []                      # nothing to watch today
    assert poller["active_count"](game_id="OLD") == 0   # expired
    assert poller["active_count"](game_id="NEXT") == 1  # waits for its day


def test_one_boxscore_fetch_per_game_for_player_alarms(poller):
    poller["scoreboards"] = {"nba": [_game("G1"), _game("G2")]}
    poller["add"](30, game="G1", condition="points", threshold=40)
    poller["add"](5, game="G2", condition="points", threshold=40)

    poller["run_cycle"]()

    assert sorted(poller["summary_fetches"]) == ["G1", "G2"]
