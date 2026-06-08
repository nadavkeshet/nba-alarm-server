"""Staged World Championship data for App Store screenshots.

Gated entirely behind the SCREENSHOT_DEMO env var — OFF in production. When on, a
full group-stage slate of fabricated-but-dramatic World Championship matches (REAL teams, so
crests / names / Hebrew render authentically; only the STATE is invented) flows
through the feed, /soccer/games, match detail, drama meter, drama timeline, and
live snapshot — a few thrilling LIVE games plus a spread of UPCOMING fixtures, so
every screen looks like a tournament in full swing. Turn the env var off after
capturing.

Demo canonical ids are bsd-99000xx — well outside the real BSD id space.
"""
from __future__ import annotations

import datetime
import math
import os
import time

DEMO_ENABLED = bool(os.getenv("SCREENSHOT_DEMO"))


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _kickoff_live(minute: int) -> str:
    """Kickoff for a match `minute` into play right now (sits on TODAY)."""
    return _iso(time.time() - (minute + 15) * 60)


def _kickoff_future(hours: float) -> str:
    return _iso(time.time() + hours * 3600)


# Real WC teams (id / name / Hebrew from the live search) — only state is faked.
_TEAMS = {
    "argentina":   ("489", "Argentina",   "ארגנטינה", "ARG"),
    "france":      ("485", "France",      "צרפת",     "FRA"),
    "croatia":     ("494", "Croatia",     "קרואטיה",  "CRO"),
    "morocco":     ("464", "Morocco",     "מרוקו",    "MAR"),
    "brazil":      ("463", "Brazil",      "ברזיל",    "BRA"),
    "spain":       ("475", "Spain",       "ספרד",     "ESP"),
    "germany":     ("467", "Germany",     "גרמניה",   "GER"),
    "japan":       ("470", "Japan",       "יפן",      "JPN"),
    "england":     ("493", "England",     "אנגליה",   "ENG"),
    "portugal":    ("491", "Portugal",    "פורטוגל",  "POR"),
    "netherlands": ("469", "Netherlands", "הולנד",    "NED"),
    "belgium":     ("477", "Belgium",     "בלגיה",    "BEL"),
}

# Three groups of four (teams stay in their group, so the schedule is coherent).
# Live, in-progress matches: (id, home, away, hs, as, minute, drama, group)
_LIVE = [
    ("bsd-9900001", "argentina", "france",   2, 2, 88, 9.4, "Group A"),
    ("bsd-9900002", "brazil",    "spain",    1, 1, 76, 7.9, "Group B"),
    ("bsd-9900003", "england",   "portugal", 0, 0, 63, 6.6, "Group C"),
]
# Upcoming fixtures: (id, home, away, hours_from_now, group)
# Offsets capped so every fixture stays on TODAY (the live matches' date) — a full
# single-day slate for screenshots, rather than spilling across several days.
_SCHED = [
    ("bsd-9900010", "croatia",     "morocco",     2,  "Group A"),
    ("bsd-9900011", "germany",     "japan",       4,  "Group B"),
    ("bsd-9900012", "netherlands", "belgium",     6,  "Group C"),
    ("bsd-9900013", "argentina",   "croatia",     8,  "Group A"),
    ("bsd-9900014", "france",      "morocco",    10,  "Group A"),
    ("bsd-9900015", "brazil",      "germany",    12,  "Group B"),
    ("bsd-9900016", "spain",       "japan",      14,  "Group B"),
    ("bsd-9900017", "england",     "netherlands",16,  "Group C"),
    ("bsd-9900018", "portugal",    "belgium",    18,  "Group C"),
]

_LIVE_BY_ID = {m[0]: m for m in _LIVE}
_SCHED_BY_ID = {m[0]: m for m in _SCHED}
DEMO_IDS = set(_LIVE_BY_ID) | set(_SCHED_BY_ID)


def is_demo_id(canonical_id: str) -> bool:
    return DEMO_ENABLED and canonical_id in DEMO_IDS


def _round_he(grp: str) -> str:
    return grp.replace("Group ", "בית ")


def _side(key: str, score, xg=None) -> dict:
    tid, name, name_he, short = _TEAMS[key]
    return {
        "id": tid, "name": name, "name_he": name_he,
        "short_name": short, "short_name_he": name_he,
        "crest": f"https://sports.bzzoiro.com/img/team/{tid}/",
        "score": score, "score_ht": (min(score, 1) if score is not None else None),
        "score_pens": None, "xg": xg,
    }


def _momentum(minute: int) -> list[dict]:
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


def _base(id_: str, home: str, away: str, grp: str, status: str) -> dict:
    return {
        "id": id_, "source": "bsd", "source_event_id": int(id_[4:]),
        "competition": "world_cup", "competition_id_native": 27,
        "round_name": grp, "round_number": None, "status": status,
        "is_derby": False, "is_neutral_ground": True,
        "venue": {"name": "MetLife Stadium"},
        "home": _side(home, None), "away": _side(away, None),
    }


def games() -> list[dict]:
    """Feed/games-shaped demo WC matches: live (scored) + upcoming (scheduled)."""
    out = []
    for id_, home, away, hs, as_, minute, drama_, grp in _LIVE:
        m = _base(id_, home, away, grp, "live")
        m.update(minute=minute, period="2h", kickoff=_kickoff_live(minute), watch_score=drama_)
        m["home"] = _side(home, hs); m["away"] = _side(away, as_)
        out.append(m)
    for id_, home, away, hours, grp in _SCHED:
        m = _base(id_, home, away, grp, "scheduled")
        m.update(minute=None, period="pre", kickoff=_kickoff_future(hours), watch_score=None)
        out.append(m)
    return out


def detail(canonical_id: str) -> dict | None:
    live_ = _LIVE_BY_ID.get(canonical_id)
    if live_:
        id_, home, away, hs, as_, minute, drama_, grp = live_
        d = _base(id_, home, away, grp, "live")
        d.update(minute=minute, period="2h", kickoff=_kickoff_live(minute), watch_score=drama_,
                 round_name_he=_round_he(grp),
                 home=_side(home, hs, xg=2.4), away=_side(away, as_, xg=2.1),
                 momentum=_momentum(minute), xg_per_minute=[], shotmap=[],
                 incidents=(_hero_incidents() if canonical_id == "bsd-9900001" else []),
                 stats=_stats(2.4, 2.1),
                 livedata={"side": "away", "situation": "dangerous_attack", "x": 80, "y": 46},
                 odds={"home": 2.4, "draw": 3.2, "away": 3.0})
        return d
    sched = _SCHED_BY_ID.get(canonical_id)
    if sched:
        id_, home, away, hours, grp = sched
        d = _base(id_, home, away, grp, "scheduled")
        d.update(minute=None, period="pre", kickoff=_kickoff_future(hours), watch_score=None,
                 round_name_he=_round_he(grp), momentum=[], xg_per_minute=[], shotmap=[],
                 incidents=[], stats=None, livedata=None, odds={"home": 2.1, "draw": 3.3, "away": 3.4})
        return d
    return None


def drama(canonical_id: str) -> dict | None:
    m = _LIVE_BY_ID.get(canonical_id)
    if not m:
        return None     # scheduled demo matches have no live drama meter
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
    m = _LIVE_BY_ID.get(canonical_id)
    if not m:
        return None
    minute, total = m[5], m[6]
    out = []
    for t in range(1, minute + 1):
        d = max(0.0, min(10.0, total * (0.3 + 0.7 * (t / minute)) + 1.1 * math.sin(t / 5.0)))
        out.append({"m": t, "drama": round(d, 2), "state": round(d * 0.36, 2),
                    "momentum": round(d * 0.25, 2), "events": round(d * 0.2, 2), "stakes": 1.55})
    return out


def live(canonical_id: str) -> dict | None:
    m = _LIVE_BY_ID.get(canonical_id)
    if not m:
        return {"ws_active": False}     # scheduled: no live snapshot
    _id, home, away, hs, as_, minute, _drama, _grp = m
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
