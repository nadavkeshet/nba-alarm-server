"""Hebrew team-name resolution.

`config/team_names_he.json` is a compact reverse index — `normalized-english →
hebrew` — generated ONCE from the big Translators.json mapping (which is keyed
Hebrew → [english variants]). We load it once into memory at import; lookups are
O(1) dict hits. Soccer (and basketball) normalizers stamp a `name_he` key onto
each team so the iOS app can show Hebrew names without shipping/parsing the big
file itself.

To regenerate after Translators.json changes, see the one-off build snippet in
the PR that introduced this file.
"""
from __future__ import annotations

import json
import os
import unicodedata
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(__file__), "config", "team_names_he.json")

# MUST match the affixes used when the index was built.
_AFFIX = {"fc", "cf", "afc", "sc", "fk", "sk", "cd", "club", "ac", "if", "sv", "bk", "hk"}


def _strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm_basic(s: str) -> str:
    return "".join(c for c in _strip_diacritics(s) if c.isalnum())


def _norm_stripped(s: str) -> str:
    folded = _strip_diacritics(s)
    tokens = "".join(c if c.isalnum() else " " for c in folded).split()
    return "".join(t for t in tokens if t not in _AFFIX)


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def hebrew_name(english: str | None) -> str | None:
    """Hebrew name for an English team name, or None if unknown."""
    if not english:
        return None
    idx = _index()
    if not idx:
        return None
    b = _norm_basic(english)
    if b in idx:
        return idx[b]
    s = _norm_stripped(english)
    if s != b and s in idx:
        return idx[s]
    return None


def attach(team: dict | None) -> None:
    """Stamp `name_he` onto a normalized team dict in place (no-op if unknown).
    Also fills `short_name_he` from the full name's Hebrew when available."""
    if not isinstance(team, dict):
        return
    he = hebrew_name(team.get("name"))
    if he:
        team["name_he"] = he
        team.setdefault("short_name_he", he)
