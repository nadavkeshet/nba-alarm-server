"""Deciding basketball games (playoffs/Finals) rate higher — the basketball
parity of football's round multiplier. Stage comes from a 365scores
cross-reference; these cover the stage→multiplier mapping and the name/date
lookup without touching the network."""
import basketball_stakes as bs


def test_stage_mapping_orders_specific_before_final():
    # "Conference Finals" must NOT fall through to the bare "final" rule.
    assert bs.stage_to_multiplier("Finals") == 1.45            # championship — highest
    assert bs.stage_to_multiplier("NBA Finals") == 1.45
    assert bs.stage_to_multiplier("Conference Finals") == 1.30
    assert bs.stage_to_multiplier("Conference Semifinals") == 1.15
    assert bs.stage_to_multiplier("Quarter Finals") == 1.15
    assert bs.stage_to_multiplier("First Round") == 1.15
    assert bs.stage_to_multiplier("Play-In") == 1.10
    assert bs.stage_to_multiplier("Regular Season") == 1.0
    assert bs.stage_to_multiplier(None) == 1.0
    assert bs.stage_to_multiplier("") == 1.0


def test_lookup_is_team_order_insensitive(monkeypatch):
    idx = {frozenset({bs._norm("San Antonio Spurs"), bs._norm("New York Knicks")}): "Finals"}
    monkeypatch.setattr(bs, "_build_index", lambda league, day: idx)
    bs._reset_cache_for_tests()
    assert bs.stakes_multiplier("nba", "San Antonio Spurs", "New York Knicks", "2026-06-06") == 1.45
    assert bs.stakes_multiplier("nba", "New York Knicks", "San Antonio Spurs", "2026-06-09T00:30:00+00:00") == 1.45


def test_unknown_matchup_is_neutral(monkeypatch):
    monkeypatch.setattr(bs, "_build_index", lambda league, day: {})
    bs._reset_cache_for_tests()
    assert bs.stakes_multiplier("nba", "Foo", "Bar", "2026-06-06") == 1.0


def test_missing_inputs_are_neutral():
    assert bs.stakes_multiplier("nba", None, "Bar", "2026-06-06") == 1.0
    assert bs.stakes_multiplier("nba", "Foo", "Bar", None) == 1.0


def test_build_index_failure_is_swallowed(monkeypatch):
    def boom(league, day):
        raise RuntimeError("365 down")
    monkeypatch.setattr(bs, "_build_index", boom)
    bs._reset_cache_for_tests()
    # Never raises — watch score must not break on a stakes lookup.
    assert bs.stakes_multiplier("nba", "A", "B", "2026-06-06") == 1.0
