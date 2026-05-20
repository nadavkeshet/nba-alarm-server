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
from i18n import (
    t_title,
    t_soccer_body_kickoff,
    t_soccer_body_knockout_et,
    t_soccer_body_late_drama,
    t_soccer_body_penalty,
    t_soccer_body_red_card,
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


def start_soccer_radar() -> None:
    """Spawn the radar polling thread. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_radar_loop, name="soccer-radar", daemon=True)
    _thread.start()
    logger.info("soccer radar thread started (poll=%.1fs)", POLL_INTERVAL)


def stop_soccer_radar() -> None:
    _stop.set()


def is_radar_running() -> bool:
    return _thread is not None and _thread.is_alive()


# ---------------------------------------------------------------------------
# Main loop


def _radar_loop() -> None:
    while not _stop.is_set():
        try:
            _tick()
        except Exception as exc:
            logger.exception("soccer radar tick failed: %s", exc)
        _stop.wait(POLL_INTERVAL)


def _tick() -> None:
    if _alarm_model is None or _db_engine is None:
        logger.debug("soccer radar tick skipped — not wired yet")
        return

    from sqlalchemy.orm import Session  # local import to keep module import-safe
    with Session(_db_engine) as session:
        rows = session.query(_alarm_model).filter(
            _alarm_model.is_active.is_(True),
            _alarm_model.match_id.isnot(None),
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
            }
            for r in rows
        ]

    # Group by match_id so we fetch each match's state once per tick
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in alarms:
        grouped[a["match_id"]].append(a)

    for match_id, alarm_group in grouped.items():
        try:
            detail = ss.get_soccer_match_detail(match_id)
        except Exception as exc:
            logger.warning("soccer radar: detail fetch failed for %s: %s", match_id, exc)
            continue
        if detail is None:
            continue
        for alarm in alarm_group:
            try:
                _evaluate_alarm(alarm, detail)
            except Exception as exc:
                logger.exception("alarm %s eval failed: %s", alarm["id"], exc)


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
        if minute >= 75 and abs(home_score - away_score) <= 1 and status in ("live", "1h", "2h"):
            _fire(alarm, "soccer_late_drama",
                  lambda h, a, c, lang: t_soccer_body_late_drama(h, a, c, minute, lang),
                  home_name, away_name, competition,
                  signal="late_drama", one_shot=True)
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

    # Unknown alarm_type → no-op (keeps forward compat for P3 types)


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
