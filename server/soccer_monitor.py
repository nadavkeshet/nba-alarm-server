"""Soccer alarm radar — polling thread that fires event-driven alarms.

Architecture in P2:
- One global thread (poll every 25s).
- On each tick: query all active soccer_alarms with a match_id (per-match alarms).
- Group alarms by match_id; fetch detail once per match (adapter caches anyway).
- Evaluate each alarm against the current state; fire via shared FCM helper.
- Mark one-shot alarms as fired (sets fired_at, is_active=False).

What we support in P2 (all event-driven, no Drama Engine yet):
- match            — fire when status flips to live (kickoff notify)
- late_drama       — minute >= 75 AND |home_score - away_score| <= 1
- knockout_et      — match enters extra-time or penalty shootout
- penalty          — penalty awarded incident, just-in-time
- red_card         — red card in last 20 min while score within 1

What's NOT in P2:
- Radar-style alarms (no specific match_id) — needs Drama Engine for the
  "fire on any worth-watching match" semantics. P3.
- comeback, hat_trick, big_chance — more complex, P3.
- drama_radar — P3 (Drama Engine).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

import soccer_source as ss
import drama_engine
from i18n import (
    t_title,
    t_soccer_body_kickoff,
    t_soccer_body_halftime,
    t_soccer_body_knockout_et,
    t_soccer_body_late_drama,
    t_soccer_body_penalty,
    t_soccer_body_red_card,
    t_soccer_body_sensation,
    t_soccer_body_drama_radar,
    t_soccer_body_player_goal,
    t_soccer_body_player_brace,
    t_soccer_body_player_hattrick,
    t_soccer_body_player_yellow,
    t_soccer_body_player_red,
)

logger = logging.getLogger(__name__)

# Wired by main.py at startup so this module stays import-safe even when
# firebase_admin / SQLAlchemy aren't initialized (e.g. unit tests).
_send_push: Callable[..., None] | None = None
_get_user_language: Callable[[str], str] | None = None
_fire_once_fn: Callable[..., bool] | None = None
_db_engine: Any = None
_alarm_model: Any = None  # SoccerAlarmDB class — injected to avoid circular import

POLL_INTERVAL = 25.0  # seconds

# Statuses that mean the match is definitively over — deactivate all its alarms immediately.
_TERMINAL_STATUSES = {"ft", "aet", "ap", "cancelled", "postponed", "abandoned"}


def wire(
    *,
    send_push: Callable[..., None],
    get_user_language: Callable[[str], str],
    fire_once: Callable[..., bool],
    db_engine: Any,
    alarm_model: Any,
) -> None:
    """Called once by main.py during startup to inject runtime dependencies."""
    global _send_push, _get_user_language, _fire_once_fn, _db_engine, _alarm_model
    _send_push = send_push
    _get_user_language = get_user_language
    _fire_once_fn = fire_once
    _db_engine = db_engine
    _alarm_model = alarm_model


# ---------------------------------------------------------------------------
# Lifecycle


_thread: threading.Thread | None = None
_stop = threading.Event()
# Set whenever the BSD WebSocket pushes a fresh `event` frame for any
# subscribed match. The radar wait loop watches it so an actual score /
# state change wakes us within ~1s instead of waiting up to POLL_INTERVAL.
_wake = threading.Event()


def start_soccer_radar() -> None:
    """Spawn the radar polling thread. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _wake.clear()
    _thread = threading.Thread(target=_radar_loop, name="soccer-radar", daemon=True)
    _thread.start()
    _register_ws_callback()
    logger.info("soccer radar thread started (poll=%.1fs, ws-wake enabled)", POLL_INTERVAL)


def stop_soccer_radar() -> None:
    _stop.set()


def is_radar_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _register_ws_callback() -> None:
    """Wire the BSD WS push stream into the radar so score changes
    short-circuit the poll interval. Best-effort: if the WS module isn't
    importable or the singleton hasn't started, the radar just keeps
    polling on its normal cadence."""
    try:
        import bsd_websocket as bw
        client = bw.client()

        def _on_push(event_id: int, kind: str, payload: dict) -> None:
            # Only react to score/time updates — livedata frames stream
            # every ~5s while a match is live and would otherwise wake the
            # radar constantly with no actionable change.
            if kind == "event":
                _wake.set()

        client.add_callback(_on_push)
        logger.info("soccer radar ↔ BSD WS callback wired")
    except Exception as exc:
        logger.warning("soccer radar: WS callback not wired: %s", exc)


# ---------------------------------------------------------------------------
# Main loop


def _radar_loop() -> None:
    while not _stop.is_set():
        try:
            _tick()
        except Exception as exc:
            logger.exception("soccer radar tick failed: %s", exc)
        # Wait up to POLL_INTERVAL OR until the WS push wakes us. Whichever
        # fires first, the next tick runs immediately after.
        woken = _wake.wait(POLL_INTERVAL)
        if woken:
            _wake.clear()


def _tick() -> None:
    if _alarm_model is None or _db_engine is None:
        logger.debug("soccer radar tick skipped — not wired yet")
        return

    from sqlalchemy.orm import Session  # local import to keep module import-safe
    with Session(_db_engine) as session:
        rows = session.query(_alarm_model).filter(
            _alarm_model.is_active.is_(True),
        ).all()
        if not rows:
            return
        # Snapshot to plain dicts so we can close the session before doing network I/O
        alarms = [
            {
                "id": r.id,
                "user_token": r.user_token,
                "alarm_type": r.alarm_type,
                "match_id": r.match_id,
                "competition_id": r.competition_id,
                "player_id": r.player_id,
                "threshold": r.threshold,
                "alert_type": r.alert_type,
                "config": r.config or {},
            }
            for r in rows
        ]

    # Two passes:
    # 1) Match-scoped alarms (match_id != None) — fetch each match once.
    # 2) Comp-scoped drama_radar alarms (match_id == None, competition_id != None)
    #    — fetch live matches in each requested competition once.

    match_scoped: dict[str, list[dict]] = defaultdict(list)
    comp_radar:   list[dict]            = []
    for a in alarms:
        if a["match_id"]:
            match_scoped[a["match_id"]].append(a)
        elif a["alarm_type"] == "drama_radar" and a["competition_id"]:
            comp_radar.append(a)

    # ── Pass 1: per-match
    for match_id, alarm_group in match_scoped.items():
        # A bsd-* id must be numeric after the prefix. A malformed one (e.g. a
        # leftover "bsd-test-match-1" fixture) can never resolve, so polling it
        # every cycle just spams warnings forever — deactivate it once.
        if match_id.startswith("bsd-") and not match_id[4:].isdigit():
            _deactivate_alarms([a["id"] for a in alarm_group],
                               reason=f"malformed match id {match_id}")
            continue
        try:
            detail = ss.get_soccer_match_detail(match_id)
        except Exception as exc:
            logger.warning("soccer radar: detail fetch failed for %s: %s", match_id, exc)
            continue
        if detail is None:
            continue

        # If the match is over, deactivate every alarm for it immediately — no more polling.
        status = (detail.get("status") or "").lower()
        if status in _TERMINAL_STATUSES:
            ids = [a["id"] for a in alarm_group]
            _deactivate_alarms(ids, reason=f"match {match_id} status={status}")
            continue

        for alarm in alarm_group:
            try:
                _evaluate_alarm(alarm, detail)
            except Exception as exc:
                logger.exception("alarm %s eval failed: %s", alarm["id"], exc)

    # ── Pass 2: competition-scoped drama radar
    if comp_radar:
        # Group comp-radar alarms by competition so we hit get_live_matches once per slug
        by_comp: dict[str, list[dict]] = defaultdict(list)
        for a in comp_radar:
            by_comp[a["competition_id"]].append(a)

        for comp_slug, alarm_group in by_comp.items():
            try:
                live_matches = ss.get_live_matches([comp_slug])
            except Exception as exc:
                logger.warning("drama_radar: live fetch failed for %s: %s", comp_slug, exc)
                continue
            for slim_match in live_matches:
                cid = slim_match.get("id")
                if not cid:
                    continue
                try:
                    detail = ss.get_soccer_match_detail(cid)
                except Exception as exc:
                    logger.warning("drama_radar: detail fetch failed for %s: %s", cid, exc)
                    continue
                if detail is None:
                    continue
                for alarm in alarm_group:
                    try:
                        _evaluate_drama_radar(alarm, detail)
                    except Exception as exc:
                        logger.exception("drama_radar alarm %s eval failed: %s", alarm["id"], exc)


# ---------------------------------------------------------------------------
# Evaluators


def _evaluate_alarm(alarm: dict, match: dict) -> None:
    alarm_type = alarm["alarm_type"]
    status = match.get("status")
    home_name = (match.get("home") or {}).get("name", "")
    away_name = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")

    if alarm_type == "match":
        # Kickoff notify — fire the first tick after status transitions from scheduled to live.
        if status in ("live", "1h"):
            _fire(alarm, "soccer_kickoff",
                  t_soccer_body_kickoff, home_name, away_name, competition,
                  signal="kickoff", one_shot=True)
        return

    if alarm_type == "late_drama":
        minute = match.get("minute") or 0
        home_score = (match.get("home") or {}).get("score") or 0
        away_score = (match.get("away") or {}).get("score") or 0
        # Configurable: max_diff (0–3, default 1), last_minutes (1–45, default 15).
        # Empty config reproduces the legacy ≥75'/≤1 behavior. Explicit None
        # checks so a chosen max_diff of 0 (tied only) isn't coerced to 1.
        cfg = alarm.get("config") or {}
        md = cfg.get("max_diff")
        max_diff = 1 if md is None else max(0, min(3, int(md)))
        lm = cfg.get("last_minutes")
        last_minutes = 15 if lm is None else max(1, min(45, int(lm)))
        min_minute = max(0, 90 - last_minutes)
        if minute >= min_minute and abs(home_score - away_score) <= max_diff and status in ("live", "1h", "2h"):
            _fire(alarm, "soccer_late_drama",
                  lambda h, a, c, lang: t_soccer_body_late_drama(h, a, c, minute, lang),
                  home_name, away_name, competition,
                  signal="late_drama", one_shot=True)
        return

    if alarm_type == "match_moments":
        # Modular kickoff/halftime/extra-time/penalties. Each enabled moment
        # fires once via its own dedup signal; one_shot=False so later moments
        # still fire. The row is deactivated at full-time by the terminal-status
        # sweep in _tick, so no per-moment one_shot is needed.
        cfg = alarm.get("config") or {}
        if cfg.get("kickoff") and status in ("live", "1h"):
            _fire(alarm, "soccer_kickoff", t_soccer_body_kickoff,
                  home_name, away_name, competition, signal="mm_kickoff", one_shot=False)
        if cfg.get("halftime") and status == "ht":
            _fire(alarm, "soccer_halftime", t_soccer_body_halftime,
                  home_name, away_name, competition, signal="mm_halftime", one_shot=False)
        if cfg.get("extra_time") and status in ("et", "pens"):
            _fire(alarm, "soccer_knockout_et", t_soccer_body_knockout_et,
                  home_name, away_name, competition, signal="mm_et", one_shot=False)
        if cfg.get("penalties") and status == "pens":
            _fire(alarm, "soccer_knockout_et", t_soccer_body_knockout_et,
                  home_name, away_name, competition, signal="mm_pens", one_shot=False)
        return

    if alarm_type == "card_penalty":
        # Modular red-card / penalty. Fire on ANY such event (no close-game gate);
        # per-event dedup signal means each distinct event notifies exactly once.
        cfg = alarm.get("config") or {}
        if cfg.get("red_card"):
            red = _find_recent_red_card(match)
            if red:
                _fire(alarm, "soccer_red_card",
                      lambda h, a, c, lang: t_soccer_body_red_card(h, a, c, red["minute"], lang),
                      home_name, away_name, competition,
                      signal=f"cp_red_{red.get('minute', 0)}", one_shot=False)
        if cfg.get("penalty") and _recent_incident(match, types=("penalty",), window_min=2):
            minute = match.get("minute") or 0
            _fire(alarm, "soccer_penalty",
                  t_soccer_body_penalty, home_name, away_name, competition,
                  signal=f"cp_pen_{minute}", one_shot=False)
        return

    if alarm_type == "sensation":
        # Underdog leading. The underdog side is resolved + stored in config at
        # creation; here we just check whether that side is ahead.
        cfg = alarm.get("config") or {}
        underdog_is_home = bool(cfg.get("underdog_is_home"))
        underdog_name = cfg.get("underdog_name") or (home_name if underdog_is_home else away_name)
        if status in ("live", "1h", "2h", "et"):
            hs = (match.get("home") or {}).get("score") or 0
            as_ = (match.get("away") or {}).get("score") or 0
            leading = (hs > as_) if underdog_is_home else (as_ > hs)
            if leading:
                minute = match.get("minute") or 0
                _fire(alarm, "soccer_sensation",
                      lambda h, a, c, lang: t_soccer_body_sensation(underdog_name, c, minute, lang),
                      home_name, away_name, competition,
                      signal="sensation", one_shot=True)
        return

    if alarm_type == "knockout_et":
        if status in ("et", "pens"):
            _fire(alarm, "soccer_knockout_et",
                  t_soccer_body_knockout_et, home_name, away_name, competition,
                  signal="knockout_et", one_shot=True)
        return

    if alarm_type == "penalty":
        # Look for a penalty incident in last 2 minutes
        if _recent_incident(match, types=("penalty",), window_min=2):
            _fire(alarm, "soccer_penalty",
                  t_soccer_body_penalty, home_name, away_name, competition,
                  signal="penalty", one_shot=False)  # can fire on multiple pens in same match
        return

    if alarm_type == "drama_radar":
        # Match-scoped drama_radar: same evaluator as comp-scoped pass 2.
        _evaluate_drama_radar(alarm, match)
        return

    if alarm_type == "red_card":
        # Red card in last 20 min, score within 1
        home_score = (match.get("home") or {}).get("score") or 0
        away_score = (match.get("away") or {}).get("score") or 0
        if abs(home_score - away_score) > 1:
            return
        minute = match.get("minute") or 0
        red = _find_recent_red_card(match)
        if red and red.get("minute", 0) >= minute - 20:
            _fire(alarm, "soccer_red_card",
                  lambda h, a, c, lang: t_soccer_body_red_card(h, a, c, red["minute"], lang),
                  home_name, away_name, competition,
                  signal=f"red_card_{red.get('minute',0)}", one_shot=True)
        return

    if alarm_type == "player_alert":
        _evaluate_player_alert(alarm, match)
        return

    # Unknown alarm_type → no-op (keeps forward compat for P3 types)


# ---------------------------------------------------------------------------
# Player alert evaluator


def _is_clutch_goal(is_home: bool | None, home_score: int, away_score: int, minute: int) -> bool:
    """True if this goal is match-decisive: equalizer, go-ahead in 70+, or reduces a 2+ deficit."""
    if is_home is None:
        return False
    scorer_score  = home_score if is_home else away_score
    opponent_score = away_score if is_home else home_score
    diff_after  = scorer_score - opponent_score
    diff_before = (scorer_score - 1) - opponent_score
    if diff_after == 0:                               # equalizer
        return True
    if minute >= 70 and diff_after == 1 and diff_before <= 0:  # late go-ahead
        return True
    if diff_before <= -2 and diff_after > diff_before:         # reduces 2+ deficit
        return True
    return False


def _goal_context_str(is_home: bool | None, home_score: int, away_score: int) -> str:
    if is_home is None:
        return ""
    scorer_score  = home_score if is_home else away_score
    opponent_score = away_score if is_home else home_score
    diff = scorer_score - opponent_score
    if diff == 0:
        return "equalizer"
    if diff == 1:
        return "go-ahead"
    if diff == -1:
        return "comeback"
    return ""


def _evaluate_player_alert(alarm: dict, match: dict) -> None:
    """Evaluate a player_alert alarm against current match incidents.

    Config keys (all optional, sensible defaults):
        goal          bool  Fire on every goal.              default True
        clutch_only   bool  Only fire on clutch goals.       default False
        hat_trick_watch bool  Brace + hat-trick escalation.  default True
        card_yellow   bool  Yellow card alert.               default False
        card_red      bool  Red card alert.                  default True
    """
    player_id = str(alarm.get("player_id") or "")
    if not player_id:
        return

    config = alarm.get("config") or {}
    goal_enabled    = bool(config.get("goal", True))
    clutch_only     = bool(config.get("clutch_only", False))
    hat_trick_watch = bool(config.get("hat_trick_watch", True))
    card_yellow     = bool(config.get("card_yellow", False))
    card_red        = bool(config.get("card_red", True))

    incidents  = match.get("incidents") or []
    home_name  = (match.get("home") or {}).get("name", "")
    away_name  = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")

    # Gather all goals by this player (chronological — incidents are ordered)
    player_goals = [
        inc for inc in incidents
        if (inc.get("type") or "").lower() == "goal"
        and str(inc.get("player_id") or "") == player_id
    ]
    p_name = player_goals[0].get("player", "") if player_goals else ""

    # ── Hat-trick watch ──────────────────────────────────────────────────────
    if hat_trick_watch and player_goals:
        count = len(player_goals)
        if count == 2:
            _fire(alarm, "soccer_player_brace",
                  lambda h, a, c, lang, _n=p_name: t_soccer_body_player_brace(_n, c, lang),
                  home_name, away_name, competition,
                  signal="brace", one_shot=False)
        elif count >= 3:
            _fire(alarm, "soccer_player_hattrick",
                  lambda h, a, c, lang, _n=p_name: t_soccer_body_player_hattrick(_n, c, lang),
                  home_name, away_name, competition,
                  signal="hattrick", one_shot=False)

    # ── Individual goal alerts ───────────────────────────────────────────────
    if goal_enabled:
        for inc in player_goals:
            minute    = inc.get("minute") or 0
            hs        = inc.get("home_score") or 0
            as_       = inc.get("away_score") or 0
            is_home   = inc.get("is_home")
            is_clutch = _is_clutch_goal(is_home, hs, as_, minute)
            if clutch_only and not is_clutch:
                continue
            ctx      = _goal_context_str(is_home, hs, as_)
            inc_name = inc.get("player") or p_name
            title_key = "soccer_player_clutch_goal" if (clutch_only and is_clutch) else "soccer_player_goal"
            _fire(
                alarm, title_key,
                lambda h, a, c, lang, _n=inc_name, _hs=hs, _as=as_, _m=minute, _ctx=ctx:
                    t_soccer_body_player_goal(_n, _hs, _as, _m, c, _ctx, lang),
                home_name, away_name, competition,
                signal=f"goal_m{minute}", one_shot=False,
            )

    # ── Card watch ───────────────────────────────────────────────────────────
    if card_yellow or card_red:
        for inc in incidents:
            if (inc.get("type") or "").lower() != "card":
                continue
            if str(inc.get("player_id") or "") != player_id:
                continue
            card_type  = (inc.get("card_type") or "").lower()
            is_yellow  = "yellow" in card_type and "red" not in card_type
            is_red     = "red" in card_type
            minute     = inc.get("minute") or 0
            card_name  = inc.get("player") or p_name
            if card_yellow and is_yellow:
                _fire(
                    alarm, "soccer_player_yellow",
                    lambda h, a, c, lang, _n=card_name, _m=minute:
                        t_soccer_body_player_yellow(_n, h, a, _m, lang),
                    home_name, away_name, competition,
                    signal=f"yellow_m{minute}", one_shot=False,
                )
            if card_red and is_red:
                _fire(
                    alarm, "soccer_player_red",
                    lambda h, a, c, lang, _n=card_name, _m=minute:
                        t_soccer_body_player_red(_n, h, a, _m, lang),
                    home_name, away_name, competition,
                    signal=f"red_m{minute}", one_shot=False,
                )


# ---------------------------------------------------------------------------
# Drama Engine evaluator (P3)


_DEFAULT_DRAMA_THRESHOLD = 7.0


def _evaluate_drama_radar(alarm: dict, match: dict) -> None:
    """Compute the Drama Engine score for `match` and fire if it crosses
    the user's threshold. Persists a row to `drama_snapshots` either way
    so we can tune weights from real data over time."""
    status = (match.get("status") or "").lower()
    # Only meaningful during a live or live-like phase
    if status not in {"live", "ht", "et", "pens", "1h", "2h"}:
        return

    ctx = drama_engine.context_from_match_detail(match)
    score = drama_engine.compute_drama(ctx)

    # Persist snapshot (best-effort — never block firing on log failure)
    _log_drama_snapshot(match.get("id"), match.get("minute") or 0, score)

    threshold = alarm.get("threshold") if alarm.get("threshold") is not None else _DEFAULT_DRAMA_THRESHOLD
    if score.total < threshold:
        return

    # Bucket the score so a hovering 7.2 doesn't re-fire each tick. A bump
    # from 7.x → 8.x will, because the bucket changes.
    bucket = int(score.total)
    home_name = (match.get("home") or {}).get("name", "")
    away_name = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")
    match_id = match.get("id", "")

    _fire(
        alarm, "soccer_drama_radar",
        lambda h, a, c, lang: t_soccer_body_drama_radar(h, a, c, score.total, lang),
        home_name, away_name, competition,
        signal=f"drama_{match_id}_{bucket}",
        one_shot=False,  # radar keeps watching other matches
    )


def _log_drama_snapshot(match_id: str | None, minute: int, score) -> None:
    if _db_engine is None or not match_id:
        return
    try:
        from sqlalchemy import text
        with _db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO drama_snapshots "
                    "(match_id, sport, minute, state_score, momentum_score, "
                    " events_score, stakes_mult, drama_total) "
                    "VALUES (:m, 'football', :min, :st, :mo, :ev, :sk, :tot)"
                ),
                {
                    "m":   match_id,
                    "min": minute,
                    "st":  score.state,
                    "mo":  score.momentum,
                    "ev":  score.events,
                    "sk":  score.stakes,
                    "tot": score.total,
                },
            )
            conn.commit()
    except Exception as exc:
        logger.debug("drama snapshot log failed for %s: %s", match_id, exc)


def _recent_incident(match: dict, *, types: tuple[str, ...], window_min: int) -> bool:
    """True if an incident with one of `types` occurred within the last `window_min` minutes."""
    incidents = match.get("incidents") or []
    minute_now = match.get("minute") or 0
    cutoff = minute_now - window_min
    for inc in incidents:
        # BSD shape: type=penalty, minute=N
        # Sofascore shape: incidentType=penalty, time=N
        inc_type = (inc.get("type") or inc.get("incidentType") or "").lower()
        inc_min = inc.get("minute") or inc.get("time") or 0
        if any(t in inc_type for t in types) and inc_min >= cutoff:
            return True
    return False


def _find_recent_red_card(match: dict) -> dict | None:
    """Return the most recent red-card incident, or None."""
    incidents = match.get("incidents") or []
    candidates: list[dict] = []
    for inc in incidents:
        inc_type = (inc.get("type") or inc.get("incidentType") or "").lower()
        card_type = (inc.get("card_type") or inc.get("incidentClass") or "").lower()
        if "card" in inc_type and "red" in card_type:
            candidates.append({
                "minute": inc.get("minute") or inc.get("time") or 0,
                "player": inc.get("player") or inc.get("playerName") or "",
            })
        # Sofascore sometimes types red cards as "card" with class "red"
        if inc_type == "red" or card_type == "redcard":
            candidates.append({
                "minute": inc.get("minute") or inc.get("time") or 0,
                "player": inc.get("player") or "",
            })
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["minute"])


# ---------------------------------------------------------------------------
# Deactivation helper


def _deactivate_alarms(alarm_ids: list[int], *, reason: str) -> None:
    """Bulk-mark a list of soccer alarm rows as inactive."""
    if not alarm_ids or _db_engine is None or _alarm_model is None:
        return
    try:
        from sqlalchemy.orm import Session
        with Session(_db_engine) as session:
            session.query(_alarm_model).filter(
                _alarm_model.id.in_(alarm_ids),
                _alarm_model.is_active.is_(True),
            ).update({"is_active": False}, synchronize_session=False)
            session.commit()
        logger.info("soccer radar: deactivated %d alarm(s) — %s", len(alarm_ids), reason)
    except Exception as exc:
        logger.warning("soccer radar: deactivate failed (%s): %s", reason, exc)


# ---------------------------------------------------------------------------
# Firing


def _fire(
    alarm: dict,
    title_key: str,
    body_fn,
    home_name: str,
    away_name: str,
    competition: str,
    *,
    signal: str,
    one_shot: bool,
) -> None:
    """Send the notification + persist fired_at. Dedup via _fire_once_fn."""
    if _send_push is None or _fire_once_fn is None:
        logger.debug("soccer monitor not fully wired; would have fired alarm %s/%s", alarm["id"], signal)
        return

    user_token = alarm["user_token"]
    dedup_key = f"soccer_{alarm['id']}_{signal}"
    if not _fire_once_fn(dedup_key, user_token):
        return

    lang = "en"
    if _get_user_language is not None:
        try:
            lang = _get_user_language(user_token) or "en"
        except Exception:
            pass

    title = t_title(title_key, lang)
    body = body_fn(home_name, away_name, competition, lang)
    alert_type = alarm.get("alert_type") or "alarm"

    try:
        _send_push(user_token, title, body, alert_type=alert_type)
    except Exception as exc:
        logger.warning("soccer FCM send failed for alarm %s: %s", alarm["id"], exc)
        return

    if one_shot and _db_engine is not None and _alarm_model is not None:
        try:
            from sqlalchemy.orm import Session
            with Session(_db_engine) as session:
                row = session.get(_alarm_model, alarm["id"])
                if row is not None:
                    row.is_active = False
                    row.fired_at = datetime.now(timezone.utc)
                    session.commit()
        except Exception as exc:
            logger.warning("soccer alarm %s: failed to mark fired: %s", alarm["id"], exc)
