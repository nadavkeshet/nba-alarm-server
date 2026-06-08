"""
National-team world-ranking lookup — a fallback underdog signal for Sensation
Watch. The BSD pre-match model underrates obvious favourites for some 48-team
World Cup minnow fixtures (e.g. it gave Germany only 44% vs Curaçao), so when the
betting odds are too even to flag an underdog we fall back to the ranking gap.

Rankings are a static, approximate snapshot in `config/fifa_rankings.json`
(keyed by country name; matched accent/case/alias-insensitively via the same
normaliser used for national-team resolution). FIFA rankings move slowly and the
consumer only cares about the *gap* between two sides, so approximate positions
are fine — refresh the file from the official ranking periodically.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import national_team as _nt  # reuse _norm / _alias for consistent name matching

log = logging.getLogger("fifa_rankings")

_PATH = Path(__file__).resolve().parent / "config" / "fifa_rankings.json"
_table: dict[str, int] | None = None


def _load() -> dict[str, int]:
    global _table
    if _table is not None:
        return _table
    data: dict[str, int] = {}
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        for name, rank in (raw.get("rankings") or {}).items():
            try:
                data[_nt._alias(name)] = int(rank)
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("fifa_rankings: load failed: %s", exc)
    _table = data
    return data


def rank(team_name: str | None) -> int | None:
    """World ranking position for a national team, or None if unknown."""
    if not team_name:
        return None
    return _load().get(_nt._alias(team_name))


def _reset_for_tests() -> None:
    global _table
    _table = None
