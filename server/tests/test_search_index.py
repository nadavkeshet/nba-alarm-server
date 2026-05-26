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
