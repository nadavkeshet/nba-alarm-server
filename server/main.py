import os
import asyncio
import logging
import threading
import requests as http
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("nba_monitor")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, text
from sqlalchemy.orm import DeclarativeBase, Session

from monitor import (
    # Alarm jobs
    start_monitoring_job,
    start_player_monitoring_job,
    # Active monitors
    get_active_alarms,
    get_active_player_alarms,
    cancel_game_alarm,
    cancel_player_alarm,
    _has_manual_alarm,
    # Roster
    fetch_game_roster,
    fetch_game_summary_roster,
    update_all_rosters,
    get_all_teams,
    # Radar
    start_radar_job,
    get_radar_status,
    # Settings
    get_preferences,
    save_preferences,
    save_push_token,
    get_saved_push_token,
    # Notifications
    _send_push_notification,
    # Data
    get_today_games,
    # Startup
    clear_stale_state,
    # DB deactivation registration
    register_db_deactivate,
    # Multi-user radar registration
    register_db_get_all_users,
    # Invalid token cleanup
    register_db_invalidate_token,
    # Fired-alert persistence (survives server restarts)
    register_db_persist_fired,
    hydrate_fired_alerts,
)


# ── Database Setup ─────────────────────────────────────────────────────────────

_raw_db_url = os.environ.get("DATABASE_URL", "")
# Railway provides postgres:// but SQLAlchemy 1.4+ requires postgresql://
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url else ""

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,       # base connections kept open
    max_overflow=20,    # extra connections allowed under burst load (total: 30)
) if DATABASE_URL else None


class _Base(DeclarativeBase):
    pass


class Alarm(_Base):
    """One-shot alarms (game / per-game player). Deactivated after firing."""
    __tablename__ = "alarms"

    id             = Column(Integer, primary_key=True, index=True)
    user_token     = Column(String,  nullable=False)
    target_id      = Column(String,  nullable=False)
    target_name    = Column(String,  nullable=True)
    condition_type = Column(String,  nullable=False)   # "points" | "score_diff" | "triple_double"
    threshold      = Column(Integer, nullable=False)
    start_quarter  = Column(Integer, nullable=True)
    # game_id / game_name: for player alarms these differ from target_id/target_name.
    # Stored so monitoring threads can be restarted after a server restart.
    game_id        = Column(String,  nullable=True)
    game_name      = Column(String,  nullable=True)
    alert_type     = Column(String,  nullable=True, default="alarm")
    is_active      = Column(Boolean, default=True, nullable=False)


class FavoritePlayerDB(_Base):
    """Persistent favorite player — stays active across restarts."""
    __tablename__ = "favorites"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    target_id     = Column(String,  nullable=False)   # ESPN player id
    target_name   = Column(String,  nullable=True)
    team_name     = Column(String,  nullable=True)
    total_pts     = Column(Integer, nullable=True)    # None = condition disabled
    early_pts     = Column(Integer, nullable=True)
    early_pts_q   = Column(Integer, nullable=True)
    td_watch      = Column(Boolean, default=False, nullable=False)
    alert_type    = Column(String,  nullable=True, default='silent')


class FavoriteTeamDB(_Base):
    """Persistent favorite team — stays active across restarts."""
    __tablename__ = "favorite_teams"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    team_id       = Column(String,  nullable=False)
    team_name     = Column(String,  nullable=True)
    score_gap     = Column(Integer, nullable=True)    # max_diff
    start_quarter = Column(Integer, nullable=True)
    alert_type    = Column(String,  nullable=True, default='silent')


class FiredAlertDB(_Base):
    """
    Persisted record of every radar/favorite alert fired today.
    Hydrated into _FIRED_ALERTS on startup so server restarts during a game
    don't cause duplicate wake-ups.  Rows older than today are deleted at startup.
    """
    __tablename__ = "fired_alerts"

    key      = Column(String,                primary_key=True)   # same key used in _fire_once
    fired_at = Column(DateTime(timezone=True), nullable=False)


class UserPreferenceDB(_Base):
    """Global radar + historical mode settings, keyed by push token."""
    __tablename__ = "user_preferences"

    user_token              = Column(String,  primary_key=True)
    radar_enabled           = Column(Boolean, default=False,   nullable=False)
    radar_gap               = Column(Integer, default=5,       nullable=False)
    radar_start_q           = Column(Integer, default=4,       nullable=False)
    historical_mode_enabled = Column(Boolean, default=False,   nullable=False)
    alert_type              = Column(String,  default="silent", nullable=False)  # radar
    hist_alert_type         = Column(String,  default="silent", nullable=False)  # historical
    hist_scoring_40_half    = Column(Boolean, default=False,   nullable=False)
    hist_threes_11          = Column(Boolean, default=False,   nullable=False)
    hist_quad_double        = Column(Boolean, default=False,   nullable=False)


def _init_db() -> None:
    """Create tables if they don't already exist, and run lightweight migrations."""
    if engine is None:
        log.warning("DB: No DATABASE_URL set – skipping table creation.")
        return
    try:
        _Base.metadata.create_all(bind=engine)
        log.info("DB: tables ready.")
    except Exception as exc:
        log.error("DB: failed to create tables – %s", exc)
        return
    migrations = [
        "ALTER TABLE alarms ADD COLUMN target_name   VARCHAR",
        "ALTER TABLE alarms ADD COLUMN start_quarter INTEGER",
        "ALTER TABLE favorites RENAME COLUMN hot_start_pts TO early_pts",
        "ALTER TABLE favorites RENAME COLUMN hot_start_q   TO early_pts_q",
        "ALTER TABLE user_preferences ADD COLUMN hist_alert_type VARCHAR NOT NULL DEFAULT 'silent'",
        "ALTER TABLE favorites      ADD COLUMN alert_type VARCHAR DEFAULT 'silent'",
        "ALTER TABLE favorite_teams ADD COLUMN alert_type VARCHAR DEFAULT 'silent'",
        # Partial index so is_active=True scans are fast even with thousands of rows
        "CREATE INDEX idx_alarms_active ON alarms (is_active) WHERE is_active = TRUE",
        # Fields needed to restart monitoring threads after a server restart
        "ALTER TABLE alarms ADD COLUMN game_id    VARCHAR",
        "ALTER TABLE alarms ADD COLUMN game_name  VARCHAR",
        "ALTER TABLE alarms ADD COLUMN alert_type VARCHAR DEFAULT 'alarm'",
    ]
    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
        except Exception:
            pass  # column already exists – safe to ignore


# ── DB Alarm Deactivation (called by monitoring threads in monitor.py) ─────────

def _deactivate_db_alarm(target_id: str) -> None:
    """Set is_active=False for all active Alarm rows matching target_id."""
    if engine is None:
        return
    try:
        with Session(engine) as session:
            rows = (
                session.query(Alarm)
                       .filter(Alarm.is_active.is_(True), Alarm.target_id == target_id)
                       .all()
            )
            for row in rows:
                row.is_active = False
            session.commit()
    except Exception as exc:
        log.error("DB: deactivation failed for %s: %s", target_id, exc)


# Register the callback so monitor.py threads can deactivate DB records after firing
register_db_deactivate(_deactivate_db_alarm)


def _invalidate_push_token(push_token: str) -> None:
    """
    Called when Firebase returns UnregisteredError for a token.

    NON-DESTRUCTIVE: only deactivates active Alarm rows so they are not
    retried against a dead token.  FavoriteTeamDB, FavoritePlayerDB, and
    UserPreferenceDB are intentionally preserved — FCM tokens rotate
    silently (reinstall, OS refresh), and deleting preference data would
    cause permanent data loss for the user.  When the user reopens the app
    and registers a new token, _migrate_token re-keys all rows to the fresh
    token so nothing is lost.
    """
    if engine is None or not push_token:
        return
    try:
        with Session(engine) as session:
            deactivated = (
                session.query(Alarm)
                .filter(Alarm.user_token == push_token, Alarm.is_active.is_(True))
                .update({"is_active": False})
            )
            session.commit()
        log.warning(
            "FCM: token invalidated %s – deactivated %d alarm(s). "
            "Favorites and preferences preserved for token re-association.",
            push_token[-8:], deactivated,
        )
    except Exception as exc:
        log.error("DB: _invalidate_push_token failed – %s", exc)


register_db_invalidate_token(_invalidate_push_token)


def _migrate_token(old_token: str, new_token: str) -> None:
    """
    Re-key all user data from old_token to new_token.
    Called by /register-token when the frontend sends an old_token alongside
    a fresh FCM token (i.e. after token rotation detected by onTokenRefresh).

    Uses raw SQL UPDATE for UserPreferenceDB because its PK is user_token
    and SQLAlchemy ORM does not support in-place PK changes.
    """
    if engine is None or not old_token or not new_token or old_token == new_token:
        return
    try:
        with Session(engine) as session:
            session.query(FavoritePlayerDB).filter_by(user_token=old_token).update(
                {"user_token": new_token}, synchronize_session=False
            )
            session.query(FavoriteTeamDB).filter_by(user_token=old_token).update(
                {"user_token": new_token}, synchronize_session=False
            )
            session.query(Alarm).filter(Alarm.user_token == old_token).update(
                {"user_token": new_token}, synchronize_session=False
            )
            # UserPreferenceDB has user_token as primary key — update via raw SQL.
            # If new_token already has a prefs row (fresh install wrote one first),
            # the old orphaned row is deleted to avoid a PK conflict.
            existing_new = session.get(UserPreferenceDB, new_token)
            if existing_new is None:
                session.execute(
                    text("UPDATE user_preferences SET user_token = :new WHERE user_token = :old"),
                    {"new": new_token, "old": old_token},
                )
            else:
                session.execute(
                    text("DELETE FROM user_preferences WHERE user_token = :old"),
                    {"old": old_token},
                )
            session.commit()
        log.info("DB: token migrated ...%s → ...%s", old_token[-8:], new_token[-8:])
    except Exception as exc:
        log.error("DB: _migrate_token failed – %s", exc)


def _persist_fired_alert(key: str) -> None:
    """
    Upsert a fired-alert key into PostgreSQL.
    Called by _fire_once immediately after adding the key to the in-memory set,
    so the record survives a server restart during the same UTC day.
    """
    if engine is None:
        return
    try:
        with Session(engine) as session:
            existing = session.get(FiredAlertDB, key)
            if existing is None:
                session.add(FiredAlertDB(key=key, fired_at=datetime.now(timezone.utc)))
                session.commit()
    except Exception as exc:
        log.warning("DB: _persist_fired_alert failed for %s: %s", key, exc)


register_db_persist_fired(_persist_fired_alert)


def _load_fired_alerts_from_db() -> None:
    """
    At startup: load today's fired-alert keys from DB into the in-memory set,
    then delete rows from previous days (they're no longer needed).
    """
    if engine is None:
        return
    try:
        today_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        with Session(engine) as session:
            rows = session.query(FiredAlertDB).all()
            today_keys  = {r.key for r in rows if r.key.startswith(today_prefix)}
            stale_keys  = [r.key for r in rows if not r.key.startswith(today_prefix)]
            if stale_keys:
                session.query(FiredAlertDB).filter(
                    FiredAlertDB.key.in_(stale_keys)
                ).delete(synchronize_session=False)
                session.commit()
        hydrate_fired_alerts(today_keys)
        log.info(
            "STARTUP: fired-alerts hydrated – %d keys loaded, %d stale rows deleted.",
            len(today_keys), len(stale_keys),
        )
    except Exception as exc:
        log.error("DB: _load_fired_alerts_from_db failed – %s", exc)


# ── Startup Restore ────────────────────────────────────────────────────────────

def _restore_prefs_from_db(user_token: str) -> None:
    """
    Read all 3 PostgreSQL tables and rebuild the local JSON cache so that
    monitor.py's radar starts with the correct favorites after a cold restart.
    """
    if engine is None or not user_token:
        return
    try:
        with Session(engine) as session:
            players = session.query(FavoritePlayerDB).filter_by(user_token=user_token).all()
            teams   = session.query(FavoriteTeamDB).filter_by(user_token=user_token).all()
            pref    = session.get(UserPreferenceDB, user_token)

            fav_players = {
                p.target_id: {
                    "name":          p.target_name or "",
                    "total_pts":     p.total_pts,
                    "early_pts_q":   p.early_pts_q,
                    "early_pts": p.early_pts,
                    "td_watch":      bool(p.td_watch),
                    "alert_type":    p.alert_type or "silent",
                }
                for p in players
            }
            fav_teams = [
                {
                    "team_id":       t.team_id,
                    "team_name":     t.team_name or "",
                    "max_diff":      t.score_gap or 5,
                    "start_quarter": t.start_quarter or 4,
                    "alert_type":    t.alert_type or "silent",
                }
                for t in teams
            ]

        prefs = get_preferences()
        prefs["FavoritePlayers"] = fav_players
        prefs["FavoriteTeams"]   = fav_teams
        if pref:
            prefs["GlobalGameRadar"] = {
                "enabled":       pref.radar_enabled,
                "max_diff":      pref.radar_gap,
                "start_quarter": pref.radar_start_q,
                "alert_type":    pref.alert_type,
            }
            prefs["HistoricalMode"] = {
                "enabled":    pref.historical_mode_enabled,
                "alert_type": pref.hist_alert_type,
                "categories": {
                    "scoring_40_half":    pref.hist_scoring_40_half,
                    "threes_11":          pref.hist_threes_11,
                    "quad_double_threat": pref.hist_quad_double,
                },
            }
        save_preferences(prefs)
        log.info(
            "DB restore  | %d teams  %d players  for ...%s",
            len(fav_teams), len(fav_players), user_token[-8:],
        )
    except Exception as exc:
        log.error("DB: _restore_prefs_from_db failed – %s", exc)


# ── Multi-user radar feed ─────────────────────────────────────────────────────

def _get_all_users_for_radar() -> list[dict]:
    """
    Return [{token, channel, prefs}] for every user that has any favorites
    or passive monitoring configured.  Called by the radar on every tick.

    Uses 3 bulk queries + Python grouping — never opens more than 1 DB session.
    """
    if engine is None:
        return []
    try:
        with Session(engine) as session:
            all_players: list[FavoritePlayerDB]  = session.query(FavoritePlayerDB).all()
            all_teams:   list[FavoriteTeamDB]    = session.query(FavoriteTeamDB).all()
            all_prefs:   list[UserPreferenceDB]  = session.query(UserPreferenceDB).all()

        # Group by token in Python — no extra DB round-trips
        players_by_token: dict[str, list] = {}
        for p in all_players:
            players_by_token.setdefault(p.user_token, []).append(p)

        teams_by_token: dict[str, list] = {}
        for t in all_teams:
            teams_by_token.setdefault(t.user_token, []).append(t)

        prefs_by_token: dict[str, UserPreferenceDB] = {p.user_token: p for p in all_prefs}

        all_tokens = set(players_by_token) | set(teams_by_token) | set(prefs_by_token)

        users: list[dict] = []
        for token in all_tokens:
            players = players_by_token.get(token, [])
            teams   = teams_by_token.get(token, [])
            pref    = prefs_by_token.get(token)

            fav_players = {
                p.target_id: {
                    "name":        p.target_name or "",
                    "total_pts":   p.total_pts,
                    "early_pts_q": p.early_pts_q,
                    "early_pts":   p.early_pts,
                    "td_watch":    bool(p.td_watch),
                    "alert_type":  p.alert_type or "silent",
                }
                for p in players
            }
            fav_teams = [
                {
                    "team_id":       t.team_id,
                    "team_name":     t.team_name or "",
                    "max_diff":      t.score_gap or 5,
                    "start_quarter": t.start_quarter or 4,
                    "alert_type":    t.alert_type or "silent",
                }
                for t in teams
            ]
            radar_cfg = {
                "enabled":       pref.radar_enabled       if pref else False,
                "max_diff":      pref.radar_gap           if pref else 5,
                "start_quarter": pref.radar_start_q       if pref else 4,
                "alert_type":    pref.alert_type          if pref else "silent",
            }
            hist_cfg = {
                "enabled":    pref.historical_mode_enabled if pref else False,
                "alert_type": pref.hist_alert_type         if pref else "silent",
                "categories": {
                    "scoring_40_half":    pref.hist_scoring_40_half if pref else False,
                    "threes_11":          pref.hist_threes_11       if pref else False,
                    "quad_double_threat": pref.hist_quad_double     if pref else False,
                },
            }

            # Sound channel: use persisted value if this is the known push token
            channel = "classic_alarm"
            saved_token, saved_channel = get_saved_push_token()
            if token == saved_token:
                channel = saved_channel

            users.append({
                "token":   token,
                "channel": channel,
                "prefs": {
                    "FavoritePlayers": fav_players,
                    "FavoriteTeams":   fav_teams,
                    "GlobalGameRadar": radar_cfg,
                    "HistoricalMode":  hist_cfg,
                },
            })
        return users
    except Exception as exc:
        log.error("_get_all_users_for_radar failed: %s", exc)
        return []


# Register so monitor.py radar can call it each tick
register_db_get_all_users(_get_all_users_for_radar)


# ── Background Async Poller ────────────────────────────────────────────────────

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)


def _fetch_scoreboard() -> dict:
    """Blocking ESPN fetch – called from a thread executor."""
    resp = http.get(ESPN_SCOREBOARD, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _get_event_state(event: dict) -> str:
    """Return ESPN status state: 'pre' | 'in' | 'post'."""
    try:
        return event.get("status", {}).get("type", {}).get("state", "pre")
    except Exception:
        return "pre"


def _get_event_period(event: dict) -> int:
    """Return current period (quarter) of the event, or 0 if not started."""
    try:
        return int(event.get("status", {}).get("period", 0))
    except Exception:
        return 0


def _extract_game_diff(event: dict) -> int | None:
    """Return absolute score difference for a live/finished event, or None."""
    try:
        comps = event["competitions"][0]["competitors"]
        scores = [int(c["score"]) for c in comps]
        return abs(scores[0] - scores[1])
    except Exception:
        return None


def _extract_player_pts(event: dict, target_id: str) -> int | None:
    """Return the current point total for target_id player, or None if not found."""
    try:
        leaders = event.get("competitions", [{}])[0].get("leaders", [])
        for cat in leaders:
            if cat.get("abbreviation") != "PTS":
                continue
            for leader in cat.get("leaders", []):
                if str(leader.get("athlete", {}).get("id")) == str(target_id):
                    return int(float(leader.get("value", 0)))
    except Exception:
        pass
    return None


def _extract_player_stats(event: dict, target_id: str) -> dict[str, int]:
    """
    Return a dict of stat totals (PTS, REB, AST, STL, BLK) for target_id
    using the ESPN leaders block. Returns empty dict if player not found.
    """
    stats: dict[str, int] = {}
    try:
        leaders = event.get("competitions", [{}])[0].get("leaders", [])
        for cat in leaders:
            abbr = cat.get("abbreviation", "")
            if abbr not in ("PTS", "REB", "AST", "STL", "BLK"):
                continue
            for leader in cat.get("leaders", []):
                if str(leader.get("athlete", {}).get("id")) == str(target_id):
                    stats[abbr] = int(float(leader.get("value", 0)))
    except Exception:
        pass
    return stats


def _period_str(period: int) -> str:
    return f"Q{period}" if period <= 4 else f"OT{period - 4}"


def _check_and_fire(alarm: Alarm, events: list[dict]) -> dict | None | bool:
    """
    Return a dict {"title": ..., "body": ...} if the alarm condition is met.
    Return None if the alarm has expired and should be deactivated without firing.
    Return False if condition not yet met (keep watching).
    """
    game_name   = alarm.game_name   or alarm.target_name or alarm.target_id
    player_name = alarm.target_name or alarm.target_id

    if alarm.condition_type == "score_diff":
        required_quarter = alarm.start_quarter or 4
        for event in events:
            if str(event.get("id")) != str(alarm.target_id):
                continue
            state  = _get_event_state(event)
            period = _get_event_period(event)
            if state == "post":
                return None
            if state != "in" or period < required_quarter:
                continue
            diff = _extract_game_diff(event)
            if diff is not None and diff <= alarm.threshold:
                return {
                    "title": "Smart Alarm! It's Getting Dramatic!",
                    "body":  f"{game_name}\n{_period_str(period)} | Point Diff: {diff} | CLOSE GAME!",
                }

    elif alarm.condition_type == "points":
        quarter_limit = alarm.start_quarter or 0
        for event in events:
            state  = _get_event_state(event)
            period = _get_event_period(event)
            if quarter_limit > 0 and period > quarter_limit:
                return None
            if state == "post":
                pts = _extract_player_pts(event, alarm.target_id)
                if pts is None or pts < alarm.threshold:
                    return None
            pts = _extract_player_pts(event, alarm.target_id)
            if pts is not None and pts >= alarm.threshold:
                if quarter_limit > 0:
                    return {
                        "title": "Player Alert!",
                        "body":  f"{player_name} already has {pts} pts in {_period_str(period)} — on fire early!",
                    }
                return {
                    "title": "Player Alert!",
                    "body":  f"{player_name} has scored {pts} points in {game_name}!",
                }

    elif alarm.condition_type == "triple_double":
        for event in events:
            stats = _extract_player_stats(event, alarm.target_id)
            if stats:
                near_double = sum(1 for v in stats.values() if v >= 8)
                if near_double >= 3:
                    hot = [f"{k} {v}" for k, v in stats.items() if v >= 8]
                    return {
                        "title": "Triple-Double Watch!",
                        "body":  f"{player_name}: {' / '.join(hot)} in {game_name}!",
                    }

    return False


async def _alarm_poller() -> None:
    """
    Every 60 s:
      1. Read the shared ESPN scoreboard cache (populated by monitor.py threads).
      2. Load all is_active alarms from DB.
      3. Check each alarm; fire + deactivate on match.
    """
    while True:
        await asyncio.sleep(60)
        if engine is None:
            continue
        try:
            from monitor import get_today_games as _get_games
            events: list[dict] = await asyncio.to_thread(_get_games)

            with Session(engine) as session:
                alarms = (
                    session.query(Alarm)
                           .filter(Alarm.is_active.is_(True))
                           .all()
                )

            # Snapshot active in-memory alarms so we can skip DB records that
            # already have a dedicated monitoring thread watching them.
            active_game_ids    = {a["game_id"] for a in get_active_alarms()}
            # Map live player alarms to (player_id, condition_slot) pairs so we only
            # skip DB alarms whose condition slot has an active monitoring thread.
            # DB "points" with start_quarter=0 → slot "pts_goal"
            # DB "points" with start_quarter>0  → slot "pts_early"
            # DB "triple_double"                → slot "triple_double"
            def _alarm_slot(alarm_obj) -> str:
                if alarm_obj.condition_type == "points":
                    return "pts_early" if (alarm_obj.start_quarter or 0) > 0 else "pts_goal"
                return alarm_obj.condition_type
            active_player_keys = {
                (a["player_id"], a["condition"])
                for a in get_active_player_alarms()
            }

            for alarm in alarms:
                try:
                    # Skip if a monitoring thread is already watching this target —
                    # that thread will deactivate the DB record when it fires.
                    if alarm.condition_type == "score_diff" and alarm.target_id in active_game_ids:
                        continue
                    if alarm.condition_type in ("points", "triple_double") and \
                            (alarm.target_id, _alarm_slot(alarm)) in active_player_keys:
                        continue

                    result = _check_and_fire(alarm, events)
                    if isinstance(result, dict):
                        log.info(
                            "POLLER ALARM fire   | alarm_id=%d | %s | %s",
                            alarm.id,
                            alarm.target_name or alarm.target_id,
                            alarm.user_token[-8:],
                        )
                        _send_push_notification(
                            push_token=alarm.user_token,
                            title=result["title"],
                            body=result["body"],
                            sound_channel="classic_alarm",
                            alert_type=alarm.alert_type or "alarm",
                        )
                        with Session(engine) as session:
                            db_row = session.get(Alarm, alarm.id)
                            if db_row:
                                db_row.is_active = False
                                session.commit()
                    elif result is None:
                        with Session(engine) as session:
                            db_row = session.get(Alarm, alarm.id)
                            if db_row:
                                db_row.is_active = False
                                session.commit()
                except Exception as alarm_err:
                    log.error("Poller error alarm %d: %s", alarm.id, alarm_err)

            # NOTE: Favorite players and teams are monitored exclusively by the
            # background radar (monitor.py _run_radar_tick) which has its own
            # per-day fire-once deduplication.  Checking them here too caused
            # double notifications (separate dedup sets).  Do not re-add.

        except Exception as exc:
            log.error("Poller: cycle error – %s", exc)


# ── App Lifespan ───────────────────────────────────────────────────────────────

def _find_token_from_db() -> str:
    """
    Fallback: if the JSON push token is missing or doesn't match any DB records,
    scan the DB tables for any token that has favorites configured and return it.
    This handles the case where Railway wiped the ephemeral JSON file and the
    cached token no longer matches the token used to register favorites.
    """
    if engine is None:
        return ""
    try:
        with Session(engine) as session:
            # Prefer a token that has favorite teams or players
            row = session.query(FavoriteTeamDB).first()
            if row:
                return row.user_token
            row = session.query(FavoritePlayerDB).first()
            if row:
                return row.user_token
            # Fall back to any token registered in user_preferences
            pref = session.query(UserPreferenceDB).first()
            if pref:
                return pref.user_token
    except Exception as exc:
        log.error("DB: _find_token_from_db failed – %s", exc)
    return ""


def _restart_alarm_threads_from_db() -> None:
    """
    On server startup, restart a dedicated monitoring thread for every alarm
    that was active when the server last shut down.

    This ensures users never miss an alarm because the server restarted
    mid-game (e.g. a Railway redeploy at midnight).

    Player alarms without a stored game_id are skipped here — the async
    _alarm_poller picks them up within 60 s as a safety net (old DB rows
    created before this column was added).
    """
    if engine is None:
        return
    try:
        with Session(engine) as session:
            rows = session.query(Alarm).filter(Alarm.is_active.is_(True)).all()
            # Snapshot inside session — ORM objects detach once session closes
            snapshot = [
                {
                    "condition_type": r.condition_type,
                    "target_id":      r.target_id,
                    "target_name":    r.target_name or "",
                    "game_id":        r.game_id,        # None for old rows
                    "game_name":      r.game_name or r.target_name or "",
                    "threshold":      r.threshold,
                    "start_quarter":  r.start_quarter or 0,
                    "user_token":     r.user_token,
                    "alert_type":     r.alert_type or "alarm",
                }
                for r in rows
            ]
    except Exception as exc:
        log.error("STARTUP: failed to read active alarms from DB – %s", exc)
        return

    started = 0
    for alarm in snapshot:
        try:
            if alarm["condition_type"] == "score_diff":
                # game_id == target_id for game alarms — always restartable
                threading.Thread(
                    target=start_monitoring_job,
                    args=(
                        alarm["target_id"],
                        alarm["game_name"],
                        alarm["threshold"],
                        alarm["start_quarter"],
                        alarm["user_token"],
                        "classic_alarm",
                        alarm["alert_type"],
                    ),
                    daemon=True,
                ).start()
                started += 1

            elif alarm["condition_type"] in ("points", "triple_double"):
                game_id = alarm["game_id"]
                if not game_id:
                    # Old row without game_id — _alarm_poller handles it
                    log.info(
                        "STARTUP: player alarm for %s has no game_id – poller will cover.",
                        alarm["target_name"] or alarm["target_id"],
                    )
                    continue
                # Derive the exact condition string from condition_type + start_quarter
                if alarm["condition_type"] == "triple_double":
                    condition = "triple_double"
                elif alarm["start_quarter"] > 0:
                    condition = "pts_early"
                else:
                    condition = "pts_goal"
                threading.Thread(
                    target=start_player_monitoring_job,
                    kwargs=dict(
                        game_id=game_id,
                        game_name=alarm["game_name"],
                        player_id=alarm["target_id"],
                        player_name=alarm["target_name"],
                        push_token=alarm["user_token"],
                        sound_channel="classic_alarm",
                        condition=condition,
                        pts_threshold=alarm["threshold"],
                        quarter_deadline=alarm["start_quarter"],
                        alert_type=alarm["alert_type"],
                    ),
                    daemon=True,
                ).start()
                started += 1

        except Exception as exc:
            log.error(
                "STARTUP: failed to restart thread for %s – %s",
                alarm["target_name"] or alarm["target_id"], exc,
            )

    if started:
        log.info("STARTUP: restarted %d monitoring thread(s) from DB.", started)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Startup ──
    _init_db()
    _load_fired_alerts_from_db()   # hydrate dedup set before radar starts
    clear_stale_state()
    _restart_alarm_threads_from_db()   # resume monitoring for alarms active at shutdown
    threading.Thread(target=update_all_rosters, daemon=True).start()
    token, channel = get_saved_push_token()
    if token:
        _restore_prefs_from_db(token)   # rebuild JSON cache from PostgreSQL
        # If restore found nothing (token mismatch after ephemeral FS wipe),
        # try to find the correct token directly from the DB.
        from monitor import get_preferences as _gp
        prefs = _gp()
        if not prefs.get("FavoriteTeams") and not prefs.get("FavoritePlayers"):
            fallback_token = _find_token_from_db()
            if fallback_token and fallback_token != token:
                log.info("STARTUP: token mismatch – retrying restore with DB token ...%s", fallback_token[-10:])
                save_push_token(fallback_token, channel)
                token = fallback_token
                _restore_prefs_from_db(token)
    else:
        # No token in JSON at all — scan DB for any registered token
        token = _find_token_from_db()
        if token:
            log.info("STARTUP: no JSON token – found token from DB ...%s", token[-10:])
            save_push_token(token, channel)
            _restore_prefs_from_db(token)
    if token:
        threading.Thread(target=start_radar_job, daemon=True).start()
    # Start the async DB poller
    poller_task = asyncio.create_task(_alarm_poller())

    yield  # server is running

    # ── Shutdown ──
    poller_task.cancel()
    try:
        await poller_task
    except asyncio.CancelledError:
        pass


# ── App Instance ───────────────────────────────────────────────────────────────

app = FastAPI(title="NBA Smart Alarm Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    db_ok = False
    db_msg = "no DATABASE_URL configured"
    if engine is not None:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
            db_msg = "connected"
        except Exception as exc:
            db_msg = str(exc)
    return {"status": "ok", "db": {"connected": db_ok, "detail": db_msg}}


# ── DB Alarm Endpoint ──────────────────────────────────────────────────────────

class SetAlarmRequest(BaseModel):
    user_token:     str
    target_id:      str
    target_name:    str = ""   # human-readable player/game name
    condition_type: str        # "points" | "score_diff" | "triple_double"
    threshold:      int
    start_quarter:  int = 4    # for score_diff: earliest quarter to fire (default Q4)
    # For player alarms: the game being played (differs from target_id/target_name).
    # Needed to restart monitoring threads after a server restart.
    game_id:        str = ""
    game_name:      str = ""
    alert_type:     str = "alarm"


@app.post("/set_alarm")
def set_alarm(req: SetAlarmRequest):
    """Persist a new alarm to PostgreSQL."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    condition_type = req.condition_type.strip().lower()
    if condition_type not in ("points", "score_diff", "triple_double"):
        raise HTTPException(
            status_code=400,
            detail="condition_type must be 'points', 'score_diff', or 'triple_double'.",
        )
    with Session(engine) as session:
        # Remove any existing active alarm for this user + target + condition slot.
        # For "points", pts_goal (start_quarter=0) and pts_early (start_quarter>0)
        # are independent — match on whether start_quarter is zero/nonzero to allow
        # both to exist simultaneously while still overriding the same sub-type.
        q = session.query(Alarm).filter(
            Alarm.user_token     == req.user_token,
            Alarm.target_id      == req.target_id,
            Alarm.condition_type == condition_type,
            Alarm.is_active.is_(True),
        )
        if condition_type == "points":
            if req.start_quarter and req.start_quarter > 0:
                # pts_early: only replace other pts_early rows (quarter-limited)
                q = q.filter(Alarm.start_quarter > 0)
            else:
                # pts_goal: only replace other pts_goal rows (no quarter limit)
                q = q.filter((Alarm.start_quarter == 0) | Alarm.start_quarter.is_(None))
        q.delete()
        alarm = Alarm(
            user_token=req.user_token,
            target_id=req.target_id,
            target_name=req.target_name or req.target_id,
            condition_type=condition_type,
            threshold=req.threshold,
            start_quarter=req.start_quarter,
            # For game alarms game_id == target_id; for player alarms they differ.
            game_id=req.game_id or req.target_id,
            game_name=req.game_name or req.target_name or req.target_id,
            alert_type=req.alert_type or "alarm",
            is_active=True,
        )
        session.add(alarm)
        session.commit()
        session.refresh(alarm)
        alarm_id = alarm.id
    return {
        "status":         "alarm_set",
        "alarm_id":       alarm_id,
        "target_id":      req.target_id,
        "condition_type": req.condition_type,
        "threshold":      req.threshold,
    }


# ── Game Alarm ─────────────────────────────────────────────────────────────────

class AlarmRequest(BaseModel):
    game_id: str
    game_name: str
    max_diff: int
    start_quarter: int
    push_token: str
    sound_channel: str = "classic_alarm"
    alert_type: str    = "alarm"


@app.post("/start-alarm")
async def start_alarm(req: AlarmRequest):
    """Kick off a background thread monitoring a game's point differential."""
    save_push_token(req.push_token, req.sound_channel)
    # Dedup: one monitoring thread per game_id — prevents thread exhaustion
    # if the client retries or a bad actor hammers this endpoint.
    if _has_manual_alarm(req.game_id):
        log.info("ALARM: duplicate request for game %s – already monitoring.", req.game_id)
        return {
            "status":        "already_monitoring",
            "game":          req.game_name,
            "max_diff":      req.max_diff,
            "start_quarter": req.start_quarter,
        }
    threading.Thread(
        target=start_monitoring_job,
        args=(req.game_id, req.game_name, req.max_diff, req.start_quarter,
              req.push_token, req.sound_channel, req.alert_type),
        daemon=True,
    ).start()
    threading.Thread(target=start_radar_job, daemon=True).start()
    return {
        "status":        "monitoring_started",
        "game":          req.game_name,
        "max_diff":      req.max_diff,
        "start_quarter": req.start_quarter,
    }


# ── Player Alarm ───────────────────────────────────────────────────────────────

class PlayerAlarmRequest(BaseModel):
    game_id: str
    game_name: str
    player_id: str
    player_name: str
    push_token: str
    sound_channel: str    = "classic_alarm"
    condition: str        = "pts_goal"
    pts_threshold: int    = 20
    quarter_deadline: int = 3
    alert_type: str       = "alarm"


@app.post("/start-player-alarm")
async def start_player_alarm(req: PlayerAlarmRequest):
    """Kick off a background thread monitoring a specific player's stats."""
    save_push_token(req.push_token, req.sound_channel)
    # Note: no game-level dedup here. _register_player_alarm keys by (player_id, condition)
    # so pts_goal + triple_double for the same player are two separate valid threads.
    # Duplicate (player_id, condition) requests are handled inside _register_player_alarm
    # by cancelling the existing thread before starting a new one — safe by design.
    threading.Thread(
        target=start_player_monitoring_job,
        kwargs=dict(
            game_id=req.game_id, game_name=req.game_name,
            player_id=req.player_id, player_name=req.player_name,
            push_token=req.push_token, sound_channel=req.sound_channel,
            condition=req.condition, pts_threshold=req.pts_threshold,
            quarter_deadline=req.quarter_deadline, alert_type=req.alert_type,
        ),
        daemon=True,
    ).start()
    threading.Thread(target=start_radar_job, daemon=True).start()
    return {
        "status":    "player_monitoring_started",
        "game":      req.game_name,
        "player":    req.player_name,
        "condition": req.condition,
    }


# ── Test Endpoints ─────────────────────────────────────────────────────────────

class TestAlarmRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"


@app.post("/test-alarm")
def test_alarm(req: TestAlarmRequest):
    _send_push_notification(
        push_token=req.push_token,
        title="Test Notification",
        body="Your NBA Smart Alarm is working!",
        sound_channel=req.sound_channel,
    )
    return {"status": "test_notification_sent"}


@app.get("/test-player-alarm")
def test_player_alarm(
    push_token: str,
    condition: str     = "triple_double",
    player_name: str   = "Test Player",
    pts_threshold: int = 20,
    sound_channel: str = "classic_alarm",
):
    threading.Thread(
        target=start_player_monitoring_job,
        kwargs=dict(
            game_id="test", game_name="Test Game",
            player_id="test", player_name=player_name,
            push_token=push_token, sound_channel=sound_channel,
            condition=condition, pts_threshold=pts_threshold,
            test_mode=True,
        ),
        daemon=True,
    ).start()
    return {
        "status":      "test_player_alarm_started",
        "condition":   condition,
        "player_name": player_name,
        "mock_stats":  {"PTS": 25, "REB": 9, "AST": 9, "STL": 2, "BLK": 9},
    }


# ── Background Radar ───────────────────────────────────────────────────────────

class RadarRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"


def _maybe_start_radar() -> None:
    threading.Thread(target=start_radar_job, daemon=True).start()


@app.post("/start-radar")
def start_radar(req: RadarRequest):
    save_push_token(req.push_token, req.sound_channel)
    threading.Thread(target=start_radar_job, daemon=True).start()
    return {"status": "radar_started"}


@app.get("/radar-status")
def radar_status():
    return {"running": get_radar_status()}


# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/settings")
def get_settings(user_token: str = ""):
    json_prefs = get_preferences()
    if engine is None or not user_token:
        return json_prefs
    try:
        with Session(engine) as session:
            players = session.query(FavoritePlayerDB).filter_by(user_token=user_token).all()
            teams   = session.query(FavoriteTeamDB).filter_by(user_token=user_token).all()
            pref    = session.get(UserPreferenceDB, user_token)

            fav_players = {
                p.target_id: {
                    "name":          p.target_name or "",
                    "total_pts":     p.total_pts,
                    "early_pts_q":   p.early_pts_q,
                    "early_pts": p.early_pts,
                    "td_watch":      bool(p.td_watch),
                    "alert_type":    p.alert_type or "silent",
                }
                for p in players
            }
            fav_teams = [
                {
                    "team_id":       t.team_id,
                    "team_name":     t.team_name or "",
                    "max_diff":      t.score_gap or 5,
                    "start_quarter": t.start_quarter or 4,
                    "alert_type":    t.alert_type or "silent",
                }
                for t in teams
            ]
            radar_cfg = json_prefs.get("GlobalGameRadar", {})
            hist_cfg  = json_prefs.get("HistoricalMode",  {})
            if pref:
                radar_cfg = {
                    "enabled":       pref.radar_enabled,
                    "max_diff":      pref.radar_gap,
                    "start_quarter": pref.radar_start_q,
                    "alert_type":    pref.alert_type,
                }
                hist_cfg = {
                    "enabled":    pref.historical_mode_enabled,
                    "alert_type": pref.hist_alert_type,
                    "categories": {
                        "scoring_40_half":    pref.hist_scoring_40_half,
                        "threes_11":          pref.hist_threes_11,
                        "quad_double_threat": pref.hist_quad_double,
                    },
                }
        return {
            **json_prefs,
            "FavoritePlayers": fav_players,
            "FavoriteTeams":   fav_teams,
            "GlobalGameRadar": radar_cfg,
            "HistoricalMode":  hist_cfg,
        }
    except Exception as exc:
        log.error("DB: /settings read error – %s", exc)
        return json_prefs


class GlobalRadarSettings(BaseModel):
    user_token:    str = ""
    enabled:       bool
    max_diff:      int = 5
    start_quarter: int = 4
    alert_type:    str = "silent"


@app.post("/settings/global-radar")
def update_global_radar(req: GlobalRadarSettings):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                pref = session.get(UserPreferenceDB, req.user_token)
                if pref:
                    pref.radar_enabled = req.enabled
                    pref.radar_gap     = req.max_diff
                    pref.radar_start_q = req.start_quarter
                    pref.alert_type    = req.alert_type
                else:
                    session.add(UserPreferenceDB(
                        user_token=req.user_token,
                        radar_enabled=req.enabled, radar_gap=req.max_diff,
                        radar_start_q=req.start_quarter, alert_type=req.alert_type,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: update_global_radar failed – %s", exc)
    prefs = get_preferences()
    prefs["GlobalGameRadar"] = {
        "enabled": req.enabled, "max_diff": req.max_diff,
        "start_quarter": req.start_quarter, "alert_type": req.alert_type,
    }
    save_preferences(prefs)
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "GlobalGameRadar": prefs["GlobalGameRadar"]}


class FavoriteTeam(BaseModel):
    user_token:    str = ""
    team_id:       str
    team_name:     str
    max_diff:      int = 5
    start_quarter: int = 4
    alert_type:    str = "silent"


@app.post("/settings/teams")
def upsert_team(req: FavoriteTeam):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoriteTeamDB).filter_by(
                    user_token=req.user_token, team_id=req.team_id
                ).first()
                if existing:
                    existing.team_name     = req.team_name
                    existing.score_gap     = req.max_diff
                    existing.start_quarter = req.start_quarter
                    existing.alert_type    = req.alert_type
                else:
                    session.add(FavoriteTeamDB(
                        user_token=req.user_token, team_id=req.team_id,
                        team_name=req.team_name, score_gap=req.max_diff,
                        start_quarter=req.start_quarter, alert_type=req.alert_type,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: upsert_team failed – %s", exc)
    prefs = get_preferences()
    teams = [t for t in prefs.get("FavoriteTeams", []) if t["team_id"] != req.team_id]
    teams.append({
        "team_id": req.team_id, "team_name": req.team_name,
        "max_diff": req.max_diff, "start_quarter": req.start_quarter,
        "alert_type": req.alert_type,
    })
    prefs["FavoriteTeams"] = teams
    save_preferences(prefs)
    _maybe_start_radar()
    return {"status": "updated"}


@app.delete("/settings/teams/{team_id}")
def remove_team(team_id: str, user_token: str = ""):
    if engine is not None and user_token:
        try:
            with Session(engine) as session:
                row = session.query(FavoriteTeamDB).filter_by(
                    user_token=user_token, team_id=team_id
                ).first()
                if row:
                    session.delete(row)
                    session.commit()
        except Exception as exc:
            log.error("DB: remove_team failed – %s", exc)
    prefs = get_preferences()
    prefs["FavoriteTeams"] = [t for t in prefs.get("FavoriteTeams", []) if t["team_id"] != team_id]
    save_preferences(prefs)
    return {"status": "removed"}


class FavoritePlayer(BaseModel):
    user_token:    str = ""
    player_id:     str
    name:          str
    total_pts:     Optional[int]  = None   # None = condition disabled
    early_pts_q:   Optional[int]  = None
    early_pts: Optional[int]  = None
    td_watch:      bool           = False
    alert_type:    str            = "silent"


@app.post("/settings/players")
def upsert_player(req: FavoritePlayer):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoritePlayerDB).filter_by(
                    user_token=req.user_token, target_id=req.player_id
                ).first()
                if existing:
                    existing.target_name   = req.name
                    existing.total_pts     = req.total_pts
                    existing.early_pts_q   = req.early_pts_q
                    existing.early_pts = req.early_pts
                    existing.td_watch      = req.td_watch
                    existing.alert_type    = req.alert_type
                else:
                    session.add(FavoritePlayerDB(
                        user_token=req.user_token, target_id=req.player_id,
                        target_name=req.name, total_pts=req.total_pts,
                        early_pts_q=req.early_pts_q, early_pts=req.early_pts,
                        td_watch=req.td_watch, alert_type=req.alert_type,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: upsert_player failed – %s", exc)
    prefs = get_preferences()
    players: dict = prefs.get("FavoritePlayers", {})
    players[req.player_id] = {
        "name":          req.name,
        "total_pts":     req.total_pts,
        "early_pts_q":   req.early_pts_q,
        "early_pts": req.early_pts,
        "td_watch":      req.td_watch,
        "alert_type":    req.alert_type,
    }
    prefs["FavoritePlayers"] = players
    save_preferences(prefs)
    _maybe_start_radar()
    return {"status": "updated"}


@app.delete("/settings/players/{player_id}")
def remove_player(player_id: str, user_token: str = ""):
    if engine is not None and user_token:
        try:
            with Session(engine) as session:
                row = session.query(FavoritePlayerDB).filter_by(
                    user_token=user_token, target_id=player_id
                ).first()
                if row:
                    session.delete(row)
                    session.commit()
        except Exception as exc:
            log.error("DB: remove_player failed – %s", exc)
    prefs = get_preferences()
    players: dict = prefs.get("FavoritePlayers", {})
    players.pop(player_id, None)
    prefs["FavoritePlayers"] = players
    save_preferences(prefs)
    return {"status": "removed"}


class HistoricalCategories(BaseModel):
    scoring_40_half:    bool = False
    threes_11:          bool = False
    quad_double_threat: bool = False


class HistoricalSettings(BaseModel):
    user_token:  str = ""
    enabled:     bool
    categories:  HistoricalCategories = HistoricalCategories()
    alert_type:  str                  = "silent"


@app.post("/settings/historical")
def update_historical(req: HistoricalSettings):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                pref = session.get(UserPreferenceDB, req.user_token)
                if pref:
                    pref.historical_mode_enabled = req.enabled
                    pref.hist_scoring_40_half    = req.categories.scoring_40_half
                    pref.hist_threes_11          = req.categories.threes_11
                    pref.hist_quad_double        = req.categories.quad_double_threat
                    pref.hist_alert_type         = req.alert_type
                else:
                    session.add(UserPreferenceDB(
                        user_token=req.user_token,
                        historical_mode_enabled=req.enabled,
                        hist_scoring_40_half=req.categories.scoring_40_half,
                        hist_threes_11=req.categories.threes_11,
                        hist_quad_double=req.categories.quad_double_threat,
                        hist_alert_type=req.alert_type,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: update_historical failed – %s", exc)
    prefs = get_preferences()
    prefs["HistoricalMode"] = {
        "enabled": req.enabled, "alert_type": req.alert_type,
        "categories": {
            "scoring_40_half":    req.categories.scoring_40_half,
            "threes_11":          req.categories.threes_11,
            "quad_double_threat": req.categories.quad_double_threat,
        },
    }
    save_preferences(prefs)
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "HistoricalMode": prefs["HistoricalMode"]}


class GlobalThresholdsRequest(BaseModel):
    pts_threshold:       int  = 40
    triple_double_watch: bool = True
    pts_per_quarter:     int  = 15


@app.post("/settings/thresholds")
def update_thresholds(req: GlobalThresholdsRequest):
    prefs = get_preferences()
    prefs["GlobalThresholds"] = {
        "pts_threshold":       req.pts_threshold,
        "triple_double_watch": req.triple_double_watch,
        "pts_per_quarter":     req.pts_per_quarter,
    }
    save_preferences(prefs)
    return {"status": "updated", "GlobalThresholds": prefs["GlobalThresholds"]}


# ── Token Registration ─────────────────────────────────────────────────────────

class RegisterTokenRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"
    old_token: str     = ""   # set by onTokenRefresh to trigger data migration


@app.post("/register-token")
def register_token(req: RegisterTokenRequest):
    if not req.push_token:
        return {"status": "error", "detail": "empty push_token"}
    # If the FCM token rotated, migrate all user data before saving the new token.
    if req.old_token and req.old_token != req.push_token:
        _migrate_token(req.old_token, req.push_token)
    save_push_token(req.push_token, req.sound_channel)
    # Rebuild JSON prefs from DB so the radar starts with the correct favorites.
    # This is critical after a Railway restart that wiped the ephemeral JSON file.
    _restore_prefs_from_db(req.push_token)
    threading.Thread(target=start_radar_job, daemon=True).start()
    return {"status": "token_registered", "push_token": req.push_token}


# ── Data Endpoints ─────────────────────────────────────────────────────────────

@app.get("/games")
def games():
    return {"games": get_today_games()}


@app.get("/roster")
def roster(game_id: str):
    return fetch_game_roster(game_id)


@app.get("/game-summary")
def game_summary(gameId: str):
    return fetch_game_summary_roster(gameId)


@app.get("/all-teams-and-players")
def all_teams_and_players():
    return {"teams": get_all_teams()}


@app.get("/refresh-rosters")
def refresh_rosters():
    threading.Thread(target=update_all_rosters, daemon=True).start()
    return {"status": "roster_refresh_started"}


@app.get("/active-monitors")
def active_monitors(user_token: str = ""):
    # Safe defaults returned on any error so the app never gets a 500
    _empty_response = {
        "game_alarms":   [],
        "player_alarms": [],
        "db_alarms":     [],
        "global_radar":  {"enabled": False, "running": False, "max_diff": 5, "start_quarter": 4},
        "favorite_teams":   [],
        "favorite_players": {},
        "historical_mode":  {"enabled": False, "categories": {
            "scoring_40_half": False, "threes_11": False, "quad_double_threat": False,
        }},
    }

    try:
        # Filter in-memory alarms by token when one is provided
        game_alarms   = get_active_alarms()
        player_alarms = get_active_player_alarms()
        if user_token:
            game_alarms   = [a for a in game_alarms   if a.get("push_token") == user_token]
            player_alarms = [a for a in player_alarms if a.get("push_token") == user_token]

        # All user-specific data read from DB, keyed by token.
        # Never read from the shared JSON prefs file — that file is a single-user
        # cache and would leak one user's favorites to every other user.
        db_alarms:      list[dict] = []
        fav_teams:      list[dict] = []
        fav_players:    dict       = {}
        radar_cfg:      dict       = {"enabled": False, "max_diff": 5, "start_quarter": 4}
        hist_cfg:       dict       = {
            "enabled": False,
            "categories": {
                "scoring_40_half": False, "threes_11": False, "quad_double_threat": False,
            },
        }

        if engine is not None and user_token:
            try:
                with Session(engine) as session:
                    # One query per table — all filtered by this user's token
                    alarm_rows  = (
                        session.query(Alarm)
                               .filter(Alarm.user_token == user_token, Alarm.is_active.is_(True))
                               .all()
                    )
                    player_rows = session.query(FavoritePlayerDB).filter_by(user_token=user_token).all()
                    team_rows   = session.query(FavoriteTeamDB).filter_by(user_token=user_token).all()
                    pref        = session.get(UserPreferenceDB, user_token)

                    # Build all dicts INSIDE the session so ORM attributes are never detached
                    db_alarms = [
                        {
                            "alarm_id":       r.id,
                            "target_id":      r.target_id,
                            "target_name":    r.target_name or r.target_id,
                            "condition_type": r.condition_type,
                            "threshold":      r.threshold,
                            "start_quarter":  r.start_quarter if r.start_quarter is not None else 4,
                        }
                        for r in alarm_rows
                    ]
                    fav_players = {
                        p.target_id: {
                            "name":        p.target_name or "",
                            "total_pts":   p.total_pts,
                            "early_pts_q": p.early_pts_q,
                            "early_pts":   p.early_pts,
                            "td_watch":    bool(p.td_watch),
                            "alert_type":  p.alert_type or "silent",
                        }
                        for p in player_rows
                    }
                    fav_teams = [
                        {
                            "team_id":       t.team_id,
                            "team_name":     t.team_name or "",
                            "max_diff":      t.score_gap or 5,
                            "start_quarter": t.start_quarter or 4,
                            "alert_type":    t.alert_type or "silent",
                        }
                        for t in team_rows
                    ]
                    if pref:
                        radar_cfg = {
                            "enabled":       pref.radar_enabled,
                            "max_diff":      pref.radar_gap,
                            "start_quarter": pref.radar_start_q,
                        }
                        hist_cfg = {
                            "enabled":    pref.historical_mode_enabled,
                            "categories": {
                                "scoring_40_half":    pref.hist_scoring_40_half,
                                "threes_11":          pref.hist_threes_11,
                                "quad_double_threat": pref.hist_quad_double,
                            },
                        }
            except Exception as db_err:
                log.error("DB error in active_monitors query: %s", db_err)
                # Degrade gracefully — return whatever was built before the error

        return {
            "game_alarms":   game_alarms,
            "player_alarms": player_alarms,
            "db_alarms":     db_alarms,
            "global_radar": {
                **radar_cfg,
                "running": get_radar_status(),   # live thread status — not stored in DB
            },
            "favorite_teams":   fav_teams,
            "favorite_players": fav_players,
            "historical_mode":  hist_cfg,
        }

    except Exception as e:
        log.error("Error in active_monitors: %s", e)
        return _empty_response


@app.delete("/active-monitors/game/{game_id}")
def cancel_game(game_id: str, user_token: str = ""):
    cancelled = cancel_game_alarm(game_id)
    return {"cancelled": cancelled, "game_id": game_id}


@app.delete("/active-monitors/player/{player_id}")
def cancel_player(player_id: str, user_token: str = ""):
    cancelled = cancel_player_alarm(player_id)
    return {"cancelled": cancelled, "player_id": player_id}


@app.delete("/active-monitors/db/{alarm_id}")
def cancel_db_alarm(alarm_id: int, user_token: str = ""):
    """Permanently delete a PostgreSQL alarm after verifying ownership."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not user_token:
        raise HTTPException(status_code=400, detail="user_token is required.")
    with Session(engine) as session:
        alarm = session.get(Alarm, alarm_id)
        if alarm is None:
            raise HTTPException(status_code=404, detail="Alarm not found.")
        if alarm.user_token != user_token:
            raise HTTPException(status_code=403, detail="Not your alarm.")
        target_id      = alarm.target_id
        condition_type = alarm.condition_type
        session.delete(alarm)
        session.commit()
    # Also stop any in-memory monitoring thread for this target so it doesn't
    # keep logging after the DB row is gone.
    if condition_type == "score_diff":
        cancel_game_alarm(target_id)
    else:
        cancel_player_alarm(target_id)
    return {"deleted": True, "alarm_id": alarm_id}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
