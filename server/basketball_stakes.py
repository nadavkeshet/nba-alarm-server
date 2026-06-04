"""Stakes multiplier for basketball watch scores — the playoff/Finals equivalent
of the football drama engine's round multiplier.

BSD basketball events carry no stage/round label, so a Game 7 of the NBA Finals
looks identical to a January regular-season game. 365scores, however, labels
games with a `stageName` ("Finals", "Conference Finals", "Conference
Semifinals", "First Round", "Play-In", …). We cross-reference the BSD game to
365 by competition + date + the two team names and turn that stage into a
multiplier so deciding games rate higher — the NBA Finals tonight should sit
above a midseason blowout.

Everything is best-effort: any failure (365 down, no match, unmapped league)
yields a neutral 1.0, so the watch score never breaks because of stakes.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from datetime import date as _date, timedelta

log = logging.getLogger("basketball_stakes")

# stage keyword (checked most-specific first) -> multiplier. A bare "Finals"
# (the championship round) gets the biggest bump; earlier rounds taper down.
_STAGE_RULES: list[tuple[tuple[str, ...], float]] = [
    (("conference final", "conf final"),                       1.30),
    (("conference semi", "conf semi"),                         1.15),
    (("semifinal", "semi-final", "semi final"),                1.30),
    (("quarterfinal", "quarter-final", "quarter final"),       1.15),
    (("play-in", "play in", "playin"),                         1.10),
    (("final",),                                               1.45),  # outright Finals
    (("round", "playoff", "series"),                           1.15),
]

# (league, yyyy-mm-dd) -> (expiry_monotonic, {frozenset(home,away)-norm: stageName})
_index_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_TTL = 6 * 3600


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def stage_to_multiplier(stage: str | None) -> float:
    s = (stage or "").lower()
    if not s:
        return 1.0
    for keywords, mult in _STAGE_RULES:
        if any(k in s for k in keywords):
            return mult
    return 1.0


def _build_index(league: str, date_iso: str) -> dict:
    """{frozenset(home_norm, away_norm): stageName} for a league's 365 games in a
    small window around the date (covers UTC-vs-local day boundaries)."""
    out: dict[frozenset, str] = {}
    try:
        import score365_adapter as s365
        from score365.client import Client365
        from score365.scraper import get_games_by_date
        comp_id = s365.COMPETITION_IDS.get(league)
        if comp_id is None:
            return out
        base = _date.fromisoformat(date_iso)
        with Client365() as client:
            for delta in (-1, 0, 1):
                day = (base + timedelta(days=delta)).isoformat()
                try:
                    data = get_games_by_date(client, day, sport_id=2)
                except Exception:
                    continue
                for g in (data.get("games") or []) if isinstance(data, dict) else []:
                    if g.get("competitionId") != comp_id:
                        continue
                    stage = g.get("stageName") or ""
                    home = (g.get("homeCompetitor") or {}).get("name")
                    away = (g.get("awayCompetitor") or {}).get("name")
                    if home and away and stage:
                        out.setdefault(frozenset({_norm(home), _norm(away)}), stage)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("basketball_stakes: index build failed (%s/%s): %s", league, date_iso, exc)
    return out


def _index(league: str, date_iso: str) -> dict:
    key = (league, date_iso)
    now = time.monotonic()
    hit = _index_cache.get(key)
    if hit and now < hit[0]:
        return hit[1]
    idx = _build_index(league, date_iso)
    # Cache even an empty index briefly so a 365 miss isn't re-fetched per game.
    _index_cache[key] = (now + _TTL, idx)
    return idx


def stakes_multiplier(league: str, home_name: str | None,
                      away_name: str | None, date_iso: str | None) -> float:
    """Multiplier (>= 1.0) for a basketball game's watch score based on its
    playoff stage. 1.0 for regular-season / unknown — neutral and safe."""
    if not date_iso or not home_name or not away_name:
        return 1.0
    try:
        day = date_iso[:10]
        stage = _index(league, day).get(frozenset({_norm(home_name), _norm(away_name)}))
        return stage_to_multiplier(stage)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("basketball_stakes: lookup failed: %s", exc)
        return 1.0


def _reset_cache_for_tests() -> None:
    _index_cache.clear()
