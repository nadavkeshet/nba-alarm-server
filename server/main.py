import os
import asyncio
import threading
import requests as http
from contextlib import asynccontextmanager

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
)


# ── Database Setup ─────────────────────────────────────────────────────────────

_raw_db_url = os.environ.get("DATABASE_URL", "")
# Railway provides postgres:// but SQLAlchemy 1.4+ requires postgresql://
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url else ""

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None


class _Base(DeclarativeBase):
    pass


class Alarm(_Base):
    """Persistent alarms stored in PostgreSQL."""
    __tablename__ = "alarms"

    id             = Column(Integer, primary_key=True, index=True)
    user_token     = Column(String,  nullable=False)   # Expo push token
    target_id      = Column(String,  nullable=False)   # player_id or game_id
    condition_type = Column(String,  nullable=False)   # "points" | "score_diff" | "triple_double"
    threshold      = Column(Integer, nullable=False)
    is_active      = Column(Boolean, default=True, nullable=False)


def _init_db() -> None:
    """Create tables if they don't already exist."""
    if engine is None:
        print("DB: No DATABASE_URL set – skipping table creation.", flush=True)
        return
    try:
        _Base.metadata.create_all(bind=engine)
        print("DB: Tables ready.", flush=True)
    except Exception as exc:
        print(f"DB: Failed to create tables – {exc}", flush=True)


# ── Background Async Poller ────────────────────────────────────────────────────

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)


def _fetch_scoreboard() -> dict:
    """Blocking ESPN fetch – called from a thread executor."""
    resp = http.get(ESPN_SCOREBOARD, timeout=10)
    resp.raise_for_status()
    return resp.json()


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
        for event in events:
            if str(event.get("id")) == str(alarm.target_id):
                diff = _extract_game_diff(event)
                if diff is not None and diff <= alarm.threshold:
                    return True

    elif alarm.condition_type == "points":
        for event in events:
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

            for alarm in alarms:
                try:
                    if _check_and_fire(alarm, events):
                        _send_push_notification(
                            push_token=alarm.user_token,
                            title="NBA Alarm",
                            body=f"Condition met for target {alarm.target_id}!",
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
    condition_type: str   # "points" | "score_diff" | "triple_double"
    threshold:      int


@app.post("/set_alarm")
def set_alarm(req: SetAlarmRequest):
    """Persist a new alarm to PostgreSQL."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database not configured.")
    if req.condition_type not in ("points", "score_diff", "triple_double"):
        raise HTTPException(
            status_code=400,
            detail="condition_type must be 'points', 'score_diff', or 'triple_double'.",
        )
    with Session(engine) as session:
        alarm = Alarm(
            user_token=req.user_token,
            target_id=req.target_id,
            condition_type=req.condition_type,
            threshold=req.threshold,
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
def get_settings():
    return get_preferences()


class GlobalRadarSettings(BaseModel):
    enabled: bool
    max_diff: int      = 5
    start_quarter: int = 4
    alert_type: str    = "silent"


@app.post("/settings/global-radar")
def update_global_radar(req: GlobalRadarSettings):
    prefs = get_preferences()
    prefs["GlobalGameRadar"] = {
        "enabled":       req.enabled,
        "max_diff":      req.max_diff,
        "start_quarter": req.start_quarter,
        "alert_type":    req.alert_type,
    }
    save_preferences(prefs)
    if req.enabled:
        _maybe_start_radar()
    return {"status": "updated", "GlobalGameRadar": prefs["GlobalGameRadar"]}


class FavoriteTeam(BaseModel):
    team_id: str
    team_name: str
    max_diff: int      = 5
    start_quarter: int = 4
    alert_type: str    = "silent"


@app.post("/settings/teams")
def upsert_team(req: FavoriteTeam):
    prefs = get_preferences()
    teams = [t for t in prefs.get("FavoriteTeams", []) if t["team_id"] != req.team_id]
    teams.append({
        "team_id":       req.team_id,
        "team_name":     req.team_name,
        "max_diff":      req.max_diff,
        "start_quarter": req.start_quarter,
        "alert_type":    req.alert_type,
    })
    prefs["FavoriteTeams"] = teams
    save_preferences(prefs)
    _maybe_start_radar()
    return {"status": "updated", "FavoriteTeams": teams}


@app.delete("/settings/teams/{team_id}")
def remove_team(team_id: str):
    prefs = get_preferences()
    prefs["FavoriteTeams"] = [t for t in prefs.get("FavoriteTeams", []) if t["team_id"] != team_id]
    save_preferences(prefs)
    return {"status": "removed", "FavoriteTeams": prefs["FavoriteTeams"]}


class FavoritePlayer(BaseModel):
    player_id:     str
    name:          str
    total_pts:     int  = 40
    hot_start_q:   int  = 1
    hot_start_pts: int  = 15
    td_watch:      bool = True
    alert_type:    str  = "silent"


@app.post("/settings/players")
def upsert_player(req: FavoritePlayer):
    print(
        f"DEBUG: Player upsert: {req.name} (id={req.player_id}, "
        f"total_pts={req.total_pts}, hot_start_q={req.hot_start_q}, "
        f"hot_start_pts={req.hot_start_pts}, td_watch={req.td_watch})",
        flush=True,
    )
    prefs = get_preferences()
    players: dict = prefs.get("FavoritePlayers", {})
    players[req.player_id] = {
        "name":          req.name,
        "total_pts":     req.total_pts,
        "hot_start_q":   req.hot_start_q,
        "hot_start_pts": req.hot_start_pts,
        "td_watch":      req.td_watch,
        "alert_type":    req.alert_type,
    }
    prefs["FavoritePlayers"] = players
    save_preferences(prefs)
    _maybe_start_radar()
    return {"status": "updated", "FavoritePlayers": players}


@app.delete("/settings/players/{player_id}")
def remove_player(player_id: str):
    prefs = get_preferences()
    players: dict = prefs.get("FavoritePlayers", {})
    players.pop(player_id, None)
    prefs["FavoritePlayers"] = players
    save_preferences(prefs)
    return {"status": "removed", "FavoritePlayers": players}


class HistoricalCategories(BaseModel):
    scoring_40_half:    bool = False
    threes_11:          bool = False
    quad_double_threat: bool = False


class HistoricalSettings(BaseModel):
    enabled: bool
    categories: HistoricalCategories = HistoricalCategories()
    alert_type: str                  = "silent"


@app.post("/settings/historical")
def update_historical(req: HistoricalSettings):
    prefs = get_preferences()
    prefs["HistoricalMode"] = {
        "enabled": req.enabled,
        "alert_type": req.alert_type,
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
def active_monitors():
    prefs = get_preferences()
    radar_cfg = prefs.get("GlobalGameRadar", {})
    return {
        "game_alarms":   get_active_alarms(),
        "player_alarms": get_active_player_alarms(),
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


@app.delete("/active-monitors/game/{game_id}")
def cancel_game(game_id: str):
    cancelled = cancel_game_alarm(game_id)
    return {"cancelled": cancelled, "game_id": game_id}


@app.delete("/active-monitors/player/{player_id}")
def cancel_player(player_id: str):
    cancelled = cancel_player_alarm(player_id)
    return {"cancelled": cancelled, "player_id": player_id}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
