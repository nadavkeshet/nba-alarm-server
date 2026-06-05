"""Hebrew team-name resolution from the prebuilt reverse index, and that the
soccer normalizers stamp `name_he` onto teams."""
import team_names_he as tn


def test_resolves_common_clubs():
    assert tn.hebrew_name("Arsenal") == "ארסנל"
    # affix-insensitive: "Manchester City" matches "... City FC" variants
    assert tn.hebrew_name("Manchester City")
    # diacritics-insensitive
    assert tn.hebrew_name("Atletico Madrid") or tn.hebrew_name("Atlético Madrid")


def test_unknown_returns_none():
    assert tn.hebrew_name("Totally Made Up FC 12345") is None
    assert tn.hebrew_name("") is None
    assert tn.hebrew_name(None) is None


def test_attach_stamps_name_he_in_place():
    team = {"name": "Arsenal"}
    tn.attach(team)
    assert team["name_he"] == "ארסנל"
    assert team["short_name_he"] == "ארסנל"


def test_attach_noop_on_unknown_or_bad():
    team = {"name": "Totally Made Up FC 12345"}
    tn.attach(team)
    assert "name_he" not in team
    tn.attach(None)  # must not raise
