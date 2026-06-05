from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import country_flags as cf


def test_basic_flags():
    assert cf.country_flag("France") == "🇫🇷"
    assert cf.country_flag("Argentina") == "🇦🇷"
    assert cf.country_flag("Brazil") == "🇧🇷"

def test_alias_spellings():
    assert cf.country_flag("USA") == "🇺🇸"
    assert cf.country_flag("Korea Republic") == "🇰🇷"

def test_unknown_returns_empty():
    assert cf.country_flag("Atlantis") == ""
    assert cf.country_flag(None) == ""
    assert cf.country_flag("") == ""

def test_flag_from_alpha2():
    assert cf._flag_from_alpha2("FR") == "🇫🇷"
    assert cf._flag_from_alpha2("us") == "🇺🇸"
    assert cf._flag_from_alpha2("X") == ""
