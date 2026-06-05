"""Football Historic Moments detection.

Self-contained, stateless detector that scans a normalized soccer `match` dict
(the shape produced by ``soccer_source.py`` and consumed by
``soccer_monitor.py``) and returns every "historic moment" that is currently
TRUE in this snapshot.

The detector is intentionally *idempotent across ticks*: it never tracks prior
state. Each moment carries a stable ``dedup`` suffix so the caller's fire-once
store can ensure the same distinct moment notifies exactly once. The caller is
also responsible for filtering by user-enabled categories.

Every field access is defensive — malformed incidents must never raise.
"""

from __future__ import annotations

from typing import Any


# Canonical, ordered list of every category key this module can emit.
CATEGORY_KEYS: list[str] = [
    # GROUP A — player goal feats
    "hat_trick",
    "poker",
    "five_plus",
    "fast_hat_trick",
    "penalty_hat_trick",
    "goal_involvements",
    # GROUP B — scoreline / team
    "rout",
    "goal_feast",
    "end_to_end",
    "goal_blitz",
    "lightning_goal",
    "last_gasp_winner",
    "historic_comeback",
    # GROUP C — discipline
    "early_red",
    "mass_red_cards",
    "penalty_barrage",
    "stoppage_penalty",
    # GROUP D — rarities
    "own_goal",
    "multiple_own_goals",
    "goalkeeper_goal",
    "defender_brace",
    # GROUP E — shootout
    "marathon_shootout",
    "shootout_decided",
]


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _str_id(v: Any) -> str:
    """Stable string form of a player id (ids may be int or str)."""
    if v is None:
        return ""
    return str(v)


def _incidents(match: dict) -> list[dict]:
    incs = match.get("incidents")
    if not isinstance(incs, list):
        return []
    return [i for i in incs if isinstance(i, dict)]


def _itype(inc: dict) -> str:
    return str(inc.get("type", "")).lower()


def _is_goal(inc: dict) -> bool:
    """A real (non-own) goal incident, including penalty/header flavours."""
    t = _itype(inc)
    if t == "owngoal":
        return False
    if t == "goal":
        return True
    # Some sources mark a penalty goal with type='penalty'.
    if "penalt" in t:
        return True
    return False


def _is_owngoal(inc: dict) -> bool:
    return _itype(inc) == "owngoal"


def _is_penalty_goal(inc: dict) -> bool:
    """Defensive penalty detection across the many source spellings."""
    if not _is_goal(inc):
        return False
    if inc.get("goal_type") and "penalt" in str(inc.get("goal_type")).lower():
        return True
    if "penalt" in _itype(inc):
        return True
    if inc.get("is_penalty"):
        return True
    return False


def _is_header_goal(inc: dict) -> bool:
    if not _is_goal(inc):
        return False
    return str(inc.get("goal_type", "")).lower() == "header"


def _is_card(inc: dict) -> bool:
    return _itype(inc) == "card"


def _is_red(inc: dict) -> bool:
    if not _is_card(inc):
        return False
    ct = str(inc.get("card_type", "")).lower()
    return "red" in ct or "yellowred" in ct


def _minute(inc: dict) -> int:
    return _safe_int(inc.get("minute"), 0)


def _scores(match: dict) -> tuple[int, int]:
    home = match.get("home") or {}
    away = match.get("away") or {}
    return _safe_int(home.get("score"), 0), _safe_int(away.get("score"), 0)


def _team_name(match: dict, is_home: bool) -> str:
    side = (match.get("home") if is_home else match.get("away")) or {}
    return str(side.get("name") or "")


def _competition(match: dict) -> str:
    return str(match.get("competition") or "")


def _base_params(match: dict) -> dict:
    home = match.get("home") or {}
    away = match.get("away") or {}
    hs, as_ = _scores(match)
    return {
        "home": hs,
        "away": as_,
        "home_score": hs,
        "away_score": as_,
        "home_name": str(home.get("name") or ""),
        "away_name": str(away.get("name") or ""),
        "competition": _competition(match),
    }


# ---------------------------------------------------------------------------
# Goal aggregation by player
# ---------------------------------------------------------------------------

def _goal_incidents(match: dict) -> list[dict]:
    return [i for i in _incidents(match) if _is_goal(i)]


def _shootout_kicks(match: dict) -> list[dict]:
    """Penalty-shootout kicks: goal incidents at minute >= 121 (the convention
    used in soccer_source.py)."""
    return [i for i in _incidents(match) if _is_goal(i) and _minute(i) >= 121]


def _regular_goals(match: dict) -> list[dict]:
    """Goals that count toward in-game tallies (exclude shootout kicks)."""
    return [i for i in _goal_incidents(match) if _minute(i) < 121]


def _player_goal_stats(match: dict) -> dict[str, dict]:
    """Aggregate per-player goal info (excludes own goals and shootout kicks)."""
    stats: dict[str, dict] = {}
    for inc in _regular_goals(match):
        pid = _str_id(inc.get("player_id"))
        if not pid:
            continue
        rec = stats.setdefault(pid, {
            "player_id": inc.get("player_id"),
            "player": inc.get("player"),
            "is_home": inc.get("is_home"),
            "goals": 0,
            "penalty_goals": 0,
            "minutes": [],
        })
        rec["goals"] += 1
        rec["minutes"].append(_minute(inc))
        if rec.get("player") is None and inc.get("player") is not None:
            rec["player"] = inc.get("player")
        if _is_penalty_goal(inc):
            rec["penalty_goals"] += 1
    return stats


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def detect_historic(match: dict, *, positions: dict | None = None) -> list[dict]:
    """Return ALL historic moments present in the current match snapshot.

    Each returned dict has the shape::

        {"category": str, "dedup": str, "params": dict}

    ``dedup`` is a stable per-distinct-moment suffix; ``params`` holds
    everything the i18n body might need.
    """
    if not isinstance(match, dict):
        return []

    out: list[dict] = []
    base = _base_params(match)
    hs, as_ = base["home_score"], base["away_score"]

    try:
        out.extend(_detect_player_feats(match, base, positions))
    except Exception:
        pass
    try:
        out.extend(_detect_scoreline(match, base, hs, as_))
    except Exception:
        pass
    try:
        out.extend(_detect_discipline(match, base))
    except Exception:
        pass
    try:
        out.extend(_detect_rarities(match, base, positions))
    except Exception:
        pass
    try:
        out.extend(_detect_shootout(match, base))
    except Exception:
        pass

    return out


# ---------------------------------------------------------------------------
# GROUP A — player goal feats
# ---------------------------------------------------------------------------

def _detect_player_feats(match: dict, base: dict, positions: dict | None) -> list[dict]:
    out: list[dict] = []
    stats = _player_goal_stats(match)

    # Assists: count by matching the scoring player's NAME against assist names.
    assist_names: dict[str, int] = {}
    for inc in _regular_goals(match):
        a = inc.get("assist")
        if a:
            key = str(a).strip().lower()
            if key:
                assist_names[key] = assist_names.get(key, 0) + 1

    for pid, rec in stats.items():
        goals = rec["goals"]
        player = rec.get("player") or ""
        is_home = rec.get("is_home")
        team = _team_name(match, bool(is_home)) if is_home is not None else ""
        opp = _team_name(match, not bool(is_home)) if is_home is not None else ""
        pp = dict(base)
        pp.update({
            "player": player,
            "player_id": rec.get("player_id"),
            "team": team,
            "opp": opp,
            "count": goals,
        })

        if goals == 3:
            out.append({"category": "hat_trick", "dedup": f"hat_trick_{pid}", "params": dict(pp)})
        elif goals == 4:
            out.append({"category": "poker", "dedup": f"poker_{pid}", "params": dict(pp)})
        elif goals >= 5:
            out.append({"category": "five_plus", "dedup": f"five_{pid}_{goals}", "params": dict(pp)})

        # fast hat-trick: 1st -> 3rd goal span <= 10 minutes
        if goals >= 3:
            mins = sorted(m for m in rec["minutes"])
            if len(mins) >= 3 and (mins[2] - mins[0]) <= 10:
                fp = dict(pp)
                fp["span"] = mins[2] - mins[0]
                out.append({"category": "fast_hat_trick", "dedup": f"fast_ht_{pid}", "params": fp})

        # penalty hat-trick
        if rec["penalty_goals"] >= 3:
            php = dict(pp)
            php["count"] = rec["penalty_goals"]
            out.append({"category": "penalty_hat_trick", "dedup": f"pen_ht_{pid}", "params": php})

        # goal involvements: goals + assists >= 4 (assists matched by name)
        name_key = str(player).strip().lower()
        assists = assist_names.get(name_key, 0) if name_key else 0
        total = goals + assists
        if total >= 4:
            ip = dict(pp)
            ip.update({"goals": goals, "assists": assists, "total": total})
            out.append({"category": "goal_involvements", "dedup": f"involve_{pid}_{total}", "params": ip})

    return out


# ---------------------------------------------------------------------------
# GROUP B — scoreline / team
# ---------------------------------------------------------------------------

def _detect_scoreline(match: dict, base: dict, hs: int, as_: int) -> list[dict]:
    out: list[dict] = []
    margin = abs(hs - as_)
    total = hs + as_

    if margin >= 5:
        p = dict(base)
        p["margin"] = margin
        out.append({"category": "rout", "dedup": f"rout_{margin}", "params": p})

    if total >= 7:
        p = dict(base)
        p["total"] = total
        out.append({"category": "goal_feast", "dedup": f"feast_{total}", "params": p})

    if hs >= 3 and as_ >= 3:
        out.append({"category": "end_to_end", "dedup": f"e2e_{hs}_{as_}", "params": dict(base)})

    # goal blitz: 3 goals (either side) within a 5-minute window.
    goals = _regular_goals(match)
    mins = sorted(_minute(g) for g in goals)
    for i in range(len(mins) - 2):
        if mins[i + 2] - mins[i] <= 5:
            third = mins[i + 2]
            p = dict(base)
            p["minute"] = third
            p["window"] = mins[i + 2] - mins[i]
            out.append({"category": "goal_blitz", "dedup": f"blitz_{third}", "params": p})
            break

    # lightning goal: a goal at minute <= 1
    for g in goals:
        m = _minute(g)
        if m <= 1:
            pid = _str_id(g.get("player_id"))
            is_home = g.get("is_home")
            p = dict(base)
            p.update({
                "player": g.get("player") or "",
                "player_id": g.get("player_id"),
                "team": _team_name(match, bool(is_home)) if is_home is not None else "",
                "minute": m,
            })
            out.append({"category": "lightning_goal", "dedup": f"lightning_{pid}_{m}", "params": p})

    # last-gasp winner & historic comeback: walk running scores.
    out.extend(_detect_comeback_and_lastgasp(match, base, goals))

    return out


def _detect_comeback_and_lastgasp(match: dict, base: dict, goals: list[dict]) -> list[dict]:
    out: list[dict] = []
    # Reconstruct running score after each goal, in chronological order.
    ordered = sorted(goals, key=_minute)
    rh = ra = 0
    home_behind_by3 = away_behind_by3 = False
    seen_lastgasp = set()
    for g in ordered:
        is_home = g.get("is_home")
        prev_h, prev_a = rh, ra
        # own goals are excluded from `goals` already (only real goals here),
        # but is_home indicates the benefiting side regardless.
        if is_home is True:
            rh += 1
        elif is_home is False:
            ra += 1
        else:
            continue

        m = _minute(g)
        # last-gasp winner: goal at minute >= 90 that takes the lead, having
        # been level or behind before.
        if m >= 90:
            scorer_was_ahead = (prev_h > prev_a) if is_home else (prev_a > prev_h)
            scorer_now_ahead = (rh > ra) if is_home else (ra > rh)
            if (not scorer_was_ahead) and scorer_now_ahead and m not in seen_lastgasp:
                seen_lastgasp.add(m)
                is_home_b = bool(is_home)
                p = dict(base)
                p.update({
                    "player": g.get("player") or "",
                    "team": _team_name(match, is_home_b),
                    "opp": _team_name(match, not is_home_b),
                    "minute": m,
                    "home_score": rh,
                    "away_score": ra,
                    "home": rh,
                    "away": ra,
                })
                out.append({"category": "last_gasp_winner", "dedup": f"lastgasp_{m}", "params": p})

        # comeback tracking: was a side ever behind by >= 3?
        if rh - ra <= -3:
            home_behind_by3 = True
        if ra - rh <= -3:
            away_behind_by3 = True

    fh, fa = _scores(match)
    if home_behind_by3 and fh >= fa:
        p = dict(base)
        p["side"] = "home"
        p["team"] = _team_name(match, True)
        p["opp"] = _team_name(match, False)
        out.append({"category": "historic_comeback", "dedup": "comeback_home", "params": p})
    if away_behind_by3 and fa >= fh:
        p = dict(base)
        p["side"] = "away"
        p["team"] = _team_name(match, False)
        p["opp"] = _team_name(match, True)
        out.append({"category": "historic_comeback", "dedup": "comeback_away", "params": p})

    return out


# ---------------------------------------------------------------------------
# GROUP C — discipline
# ---------------------------------------------------------------------------

def _detect_discipline(match: dict, base: dict) -> list[dict]:
    out: list[dict] = []
    incs = _incidents(match)

    reds = [i for i in incs if _is_red(i)]
    # early red: a red card at minute <= 10
    for r in reds:
        m = _minute(r)
        if m <= 10:
            pid = _str_id(r.get("player_id"))
            is_home = r.get("is_home")
            p = dict(base)
            p.update({
                "player": r.get("player") or "",
                "player_id": r.get("player_id"),
                "team": _team_name(match, bool(is_home)) if is_home is not None else "",
                "minute": m,
            })
            out.append({"category": "early_red", "dedup": f"early_red_{pid}", "params": p})

    # mass red cards: prefer counting from incidents; fall back to stats.
    red_count = len(reds)
    stats = match.get("stats") or {}
    rc = stats.get("red_cards") if isinstance(stats, dict) else None
    if isinstance(rc, dict):
        red_count = max(red_count, _safe_int(rc.get("home"), 0) + _safe_int(rc.get("away"), 0))
    if red_count >= 3:
        p = dict(base)
        p["count"] = red_count
        out.append({"category": "mass_red_cards", "dedup": f"mass_red_{red_count}", "params": p})

    # penalty barrage: total penalties awarded >= 3 (penalty goals + misses).
    pen_goals = [g for g in _regular_goals(match) if _is_penalty_goal(g)]
    pen_misses = [i for i in incs if "penalt" in _itype(i) and i.get("missed")]
    pen_count = len(pen_goals) + len(pen_misses)
    if pen_count >= 3:
        p = dict(base)
        p["count"] = pen_count
        out.append({"category": "penalty_barrage", "dedup": f"pen_barrage_{pen_count}", "params": p})

    # stoppage penalty: a penalty goal at minute >= 90.
    for g in pen_goals:
        m = _minute(g)
        if m >= 90:
            is_home = g.get("is_home")
            p = dict(base)
            p.update({
                "player": g.get("player") or "",
                "team": _team_name(match, bool(is_home)) if is_home is not None else "",
                "minute": m,
            })
            out.append({"category": "stoppage_penalty", "dedup": f"stoppage_pen_{m}", "params": p})

    return out


# ---------------------------------------------------------------------------
# GROUP D — rarities
# ---------------------------------------------------------------------------

def _detect_rarities(match: dict, base: dict, positions: dict | None) -> list[dict]:
    out: list[dict] = []
    incs = _incidents(match)
    own_goals = [i for i in incs if _is_owngoal(i)]

    for og in own_goals:
        m = _minute(og)
        pid = _str_id(og.get("player_id"))
        # is_home indicates the BENEFITING side; the scorer is on the other team.
        benefits_home = og.get("is_home")
        p = dict(base)
        p.update({
            "player": og.get("player") or "",
            "player_id": og.get("player_id"),
            "minute": m,
            # the team that conceded the OG (the scorer's own team)
            "team": _team_name(match, not bool(benefits_home)) if benefits_home is not None else "",
            "beneficiary": _team_name(match, bool(benefits_home)) if benefits_home is not None else "",
        })
        out.append({"category": "own_goal", "dedup": f"og_{m}_{pid}", "params": p})

    if len(own_goals) >= 2:
        p = dict(base)
        p["count"] = len(own_goals)
        out.append({"category": "multiple_own_goals", "dedup": f"multi_og_{len(own_goals)}", "params": p})

    # GK / defender categories require positions.
    if not positions:
        return out

    def _pos(pid: Any) -> str:
        return str(positions.get(_str_id(pid), "") or "")

    # goalkeeper goal: a (real) goal whose scorer is a GK.
    for g in _regular_goals(match):
        pos = _pos(g.get("player_id"))
        if pos.upper().startswith("G"):
            pid = _str_id(g.get("player_id"))
            is_home = g.get("is_home")
            p = dict(base)
            p.update({
                "player": g.get("player") or "",
                "player_id": g.get("player_id"),
                "team": _team_name(match, bool(is_home)) if is_home is not None else "",
                "minute": _minute(g),
            })
            out.append({"category": "goalkeeper_goal", "dedup": f"gk_goal_{pid}", "params": p})

    # defender brace: a defender (pos starts "D") with >= 2 goals.
    stats = _player_goal_stats(match)
    for pid, rec in stats.items():
        if rec["goals"] >= 2 and _pos(rec.get("player_id")).upper().startswith("D"):
            is_home = rec.get("is_home")
            p = dict(base)
            p.update({
                "player": rec.get("player") or "",
                "player_id": rec.get("player_id"),
                "team": _team_name(match, bool(is_home)) if is_home is not None else "",
                "count": rec["goals"],
            })
            out.append({"category": "defender_brace", "dedup": f"def_brace_{pid}", "params": p})

    return out


# ---------------------------------------------------------------------------
# GROUP E — shootout
# ---------------------------------------------------------------------------

def _detect_shootout(match: dict, base: dict) -> list[dict]:
    out: list[dict] = []
    status = str(match.get("status") or "").lower()
    kicks = _shootout_kicks(match)
    had_shootout = len(kicks) > 0

    # marathon shootout: pens/ap reaching sudden death (>= 10 kicks total).
    if status in ("pens", "ap") and len(kicks) >= 10:
        p = dict(base)
        p["count"] = len(kicks)
        out.append({"category": "marathon_shootout", "dedup": "marathon_shootout", "params": p})

    # shootout decided.
    if status == "ap" or (status == "ft" and had_shootout):
        p = dict(base)
        p["count"] = len(kicks)
        out.append({"category": "shootout_decided", "dedup": "shootout_decided", "params": p})

    return out
