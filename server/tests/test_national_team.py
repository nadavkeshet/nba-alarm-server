import json

import national_team as nt


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


def test_dynamic_index_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(nt, "_OVERRIDE_PATH", nt._OVERRIDE_PATH.parent / "does-not-exist.json")
    def boom():
        raise RuntimeError("provider down")
    monkeypatch.setattr(nt, "_dynamic_index", boom)
    nt._reset_caches_for_tests()
    # Resolution must never raise — favoriting depends on it.
    assert nt.national_team_id("France") is None
