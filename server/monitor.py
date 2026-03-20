"""
NBA Smart Alarm – Monitoring Logic
===================================

Alarm types:
  1. Manual Game Alarm   – point diff ≤ max_diff from start_quarter onward
  2. Manual Player Alarm – player hits a chosen stat milestone
  3. Global Radar        – background scan of every live game
  4. Favorite Teams      – per-team criteria overriding global radar
  5. Favorite Players    – career-high proximity alerts
  6. Historical Mode     – catches all-time-rare stat lines
"""

import os
import sys
import json
import time
import logging
import threading
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging as fb_messaging

# ── Firebase Admin SDK init (runs once at module import) ──────────────────────
def _init_firebase() -> bool:
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        logging.warning("FIREBASE_SERVICE_ACCOUNT_JSON not set – FCM push disabled.")
        return False
    try:
        sa_dict = json.loads(sa_json)
        cred = credentials.Certificate(sa_dict)
        firebase_admin.initialize_app(cred)
        logging.info("Firebase Admin SDK initialised.")
        return True
    except Exception as exc:
        logging.error("Firebase init failed: %s", exc)
        return False

_firebase_ready: bool = _init_firebase()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("nba_monitor")

POLL_INTERVAL = 60  # seconds between active-monitoring checks

# Mock stats used when test_mode=True (bypasses live ESPN fetch)
MOCK_STATS = {"PTS": 25, "REB": 9, "AST": 9, "STL": 2, "BLK": 9}

ESPN_NBA_URL          = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_SUMMARY_URL      = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
ESPN_TEAMS_URL        = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ESPN_ATHLETE_STATS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes"

# ---------------------------------------------------------------------------
# In-memory push token  (survives disk wipes on Render's ephemeral filesystem)
# ---------------------------------------------------------------------------
_MEM_PUSH_TOKEN:    str = ""
_MEM_SOUND_CHANNEL: str = "classic_alarm"

# ---------------------------------------------------------------------------
# Roster cache  (populated at startup, refreshed on demand)
# ---------------------------------------------------------------------------
# Maps team_id (str) → {id, name, abbreviation, logo, players: [...]}
_ROSTER_CACHE: dict[str, dict] = {}
_CACHE_LOCK   = threading.Lock()


def update_all_rosters() -> None:
    """
    ממלא את המחסן בכל השחקנים של 30 הקבוצות מ-ESPN.
    """
    log.info("Roster cache: starting full refresh...")

    # שלב 1: קבלת רשימת כל הקבוצות
    try:
        resp = requests.get(ESPN_TEAMS_URL, params={"limit": 32}, timeout=15)
        resp.raise_for_status()
        # מבנה ה-JSON של ESPN לרשימת קבוצות
        sports = resp.json().get("sports", [])
        leagues = sports[0].get("leagues", []) if sports else []
        team_entries = leagues[0].get("teams", []) if leagues else []
    except Exception as exc:
        log.warning("Roster cache: failed to fetch team list: %s", exc)
        return

    new_cache: dict[str, dict] = {}

    for entry in team_entries:
        team = entry.get("team", {})
        team_id = str(team.get("id", ""))
        team_abbr = team.get("abbreviation", "")

        if not team_id: continue

        try:
            # שלב 2: פנייה ישירה לדף הסגל (Roster) שבו ראינו את השחקנים
            # הכתובת הנכונה היא: .../teams/{id}/roster
            r = requests.get(f"{ESPN_TEAMS_URL}/{team_id}/roster", timeout=10)
            r.raise_for_status()
            data = r.json()

            # שליפת המערך athletes שראינו ב-JSON שלך
            athletes_list = data.get("athletes", [])

            players = []
            for a in athletes_list:
                players.append({
                    "id": str(a.get("id", "")),
                    "name": a.get("displayName", "?"),
                    "jersey": a.get("jersey", ""),
                    "position": a.get("position", {}).get("abbreviation", ""),
                })

            # שמירה ב-Cache לפי ה-ID של הקבוצה
            new_cache[team_id] = {
                "id": team_id,
                "name": team.get("displayName", ""),
                "abbreviation": team_abbr,
                "logo": team.get("logos", [{}])[0].get("href") if team.get("logos") else None,
                "players": players,
            }

            print(f"Successfully cached {len(players)} players for {team_abbr}", flush=True)
            time.sleep(0.15)  # מנוחה קצרה כדי לא להיחסם

        except Exception as exc:
            log.warning("Roster cache failed for team %s: %s", team_id, exc)

    with _CACHE_LOCK:
        _ROSTER_CACHE.clear()
        _ROSTER_CACHE.update(new_cache)

    log.info("Roster cache: refresh complete – %d teams cached.", len(_ROSTER_CACHE))


def get_all_teams() -> list[dict]:
    """Return all cached teams with their rosters, sorted alphabetically."""
    with _CACHE_LOCK:
        teams = list(_ROSTER_CACHE.values())
    teams.sort(key=lambda t: t.get("name", ""))
    return teams


# ---------------------------------------------------------------------------
# Persistent storage
# ---------------------------------------------------------------------------

_SERVER_DIR   = Path(os.getcwd())
_PREFS_FILE   = _SERVER_DIR / "user_preferences.json"
print(f"DEBUG: Data directory = {_SERVER_DIR}", flush=True)
_FILE_LOCK    = threading.Lock()

_DEFAULT_PREFS: dict = {
    "GlobalGameRadar": {
        "enabled":       False,
        "max_diff":      5,
        "start_quarter": 4,
    },
    "FavoriteTeams":   [],   # [{team_id, team_name, max_diff, start_quarter}]
    "FavoritePlayers": {},   # {player_id: {name, threshold, alert_type}}
    "GlobalThresholds": {
        "pts_threshold":        40,    # alert when fav player hits this many pts
        "triple_double_watch":  True,  # alert when fav player approaches a triple-double
        "pts_per_quarter":      15,    # alert when fav player has this many pts in Q1
    },
    "HistoricalMode":  {
        "enabled": False,
        "categories": {
            "scoring_40_half":    False,
            "threes_11":          False,
            "quad_double_threat": False,
        },
    },
}


def _load_json(path: Path, default: dict) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not load %s: %s", path.name, exc)
    return json.loads(json.dumps(default))  # deep copy of default


def _save_json(path: Path, data: dict) -> None:
    try:
        with _FILE_LOCK:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not save %s: %s", path.name, exc)


def get_preferences() -> dict:
    prefs = _load_json(_PREFS_FILE, _DEFAULT_PREFS)
    # Fill in any keys added after the file was first written
    for key, default_val in _DEFAULT_PREFS.items():
        if key not in prefs:
            prefs[key] = default_val
    # Migrate FavoritePlayers: list → dict (old v1 format)
    if isinstance(prefs.get("FavoritePlayers"), list):
        old_list = prefs["FavoritePlayers"]
        prefs["FavoritePlayers"] = {
            p["player_id"]: {
                "name":          p.get("name", "?"),
                "total_pts":     p.get("threshold", 40),
                "early_pts_q":   1,
                "early_pts": 15,
                "td_watch":      True,
                "alert_type":    p.get("alert_type", "silent"),
            }
            for p in old_list if "player_id" in p
        }
        save_preferences(prefs)
        log.info("Migrated FavoritePlayers list → dict (v2).")
    else:
        # Migrate dict entries that still use old "threshold" key (v1 dict format)
        changed = False
        for pid, p in prefs.get("FavoritePlayers", {}).items():
            if "threshold" in p and "total_pts" not in p:
                p["total_pts"]     = p.pop("threshold")
                p.setdefault("early_pts_q",   1)
                p.setdefault("early_pts", 15)
                p.setdefault("td_watch",      True)
                changed = True
        if changed:
            save_preferences(prefs)
            log.info("Migrated FavoritePlayers dict entries to v2 schema.")
    return prefs


def save_preferences(prefs: dict) -> None:
    _save_json(_PREFS_FILE, prefs)


def save_push_token(push_token: str, sound_channel: str = "classic_alarm") -> None:
    """Persist the push token both in-memory and on disk."""
    global _MEM_PUSH_TOKEN, _MEM_SOUND_CHANNEL
    if not push_token:
        return
    changed = _MEM_PUSH_TOKEN != push_token
    _MEM_PUSH_TOKEN    = push_token
    _MEM_SOUND_CHANNEL = sound_channel
    # Also write to disk (best-effort – may be wiped on Render restart)
    try:
        prefs = get_preferences()
        prefs["_push_token"]    = push_token
        prefs["_sound_channel"] = sound_channel
        save_preferences(prefs)
    except Exception as exc:
        log.warning("save_push_token: disk write failed: %s", exc)
    if changed:
        print(f"DEBUG: Push token saved (memory + disk): {push_token[:20]}...", flush=True)


def get_saved_push_token() -> tuple[str, str]:
    """Return (push_token, sound_channel) – memory first, disk fallback."""
    global _MEM_PUSH_TOKEN, _MEM_SOUND_CHANNEL
    if _MEM_PUSH_TOKEN:
        return _MEM_PUSH_TOKEN, _MEM_SOUND_CHANNEL
    # Memory empty (fresh process start) – try disk
    try:
        prefs = get_preferences()
        token   = prefs.get("_push_token", "")
        channel = prefs.get("_sound_channel", "classic_alarm")
        if token:
            # Warm up memory cache from disk
            _MEM_PUSH_TOKEN    = token
            _MEM_SOUND_CHANNEL = channel
        return token, channel
    except Exception:
        return "", "classic_alarm"


# ---------------------------------------------------------------------------
# Anti-spam: one alert per (key + calendar date UTC)
# ---------------------------------------------------------------------------

_FIRED_ALERTS: set[str] = set()
_FIRED_LOCK   = threading.Lock()

# ---------------------------------------------------------------------------
# DB deactivation callback  (set by main.py at startup to avoid circular import)
# ---------------------------------------------------------------------------

_DB_DEACTIVATE_FN = None


def register_db_deactivate(fn) -> None:
    """Register a callable(target_id: str) that marks DB alarms inactive after firing."""
    global _DB_DEACTIVATE_FN
    _DB_DEACTIVATE_FN = fn


def _deactivate_db_alarm_for(target_id: str) -> None:
    if _DB_DEACTIVATE_FN is not None:
        try:
            _DB_DEACTIVATE_FN(target_id)
        except Exception as exc:
            log.warning("DB alarm deactivation failed for %s: %s", target_id, exc)


def _fire_once(key: str) -> bool:
    """
    Returns True (and marks the key) the first time it is called for this key
    on the current UTC date.  Returns False on all subsequent calls today.
    """
    today    = datetime.now(timezone.utc).strftime("%Y%m%d")
    full_key = f"{today}_{key}"
    with _FIRED_LOCK:
        if full_key in _FIRED_ALERTS:
            return False
        _FIRED_ALERTS.add(full_key)
        return True


# ---------------------------------------------------------------------------
# Manual alarm registry  (radar skips games being monitored manually)
# ---------------------------------------------------------------------------

_active_manual_alarms: dict[str, dict] = {}                      # game_id → alarm info
_active_player_alarms: dict[tuple[str,str], dict] = {}           # (player_id, group) → alarm info
_alarm_stop_events:    dict[str, threading.Event] = {}
_player_stop_events:   dict[tuple[str,str], threading.Event] = {}
_manual_lock = threading.Lock()


def _player_condition_group(condition: str) -> str:
    """Each condition type gets its own thread slot — no two conditions ever share a key."""
    return condition


def _register_manual_alarm(
    game_id: str, game_name: str, max_diff: int, start_quarter: int,
    alert_type: str = "alarm",
) -> threading.Event:
    stop = threading.Event()
    with _manual_lock:
        # Cancel existing alarm for this game so the old thread exits cleanly
        existing = _alarm_stop_events.get(game_id)
        if existing:
            existing.set()
        _active_manual_alarms[game_id] = {
            "game_id":       game_id,
            "game_name":     game_name,
            "max_diff":      max_diff,
            "start_quarter": start_quarter,
            "alert_type":    alert_type,
            "status":        "scheduled",
            "started_at":    datetime.now(timezone.utc).isoformat(),
        }
        _alarm_stop_events[game_id] = stop
    return stop


def _register_player_alarm(
    game_id: str, game_name: str, player_id: str, player_name: str,
    condition: str, pts_threshold: int,
    alert_type: str = "alarm",
) -> threading.Event:
    stop = threading.Event()
    group = _player_condition_group(condition)
    key = (player_id, group)
    with _manual_lock:
        # Cancel existing alarm for same player+condition-group so the old thread exits cleanly
        existing = _player_stop_events.get(key)
        if existing:
            existing.set()
        _active_player_alarms[key] = {
            "game_id":       game_id,
            "game_name":     game_name,
            "player_id":     player_id,
            "player_name":   player_name,
            "condition":     condition,
            "pts_threshold": pts_threshold,
            "alert_type":    alert_type,
            "status":        "scheduled",
            "started_at":    datetime.now(timezone.utc).isoformat(),
        }
        _player_stop_events[key] = stop
    return stop


def _update_alarm_status(game_id: str, status: str) -> None:
    with _manual_lock:
        if game_id in _active_manual_alarms:
            _active_manual_alarms[game_id]["status"] = status


def _update_player_alarm_status(player_id: str, condition: str, status: str) -> None:
    key = (player_id, _player_condition_group(condition))
    with _manual_lock:
        if key in _active_player_alarms:
            _active_player_alarms[key]["status"] = status


def _unregister_manual_alarm(game_id: str) -> None:
    with _manual_lock:
        _active_manual_alarms.pop(game_id, None)
        _alarm_stop_events.pop(game_id, None)


def _unregister_player_alarm(player_id: str, condition: str) -> None:
    key = (player_id, _player_condition_group(condition))
    with _manual_lock:
        _active_player_alarms.pop(key, None)
        _player_stop_events.pop(key, None)


def _has_manual_alarm(game_id: str) -> bool:
    with _manual_lock:
        return game_id in _active_manual_alarms


def _has_manual_player_alarm(player_id: str) -> bool:
    with _manual_lock:
        return any(k[0] == player_id for k in _active_player_alarms)


def get_active_alarms() -> list[dict]:
    with _manual_lock:
        return list(_active_manual_alarms.values())


def get_active_player_alarms() -> list[dict]:
    with _manual_lock:
        return list(_active_player_alarms.values())


def cancel_game_alarm(game_id: str) -> bool:
    with _manual_lock:
        event = _alarm_stop_events.get(game_id)
        if event:
            event.set()
            return True
    return False


def cancel_player_alarm(player_id: str) -> bool:
    with _manual_lock:
        keys = [k for k in _player_stop_events if k[0] == player_id]
        if not keys:
            return False
        for k in keys:
            _player_stop_events[k].set()
        return True


def clear_stale_state() -> None:
    """
    Wipe all in-memory alarm state on server restart.
    This prevents ghost monitors from a previous deployment from showing up in logs.
    """
    with _manual_lock:
        # Signal any leftover threads to stop
        for ev in list(_alarm_stop_events.values()):
            ev.set()
        for ev in list(_player_stop_events.values()):
            ev.set()
        _active_manual_alarms.clear()
        _active_player_alarms.clear()
        _alarm_stop_events.clear()
        _player_stop_events.clear()

    print("STARTUP: Stale state cleared – active_monitors reset.", flush=True)


# ---------------------------------------------------------------------------
# NBA game helpers
# ---------------------------------------------------------------------------

def _fetch_nba_game(game_id: str) -> dict | None:
    """Return the raw ESPN event dict for game_id, or None on failure."""
    try:
        resp = requests.get(ESPN_NBA_URL, timeout=10)
        resp.raise_for_status()
        for event in resp.json().get("events", []):
            if str(event.get("id")) == str(game_id):
                return event
    except Exception as exc:
        log.warning("ESPN scoreboard fetch failed: %s", exc)
    return None


def _parse_nba_state(event: dict) -> tuple[int, int, str]:
    """
    Returns (quarter, point_diff, detail).
      quarter: 0 = pre-game, 1-4 = live, 9 = final
      point_diff: absolute score difference (999 when unknown)
    """
    try:
        competition = event["competitions"][0]
        status = competition["status"]
        state  = status["type"].get("state", "")
        detail = status["type"].get("detail", "")
        period = status.get("period", 0)

        if state == "pre":
            return 0, 999, detail
        if state == "post":
            return 9, 0, detail

        scores = [int(c.get("score", 0)) for c in competition.get("competitors", [])]
        diff = abs(scores[0] - scores[1]) if len(scores) >= 2 else 999
        return period, diff, detail

    except (KeyError, IndexError, ValueError) as exc:
        log.warning("State parse error: %s", exc)
        return 0, 999, "unknown"


def _parse_nba_start_time(event: dict) -> datetime | None:
    try:
        raw = event.get("date", "")
        if raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        log.warning("Date parse error: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Player / roster helpers
# ---------------------------------------------------------------------------

def _fetch_player_stats(game_id: str, player_id: str) -> dict | None:
    """
    Return a stat dict with keys PTS, REB, AST, STL, BLK, 3PM (and others)
    for the given player from the ESPN live box score, or None if unavailable.
    """
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={"event": game_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for section in data.get("boxscore", {}).get("players", []):
            for stats_group in section.get("statistics", []):
                names = stats_group.get("names", [])
                for entry in stats_group.get("athletes", []):
                    if str(entry.get("athlete", {}).get("id", "")) == str(player_id):
                        return dict(zip(names, entry.get("stats", [])))
    except Exception as exc:
        log.warning("Player stats fetch failed: %s", exc)
    return None


def fetch_game_summary_roster(game_id: str) -> dict:
    """
    Return cached rosters for both teams in game_id.

    Step 1 – identify the two teams:
      a) from the ESPN scoreboard (fast, works for today's live games)
      b) fallback: ESPN summary header (works for any valid game_id / date)

    Step 2 – serve each team from _ROSTER_CACHE.
      Cache miss (e.g., cache still warming up) → live fetch + populate cache.
    """
    # ── Step 1: collect raw team stubs ─────────────────────────────────────────
    team_stubs: list[dict] = []

    event = _fetch_nba_game(game_id)
    if event is not None:
        for comp in event.get("competitions", [{}])[0].get("competitors", []):
            team_stubs.append(comp.get("team", {}))

    # Fallback: summary header works for any game_id regardless of date
    if not team_stubs:
        log.info("Game %s not in scoreboard – trying summary header.", game_id)
        try:
            resp = requests.get(ESPN_SUMMARY_URL, params={"event": game_id}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for comp in (data.get("header", {})
                             .get("competitions", [{}])[0]
                             .get("competitors", [])):
                team_stubs.append(comp.get("team", {}))
        except Exception as exc:
            log.warning("Summary header fallback failed for game %s: %s", game_id, exc)

    if not team_stubs:
        log.warning("Game summary: could not identify teams for game %s.", game_id)
        return {"teams": []}

    # ── Step 2: serve from cache, live-fetch on miss ────────────────────────────
    teams_out: list[dict] = []

    for team in team_stubs:
        team_id = str(team.get("id", ""))
        if not team_id:
            continue

        with _CACHE_LOCK:
            cached = _ROSTER_CACHE.get(team_id)

        if cached:
            teams_out.append(cached)
            log.info("Cache hit: %s (%d players)", cached["abbreviation"], len(cached["players"]))
            continue

        # Cache miss — live fetch and populate cache for next time
        log.warning("Cache miss for team %s; fetching live.", team_id)
        try:
            r = requests.get(f"{ESPN_TEAMS_URL}/{team_id}/roster", timeout=10)
            r.raise_for_status()
            athletes = r.json().get("athletes", [])
            players = [
                {
                    "id":       str(a.get("id", "")),
                    "name":     a.get("displayName", "?"),
                    "jersey":   a.get("jersey", ""),
                    "position": a.get("position", {}).get("abbreviation", ""),
                }
                for a in athletes
            ]
            logos    = team.get("logos", [])
            logo_url = logos[0].get("href") if logos else team.get("logo")
            entry    = {
                "id":           team_id,
                "name":         team.get("displayName", team.get("name", "?")),
                "abbreviation": team.get("abbreviation", "?"),
                "logo":         logo_url,
                "players":      players,
            }
            with _CACHE_LOCK:
                _ROSTER_CACHE[team_id] = entry
            teams_out.append(entry)
            print(f"Successfully cached {len(players)} players for {entry['abbreviation']} (live fallback)", flush=True)
        except Exception as exc:
            log.warning("Live fetch for team %s failed: %s", team_id, exc)

    log.info("Game summary returned %d teams for game %s.", len(teams_out), game_id)
    return {"teams": teams_out}


def fetch_game_roster(game_id: str) -> dict:
    """Legacy roster endpoint – kept for backwards compatibility."""
    return fetch_game_summary_roster(game_id)


# ---------------------------------------------------------------------------
# Push notification
# ---------------------------------------------------------------------------

def _send_push_notification(
    push_token: str,
    title: str,
    body: str,
    sound_channel: str = "classic_alarm",
    alert_type: str = "alarm",
) -> None:
    # Fall back to persisted token when caller passes empty string
    if not push_token:
        saved_token, saved_channel = get_saved_push_token()
        if saved_token:
            print(f"DEBUG: push_token empty – using saved ({saved_token[:20]}...)", flush=True)
            push_token    = saved_token
            sound_channel = saved_channel
        else:
            log.warning("No push token – skipping notification.")
            return

    if not _firebase_ready:
        log.warning("Firebase not initialised – skipping notification.")
        return

    is_alarm   = (alert_type == "alarm")
    channel_id = sound_channel if is_alarm else "silent"

    # Data-only FCM message — no `notification` field so Android ALWAYS calls
    # setBackgroundMessageHandler regardless of app state (foreground/background/killed).
    # The Notifee background handler builds and displays the notification from this data.
    data = {
        "title":      title,
        "body":       body,
        "channel_id": channel_id,
        "alert_type": alert_type,
        "sound":      "classic_alarm" if is_alarm else "",
    }

    try:
        message = fb_messaging.Message(
            data=data,
            android=fb_messaging.AndroidConfig(
                priority="high",
            ),
            token=push_token,
        )
        response = fb_messaging.send(message)
        log.info("FCM push sent → %s", response)
    except Exception as exc:
        log.error("FCM push failed: %s", exc)


# ---------------------------------------------------------------------------
# Shared initial-wait helper
# ---------------------------------------------------------------------------

def _wait_for_tipoff(
    game_id: str,
    stop_event: threading.Event | None = None,
) -> bool:
    """
    If the game hasn't started, sleep until 1 minute before tip-off.
    Returns False if the game is already Final OR if stop_event is set
    during the wait (alarm was cancelled). Returns True otherwise.
    """
    event = _fetch_nba_game(game_id)
    if event is None:
        return True  # let the polling loop handle it

    quarter, _, _ = _parse_nba_state(event)

    if quarter == 9:
        return False  # already over

    if quarter == 0:
        start_time = _parse_nba_start_time(event)
        if start_time:
            secs = (start_time - datetime.now(timezone.utc)).total_seconds() - 60
            if secs > 0:
                h, m = int(secs // 3600), int((secs % 3600) // 60)
                log.info("Game starts in %dh %02dm. Sleeping until 1 min before tip-off...", h, m)
                if stop_event is not None:
                    # Interruptible sleep — returns True if cancelled
                    cancelled = stop_event.wait(timeout=secs)
                    if cancelled:
                        log.info("Alarm cancelled during pre-game wait.")
                        return False
                else:
                    time.sleep(secs)
            else:
                log.info("Tip-off is imminent – skipping initial sleep.")
        else:
            log.warning("No start time found. Proceeding to active monitoring now.")
    else:
        log.info("Game is already live (Q%d). Starting active monitoring.", quarter)

    return True


# ---------------------------------------------------------------------------
# Game monitoring job  (Manual – Priority 1)
# ---------------------------------------------------------------------------

def start_monitoring_job(
    game_id: str,
    game_name: str,
    max_diff: int,
    start_quarter: int,
    push_token: str,
    sound_channel: str = "classic_alarm",
    alert_type: str = "alarm",
) -> None:
    log.info(
        "START game monitoring | game=%s | max_diff=%d | start_quarter=%d",
        game_name, max_diff, start_quarter,
    )

    # Register FIRST so the Active tab shows the alarm immediately (status="scheduled")
    stop_event = _register_manual_alarm(game_id, game_name, max_diff, start_quarter, alert_type)

    try:
        if not _wait_for_tipoff(game_id, stop_event):
            log.info("Game already Final or alarm cancelled – stopping.")
            return

        # Game is now live (or imminent) – flip status so UI shows "WATCHING"
        _update_alarm_status(game_id, "live")
        log.info("Active game monitoring started. Checking every %ds.", POLL_INTERVAL)

        while True:
            event = _fetch_nba_game(game_id)
            if event is None:
                log.warning("Fetch failed. Retrying in %ds.", POLL_INTERVAL)
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("Game alarm for %s was cancelled.", game_name)
                    return
                continue

            quarter, diff, detail = _parse_nba_state(event)
            log.info("Poll | Q%s | diff=%d | %s", quarter, diff, detail)

            if quarter == 9:
                log.info("Game FINAL. Alarm not triggered. Stopping.")
                _deactivate_db_alarm_for(game_id)
                return

            if quarter >= start_quarter and diff <= max_diff:
                log.info("ALARM TRIGGERED | Q%d | diff=%d pts", quarter, diff)
                _send_push_notification(
                    push_token=push_token,
                    title="Smart Alarm! It's Getting Dramatic!",
                    body=f"{game_name}\nQ{quarter} | Point Diff: {diff} | CLOSE GAME!",
                    sound_channel=sound_channel,
                    alert_type=alert_type,
                )
                _deactivate_db_alarm_for(game_id)
                return

            log.info(
                "Game: %s | Q%d | diff=%d pts | Waiting for Q>=%d and diff<=%d. No alarm. Next check in %ds.",
                game_name, quarter, diff, start_quarter, max_diff, POLL_INTERVAL,
            )
            stop_event.wait(timeout=POLL_INTERVAL)
            if stop_event.is_set():
                log.info("Game alarm for %s was cancelled.", game_name)
                return

    finally:
        _unregister_manual_alarm(game_id)


# ---------------------------------------------------------------------------
# Player monitoring job  (Manual – Priority 1)
# ---------------------------------------------------------------------------

def _check_triple_double(pts: int, reb: int, ast: int, stl: int, blk: int) -> bool:
    """Return True if any 3 of the 5 stat categories are >= 9."""
    return sum(1 for v in [pts, reb, ast, stl, blk] if v >= 9) >= 3


def start_player_monitoring_job(
    game_id: str,
    game_name: str,
    player_id: str,
    player_name: str,
    push_token: str,
    sound_channel: str = "classic_alarm",
    condition: str = "pts_goal",   # "pts_goal" | "pts_early" | "triple_double"
    pts_threshold: int = 20,
    quarter_deadline: int = 3,
    test_mode: bool = False,
    alert_type: str = "alarm",
) -> None:
    log.info(
        "START player monitoring | player=%s | game=%s | condition=%s | threshold=%d%s",
        player_name, game_name, condition, pts_threshold,
        " | TEST MODE" if test_mode else "",
    )

    # ── Test mode: skip live game entirely, fire immediately with mock stats ──
    if test_mode:
        stats   = MOCK_STATS
        quarter = 3
        pts = int(stats["PTS"])
        reb = int(stats["REB"])
        ast = int(stats["AST"])
        stl = int(stats["STL"])
        blk = int(stats["BLK"])
        log.info(
            "[TEST] Mock stats | %s | PTS=%d REB=%d AST=%d STL=%d BLK=%d | Q%d",
            player_name, pts, reb, ast, stl, blk, quarter,
        )

        triggered  = False
        notif_body = ""

        if condition == "pts_goal" and pts >= pts_threshold:
            triggered  = True
            notif_body = f"[TEST] {player_name} has scored {pts} points in {game_name}!"

        elif condition == "pts_early" and pts >= pts_threshold:
            triggered  = True
            notif_body = f"[TEST] {player_name} already has {pts} pts in Q{quarter} — on fire early!"

        elif condition == "triple_double" and _check_triple_double(pts, reb, ast, stl, blk):
            hot        = [c for c, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
            triggered  = True
            notif_body = (
                f"[TEST] {player_name}: {' / '.join(hot)}\n"
                f"Approaching a stat-stuffing game in {game_name}!"
            )

        if triggered:
            log.info("[TEST] ALARM TRIGGERED | %s", notif_body)
            _send_push_notification(
                push_token=push_token,
                title="[TEST] Player Alert!",
                body=notif_body,
                sound_channel=sound_channel,
                alert_type=alert_type,
            )
        else:
            log.info("[TEST] Condition '%s' not met with mock stats – no notification sent.", condition)
        return

    # ── Live mode ─────────────────────────────────────────────────────────────
    # Register FIRST so the Active tab shows the alarm immediately (status="scheduled")
    stop_event = _register_player_alarm(
        game_id, game_name, player_id, player_name, condition, pts_threshold, alert_type,
    )

    try:
        if not _wait_for_tipoff(game_id, stop_event):
            log.info("Game already Final or alarm cancelled – stopping.")
            return

        # Game is now live (or imminent) – flip status so UI shows "WATCHING"
        _update_player_alarm_status(player_id, condition, "live")
        log.info("Active player monitoring started. Checking every %ds.", POLL_INTERVAL)

        while True:
            event = _fetch_nba_game(game_id)
            if event is None:
                log.warning("Game fetch failed. Retrying in %ds.", POLL_INTERVAL)
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("Player alarm for %s was cancelled.", player_name)
                    return
                continue

            quarter, _, _ = _parse_nba_state(event)

            if quarter == 9:
                log.info("Game FINAL. Player alarm not triggered. Stopping.")
                _deactivate_db_alarm_for(player_id)
                return

            if condition == "pts_early" and quarter >= quarter_deadline:
                log.info(
                    "Q%d reached – %s didn't hit %d pts before Q%d. Stopping.",
                    quarter, player_name, pts_threshold, quarter_deadline,
                )
                return

            stats = _fetch_player_stats(game_id, player_id)
            if stats is None:
                log.warning("Player stats unavailable. Retrying in %ds.", POLL_INTERVAL)
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("Player alarm for %s was cancelled.", player_name)
                    return
                continue

            pts = int(stats.get("PTS", 0) or 0)
            reb = int(stats.get("REB", 0) or 0)
            ast = int(stats.get("AST", 0) or 0)
            stl = int(stats.get("STL", 0) or 0)
            blk = int(stats.get("BLK", 0) or 0)
            log.info(
                "Player stats | %s | PTS=%d REB=%d AST=%d STL=%d BLK=%d | Q%d",
                player_name, pts, reb, ast, stl, blk, quarter,
            )

            triggered  = False
            notif_body = ""

            if condition == "pts_goal" and pts >= pts_threshold:
                triggered  = True
                notif_body = f"{player_name} has scored {pts} points in {game_name}!"

            elif condition == "pts_early" and pts >= pts_threshold:
                triggered  = True
                notif_body = f"{player_name} already has {pts} pts in Q{quarter} — on fire early!"

            elif condition == "triple_double" and _check_triple_double(pts, reb, ast, stl, blk):
                hot        = [f"{n} {v}" for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
                triggered  = True
                notif_body = (
                    f"{player_name}: {' / '.join(hot)}\n"
                    f"Approaching a stat-stuffing game in {game_name}!"
                )

            if triggered:
                log.info("PLAYER ALARM TRIGGERED | %s", notif_body)
                _send_push_notification(
                    push_token=push_token,
                    title="Player Alert!",
                    body=notif_body,
                    sound_channel=sound_channel,
                    alert_type=alert_type,
                )
                _deactivate_db_alarm_for(player_id)
                return

            log.info("No trigger yet. Next check in %ds.", POLL_INTERVAL)
            stop_event.wait(timeout=POLL_INTERVAL)
            if stop_event.is_set():
                log.info("Player alarm for %s was cancelled.", player_name)
                return

    finally:
        _unregister_player_alarm(player_id, condition)


# ---------------------------------------------------------------------------
# Background Radar job  (Priorities 2 & 3 + Historical + Career High)
# ---------------------------------------------------------------------------

_radar_running = False
_radar_lock    = threading.Lock()


def get_radar_status() -> bool:
    return _radar_running


def _run_radar_tick(push_token: str, sound_channel: str, prefs: dict) -> None:
    """Single radar scan – called once per poll cycle."""
    print("--- STARTING RADAR TICK ---", flush=True)
    radar_cfg       = prefs.get("GlobalGameRadar", {})
    radar_alert_type = radar_cfg.get("alert_type", "silent")
    fav_teams       = {t["team_id"]: t for t in prefs.get("FavoriteTeams", [])}
    fav_players     = prefs.get("FavoritePlayers", {})   # {player_id: {name, threshold, alert_type}}
    global_thresh   = prefs.get("GlobalThresholds", {})
    if not fav_players:
        print("DEBUG: Radar is ticking – no favorite players configured.", flush=True)
    else:
        print(f"DEBUG: Radar watching {len(fav_players)} favorite player(s): {[v.get('name','?') for v in fav_players.values()]}", flush=True)
    hist_cfg        = prefs.get("HistoricalMode", {})
    hist_enabled    = hist_cfg.get("enabled", False)
    hist_alert_type = hist_cfg.get("alert_type", "silent")
    hist_cats       = hist_cfg.get("categories", {})

    # ── Fetch all of today's games ───────────────────────────────────────────
    try:
        resp = requests.get(ESPN_NBA_URL, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as exc:
        log.warning("RADAR: scoreboard fetch failed: %s", exc)
        return

    needs_boxscore = bool(fav_players) or hist_enabled

    for event in events:
        game_id   = str(event.get("id", ""))
        game_name = event.get("name", game_id)
        quarter, diff, _ = _parse_nba_state(event)

        # Only care about live games
        if quarter < 1 or quarter > 4:
            continue

        # ── Game-level alert (Priority 2 / 3) ───────────────────────────────
        if not _has_manual_alarm(game_id):
            competitors = event.get("competitions", [{}])[0].get("competitors", [])

            # Priority 2: Favorite Team
            criteria = None
            for comp in competitors:
                tid = str(comp.get("team", {}).get("id", ""))
                if tid in fav_teams:
                    criteria = fav_teams[tid]
                    break

            # Priority 3: Global Radar (fallback)
            if criteria is None and radar_cfg.get("enabled", False):
                criteria = radar_cfg

            if criteria:
                max_d    = int(criteria.get("max_diff", 5))
                start_q  = int(criteria.get("start_quarter", 4))
                c_alert  = criteria.get("alert_type", radar_alert_type)
                if quarter >= start_q and diff <= max_d:
                    label = "Favorite Team Alert!" if "team_name" in criteria else "NBA Radar – Close Game!"
                    if _fire_once(f"{game_id}_game"):
                        _send_push_notification(
                            push_token=push_token,
                            title=label,
                            body=f"{game_name}\nQ{quarter} | Diff: {diff} pts",
                            sound_channel=sound_channel,
                            alert_type=c_alert,
                        )

        # ── Player-level alerts (Career High + Historical) ───────────────────
        if not needs_boxscore:
            continue

        try:
            s_resp = requests.get(ESPN_SUMMARY_URL, params={"event": game_id}, timeout=10)
            s_resp.raise_for_status()
            summary = s_resp.json()
        except Exception as exc:
            log.warning("RADAR: summary fetch failed for %s: %s", game_id, exc)
            continue

        for section in summary.get("boxscore", {}).get("players", []):
            for stats_group in section.get("statistics", []):
                names = stats_group.get("names", [])
                for entry in stats_group.get("athletes", []):
                    athlete     = entry.get("athlete", {})
                    player_id   = str(athlete.get("id", ""))
                    player_name = athlete.get("displayName", "?")
                    raw         = dict(zip(names, entry.get("stats", [])))

                    pts    = int(raw.get("PTS",  0) or 0)
                    reb    = int(raw.get("REB",  0) or 0)
                    ast    = int(raw.get("AST",  0) or 0)
                    stl    = int(raw.get("STL",  0) or 0)
                    blk    = int(raw.get("BLK",  0) or 0)
                    threes = int(raw.get("3PM",  0) or 0)

                    # ── Favorite Player Alerts (fully per-player settings) ──
                    # Skip if a manual player alarm is already handling this player
                    if player_id in fav_players and _has_manual_player_alarm(player_id):
                        continue

                    if player_id in fav_players:
                        fav_cfg       = fav_players[player_id]
                        p_alert_type  = fav_cfg.get("alert_type", "silent")
                        # None = user did not enable that condition — skip it entirely
                        total_pts_thr = fav_cfg.get("total_pts")      # int or None
                        early_pts_q   = fav_cfg.get("early_pts_q")    # int or None
                        early_pts = fav_cfg.get("early_pts")  # int or None
                        td_watch      = bool(fav_cfg.get("td_watch", False))

                        # Total PTS threshold (only if condition is enabled)
                        if total_pts_thr is not None:
                            total_pts_thr = int(total_pts_thr)
                            if pts >= total_pts_thr:
                                print(f"Checking {player_name}: {pts} pts >= total_pts {total_pts_thr}. FIRING!", flush=True)
                                if _fire_once(f"{game_id}_{player_id}_total_pts"):
                                    _send_push_notification(
                                        push_token=push_token,
                                        title="Scoring Alert!",
                                        body=f"{player_name} has {pts} pts in {game_name}!",
                                        sound_channel=sound_channel,
                                        alert_type=p_alert_type,
                                    )
                            else:
                                print(f"Checking {player_name}: {pts} pts / total_pts {total_pts_thr}. No alarm.", flush=True)

                        # Early Points (fires when threshold is hit at or before the deadline quarter)
                        if early_pts_q is not None and early_pts is not None:
                            early_pts_q   = int(early_pts_q)
                            early_pts = int(early_pts)
                            if quarter <= early_pts_q and pts >= early_pts:
                                if _fire_once(f"{game_id}_{player_id}_early_pts_q{early_pts_q}"):
                                    _send_push_notification(
                                        push_token=push_token,
                                        title="Early Points Alert!",
                                        body=f"{player_name}: {pts} pts by Q{quarter} in {game_name}!",
                                        sound_channel=sound_channel,
                                        alert_type=p_alert_type,
                                    )

                        # Triple-double watch (only if enabled)
                        if td_watch and _check_triple_double(pts, reb, ast, stl, blk):
                            hot = [f"{n} {v}" for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
                            if _fire_once(f"{game_id}_{player_id}_triple_double"):
                                _send_push_notification(
                                    push_token=push_token,
                                    title="Triple-Double Watch!",
                                    body=f"{player_name}: {' / '.join(hot)} in {game_name}!",
                                    sound_channel=sound_channel,
                                    alert_type=p_alert_type,
                                )

                    # ── Historical Mode ──────────────────────────────────────
                    if not hist_enabled:
                        continue

                    # Historic scoring pace: 40+ pts by end of Q2 (halftime)
                    if hist_cats.get("scoring_40_half") and pts >= 40 and quarter <= 2:
                        if _fire_once(f"{game_id}_{player_id}_scoring_40"):
                            _send_push_notification(
                                push_token=push_token,
                                title="Historic Scoring Pace!",
                                body=f"{player_name}: {pts} pts in Q{quarter} of {game_name}!",
                                sound_channel=sound_channel,
                                alert_type=hist_alert_type,
                            )

                    # Historic 3PM game: 11+
                    if hist_cats.get("threes_11") and threes >= 11:
                        if _fire_once(f"{game_id}_{player_id}_threes_11"):
                            _send_push_notification(
                                push_token=push_token,
                                title="Historic 3-Point Game!",
                                body=f"{player_name}: {threes} threes in {game_name}!",
                                sound_channel=sound_channel,
                                alert_type=hist_alert_type,
                            )

                    # Quad-double threat: 8+ in any 4 of 5 categories
                    if hist_cats.get("quad_double_threat"):
                        cats_at_8 = sum(1 for v in [pts, reb, ast, stl, blk] if v >= 8)
                        if cats_at_8 >= 4:
                            if _fire_once(f"{game_id}_{player_id}_quad"):
                                hot = [
                                    f"{n} {v}"
                                    for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk])
                                    if v >= 8
                                ]
                                _send_push_notification(
                                    push_token=push_token,
                                    title="Quad-Double Threat!",
                                    body=f"{player_name}: {' / '.join(hot)} in {game_name}!",
                                    sound_channel=sound_channel,
                                    alert_type=hist_alert_type,
                                )


def _has_passive_monitoring(prefs: dict) -> bool:
    """Return True if the radar has anything to do this tick."""
    # Active manual alarms — keep radar alive alongside them
    with _manual_lock:
        if _active_manual_alarms or _active_player_alarms:
            return True
    # Passive preference-based features
    if prefs.get("GlobalGameRadar", {}).get("enabled", False):
        return True
    if prefs.get("FavoriteTeams", []):
        return True
    if bool(prefs.get("FavoritePlayers", {})):
        return True
    if prefs.get("HistoricalMode", {}).get("enabled", False):
        return True
    return False


def start_radar_job(push_token: str, sound_channel: str = "classic_alarm") -> None:
    """
    Continuous background radar.  Only one instance runs at a time.
    Scans all live games every POLL_INTERVAL seconds.
    Stops automatically when no passive monitoring features are configured.
    """
    global _radar_running
    with _radar_lock:
        if _radar_running:
            log.info("RADAR: already running – ignoring duplicate start.")
            return
        _radar_running = True

    log.info("RADAR: background radar started.")
    try:
        while True:
            prefs = get_preferences()
            if not _has_passive_monitoring(prefs):
                log.info("RADAR: nothing to watch (no passive features configured) – stopping.")
                return
            _run_radar_tick(push_token, sound_channel, prefs)
            time.sleep(POLL_INTERVAL)
    except Exception as exc:
        log.error("RADAR: unexpected error: %s", exc)
    finally:
        with _radar_lock:
            _radar_running = False
        log.info("RADAR: stopped.")


# ---------------------------------------------------------------------------
# Games list  (for /games endpoint)
# ---------------------------------------------------------------------------

def get_today_games() -> list[dict]:
    """Return today's NBA games from ESPN (US Eastern date)."""
    try:
        eastern_date = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y%m%d")
        resp = requests.get(f"{ESPN_NBA_URL}?dates={eastern_date}", timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        log.info("NBA: %d games fetched for %s", len(events), eastern_date)
        return events
    except Exception as exc:
        log.warning("NBA games fetch failed: %s", exc)
        return []
