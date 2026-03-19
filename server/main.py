import os
import asyncio
import threading
import requests as http
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from sqlalchemy import create_engine, Column, Integer, String, Boolean, text
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
)


# ── Database Setup ─────────────────────────────────────────────────────────────

_raw_db_url = os.environ.get("DATABASE_URL", "")
# Railway provides postgres:// but SQLAlchemy 1.4+ requires postgresql://
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url else ""

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None


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
        print("DB: No DATABASE_URL set – skipping table creation.", flush=True)
        return
    try:
        _Base.metadata.create_all(bind=engine)
        print("DB: Tables ready.", flush=True)
    except Exception as exc:
        print(f"DB: Failed to create tables – {exc}", flush=True)
        return
    # Migrations: each statement gets its own connection+transaction so a
    # "column already exists" failure on one doesn't abort the others.
    migrations = [
        "ALTER TABLE alarms ADD COLUMN target_name   VARCHAR",
        "ALTER TABLE alarms ADD COLUMN start_quarter INTEGER",
        "ALTER TABLE favorites RENAME COLUMN hot_start_pts TO early_pts",
        "ALTER TABLE favorites RENAME COLUMN hot_start_q   TO early_pts_q",
        "ALTER TABLE user_preferences ADD COLUMN hist_alert_type VARCHAR NOT NULL DEFAULT 'silent'",
        "ALTER TABLE favorites      ADD COLUMN alert_type VARCHAR DEFAULT 'silent'",
        "ALTER TABLE favorite_teams ADD COLUMN alert_type VARCHAR DEFAULT 'silent'",
    ]
    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
            print(f"DB: Migration applied – {sql}", flush=True)
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
            if rows:
                print(f"DB: deactivated {len(rows)} alarm(s) for target {target_id}", flush=True)
    except Exception as exc:
        print(f"DB: deactivation failed for {target_id}: {exc}", flush=True)


# Register the callback so monitor.py threads can deactivate DB records after firing
register_db_deactivate(_deactivate_db_alarm)


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
        print(
            f"DB: Restored {len(fav_players)} players, {len(fav_teams)} teams "
            f"for ...{user_token[-10:]}",
            flush=True,
        )
    except Exception as exc:
        print(f"DB: _restore_prefs_from_db failed – {exc}", flush=True)


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


def _check_and_fire(alarm: Alarm, events: list[dict]) -> bool:
    """Return True if the alarm condition is met against the live scoreboard."""
    if alarm.condition_type == "score_diff":
        required_quarter = alarm.start_quarter or 4   # default: only fire from Q4
        for event in events:
            if str(event.get("id")) != str(alarm.target_id):
                continue
            state  = _get_event_state(event)
            period = _get_event_period(event)
            # Must be live AND have reached the target quarter
            if state != "in":
                print(f"Poller: score_diff alarm {alarm.id} – game not live (state={state}), skipping.", flush=True)
                continue
            if period < required_quarter:
                print(f"Poller: score_diff alarm {alarm.id} – Q{period} < Q{required_quarter} target, skipping.", flush=True)
                continue
            diff = _extract_game_diff(event)
            if diff is not None and diff <= alarm.threshold:
                return True

    elif alarm.condition_type == "points":
        # start_quarter > 0 means "only fire if still in that quarter or earlier"
        # start_quarter == 0 / None means no quarter limit (pts_goal)
        quarter_limit = alarm.start_quarter or 0
        for event in events:
            period = _get_event_period(event)
            if quarter_limit > 0 and period > quarter_limit:
                print(
                    f"Poller: points alarm {alarm.id} ({alarm.target_name}) – "
                    f"Q{period} exceeded Q{quarter_limit} limit, alarm expired.",
                    flush=True,
                )
                return False   # quarter has passed – condition can never be met now
            pts = _extract_player_pts(event, alarm.target_id)
            if pts is not None and pts >= alarm.threshold:
                return True

    elif alarm.condition_type == "triple_double":
        # Fire when the player has 8+ in at least 3 stat categories (near TD)
        for event in events:
            stats = _extract_player_stats(event, alarm.target_id)
            if stats:
                near_double = sum(1 for v in stats.values() if v >= 8)
                if near_double >= 3:
                    return True

    return False


async def _alarm_poller() -> None:
    """
    Every 60 s:
      1. Fetch the ESPN scoreboard once.
      2. Load all is_active alarms from DB.
      3. Check each alarm; fire + deactivate on match.
    """
    while True:
        await asyncio.sleep(60)
        if engine is None:
            continue
        try:
            data = await asyncio.to_thread(_fetch_scoreboard)
            events: list[dict] = data.get("events", [])

            with Session(engine) as session:
                alarms = (
                    session.query(Alarm)
                           .filter(Alarm.is_active.is_(True))
                           .all()
                )

            # Snapshot active in-memory alarms so we can skip DB records that
            # already have a dedicated monitoring thread watching them.
            active_game_ids   = {a["game_id"]   for a in get_active_alarms()}
            active_player_ids = {a["player_id"] for a in get_active_player_alarms()}

            for alarm in alarms:
                try:
                    # Skip if a monitoring thread is already watching this target —
                    # that thread will deactivate the DB record when it fires.
                    if alarm.condition_type == "score_diff" and alarm.target_id in active_game_ids:
                        continue
                    if alarm.condition_type in ("points", "triple_double") and alarm.target_id in active_player_ids:
                        continue

                    if _check_and_fire(alarm, events):
                        _send_push_notification(
                            push_token=alarm.user_token,
                            title="NBA Alarm",
                            body=f"Condition met for {alarm.target_name or alarm.target_id}!",
                            sound_channel="classic_alarm",
                        )
                        with Session(engine) as session:
                            db_row = session.get(Alarm, alarm.id)
                            if db_row:
                                db_row.is_active = False
                                session.commit()
                        print(
                            f"Poller: fired + deactivated alarm {alarm.id} "
                            f"({alarm.condition_type} / {alarm.target_id})",
                            flush=True,
                        )
                except Exception as alarm_err:
                    print(f"Poller: error processing alarm {alarm.id} – {alarm_err}", flush=True)

            # NOTE: Favorite players and teams are monitored exclusively by the
            # background radar (monitor.py _run_radar_tick) which has its own
            # per-day fire-once deduplication.  Checking them here too caused
            # double notifications (separate dedup sets).  Do not re-add.

        except Exception as exc:
            print(f"Poller: cycle error – {exc}", flush=True)


# ── App Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Startup ──
    _init_db()
    clear_stale_state()
    threading.Thread(target=update_all_rosters, daemon=True).start()
    token, channel = get_saved_push_token()
    if token:
        _restore_prefs_from_db(token)   # rebuild JSON cache from PostgreSQL
        threading.Thread(target=start_radar_job, args=(token, channel), daemon=True).start()
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
        # Remove any existing active alarm for this user + target + condition
        # so the active screen never shows duplicates
        session.query(Alarm).filter(
            Alarm.user_token     == req.user_token,
            Alarm.target_id      == req.target_id,
            Alarm.condition_type == condition_type,
            Alarm.is_active.is_(True),
        ).delete()
        alarm = Alarm(
            user_token=req.user_token,
            target_id=req.target_id,
            target_name=req.target_name or req.target_id,
            condition_type=condition_type,
            threshold=req.threshold,
            start_quarter=req.start_quarter,
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
    threading.Thread(
        target=start_monitoring_job,
        args=(req.game_id, req.game_name, req.max_diff, req.start_quarter,
              req.push_token, req.sound_channel, req.alert_type),
        daemon=True,
    ).start()
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
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
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
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
    token, channel = get_saved_push_token()
    if token:
        threading.Thread(target=start_radar_job, args=(token, channel), daemon=True).start()


@app.post("/start-radar")
def start_radar(req: RadarRequest):
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
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
        print(f"DB: /settings read error – {exc}", flush=True)
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
            print(f"DB: update_global_radar failed – {exc}", flush=True)
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
            print(f"DB: Upserted favorite team {req.team_name}", flush=True)
        except Exception as exc:
            print(f"DB: upsert_team failed – {exc}", flush=True)
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
            print(f"DB: remove_team failed – {exc}", flush=True)
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
            print(f"DB: Upserted favorite player {req.name}", flush=True)
        except Exception as exc:
            print(f"DB: upsert_player failed – {exc}", flush=True)
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
            print(f"DB: remove_player failed – {exc}", flush=True)
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
            print(f"DB: update_historical failed – {exc}", flush=True)
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


@app.post("/register-token")
def register_token(req: RegisterTokenRequest):
    if not req.push_token:
        return {"status": "error", "detail": "empty push_token"}
    print(f"DEBUG: /register-token  token='{req.push_token[:20]}...'  channel='{req.sound_channel}'", flush=True)
    save_push_token(req.push_token, req.sound_channel)
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
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
        prefs     = get_preferences()
        radar_cfg = prefs.get("GlobalGameRadar", {})

        # Filter in-memory alarms by token when one is provided
        game_alarms   = get_active_alarms()
        player_alarms = get_active_player_alarms()
        if user_token:
            game_alarms   = [a for a in game_alarms   if a.get("push_token") == user_token]
            player_alarms = [a for a in player_alarms if a.get("push_token") == user_token]

        # DB alarms filtered by token
        db_alarms: list[dict] = []
        if engine is not None and user_token:
            try:
                with Session(engine) as session:
                    rows = (
                        session.query(Alarm)
                               .filter(Alarm.user_token == user_token, Alarm.is_active.is_(True))
                               .all()
                    )
                    print(f"DEBUG: Found {len(rows)} rows in DB for user ...{user_token[-10:]}", flush=True)
                    for r in rows:
                        print(f"DEBUG: alarm id={r.id} type={r.condition_type} target={r.target_name} active={r.is_active}", flush=True)
                    # Build dicts INSIDE the session so attributes are never detached
                    db_alarms = [
                        {
                            "alarm_id":       r.id,
                            "target_id":      r.target_id,
                            "target_name":    r.target_name or r.target_id,
                            "condition_type": r.condition_type,
                            "threshold":      r.threshold,
                            "start_quarter":  r.start_quarter if r.start_quarter is not None else 4,
                        }
                        for r in rows
                    ]
                print(f"DB: Returning {len(db_alarms)} active alarms for token ...{user_token[-10:]}", flush=True)
            except Exception as db_err:
                print(f"CRITICAL DB ERROR in active_monitors query: {db_err}", flush=True)
                db_alarms = []   # degrade gracefully – don't 500

        return {
            "game_alarms":   game_alarms,
            "player_alarms": player_alarms,
            "db_alarms":     db_alarms,
            "global_radar": {
                "enabled":       radar_cfg.get("enabled", False),
                "running":       get_radar_status(),
                "max_diff":      radar_cfg.get("max_diff", 5),
                "start_quarter": radar_cfg.get("start_quarter", 4),
            },
            "favorite_teams":   prefs.get("FavoriteTeams", []),
            "favorite_players": prefs.get("FavoritePlayers", {}),
            "historical_mode":  prefs.get("HistoricalMode", {
                "enabled": False,
                "categories": {
                    "scoring_40_half":    False,
                    "threes_11":          False,
                    "quad_double_threat": False,
                },
            }),
        }

    except Exception as e:
        print(f"CRITICAL SERVER ERROR in active_monitors: {e}", flush=True)
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
        session.delete(alarm)
        session.commit()
    print(f"DB: Alarm {alarm_id} permanently deleted for token ...{user_token[-10:]}", flush=True)
    return {"deleted": True, "alarm_id": alarm_id}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
