import json
import sys
import types

import national_team as nt


def _fake_soccer_source(standings, *, matches=None):
    """A stand-in `soccer_source` module so standings/fixtures parsing can be
    exercised without hitting the provider."""
    mod = types.ModuleType("soccer_source")
    mod.BSD_COMPETITION_IDS = {"world_cup": 27}
    mod.bsd = types.SimpleNamespace(league_standings=lambda cid: standings)
    mod.get_soccer_matches = lambda *a, **k: (matches or [])
    return mod


def test_norm_strips_accents_case_and_punctuation():
    assert nt._norm("Côte d'Ivoire") == "cote d'ivoire"
    assert nt._norm("  Bosnia-Herzegovina ") == "bosnia herzegovina"
    assert nt._norm("FRANCE") == "france"
    assert nt._norm(None) == ""


def test_alias_maps_provider_names_to_fixture_names():
    assert nt._alias("USA") == "united states"
    assert nt._alias("South Korea") == "korea republic"
    # Unknown names pass through normalized, unchanged.
    assert nt._alias("France") == "france"


def test_resolves_from_override_file(tmp_path, monkeypatch):
    override = tmp_path / "national_teams.json"
    override.write_text(json.dumps({"teams": {"France": "9001", "USA": "9002"}}))
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", override)
    nt._reset_caches_for_tests()
    # Override hit short-circuits the dynamic index; accent/alias-insensitive.
    assert nt.national_team_id("France") == "9001"
    assert nt.national_team_id("united states of america") == "9002"


def test_falls_back_to_dynamic_index(monkeypatch):
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", nt._OVERRIDE_PATH.parent / "does-not-exist.json")
    monkeypatch.setattr(nt, "_dynamic_index", lambda: {"argentina": "55", "brazil": "66"})
    nt._reset_caches_for_tests()
    assert nt.national_team_id("Argentina") == "55"
    assert nt.national_team_id("Brazil") == "66"


def test_unknown_nationality_returns_none(monkeypatch):
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", nt._OVERRIDE_PATH.parent / "does-not-exist.json")
    monkeypatch.setattr(nt, "_dynamic_index", lambda: {})
    nt._reset_caches_for_tests()
    assert nt.national_team_id("Atlantis") is None
    assert nt.national_team_id(None) is None
    assert nt.national_team_id("") is None


def test_standings_names_parses_dict_of_groups(monkeypatch):
    # Regression: BSD world_cup standings nest rows under `groups` as a DICT
    # keyed by group name. The old parser did `for grp in groups:` (iterating the
    # string keys) and crashed with `'str' object has no attribute 'get'`, so the
    # whole index came back empty and France never resolved.
    raw = {"grouped": True, "groups": {
        "Group I": [
            {"position": 1, "team_id": 485, "team_name": "France"},
            {"position": 2, "team_id": 486, "team_name": "Senegal"},
        ],
        "Group A": [{"position": 1, "team_id": 463, "team_name": "Brazil"}],
    }}
    monkeypatch.setitem(sys.modules, "soccer_source", _fake_soccer_source(raw))
    out = nt._standings_names("world_cup")
    assert out == {"france": "485", "senegal": "486", "brazil": "463"}


def test_standings_names_parses_list_and_flat_shapes(monkeypatch):
    # List of group objects wrapping rows, plus a nested `team` object.
    raw_list = {"standings": [
        {"rows": [{"team_id": 1, "team_name": "Italy"}]},
        {"standings": [{"team": {"id": 2, "name": "Spain"}}]},
    ]}
    monkeypatch.setitem(sys.modules, "soccer_source", _fake_soccer_source(raw_list))
    assert nt._standings_names("world_cup") == {"italy": "1", "spain": "2"}


def test_resolution_works_for_both_country_spellings(monkeypatch):
    # The dynamic index keys fixtures under their normalized name AND aliased
    # form, so a "USA" fixture resolves whether the player's nationality reads
    # "USA" or the spelled-out "United States".
    raw = {"groups": {"Group D": [
        {"team_id": 457, "team_name": "USA"},
        {"team_id": 485, "team_name": "France"},
    ]}}
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", nt._OVERRIDE_PATH.parent / "nope.json")
    monkeypatch.setitem(sys.modules, "soccer_source", _fake_soccer_source(raw))
    nt._reset_caches_for_tests()
    assert nt.national_team_id("USA") == "457"
    assert nt.national_team_id("United States") == "457"
    assert nt.national_team_id("France") == "485"


def test_dynamic_index_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", nt._OVERRIDE_PATH.parent / "does-not-exist.json")
    def boom():
        raise RuntimeError("provider down")
    monkeypatch.setattr(nt, "_dynamic_index", boom)
    nt._reset_caches_for_tests()
    # Resolution must never raise — favoriting depends on it.
    assert nt.national_team_id("France") is None
