import os
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
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

app = FastAPI(title="NBA Smart Alarm Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    """Pre-load rosters and auto-start the background radar on server start."""
    # Always clear ghost monitors/career-high cache from any previous deployment
    clear_stale_state()
    threading.Thread(target=update_all_rosters, daemon=True).start()
    token, channel = get_saved_push_token()
    if token:
        threading.Thread(target=start_radar_job, args=(token, channel), daemon=True).start()


# ── Game Alarm ────────────────────────────────────────────────────────────────

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
    # Also ensure the radar is running (singleton – safe to call repeatedly)
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


# ── Player Alarm ──────────────────────────────────────────────────────────────

class PlayerAlarmRequest(BaseModel):
    game_id: str
    game_name: str
    player_id: str
    player_name: str
    push_token: str
    sound_channel: str    = "classic_alarm"
    condition: str        = "pts_goal"   # "pts_goal" | "pts_early" | "triple_double"
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
    # Also ensure the radar is running (singleton – safe to call repeatedly)
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


# ── Test Endpoints ────────────────────────────────────────────────────────────

class TestAlarmRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"


@app.post("/test-alarm")
def test_alarm(req: TestAlarmRequest):
    """Immediately fire a test push notification."""
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
    condition: str    = "triple_double",
    player_name: str  = "Test Player",
    pts_threshold: int = 20,
    sound_channel: str = "classic_alarm",
):
    """
    Start a player monitoring job in test_mode using mock stats.
    Fires immediately – no live game needed.
    Mock stats: PTS=25, REB=9, AST=9, STL=2, BLK=9
    """
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


# ── Background Radar ──────────────────────────────────────────────────────────

class RadarRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"


@app.post("/start-radar")
def start_radar(req: RadarRequest):
    """Start the background radar (singleton – safe to call multiple times)."""
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
    return {"status": "radar_started"}


@app.get("/radar-status")
def radar_status():
    return {"running": get_radar_status()}


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/settings")
def get_settings():
    """Return the full user preferences object."""
    return get_preferences()


# ── Global Game Radar settings ───────────────────────────────────────────────

class GlobalRadarSettings(BaseModel):
    enabled: bool
    max_diff: int      = 5
    start_quarter: int = 4
    alert_type: str    = "silent"


def _maybe_start_radar() -> None:
    """Start the radar if a push token is saved (safe to call when radar is already running)."""
    token, channel = get_saved_push_token()
    if token:
        threading.Thread(target=start_radar_job, args=(token, channel), daemon=True).start()


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


# ── Favorite Teams settings ───────────────────────────────────────────────────

class FavoriteTeam(BaseModel):
    team_id: str
    team_name: str
    max_diff: int      = 5
    start_quarter: int = 4
    alert_type: str    = "silent"


@app.post("/settings/teams")
def upsert_team(req: FavoriteTeam):
    """Add or update a favorite team."""
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
    _maybe_start_radar()  # favorite team added → radar needed
    return {"status": "updated", "FavoriteTeams": teams}


@app.delete("/settings/teams/{team_id}")
def remove_team(team_id: str):
    """Remove a favorite team."""
    prefs = get_preferences()
    prefs["FavoriteTeams"] = [t for t in prefs.get("FavoriteTeams", []) if t["team_id"] != team_id]
    save_preferences(prefs)
    return {"status": "removed", "FavoriteTeams": prefs["FavoriteTeams"]}


# ── Favorite Players settings ─────────────────────────────────────────────────

class FavoritePlayer(BaseModel):
    player_id: str
    name: str
    alert_type: str = "silent"


@app.post("/settings/players")
def upsert_player(req: FavoritePlayer):
    """Add or update a favorite player."""
    print(f"DEBUG: Received player from app: {req.name} (id={req.player_id})", flush=True)
    prefs = get_preferences()
    players = [p for p in prefs.get("FavoritePlayers", []) if p["player_id"] != req.player_id]
    players.append({
        "player_id":  req.player_id,
        "name":       req.name,
        "alert_type": req.alert_type,
    })
    prefs["FavoritePlayers"] = players
    save_preferences(prefs)
    _maybe_start_radar()  # favorite player added → radar needed
    return {"status": "updated", "FavoritePlayers": players}


@app.delete("/settings/players/{player_id}")
def remove_player(player_id: str):
    """Remove a favorite player."""
    prefs = get_preferences()
    prefs["FavoritePlayers"] = [p for p in prefs.get("FavoritePlayers", []) if p["player_id"] != player_id]
    save_preferences(prefs)
    return {"status": "removed", "FavoritePlayers": prefs["FavoritePlayers"]}


# ── Historical Mode settings ──────────────────────────────────────────────────

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
        _maybe_start_radar()  # historical mode enabled → radar needed
    return {"status": "updated", "HistoricalMode": prefs["HistoricalMode"]}


# ── Data Endpoints ────────────────────────────────────────────────────────────

@app.get("/games")
def games():
    """Return today's NBA games."""
    return {"games": get_today_games()}


@app.get("/roster")
def roster(game_id: str):
    """Return both teams' rosters for a game (legacy endpoint)."""
    return fetch_game_roster(game_id)


@app.get("/game-summary")
def game_summary(gameId: str):
    """Return cached rosters for both teams in the given game."""
    return fetch_game_summary_roster(gameId)


@app.get("/all-teams-and-players")
def all_teams_and_players():
    """Return all 30 cached NBA teams with their full rosters."""
    return {"teams": get_all_teams()}


@app.get("/refresh-rosters")
def refresh_rosters():
    """Manually trigger a fresh fetch of all 30 NBA team rosters."""
    threading.Thread(target=update_all_rosters, daemon=True).start()
    return {"status": "roster_refresh_started"}


@app.get("/active-monitors")
def active_monitors():
    """Return a full snapshot of everything the server is currently watching."""
    prefs = get_preferences()
    radar_cfg = prefs.get("GlobalGameRadar", {})
    return {
        "game_alarms":    get_active_alarms(),
        "player_alarms":  get_active_player_alarms(),
        "global_radar": {
            "enabled":       radar_cfg.get("enabled", False),
            "running":       get_radar_status(),
            "max_diff":      radar_cfg.get("max_diff", 5),
            "start_quarter": radar_cfg.get("start_quarter", 4),
        },
        "favorite_teams":   prefs.get("FavoriteTeams", []),
        "favorite_players": prefs.get("FavoritePlayers", []),
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
    """Cancel an active manual game alarm."""
    cancelled = cancel_game_alarm(game_id)
    return {"cancelled": cancelled, "game_id": game_id}


@app.delete("/active-monitors/player/{player_id}")
def cancel_player(player_id: str):
    """Cancel an active manual player alarm."""
    cancelled = cancel_player_alarm(player_id)
    return {"cancelled": cancelled, "player_id": player_id}


class GlobalThresholdsRequest(BaseModel):
    pts_threshold:       int  = 40
    triple_double_watch: bool = True
    pts_per_quarter:     int  = 15


@app.post("/settings/thresholds")
def update_thresholds(req: GlobalThresholdsRequest):
    """Save global player-alert thresholds used by the radar for all favorite players."""
    prefs = get_preferences()
    prefs["GlobalThresholds"] = {
        "pts_threshold":       req.pts_threshold,
        "triple_double_watch": req.triple_double_watch,
        "pts_per_quarter":     req.pts_per_quarter,
    }
    save_preferences(prefs)
    print(f"DEBUG: GlobalThresholds updated → pts={req.pts_threshold}, td={req.triple_double_watch}, ppq={req.pts_per_quarter}", flush=True)
    return {"status": "updated", "GlobalThresholds": prefs["GlobalThresholds"]}


class RegisterTokenRequest(BaseModel):
    push_token: str
    sound_channel: str = "classic_alarm"


@app.post("/register-token")
def register_token(req: RegisterTokenRequest):
    """
    Called by the app on startup to register the push token with the server.
    This ensures the radar can send notifications even before any alarm is set.
    """
    print(f"DEBUG: /register-token hit. token='{req.push_token[:20]}...' channel='{req.sound_channel}'", flush=True)
    if not req.push_token:
        print("DEBUG: /register-token rejected – empty push_token", flush=True)
        return {"status": "error", "detail": "empty push_token"}
    save_push_token(req.push_token, req.sound_channel)
    print(f"DEBUG: Push Token saved successfully: {req.push_token}", flush=True)
    # Kick off the radar now that we have a token (singleton – safe to call repeatedly)
    threading.Thread(
        target=start_radar_job,
        args=(req.push_token, req.sound_channel),
        daemon=True,
    ).start()
    return {"status": "token_registered", "push_token": req.push_token}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
