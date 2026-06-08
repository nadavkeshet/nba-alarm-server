"""
Resolve a soccer player's NATIONALITY (a country *name* the data provider gives,
e.g. "France") to the national-team id that appears as a home/away team in
international competition fixtures (World Championship, Euros, …).

Why this exists: the provider never hands us a national-team id. Player *detail*
exposes `nationality` as a country name only; player *search* exposes just the
club id. So favoriting Kylian Mbappé stored only Real Madrid — France's games
were never matched. Here we map the nationality name -> national-team id and the
caller stores it as an alternate team id, so the favorites feed (which already
fetches international competitions and matches on the team-id set) surfaces the
player's national-team games too.

The name -> id map is assembled from two sources, in priority order:

  1. A static override file (`config/national_teams.json`) — authoritative, works
     offline and off-season. Pin ids here whenever you want certainty or to cover
     a nation the dynamic index can't currently see.
  2. A best-effort dynamic index built at runtime from international standings and
     a small fixtures probe, then cached. Populates automatically during
     tournament windows (exactly when national-team games happen).

Everything is wrapped defensively: any failure yields None, so favoriting a
player never breaks because of national-team resolution.
"""
from __future__ import annotations

import json
import logging
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("national_team")

# International competition slugs whose fixtures/standings feature national teams.
# Extend this as more international competitions are added to competition_map.json
# (Nations League, friendlies, qualifiers, Copa América, AFCON, …) — the resolver
# and feed pick them up automatically once they're catalogued and fetchable.
INTERNATIONAL_SLUGS: list[str] = ["world_cup", "euros"]

_OVERRIDE_PATH = Path(__file__).resolve().parent / "config" / "national_teams.json"

# Provider nationality string (normalized) -> the name used in fixtures/standings
# (normalized). National-team naming differs across feeds; pin the tricky ones so
# e.g. a "USA" nationality still matches a "United States" fixture team.
_ALIASES: dict[str, str] = {
    "usa": "united states",
    "united states of america": "united states",
    "south korea": "korea republic",
    "north korea": "korea dpr",
    "ivory coast": "cote d'ivoire",
    "czechia": "czech republic",
    "china": "china pr",
    "iran": "ir iran",
    "cape verde": "cabo verde",
    "bosnia": "bosnia & herzegovina",
    "bosnia and herzegovina": "bosnia & herzegovina",
    "dr congo": "congo dr",
    "uae": "united arab emirates",
    "turkey": "turkiye",
}


def _norm(s: str | None) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", " ").replace("-", " ")
    return " ".join(s.split())


def _alias(name: str | None) -> str:
    n = _norm(name)
    return _ALIASES.get(n, n)


# ── Static overrides ─────────────────────────────────────────────────────────
_override_cache: dict[str, str] | None = None


def _overrides() -> dict[str, str]:
    """Normalized-name -> team-id map from the override file (cached)."""
    global _override_cache
    if _override_cache is not None:
        return _override_cache
    data: dict[str, str] = {}
    try:
        if _OVERRIDE_PATH.exists():
            raw = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            for name, tid in (raw.get("teams") or {}).items():
                if tid:
                    data[_alias(name)] = str(tid)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("national_team: override load failed: %s", exc)
    _override_cache = data
    return data


# ── Dynamic index (standings + small fixtures probe) ─────────────────────────
_index_cache: dict[str, str] = {}
_index_exp: float = 0.0
_INDEX_TTL = 6 * 3600  # seconds


def _standings_names(slug: str) -> dict[str, str]:
    """Normalized team name -> id from a competition's raw standings (stable once
    groups are drawn). Best-effort; tolerant of the raw BSD group shape."""
    out: dict[str, str] = {}
    try:
        import soccer_source as ss

        comp_id = ss.BSD_COMPETITION_IDS.get(slug)
        if comp_id is None:
            return out
        raw = ss.bsd.league_standings(comp_id) or {}
        groups = raw.get("groups") or raw.get("standings") or []
        # `groups` comes in three shapes across feeds/competitions:
        #   • dict keyed by group name -> list of rows  (BSD world_cup: {"Group A": [...]})
        #   • list of group objects each wrapping rows  ({"rows"/"standings"/...: [...]})
        #   • a flat list of rows
        group_iter = groups.values() if isinstance(groups, dict) else groups
        for grp in group_iter:
            if isinstance(grp, dict):
                rows = (grp.get("rows") or grp.get("standings") or grp.get("table")
                        or grp.get("teams") or [])
            else:
                rows = grp  # already a list of rows
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                team = r.get("team") or {}
                tid = r.get("team_id") or team.get("id")
                name = team.get("name") or r.get("team_name")
                if name and tid:
                    out.setdefault(_norm(name), str(tid))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("national_team: standings(%s) failed: %s", slug, exc)
    return out


def _fixtures_names(slug: str) -> dict[str, str]:
    """Normalized team name -> id from a small window of fixtures around today."""
    out: dict[str, str] = {}
    try:
        import soccer_source as ss

        today = date.today()
        for d in (-1, 0, 1, 2, 3):
            iso = (today + timedelta(days=d)).isoformat()
            for m in (ss.get_soccer_matches(slug, date_iso=iso) or []):
                for side in ("home", "away"):
                    t = m.get(side) or {}
                    name, tid = t.get("name"), t.get("id")
                    if name and tid:
                        out.setdefault(_norm(name), str(tid))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("national_team: fixtures(%s) failed: %s", slug, exc)
    return out


def _dynamic_index() -> dict[str, str]:
    """Cached name -> id index built from all international competitions."""
    global _index_cache, _index_exp
    now = time.monotonic()
    if _index_cache and now < _index_exp:
        return _index_cache
    idx: dict[str, str] = {}
    for slug in INTERNATIONAL_SLUGS:
        for src in (_standings_names(slug), _fixtures_names(slug)):
            for name, tid in src.items():
                idx.setdefault(name, tid)
                # Also key under the aliased form so either spelling resolves
                # (fixture "USA" becomes reachable as "united states" and vice versa).
                idx.setdefault(_ALIASES.get(name, name), tid)
    if idx:
        _index_cache, _index_exp = idx, now + _INDEX_TTL
    return idx


# ── Public API ───────────────────────────────────────────────────────────────
def national_team_id(nationality: str | None) -> str | None:
    """Best-effort national-team id for a nationality name (e.g. "France").
    Returns None if it can't be resolved — callers must treat that as "no
    national team to track", never as an error."""
    raw = _norm(nationality)
    key = _alias(nationality)
    if not key:
        return None
    # Try the aliased name first, then the raw normalized name. The alias bridges
    # nationality vs fixture naming (e.g. "USA" -> "United States"), but some feeds
    # name the team the other way ("USA"), so attempting both avoids false misses.
    ov = _overrides()
    if key in ov:
        return ov[key]
    if raw in ov:
        return ov[raw]
    try:
        idx = _dynamic_index()
        return idx.get(key) or idx.get(raw)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("national_team: resolve(%s) failed: %s", nationality, exc)
        return None


def _reset_caches_for_tests() -> None:
    """Test hook — clears module caches so tests can re-seed overrides/index."""
    global _override_cache, _index_cache, _index_exp
    _override_cache = None
    _index_cache = {}
    _index_exp = 0.0
