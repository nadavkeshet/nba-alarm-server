"""Unit tests for the in-process search index: name normalization + merge."""
from __future__ import annotations

import search_index as si


def test_normalize_lowercases_and_trims():
    assert si._normalize_team_name("  Maccabi Tel Aviv  ") == "maccabi tel aviv"


def test_normalize_strips_diacritics():
    assert si._normalize_team_name("Atlético Madrid") == "atletico madrid"


def test_normalize_strips_punctuation():
    assert si._normalize_team_name("Maccabi Tel-Aviv") == "maccabi tel aviv"


def test_normalize_drops_common_club_suffixes():
    assert si._normalize_team_name("FC Barcelona") == "barcelona"
    assert si._normalize_team_name("Real Madrid CF") == "real madrid"


def test_normalize_keeps_distinguishing_prefix():
    # "Maccabi"/"Hapoel" are NOT suffixes — must survive so Haifa != Tel Aviv
    assert si._normalize_team_name("Maccabi Haifa") == "maccabi haifa"
    assert si._normalize_team_name("Maccabi Tel Aviv") == "maccabi tel aviv"


def test_normalize_all_suffix_name_is_preserved():
    # If stripping would empty the name, keep the original tokens
    assert si._normalize_team_name("FC") == "fc"


def _seed_two_maccabi():
    """Replace the soccer accumulator with the two-provider Maccabi split."""
    si._SOCCER_TEAMS.clear()
    si.remember_soccer_team(
        "130", "Maccabi Tel Aviv",
        crest="https://sports.bzzoiro.com/img/team/130/",
        competition="europa_league",
    )
    si.remember_soccer_team(
        "566", "Maccabi Tel Aviv",
        crest="https://imagecache.365scores.com/image/upload/Competitors/566",
        competition="ligat_haal",
    )


def test_merge_collapses_same_name_into_one_row():
    _seed_two_maccabi()
    res = si.search("maccabi tel aviv", limit=10)
    rows = [t for t in res["teams"] if t["sport"] == "football"]
    assert len(rows) == 1, rows


def test_merge_carries_all_ids_and_leagues():
    _seed_two_maccabi()
    row = next(t for t in si.search("maccabi tel aviv")["teams"] if t["sport"] == "football")
    assert sorted(row["ids"]) == ["130", "566"]
    assert sorted(row["leagues"]) == ["europa_league", "ligat_haal"]


def test_merge_prefers_bsd_sourced_primary():
    _seed_two_maccabi()
    row = next(t for t in si.search("maccabi tel aviv")["teams"] if t["sport"] == "football")
    # bzzoiro-sourced id wins the primary slot (stable crest host)
    assert row["id"] == "130"
    assert "bzzoiro" in row["logo"]


def test_singleton_team_still_has_ids_and_leagues():
    si._SOCCER_TEAMS.clear()
    si.remember_soccer_team(
        "562", "Maccabi Haifa",
        crest="https://imagecache.365scores.com/image/upload/Competitors/562",
        competition="ligat_haal",
    )
    row = next(t for t in si.search("maccabi haifa")["teams"] if t["sport"] == "football")
    assert row["ids"] == ["562"]
    assert row["leagues"] == ["ligat_haal"]
