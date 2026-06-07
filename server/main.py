import os
import asyncio
import logging
import secrets
import threading
import time
import requests
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("nba_monitor")

import team_names_he
from epg import get_broadcasts_for_game, get_all_broadcasts
from i18n import (t_title, t_body_game, t_body_period_start, t_body_scoring,
                  t_body_early_pts, t_body_triple_double, translate_game_name)

from fastapi import FastAPI, Header, HTTPException, Query, Response
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
    _eastern_today,
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
    league         = Column(String,  nullable=True, default="nba")  # nba|euroleague|eurocup|bcl|winner_league
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
    team_id       = Column(String,  nullable=True)
    league        = Column(String,  nullable=True, default='nba')   # nba|euroleague|eurocup|bcl|winner_league
    total_pts     = Column(Integer, nullable=True)    # None = condition disabled
    early_pts     = Column(Integer, nullable=True)
    early_pts_q   = Column(Integer, nullable=True)
    td_watch        = Column(Boolean, default=False, nullable=False)
    alert_type      = Column(String,  nullable=True, default='silent')
    broadcast_only  = Column(Boolean, default=False, nullable=False)
    skip_euroleague_evening = Column(Boolean, default=False, nullable=False)
    paused          = Column(Boolean, default=False, nullable=False)
    alt_team_ids    = Column(String,  nullable=True)   # comma-sep alternate provider ids
    created_at      = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FavoriteTeamDB(_Base):
    """Persistent favorite team — stays active across restarts."""
    __tablename__ = "favorite_teams"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    team_id       = Column(String,  nullable=False)
    team_name     = Column(String,  nullable=True)
    league        = Column(String,  nullable=True, default='nba')   # nba|euroleague|eurocup|bcl|winner_league
    score_gap        = Column(Integer, nullable=True)    # max_diff
    start_quarter    = Column(Integer, nullable=True)
    alert_type       = Column(String,  nullable=True, default='silent')
    broadcast_only   = Column(Boolean, default=False, nullable=False)
    skip_euroleague_evening = Column(Boolean, default=False, nullable=False)
    notify_at_start  = Column(Boolean, default=False, nullable=False)
    paused           = Column(Boolean, default=False, nullable=False)
    alt_team_ids     = Column(String,  nullable=True)   # comma-sep other provider ids
    created_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FavoriteLeagueDB(_Base):
    """Followed basketball league — applies the SAME alert criteria as a favorite
    team to EVERY game in the league. Survives restarts. The radar evaluates each
    game in a followed league against these criteria when no favorite-team config
    overrides it."""
    __tablename__ = "favorite_leagues"

    id            = Column(Integer, primary_key=True, index=True)
    user_token    = Column(String,  nullable=False, index=True)
    league        = Column(String,  nullable=False)   # nba|euroleague|... slug
    score_gap        = Column(Integer, nullable=True)   # max_diff
    start_quarter    = Column(Integer, nullable=True)
    alert_type       = Column(String,  nullable=True, default='silent')
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
    # Comma-separated list of league keys the radar should scan for this user.
    # Empty/null means "nba" (backwards compat).
    radar_leagues           = Column(String,  default="nba",   nullable=False)
    historical_mode_enabled = Column(Boolean, default=False,   nullable=False)
    alert_type              = Column(String,  default="silent", nullable=False)  # radar
    hist_alert_type         = Column(String,  default="silent", nullable=False)  # historical
    hist_scoring_40_half    = Column(Boolean, default=False,   nullable=False)
    hist_threes_11          = Column(Boolean, default=False,   nullable=False)
    hist_quad_double        = Column(Boolean, default=False,   nullable=False)
    sound_channel           = Column(String,  default="classic_alarm", nullable=False)
    language                = Column(String,  default="en", nullable=False)
    created_at              = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── v3 Soccer ORM ──────────────────────────────────────────────────────────────

from sqlalchemy import Float, JSON as _SA_JSON  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB  # noqa: E402
_JSONB = _PG_JSONB if engine is not None and "postgres" in str(engine.url).lower() else _SA_JSON


class SoccerAlarmDB(_Base):
    """Soccer alarm. Most types are event-driven; drama_radar is Drama-Engine-driven (P3)."""
    __tablename__ = "soccer_alarms"

    id              = Column(Integer, primary_key=True, index=True)
    user_token      = Column(String,  nullable=False, index=True)
    alarm_type      = Column(String,  nullable=False)   # match | late_drama | knockout_et | comeback | hat_trick | red_card | big_chance | penalty | drama_radar
    match_id        = Column(String,  nullable=True)    # canonical id ('bsd-372'); nullable for radar-type
    match_name      = Column(String,  nullable=True)    # human-readable "Home vs Away"
    competition_id  = Column(String,  nullable=True)    # slug
    player_id       = Column(String,  nullable=True)
    threshold       = Column(Float,   nullable=True)    # SQL column type is REAL (see migrations/001_soccer_schema.sql)
    config          = Column(_JSONB,  nullable=False, default=dict)
    alert_type      = Column(String,  nullable=False, default="alarm")
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    fired_at        = Column(DateTime(timezone=True), nullable=True)


class SoccerFavoritePlayerDB(_Base):
    """Persistent soccer favorite player (e.g. hat-trick watch)."""
    __tablename__ = "soccer_favorites"

    user_token   = Column(String, primary_key=True)
    player_id    = Column(String, primary_key=True)
    player_name  = Column(String, nullable=True)
    team_id      = Column(String, nullable=True)
    # Alternate team ids tracked for this player beyond the primary club — most
    # importantly the player's NATIONAL TEAM, so favoriting e.g. Mbappé surfaces
    # France's games too. Comma-separated; the list endpoint folds them into
    # `team_ids` which the feed matches against.
    alt_team_ids = Column(String, nullable=True)
    config       = Column(_JSONB, nullable=False, default=dict)
    alert_type   = Column(String, nullable=False, default="alarm")
    paused       = Column(Boolean, nullable=False, default=False)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SoccerFavoriteTeamDB(_Base):
    """Persistent soccer favorite team (drives team-scoped drama alerts)."""
    __tablename__ = "soccer_favorite_teams"

    user_token        = Column(String,  primary_key=True)
    team_id           = Column(String,  primary_key=True)
    team_name         = Column(String,  nullable=True)
    competition_id    = Column(String,  nullable=True)
    alt_team_ids      = Column(String,  nullable=True)   # comma-sep other provider ids
    competitions      = Column(String,  nullable=True)   # comma-sep all leagues
    drama_threshold   = Column(Float,   nullable=False, default=7.0)
    alert_type        = Column(String,  nullable=False, default="alarm")
    paused            = Column(Boolean, nullable=False, default=False)
    notify_at_kickoff = Column(Boolean, nullable=False, default=False)
    # Full per-match criteria (drama_radar/late_drama/match_moments/card_penalty/
    # sensation), same shape as a per-match alarm config. When present the monitor
    # applies ALL enabled criteria to the team's matches; when null it falls back
    # to the legacy drama_threshold + notify_at_kickoff behavior.
    config            = Column(_JSONB,  nullable=True)
    created_at        = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SoccerFavoriteCompetitionDB(_Base):
    """Persistent followed soccer competition. Applies the same alert criteria as
    a favorite team to EVERY live match in the competition: a drama-radar alert
    (score.total >= drama_threshold) and, when notify_at_kickoff, a kickoff alert.
    alert_type='silent' (the default / "Just Follow") = display-only, no alerts."""
    __tablename__ = "soccer_favorite_competitions"

    user_token        = Column(String,  primary_key=True)
    competition_id    = Column(String,  primary_key=True)
    drama_threshold   = Column(Float,   nullable=False, default=7.0)
    alert_type        = Column(String,  nullable=True, default="silent")
    notify_at_kickoff = Column(Boolean, nullable=False, default=False)
    paused            = Column(Boolean, nullable=False, default=False)
    # Full per-match criteria applied to EVERY match in the competition (same
    # shape as a per-match alarm config). Null → legacy drama+kickoff behavior.
    config            = Column(_JSONB,  nullable=True)
    created_at        = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WatchScoreCacheDB(_Base):
    """
    Persisted *final* watch scores so they survive restarts and deploys.

    A finished game's score is immutable, so a row here is permanent — written
    once when first computed, read forever after. This is what makes the feed
    badges instant: without it the in-memory cache is wiped on every Railway
    redeploy / cold-start and the app re-pays the BSD recompute. Live scores are
    NOT persisted (they change) — they stay in the in-memory cache (45 s TTL).
    """
    __tablename__ = "watch_score_cache"

    id            = Column(String, primary_key=True)   # "{league}_{date}_{home}_{away}"
    league        = Column(String, index=True)
    date          = Column(String, index=True)
    home          = Column(String)
    away          = Column(String)
    score         = Column(Float)
    tension       = Column(Float)
    flow          = Column(Float)
    clutch        = Column(Float)
    overtime      = Column(Boolean, default=False)
    buzzer_beater = Column(Boolean, default=False)
    computed_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CalendarSubscriptionDB(_Base):
    """Opaque, revocable calendar token -> subscription scope. The public webcal
    URL carries only this random token, never the FCM user_token."""
    __tablename__ = "calendar_subscriptions"

    token      = Column(String, primary_key=True)            # secrets.token_urlsafe(24)
    user_token = Column(String, nullable=False, index=True)  # FCM push token (private)
    scope      = Column(String, nullable=False)              # favorites|custom|all|world_cup
    params     = Column(_JSONB, nullable=False, default=dict)
    revoked    = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SoccerHistoricPrefDB(_Base):
    """Per-user "Football Historic Moments" config. Its own table (not extra
    columns on user_preferences) so create_all provisions it with no ALTER, and
    the per-category flags live in one JSONB blob — new moment types need no
    schema change."""
    __tablename__ = "soccer_historic_prefs"

    user_token  = Column(String, primary_key=True)
    enabled     = Column(Boolean, nullable=False, default=False)
    alert_type  = Column(String,  nullable=False, default="silent")
    categories  = Column(_JSONB,  nullable=False, default=dict)   # {category_key: bool}
    updated_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def _mint_calendar_token() -> str:
    """High-entropy opaque token for calendar subscription URLs (~192 bits)."""
    return secrets.token_urlsafe(24)


# ─── Calendar subscription config + per-token ICS cache ─────────────────────
import time as _time  # noqa: E402  (monotonic clock for the ICS TTL cache)
_CAL_HOST = "clutchalarm.com"
_ICS_CACHE: dict[str, tuple[float, str]] = {}   # token -> (expiry_monotonic, ics_text)
_ICS_TTL = 300.0

_CAL_SCOPES = {"favorites", "custom", "all", "world_cup"}
_CAL_NAMES = {
    "favorites": "Clutch Alarm – Favorites",
    "custom":    "Clutch Alarm – Custom",
    "all":       "Clutch Alarm – All Matches",
    "world_cup": "Clutch Alarm – World Cup",
}


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
        # League scoping (NBA, Euroleague, EuroCup, BCL, Winner League)
        "ALTER TABLE favorite_teams ADD COLUMN league VARCHAR DEFAULT 'nba'",
        "ALTER TABLE favorites      ADD COLUMN league VARCHAR DEFAULT 'nba'",
        "ALTER TABLE user_preferences ADD COLUMN radar_leagues VARCHAR NOT NULL DEFAULT 'nba'",
        "ALTER TABLE alarms         ADD COLUMN league VARCHAR DEFAULT 'nba'",
        "ALTER TABLE favorites      ADD COLUMN skip_euroleague_evening BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE favorite_teams ADD COLUMN skip_euroleague_evening BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE soccer_favorite_teams ADD COLUMN alt_team_ids VARCHAR",
        "ALTER TABLE soccer_favorite_teams ADD COLUMN competitions VARCHAR",
        # Soccer favorite PLAYERS gained alt ids (national team + provider ids).
        # Without this the upsert handler set row.alt_team_ids on an unmapped
        # attribute, so a player's national-team / alternate ids were silently
        # dropped and never matched in the feed.
        "ALTER TABLE soccer_favorites ADD COLUMN alt_team_ids VARCHAR",
        "ALTER TABLE favorite_teams ADD COLUMN alt_team_ids VARCHAR",
        "ALTER TABLE soccer_alarms ADD COLUMN match_name VARCHAR",
        # FavoritePlayerDB columns added to the model after the original
        # `favorites` table was created. Without these, GET /settings does a
        # `SELECT favorites.team_id ...` that 500s on UndefinedColumn — the
        # handler swallows it and returns empty defaults, so NO favorites
        # (teams or players) ever come back even though the saves succeeded.
        "ALTER TABLE favorites ADD COLUMN alt_team_ids VARCHAR",
        "ALTER TABLE favorites ADD COLUMN team_id   VARCHAR",
        "ALTER TABLE favorites ADD COLUMN team_name VARCHAR",
        "ALTER TABLE favorites ADD COLUMN td_watch  BOOLEAN DEFAULT FALSE NOT NULL",
        # Followed-competition alert criteria (league-wide soccer alarms).
        "ALTER TABLE soccer_favorite_competitions ADD COLUMN alert_type VARCHAR DEFAULT 'silent'",
        "ALTER TABLE soccer_favorite_competitions ADD COLUMN notify_at_kickoff BOOLEAN DEFAULT FALSE NOT NULL",
        "ALTER TABLE soccer_favorite_competitions ADD COLUMN paused BOOLEAN DEFAULT FALSE NOT NULL",
        # Full per-match criteria for team/league follows (1:1 with match alerts).
        "ALTER TABLE soccer_favorite_teams ADD COLUMN config JSONB",
        "ALTER TABLE soccer_favorite_competitions ADD COLUMN config JSONB",
    ]
    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
        except Exception:
            pass  # column already exists – safe to ignore

    # ── v3 (soccer) — load multi-statement SQL file ───────────────────────
    soccer_schema_path = Path(__file__).resolve().parent / "migrations" / "001_soccer_schema.sql"
    if soccer_schema_path.exists():
        soccer_sql = soccer_schema_path.read_text()
        # Statements separated by `;\n` (semicolons inside JSONB defaults won't trip
        # this because we keep them quoted on one line)
        for stmt in [s.strip() for s in soccer_sql.split(";\n") if s.strip()]:
            try:
                with engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
            except Exception as exc:
                log.warning("DB: soccer migration skipped (%s): %s", exc, stmt[:80])


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


# ── League helpers ─────────────────────────────────────────────────────────────
# Used by both the alarm endpoints (which need to know which league to fetch
# games from when monitoring) and the favorites endpoints (which need to scope
# the user's preferences). Defined early so both can call into them.

_VALID_LEAGUES = {"nba", "euroleague", "eurocup", "bcl", "winner_league"}


def _league_for_team_id(team_id: str | int | None) -> str | None:
    """
    Best-effort league lookup for a 365scores team id. Tries the in-memory
    roster cache first (always populated for active leagues), then falls back
    to the static team-id maps in score365_adapter. Returns None if the team
    is unknown so callers can decide a sensible default.
    """
    if team_id is None or team_id == "":
        return None
    try:
        tid_int = int(team_id)
    except (TypeError, ValueError):
        return None

    # Roster cache is the freshest source — it tags every team with its league
    # in update_all_rosters().
    try:
        from monitor import _ROSTER_CACHE  # local import to avoid cycles
        cached = _ROSTER_CACHE.get(str(tid_int))
        if cached and cached.get("league"):
            return cached["league"]
    except Exception:
        pass

    # Static fallback for NBA — well-known id range, no HTTP needed.
    from score365_adapter import _365_TEAM_IDS as _NBA_IDS
    if tid_int in _NBA_IDS.values():
        return "nba"

    # Last resort: walk each non-NBA league once. fetch_all_teams uses the
    # 30s _fetch_competition_window cache so this only spends real network
    # the first time it's called per cold start.
    try:
        from score365_adapter import LEAGUES, fetch_all_teams
        for league in LEAGUES:
            if league == "nba":
                continue
            for team in fetch_all_teams(league=league):
                if str(team.get("id", "")) == str(tid_int):
                    return league
    except Exception as exc:
        log.debug("_league_for_team_id walk failed for %s: %s", tid_int, exc)
    return None


def _with_team_name_he(entry: dict) -> dict:
    """Stamp `team_name_he` onto a favorite-team dict (keyed `team_name`) in
    place. No-op on a lookup miss. Favorites are stored with English names, so
    Hebrew is resolved on read."""
    he = team_names_he.hebrew_name(entry.get("team_name"))
    if he:
        entry["team_name_he"] = he
    return entry


def _league_for_alarm(home_team_id: str | None,
                      away_team_id: str | None,
                      provided: str | None) -> str:
    """
    Resolve the league for a new alarm. Priority:
      1. Explicit ?league= from the client (validated)
      2. Roster cache lookup on home_team_id, then away_team_id
      3. Default to 'nba'
    """
    if provided and provided in _VALID_LEAGUES:
        return provided
    for tid in (home_team_id, away_team_id):
        guess = _league_for_team_id(tid)
        if guess in _VALID_LEAGUES:
            return guess
    return "nba"


_365_CDN = "https://imagecache.365scores.com/image/upload/d_Competitors:default1.png/Competitors/"

def _team_logo_for_abbr(abbr: str) -> str:
    """Return the 365scores CDN logo URL for a team abbreviation, or '' if unknown.
    Covers NBA, Euroleague, EuroCup, BCL, and Winner League."""
    if not abbr:
        return ""
    try:
        from score365_adapter import _365_TEAM_IDS, _INTL_TEAM_ID_TO_ABBR
        team_id = _365_TEAM_IDS.get(abbr)
        if team_id:
            return f"{_365_CDN}{team_id}"
        for tid, a in _INTL_TEAM_ID_TO_ABBR.items():
            if a == abbr:
                return f"{_365_CDN}{tid}"
    except Exception:
        pass
    return ""


def _resolve_monitor_game_id(game_id: str, game_name: str, league: str, game_date: str | None = None) -> str:
    """
    Translate a canonical basketball game_id to the bare 365scores numeric ID
    that the alarm poller and boxscore fetcher need.

    The /games endpoint now routes through basketball_source, which returns
    bsd-* or s365-* prefixed IDs.  The _alarm_poller and score365_adapter
    boxscore fetcher both work with bare 365scores numeric IDs, so we resolve
    at alarm-creation time — one cheap cache hit keeps the rest of the system
    unchanged.

      bsd-*   → look up today's 365scores scoreboard by game name
      s365-*  → strip the prefix (already a 365scores ID)
      bare ID → return as-is (legacy path, already correct)

    Falls back to the original game_id on any failure so the alarm is still
    stored (and will self-expire via today_gone within 60 s).
    """
    if not game_id:
        return game_id

    if game_id.startswith("s365-"):
        return game_id.split("-", 1)[1]

    if not game_id.startswith("bsd-"):
        return game_id  # bare 365scores ID or unknown — leave alone

    # bsd-* → cross-reference via the 365scores scoreboard (cached)
    try:
        target_date = game_date or _eastern_today()
        events = get_games_for_date(target_date, league) or []

        gn = (game_name or "").strip().lower()
        for e in events:
            ev_name = (e.get("name") or "").strip().lower()
            if ev_name == gn:
                eid = str(e.get("id", ""))
                if eid:
                    log.info("resolve game_id: %s → %s  (%s)", game_id, eid, game_name)
                    return eid

        # Looser fallback: match on the short abbreviation-style name (e.g. "BKN @ LAL")
        gn_short = gn.replace(" at ", " @ ")
        for e in events:
            ev_short = (e.get("shortName") or "").strip().lower()
            if ev_short and ev_short == gn_short:
                eid = str(e.get("id", ""))
                if eid:
                    log.info("resolve game_id (short): %s → %s  (%s)", game_id, eid, game_name)
                    return eid

        log.warning(
            "resolve game_id: %s (%s) — no 365scores match in %d %s game(s)",
            game_id, game_name, len(events), league,
        )
    except Exception as exc:
        log.warning("resolve game_id failed for %s: %s", game_id, exc)

    return game_id  # fallback: store the BSD id; today_gone will expire it


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
            session.query(CalendarSubscriptionDB).filter_by(user_token=old_token).update(
                {"user_token": new_token}, synchronize_session=False
            )
            # SoccerHistoricPrefDB also has user_token as PK — same raw-SQL +
            # conflict handling as user_preferences below.
            if session.get(SoccerHistoricPrefDB, new_token) is None:
                session.execute(
                    text("UPDATE soccer_historic_prefs SET user_token = :new WHERE user_token = :old"),
                    {"new": new_token, "old": old_token},
                )
            else:
                session.execute(
                    text("DELETE FROM soccer_historic_prefs WHERE user_token = :old"),
                    {"old": old_token},
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


def _cleanup_prefixed_alarms() -> None:
    """
    One-time migration: expire any active alarms whose game_id or target_id
    still has a canonical source prefix (bsd-* or s365-*).

    These were stored before the _resolve_monitor_game_id fix was deployed.
    The alarm poller works with bare 365scores numeric IDs and can never match
    a prefixed ID, so these alarms would never fire or self-expire (they rely
    on today_gone, which requires a non-empty 365scores events list for that
    day — when there are no games the list is empty and cleanup is skipped).
    """
    if engine is None:
        return
    try:
        with Session(engine) as session:
            stale = (
                session.query(Alarm)
                .filter(
                    Alarm.is_active.is_(True),
                    or_(
                        Alarm.game_id.like("bsd-%"),
                        Alarm.game_id.like("s365-%"),
                        Alarm.target_id.like("bsd-%"),
                        Alarm.target_id.like("s365-%"),
                    ),
                )
                .all()
            )
            count = len(stale)
            for row in stale:
                row.is_active = False
            if count:
                session.commit()
                log.info("STARTUP: expired %d pre-fix alarm(s) with canonical game_ids.", count)
    except Exception as exc:
        log.error("DB: _cleanup_prefixed_alarms failed – %s", exc)


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
            all_leagues: list[FavoriteLeagueDB]  = session.query(FavoriteLeagueDB).all()
            all_prefs:   list[UserPreferenceDB]  = session.query(UserPreferenceDB).all()

        # Group by token in Python — no extra DB round-trips
        players_by_token: dict[str, list] = {}
        for p in all_players:
            players_by_token.setdefault(p.user_token, []).append(p)

        teams_by_token: dict[str, list] = {}
        for t in all_teams:
            teams_by_token.setdefault(t.user_token, []).append(t)

        leagues_by_token: dict[str, list] = {}
        for lg in all_leagues:
            leagues_by_token.setdefault(lg.user_token, []).append(lg)

        prefs_by_token: dict[str, UserPreferenceDB] = {p.user_token: p for p in all_prefs}

        all_tokens = set(players_by_token) | set(teams_by_token) | set(leagues_by_token) | set(prefs_by_token)

        users: list[dict] = []
        for token in all_tokens:
            players = players_by_token.get(token, [])
            teams   = teams_by_token.get(token, [])
            leagues = leagues_by_token.get(token, [])
            pref    = prefs_by_token.get(token)

            fav_players = {
                p.target_id: {
                    "name":        p.target_name or "",
                    "league":       (p.league or "nba"),
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
                _with_team_name_he({
                    "team_id":        t.team_id,
                    "team_name":      t.team_name or "",
                    "league":         (t.league or "nba"),
                    "max_diff":       t.score_gap or 5,
                    "start_quarter":  t.start_quarter or 4,
                    "alert_type":     t.alert_type or "silent",
                    "broadcast_only":  getattr(t, 'broadcast_only', False),
                    "notify_at_start": getattr(t, 'notify_at_start', False),
                })
                for t in teams
            ]
            fav_leagues = [
                {
                    "league":          lg.league,
                    "max_diff":        lg.score_gap or 5,
                    "start_quarter":   lg.start_quarter or 4,
                    "alert_type":      lg.alert_type or "silent",
                    "notify_at_start": getattr(lg, 'notify_at_start', False),
                    "paused":          getattr(lg, 'paused', False),
                }
                for lg in leagues if not getattr(lg, 'paused', False)
            ]
            radar_cfg = {
                "enabled":       pref.radar_enabled       if pref else False,
                "max_diff":      pref.radar_gap           if pref else 5,
                "start_quarter": pref.radar_start_q       if pref else 4,
                "alert_type":    pref.alert_type          if pref else "silent",
                "leagues":       _split_leagues(pref.radar_leagues if pref else None),
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
                    "FavoriteLeagues": fav_leagues,
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

from score365_adapter import fetch_player_boxscore as api_fetch_boxscore, next_game_start


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
    """Fetch (and cache) the boxscore for a specific game via 365scores."""
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
        log.info("POLLER DEBUG: checking pts alarm | player=%s id=%s thr=%s game_id=%s summary=%s",
                 player_name, alarm.target_id, alarm.threshold, alarm.game_id, "YES" if summary else "NO")
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
            log.info("POLLER DEBUG: player=%s pts=%s state=%s period=%s thr=%s",
                     player_name, pts, state, period, alarm.threshold)
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
    Dynamic poll interval:
      • No active alarms            → sleep 5 min  (nothing to watch)
      • Alarms exist, no live games → sleep 2 min  (games might start soon)
      • Live games present          → sleep 60 s   (active monitoring)

    Per cycle:
      1. Load all is_active alarms from DB.
      2. Group by (game_date, league) and fetch the right scoreboard once per group.
      3. Run _check_and_fire against each alarm with its proper event list.
      4. Auto-expire alarms whose game_date is more than 1 day in the past.
    """
    while True:
        if engine is None:
            await asyncio.sleep(60)
            continue
        alarms: list = []
        events_by_group: dict[tuple[str, str], list[dict]] = {}
        try:
            with Session(engine) as session:
                alarms = (
                    session.query(Alarm)
                           .filter(Alarm.is_active.is_(True))
                           .all()
                )

            today_str = _eastern_today()
            yesterday_str = (datetime.now(timezone.utc) - timedelta(hours=5, days=1)).strftime("%Y%m%d")

            # ── Group alarms by (date, league) so each scoreboard is fetched once ──
            groups: dict[tuple[str, str], list[Alarm]] = {}
            stale: list[Alarm] = []
            for alarm in alarms:
                # Skip alarms scheduled for a future date — wait for their day
                if alarm.game_date and alarm.game_date > today_str:
                    continue
                # Alarms with no game_date are pre-migration orphans — expire them all.
                if not alarm.game_date:
                    stale.append(alarm)
                    continue
                # Auto-expire alarms whose game day is in the past — the game is
                # over and we either missed the window or the condition was
                # never met. Without this, the user's "active alarms" list grows
                # forever after they set an alarm on a finished game.
                if alarm.game_date < yesterday_str:
                    stale.append(alarm)
                    continue

                date_key = alarm.game_date or today_str
                league_key = (alarm.league or "nba")
                groups.setdefault((date_key, league_key), []).append(alarm)

            # Drop the stale ones first so we never check them
            if stale:
                with Session(engine) as session:
                    for a in stale:
                        row = session.get(Alarm, a.id)
                        if row:
                            row.is_active = False
                    session.commit()
                for a in stale:
                    log.info("ALARM EXPIRED   | alarm_id=%d | %s | game_date=%s past | ...%s",
                             a.id, a.target_name or a.target_id, a.game_date, a.user_token[-8:])

            # ── Fetch scoreboards once per (date, league) ──
            for key in groups:
                date_str, league = key
                try:
                    events_by_group[key] = await asyncio.to_thread(get_games_for_date, date_str, league)
                except Exception as exc:
                    log.warning("Poller: %s/%s scoreboard fetch failed: %s", league, date_str, exc)
                    events_by_group[key] = []

            # Pre-fetch boxscore summaries for all player alarms (still ESPN-based)
            _summary_cache.clear()
            player_game_ids = {
                alarm.game_id for alarm in alarms
                if alarm.condition_type in ("points", "triple_double") and alarm.game_id
            }
            summaries: dict[str, dict | None] = {}
            for gid in player_game_ids:
                summaries[gid] = await asyncio.to_thread(_fetch_game_summary, gid)

            for (date_str, league), group_alarms in groups.items():
                events = events_by_group.get((date_str, league), [])
                # Build a set of game IDs that are still in the scoreboard feed
                live_game_ids = {str(e.get("id")) for e in events}
                for alarm in group_alarms:
                    try:
                        summary = summaries.get(alarm.game_id) if alarm.game_id else None
                        alarm_lang = _get_user_language_from_db(alarm.user_token)

                        # If the game is from a past date, or is no longer in the
                        # scoreboard feed for today — it has ended, expire it.
                        # NULL game_date uses today_str (via date_key fallback above),
                        # so past-date NULL alarms are already grouped under today_str.
                        # Additionally expire NULL-date alarms that are no longer in feed.
                        # At this point alarm.game_date is always set (NULL-date alarms
                        # are caught above as stale). Expire if the game is no longer in feed.
                        alarm_game_id = str(alarm.game_id or alarm.target_id)
                        past_date = date_str < today_str
                        today_gone = date_str == today_str and events and alarm_game_id not in live_game_ids
                        game_is_gone = past_date or today_gone
                        if game_is_gone:
                            log.info("ALARM EXPIRED   | alarm_id=%d | %s | game ended | ...%s",
                                     alarm.id, alarm.target_name or alarm.target_id, alarm.user_token[-8:])
                            with Session(engine) as session:
                                db_row = session.get(Alarm, alarm.id)
                                if db_row:
                                    db_row.is_active = False
                                    session.commit()
                            continue

                        result = _check_and_fire(alarm, events, summary, language=alarm_lang)
                        if isinstance(result, dict):
                            # Build dedup key using the same format as the radar
                            # so poller and radar share one dedup set and never
                            # both fire.
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

        # Dynamic sleep: back off when there is nothing live to watch.
        # When no live games are detected, sleep until 2 min before the next
        # scheduled game (across all leagues the active alarms care about).
        # Fall back to 5-min polling if no upcoming game is found in the feed.
        if any(events_by_group.values()):
            await asyncio.sleep(60)    # live games → 1 min
        else:
            alarm_leagues = list({alarm.league or "nba" for alarm in alarms}) if alarms else None
            nxt = await asyncio.to_thread(next_game_start, alarm_leagues)
            if nxt is not None:
                secs = (nxt - datetime.now(timezone.utc)).total_seconds() - 120
                secs = max(60.0, min(secs, 3600.0))  # clamp: 1 min – 1 hour
                log.info("Poller: next game at %s — sleeping %.0f s", nxt.isoformat(), secs)
                await asyncio.sleep(secs)
            else:
                await asyncio.sleep(6 * 3600)  # no upcoming games in feed → 6 h (off-season / schedule not yet published)


# ── App Lifespan ───────────────────────────────────────────────────────────────


def _restart_alarm_threads_from_db() -> None:
    """
    On startup: expire alarms whose game_date is more than a day in the past
    (their game is over and was never matched). Newer alarms are left active —
    the poller will pick them up on its next tick and either fire or expire
    them per (date, league) without any NBA-only assumptions.
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

            yesterday_str = (datetime.now(timezone.utc) - timedelta(hours=5, days=1)).strftime("%Y%m%d")
            expired = 0
            for row in active_alarms:
                if row.game_date and row.game_date < yesterday_str:
                    row.is_active = False
                    expired += 1
            if expired:
                session.commit()
                log.info("STARTUP: expired %d stale alarm(s) (game_date older than yesterday).", expired)

            count = session.query(Alarm).filter(Alarm.is_active.is_(True)).count()
        if count:
            log.info("STARTUP: %d active DB alarm(s) will be handled by the poller.", count)
    except Exception as exc:
        log.error("STARTUP: failed to expire stale alarms – %s", exc)


def _bsd_ws_sub_manager_loop() -> None:
    """Keep the BSD WS subscription set in sync with the set of currently-
    live BSD events. Re-syncs every 30 s.

    Sources of "live" events:
      - Soccer: `bzzoiro_adapter.live_events()` (football).
      - Basketball: `bsd_basketball.live_events()`.

    BSD caps subscriptions at 10 per socket, so we prioritize by
    "is this event referenced by an active alarm?" — those go first, then
    fill remaining slots with whatever else is live.

    The helper runs in a normal thread (not asyncio) so it can call the
    sync BSD REST adapters without trampoline overhead.
    """
    import bsd_websocket
    import bzzoiro_adapter as _bsd_soccer

    log.info("BSD WS sub-manager started (30s sync cycle)")
    while True:
        try:
            ws = bsd_websocket.client()
            if ws._fatal_auth_error:
                # Subscription not active — no work to do
                time.sleep(60)
                continue

            desired: set[int] = set()
            # Soccer only — BSD basketball was disconnected (all basketball is
            # 365-sourced now), so the WS only tracks live football events.
            try:
                for e in _bsd_soccer.live_events() or []:
                    eid = e.get("id")
                    if isinstance(eid, int):
                        desired.add(eid)
            except Exception as exc:
                log.debug("BSD soccer live_events failed in sub-manager: %s", exc)

            # Cap to BSD's per-socket limit. We could shard later, but
            # 10 simultaneous live events across our sports is usually
            # enough — pick a stable order (smallest id first) so the cap
            # is deterministic.
            if len(desired) > bsd_websocket.MAX_SUBS_PER_SOCKET:
                desired = set(sorted(desired)[:bsd_websocket.MAX_SUBS_PER_SOCKET])

            ws.sync_subscriptions(desired)
        except Exception as exc:
            log.warning("WS sub-manager iteration crashed: %s", exc)
        time.sleep(30)


def _seed_soccer_search_index() -> None:
    """Seed soccer team search accumulator from league standings on startup.

    BSD leagues → BSD standings API (authoritative, available even off-season).
    All leagues → 365scores standings as a reliable fallback / supplement,
    since it covers all competitions including playoff rounds.
    """
    try:
        from score365_soccer_adapter import (
            BSD_COMPETITION_IDS,
            COMPETITION_IDS as S365_IDS,
            soccer_league_standings,
        )
        import bzzoiro_adapter as _bsd
        from search_index import remember_soccer_team

        # ── BSD leagues: use BSD standings (authoritative) ──
        for slug, league_id in BSD_COMPETITION_IDS.items():
            try:
                raw = _bsd.league_standings(league_id)
                if not raw:
                    continue
                rows = raw.get("standings") or raw.get("table") or raw.get("rows") or []
                for row in rows:
                    team = row.get("team") if isinstance(row.get("team"), dict) else {}
                    tid  = str(team.get("id") or row.get("team_id") or row.get("id") or "")
                    name = team.get("name") or row.get("team_name") or ""
                    if tid and name:
                        crest = f"https://sports.bzzoiro.com/img/team/{tid}/"
                        remember_soccer_team(tid, name, crest=crest, competition=slug)
            except Exception as e:
                log.debug("soccer seed (BSD standings) skipped for %s: %s", slug, e)

        # ── All leagues: supplement with 365scores standings ──
        # Covers non-BSD leagues (ligat_haal, euros) and fills any gaps from BSD.
        # 365scores standings always reflect the current-season roster, including
        # teams that only appear in playoff rounds not seen in recent game data.
        for slug, comp_id in S365_IDS.items():
            try:
                competitors = soccer_league_standings(comp_id)
                for comp in competitors:
                    tid  = str(comp.get("id") or "")
                    name = comp.get("name") or ""
                    if tid and name:
                        crest = f"https://imagecache.365scores.com/image/upload/d_Competitors:default1.png/Competitors/{tid}"
                        remember_soccer_team(tid, name, crest=crest, competition=slug)
            except Exception as e:
                log.debug("soccer seed (365 standings) skipped for %s: %s", slug, e)

    except Exception as e:
        log.warning("soccer search index seeding failed: %s", e)


# Max concurrent sync (`def`) route handlers. FastAPI runs every non-async
# endpoint on AnyIO's worker threadpool, which defaults to only 40 tokens — so a
# first-run burst of ~100 clients could park all 40 on slow upstream waits and
# queue everything behind them, including /health (observed: health checks timing
# out under stampedes). Upstream fan-out is separately bounded by the shared
# _FEED_POOL/_WATCH_POOL/_DETAIL_POOL/_SUMMARY_POOL, so the route handlers
# themselves are mostly fast cache reads or short pool waits; raising this cap
# just lets more be in flight at once without exploding upstream concurrency.
_SYNC_THREAD_LIMIT = int(os.environ.get("SYNC_THREAD_LIMIT", "96"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Startup ──
    # Raise the sync-route concurrency cap before any traffic arrives. Must run
    # inside the event loop (it is — lifespan is async), and we only ever raise
    # the limit, never lower it below borrowed tokens. Single uvicorn worker is
    # deliberate: extra workers would each re-run this lifespan and duplicate the
    # radar jobs / BSD websocket below, double-firing alarm notifications.
    try:
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens = _SYNC_THREAD_LIMIT
        log.info("sync-route thread limiter raised to %d", _SYNC_THREAD_LIMIT)
    except Exception as lim_exc:
        log.warning("could not raise sync-route thread limiter: %s", lim_exc)

    _init_db()
    _migrate_team_ids_to_365()  # legacy ESPN/API-Basketball → 365scores
    _load_fired_alerts_from_db()   # hydrate dedup set before radar starts
    _purge_old_alarms()            # delete inactive alarms older than 7 days
    _cleanup_prefixed_alarms()     # expire pre-fix bsd-*/s365-* alarms
    clear_stale_state()
    _restart_alarm_threads_from_db()
    threading.Thread(target=update_all_rosters, daemon=True).start()
    # Pre-warm games cache so /games is ready immediately after startup
    threading.Thread(target=get_today_games, daemon=True).start()
    # Seed soccer team search index from standings so /search works on cold start
    threading.Thread(target=_seed_soccer_search_index, daemon=True).start()
    # Prime the /feed result cache around today so the first client after a
    # (re)deploy never hits a fully cold upstream cascade.
    threading.Thread(target=_prewarm_feed_cache, daemon=True).start()
    # Radar reads all users directly from DB — always start it
    threading.Thread(target=start_radar_job, daemon=True).start()
    # v3 soccer radar (event-driven). Wired with our existing FCM helper + dedup.
    try:
        import soccer_monitor
        from monitor import _send_push_notification
        soccer_monitor.wire(
            send_push=_send_push_notification,
            get_user_language=_get_user_language_from_db,
            fire_once=_fire_once,
            db_engine=engine,
            alarm_model=SoccerAlarmDB,
            comp_follow_model=SoccerFavoriteCompetitionDB,
            team_follow_model=SoccerFavoriteTeamDB,
            soccer_historic_model=SoccerHistoricPrefDB,
        )
        soccer_monitor.start_soccer_radar()
        log.info("soccer radar wired and started")
    except Exception as soccer_exc:
        log.warning("soccer radar not started: %s", soccer_exc)

    # BSD live WebSocket client — pushes per-event deltas instead of
    # forcing monitor.py to poll REST every few seconds. The client itself
    # exits cleanly if the BSD subscription isn't active; the sub-manager
    # below keeps the desired subscription set in sync with currently-
    # live BSD events for both soccer and basketball.
    try:
        import bsd_websocket
        bsd_websocket.client()  # starts the singleton
        threading.Thread(target=_bsd_ws_sub_manager_loop,
                         name="bsd-ws-sub-manager", daemon=True).start()
        log.info("BSD live WS client started")
    except Exception as ws_exc:
        log.warning("BSD WS not started: %s", ws_exc)

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

@app.get("/livez")
async def livez():
    """Ultra-light liveness probe. Async, so it runs directly on the event loop
    and never queues behind the sync-route threadpool — a sync-route burst can't
    make the app look dead. Point platform/uptime health checks here; use /health
    for the richer DB + soccer-readiness diagnostics."""
    return {"status": "ok"}


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
    # v3 soccer readiness: confirms the new modules and competition map loaded.
    soccer_status = {"ready": False, "competitions": 0, "bsd_token": False, "radar_running": False}
    try:
        from score365_soccer_adapter import _COMPETITION_MAP, PRIMARY_SOURCE
        soccer_status["competitions"] = len(_COMPETITION_MAP)
        soccer_status["ready"] = len(PRIMARY_SOURCE) >= 10
        soccer_status["bsd_token"] = bool(os.environ.get("BZZOIRO_API_TOKEN", "").strip())
        try:
            import soccer_monitor
            soccer_status["radar_running"] = soccer_monitor.is_radar_running()
        except Exception:
            pass
    except Exception as exc:
        soccer_status["error"] = str(exc)[:120]
    return {
        "status": "ok",
        "version": "v3-p3.0",
        "db": {"connected": db_ok, "detail": db_msg},
        "soccer": soccer_status,
    }


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
    league: Optional[str]   = None    # explicit from iOS; falls back to lookup
    notify_at_start: bool   = False
    game_date: Optional[str] = None   # "YYYYMMDD" for future-day alarms


@app.post("/start-alarm")
async def start_alarm(req: AlarmRequest):
    """Register game alarm in DB; _alarm_poller handles the monitoring."""
    if not req.push_token or not req.push_token.strip():
        raise HTTPException(status_code=400, detail="push_token is required.")
    save_push_token(req.push_token, req.sound_channel)

    # Resolve the league for this alarm. The poller needs it so it can fetch
    # games from the right competition — get_today_games() is NBA-only.
    league = _league_for_alarm(req.home_team_id or None, req.away_team_id or None, req.league)
    # Default game_date to "today" (Eastern). Without this, the poller can
    # never expire stale alarms whose game has already finished on a prior day.
    game_date = req.game_date or _eastern_today()

    # Translate bsd-* / s365-* canonical IDs to the bare 365scores numeric ID
    # that the alarm poller and boxscore fetcher expect.  The /games endpoint
    # now routes through basketball_source (returns prefixed IDs), while the
    # poller still fetches from score365_adapter (bare IDs).  Resolving here
    # keeps the poller, dedup logic, and boxscore path unchanged.
    monitor_game_id = _resolve_monitor_game_id(req.game_id, req.game_name, league, game_date)

    if engine is not None:
        try:
            with Session(engine) as session:
                # Dedup: replace any existing active alarm for same user+game.
                # Match on both the original (possibly prefixed) and the resolved
                # ID so re-setting an alarm always cancels the previous one even
                # if it was stored under a different ID format.
                session.query(Alarm).filter(
                    Alarm.user_token     == req.push_token,
                    Alarm.condition_type == "score_diff",
                    Alarm.is_active.is_(True),
                    or_(
                        Alarm.target_id == req.game_id,
                        Alarm.target_id == monitor_game_id,
                    ),
                ).update({"is_active": False}, synchronize_session=False)
                session.add(Alarm(
                    user_token       = req.push_token,
                    target_id        = monitor_game_id,
                    target_name      = req.game_name,
                    condition_type   = "score_diff",
                    threshold        = req.max_diff,
                    start_quarter    = req.start_quarter,
                    game_id          = monitor_game_id,
                    game_name        = req.game_name,
                    home_team_id     = req.home_team_id or None,
                    away_team_id     = req.away_team_id or None,
                    league           = league,
                    alert_type       = req.alert_type,
                    notify_at_start  = req.notify_at_start,
                    game_date        = game_date,
                ))
                session.commit()
        except Exception as exc:
            log.error("start_alarm DB write failed: %s", exc)
    log.info("ALARM REGISTERED | game=%s | diff<=%d Q%d+ | monitor_id=%s | ...%s",
             req.game_name, req.max_diff, req.start_quarter, monitor_game_id, req.push_token[-8:])
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
    home_team_id: str     = ""
    away_team_id: str     = ""
    league: Optional[str] = None
    game_date: Optional[str] = None   # "YYYYMMDD" for future-day alarms


@app.post("/start-player-alarm")
async def start_player_alarm(req: PlayerAlarmRequest):
    """Register player alarm in DB; _alarm_poller handles the monitoring."""
    if not req.push_token or not req.push_token.strip():
        raise HTTPException(status_code=400, detail="push_token is required.")
    save_push_token(req.push_token, req.sound_channel)

    league = _league_for_alarm(req.home_team_id or None, req.away_team_id or None, req.league)
    game_date = req.game_date or _eastern_today()

    # Resolve the game_id to a bare 365scores ID so the poller can find the
    # game in the 365scores scoreboard and fetch the correct boxscore for
    # player-stat lookups.  target_id stays as the player ID (365scores).
    monitor_game_id = _resolve_monitor_game_id(req.game_id, req.game_name, league, game_date)

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
                    game_id        = monitor_game_id,
                    game_name      = req.game_name,
                    league         = league,
                    alert_type     = req.alert_type,
                    game_date      = game_date,
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


@app.post("/screenshot-alert")
def screenshot_alert(token: str, title: str = "", body: str = "",
                     sound_channel: str = "classic_alarm", alert_type: str = "alarm"):
    """Fire a real, dramatic Clutch Alarm push to one device for App Store capture.
    Env-gated behind SCREENSHOT_DEMO (same flag as the staged matches)."""
    import demo_data
    if not demo_data.DEMO_ENABLED:
        raise HTTPException(status_code=403, detail="screenshot demo disabled")
    title = title or "🔥 Argentina 2-2 France"
    body = body or "88' · Drama 9.4 — it's going to the wire. Don't miss the finish!"
    _send_push_notification(push_token=token, title=title, body=body,
                            sound_channel=sound_channel, alert_type=alert_type)
    return {"sent": True, "title": title, "body": body}


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
    "FavoriteLeagues": [],
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
            leagues = session.query(FavoriteLeagueDB).filter_by(user_token=user_token).all()
            pref    = session.get(UserPreferenceDB, user_token)

            fav_players = {
                p.target_id: {
                    "name":          p.target_name or "",
                    "team_id":       p.team_id,
                    "team_ids":      ([p.team_id] if p.team_id else []) + (p.alt_team_ids.split(",") if getattr(p, "alt_team_ids", None) else []),
                    "league":        (p.league or "nba"),
                    "total_pts":     p.total_pts,
                    "early_pts_q":   p.early_pts_q,
                    "early_pts":     p.early_pts,
                    "td_watch":       bool(p.td_watch),
                    "alert_type":     p.alert_type or "silent",
                    "broadcast_only": getattr(p, 'broadcast_only', False),
                    "paused":         getattr(p, 'paused', False),
                    "skip_euroleague_evening": getattr(p, 'skip_euroleague_evening', False),
                }
                for p in players
            }
            fav_teams = [
                _with_team_name_he({
                    "team_id":        t.team_id,
                    "team_name":      t.team_name or "",
                    "league":         (t.league or "nba"),
                    "max_diff":       t.score_gap or 5,
                    "start_quarter":  t.start_quarter or 4,
                    "alert_type":     t.alert_type or "silent",
                    "broadcast_only":  getattr(t, 'broadcast_only', False),
                    "notify_at_start": getattr(t, 'notify_at_start', False),
                    "paused":          getattr(t, 'paused', False),
                    "skip_euroleague_evening": getattr(t, 'skip_euroleague_evening', False),
                    "team_ids":        (t.alt_team_ids.split(",") if t.alt_team_ids else [t.team_id]),
                })
                for t in teams
            ]
            fav_leagues = [
                {
                    "league":          lg.league,
                    "max_diff":        lg.score_gap or 5,
                    "start_quarter":   lg.start_quarter or 4,
                    "alert_type":      lg.alert_type or "silent",
                    "notify_at_start": getattr(lg, 'notify_at_start', False),
                    "paused":          getattr(lg, 'paused', False),
                }
                for lg in leagues
            ]
            radar_cfg = _SETTINGS_DEFAULTS["GlobalGameRadar"].copy()
            radar_cfg["leagues"] = ["nba"]
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
                    "leagues":       _split_leagues(pref.radar_leagues),
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
            soc_hist_cfg = {"enabled": False, "alert_type": "silent", "categories": {}}
            sh = session.get(SoccerHistoricPrefDB, user_token)
            if sh:
                soc_hist_cfg = {
                    "enabled":    sh.enabled,
                    "alert_type": sh.alert_type,
                    "categories": sh.categories or {},
                }
        return {
            "FavoritePlayers": fav_players,
            "FavoriteTeams":   fav_teams,
            "FavoriteLeagues": fav_leagues,
            "GlobalGameRadar": radar_cfg,
            "HistoricalMode":  hist_cfg,
            "soccer_historic": soc_hist_cfg,
        }
    except Exception as exc:
        log.error("DB: /settings read error – %s", exc)
        return dict(_SETTINGS_DEFAULTS)


_VALID_LEAGUES = {"nba", "euroleague", "eurocup", "bcl", "winner_league"}


def _split_leagues(raw: Optional[str]) -> list[str]:
    """Parse the comma-separated user_preferences.radar_leagues column."""
    if not raw:
        return ["nba"]
    parts = [p.strip() for p in raw.split(",") if p.strip() in _VALID_LEAGUES]
    return parts or ["nba"]


def _join_leagues(leagues: Optional[list[str]]) -> str:
    """Serialize a leagues list to the comma-separated DB form, with validation."""
    if not leagues:
        return "nba"
    cleaned = [l for l in leagues if l in _VALID_LEAGUES]
    return ",".join(cleaned) if cleaned else "nba"


class GlobalRadarSettings(BaseModel):
    user_token:    str = ""
    enabled:       bool
    max_diff:      int = 5
    start_quarter: int = 4
    alert_type:    str = "silent"
    leagues:       Optional[list[str]] = None


@app.post("/settings/global-radar")
def update_global_radar(req: GlobalRadarSettings):
    leagues_str = _join_leagues(req.leagues)
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                pref = session.get(UserPreferenceDB, req.user_token)
                if pref:
                    pref.radar_enabled = req.enabled
                    pref.radar_gap     = req.max_diff
                    pref.radar_start_q = req.start_quarter
                    pref.alert_type    = req.alert_type
                    pref.radar_leagues = leagues_str
                else:
                    session.add(UserPreferenceDB(
                        user_token=req.user_token,
                        radar_enabled=req.enabled, radar_gap=req.max_diff,
                        radar_start_q=req.start_quarter, alert_type=req.alert_type,
                        radar_leagues=leagues_str,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: update_global_radar failed – %s", exc)
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "GlobalGameRadar": {
        "enabled": req.enabled, "max_diff": req.max_diff,
        "start_quarter": req.start_quarter, "alert_type": req.alert_type,
        "leagues": _split_leagues(leagues_str),
    }}


class FavoriteTeam(BaseModel):
    user_token:      str = ""
    team_id:         str
    team_name:       str
    league:          str = "nba"
    max_diff:        int = 5
    start_quarter:   int = 4
    alert_type:      str = "silent"
    broadcast_only:  bool = False
    free_only:       bool = False
    notify_at_start: bool = False
    skip_euroleague_evening: bool = False
    team_ids:        list[str] = []


@app.post("/settings/teams")
def upsert_team(req: FavoriteTeam):
    league = req.league if req.league in _VALID_LEAGUES else "nba"
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoriteTeamDB).filter_by(
                    user_token=req.user_token, team_id=req.team_id
                ).first()
                team_ids = req.team_ids or []
                alt_team_ids_val = ",".join(str(i) for i in team_ids) if team_ids else None
                if existing:
                    existing.team_name      = req.team_name
                    existing.league         = league
                    existing.score_gap      = req.max_diff
                    existing.start_quarter  = req.start_quarter
                    existing.alert_type     = req.alert_type
                    existing.broadcast_only  = getattr(req, 'broadcast_only', False)
                    existing.notify_at_start = getattr(req, 'notify_at_start', False)
                    existing.skip_euroleague_evening = getattr(req, 'skip_euroleague_evening', False)
                    existing.alt_team_ids   = alt_team_ids_val
                else:
                    session.add(FavoriteTeamDB(
                        user_token=req.user_token, team_id=req.team_id,
                        team_name=req.team_name, league=league,
                        score_gap=req.max_diff,
                        start_quarter=req.start_quarter, alert_type=req.alert_type,
                        broadcast_only=getattr(req, 'broadcast_only', False),
                        notify_at_start=getattr(req, 'notify_at_start', False),
                        skip_euroleague_evening=getattr(req, 'skip_euroleague_evening', False),
                        alt_team_ids=alt_team_ids_val,
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


class FavoriteLeague(BaseModel):
    """Follow a basketball league — same criteria shape as FavoriteTeam, applied
    to every game in the league."""
    user_token:      str = ""
    league:          str
    max_diff:        int = 5
    start_quarter:   int = 4
    alert_type:      str = "silent"
    notify_at_start: bool = False


@app.post("/settings/leagues")
def upsert_league(req: FavoriteLeague):
    league = req.league if req.league in _VALID_LEAGUES else None
    if league is None:
        raise HTTPException(status_code=400, detail=f"unknown basketball league '{req.league}'")
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoriteLeagueDB).filter_by(
                    user_token=req.user_token, league=league
                ).first()
                if existing:
                    existing.score_gap       = req.max_diff
                    existing.start_quarter   = req.start_quarter
                    existing.alert_type      = req.alert_type
                    existing.notify_at_start = req.notify_at_start
                    existing.paused          = False
                else:
                    session.add(FavoriteLeagueDB(
                        user_token=req.user_token, league=league,
                        score_gap=req.max_diff, start_quarter=req.start_quarter,
                        alert_type=req.alert_type, notify_at_start=req.notify_at_start,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: upsert_league failed – %s", exc)
    _maybe_start_radar()
    return {"status": "updated"}


@app.delete("/settings/leagues/{league}")
def remove_league(league: str, user_token: str = ""):
    if engine is not None and user_token:
        try:
            with Session(engine) as session:
                row = session.query(FavoriteLeagueDB).filter_by(
                    user_token=user_token, league=league
                ).first()
                if row:
                    session.delete(row)
                    session.commit()
        except Exception as exc:
            log.error("DB: remove_league failed – %s", exc)
    return {"status": "removed"}


class FavoritePlayer(BaseModel):
    user_token:     str = ""
    player_id:      str
    name:           str
    team_id:        Optional[str]  = None
    team_ids:       list[str] = []
    league:         str = "nba"
    total_pts:      Optional[int]  = None
    early_pts_q:    Optional[int]  = None
    early_pts:      Optional[int]  = None
    td_watch:       bool           = False
    alert_type:     str            = "silent"
    broadcast_only: bool           = False
    skip_euroleague_evening: bool  = False


@app.post("/settings/players")
def upsert_player(req: FavoritePlayer):
    league = req.league if req.league in _VALID_LEAGUES else "nba"
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                existing = session.query(FavoritePlayerDB).filter_by(
                    user_token=req.user_token, target_id=req.player_id
                ).first()
                if existing:
                    alt_ids = [tid for tid in dict.fromkeys(req.team_ids) if tid and tid != req.team_id]
                    existing.target_name    = req.name
                    existing.team_id        = req.team_id
                    existing.alt_team_ids   = ",".join(alt_ids) or None
                    existing.league         = league
                    existing.total_pts      = req.total_pts
                    existing.early_pts_q    = req.early_pts_q
                    existing.early_pts      = req.early_pts
                    existing.td_watch       = req.td_watch
                    existing.alert_type     = req.alert_type
                    existing.broadcast_only = req.broadcast_only
                    existing.skip_euroleague_evening = req.skip_euroleague_evening
                else:
                    alt_ids = [tid for tid in dict.fromkeys(req.team_ids) if tid and tid != req.team_id]
                    session.add(FavoritePlayerDB(
                        user_token=req.user_token, target_id=req.player_id,
                        target_name=req.name, team_id=req.team_id, 
                        alt_team_ids=",".join(alt_ids) or None, league=league,
                        total_pts=req.total_pts,
                        early_pts_q=req.early_pts_q, early_pts=req.early_pts,
                        td_watch=req.td_watch, alert_type=req.alert_type,
                        broadcast_only=req.broadcast_only,
                        skip_euroleague_evening=req.skip_euroleague_evening,
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


class SoccerHistoricSettings(BaseModel):
    user_token: str = ""
    enabled:    bool = False
    alert_type: str = "silent"
    categories: dict[str, bool] = {}


@app.post("/settings/soccer-historic")
def update_soccer_historic(req: SoccerHistoricSettings):
    cats = {str(k): bool(v) for k, v in (req.categories or {}).items()}
    if engine is not None and req.user_token:
        try:
            with Session(engine) as session:
                row = session.get(SoccerHistoricPrefDB, req.user_token)
                if row:
                    row.enabled    = req.enabled
                    row.alert_type = req.alert_type
                    row.categories = cats
                    row.updated_at = datetime.now(timezone.utc)
                else:
                    session.add(SoccerHistoricPrefDB(
                        user_token=req.user_token, enabled=req.enabled,
                        alert_type=req.alert_type, categories=cats,
                    ))
                session.commit()
        except Exception as exc:
            log.error("DB: update_soccer_historic failed – %s", exc)
    return {"status": "updated", "soccer_historic": {
        "enabled": req.enabled, "alert_type": req.alert_type, "categories": cats,
    }}


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
def games(
    date: Optional[str] = None,
    league: str = "nba",
    tz: Optional[str] = None,
):
    """Return games for a given date (YYYYMMDD) and league. Defaults to today NBA.

    Routes through the basketball_source cascade (BSD primary, 365scores
    fallback) for leagues we've migrated. Falls back to the legacy
    `get_games_for_date` path for anything not yet routed (e.g. ESPN
    direct calls used by monitor.py).
    """
    import basketball_source as _bb
    iso_date = _yyyymmdd_to_iso(date)
    try:
        games = _bb.get_basketball_games(league, date_iso=iso_date, tz=tz)
        return {"games": games, "league": league, "date": iso_date, "tz": tz}
    except Exception as exc:
        # Defensive fallback so the schedule never goes black during
        # rollout. Removable once BSD basketball is proven in prod.
        log.warning("basketball_source failed for %s/%s: %s — falling back to legacy",
                       league, iso_date, exc)
        return {"games": get_games_for_date(date, league=league)}


# ── Feed batch (one-shot fan-out + empty-result memoization) ────────────────
#
# The app's schedule feed asks for ~50 days × ~15 leagues. As individual
# requests that's hundreds of round-trips on a cold launch, and each soccer
# miss re-cascades 2-3 upstream sources — enough to saturate a small instance
# (observed: health checks timing out under the stampede). /feed collapses the
# whole grid into ONE request, fans it out server-side (next to the upstreams),
# and remembers empty (league, date) results so off-season dates stop
# re-cascading every load.

_empty_feed_cache: dict[str, float] = {}   # "sport|league|iso" -> monotonic expiry

# ONE shared, bounded pool for all /feed fan-out. A per-request pool meant
# ~6 concurrent /feed calls (the app fans out per-date) each spawned 16 worker
# threads = ~96 upstream calls in flight, which exhausted the small dev box and
# made even /health time out. A single capped pool bounds total upstream
# concurrency no matter how many feed requests land at once.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
# Bounded a bit lower than before: combined with the result cache below, most
# feed requests are cache hits and never touch the pool, so a smaller pool keeps
# the (small) box from thrashing when a cold scroll-back lands.
_FEED_POOL = _ThreadPoolExecutor(max_workers=10, thread_name_prefix="feed")

# ONE shared, bounded pool for watch-score computation. Each compute makes 2-3
# BSD HTTP calls (sometimes a 20s play-by-play fetch), so a per-request pool meant
# a first run asking for many dates spawned hundreds of threads at once and OOM'd
# the box. A single small pool caps total upstream concurrency no matter how many
# /watch-scores requests land together.
_WATCH_POOL = _ThreadPoolExecutor(max_workers=4, thread_name_prefix="watch")

# ONE shared, bounded pool for the basketball summary fan-out (detail + team-stats
# + box-score, 3 calls). Was a fresh per-request pool, so concurrent summary opens
# multiplied threads on top of the request pool — same explosion the pools above
# fix. Tasks are leaf HTTP calls (no re-submission), so a bounded shared pool is
# deadlock-safe; results are gathered inside the block, so it's reused not closed.
_SUMMARY_POOL = _ThreadPoolExecutor(max_workers=6, thread_name_prefix="bbsummary")
# Only auto-compute scores for the last few days; older history is read from the
# DB if already persisted, else computed lazily per match (so a cold first run
# can't trigger hundreds of synchronous score computations).
_WATCH_AUTO_DAYS = 3


def _watch_date_is_recent(game_date: str) -> bool:
    try:
        from monitor import _eastern_today
        from datetime import date as _date
        today = _eastern_today()
        td = _date(int(today[0:4]), int(today[4:6]), int(today[6:8]))
        cutoff = (td - timedelta(days=_WATCH_AUTO_DAYS)).strftime("%Y%m%d")
        return game_date >= cutoff
    except Exception:
        return True

# Result cache for NON-empty feed days. Past dates are immutable (final scores)
# so they're cached for hours — this is the fix for "loading old games" being
# slow/crashy: scrolling back used to re-fetch upstream AND re-normalize every
# single request. Keyed "bb|league|iso" / "sc|league|iso" -> (expiry, payload).
_feed_cache: dict[str, tuple[float, list]] = {}
_FEED_CACHE_MAX = 6000


def _feed_empty_ttl(iso_date: str) -> float:
    """Past dates never gain games retroactively (cache long); today/future can
    still pick up newly scheduled fixtures (cache short)."""
    try:
        from monitor import _eastern_today
        today_iso = _yyyymmdd_to_iso(_eastern_today()) or ""
        if iso_date and iso_date < today_iso:
            return 24 * 3600.0
    except Exception:
        pass
    return 900.0                            # 15 min


def _feed_today_iso(tz: Optional[str]) -> str:
    """Today's date in the REQUEST tz (falls back to US-Eastern, then UTC)."""
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz)).date().isoformat()
        except Exception:
            pass
    try:
        from monitor import _eastern_today
        return _yyyymmdd_to_iso(_eastern_today()) or ""
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _feed_ttl(iso_date: str, tz: Optional[str] = None) -> float:
    """TTL for a NON-empty feed day. Past = long (final scores don't change),
    today = short (live scores), future = medium (fixtures stable).

    "Today" is resolved in the REQUEST tz, not US-Eastern. Otherwise a match near
    the UTC boundary (e.g. a 00:00 UTC kickoff = today in Israel but yesterday in
    Eastern) reads as a FUTURE fixture, gets a multi-hour cache, and a feed cached
    before kickoff keeps showing it as scheduled (no score) for hours."""
    try:
        today_iso = _feed_today_iso(tz)
        if iso_date and today_iso and iso_date < today_iso:
            return 12 * 3600.0
        if iso_date == today_iso:
            return 75.0
    except Exception:
        pass
    return 6 * 3600.0


def _feed_cache_get(key: str) -> Optional[list]:
    hit = _feed_cache.get(key)
    if hit is not None and time.monotonic() < hit[0]:
        return hit[1]
    return None


def _feed_cache_put(key: str, payload: list, ttl: float) -> None:
    if len(_feed_cache) >= _FEED_CACHE_MAX:
        # Evict the soonest-to-expire quarter to bound memory.
        victims = sorted(_feed_cache, key=lambda k: _feed_cache[k][0])[: _FEED_CACHE_MAX // 4 + 1]
        for k in victims:
            _feed_cache.pop(k, None)
    _feed_cache[key] = (time.monotonic() + ttl, payload)


def _feed_basketball(league: str, iso_date: str, tz: Optional[str]) -> list[dict]:
    key = f"bb|{league}|{iso_date}"
    exp = _empty_feed_cache.get(key)
    if exp is not None and time.monotonic() < exp:
        return []
    cached = _feed_cache_get(key)
    if cached is not None:
        return cached
    try:
        import basketball_source as _bb
        games, trustworthy = _bb.get_basketball_games_status(league, date_iso=iso_date, tz=tz)
    except Exception as exc:
        log.warning("feed: basketball %s/%s failed: %s", league, iso_date, exc)
        return []
    if not games:
        # Only cache "no games" when a source confirmed it (see _feed_soccer):
        # an all-sources-errored empty must not stick for 15 minutes.
        if trustworthy:
            _empty_feed_cache[key] = time.monotonic() + _feed_empty_ttl(iso_date)
    else:
        # Live day → short TTL regardless of date classification (see _feed_soccer):
        # an in-progress game read as a future fixture would otherwise freeze for hours.
        has_live = any((g.get("status") or {}).get("type", {}).get("state") == "in" for g in games)
        _feed_cache_put(key, games, 25.0 if has_live else _feed_ttl(iso_date, tz))
    return games


def _feed_soccer(league: str, iso_date: str, tz: Optional[str]) -> list[dict]:
    import demo_data
    if demo_data.DEMO_ENABLED and league == "world_cup":
        return demo_data.games()    # screenshot session: always show the staged WC matches
    key = f"sc|{league}|{iso_date}"
    exp = _empty_feed_cache.get(key)
    if exp is not None and time.monotonic() < exp:
        return []
    cached = _feed_cache_get(key)
    if cached is not None:
        return cached
    try:
        import soccer_source as _ss
        matches, trustworthy = _ss.get_soccer_matches_status(league, date_iso=iso_date, tz=tz)
    except Exception as exc:
        log.warning("feed: soccer %s/%s failed: %s", league, iso_date, exc)
        return []
    if not matches:
        # Only memoize "no games" when a source actually CONFIRMED it. An
        # untrustworthy empty (every upstream errored — e.g. a transient 429)
        # must not be cached, or one hiccup hides real fixtures for 15 minutes
        # (this is what made World Cup days vanish from /feed while /soccer/games
        # — which never caches — kept serving them).
        if trustworthy:
            _empty_feed_cache[key] = time.monotonic() + _feed_empty_ttl(iso_date)
    else:
        # A day holding a LIVE match must stay fresh regardless of how _feed_ttl
        # classifies the calendar date. _feed_ttl keys "today" off US-Eastern, but
        # the feed is requested in the user's tz — a match near the UTC boundary
        # (e.g. a 22:00 UTC friendly = next-day in Israel, same-day in Eastern)
        # reads as a FUTURE fixture and otherwise caches for HOURS, freezing the
        # live minute/score.
        live = {"live", "ht", "et", "pens"}
        has_live = any((m.get("status") or "") in live for m in matches)
        _feed_cache_put(key, matches, 25.0 if has_live else _feed_ttl(iso_date, tz))
    return matches


def _feed_date_range(frm: str, to: str) -> list[str]:
    """Inclusive YYYYMMDD list between frm and to, capped to 120 days.

    Accepts both compact `YYYYMMDD` (what the app sends) and dashed `YYYY-MM-DD`
    so a caller passing ISO dates isn't silently coerced to "today" — that
    foot-gun made dashed-date probes look like an empty schedule.
    """
    from datetime import date as _date
    def _parse(s: str):
        s = (s or "").strip().replace("-", "")   # tolerate YYYY-MM-DD
        return _date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    try:
        d0, d1 = _parse(frm), _parse(to)
    except Exception:
        return []
    if d1 < d0:
        d0, d1 = d1, d0
    out: list[str] = []
    cur = d0
    while cur <= d1 and len(out) < 120:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


# Pre-warm window: today-2 through +3 days. Covers the client's first-run
# prefetch range (today +/-2) so even the very first user after a deploy gets a
# fully warm window. Future days cache for 6h and past days 12-24h, so the warm
# survives well past boot; today caches ~75s but a fresh-deploy user usually
# lands within that. tz is the primary audience (and the 365 client's default) —
# the _feed_cache is tz-keyless, so this just makes the server, not the first
# random client, decide day-grouping for the TTL (a pre-existing tradeoff).
_PREWARM_OFFSETS = range(-2, 4)
_PREWARM_TZ = "Asia/Jerusalem"


def _prewarm_feed_cache() -> None:
    """Prime `_feed_cache` for the popular leagues around today so the first
    client after a (re)deploy paints from cache instead of triggering a cold
    upstream cascade. Runs once at startup in a daemon thread; uses the shared
    `_FEED_POOL` so it can't explode threads, and tolerates per-item failures.
    A brief settle delay lets the monitors/alarm restore finish booting first.
    """
    try:
        time.sleep(5)   # let boot settle before the warm burst
        from datetime import date as _d
        from monitor import _eastern_today
        from score365_soccer_adapter import DEFAULT_LEAGUE_ORDER

        t = _eastern_today()
        base = _d(int(t[0:4]), int(t[4:6]), int(t[6:8]))
        dates = [(base + timedelta(days=off)).strftime("%Y-%m-%d") for off in _PREWARM_OFFSETS]
        # All basketball leagues we surface are 365-sourced.
        bb_leagues = ["nba", "euroleague", "eurocup", "bcl", "winner_league"]
        sc_leagues = list(DEFAULT_LEAGUE_ORDER)

        work: list[tuple[str, str, str]] = []
        for iso in dates:
            work += [("bb", lg, iso) for lg in bb_leagues]
            work += [("sc", lg, iso) for lg in sc_leagues]

        def _warm(item: tuple[str, str, str]) -> None:
            kind, lg, iso = item
            try:
                if kind == "bb":
                    _feed_basketball(lg, iso, _PREWARM_TZ)
                else:
                    _feed_soccer(lg, iso, _PREWARM_TZ)
            except Exception:
                pass

        list(_FEED_POOL.map(_warm, work))
        log.info("feed cache prewarmed: %d (league,date) pairs over %d days", len(work), len(dates))
    except Exception as exc:
        log.warning("feed cache prewarm skipped: %s", exc)


@app.get("/feed")
def feed(
    frm: str = Query("", alias="from"),
    to: str = "",
    basketball: str = "",
    football: str = "",
    tz: Optional[str] = None,
):
    """One-shot schedule fan-out across many leagues and a date range.

    Query params:
      from, to     YYYYMMDD inclusive range (defaults to today only).
      basketball   comma-separated basketball league slugs (client's catalog).
      football     comma-separated soccer league slugs.
      tz           IANA tz to anchor the calendar-day filter.

    Leagues come from the client because LeagueCatalog is the source of truth
    and the server's basketball set differs. Returns a flat item list so the
    app maps it 1:1 onto its progressive accumulator; the per-item games/matches
    payloads are byte-for-byte what /games and /soccer/games return.
    """
    bb_leagues = [s.strip() for s in basketball.split(",") if s.strip()]
    sc_leagues = [s.strip() for s in football.split(",") if s.strip()]
    dates = _feed_date_range(frm, to)
    if not dates:
        from monitor import _eastern_today
        dates = [_eastern_today()]

    work: list[tuple[str, str, str, str]] = []   # (sport, league, yyyymmdd, iso)
    for d in dates:
        iso = _yyyymmdd_to_iso(d) or d
        for lg in bb_leagues:
            work.append(("basketball", lg, d, iso))
        for lg in sc_leagues:
            work.append(("football", lg, d, iso))

    def _one(item: tuple[str, str, str, str]):
        sport, lg, d, iso = item
        if sport == "basketball":
            return (sport, lg, d, "games", _feed_basketball(lg, iso, tz))
        return (sport, lg, d, "matches", _feed_soccer(lg, iso, tz))

    items: list[dict] = []
    if work:
        # Submit to the shared bounded pool (NOT a fresh per-request pool) so
        # concurrent /feed calls can't explode the thread count.
        for sport, lg, d, payload_key, payload in _FEED_POOL.map(_one, work):
            if not payload:
                continue
            items.append({"sport": sport, "league": lg, "date": d, payload_key: payload})

    return {"items": items, "tz": tz}


@app.get("/basketball/event/{canonical_id}")
def basketball_event_detail(canonical_id: str):
    """Single basketball event with score header + per-period scoring +
    pre-match prediction. BSD-only — 365 fallback IDs return 404."""
    import basketball_source as _bb
    detail = _bb.get_basketball_event_detail(canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="event not found or source has no detail endpoint")
    return detail


@app.get("/basketball/event/{canonical_id}/team-stats")
def basketball_event_team_stats(canonical_id: str):
    """Quarter-by-quarter team stats. BSD-only."""
    import basketball_source as _bb
    stats = _bb.get_basketball_team_stats(canonical_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="team stats unavailable for this source")
    return stats


@app.get("/basketball/event/{canonical_id}/box-score")
def basketball_event_box_score(canonical_id: str):
    """Per-player stats for both teams. BSD-only."""
    import basketball_source as _bb
    box = _bb.get_basketball_box_score(canonical_id)
    if box is None:
        raise HTTPException(status_code=404, detail="box score unavailable for this source")
    return box


@app.get("/basketball/event/{canonical_id}/summary")
def basketball_event_summary(canonical_id: str):
    """Aggregated payload for the iOS BasketballSummaryView.

    Fans out detail + team-stats + box-score in parallel and returns one
    merged object. Mirrors `/soccer/match/{id}` so the iOS layer can fetch
    everything in a single round-trip.
    """
    import basketball_source as _bb

    with nullcontext(_SUMMARY_POOL) as pool:
        f_detail = pool.submit(_bb.get_basketball_event_detail, canonical_id)
        f_stats  = pool.submit(_bb.get_basketball_team_stats, canonical_id)
        f_box    = pool.submit(_bb.get_basketball_box_score, canonical_id)
        # Wrap every result() call so upstream exceptions (auth failures,
        # network timeouts, scraper errors) become clean 404s instead of 500s.
        detail = None
        try:
            detail = f_detail.result()
        except Exception as exc:
            log.warning("basketball detail failed for %s: %s", canonical_id, exc)
        if detail is None:
            raise HTTPException(status_code=404, detail="event not found")
        team_stats = None
        box_score = None
        try:
            team_stats = f_stats.result()
        except Exception as exc:
            log.warning("team-stats failed for %s: %s", canonical_id, exc)
        try:
            box_score = f_box.result()
        except Exception as exc:
            log.warning("box-score failed for %s: %s", canonical_id, exc)

    # Stamp 365scores logo URLs onto the team objects in the detail so
    # the iOS score header pulls from the same source as the schedule
    # rows. BSD's team images are inconsistent (many NBA crests are 3 KB
    # placeholders); 365 has a clean, uniform CDN across all leagues we
    # cover.
    if isinstance(detail, dict):
        import bsd_basketball as _bsd
        import basketball_source as _bb
        slug_by_id = {v: k for k, v in _bsd.BSD_BASKETBALL_LEAGUE_IDS.items()}
        league_id  = (detail.get("league") or {}).get("id")
        slug       = slug_by_id.get(league_id)
        if slug:
            for key in ("home_team", "away_team"):
                team = detail.get(key)
                if isinstance(team, dict):
                    team["logo"] = _bb._basketball_logo_url(
                        slug, team.get("name"), team.get("id")
                    )

    # Normalize BSD player objects to a predictable schema so the mobile
    # app doesn't need to guess BSD's internal field names.
    # Use a shallow copy so we never mutate the object stored in the BSD cache.
    if isinstance(box_score, dict):
        box_score = dict(box_score)
        for side in ("home_box", "away_box"):
            raw_list = box_score.get(side)
            if isinstance(raw_list, list):
                normalized = []
                for player in raw_list:
                    try:
                        normalized.append(_normalize_bsd_player(player))
                    except Exception as exc:
                        log.warning("bsd player normalize error: %s | raw=%s", exc, str(player)[:200])
                        normalized.append(player)
                box_score[side] = normalized

    return {
        "id":         canonical_id,
        "detail":     detail,
        "team_stats": team_stats,
        "box_score":  box_score,
    }


def _normalize_bsd_player(p: dict) -> dict:
    """Map a raw BSD basketball box-score player to a stable schema.

    BSD field names (confirmed from iOS BasketballBoxPlayer CodingKeys):
      name, player_id, jersey_number, position, is_starter, minutes (Double),
      points, rebounds, assists, steals, blocks, turnovers, personal_fouls.
    """
    raw_mins = p.get("minutes") or 0
    try:
        mins_int = int(float(raw_mins))
    except (ValueError, TypeError):
        mins_int = 0

    return {
        "player_id":   str(p.get("player_id") or ""),
        "player_name": p.get("name") or "",
        "jersey":      str(p.get("jersey_number") or ""),
        "position":    str(p.get("position") or ""),
        "starter":     bool(p.get("is_starter")),
        "minutes":     str(mins_int),
        "PTS": p.get("points"),
        "REB": p.get("rebounds"),
        "AST": p.get("assists"),
        "STL": p.get("steals"),
        "BLK": p.get("blocks"),
        "TO":  p.get("turnovers"),
        "PF":  p.get("personal_fouls"),
        # Shooting splits + plus/minus + advanced stats. Both the BSD raw box
        # score and the 365 normalizer (_s365_box_player) emit these exact
        # snake_case keys, so passing them straight through fixes FG / 3P / +/-
        # (which were showing "-") for BOTH sources.
        "field_goals_made":         p.get("field_goals_made"),
        "field_goals_attempted":    p.get("field_goals_attempted"),
        "three_pointers_made":      p.get("three_pointers_made"),
        "three_pointers_attempted": p.get("three_pointers_attempted"),
        "free_throws_made":         p.get("free_throws_made"),
        "free_throws_attempted":    p.get("free_throws_attempted"),
        "plus_minus":               p.get("plus_minus"),
        "offensive_rebounds":       p.get("offensive_rebounds"),
        "defensive_rebounds":       p.get("defensive_rebounds"),
        "field_goal_pct":           p.get("field_goal_pct"),
        "three_point_pct":          p.get("three_point_pct"),
        "true_shooting_pct":        p.get("true_shooting_pct"),
        "effective_fg_pct":         p.get("effective_fg_pct"),
        "usage_pct":                p.get("usage_pct"),
        "rating":                   p.get("rating"),
        # 365 headshot stamped onto the player by get_basketball_box_score —
        # keep it through normalization so the box score shows player photos.
        "photo": p.get("photo"),
    }


@app.get("/basketball/player/{player_id}")
def basketball_player(player_id: str):
    """Normalized basketball player detail (365scores bio). All basketball is
    365-sourced now; 365's API exposes athlete bio but no per-player season
    averages, so only bio fields populate."""
    import basketball_source as bbsrc
    import player_source as ps
    raw = bbsrc.s365_player_detail(player_id)
    if not raw:
        raise HTTPException(status_code=404, detail="player not found")
    detail = ps.normalize_basketball_detail(raw, player_id=player_id)
    # Prefer the 365 athlete headshot over the default (BSD) photo URL.
    if raw.get("photo_365"):
        detail["photo"] = raw["photo_365"]
    return detail


@app.get("/basketball/player/{player_id}/games")
def basketball_player_games(player_id: str, limit: int = 10):
    """Recent basketball box-score lines. 365scores' public API does not expose
    a per-player game log, so this is empty (basketball moved off BSD)."""
    return {"games": []}


# ── Soccer endpoints (v3) ────────────────────────────────────────────────────


def _yyyymmdd_to_iso(s: str | None) -> str | None:
    """'20260517' → '2026-05-17'. Pass-through for already-ISO strings."""
    if not s:
        return None
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


@app.get("/soccer/games")
def soccer_games(
    date: Optional[str] = None,
    league: str = "premier_league",
    tz: Optional[str] = None,
):
    """Return soccer matches for a given date (YYYYMMDD or YYYY-MM-DD) and league.
    `league` is a slug from competition_map.json (premier_league, ucl, world_cup, etc.).
    `tz` is an IANA timezone identifier (e.g. "Asia/Jerusalem") used to anchor
    the calendar-day filter; defaults to UTC. iOS should send the device's
    current timezone so "today" matches the user's local notion of today.
    """
    import soccer_source as _ss  # late import to avoid heavy startup if soccer deps missing
    iso_date = _yyyymmdd_to_iso(date)
    matches = _ss.get_soccer_matches(league, date_iso=iso_date, tz=tz)
    import demo_data
    if demo_data.DEMO_ENABLED and league == "world_cup":
        matches = demo_data.games() + matches
    return {"matches": matches, "league": league, "date": iso_date, "tz": tz}


@app.get("/soccer/match/{canonical_id}")
def soccer_match_detail(canonical_id: str):
    """Full match payload including xG, momentum, shotmap, incidents.
    `canonical_id` is the source-tagged id ('bsd-372' or 's365-4702049')
    returned by /soccer/games.
    """
    import soccer_source as _ss
    import i18n as _i18n
    import demo_data
    if demo_data.is_demo_id(canonical_id):
        return demo_data.detail(canonical_id)
    detail = _ss.get_soccer_match_detail(canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="match not found")
    rh = _i18n.t_round_name(detail.get("round_name"), "he")
    if rh and rh != detail.get("round_name"):
        detail["round_name_he"] = rh
    return detail


@app.get("/soccer/match/{canonical_id}/prediction")
def soccer_match_prediction(canonical_id: str):
    """Pre-match prediction for the alarm sheet: win probabilities, the clear
    underdog if any (Sensation Watch), and a heuristic 'alert chance' for the
    Drama Radar slider. BSD-only — degrades to has_prediction=false for s365-*
    ids or when no prediction exists, so the client just hides that UI.
    """
    import soccer_source as _ss
    import drama_engine as _de

    empty = {
        "match_id": canonical_id, "has_prediction": False,
        "win_prob": None, "underdog": None, "favorite": None,
        "has_clear_underdog": False, "alert_chance": None, "stakes": None,
    }
    detail = _ss.get_soccer_match_detail(canonical_id)
    if detail is None:
        return empty

    stakes = _de._stakes_multiplier({
        "competition": detail.get("competition"),
        "round_name": detail.get("round_name"),
        "is_derby": bool(detail.get("is_derby")),
    })
    try:
        source, native = _ss.parse_canonical_id(canonical_id)
        eid = int(native) if source == "bsd" else None
    except (ValueError, TypeError):
        eid = None
    pm = _ss.pre_match_win_prob(eid) if eid is not None else None
    if not pm:
        return {**empty, "stakes": round(stakes, 3)}

    p_home, p_draw, p_away = pm
    # Heuristic alert chance (NOT a calibrated probability): a tight 1X2 market
    # in a high-stakes, draw-prone match is more likely to produce drama.
    closeness = max(0.0, 1.0 - abs(p_home - p_away))
    raw = (0.55 * closeness
           + 0.25 * min(1.0, max(0.0, (stakes - 0.85) / 1.3))
           + 0.20 * min(1.0, p_draw / 0.30))
    chance = max(0.0, min(1.0, raw))
    band = "high" if chance >= 0.66 else ("medium" if chance >= 0.33 else "low")

    underdog_is_home = p_home < p_away
    home = detail.get("home") or {}
    away = detail.get("away") or {}
    fav_side = home if not underdog_is_home else away
    dog = _ss.resolve_underdog(canonical_id, detail=detail, pm=pm)
    return {
        "match_id": canonical_id,
        "has_prediction": True,
        "win_prob": {"home": round(p_home, 4), "draw": round(p_draw, 4), "away": round(p_away, 4)},
        "underdog": ({
            "is_home": dog["underdog_is_home"],
            "team_id": dog["underdog_team_id"],
            "team_name": dog["underdog_name"],
            "team_name_he": team_names_he.hebrew_name(dog["underdog_name"]),
            "win_prob": dog["underdog_win_prob"],
        } if dog else None),
        "favorite": {
            "is_home": not underdog_is_home,
            "team_id": str(fav_side.get("id") or ""),
            "team_name": fav_side.get("name") or "",
            "team_name_he": team_names_he.hebrew_name(fav_side.get("name")),
            "win_prob": round(max(p_home, p_away), 4),
        },
        "has_clear_underdog": dog is not None,
        "alert_chance": {"value": round(chance, 2), "band": band},
        "stakes": round(stakes, 3),
    }


@app.get("/soccer/live/{canonical_id}")
def soccer_live_snapshot(canonical_id: str):
    """Lightweight WebSocket-only live state. Zero REST calls — returns the
    in-memory WS snapshot for score, minute, per-team stats (40+ counters),
    ball position (livedata, ~5 s cadence), and consensus odds.

    Always 200. `ws_active: false` means no snapshot yet (match not tracked
    or WS not connected); iOS should fall back to the full /soccer/match
    endpoint in that case.  Only meaningful for bsd-* canonical ids.
    """
    import demo_data
    if demo_data.is_demo_id(canonical_id):
        return demo_data.live(canonical_id)
    import soccer_source as _ss
    return _ss.build_live_snapshot(canonical_id)


@app.get("/soccer/drama/{canonical_id}")
def soccer_drama(canonical_id: str):
    """Return the Drama Engine breakdown for a match. Debug + iOS Drama Meter.

    Response shape:
        {
          "match_id": "bsd-372",
          "minute": 78,
          "drama": {"total": 7.4, "state": 3.6, "momentum": 2.83, "events": 0.0, "stakes": 1.15},
          "match": {...slim match payload...}
        }
    """
    import soccer_source as _ss
    import drama_engine as _de
    import demo_data
    if demo_data.is_demo_id(canonical_id):
        return demo_data.drama(canonical_id)
    detail = _ss.get_soccer_match_detail(canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="match not found")
    ctx = _de.context_from_match_detail(detail)
    # SHARED enrichment — IDENTICAL to the radar's _match_drama, so the card's
    # Drama Meter shows the same real number that fires alerts (WS win-prob swing,
    # blended live momentum, near-miss/saves thrill, pre-match upset, WC-group
    # jeopardy). Previously this endpoint had only swing + pre-match + groups and
    # missed the live momentum/thrill — a different, misleading number.
    _ss.enrich_drama_ctx(ctx, canonical_id, detail)
    score = _de.compute_drama(ctx)
    _ss.record_peak_drama(canonical_id, score.total)
    return {
        "match_id": detail.get("id"),
        "minute":   detail.get("minute"),
        "status":   detail.get("status"),
        "drama":    score.as_dict(),
        "match":    {
            "home":        detail.get("home"),
            "away":        detail.get("away"),
            "competition": detail.get("competition"),
            "round_name":  detail.get("round_name"),
        },
    }


def _drama_snapshot_series(match_id: str) -> dict[int, dict]:
    """The REAL per-minute drama meter the live radar logged for this match — the
    LATEST snapshot in each minute (these carry the WebSocket enrichment: win-prob
    swing, attack surge, near-miss/saves). Empty when the match was never monitored
    (no alarm/follow on its competition) or the DB is unavailable."""
    if engine is None or not match_id:
        return {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT ON (minute) minute, state_score, momentum_score, "
                "       events_score, stakes_mult, drama_total "
                "FROM drama_snapshots "
                "WHERE match_id = :mid AND minute IS NOT NULL "
                "ORDER BY minute, id DESC"
            ), {"mid": match_id}).fetchall()
        return {
            int(r[0]): {"drama": float(r[5]), "state": float(r[1]),
                        "momentum": float(r[2]), "events": float(r[3]), "stakes": float(r[4])}
            for r in rows
        }
    except Exception as exc:
        log.debug("drama snapshot series failed for %s: %s", match_id, exc)
        return {}


@app.get("/soccer/drama/{canonical_id}/timeline")
def soccer_drama_timeline(canonical_id: str, step: int = 2):
    """Per-minute Drama Engine timeline.

    Prefers the REAL meter the live radar recorded each minute (drama_snapshots —
    WebSocket-enriched), so the graph is 1:1 with what fired the alerts. Minutes
    the radar never saw (pre-monitoring, or an unmonitored match) fall back to the
    retroactive reconstruction (replaying BSD's xg_per_minute/momentum/incidents)
    so the curve stays complete.

    `step` controls granularity ONLY for the pure-reconstruction fallback; when
    real snapshots exist the timeline is per-minute.

    Response: { match_id, timeline: [{m, drama, state, momentum, events, stakes}] }
    """
    import demo_data
    if demo_data.is_demo_id(canonical_id):
        return {"match_id": canonical_id, "timeline": demo_data.drama_timeline(canonical_id)}
    import soccer_source as _ss
    import drama_engine as _de
    detail = _ss.get_soccer_match_detail(canonical_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="match not found")
    step = max(1, min(5, step))
    snaps = _drama_snapshot_series(detail.get("id") or canonical_id)
    if snaps:
        recon = {p["m"]: p for p in _de.compute_drama_timeline(detail, step=1)}
        last = max(max(snaps), max(recon, default=0))
        timeline = []
        for m in range(1, last + 1):
            if m in snaps:
                timeline.append({"m": m, **snaps[m]})   # the real logged meter
            elif m in recon:
                timeline.append(recon[m])                # gap fill
    else:
        timeline = _de.compute_drama_timeline(detail, step=step)
    # Cache the peak so a finished match's feed badge reflects how dramatic it
    # actually got (not the FT-damped live state).
    try:
        peak = max((p.get("drama", 0.0) for p in timeline), default=0.0)
        if peak > 0:
            _ss.record_peak_drama(canonical_id, peak)
    except Exception:
        pass
    return {"match_id": detail.get("id"), "timeline": timeline}


@app.get("/soccer/team/{team_id}/matches")
def soccer_team_matches(team_id: str, league: str = "", name: str = ""):
    """All of a team's matches this season across competitions (one call).
    BSD for its leagues; 365scores for the 365-primary leagues (euros,
    ligat_haal). `league` is the team's primary league slug (used to pick the
    source). `name` is accepted for backward compatibility but unused."""
    import soccer_source as _ss
    return _ss.get_team_season(team_id, league or None, name or None)


@app.get("/soccer/team/{team_id}/squad")
def soccer_team_squad(team_id: str):
    """Official World Cup squad for a national team, each player merged to the
    club they currently play for. Empty for non-World-Cup ids / unannounced
    squads (BSD covers the current WC edition only)."""
    import soccer_source as _ss
    try:
        tid = int(team_id)
    except ValueError:
        return {"team_id": 0, "count": 0, "players": []}
    squad = _ss.national_team_squad(tid)
    for p in (squad.get("players") or []):
        if isinstance(p, dict):
            he = team_names_he.hebrew_name(p.get("club"))
            if he:
                p["club_he"] = he
    return squad


@app.get("/soccer/match/{canonical_id}/lineups")
def _stamp_lineup_he(result: dict) -> dict:
    """Stamp `team_name_he` onto each lineup side so the Lineups tab shows Hebrew
    team names (the detail header already does; this kept the tab in English)."""
    lu = (result or {}).get("lineups")
    if isinstance(lu, dict):
        for side in ("home", "away"):
            s = lu.get(side)
            if isinstance(s, dict):
                he = team_names_he.hebrew_name(s.get("team_name") or s.get("name"))
                if he:
                    s["team_name_he"] = he
    return result


def soccer_match_lineups(canonical_id: str):
    """Confirmed / predicted lineups for a soccer match. Supports BSD- and
    365scores-sourced ids; unknown sources return lineup_status=unavailable."""
    if canonical_id.startswith("bsd-"):
        try:
            event_id = int(canonical_id[4:])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid canonical_id format")
        import bzzoiro_adapter as _bsd_soccer
        result = _bsd_soccer.event_lineups(event_id)
        if result is None:
            return {"lineup_status": "unavailable", "lineups": None, "event_id": event_id}
        # Merge real post-match player ratings (0–10) keyed by player_id. Only
        # finished/live matches have them; upcoming matches return an empty list.
        ratings: dict[int, float] = {}
        try:
            ps = _bsd_soccer.event_player_stats(event_id) or {}
            for r in (ps.get("player_stats") or []):
                pid = r.get("player_id")
                rat = r.get("rating")
                if pid is not None and rat is not None:
                    ratings[int(pid)] = float(rat)
        except Exception as exc:
            log.warning("player-stats merge failed for %s: %s", event_id, exc)
        return _stamp_lineup_he(_normalize_lineups(result, ratings))

    if canonical_id.startswith("s365-"):
        try:
            event_id = int(canonical_id[5:])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid canonical_id format")
        import soccer_source as _ss
        result = _ss.get_365_lineups(event_id)
        if result is None:
            return {"lineup_status": "unavailable", "lineups": None, "event_id": event_id}
        # The 365 normalizer already emits the final per-player shape (name,
        # position, jersey, ai_score = the 365 `ranking`, and the same value as
        # `rating` since 365's ranking IS the post-match player rating). No BSD
        # ratings-merge applies here.
        return _stamp_lineup_he(result)

    return {"lineup_status": "unavailable", "lineups": None, "event_id": None}


def _normalize_lineups(raw: dict, ratings: dict[int, float] | None = None) -> dict:
    """Attach a real post-match `rating` (0–10) to each lineup player when it's
    available (from event player-stats, keyed by player_id), plus keep the
    `ai_score` pre-match prediction and a `photo_url`.
    """
    ratings = ratings or {}

    def _fix_players(players: list) -> list:
        out = []
        for p in (players or []):
            score = (p.get("ai_score") or p.get("score")
                     or p.get("performance_score") or p.get("match_rating"))
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None
            pid = p.get("id") or p.get("player_id")
            rating = None
            try:
                if pid is not None:
                    rating = ratings.get(int(pid))
            except (TypeError, ValueError):
                rating = None
            photo = p.get("photo_url") or (
                f"https://sports.bzzoiro.com/img/player/{pid}/" if pid else None
            )
            out.append({**p, "ai_score": score, "rating": rating, "photo_url": photo})
        return out

    def _fix_side(side: dict) -> dict:
        if not isinstance(side, dict):
            return side
        return {
            **side,
            "players":     _fix_players(side.get("players", [])),
            "substitutes": _fix_players(side.get("substitutes", [])),
        }

    lineups = raw.get("lineups") or {}
    if isinstance(lineups, dict) and ("home" in lineups or "away" in lineups):
        raw = {
            **raw,
            "lineups": {
                **lineups,
                "home": _fix_side(lineups.get("home", {})),
                "away": _fix_side(lineups.get("away", {})),
            }
        }
    return raw


@app.get("/soccer/league/{slug}/standings")
def soccer_league_standings(slug: str):
    """Current league table for a soccer league slug (e.g. premier_league)."""
    from score365_soccer_adapter import BSD_COMPETITION_IDS
    league_id = BSD_COMPETITION_IDS.get(slug)
    if league_id is not None:
        import bzzoiro_adapter as _bsd_soccer
        try:
            raw = _bsd_soccer.league_standings(league_id)
        except Exception as exc:
            log.warning("soccer standings error for %s: %s", slug, exc)
            raise HTTPException(status_code=503, detail="Standings temporarily unavailable")
        if raw is None:
            raise HTTPException(status_code=503, detail="Standings temporarily unavailable")
        return _normalize_soccer_standings(raw, league_id)

    # 365-primary leagues (euros, ligat_haal) have no BSD competition id — serve
    # the table from 365scores. Group-based competitions (Euros groups, the
    # Israeli Championship/Relegation split) come back as a `groups[]` array.
    from score365_soccer_adapter import COMPETITION_IDS, soccer_league_standings_table
    comp_id = COMPETITION_IDS.get(slug)
    if comp_id is not None:
        try:
            result = soccer_league_standings_table(comp_id)
        except Exception as exc:
            log.warning("365 soccer standings error for %s: %s", slug, exc)
            raise HTTPException(status_code=503, detail="Standings temporarily unavailable")
        if result is not None:
            return result
    raise HTTPException(status_code=404, detail=f"No standings for league '{slug}'")


def _normalize_soccer_standings(raw: dict, league_id: int) -> dict:
    season_raw = raw.get("season") or {}

    def _norm_rows(rows: list) -> list:
        out = []
        for row in rows or []:
            team = row.get("team") if isinstance(row.get("team"), dict) else {}
            tid  = team.get("id") or row.get("team_id") or row.get("id") or 0
            name = team.get("name") or row.get("team_name") or ""
            gf   = _i(row, "goals_for",      "gf", "scored",   "goals_scored")
            ga   = _i(row, "goals_against",  "ga", "conceded", "goals_conceded")
            gd   = _i(row, "goal_difference", "gd") or (gf - ga)
            name_he = team_names_he.hebrew_name(name)
            entry = {
                "position":   _i(row, "position", "rank"),
                "team_id":    tid,
                "team_name":  name,
                "team_crest": f"https://sports.bzzoiro.com/img/team/{tid}/" if tid else None,
                "played":     _i(row, "played", "matches", "games_played"),
                "won":        _i(row, "won",   "wins",   "w"),
                "drawn":      _i(row, "drawn", "draws",  "d"),
                "lost":       _i(row, "lost",  "losses", "l"),
                "gf": gf, "ga": ga, "gd": gd,
                "pts":        _i(row, "points", "pts"),
                "form":       row.get("form"),
                "live":       row.get("live"),
            }
            if name_he:
                entry["team_name_he"] = name_he
            out.append(entry)
        return out

    out = {
        "league_id": league_id,
        "season":    {"id": season_raw.get("id") or 0, "name": season_raw.get("name") or ""},
        "grouped":   bool(raw.get("grouped", False)),
        "standings": [],
        "groups":    [],
    }
    # Grouped competitions (World Cup, Euros, …) come back as
    # `{"groups": {"Group A": [rows], ...}}` — emit a groups array so the client
    # can render one mini-table per group instead of a single flat list.
    groups_raw = raw.get("groups")
    if isinstance(groups_raw, dict) and groups_raw:
        out["grouped"] = True
        out["groups"] = [{"name": gname, "standings": _norm_rows(grows)}
                         for gname, grows in groups_raw.items()]
    else:
        out["standings"] = _norm_rows(
            raw.get("standings") or raw.get("table") or raw.get("rows") or [])
    return out


def _i(d: dict, *keys: str) -> int:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try: return int(v)
            except (TypeError, ValueError): pass
    return 0


@app.get("/basketball/team/{team_id}/games")
def basketball_team_games(team_id: str, league: str = "nba"):
    """Recent + upcoming games for a basketball team in the 365scores rolling window.
    Returns games in ESPN event format, sorted by date."""
    from score365_adapter import _fetch_competition_window, _transform_game, COMPETITION_IDS
    comp_id = COMPETITION_IDS.get(league)
    if not comp_id:
        return []
    try:
        raw = _fetch_competition_window(comp_id)
    except Exception as exc:
        log.warning("basketball_team_games fetch failed for team=%s league=%s: %s", team_id, league, exc)
        return []
    results = []
    for g in raw:
        hid = str((g.get("homeCompetitor") or {}).get("id", ""))
        aid = str((g.get("awayCompetitor") or {}).get("id", ""))
        if team_id in (hid, aid):
            results.append(_transform_game(g, league=league))
    results.sort(key=lambda x: x.get("date", ""))
    return results


@app.get("/basketball/league/{slug}/standings")
def basketball_league_standings(slug: str):
    """Current standings for a basketball league slug (e.g. nba, euroleague).
    365scores is the sole basketball source — it covers every league we
    surface (nba, euroleague, eurocup, bcl, winner_league)."""
    import basketball_source as _bb
    s365_result = _bb.get_s365_basketball_standings(slug)
    if s365_result is not None:
        return s365_result
    raise HTTPException(status_code=404, detail=f"No standings available for basketball league '{slug}'")


def _normalize_basketball_standings(raw: dict, league_id: int, slug: str) -> dict:
    import basketball_source as _bb
    league_raw  = raw.get("league") or {}
    season_raw  = raw.get("season") or {}
    rows = raw.get("standings") or raw.get("results") or raw.get("table") or []
    if isinstance(rows, dict):
        rows = rows.get("results") or []

    out = []
    for i, row in enumerate(rows, start=1):
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        tid  = team.get("id") or row.get("team_id") or 0
        name = team.get("name") or row.get("team_name") or ""
        short_name = (team.get("abbreviation") or team.get("short_name")
                      or team.get("code") or None)
        wins    = _i(row, "wins",   "win", "w")
        losses  = _i(row, "losses", "loss", "l")
        matches = wins + losses or _i(row, "matches", "games", "games_played", "gp")
        pct_raw = (row.get("win_percentage") or row.get("percentage") or row.get("pct"))
        try:
            pct = float(pct_raw) if pct_raw is not None else None
        except (TypeError, ValueError):
            pct = None
        sf  = _i(row, "points_scored",  "points_for",     "pf") or None
        sa  = _i(row, "points_allowed", "points_against",  "pa") or None
        team_obj = {"id": tid, "name": name, "short_name": short_name,
                    "logo": _bb._basketball_logo_url(slug, name, tid)}
        team_names_he.attach(team_obj)
        out.append({
            "team": team_obj,
            "position":       _i(row, "position", "rank", "standing") or i,
            "matches":        matches,
            "wins":           wins,
            "losses":         losses,
            "percentage":     pct,
            "scores_for":     sf,
            "scores_against": sa,
        })
    return {
        "league":  {"id": league_raw.get("id") or league_id,
                    "name": league_raw.get("name") or slug.upper().replace("_", " ")},
        "season":  {"id": season_raw.get("id") or 0,
                    "name": season_raw.get("name") or ""},
        "standings": out,
    }


@app.get("/soccer/live")
def soccer_live(leagues: Optional[str] = None):
    """All currently-live soccer matches across requested leagues
    (comma-separated slugs). Defaults to all 10 tracked competitions.
    """
    import soccer_source as _ss
    league_slugs = leagues.split(",") if leagues else None
    return {"matches": _ss.get_live_matches(league_slugs)}


# ── Soccer alarm CRUD ────────────────────────────────────────────────────────

# alarm_type values accepted by the API. Keep in sync with the iOS SoccerAlarmType
# enum and the soccer_monitor evaluator.
_SOCCER_ALARM_TYPES = {
    "match",          # single-match alarm — config carries the condition
    "late_drama",     # any tracked match, ≥75', score within 1
    "knockout_et",    # any tracked knockout match entering ET or pens
    "comeback",       # favorite team trailing, score brought within 1
    "hat_trick",      # legacy — superseded by player_alert with hat_trick_watch=True
    "red_card",       # red card in close match in last 20 min
    "big_chance",     # 3+ big chances in last 10 min, score within 1
    "penalty",        # penalty awarded in any tracked match
    "drama_radar",    # Drama Engine threshold
    "player_alert",   # per-player goal/card/hattrick watch (config carries toggles)
    # v3.1 modular types — config carries the sub-options:
    "match_moments",  # config flags kickoff/halftime/extra_time/penalties (multi-fire)
    "card_penalty",   # config flags red_card/penalty (fire on any such event)
    "sensation",      # underdog leading — config stores the underdog (resolved at creation)
    "score_gap",      # threshold=max_diff, config.from_minute=start; fires once when gap<=threshold
}


class SoccerAlarmRequest(BaseModel):
    user_token: str
    alarm_type: str
    match_id: Optional[str] = None       # canonical id ('bsd-372') — required for 'match' type
    match_name: Optional[str] = None     # human-readable "Home vs Away" for display in Active tab
    competition_id: Optional[str] = None # slug — used by radar-style alarms to scope
    player_id: Optional[str] = None      # for hat_trick
    threshold: Optional[float] = None    # interpretation depends on alarm_type
    config: Optional[dict] = None        # alarm-type-specific overrides
    alert_type: str = "alarm"            # 'alarm' | 'silent'


@app.post("/soccer-alarm")
def soccer_alarm_create(req: SoccerAlarmRequest):
    """Create a soccer alarm. Returns the row id for later deletion."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not req.user_token.strip():
        raise HTTPException(status_code=400, detail="user_token is required.")
    if req.alarm_type not in _SOCCER_ALARM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"alarm_type must be one of: {sorted(_SOCCER_ALARM_TYPES)}",
        )
    if req.alarm_type == "match" and not req.match_id:
        raise HTTPException(status_code=400, detail="match_id is required for alarm_type='match'.")
    if req.alarm_type in ("hat_trick", "player_alert") and not req.player_id:
        raise HTTPException(status_code=400, detail=f"player_id is required for alarm_type='{req.alarm_type}'.")

    # ── v3.1 modular validation / enrichment ────────────────────────────────
    cfg = dict(req.config or {})
    if req.alarm_type == "late_drama":
        # Clamp to sane bounds; empty config reproduces the legacy ≥75'/≤1 behavior.
        cfg["max_diff"] = max(0, min(3, int(cfg.get("max_diff", 1))))
        cfg["last_minutes"] = max(1, min(45, int(cfg.get("last_minutes", 15))))
        req.config = cfg
    elif req.alarm_type == "match_moments":
        if not any(bool(cfg.get(k)) for k in ("kickoff", "halftime", "extra_time", "penalties")):
            raise HTTPException(status_code=422, detail="select at least one moment.")
    elif req.alarm_type == "card_penalty":
        if not (bool(cfg.get("red_card")) or bool(cfg.get("penalty"))):
            raise HTTPException(status_code=422, detail="select red card and/or penalty.")
    elif req.alarm_type == "sensation":
        if not req.match_id:
            raise HTTPException(status_code=400, detail="match_id is required for alarm_type='sensation'.")
        import soccer_source as _ss
        dog = _ss.resolve_underdog(req.match_id)
        if dog is None:
            raise HTTPException(status_code=422, detail="no clear underdog for this match.")
        cfg.update(dog)  # underdog_is_home / team_id / name / win_prob
        req.config = cfg

    with Session(engine) as session:
        # Idempotency: if an active alarm already exists for the same (user, type, match, player),
        # update it in place rather than stacking duplicates.
        existing = session.query(SoccerAlarmDB).filter(
            SoccerAlarmDB.user_token == req.user_token,
            SoccerAlarmDB.alarm_type == req.alarm_type,
            SoccerAlarmDB.match_id == req.match_id,
            SoccerAlarmDB.player_id == req.player_id,
            SoccerAlarmDB.is_active.is_(True),
        ).first()
        if existing:
            existing.competition_id = req.competition_id
            existing.threshold      = req.threshold
            existing.config         = req.config or {}
            existing.alert_type     = req.alert_type
            if req.match_name:
                existing.match_name = req.match_name
            session.commit()
            session.refresh(existing)
            alarm_id = existing.id
            created = False
        else:
            alarm = SoccerAlarmDB(
                user_token=req.user_token,
                alarm_type=req.alarm_type,
                match_id=req.match_id,
                match_name=req.match_name,
                competition_id=req.competition_id,
                player_id=req.player_id,
                threshold=req.threshold,
                config=req.config or {},
                alert_type=req.alert_type,
                is_active=True,
            )
            session.add(alarm)
            session.commit()
            session.refresh(alarm)
            alarm_id = alarm.id
            created = True

    return {
        "status": "alarm_set" if created else "alarm_updated",
        "alarm_id": alarm_id,
        "alarm_type": req.alarm_type,
        "match_id": req.match_id,
    }


@app.delete("/soccer-alarm/{alarm_id}")
def soccer_alarm_delete(alarm_id: int, user_token: str = ""):
    """Deactivate a soccer alarm. Requires the same user_token that created it."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not user_token.strip():
        raise HTTPException(status_code=400, detail="user_token is required.")
    with Session(engine) as session:
        alarm = session.query(SoccerAlarmDB).filter(
            SoccerAlarmDB.id == alarm_id,
            SoccerAlarmDB.user_token == user_token,
        ).first()
        if alarm is None:
            raise HTTPException(status_code=404, detail="alarm not found.")
        alarm.is_active = False
        session.commit()
    return {"status": "alarm_deactivated", "alarm_id": alarm_id}


@app.get("/soccer-alarms")
def _match_name_he(name: str | None) -> Optional[str]:
    """Best-effort Hebrew for a 'Home vs Away' match label (both sides must
    resolve, else None so the client keeps the English original)."""
    if not name:
        return None
    for sep in (" vs ", " - "):
        if sep in name:
            h, a = name.split(sep, 1)
            hh = team_names_he.hebrew_name(h.strip())
            ah = team_names_he.hebrew_name(a.strip())
            return f"{hh} נגד {ah}" if hh and ah else None
    return None


def soccer_alarms_list(user_token: str = ""):
    """List a user's active soccer alarms."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not user_token.strip():
        raise HTTPException(status_code=400, detail="user_token is required.")
    with Session(engine) as session:
        rows = session.query(SoccerAlarmDB).filter(
            SoccerAlarmDB.user_token == user_token,
            SoccerAlarmDB.is_active.is_(True),
        ).order_by(SoccerAlarmDB.created_at.desc()).limit(50).all()
        out = [{
            "id": r.id,
            "alarm_type": r.alarm_type,
            "match_id": r.match_id,
            "match_name": r.match_name,
            "match_name_he": _match_name_he(r.match_name),
            "competition_id": r.competition_id,
            "player_id": r.player_id,
            "threshold": r.threshold,
            "config": r.config or {},
            "alert_type": r.alert_type,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "fired_at": r.fired_at.isoformat() if r.fired_at else None,
        } for r in rows]
    return {"alarms": out}


# ── Soccer Favorites CRUD ────────────────────────────────────────────────────
# Persistent player / team / competition watch lists. Players & teams power
# event-driven alerts (hat-trick, red card, etc.); competitions scope the
# Drama Radar (P3). All endpoints are idempotent — POST upserts by PK.

class SoccerFavoritePlayerRequest(BaseModel):
    user_token: str
    player_id: str
    player_name: Optional[str] = None
    team_id: Optional[str] = None
    # Alt provider ids for this player. The upsert handler reads it; without
    # this field the endpoint 500'd because clients (iOS) don't send it.
    team_ids: Optional[list[str]] = None
    # National-team tracking. Clients may pass either the resolved national-team
    # id (`country_id`) or just the nationality name (`country`); if neither is
    # given the server resolves it from the player's detail. Any of these merges
    # the national team into the tracked id set so e.g. France's games show up.
    country: Optional[str] = None
    country_id: Optional[str] = None
    config: Optional[dict] = None
    alert_type: str = "alarm"
    paused: bool = False


class SoccerFavoriteTeamRequest(BaseModel):
    user_token: str
    team_id: str
    team_name: Optional[str] = None
    competition_id: Optional[str] = None
    team_ids: list[str] = []          # all provider ids for this club (incl. primary)
    competitions: list[str] = []      # all leagues this club plays in
    drama_threshold: float = 7.0
    alert_type: str = "alarm"
    paused: bool = False
    notify_at_kickoff: bool = False
    # Full per-match criteria (drama_radar/late_drama/match_moments/card_penalty/
    # sensation) applied to every one of the team's matches. Optional — absent
    # keeps the legacy drama_threshold + notify_at_kickoff behavior.
    config: Optional[dict] = None


class SoccerFavoriteCompetitionRequest(BaseModel):
    user_token: str
    competition_id: str
    drama_threshold: float = 7.0
    alert_type: str = "silent"
    notify_at_kickoff: bool = False
    config: Optional[dict] = None


def _require_token(user_token: str) -> None:
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if not user_token.strip():
        raise HTTPException(status_code=400, detail="user_token is required.")


# ─── Players ────────────────────────────────────────────────────────────────

@app.post("/soccer/favorites/players")
def soccer_fav_player_upsert(req: SoccerFavoritePlayerRequest):
    _require_token(req.user_token)
    with Session(engine) as session:
        row = session.get(SoccerFavoritePlayerDB, (req.user_token, req.player_id))
        if row is None:
            row = SoccerFavoritePlayerDB(
                user_token=req.user_token,
                player_id=req.player_id,
            )
            session.add(row)
        row.player_name = req.player_name
        alt_ids = [tid for tid in dict.fromkeys(req.team_ids or []) if tid and tid != req.team_id]
        # Resolve the player's NATIONAL TEAM and track it alongside the club, so
        # the favorites feed surfaces e.g. France's games for Mbappé. Entirely
        # best-effort: a missing/unknown nationality just means no national team
        # is added — it must never block favoriting.
        nat_id = _resolve_national_team_id(req)
        if nat_id and nat_id != req.team_id and nat_id not in alt_ids:
            alt_ids.append(nat_id)
        row.team_id     = req.team_id
        row.alt_team_ids = ",".join(alt_ids) or None
        row.config      = req.config or {}
        row.alert_type  = req.alert_type
        row.paused      = req.paused
        session.commit()
    return {"status": "ok", "player_id": req.player_id}


def _resolve_national_team_for_player(
    player_id: str,
    *,
    country: Optional[str] = None,
    country_id: Optional[str] = None,
) -> Optional[str]:
    """National-team id for a player. Resolution order: a client-supplied
    `country_id`, then BSD's direct `national_team_id` from player detail, then
    a nationality-name lookup (using `country` if supplied, else the player's
    nationality). Returns None on anything unexpected — callers treat that as
    "no national team to track", never as an error."""
    try:
        import national_team as nt
        if country_id:
            return str(country_id)
        import bzzoiro_adapter as bz
        detail = bz.player_detail(player_id) or {}
        # BSD's direct national_team_id is the most reliable signal — prefer it.
        direct = detail.get("national_team_id")
        if direct is not None:
            return str(direct)
        # Fall back to a nationality-name lookup: covers players BSD leaves
        # national_team_id null but whose nation is in a current competition.
        return nt.national_team_id(country or detail.get("nationality"))
    except Exception as exc:
        log.warning("soccer fav player: national-team resolve failed (%s): %s",
                    player_id, exc)
        return None


def _resolve_national_team_id(req: "SoccerFavoritePlayerRequest") -> Optional[str]:
    """National-team id for a favorite-player upsert request."""
    return _resolve_national_team_for_player(
        req.player_id, country=req.country, country_id=req.country_id)


@app.delete("/soccer/favorites/players/{player_id}")
def soccer_fav_player_delete(player_id: str, user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        row = session.get(SoccerFavoritePlayerDB, (user_token, player_id))
        if row is None:
            raise HTTPException(status_code=404, detail="favorite player not found.")
        session.delete(row)
        session.commit()
    return {"status": "deleted", "player_id": player_id}


@app.get("/soccer/favorites/players")
def soccer_fav_players_list(user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        rows = session.query(SoccerFavoritePlayerDB).filter(
            SoccerFavoritePlayerDB.user_token == user_token,
        ).order_by(SoccerFavoritePlayerDB.created_at.desc()).all()
        # Self-heal: favorites saved before national-team resolution worked (or
        # before a player's nationality was resolvable) carry only the club id.
        # Backfill the national team for any row with no alternate ids yet so the
        # feed starts surfacing e.g. France's games for an already-favorited
        # Mbappé without the user re-adding. Best-effort; never break listing.
        _changed = False
        for r in rows:
            if getattr(r, "alt_team_ids", None):
                continue
            nat_id = _resolve_national_team_for_player(r.player_id)
            if nat_id and nat_id != r.team_id:
                r.alt_team_ids = nat_id
                _changed = True
        if _changed:
            try:
                session.commit()
            except Exception as exc:
                log.warning("fav players national-team backfill commit failed: %s", exc)
                session.rollback()
        out = [{
            "player_id":   r.player_id,
            "player_name": r.player_name,
            "team_id":     r.team_id,
            "team_ids":    ([r.team_id] if r.team_id else []) + (r.alt_team_ids.split(",") if getattr(r, "alt_team_ids", None) else []),
            "config":      r.config or {},
            "alert_type":  r.alert_type,
            "paused":      r.paused,
            "created_at":  r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    return {"players": out}


# ─── Teams ──────────────────────────────────────────────────────────────────

@app.post("/soccer/favorites/teams")
def soccer_fav_team_upsert(req: SoccerFavoriteTeamRequest):
    _require_token(req.user_token)
    alt_ids = [tid for tid in dict.fromkeys(req.team_ids) if tid and tid != req.team_id]
    comps   = [c for c in dict.fromkeys(req.competitions) if c]
    if not comps and req.competition_id:
        comps = [req.competition_id]
    with Session(engine) as session:
        row = session.get(SoccerFavoriteTeamDB, (req.user_token, req.team_id))
        if row is None:
            row = SoccerFavoriteTeamDB(
                user_token=req.user_token,
                team_id=req.team_id,
            )
            session.add(row)
        row.team_name         = req.team_name
        row.competition_id    = req.competition_id
        row.alt_team_ids      = ",".join(alt_ids) or None
        row.competitions      = ",".join(comps) or None
        row.drama_threshold   = req.drama_threshold
        row.alert_type        = req.alert_type
        row.paused            = req.paused
        row.notify_at_kickoff = req.notify_at_kickoff
        row.config            = req.config
        session.commit()
    return {"status": "ok", "team_id": req.team_id}


@app.delete("/soccer/favorites/teams/{team_id}")
def soccer_fav_team_delete(team_id: str, user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        row = session.get(SoccerFavoriteTeamDB, (user_token, team_id))
        if row is None:
            raise HTTPException(status_code=404, detail="favorite team not found.")
        session.delete(row)
        session.commit()
    return {"status": "deleted", "team_id": team_id}


@app.get("/soccer/favorites/teams")
def soccer_fav_teams_list(user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        rows = session.query(SoccerFavoriteTeamDB).filter(
            SoccerFavoriteTeamDB.user_token == user_token,
        ).order_by(SoccerFavoriteTeamDB.created_at.desc()).all()
        out = []
        for r in rows:
            alt_ids = [t for t in (r.alt_team_ids or "").split(",") if t]
            comps   = [c for c in (r.competitions or "").split(",") if c]
            if not comps and r.competition_id:
                comps = [r.competition_id]
            out.append(_with_team_name_he({
                "team_id":           r.team_id,
                "team_ids":          [r.team_id] + alt_ids,
                "team_name":         r.team_name,
                "competition_id":    r.competition_id,
                "competitions":      comps,
                "drama_threshold":   r.drama_threshold,
                "alert_type":        r.alert_type,
                "paused":            r.paused,
                "notify_at_kickoff": r.notify_at_kickoff,
                "config":            r.config,
                "created_at":        r.created_at.isoformat() if r.created_at else None,
            }))
    return {"teams": out}


# ─── Competitions ───────────────────────────────────────────────────────────

@app.post("/soccer/favorites/competitions")
def soccer_fav_competition_upsert(req: SoccerFavoriteCompetitionRequest):
    _require_token(req.user_token)
    with Session(engine) as session:
        row = session.get(SoccerFavoriteCompetitionDB, (req.user_token, req.competition_id))
        if row is None:
            row = SoccerFavoriteCompetitionDB(
                user_token=req.user_token,
                competition_id=req.competition_id,
            )
            session.add(row)
        row.drama_threshold   = req.drama_threshold
        row.alert_type        = req.alert_type
        row.notify_at_kickoff = req.notify_at_kickoff
        row.paused            = False
        row.config            = req.config
        session.commit()
    return {"status": "ok", "competition_id": req.competition_id}


@app.delete("/soccer/favorites/competitions/{competition_id}")
def soccer_fav_competition_delete(competition_id: str, user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        row = session.get(SoccerFavoriteCompetitionDB, (user_token, competition_id))
        if row is None:
            raise HTTPException(status_code=404, detail="favorite competition not found.")
        session.delete(row)
        session.commit()
    return {"status": "deleted", "competition_id": competition_id}


@app.get("/soccer/favorites/competitions")
def soccer_fav_competitions_list(user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        rows = session.query(SoccerFavoriteCompetitionDB).filter(
            SoccerFavoriteCompetitionDB.user_token == user_token,
        ).order_by(SoccerFavoriteCompetitionDB.created_at.desc()).all()
        out = [{
            "competition_id":    r.competition_id,
            "drama_threshold":   r.drama_threshold,
            "alert_type":        getattr(r, "alert_type", None) or "silent",
            "notify_at_kickoff": bool(getattr(r, "notify_at_kickoff", False)),
            "paused":            bool(getattr(r, "paused", False)),
            "config":            getattr(r, "config", None),
            "created_at":        r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    return {"competitions": out}


# ─── Combined fetch (one round-trip from iOS) ───────────────────────────────

@app.get("/soccer/favorites")
def soccer_favorites_all(user_token: str = ""):
    _require_token(user_token)
    return {
        "players":      soccer_fav_players_list(user_token=user_token)["players"],
        "teams":        soccer_fav_teams_list(user_token=user_token)["teams"],
        "competitions": soccer_fav_competitions_list(user_token=user_token)["competitions"],
    }


# ─── Calendar subscriptions (webcal/ICS) ────────────────────────────────────

class CalendarSubscribeRequest(BaseModel):
    user_token: str
    scope: str
    include_players: bool = False
    custom_soccer_teams: list[dict] = []        # [{"ids":[..], "leagues":[..]}] (old {"id","league"} ok)
    custom_soccer_team_ids: list[str] = []      # legacy (no league), kept for back-compat
    custom_soccer_league_slugs: list[str] = []
    custom_bball_teams: list[dict] = []         # [{"ids":[..], "leagues":[..]}] (old {"id","league"} ok)
    custom_bball_league_slugs: list[str] = []


class CalendarRevokeRequest(BaseModel):
    user_token: str
    token: str


@app.post("/calendar/subscribe")
def calendar_subscribe(req: CalendarSubscribeRequest):
    _require_token(req.user_token)
    if req.scope not in _CAL_SCOPES:
        raise HTTPException(status_code=400, detail="invalid scope")
    params = {
        "include_players": req.include_players,
        "custom_soccer_teams": req.custom_soccer_teams,
        "custom_soccer_team_ids": req.custom_soccer_team_ids,
        "custom_soccer_league_slugs": req.custom_soccer_league_slugs,
        "custom_bball_teams": req.custom_bball_teams,
        "custom_bball_league_slugs": req.custom_bball_league_slugs,
    }
    with Session(engine) as session:
        # Reuse an existing non-revoked subscription for (user, scope); for custom,
        # also require identical params so different custom sets get distinct tokens.
        q = session.query(CalendarSubscriptionDB).filter_by(
            user_token=req.user_token, scope=req.scope, revoked=False)
        existing = None
        for row in q.all():
            if req.scope != "custom" or (row.params or {}) == params:
                existing = row
                break
        if existing is not None:
            existing.params = params
            existing.updated_at = datetime.now(timezone.utc)
            token = existing.token
        else:
            token = _mint_calendar_token()
            session.add(CalendarSubscriptionDB(
                token=token, user_token=req.user_token, scope=req.scope, params=params))
        session.commit()
    _ICS_CACHE.pop(token, None)   # bust cache on (re)subscribe
    return {
        "token": token,
        "webcal_url": f"webcal://{_CAL_HOST}/cal/{token}.ics",
        "https_url": f"https://{_CAL_HOST}/cal/{token}.ics",
    }


@app.get("/calendar/{token}.ics")
def calendar_ics_feed(token: str):
    import calendar_scope
    import calendar_ics
    cached = _ICS_CACHE.get(token)
    if cached and cached[0] > _time.monotonic():
        return Response(content=cached[1], media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})
    with Session(engine) as session:
        row = session.get(CalendarSubscriptionDB, token)
        if row is None or row.revoked:
            raise HTTPException(status_code=404, detail="calendar not found")
        scope, params, user_token = row.scope, (row.params or {}), row.user_token
        matches = calendar_scope.collect_matches(session, scope, params, user_token)
    lang = _get_user_language_from_db(user_token) or "en"
    ics = calendar_ics.build_calendar(matches, cal_name=_CAL_NAMES.get(scope, "Clutch Alarm"), lang=lang)
    _ICS_CACHE[token] = (_time.monotonic() + _ICS_TTL, ics)
    return Response(content=ics, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=300"})


@app.post("/calendar/revoke")
def calendar_revoke(req: CalendarRevokeRequest):
    _require_token(req.user_token)
    with Session(engine) as session:
        row = session.get(CalendarSubscriptionDB, req.token)
        if row is None or row.user_token != req.user_token:
            raise HTTPException(status_code=404, detail="subscription not found")
        row.revoked = True
        session.commit()
    _ICS_CACHE.pop(req.token, None)
    return {"status": "revoked", "token": req.token}


@app.get("/calendar/subscriptions")
def calendar_subscriptions(user_token: str = ""):
    _require_token(user_token)
    with Session(engine) as session:
        rows = session.query(CalendarSubscriptionDB).filter_by(
            user_token=user_token, revoked=False).order_by(
            CalendarSubscriptionDB.created_at.desc()).all()
        out = [{
            "token": r.token, "scope": r.scope, "params": r.params or {},
            "webcal_url": f"webcal://{_CAL_HOST}/cal/{r.token}.ics",
            "https_url": f"https://{_CAL_HOST}/cal/{r.token}.ics",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    return {"subscriptions": out}


def _stamp_player_he(p: dict | None) -> dict | None:
    """Stamp Hebrew names for a player's club + national team (the player name
    itself stays Latin — sources carry no Hebrew player names)."""
    if isinstance(p, dict):
        for src, dst in (("team_name", "team_name_he"),
                         ("national_team_name", "national_team_name_he")):
            he = team_names_he.hebrew_name(p.get(src))
            if he:
                p[dst] = he
    return p


@app.get("/soccer/player/{player_id}")
def soccer_player(player_id: str, source: str = ""):
    """Normalized football player detail (bio + latest-season summary), with
    the player's current club and national team (ids + names) so the client
    can link to both.

    `source` disambiguates the id space: BSD player ids and 365 athlete ids
    overlap numerically (e.g. 13557 is a different player in each), so a caller
    that knows the match source ("s365"/"bsd") should pass it. Without it we try
    BSD then fall back to 365 — fine for non-colliding ids, but the explicit hint
    is what makes a 365-sourced match's scorer resolve to the RIGHT player."""
    import bzzoiro_adapter as bz
    import player_source as ps
    import soccer_source as ss
    if source == "s365":
        s365 = ss.s365_soccer_player(player_id)
        if s365:
            return _stamp_player_he(s365)
        raise HTTPException(status_code=404, detail="player not found")
    detail = bz.player_detail(player_id)
    if not detail:
        # Not a BSD id — a 365-sourced match's scorer (e.g. friendlies). Resolve
        # the 365 athlete so the incident tap still opens a player page.
        s365 = ss.s365_soccer_player(player_id)
        if s365:
            return _stamp_player_he(s365)
        raise HTTPException(status_code=404, detail="player not found")
    career = bz.player_career(player_id)
    club_id = detail.get("current_team_id")
    team_name = (bz.team(club_id) or {}).get("name") if club_id else None
    nat_id = detail.get("national_team_id")
    nat_name = (bz.team(nat_id) or {}).get("name") if nat_id else None
    return _stamp_player_he(ps.normalize_soccer_detail(
        detail, career, player_id=player_id, team_name=team_name,
        current_team_id=club_id,
        national_team_id=nat_id, national_team_name=nat_name))


@app.get("/soccer/player/{player_id}/stats")
def soccer_player_stats(player_id: str, limit: int = 10):
    """Recent football matches: minutes, goals, assists, rating, opponent."""
    import bzzoiro_adapter as bz
    import player_source as ps
    rows = bz.player_match_stats(player_id, limit=limit)
    events: dict = {}
    for s in rows:
        eid = s.get("event_id")
        if eid and eid not in events:
            ev = bz.event(eid)
            if ev:
                events[eid] = ev
    games = ps.normalize_soccer_stats({"results": rows}, events, limit=limit)
    for g in (games or []):
        if isinstance(g, dict):
            he = team_names_he.hebrew_name(g.get("opponent") or g.get("opponent_name"))
            if he:
                g["opponent_he"] = he
    return {"games": games}


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
    Get all basketball TV broadcasts for today's games (all leagues), keyed by game_id.
    Returns: { "broadcasts": { "401810906": [...], "401810907": [...] } }
    """
    if country.lower() not in _ALLOWED_EPG_COUNTRIES:
        return {"broadcasts": {}}
    all_games = []
    for lid in ("nba", "euroleague", "eurocup", "bcl", "winner_league"):
        all_games.extend(get_games_for_date(None, league=lid))
    return {"broadcasts": get_all_broadcasts(country=country, games=all_games)}


# ── Watch Score (in-memory cache, keyed by "DATE_HOME_AWAY") ─────────────────

_watch_score_cache: dict[str, dict] = {}


def _get_cached_watch_score(home: str, away: str, date_str: str, league: str) -> dict | None:
    key = f"{league}_{date_str}_{home}_{away}"
    return _watch_score_cache.get(key)


def _watch_cache_fresh(entry: dict) -> bool:
    """Final scores are permanent; live scores refresh every 45 s."""
    if not entry.get("live"):
        return True
    return (time.monotonic() - entry.get("_ts", 0.0)) < 45.0


def _cache_watch_score(home: str, away: str, date_str: str, league: str, result, *, live: bool = False) -> dict:
    entry = {
        "score": result.score,
        "tension": result.tension_score,
        "flow": result.flow_score,
        "clutch": result.clutch_score,
        "overtime": result.overtime,
        "buzzer_beater": result.buzzer_beater,
        "live": live,
        "_ts": time.monotonic(),
    }
    key = f"{league}_{date_str}_{home}_{away}"
    _watch_score_cache[key] = entry
    return entry


def _entry_from_db_row(row: "WatchScoreCacheDB") -> dict:
    """A persisted final score reads back as a fresh, non-live cache entry."""
    return {
        "score": row.score,
        "tension": row.tension,
        "flow": row.flow,
        "clutch": row.clutch,
        "overtime": bool(row.overtime),
        "buzzer_beater": bool(row.buzzer_beater),
        "live": False,
        "_ts": time.monotonic(),
    }


def _load_persisted_watch_scores(league: str, date_str: str) -> dict[str, dict]:
    """All persisted final scores for one (league, date), keyed "HOME_AWAY".

    One query, so seeding a whole date is a single round-trip. Also warms the
    in-memory cache so repeat requests skip the DB entirely.
    """
    if engine is None:
        return {}
    out: dict[str, dict] = {}
    try:
        with Session(engine) as session:
            rows = session.query(WatchScoreCacheDB).filter_by(
                league=league, date=date_str).all()
            for r in rows:
                entry = _entry_from_db_row(r)
                out[f"{r.home}_{r.away}"] = entry
                _watch_score_cache[f"{league}_{date_str}_{r.home}_{r.away}"] = entry
    except Exception as exc:
        log.warning("watch-scores: persisted load failed (%s/%s): %s", league, date_str, exc)
    return out


def _persist_watch_score(league: str, date_str: str, home: str, away: str, entry: dict) -> None:
    """Upsert a final score. No-op for live scores or when no DB is configured."""
    if engine is None or entry.get("live"):
        return
    key = f"{league}_{date_str}_{home}_{away}"
    try:
        with Session(engine) as session:
            row = session.get(WatchScoreCacheDB, key)
            if row is None:
                row = WatchScoreCacheDB(id=key, league=league, date=date_str,
                                        home=home, away=away)
                session.add(row)
            row.score         = entry.get("score")
            row.tension       = entry.get("tension")
            row.flow          = entry.get("flow")
            row.clutch        = entry.get("clutch")
            row.overtime      = bool(entry.get("overtime"))
            row.buzzer_beater = bool(entry.get("buzzer_beater"))
            row.computed_at   = datetime.now(timezone.utc)
            session.commit()
    except Exception as exc:
        log.warning("watch-scores: persist failed (%s): %s", key, exc)


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
    if cached and _watch_cache_fresh(cached):
        return cached
    result = calculate_watch_score_by_teams(
        home_abbr=home, away_abbr=away, date_yyyymmdd=game_date, league=league)
    if result is None:
        return {"error": "Game not found or play-by-play unavailable", "score": None}
    return _cache_watch_score(home, away, game_date, league, result)


@app.get("/watch-scores")
def watch_scores(date: str = "", league: str = "nba", tz: Optional[str] = None):
    """
    Batch: return watch scores for all finished games on a date for a league.
    Returns: { "scores": { "DEN_SA": { "score": 8.8, ... }, ... } }
    Cached in-memory — subsequent requests are instant.
    """
    from watch_score import calculate_watch_score_by_teams, calculate_watch_score_bsd
    from monitor import _eastern_today
    from concurrent.futures import ThreadPoolExecutor
    game_date = date if date else _eastern_today()
    recent = _watch_date_is_recent(game_date)

    # Use the SAME game source as the schedule feed (BSD-primary cascade), not
    # the legacy ESPN `get_games_for_date` — which no longer surfaces BSD games,
    # so BSD-sourced leagues (NBA, etc.) yielded zero games here and never got a
    # watch-score badge. Going through basketball_source also guarantees the
    # team abbreviations (and tz day-bucket) match the cards' lookup key exactly.
    import basketball_source as _bb
    try:
        events = _bb.get_basketball_games(
            league, date_iso=_yyyymmdd_to_iso(game_date), tz=tz)
    except Exception as exc:
        log.warning("watch-scores: basketball_source failed (%s/%s): %s — legacy fallback",
                    league, game_date, exc)
        events = get_games_for_date(game_date, league=league)

    # One round-trip seeds every persisted final score for the date (permanent,
    # survives restarts/deploys). Misses fall through to compute below.
    persisted = _load_persisted_watch_scores(league, game_date)

    results: dict[str, dict] = {}
    todo: list[tuple[str, str, str, str]] = []   # (home, away, gid, state) to compute

    for event in events:
        try:
            state = event.get("status", {}).get("type", {}).get("state", "")
            if state not in ("post", "in"):     # skip pre-game; score live + final
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
            # Fresh in-memory hit (covers live within 45 s and final).
            cached = _get_cached_watch_score(home_abbr, away_abbr, game_date, league)
            if cached and _watch_cache_fresh(cached):
                results[key] = cached
                continue
            # Persisted final score — permanent, only valid once the game is over.
            if state == "post" and key in persisted:
                results[key] = persisted[key]
                continue

            # Already-persisted scores are returned above. Only AUTO-compute
            # misses for recent days; older history stays read-only here and is
            # computed lazily per match — this is what stops a cold first run
            # from triggering a mass synchronous compute that OOM'd the box.
            if recent:
                gid = str(event.get("id") or "")
                todo.append((home_abbr, away_abbr, gid, state))
        except Exception as exc:
            log.warning("watch-scores entry failed: %s", exc)
            continue

    # Compute the misses in parallel — each calculate_* makes 2-3 BSD HTTP calls,
    # so this is I/O-bound and threads overlap the waits (serial was the dominant
    # cost on a cold date).
    def _compute(item: tuple[str, str, str, str]):
        home_abbr, away_abbr, gid, state = item
        try:
            # 365scores is the sole basketball source. The 365 play-by-play path
            # scores FINISHED games; live games are not scored here (there is no
            # live 365 watch-score path), so live drama badges aren't computed.
            if state == "post":
                result = calculate_watch_score_by_teams(
                    home_abbr=home_abbr, away_abbr=away_abbr,
                    date_yyyymmdd=game_date, league=league)
            else:
                result = None
        except Exception:
            result = None
        return (home_abbr, away_abbr, state, result)

    if todo:
        # Shared, bounded pool (NOT a fresh per-request pool) so concurrent
        # /watch-scores requests on a cold first run can't explode the thread /
        # connection count and OOM the box.
        for home_abbr, away_abbr, state, result in _WATCH_POOL.map(_compute, todo):
                if not result:
                    continue
                live = (state == "in")
                entry = _cache_watch_score(
                    home_abbr, away_abbr, game_date, league, result, live=live)
                results[f"{home_abbr}_{away_abbr}"] = entry
                if not live:                  # finished → persist permanently
                    _persist_watch_score(league, game_date, home_abbr, away_abbr, entry)

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
def game_summary(gameId: str, date: Optional[str] = None, league: Optional[str] = None):
    roster = fetch_game_summary_roster(gameId, date=date, league=league)

    # Attach live boxscore (player stats) to the response.
    # fetch_player_boxscore is cached 55s so this adds negligible overhead.
    boxscore_data: list[dict] | None = None
    try:
        from score365_adapter import fetch_player_boxscore as _fetch_bs
        raw = _fetch_bs(gameId)
        log.info("game-summary: boxscore for %s: is_truthy=%s", gameId, bool(raw))
        if raw:
            stat_names = ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PF", "PTS", "3PM"]
            boxscore_data = []
            for section in raw.get("boxscore", {}).get("players", []):
                team_id = section.get("team", {}).get("id", "")
                players_out = []
                for ath in section.get("statistics", [{}])[0].get("athletes", []):
                    vals = ath.get("stats", [])
                    stats_dict = dict(zip(stat_names, vals)) if len(vals) == len(stat_names) else {}
                    players_out.append({
                        "id":      ath.get("athlete", {}).get("id", ""),
                        "name":    ath.get("athlete", {}).get("displayName", ""),
                        "starter": ath.get("starter", False),
                        "stats":   stats_dict,
                    })
                boxscore_data.append({"team_id": team_id, "players": players_out})
    except Exception as exc:
        log.warning("game-summary: boxscore fetch EXCEPTION for %s: %r", gameId, exc)

    roster["boxscore"] = boxscore_data
    return roster


@app.get("/all-teams-and-players")
def all_teams_and_players():
    return {"teams": get_all_teams()}


@app.get("/search")
def search_endpoint(q: str = "", limit: int = 10):
    """Free-text search across teams, leagues, and players.

    Used by the iOS top-bar search overlay. Scores in-process against
    the basketball roster cache and the soccer team accumulator (which
    is seeded as soccer matches are fetched). Static league catalog is
    always searchable regardless of cache state.
    """
    import search_index as _si
    limit = max(1, min(20, int(limit)))
    return _si.search(q or "", limit=limit)


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
        "soccer_alarms": [],
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
                            "home_logo":       _team_logo_for_abbr(r.home_team_id or ""),
                            "away_logo":       _team_logo_for_abbr(r.away_team_id or ""),
                            "league":          (getattr(r, 'league', None) or "nba"),
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
                        _with_team_name_he({
                            "team_id":        t.team_id,
                            "team_name":      t.team_name or "",
                            "max_diff":       t.score_gap or 5,
                            "start_quarter":  t.start_quarter or 4,
                            "alert_type":     t.alert_type or "silent",
                            "notify_at_start": getattr(t, 'notify_at_start', False),
                            "paused":          getattr(t, 'paused', False),
                        })
                        for t in team_rows
                    ]
                    if pref:
                        radar_cfg = {
                            "enabled":       pref.radar_enabled,
                            "max_diff":      pref.radar_gap,
                            "start_quarter": pref.radar_start_q,
                            "leagues":       _split_leagues(pref.radar_leagues),
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

        # v3 soccer alarms — best-effort; never break the basketball path
        soccer_alarms: list[dict] = []
        if engine is not None and user_token:
            try:
                with Session(engine) as session:
                    rows = (
                        session.query(SoccerAlarmDB)
                               .filter(SoccerAlarmDB.user_token == user_token,
                                       SoccerAlarmDB.is_active.is_(True))
                               .order_by(SoccerAlarmDB.created_at.desc())
                               .limit(50)
                               .all()
                    )
                    soccer_alarms = [
                        {
                            "id":             r.id,
                            "alarm_type":     r.alarm_type,
                            "match_id":       r.match_id,
                            "competition_id": r.competition_id,
                            "player_id":      r.player_id,
                            "threshold":      r.threshold,
                            "alert_type":     r.alert_type,
                            "is_active":      r.is_active,
                            "config":         r.config or {},
                            "fired_at":       r.fired_at.isoformat() if r.fired_at else None,
                        }
                        for r in rows
                    ]
            except Exception as soccer_err:
                log.warning("DB error fetching soccer alarms: %s", soccer_err)

        # v3 soccer favorites — best-effort; failure must not break basketball
        soccer_fav_players: list[dict] = []
        soccer_fav_teams: list[dict]   = []
        soccer_fav_comps: list[dict]   = []
        if engine is not None and user_token:
            try:
                with Session(engine) as session:
                    p_rows = (session.query(SoccerFavoritePlayerDB)
                                     .filter(SoccerFavoritePlayerDB.user_token == user_token)
                                     .all())
                    soccer_fav_players = [{
                        "player_id":   r.player_id,
                        "player_name": r.player_name,
                        "team_id":     r.team_id,
                        "config":      r.config or {},
                        "alert_type":  r.alert_type,
                        "paused":      r.paused,
                    } for r in p_rows]

                    t_rows = (session.query(SoccerFavoriteTeamDB)
                                     .filter(SoccerFavoriteTeamDB.user_token == user_token)
                                     .all())
                    soccer_fav_teams = [{
                        "team_id":           r.team_id,
                        "team_name":         r.team_name,
                        "competition_id":    r.competition_id,
                        "drama_threshold":   r.drama_threshold,
                        "alert_type":        r.alert_type,
                        "paused":            r.paused,
                        "notify_at_kickoff": r.notify_at_kickoff,
                    } for r in t_rows]

                    c_rows = (session.query(SoccerFavoriteCompetitionDB)
                                     .filter(SoccerFavoriteCompetitionDB.user_token == user_token)
                                     .all())
                    soccer_fav_comps = [{
                        "competition_id":    r.competition_id,
                        "drama_threshold":   r.drama_threshold,
                        "alert_type":        getattr(r, "alert_type", None) or "silent",
                        "notify_at_kickoff": bool(getattr(r, "notify_at_kickoff", False)),
                        "paused":            bool(getattr(r, "paused", False)),
                    } for r in c_rows]
            except Exception as fav_err:
                log.warning("DB error fetching soccer favorites: %s", fav_err)

        return {
            "game_alarms":   game_alarms,
            "player_alarms": player_alarms,
            "db_alarms":     db_alarms,
            "soccer_alarms": soccer_alarms,
            "soccer_favorite_players":      soccer_fav_players,
            "soccer_favorite_teams":        soccer_fav_teams,
            "soccer_favorite_competitions": soccer_fav_comps,
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

@app.post("/settings/favorites/pause-all")
def pause_all_favorites(req: PauseRequest):
    """Pause or resume ALL favorite teams and players for a user at once."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    with Session(engine) as session:
        teams = session.query(FavoriteTeamDB).filter_by(user_token=req.user_token).all()
        for t in teams:
            t.paused = req.paused
        players = session.query(FavoritePlayerDB).filter_by(user_token=req.user_token).all()
        for p in players:
            p.paused = req.paused
        session.commit()
    return {"paused": req.paused, "teams": len(teams), "players": len(players)}


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
