"""The real _fire_once dedup gate: every alert path (poller, both radars) goes
through it, so it must fire each (user, alert, Eastern day) exactly once."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

import monitor


class _FrozenDatetime(datetime):
    frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


@pytest.fixture
def ledger(monkeypatch):
    """Fresh in-memory ledger plus an in-memory stand-in for the fired_alerts table."""
    table: set[str] = set()
    persisted: list[str] = []

    monkeypatch.setattr(monitor, "_FIRED_ALERTS", set())
    monkeypatch.setattr(monitor, "_DB_CHECK_FIRED_FN", lambda key: key in table)

    def persist(key):
        persisted.append(key)
        table.add(key)

    monkeypatch.setattr(monitor, "_DB_PERSIST_FIRED_FN", persist)
    return table, persisted


def _freeze(monkeypatch, iso_utc: str):
    _FrozenDatetime.frozen = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(monitor, "datetime", _FrozenDatetime)


def test_same_alert_fires_once(ledger):
    assert monitor._fire_once("g1_game_q4_id7", "userA") is True
    assert monitor._fire_once("g1_game_q4_id7", "userA") is False


def test_different_users_never_block_each_other(ledger):
    assert monitor._fire_once("g1_game_q4", "userA") is True
    assert monitor._fire_once("g1_game_q4", "userB") is True


def test_different_alerts_for_one_user_are_independent(ledger):
    assert monitor._fire_once("g1_p23_total_pts", "userA") is True
    assert monitor._fire_once("g1_p23_triple_double", "userA") is True


def test_first_fire_is_persisted_with_the_eastern_date(ledger, monkeypatch):
    _, persisted = ledger
    _freeze(monkeypatch, "2026-11-21T18:00:00")   # 13:00 Eastern, Nov 21
    monitor._fire_once("g1_game_q4", "userA")
    monitor._fire_once("g1_game_q4", "userA")     # blocked: no second write
    assert persisted == ["20261121_userA_g1_game_q4"]


def test_day_does_not_roll_over_mid_game(ledger, monkeypatch):
    # A game tipping off 22:00 Eastern runs past midnight UTC. Using a UTC date
    # would start a "new day" mid-game and re-fire; the Eastern date must not.
    _freeze(monkeypatch, "2026-11-22T03:00:00")   # 22:00 Eastern, Nov 21
    assert monitor._fire_once("g1_game_q4", "userA") is True
    _freeze(monkeypatch, "2026-11-22T04:30:00")   # 23:30 Eastern, still Nov 21
    assert monitor._fire_once("g1_game_q4", "userA") is False


def test_next_eastern_day_starts_a_fresh_ledger(ledger, monkeypatch):
    _freeze(monkeypatch, "2026-11-22T04:59:00")   # 23:59 Eastern, Nov 21
    assert monitor._fire_once("g1_game_q4", "userA") is True
    _freeze(monkeypatch, "2026-11-22T05:01:00")   # 00:01 Eastern, Nov 22
    assert monitor._fire_once("g1_game_q4", "userA") is True


def test_db_record_blocks_after_restart_wiped_memory(ledger, monkeypatch):
    # Simulates a redeploy: the table still has the key, memory is empty.
    table, persisted = ledger
    monitor._fire_once("g1_game_q4", "userA")
    monkeypatch.setattr(monitor, "_FIRED_ALERTS", set())

    assert monitor._fire_once("g1_game_q4", "userA") is False
    assert len(persisted) == 1                       # not written twice
    assert len(monitor._FIRED_ALERTS) == 1           # re-hydrated into memory


def test_db_outage_on_check_still_fires(ledger, monkeypatch):
    def broken(_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(monitor, "_DB_CHECK_FIRED_FN", broken)
    assert monitor._fire_once("g1_game_q4", "userA") is True


def test_db_outage_on_persist_still_fires_and_memory_dedups(ledger, monkeypatch):
    def broken(_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(monitor, "_DB_PERSIST_FIRED_FN", broken)
    assert monitor._fire_once("g1_game_q4", "userA") is True
    assert monitor._fire_once("g1_game_q4", "userA") is False


def test_concurrent_callers_fire_exactly_once(ledger, monkeypatch):
    # The poller and both radars can reach the same alert at the same moment.
    # The check-and-mark must be atomic: 20 racing threads, exactly one winner.
    table, _ = ledger

    def slow_check(key):
        # Read first, then stall: this widens the gap between "checked, not
        # fired yet" and "marked as fired", which is exactly where a missing
        # lock would let several threads through. (Stalling before the read
        # would let late threads see the winner's write and hide the race.)
        seen = key in table
        threading.Event().wait(0.01)
        return seen

    monkeypatch.setattr(monitor, "_DB_CHECK_FIRED_FN", slow_check)

    start = threading.Barrier(20)
    results: list[bool] = []
    results_lock = threading.Lock()

    def worker():
        start.wait()
        fired = monitor._fire_once("g1_game_q4", "userA")
        with results_lock:
            results.append(fired)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 20
    assert results.count(True) == 1
