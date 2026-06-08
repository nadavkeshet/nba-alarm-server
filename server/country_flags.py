"""Country name → flag emoji, for World Cup calendar/event titles."""
from __future__ import annotations
import json
import os
from functools import lru_cache

import national_team as _nt

_CONFIG = os.path.join(os.path.dirname(__file__), "config", "country_flags.json")


@lru_cache(maxsize=1)
def _flag_map() -> dict:
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _flag_from_alpha2(cc: str) -> str:
    cc = (cc or "").strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + (ord(c) - ord("A"))) for c in cc)


def country_flag(name: str | None) -> str:
    """Flag emoji for a country/national-team name, or "" if unresolvable.
    Reuses national_team's alias/normalization so provider spellings resolve."""
    if not name:
        return ""
    m = _flag_map()
    # Try the aliased-then-normalized key, then the plain normalized key.
    for key in (_nt._norm(_nt._alias(name)), _nt._norm(name)):
        cc = m.get(key)
        if cc:
            return _flag_from_alpha2(cc)
    return ""
