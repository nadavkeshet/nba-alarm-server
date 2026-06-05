"""Normalize BSD player payloads into the stable wire shape iOS consumes.

Pure transforms (BSD dict -> wire dict) live here so they're unit-testable;
the live BSD calls + orchestration (event resolution, team-name lookup) live
in the FastAPI endpoints. Photo URLs are emitted optimistically -- the iOS
client falls back to an initials avatar if the image 404s.
"""
from __future__ import annotations

_FOOTBALL_PHOTO = "https://sports.bzzoiro.com/img/player/{}/"
_BASKETBALL_PHOTO = "https://sports.bzzoiro.com/basketball/img/player/{}/"


def football_photo_url(player_id) -> str:
    return _FOOTBALL_PHOTO.format(player_id)


def basketball_photo_url(player_id) -> str:
    return _BASKETBALL_PHOTO.format(player_id)


def _team_crest_url(team_id) -> str:
    """BSD team crest (works for clubs and national teams alike)."""
    return f"https://sports.bzzoiro.com/img/team/{team_id}/"


def _jersey(raw_value) -> str | None:
    return str(raw_value) if raw_value not in (None, "") else None


def normalize_basketball_detail(raw: dict, *, player_id: str) -> dict:
    raw = raw or {}
    sa = raw.get("season_avg") or {}
    team = raw.get("team") or {}
    pid = raw.get("id") or player_id
    return {
        "id": str(pid),
        "name": raw.get("name") or "",
        "sport": "basketball",
        "team_name": team.get("name"),
        "position": raw.get("position"),
        "jersey": _jersey(raw.get("jersey_number")),
        "height_cm": raw.get("height_cm"),
        "country": raw.get("country"),
        "photo": basketball_photo_url(pid),
        "season": {
            "games": sa.get("games"),
            "ppg": sa.get("points"),
            "rpg": sa.get("rebounds"),
            "apg": sa.get("assists"),
            "spg": sa.get("steals"),
            "bpg": sa.get("blocks"),
            "fg_pct": sa.get("field_goal_pct"),
            "usage_pct": sa.get("usage_pct"),
        },
    }


def normalize_basketball_games(raw, *, limit: int = 10) -> list[dict]:
    results = raw.get("results") if isinstance(raw, dict) else (raw or [])
    out: list[dict] = []
    for g in (results or [])[:limit]:
        out.append({
            "date": g.get("event_date"),
            "opponent": g.get("opponent"),
            "is_home": g.get("is_home"),
            "min": g.get("minutes"),
            "pts": g.get("points"),
            "reb": g.get("rebounds"),
            "ast": g.get("assists"),
            "rating": g.get("rating"),
        })
    return out


def normalize_soccer_detail(detail: dict, career: dict | None, *, player_id: str,
                            team_name: str | None,
                            current_team_id=None,
                            national_team_id=None,
                            national_team_name: str | None = None) -> dict:
    detail = detail or {}
    seasons = (career or {}).get("seasons") or []
    latest = seasons[0] if seasons else {}
    pid = detail.get("id") or player_id
    return {
        "id": str(pid),
        "name": detail.get("name") or "",
        "sport": "football",
        "team_name": team_name,
        "current_team_id": str(current_team_id) if current_team_id is not None else None,
        "club_crest": _team_crest_url(current_team_id) if current_team_id is not None else None,
        "national_team_id": str(national_team_id) if national_team_id is not None else None,
        "national_team_name": national_team_name,
        "national_team_crest": _team_crest_url(national_team_id) if national_team_id is not None else None,
        "position": detail.get("position"),
        "jersey": _jersey(detail.get("jersey_number")),
        "country": detail.get("nationality"),
        "photo": football_photo_url(pid),
        "season": {
            "apps": latest.get("matches"),
            "goals": latest.get("goals"),
            "assists": latest.get("assists"),
            "avg_rating": latest.get("avg_rating"),
        },
    }


def normalize_soccer_stats(stats, events: dict, *, limit: int = 10) -> list[dict]:
    results = stats.get("results") if isinstance(stats, dict) else (stats or [])
    out: list[dict] = []
    for s in (results or [])[:limit]:
        ev = events.get(s.get("event_id")) or {}
        is_home = s.get("team_id") == ev.get("home_team_id")
        opponent = ev.get("away_team") if is_home else ev.get("home_team")
        out.append({
            "date": ev.get("event_date"),
            "opponent": opponent,
            "is_home": is_home,
            "minutes": s.get("minutes_played"),
            "goals": s.get("goals"),
            "assists": s.get("goal_assist"),
            "rating": s.get("rating"),
        })
    return out
