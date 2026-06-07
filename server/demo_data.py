"""Staged World Cup data for App Store screenshots.

Gated entirely behind the SCREENSHOT_DEMO env var — OFF in production. When on, a
few fabricated-but-dramatic World Cup matches (REAL teams, so crests / names /
Hebrew render authentically; only the live STATE is invented) flow through the
feed, match detail, drama meter, drama timeline, and live snapshot so the capture
session has consistent, high-drama screens. Turn the env var off after capturing.

Demo canonical ids are bsd-99000xx — well outside the real BSD id space.
"""
from __future__ import annotations

import math
import os
import time

DEMO_ENABLED = bool(os.getenv("SCREENSHOT_DEMO"))

# Real WC teams (id / crest / Hebrew from the live search) — only state is faked.
_TEAMS = {
    "argentina": ("489", "Argentina", "ארגנטינה", "ARG"),
    "france":    ("485", "France",    "צרפת",     "FRA"),
    "brazil":    ("463", "Brazil",    "ברזיל",    "BRA"),
    "spain":     ("475", "Spain",     "ספרד",     "ESP"),
    "england":   ("493", "England",   "אנגליה",   "ENG"),
    "portugal":  ("491", "Portugal",  "פורטוגל",  "POR"),
}

_ROUND_HE = {"Final": "גמר", "Semi-final": "חצי גמר", "Quarter-final": "רבע גמר"}

# (id, home, away, home_score, away_score, minute, drama_total, round)
_MATCHES = [
    ("bsd-9900001", "argentina", "france",   2, 2, 88, 9.4, "Final"),
    ("bsd-9900002", "brazil",    "spain",    1, 1, 76, 7.9, "Semi-final"),
    ("bsd-9900003", "england",   "portugal", 0, 0, 63, 6.6, "Quarter-final"),
]
_BY_ID = {m[0]: m for m in _MATCHES}
DEMO_IDS = set(_BY_ID)


def is_demo_id(canonical_id: str) -> bool:
    return DEMO_ENABLED and canonical_id in _BY_ID


def _side(key: str, score: int, xg: float = 0.0) -> dict:
    tid, name, name_he, short = _TEAMS[key]
    return {
        "id": tid, "name": name, "name_he": name_he,
        "short_name": short, "short_name_he": name_he,
        "crest": f"https://sports.bzzoiro.com/img/team/{tid}/",
        "score": score, "score_ht": min(score, 1), "score_pens": None, "xg": xg,
    }


def _momentum(minute: int) -> list[dict]:
    """Oscillating, both-signed series (green above / navy below) trending to a late
    away surge — visually dramatic for the momentum chart."""
    out = []
    for m in range(1, minute + 1):
        v = 42 * math.sin(m / 6.0) + 22 * math.cos(m / 10.0) - (m - 44) * 0.35
        out.append({"m": m, "v": round(v)})
    return out


def _hero_incidents() -> list[dict]:
    def goal(minute, home, player, hs, as_, gtype="regular", assist=None):
        return {"type": "goal", "minute": minute, "is_home": home, "player": player,
                "assist": assist, "goal_type": gtype, "player_id": minute,
                "added_time": None, "home_score": hs, "away_score": as_}
    return [
        goal(12, True,  "L. Messi",   1, 0, gtype="penalty"),
        goal(28, False, "K. Mbappé",  1, 1),
        goal(53, True,  "J. Álvarez", 2, 1, assist="L. Messi"),
        goal(79, False, "O. Dembélé", 2, 2, assist="K. Mbappé"),
        {"type": "card", "card_type": "red", "minute": 85, "is_home": False,
         "player": "J. Koundé", "player_id": 850, "added_time": None},
    ]


def _stats(hs_xg: float, as_xg: float) -> dict:
    return {
        "home": {"possession": 54, "shots": 14, "shots_on_target": 6, "big_chances": 4,
                 "corners": 7, "fouls": 11, "passes": 512, "expected_goals": hs_xg},
        "away": {"possession": 46, "shots": 12, "shots_on_target": 5, "big_chances": 3,
                 "corners": 5, "fouls": 14, "passes": 463, "expected_goals": as_xg},
    }


def detail(canonical_id: str) -> dict | None:
    m = _BY_ID.get(canonical_id)
    if not m:
        return None
    _id, home, away, hs, as_, minute, _drama, rnd = m
    incidents = _hero_incidents() if canonical_id == "bsd-9900001" else []
    return {
        "id": canonical_id, "source": "bsd", "source_event_id": int(canonical_id[4:]),
        "competition": "world_cup", "competition_id_native": "27",
        "round_name": rnd, "round_name_he": _ROUND_HE.get(rnd), "round_number": None,
        "status": "live", "minute": minute, "period": "2h",
        "home": _side(home, hs, xg=2.4), "away": _side(away, as_, xg=2.1),
        "is_derby": False, "is_neutral_ground": True,
        "kickoff": "2026-06-21T19:00:00+00:00", "venue": {"name": "MetLife Stadium"},
        "momentum": _momentum(minute), "xg_per_minute": [], "shotmap": [],
        "incidents": incidents, "stats": _stats(2.4, 2.1),
        "livedata": {"side": "away", "situation": "dangerous_attack", "x": 80, "y": 46},
        "odds": {"home": 2.4, "draw": 3.2, "away": 3.0},
        "watch_score": _drama,
    }


def games() -> list[dict]:
    """Feed/games-shaped demo WC matches (no detail-only fields)."""
    out = []
    for _id, home, away, hs, as_, minute, drama, rnd in _MATCHES:
        out.append({
            "id": _id, "source": "bsd", "source_event_id": int(_id[4:]),
            "competition": "world_cup", "competition_id_native": "27",
            "round_name": rnd, "round_number": None,
            "status": "live", "minute": minute, "period": "2h",
            "home": _side(home, hs), "away": _side(away, as_),
            "is_derby": False, "is_neutral_ground": True,
            "kickoff": "2026-06-21T19:00:00+00:00", "venue": {"name": "MetLife Stadium"},
            "watch_score": drama,
        })
    return out


def drama(canonical_id: str) -> dict | None:
    m = _BY_ID.get(canonical_id)
    if not m:
        return None
    total = m[6]
    return {
        "match_id": canonical_id, "minute": m[5], "status": "live",
        "drama": {"total": total, "state": round(total * 0.36, 2), "momentum": round(total * 0.25, 2),
                  "events": round(total * 0.20, 2), "stakes": 1.55, "odds": round(total * 0.15, 2),
                  "chances": round(total * 0.13, 2), "upset": 0.0, "qualification": 0.0},
        "match": {"home": _side(m[1], m[3]), "away": _side(m[2], m[4]),
                  "competition": "world_cup", "round_name": m[7]},
    }


def drama_timeline(canonical_id: str) -> list[dict] | None:
    m = _BY_ID.get(canonical_id)
    if not m:
        return None
    minute, total = m[5], m[6]
    out = []
    for t in range(1, minute + 1):
        ramp = total * (0.3 + 0.7 * (t / minute))                  # climbs to `total`
        wobble = 1.1 * math.sin(t / 5.0)
        d = max(0.0, min(10.0, ramp + wobble))
        out.append({"m": t, "drama": round(d, 2), "state": round(d * 0.36, 2),
                    "momentum": round(d * 0.25, 2), "events": round(d * 0.2, 2), "stakes": 1.55})
    return out


def live(canonical_id: str) -> dict | None:
    m = _BY_ID.get(canonical_id)
    if not m:
        return None
    _id, home, away, hs, as_, minute, _drama, _rnd = m
    return {
        "ws_active": True, "event_id": int(canonical_id[4:]),
        "score": {"home": hs, "away": as_}, "minute": minute, "second": 12,
        "status": "live", "period": 2,
        "period_started_at_uts": int(time.time()) - max(0, (minute - 45)) * 60,
        "injury_time": 4, "momentum_pressure": -0.45,
        "win_prob": [0.40, 0.30, 0.30], "win_prob_swing": 0.35,
        "stats": _stats(2.4, 2.1),
        "livedata": {"side": "away", "situation": "dangerous_attack", "x": 80, "y": 46},
    }
