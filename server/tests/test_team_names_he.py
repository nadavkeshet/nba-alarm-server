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


def test_basketball_s365_team_obj_gets_name_he():
    """The 365scores basketball team builder stamps name_he (full name key)."""
    import basketball_source as bb
    team = bb._s365_team_obj({"id": 1, "name": "Maccabi Tel Aviv", "symbolicName": "MTA"})
    assert team["name_he"] == "מכבי תל אביב"
    # Unknown team → no _he key, never raises.
    miss = bb._s365_team_obj({"id": 2, "name": "Totally Made Up BC 999"})
    assert "name_he" not in miss


def test_basketball_espn_team_he_uses_displayname():
    """ESPN-shape basketball teams resolve Hebrew off `displayName` and emit
    both displayName_he and name_he."""
    import basketball_source as bb
    team = {"name": "MTA", "displayName": "Maccabi Tel Aviv"}
    bb._attach_espn_team_he(team)
    assert team["displayName_he"] == "מכבי תל אביב"
    assert team["name_he"] == "מכבי תל אביב"
    bb._attach_espn_team_he(None)  # must not raise


def test_basketball_make_team_obj_gets_name_he():
    """The 365scores adapter's ESPN team builder stamps Hebrew."""
    import score365_adapter as s365
    team = s365._make_team_obj({"id": 1, "name": "Real Madrid", "symbolicName": "RMA"})
    assert team["displayName_he"] == "ריאל מדריד"
    assert team["name_he"] == "ריאל מדריד"
