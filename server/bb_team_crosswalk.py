"""Cross-source basketball team identity (BSD <-> 365scores).

BSD supplies EuroLeague/EuroCup/BCL games (team ids like 33); 365scores
supplies NBA/Winner League (ids like 631). The same club therefore has two
disjoint ids. This module maps between them so /search can return both, and
normalizes names so unseen teams still merge by name as a fallback.
"""
from __future__ import annotations
import re
import unicodedata

# Curated BSD -> 365 mapping for cross-source (EuroLeague family) teams.
# Seed with the confirmed pair; extend as teams are verified. Strings to match
# the wire ids the rest of the app uses.
_BSD_TO_365: dict[str, str] = {
    "33": "631",   # Maccabi Tel Aviv
}
_365_TO_BSD: dict[str, str] = {v: k for k, v in _BSD_TO_365.items()}

# Name-token aliases applied during normalization so abbreviated provider
# names collapse onto their canonical form for the name-merge fallback.
_NAME_ALIASES: dict[str, str] = {
    "ta": "tel aviv",
}

def bsd_for_365(s365_id: str) -> str | None:
    return _365_TO_BSD.get(str(s365_id))

def s365_for_bsd(bsd_id: str) -> str | None:
    return _BSD_TO_365.get(str(bsd_id))

import time as _time

# BSD-primary basketball leagues whose teams we enumerate for name-merge.
_BSD_LEAGUE_SLUGS = ("euroleague", "eurocup", "bcl")
_INDEX_TTL = 6 * 3600.0
_index_cache: tuple[float, dict[str, str]] | None = None

def bsd_team_index() -> dict[str, str]:
    """{bsd_team_id (str): team name} across BSD-primary basketball leagues.

    BSD basketball is disconnected — every league is 365-sourced now, so there
    are no BSD-sourced games whose team ids a 365 search row would need to merge
    with. This returns an empty index (no BSD calls); the curated `_BSD_TO_365`
    map below still serves any historical id callers pass in. Kept as a stable
    no-op so search's name-merge fallback degrades cleanly."""
    return {}


def norm_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    no_punct = re.sub(r"[^\w\s]", " ", ascii_only.lower())
    tokens = [t for t in no_punct.split() if t]
    expanded: list[str] = []
    for t in tokens:
        expanded.extend(_NAME_ALIASES.get(t, t).split())
    return " ".join(expanded)
