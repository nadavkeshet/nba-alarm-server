import os
import asyncio
import logging
import threading
import requests
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("nba_monitor")

from epg import get_broadcasts_for_game, get_all_broadcasts
from i18n import (t_title, t_body_game, t_body_period_start, t_body_scoring,
                  t_body_early_pts, t_body_triple_double, translate_game_name)

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, text, or_
from sqlalchemy.orm import DeclarativeBase, Session

from monitor import (
    # Active monitors (in-memory; always empty — threads removed)
    get_active_alarms,
    get_active_player_alarms,
    cancel_game_alarm,
    cancel_player_alarm,
    # Roster
    fetch_game_roster,
    fetch_game_summary_roster,
    update_all_rosters,
    get_all_teams,
    # Radar
    start_radar_job,
    get_radar_status,
    # Token (in-memory only)
    save_push_token,
    get_saved_push_token,
    # Notifications
    _send_push_notification,
    # Clock helper
    _clock_minutes_remaining,
    # Data
    get_today_games,
    get_games_for_date,
    # Startup
    clear_stale_state,
    # DB deactivation registration
    register_db_deactivate,
    # Multi-user radar registration
    register_db_get_all_users,
    # Invalid token cleanup
    register_db_invalidate_token,
    register_db_save_notification,
    register_db_get_user_language,
    # Fired-alert persistence (survives server restarts)
    register_db_persist_fired,
    register_db_check_fired,
    hydrate_fired_alerts,
    # Dedup (shared with radar so poller and radar never double-fire)
    _fire_once,
    # Test-only: direct player monitoring thread
    start_player_monitoring_job,
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
    home_team_id   = Column(String,  nullable=True)
    away_team_id   = Column(String,  nullable=True)
    alert_type       = Column(String,  nullable=True, default="alarm")
    notify_at_start  = Column(Boolean, default=False, nullable=False)
    game_date        = Column(String,  nullable=True)   # "YYYYMMDD" — None means today
    is_active        = Column(Boolean, default=True, nullable=False)
    created_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FavoritePlayerDB(_Base):
    """Persistent favorite player — stays active across restarts."""
    __tablename__ = "favorites"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    target_id     = Column(String,  nullable=False)   # player id
    target_name   = Column(String,  nullable=True)
    team_name     = Column(String,  nullable=True)
    total_pts     = Column(Integer, nullable=True)    # None = condition disabled
    early_pts     = Column(Integer, nullable=True)
    early_pts_q   = Column(Integer, nullable=True)
    td_watch        = Column(Boolean, default=False, nullable=False)
    alert_type      = Column(String,  nullable=True, default='silent')
    broadcast_only  = Column(Boolean, default=False, nullable=False)
    paused          = Column(Boolean, default=False, nullable=False)
    created_at      = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FavoriteTeamDB(_Base):
    """Persistent favorite team — stays active across restarts."""
    __tablename__ = "favorite_teams"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    team_id       = Column(String,  nullable=False)
    team_name     = Column(String,  nullable=True)
    score_gap        = Column(Integer, nullable=True)    # max_diff
    start_quarter    = Column(Integer, nullable=True)
    alert_type       = Column(String,  nullable=True, default='silent')
    broadcast_only   = Column(Boolean, default=False, nullable=False)
    notify_at_start  = Column(Boolean, default=False, nullable=False)
    paused           = Column(Boolean, default=False, nullable=False)
    created_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FiredAlertDB(_Base):
    """
    Persisted record of every radar/favorite alert fired today.
    Hydrated into _FIRED_ALERTS on startup so server restarts during a game
    don't cause duplicate wake-ups.  Rows older than today are deleted at startup.
    """
    __tablename__ = "fired_alerts"

    key      = Column(String,                primary_key=True)
    fired_at = Column(DateTime(timezone=True), nullable=False)


class NotificationHistoryDB(_Base):
    """Every notification sent to a user, for Trophy Room display."""
    __tablename__ = "notification_history"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    user_token = Column(String, nullable=False, index=True)
    title     = Column(String, nullable=False)
    body      = Column(String, nullable=False)
    sent_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
    sound_channel           = Column(String,  default="classic_alarm", nullable=False)
    language                = Column(String,  default="en", nullable=False)
    created_at              = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
        "ALTER TABLE alarms ADD COLUMN alert_type    VARCHAR DEFAULT 'alarm'",
        "ALTER TABLE alarms ADD COLUMN home_team_id  VARCHAR",
        "ALTER TABLE alarms ADD COLUMN away_team_id  VARCHAR",
        "ALTER TABLE alarms ADD COLUMN created_at TIMESTAMPTZ",
        "ALTER TABLE favorites ADD COLUMN broadcast_only BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE favorite_teams ADD COLUMN broadcast_only BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE user_preferences ADD COLUMN sound_channel VARCHAR NOT NULL DEFAULT 'classic_alarm'",
        "ALTER TABLE alarms ADD COLUMN notify_at_start BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE favorite_teams ADD COLUMN notify_at_start BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE user_preferences ADD COLUMN language VARCHAR NOT NULL DEFAULT 'en'",
        "ALTER TABLE alarms ADD COLUMN game_date VARCHAR",
        "ALTER TABLE favorites ADD COLUMN paused BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE favorite_teams ADD COLUMN paused BOOLEAN DEFAULT FALSE NOT NULL",
    ]
    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
        except Exception:
            pass  # column already exists – safe to ignore


def _migrate_team_ids_to_365() -> None:
    """
    Migrate any legacy team IDs (ESPN or API-Basketball) in favorite_teams
    to 365scores IDs. Safe to re-run; rows already on 365 IDs are skipped.

    Player IDs are not auto-migrated — users re-add players from the in-app
    roster, which now serves 365scores IDs.
    """
    if engine is None:
        return
    from score365_adapter import LEGACY_TO_365_TEAM_ID

    try:
        with Session(engine) as session:
            teams = session.query(FavoriteTeamDB).all()
            migrated = 0
            for t in teams:
                new_id = LEGACY_TO_365_TEAM_ID.get(t.team_id)
                if new_id and new_id != t.team_id:
                    t.team_id = new_id
                    migrated += 1
            if migrated:
                session.commit()
                log.info("ID MIGRATION: %d favorite_team row(s) → 365scores IDs.", migrated)
    except Exception as exc:
        log.error("ID MIGRATION failed: %s", exc)


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


def _save_notification(user_token: str, title: str, body: str) -> None:
    """Save a sent notification to history for Trophy Room."""
    if engine is None:
        return
    try:
        with Session(engine) as session:
            session.add(NotificationHistoryDB(
                user_token=user_token, title=title, body=body,
            ))
            session.commit()
    except Exception as exc:
        log.error("DB: save notification history failed – %s", exc)


register_db_save_notification(_save_notification)


def _get_user_language_from_db(push_token: str) -> str:
    if engine is None:
        return "en"
    try:
        with Session(engine) as session:
            pref = session.get(UserPreferenceDB, push_token)
            if pref and hasattr(pref, 'language') and pref.language:
                return pref.language
    except Exception:
        pass
    return "en"


register_db_get_user_language(_get_user_language_from_db)


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


def _check_fired_alert_in_db(key: str) -> bool:
    """Return True if this key was already fired today (exists in FiredAlertDB)."""
    if engine is None:
        return False
    try:
        with Session(engine) as session:
            return session.get(FiredAlertDB, key) is not None
    except Exception:
        return False


register_db_check_fired(_check_fired_alert_in_db)


def _load_fired_alerts_from_db() -> None:
    """
    At startup: load today's fired-alert keys from DB into the in-memory set,
    then delete rows from previous days (they're no longer needed).
    """
    if engine is None:
        return
    try:
        # Use Eastern time so the prefix never changes mid-game (games end before 7am ET)
        today_prefix = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y%m%d")
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


def _purge_old_alarms() -> None:
    """Delete inactive alarms older than 7 days to keep the table lean."""
    if engine is None:
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        with Session(engine) as session:
            deleted = (
                session.query(Alarm)
                .filter(Alarm.is_active.is_(False))
                .filter(Alarm.created_at < cutoff)
                .delete(synchronize_session=False)
            )
            session.commit()
        if deleted:
            log.info("STARTUP: purged %d old inactive alarm(s).", deleted)
    except Exception as exc:
        log.error("DB: _purge_old_alarms failed – %s", exc)


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
                    "td_watch":       bool(p.td_watch),
                    "alert_type":     p.alert_type or "silent",
                    "broadcast_only": getattr(p, 'broadcast_only', False),
                }
                for p in players
            }
            fav_teams = [
                {
                    "team_id":        t.team_id,
                    "team_name":      t.team_name or "",
                    "max_diff":       t.score_gap or 5,
                    "start_quarter":  t.start_quarter or 4,
                    "alert_type":     t.alert_type or "silent",
                    "broadcast_only":  getattr(t, 'broadcast_only', False),
                    "notify_at_start": getattr(t, 'notify_at_start', False),
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

            # Sound channel: read from DB per user
            channel = pref.sound_channel if pref and hasattr(pref, 'sound_channel') and pref.sound_channel else "classic_alarm"
            lang = pref.language if pref and hasattr(pref, 'language') and pref.language else "en"

            users.append({
                "token":   token,
                "channel": channel,
                "language": lang,
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

from score365_adapter import fetch_player_boxscore as api_fetch_boxscore


def _get_event_state(event: dict) -> str:
    """Return game status state: 'pre' | 'in' | 'post'."""
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
    using the scoreboard leaders block. Returns empty dict if player not found.
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


_summary_cache: dict[str, dict] = {}   # game_id → boxscore JSON (shared across poller ticks)


def _fetch_game_summary(game_id: str) -> dict | None:
    """Fetch (and cache) the boxscore for a specific game via API-Basketball."""
    if game_id in _summary_cache:
        return _summary_cache[game_id]
    try:
        data = api_fetch_boxscore(game_id)
        if data:
            _summary_cache[game_id] = data
        return data
    except Exception as exc:
        log.warning("_fetch_game_summary failed for %s: %s", game_id, exc)
        return None


def _player_pts_from_summary(summary: dict, player_id: str) -> int | None:
    """Extract current points for a player from the boxscore summary."""
    try:
        for section in summary.get("boxscore", {}).get("players", []):
            for stats_group in section.get("statistics", []):
                names = stats_group.get("names", [])
                for entry in stats_group.get("athletes", []):
                    if str(entry.get("athlete", {}).get("id")) == str(player_id):
                        raw = dict(zip(names, entry.get("stats", [])))
                        return int(raw.get("PTS", 0) or 0)
    except Exception:
        pass
    return None


def _player_stats_from_summary(summary: dict, player_id: str) -> dict[str, int]:
    """Extract PTS/REB/AST/STL/BLK for a player from the boxscore summary."""
    try:
        for section in summary.get("boxscore", {}).get("players", []):
            for stats_group in section.get("statistics", []):
                names = stats_group.get("names", [])
                for entry in stats_group.get("athletes", []):
                    if str(entry.get("athlete", {}).get("id")) == str(player_id):
                        raw = dict(zip(names, entry.get("stats", [])))
                        return {k: int(raw.get(k, 0) or 0) for k in ("PTS", "REB", "AST", "STL", "BLK")}
    except Exception:
        pass
    return {}


def _period_str(period: int) -> str:
    return f"Q{period}" if period <= 4 else f"OT{period - 4}"


def _check_and_fire(alarm: Alarm, events: list[dict], summary: dict | None = None, language: str = "en") -> dict | None | bool:
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
            if state != "in":
                continue

            # "Notify at start" — fire when the selected quarter begins
            if getattr(alarm, 'notify_at_start', False):
                quarter_started = False
                if required_quarter == 5:
                    quarter_started = period > 4 or (period == 4 and _clock_minutes_remaining(event) <= 5.0)
                else:
                    quarter_started = period >= required_quarter
                if quarter_started:
                    diff = _extract_game_diff(event) or 0
                    return {
                        "title": t_title("period_starting", language),
                        "body":  t_body_period_start(game_name, period, diff, language),
                    }

            if required_quarter == 5:
                last5 = period > 4 or (period == 4 and _clock_minutes_remaining(event) <= 5.0)
                if not last5:
                    continue
            elif period < required_quarter:
                continue
            diff = _extract_game_diff(event)
            if diff is not None and diff <= alarm.threshold:
                return {
                    "title": t_title("smart_alarm", language),
                    "body":  t_body_game(game_name, period, diff, language),
                }

    elif alarm.condition_type == "points":
        quarter_limit = alarm.start_quarter or 0
        for event in events:
            if alarm.game_id and str(event.get("id")) != str(alarm.game_id):
                continue  # only check the specific game this alarm was set for
            state  = _get_event_state(event)
            period = _get_event_period(event)
            if quarter_limit > 0 and period > quarter_limit:
                return None
            # Use full boxscore if available; fall back to leaders block
            pts = (_player_pts_from_summary(summary, alarm.target_id)
                   if summary else None)
            if pts is None:
                pts = _extract_player_pts(event, alarm.target_id)
            if state == "post" and (pts is None or pts < alarm.threshold):
                return None
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
            if alarm.game_id and str(event.get("id")) != str(alarm.game_id):
                continue  # only check the specific game this alarm was set for
            stats = (_player_stats_from_summary(summary, alarm.target_id)
                     if summary else {})
            if not stats:
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
      1. Read the shared scoreboard cache (populated by monitor.py).
      2. Load all is_active alarms from DB.
      3. Check each alarm; fire + deactivate on match.
    """
    while True:
        await asyncio.sleep(10)
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

            # Pre-fetch boxscore summaries for all player alarms (grouped by game)
            # Clear the per-process cache first so each tick gets fresh stats
            _summary_cache.clear()
            player_game_ids = {
                alarm.game_id for alarm in alarms
                if alarm.condition_type in ("points", "triple_double") and alarm.game_id
            }
            summaries: dict[str, dict | None] = {}
            for gid in player_game_ids:
                summaries[gid] = await asyncio.to_thread(_fetch_game_summary, gid)

            from monitor import _eastern_today as _et
            today_str = _et()

            for alarm in alarms:
                try:
                    # Skip alarms scheduled for a future date — wait until their day
                    if alarm.game_date and alarm.game_date > today_str:
                        continue
                    summary = summaries.get(alarm.game_id) if alarm.game_id else None
                    alarm_lang = _get_user_language_from_db(alarm.user_token)
                    result = _check_and_fire(alarm, events, summary, language=alarm_lang)
                    if isinstance(result, dict):
                        # Build dedup key using same format as the radar so
                        # poller and radar share one dedup set and never both fire.
                        gid = alarm.game_id or alarm.target_id
                        pid = alarm.target_id
                        if alarm.condition_type == "score_diff":
                            dedup_key = f"{gid}_game_q{alarm.start_quarter or 4}_id{alarm.id}"
                        elif alarm.condition_type == "points" and (alarm.start_quarter or 0) > 0:
                            dedup_key = f"{gid}_{pid}_early_pts_q{alarm.start_quarter}"
                        elif alarm.condition_type == "points":
                            dedup_key = f"{gid}_{pid}_total_pts"
                        else:  # triple_double
                            dedup_key = f"{gid}_{pid}_triple_double"

                        if not _fire_once(dedup_key, alarm.user_token):
                            # Already fired by radar or a previous poller tick — skip
                            with Session(engine) as session:
                                db_row = session.get(Alarm, alarm.id)
                                if db_row:
                                    db_row.is_active = False
                                    session.commit()
                            continue

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
                        log.info("ALARM EXPIRED   | alarm_id=%d | %s | condition never met | ...%s",
                                 alarm.id, alarm.target_name or alarm.target_id, alarm.user_token[-8:])
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


def _restart_alarm_threads_from_db() -> None:
    """
    On startup: expire stale alarms whose game_id is no longer in today's
    today's schedule (game is over or postponed). Alarms whose game_id IS in
    today's schedule are left active — the poller will handle them normally.
    This handles the case where the server crashed mid-game and the poller
    never got a chance to mark those alarms as expired.
    """
    if engine is None:
        return
    try:
        with Session(engine) as session:
            active_alarms = (
                session.query(Alarm)
                .filter(Alarm.is_active.is_(True))
                .all()
            )
            if not active_alarms:
                return

            # Fetch today's game IDs (already cached if pre-warm ran first)
            try:
                today_games = get_today_games()
                today_game_ids = {str(g["id"]) for g in today_games}
            except Exception:
                today_game_ids = None  # API unavailable — skip cleanup, be safe

            from monitor import _eastern_today as _et
            today_str = _et()

            expired = 0
            if today_game_ids is not None:
                for row in active_alarms:
                    # Never expire alarms scheduled for a future date
                    if row.game_date and row.game_date > today_str:
                        continue
                    game_id = str(row.game_id or row.target_id or "")
                    if game_id and game_id not in today_game_ids:
                        row.is_active = False
                        expired += 1
                if expired:
                    session.commit()
                    log.info("STARTUP: expired %d stale alarm(s) not in today's schedule.", expired)

            count = session.query(Alarm).filter(Alarm.is_active.is_(True)).count()
        if count:
            log.info("STARTUP: %d active DB alarm(s) will be handled by the poller.", count)
    except Exception as exc:
        log.error("STARTUP: failed to count active alarms – %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Startup ──
    _init_db()
    _migrate_team_ids_to_365()  # legacy ESPN/API-Basketball → 365scores
    _load_fired_alerts_from_db()   # hydrate dedup set before radar starts
    _purge_old_alarms()            # delete inactive alarms older than 7 days
    clear_stale_state()
    _restart_alarm_threads_from_db()
    threading.Thread(target=update_all_rosters, daemon=True).start()
    # Pre-warm games cache so /games is ready immediately after startup
    threading.Thread(target=get_today_games, daemon=True).start()
    # Radar reads all users directly from DB — always start it
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

app = FastAPI(title="Clutch Alarm Server", lifespan=lifespan)

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
    return {"status": "ok", "version": "ios-v5", "db": {"connected": db_ok, "detail": db_msg}}


# ── DB Alarm Endpoint ──────────────────────────────────────────────────────────

class SetAlarmRequest(BaseModel):
    user_token:      str
    target_id:       str
    target_name:     str = ""
    condition_type:  str
    threshold:       int
    start_quarter:   int = 4
    game_id:         str = ""
    game_name:       str = ""
    alert_type:      str = "alarm"
    notify_at_start: bool = False


@app.post("/set_alarm")
def set_alarm(req: SetAlarmRequest):
    """Persist a new alarm to PostgreSQL."""
    if not req.user_token or not req.user_token.strip():
        raise HTTPException(status_code=400, detail="user_token is required.")
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
            game_id=req.game_id or req.target_id,
            game_name=req.game_name or req.target_name or req.target_id,
            alert_type=req.alert_type or "alarm",
            notify_at_start=req.notify_at_start,
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
    sound_channel: str      = "classic_alarm"
    alert_type: str         = "alarm"
    home_team_id: str       = ""
    away_team_id: str       = ""
    notify_at_start: bool   = False
    game_date: Optional[str] = None   # "YYYYMMDD" for future-day alarms


@app.post("/start-alarm")
async def start_alarm(req: AlarmRequest):
    """Register game alarm in DB; _alarm_poller handles the monitoring."""
    if not req.push_token or not req.push_token.strip():
        raise HTTPException(status_code=400, detail="push_token is required.")
    save_push_token(req.push_token, req.sound_channel)
    if engine is not None:
        try:
            with Session(engine) as session:
                # Dedup: replace any existing active alarm for same user+game
                session.query(Alarm).filter(
                    Alarm.user_token     == req.push_token,
                    Alarm.target_id      == req.game_id,
                    Alarm.condition_type == "score_diff",
                    Alarm.is_active.is_(True),
                ).update({"is_active": False}, synchronize_session=False)
                session.add(Alarm(
                    user_token       = req.push_token,
                    target_id        = req.game_id,
                    target_name      = req.game_name,
                    condition_type   = "score_diff",
                    threshold        = req.max_diff,
                    start_quarter    = req.start_quarter,
                    game_id          = req.game_id,
                    game_name        = req.game_name,
                    home_team_id     = req.home_team_id or None,
                    away_team_id     = req.away_team_id or None,
                    alert_type       = req.alert_type,
                    notify_at_start  = req.notify_at_start,
                    game_date        = req.game_date or None,
                ))
                session.commit()
        except Exception as exc:
            log.error("start_alarm DB write failed: %s", exc)
    log.info("ALARM REGISTERED | game=%s | diff<=%d Q%d+ | ...%s",
             req.game_name, req.max_diff, req.start_quarter, req.push_token[-8:])
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
    game_date: Optional[str] = None   # "YYYYMMDD" for future-day alarms


@app.post("/start-player-alarm")
async def start_player_alarm(req: PlayerAlarmRequest):
    """Register player alarm in DB; _alarm_poller handles the monitoring."""
    if not req.push_token or not req.push_token.strip():
        raise HTTPException(status_code=400, detail="push_token is required.")
    save_push_token(req.push_token, req.sound_channel)
    if engine is not None:
        try:
            if req.condition == "triple_double":
                condition_type = "triple_double"
                start_q = None
            elif req.condition == "pts_early":
                condition_type = "points"
                start_q = req.quarter_deadline
            else:  # pts_goal
                condition_type = "points"
                start_q = None
            with Session(engine) as session:
                # Dedup: replace any existing active alarm for same user+player+condition
                q = session.query(Alarm).filter(
                    Alarm.user_token     == req.push_token,
                    Alarm.target_id      == req.player_id,
                    Alarm.condition_type == condition_type,
                    Alarm.is_active.is_(True),
                )
                if start_q is None:
                    # pts_goal: dedup rows stored with either NULL or 0 (legacy /set_alarm used 0)
                    q = q.filter(or_(Alarm.start_quarter.is_(None), Alarm.start_quarter == 0))
                else:
                    q = q.filter(Alarm.start_quarter == start_q)
                q.update({"is_active": False}, synchronize_session=False)
                session.add(Alarm(
                    user_token     = req.push_token,
                    target_id      = req.player_id,
                    target_name    = req.player_name,
                    condition_type = condition_type,
                    threshold      = req.pts_threshold,
                    start_quarter  = start_q,
                    game_id        = req.game_id,
                    game_name      = req.game_name,
                    alert_type     = req.alert_type,
                    game_date      = req.game_date or None,
                ))
                session.commit()
        except Exception as exc:
            log.error("start_player_alarm DB write failed: %s", exc)
    log.info("ALARM REGISTERED | player=%s | %s threshold=%d | ...%s",
             req.player_name, req.condition, req.pts_threshold, req.push_token[-8:])
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
    from i18n import t_title as _t_title, t_body_test as _t_body_test
    lang = _get_user_language_from_db(req.push_token)
    _send_push_notification(
        push_token=req.push_token,
        title=_t_title("test", lang),
        body=_t_body_test(lang),
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

_SETTINGS_DEFAULTS = {
    "GlobalGameRadar": {"enabled": False, "max_diff": 5, "start_quarter": 4, "alert_type": "silent"},
    "FavoriteTeams":   [],
    "FavoritePlayers": {},
    "HistoricalMode":  {
        "enabled": False,
        "alert_type": "silent",
        "categories": {"scoring_40_half": False, "threes_11": False, "quad_double_threat": False},
    },
}


@app.get("/settings")
def get_settings(user_token: str = ""):
    if engine is None or not user_token:
        return dict(_SETTINGS_DEFAULTS)
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
                    "early_pts":     p.early_pts,
                    "td_watch":       bool(p.td_watch),
                    "alert_type":     p.alert_type or "silent",
                    "broadcast_only": getattr(p, 'broadcast_only', False),
                    "paused":         getattr(p, 'paused', False),
                }
                for p in players
            }
            fav_teams = [
                {
                    "team_id":        t.team_id,
                    "team_name":      t.team_name or "",
                    "max_diff":       t.score_gap or 5,
                    "start_quarter":  t.start_quarter or 4,
                    "alert_type":     t.alert_type or "silent",
                    "broadcast_only":  getattr(t, 'broadcast_only', False),
                    "notify_at_start": getattr(t, 'notify_at_start', False),
                    "paused":          getattr(t, 'paused', False),
                }
                for t in teams
            ]
            radar_cfg = _SETTINGS_DEFAULTS["GlobalGameRadar"].copy()
            hist_cfg  = {
                "enabled": False, "alert_type": "silent",
                "categories": {"scoring_40_half": False, "threes_11": False, "quad_double_threat": False},
            }
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
            "FavoritePlayers": fav_players,
            "FavoriteTeams":   fav_teams,
            "GlobalGameRadar": radar_cfg,
            "HistoricalMode":  hist_cfg,
        }
    except Exception as exc:
        log.error("DB: /settings read error – %s", exc)
        return dict(_SETTINGS_DEFAULTS)


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
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "GlobalGameRadar": {
        "enabled": req.enabled, "max_diff": req.max_diff,
        "start_quarter": req.start_quarter, "alert_type": req.alert_type,
    }}


class FavoriteTeam(BaseModel):
    user_token:      str = ""
    team_id:         str
    team_name:       str
    max_diff:        int = 5
    start_quarter:   int = 4
    alert_type:      str = "silent"
    broadcast_only:  bool = False
    free_only:       bool = False
    notify_at_start: bool = False


@app.post("/settings/teams")
def upsert_team(req: FavoriteTeam):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoriteTeamDB).filter_by(
                    user_token=req.user_token, team_id=req.team_id
                ).first()
                if existing:
                    existing.team_name      = req.team_name
                    existing.score_gap      = req.max_diff
                    existing.start_quarter  = req.start_quarter
                    existing.alert_type     = req.alert_type
                    existing.broadcast_only  = getattr(req, 'broadcast_only', False)
                    existing.notify_at_start = getattr(req, 'notify_at_start', False)
                else:
                    session.add(FavoriteTeamDB(
                        user_token=req.user_token, team_id=req.team_id,
                        team_name=req.team_name, score_gap=req.max_diff,
                        start_quarter=req.start_quarter, alert_type=req.alert_type,
                        broadcast_only=getattr(req, 'broadcast_only', False),
                        notify_at_start=getattr(req, 'notify_at_start', False),
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: upsert_team failed – %s", exc)
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
    return {"status": "removed"}


class FavoritePlayer(BaseModel):
    user_token:     str = ""
    player_id:      str
    name:           str
    total_pts:      Optional[int]  = None
    early_pts_q:    Optional[int]  = None
    early_pts:      Optional[int]  = None
    td_watch:       bool           = False
    alert_type:     str            = "silent"
    broadcast_only: bool           = False


@app.post("/settings/players")
def upsert_player(req: FavoritePlayer):
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoritePlayerDB).filter_by(
                    user_token=req.user_token, target_id=req.player_id
                ).first()
                if existing:
                    existing.target_name    = req.name
                    existing.total_pts      = req.total_pts
                    existing.early_pts_q    = req.early_pts_q
                    existing.early_pts      = req.early_pts
                    existing.td_watch       = req.td_watch
                    existing.alert_type     = req.alert_type
                    existing.broadcast_only = req.broadcast_only
                else:
                    session.add(FavoritePlayerDB(
                        user_token=req.user_token, target_id=req.player_id,
                        target_name=req.name, total_pts=req.total_pts,
                        early_pts_q=req.early_pts_q, early_pts=req.early_pts,
                        td_watch=req.td_watch, alert_type=req.alert_type,
                        broadcast_only=req.broadcast_only,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: upsert_player failed – %s", exc)
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
    hist_mode = {
        "enabled": req.enabled, "alert_type": req.alert_type,
        "categories": {
            "scoring_40_half":    req.categories.scoring_40_half,
            "threes_11":          req.categories.threes_11,
            "quad_double_threat": req.categories.quad_double_threat,
        },
    }
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "HistoricalMode": hist_mode}


class GlobalThresholdsRequest(BaseModel):
    pts_threshold:       int  = 40
    triple_double_watch: bool = True
    pts_per_quarter:     int  = 15


@app.post("/settings/thresholds")
def update_thresholds(req: GlobalThresholdsRequest):
    return {"status": "updated", "GlobalThresholds": {
        "pts_threshold":       req.pts_threshold,
        "triple_double_watch": req.triple_double_watch,
        "pts_per_quarter":     req.pts_per_quarter,
    }}


# ── Token Registration ─────────────────────────────────────────────────────────

class RegisterTokenRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"
    old_token: str     = ""
    language: str      = "en"


@app.post("/register-token")
def register_token(req: RegisterTokenRequest):
    if not req.push_token:
        return {"status": "error", "detail": "empty push_token"}
    # If the FCM token rotated, migrate all user data before saving the new token.
    if req.old_token and req.old_token != req.push_token:
        _migrate_token(req.old_token, req.push_token)
    save_push_token(req.push_token, req.sound_channel)
    # Persist sound_channel to DB (per-user, survives restarts)
    if engine is not None:
        try:
            with Session(engine) as session:
                pref = session.get(UserPreferenceDB, req.push_token)
                if pref:
                    pref.sound_channel = req.sound_channel
                    pref.language = req.language or "en"
                else:
                    session.add(UserPreferenceDB(
                        user_token=req.push_token,
                        sound_channel=req.sound_channel,
                        language=req.language or "en",
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: register-token sound_channel save failed – %s", exc)
    threading.Thread(target=start_radar_job, daemon=True).start()
    return {"status": "token_registered", "push_token": req.push_token}


# ── Data Endpoints ─────────────────────────────────────────────────────────────

@app.get("/leagues")
def leagues():
    """Return available leagues."""
    from score365_adapter import LEAGUES
    return {"leagues": [
        {"id": key, "name": cfg["name"], "country": cfg["country"]}
        for key, cfg in LEAGUES.items()
    ]}


@app.get("/games")
def games(date: Optional[str] = None, league: str = "nba"):
    """Return games for a given date (YYYYMMDD) and league. Defaults to today NBA."""
    return {"games": get_games_for_date(date, league=league)}


# ── EPG (TV Broadcast Guide) ─────────────────────────────────────────────────

# Countries whose EPG files are small enough to safely load in memory.
# Large countries (us, gb, de, fr, ...) have massive XML files that OOM the server.
_ALLOWED_EPG_COUNTRIES = {"il"}

@app.get("/epg")
def epg_for_game(game_id: str = "", country: str = "il"):
    """
    Get TV broadcast channels for a specific game.
    Query params: game_id, country (ISO 2-letter code, default: il)
    """
    if country.lower() not in _ALLOWED_EPG_COUNTRIES:
        return {"game_id": game_id, "broadcasts": []}
    event = None
    for g in get_today_games():
        if str(g.get("id")) == game_id:
            event = g
            break
    if event is None:
        return {"broadcasts": [], "error": "game not found"}

    # Extract team abbreviations from game event
    comp = (event.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    home_abbr = ""
    away_abbr = ""
    game_name = event.get("shortName", event.get("name", ""))
    game_date = event.get("date", "")
    for c in competitors:
        team = c.get("team", {})
        abbr = team.get("abbreviation", "")
        if c.get("homeAway") == "home":
            home_abbr = abbr
        else:
            away_abbr = abbr

    broadcasts = get_broadcasts_for_game(
        game_id=game_id,
        game_name=game_name,
        home_abbr=home_abbr,
        away_abbr=away_abbr,
        game_date_utc=game_date,
        country=country,
    )
    return {"game_id": game_id, "broadcasts": broadcasts}


@app.get("/epg/all")
def epg_all_games(country: str = "il"):
    """
    Get all NBA TV broadcasts for today's games, keyed by game_id.
    Returns: { "broadcasts": { "401810906": [...], "401810907": [...] } }
    """
    if country.lower() not in _ALLOWED_EPG_COUNTRIES:
        return {"broadcasts": {}}
    today_games = get_today_games()
    return {"broadcasts": get_all_broadcasts(country=country, games=today_games)}


# ── Watch Score (in-memory cache, keyed by "DATE_HOME_AWAY") ─────────────────

_watch_score_cache: dict[str, dict] = {}


def _get_cached_watch_score(home: str, away: str, date_str: str, league: str) -> dict | None:
    key = f"{league}_{date_str}_{home}_{away}"
    return _watch_score_cache.get(key)


def _cache_watch_score(home: str, away: str, date_str: str, league: str, result) -> dict:
    entry = {
        "score": result.score,
        "tension": result.tension_score,
        "flow": result.flow_score,
        "clutch": result.clutch_score,
        "overtime": result.overtime,
        "buzzer_beater": result.buzzer_beater,
    }
    key = f"{league}_{date_str}_{home}_{away}"
    _watch_score_cache[key] = entry
    return entry


@app.get("/watch-score")
def watch_score(home: str, away: str, date: str = "", league: str = "nba"):
    """
    Calculate the smart watch score (1-10) for a finished game.
    Query params: home (team abbr), away (team abbr), date (YYYYMMDD, default today), league.
    """
    from watch_score import calculate_watch_score_by_teams
    from monitor import _eastern_today
    game_date = date if date else _eastern_today()
    cached = _get_cached_watch_score(home, away, game_date, league)
    if cached:
        return cached
    result = calculate_watch_score_by_teams(
        home_abbr=home, away_abbr=away, date_yyyymmdd=game_date, league=league)
    if result is None:
        return {"error": "Game not found or play-by-play unavailable", "score": None}
    return _cache_watch_score(home, away, game_date, league, result)


@app.get("/watch-scores")
def watch_scores(date: str = "", league: str = "nba"):
    """
    Batch: return watch scores for all finished games on a date for a league.
    Returns: { "scores": { "DEN_SA": { "score": 8.8, ... }, ... } }
    Cached in-memory — subsequent requests are instant.
    """
    from watch_score import calculate_watch_score_by_teams
    from monitor import _eastern_today
    game_date = date if date else _eastern_today()

    events = get_games_for_date(game_date, league=league)
    results: dict[str, dict] = {}

    for event in events:
        state = event.get("status", {}).get("type", {}).get("state", "")
        if state != "post":
            continue
        comp = (event.get("competitions") or [{}])[0]
        home_abbr, away_abbr = "", ""
        for c in comp.get("competitors", []):
            abbr = c.get("team", {}).get("abbreviation", "")
            if c.get("homeAway") == "home":
                home_abbr = abbr
            else:
                away_abbr = abbr
        if not home_abbr or not away_abbr:
            continue

        key = f"{home_abbr}_{away_abbr}"
        cached = _get_cached_watch_score(home_abbr, away_abbr, game_date, league)
        if cached:
            results[key] = cached
            continue

        result = calculate_watch_score_by_teams(
            home_abbr=home_abbr, away_abbr=away_abbr, date_yyyymmdd=game_date, league=league)
        if result:
            results[key] = _cache_watch_score(home_abbr, away_abbr, game_date, league, result)

    return {"scores": results}


@app.get("/trophy-room")
def trophy_room(user_token: str = ""):
    """Get notification history for a user (Trophy Room)."""
    if not user_token or engine is None:
        return {"notifications": []}
    try:
        with Session(engine) as session:
            rows = session.query(NotificationHistoryDB).filter_by(
                user_token=user_token
            ).order_by(NotificationHistoryDB.sent_at.desc()).limit(50).all()
            return {"notifications": [
                {
                    "title": r.title,
                    "body": r.body,
                    "sent_at": r.sent_at.isoformat() if r.sent_at else "",
                }
                for r in rows
            ]}
    except Exception as exc:
        log.error("trophy-room error: %s", exc)
        return {"notifications": []}


@app.get("/roster")
def roster(game_id: str):
    return fetch_game_roster(game_id)


@app.get("/game-summary")
def game_summary(gameId: str, date: Optional[str] = None):
    return fetch_game_summary_roster(gameId, date=date)


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
                            "alarm_id":        r.id,
                            "target_id":       r.target_id,
                            "target_name":     r.target_name or r.target_id,
                            "condition_type":  r.condition_type,
                            "threshold":       r.threshold,
                            "start_quarter":   r.start_quarter if r.start_quarter is not None else 4,
                            "home_team_id":    r.home_team_id or "",
                            "away_team_id":    r.away_team_id or "",
                            "notify_at_start": getattr(r, 'notify_at_start', False),
                            "game_date":       getattr(r, 'game_date', None) or "",
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
                            "paused":      getattr(p, 'paused', False),
                        }
                        for p in player_rows
                    }
                    fav_teams = [
                        {
                            "team_id":        t.team_id,
                            "team_name":      t.team_name or "",
                            "max_diff":       t.score_gap or 5,
                            "start_quarter":  t.start_quarter or 4,
                            "alert_type":     t.alert_type or "silent",
                            "notify_at_start": getattr(t, 'notify_at_start', False),
                            "paused":          getattr(t, 'paused', False),
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
    # Also deactivate any matching DB alarm so the poller doesn't re-fire it
    if engine is not None and user_token:
        try:
            with Session(engine) as session:
                session.query(Alarm).filter(
                    Alarm.user_token == user_token,
                    Alarm.target_id  == game_id,
                    Alarm.condition_type == "score_diff",
                    Alarm.is_active.is_(True),
                ).update({"is_active": False})
                session.commit()
        except Exception as exc:
            log.error("cancel_game DB deactivation failed: %s", exc)
    return {"cancelled": cancelled, "game_id": game_id}


@app.delete("/active-monitors/player/{player_id}")
def cancel_player(player_id: str, user_token: str = ""):
    cancelled = cancel_player_alarm(player_id)
    # Also deactivate any matching DB alarm so the poller doesn't re-fire it
    if engine is not None and user_token:
        try:
            with Session(engine) as session:
                session.query(Alarm).filter(
                    Alarm.user_token == user_token,
                    Alarm.target_id  == player_id,
                    Alarm.condition_type.in_(["points", "triple_double"]),
                    Alarm.is_active.is_(True),
                ).update({"is_active": False}, synchronize_session=False)
                session.commit()
        except Exception as exc:
            log.error("cancel_player DB deactivation failed: %s", exc)
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


# ── Pause / resume favorite players and teams ─────────────────────────────────

class PauseRequest(BaseModel):
    user_token: str
    paused: bool

@app.post("/settings/players/{player_id}/pause")
def pause_player(player_id: str, req: PauseRequest):
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    with Session(engine) as session:
        row = session.query(FavoritePlayerDB).filter_by(
            user_token=req.user_token, target_id=player_id
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Favorite player not found.")
        row.paused = req.paused
        session.commit()
    return {"paused": req.paused, "player_id": player_id}

@app.post("/settings/teams/{team_id}/pause")
def pause_team(team_id: str, req: PauseRequest):
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    with Session(engine) as session:
        row = session.query(FavoriteTeamDB).filter_by(
            user_token=req.user_token, team_id=team_id
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Favorite team not found.")
        row.paused = req.paused
        session.commit()
    return {"paused": req.paused, "team_id": team_id}


# ── Admin: Broadcast push to all users ────────────────────────────────────────

ADMIN_KEY = "clutch_admin_2026"

class BroadcastRequest(BaseModel):
    title: str
    body:  str
    alert_type: str = "info"   # "alarm" plays sound, "info" is silent

@app.post("/admin/broadcast")
def admin_broadcast(req: BroadcastRequest, x_admin_key: str = Header(default="")):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")

    with Session(engine) as session:
        tokens: set[str] = set()
        tokens.update(r[0] for r in session.query(Alarm.user_token).distinct())
        tokens.update(r[0] for r in session.query(FavoritePlayerDB.user_token).distinct())
        tokens.update(r[0] for r in session.query(FavoriteTeamDB.user_token).distinct())
        tokens.update(r[0] for r in session.query(UserPreferenceDB.user_token).distinct())
        tokens.discard("")
        tokens.discard(None)

    sent = 0
    failed = 0
    for token in tokens:
        try:
            _send_push_notification(
                push_token=token,
                title=req.title,
                body=req.body,
                alert_type=req.alert_type,
                sound_channel="classic_alarm" if req.alert_type == "alarm" else "silent",
            )
            sent += 1
        except Exception as exc:
            log.warning("Broadcast: failed for %s: %s", token[-8:], exc)
            failed += 1

    log.info("Broadcast complete: sent=%d failed=%d", sent, failed)
    return {"sent": sent, "failed": failed, "total_tokens": len(tokens)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
