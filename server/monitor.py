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
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("nba_monitor")

POLL_INTERVAL = 60  # seconds between active-monitoring checks


def _tid(token: str) -> str:
    """Short user identifier — last 8 chars of push token."""
    return f"[...{token[-8:]}]" if len(token) >= 8 else "[no-token]"

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
    log.info("Roster cache: refreshing all 30 teams...")

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

            new_cache[team_id] = {
                "id": team_id,
                "name": team.get("displayName", ""),
                "abbreviation": team_abbr,
                "logo": team.get("logos", [{}])[0].get("href") if team.get("logos") else None,
                "players": players,
            }

            time.sleep(0.15)

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



def save_push_token(push_token: str, sound_channel: str = "classic_alarm") -> None:
    """Persist the push token in-memory."""
    global _MEM_PUSH_TOKEN, _MEM_SOUND_CHANNEL
    if not push_token:
        return
    changed = _MEM_PUSH_TOKEN != push_token
    _MEM_PUSH_TOKEN    = push_token
    _MEM_SOUND_CHANNEL = sound_channel
    if changed:
        log.info("Token registered %s", _tid(push_token))


def get_saved_push_token() -> tuple[str, str]:
    """Return (push_token, sound_channel) from in-memory store."""
    return _MEM_PUSH_TOKEN, _MEM_SOUND_CHANNEL


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


# ---------------------------------------------------------------------------
# Invalid-token callback  (set by main.py — removes stale FCM tokens from DB)
# ---------------------------------------------------------------------------

_DB_INVALIDATE_TOKEN_FN = None


def register_db_invalidate_token(fn) -> None:
    """Register a callable(push_token: str) that removes an invalid FCM token from DB."""
    global _DB_INVALIDATE_TOKEN_FN
    _DB_INVALIDATE_TOKEN_FN = fn


# ---------------------------------------------------------------------------
# Fired-alert persistence  (survives server restarts during live games)
# ---------------------------------------------------------------------------

_DB_PERSIST_FIRED_FN = None
_DB_CHECK_FIRED_FN   = None


def register_db_persist_fired(fn) -> None:
    """
    Register a callable(key: str) that persists a fired-alert key to the DB.
    Called from _fire_once every time an alert is sent for the first time.
    """
    global _DB_PERSIST_FIRED_FN
    _DB_PERSIST_FIRED_FN = fn


def register_db_check_fired(fn) -> None:
    """
    Register a callable(key: str) -> bool that checks if a key exists in DB.
    Used by _fire_once as a fallback when startup hydration may have failed.
    """
    global _DB_CHECK_FIRED_FN
    _DB_CHECK_FIRED_FN = fn


def hydrate_fired_alerts(keys: set[str]) -> None:
    """
    Load a set of already-fired keys (read from DB at startup) into the
    in-memory _FIRED_ALERTS set so duplicate alerts are suppressed even after
    a server restart mid-game.
    """
    if not keys:
        return
    with _FIRED_LOCK:
        _FIRED_ALERTS.update(keys)
    log.info("STARTUP: hydrated %d fired-alert keys from DB.", len(keys))


def _deactivate_db_alarm_for(target_id: str) -> None:
    if _DB_DEACTIVATE_FN is not None:
        try:
            _DB_DEACTIVATE_FN(target_id)
        except Exception as exc:
            log.warning("DB alarm deactivation failed for %s: %s", target_id, exc)


def _fire_once(key: str, token: str = "") -> bool:
    """
    Returns True (and marks the key) the first time it is called for this key
    on the current UTC date.  Returns False on all subsequent calls today.
    token is included in the key so different users never block each other.

    On first fire: persists the key to the DB (via callback) so the dedup
    state survives a server restart mid-game — no duplicate wake-ups.
    """
    today    = datetime.now(timezone.utc).strftime("%Y%m%d")
    full_key = f"{today}_{token}_{key}" if token else f"{today}_{key}"
    with _FIRED_LOCK:
        if full_key in _FIRED_ALERTS:
            log.info("DEDUP BLOCKED  (memory) | %s | ...%s", key, token[-8:] if token else "")
            return False
        # DB fallback: if startup hydration failed after a server restart,
        # the in-memory set may be empty even though the key exists in DB.
        # A quick DB check here prevents double-notifications after redeploys.
        if _DB_CHECK_FIRED_FN is not None:
            try:
                if _DB_CHECK_FIRED_FN(full_key):
                    _FIRED_ALERTS.add(full_key)   # re-hydrate for future calls
                    log.info("DEDUP BLOCKED  (db)     | %s | ...%s", key, token[-8:] if token else "")
                    return False
            except Exception:
                pass  # DB unavailable — proceed cautiously
        _FIRED_ALERTS.add(full_key)
    # Persist outside the lock — DB write is slow, shouldn't block concurrent checks
    if _DB_PERSIST_FIRED_FN is not None:
        try:
            _DB_PERSIST_FIRED_FN(full_key)
        except Exception as exc:
            log.warning("_fire_once: DB persist failed for %s: %s", full_key, exc)
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

    log.info("STARTUP: Stale state cleared – active_monitors reset.")


# ---------------------------------------------------------------------------
# NBA game helpers
# ---------------------------------------------------------------------------

def _fetch_nba_game(game_id: str) -> dict | None:
    """
    Return the raw ESPN event dict for game_id from the shared 60-second cache.
    All monitoring threads (game + player) share one ESPN request per minute
    instead of each making their own — prevents rate-limiting at scale.
    Falls back to a direct fetch only if the cache is empty (e.g. first call).
    """
    for event in get_today_games():
        if str(event.get("id")) == str(game_id):
            return event
    return None


def _period_label(quarter: int) -> str:
    """Return 'Q1'–'Q4' for regulation, 'OT1'/'OT2'... for overtime periods."""
    if quarter <= 4:
        return f"Q{quarter}"
    return f"OT{quarter - 4}"


def _clock_minutes_remaining(event: dict) -> float:
    """
    Returns minutes remaining in the current period from ESPN's displayClock.
    Returns 99.0 if the value cannot be parsed (safe default = assume time remains).
    """
    try:
        clock = event["competitions"][0]["status"].get("displayClock", "")
        if not clock:
            return 99.0
        parts = clock.split(":")
        return int(parts[0]) + int(parts[1]) / 60
    except (KeyError, IndexError, ValueError):
        return 99.0


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
            continue

        # Cache miss — live fetch and populate cache for next time
        log.warning("Roster cache miss for team %s – fetching live.", team_id)
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
        except Exception as exc:
            log.warning("Live fetch for team %s failed: %s", team_id, exc)

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
            push_token    = saved_token
            sound_channel = saved_channel
        else:
            log.warning("FCM: no push token available – notification dropped.")
            return

    if not _firebase_ready:
        log.warning("Firebase not initialised – skipping notification.")
        return

    is_alarm   = (alert_type == "alarm")
    channel_id = sound_channel if is_alarm else "silent"

    # Data-only FCM message — no `notification` field, so Android does NOT
    # handle the message at OS level; instead it wakes the Headless JS task
    # which calls setBackgroundMessageHandler.  Notifee then builds and
    # displays the full notification (with sound, channel, DISMISS action).
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
            # Top-level notification makes iOS show a native banner.
            # Android suppresses this because we set suppress_notification
            # in the android config — the RN app handles display via
            # setBackgroundMessageHandler + Notifee.
            notification=fb_messaging.Notification(
                title=title,
                body=body,
                sound="default",
            ),
            android=fb_messaging.AndroidConfig(
                priority="high",
                notification=fb_messaging.AndroidNotification(
                    channel_id=channel_id,
                ),
            ),
            token=push_token,
        )
        response = fb_messaging.send(message)
        log.info("FCM push sent → %s", response)
    except fb_messaging.UnregisteredError:
        # Token is no longer valid (app uninstalled / re-installed without re-registering).
        # Remove it from the DB so we never attempt it again.
        log.warning("FCM: token unregistered – removing %s", _tid(push_token))
        if _DB_INVALIDATE_TOKEN_FN is not None:
            try:
                _DB_INVALIDATE_TOKEN_FN(push_token)
            except Exception as cb_exc:
                log.warning("FCM: token invalidation callback failed: %s", cb_exc)
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
        log.info("GAME WATCH  start | %s | diff≤%d from Q%d", game_name, max_diff, start_quarter)

        while True:
            event = _fetch_nba_game(game_id)
            if event is None:
                log.warning("ESPN fetch failed for %s – retrying in %ds.", game_name, POLL_INTERVAL)
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("GAME WATCH  cancel | %s", game_name)
                    return
                continue

            # Hard guard: ensure the event is strictly for this alarm's game.
            # _fetch_nba_game already filters by game_id, but this makes it
            # impossible for a mis-matched event to ever trigger a wrong alarm.
            if str(event.get("id")) != str(game_id):
                log.error("GAME WATCH  mismatch | expected %s got %s – skipping tick.", game_id, event.get("id"))
                stop_event.wait(timeout=POLL_INTERVAL)
                continue

            quarter, diff, _ = _parse_nba_state(event)

            if quarter == 9:
                log.info("GAME WATCH  final  | %s | alarm not triggered", game_name)
                _deactivate_db_alarm_for(game_id)
                return

            if quarter >= start_quarter and diff <= max_diff:
                log.info("GAME ALARM  fire   | %s | Q%d diff=%d", game_name, quarter, diff)
                _send_push_notification(
                    push_token=push_token,
                    title="Smart Alarm! It's Getting Dramatic!",
                    body=f"{game_name}\n{_period_label(quarter)} | Point Diff: {diff} | CLOSE GAME!",
                    sound_channel=sound_channel,
                    alert_type=alert_type,
                )
                _deactivate_db_alarm_for(game_id)
                return

            stop_event.wait(timeout=POLL_INTERVAL)
            if stop_event.is_set():
                log.info("GAME WATCH  cancel | %s", game_name)
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
        "PLAYER WATCH start | %s | %s | %s threshold=%d%s",
        player_name, game_name, condition, pts_threshold,
        " [TEST]" if test_mode else "",
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
        triggered  = False
        notif_body = ""

        if condition == "pts_goal" and pts >= pts_threshold:
            triggered  = True
            notif_body = f"[TEST] {player_name} has scored {pts} points in {game_name}!"
        elif condition == "pts_early" and pts >= pts_threshold:
            triggered  = True
            notif_body = f"[TEST] {player_name} already has {pts} pts in {_period_label(quarter)} — on fire early!"
        elif condition == "triple_double" and _check_triple_double(pts, reb, ast, stl, blk):
            hot        = [f"{c} {v}" for c, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
            triggered  = True
            notif_body = f"[TEST] {player_name}: {' / '.join(hot)} in {game_name}!"

        if triggered:
            log.info("PLAYER ALARM fire   | [TEST] %s", notif_body)
            _send_push_notification(
                push_token=push_token,
                title="[TEST] Player Alert!",
                body=notif_body,
                sound_channel=sound_channel,
                alert_type=alert_type,
            )
        else:
            log.info("PLAYER WATCH [TEST] | %s | condition '%s' not met with mock stats", player_name, condition)
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
        log.info("PLAYER WATCH live   | %s | %s | threshold=%d", player_name, game_name, pts_threshold)

        while True:
            event = _fetch_nba_game(game_id)
            if event is None:
                log.warning("ESPN fetch failed for %s – retrying in %ds.", game_name, POLL_INTERVAL)
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("PLAYER WATCH cancel | %s | %s", player_name, condition)
                    return
                continue

            # Hard guard: ensure event is strictly for this alarm's game.
            if str(event.get("id")) != str(game_id):
                log.error("PLAYER WATCH mismatch | expected %s got %s – skipping tick.", game_id, event.get("id"))
                stop_event.wait(timeout=POLL_INTERVAL)
                continue

            quarter, _, _ = _parse_nba_state(event)

            if quarter == 9:
                log.info("PLAYER WATCH final  | %s | alarm not triggered", player_name)
                _deactivate_db_alarm_for(player_id)
                return

            if condition == "pts_early" and quarter >= quarter_deadline:
                log.info("PLAYER WATCH expire | %s | Q%d deadline passed", player_name, quarter_deadline)
                return

            stats = _fetch_player_stats(game_id, player_id)
            if stats is None:
                stop_event.wait(timeout=POLL_INTERVAL)
                if stop_event.is_set():
                    log.info("PLAYER WATCH cancel | %s | %s", player_name, condition)
                    return
                continue

            pts = int(stats.get("PTS", 0) or 0)
            reb = int(stats.get("REB", 0) or 0)
            ast = int(stats.get("AST", 0) or 0)
            stl = int(stats.get("STL", 0) or 0)
            blk = int(stats.get("BLK", 0) or 0)

            triggered  = False
            notif_body = ""

            if condition == "pts_goal" and pts >= pts_threshold:
                triggered  = True
                notif_body = f"{player_name} has scored {pts} points in {game_name}!"
            elif condition == "pts_early" and pts >= pts_threshold:
                triggered  = True
                notif_body = f"{player_name} already has {pts} pts in {_period_label(quarter)} — on fire early!"
            elif condition == "triple_double" and _check_triple_double(pts, reb, ast, stl, blk) \
                    and not (quarter >= 4 and _clock_minutes_remaining(event) <= 3):
                hot        = [f"{n} {v}" for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
                triggered  = True
                notif_body = f"{player_name}: {' / '.join(hot)} in {game_name}!"

            if triggered:
                # Claim the same dedup key the radar uses for this condition.
                # This prevents the radar from re-firing the same alert after
                # this manual alarm thread exits and _has_manual_player_alarm
                # returns False again.
                if condition == "pts_goal":
                    _fire_once(f"{game_id}_{player_id}_total_pts", push_token)
                elif condition == "pts_early":
                    _fire_once(f"{game_id}_{player_id}_early_pts_q{quarter_deadline}", push_token)
                elif condition == "triple_double":
                    _fire_once(f"{game_id}_{player_id}_triple_double", push_token)

                log.info("PLAYER ALARM fire   | %s", notif_body)
                _send_push_notification(
                    push_token=push_token,
                    title="Player Alert!",
                    body=notif_body,
                    sound_channel=sound_channel,
                    alert_type=alert_type,
                )
                _deactivate_db_alarm_for(player_id)
                return

            stop_event.wait(timeout=POLL_INTERVAL)
            if stop_event.is_set():
                log.info("PLAYER WATCH cancel | %s | %s", player_name, condition)
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


# ---------------------------------------------------------------------------
# DB callback — registered by main.py to supply all users' preferences
# ---------------------------------------------------------------------------

_DB_GET_ALL_USERS_FN = None


def register_db_get_all_users(fn) -> None:
    """Register a callable() → list[{token, channel, prefs}] for multi-user radar."""
    global _DB_GET_ALL_USERS_FN
    _DB_GET_ALL_USERS_FN = fn


# ---------------------------------------------------------------------------
# Multi-user radar
# ---------------------------------------------------------------------------

def _run_radar_tick_for_user(
    push_token: str,
    sound_channel: str,
    prefs: dict,
    events: list[dict],
    summaries: dict,          # mutable cache: game_id → summary JSON (shared across users)
) -> None:
    """
    Check one user's preferences against pre-fetched ESPN data.
    `summaries` is populated lazily and shared so each game is fetched only once
    even when multiple users watch the same game.
    """
    radar_cfg        = prefs.get("GlobalGameRadar", {})
    radar_alert_type = radar_cfg.get("alert_type", "silent")
    fav_teams        = {t["team_id"]: t for t in prefs.get("FavoriteTeams", [])}
    fav_players      = prefs.get("FavoritePlayers", {})
    hist_cfg         = prefs.get("HistoricalMode", {})
    hist_enabled     = hist_cfg.get("enabled", False)
    hist_alert_type  = hist_cfg.get("alert_type", "silent")
    hist_cats        = hist_cfg.get("categories", {})
    needs_boxscore   = bool(fav_players) or hist_enabled
    token            = push_token   # short alias for _fire_once calls

    for event in events:
        game_id   = str(event.get("id", ""))
        game_name = event.get("name", game_id)
        quarter, diff, _ = _parse_nba_state(event)

        if quarter < 1 or quarter == 9:
            continue

        # ── Game-level alert (Priority 2: Favorite Team / Priority 3: Global Radar) ─
        if not _has_manual_alarm(game_id):
            competitors = event.get("competitions", [{}])[0].get("competitors", [])
            criteria = None
            for comp in competitors:
                tid = str(comp.get("team", {}).get("id", ""))
                if tid in fav_teams:
                    criteria = fav_teams[tid]
                    break
            if criteria is None and radar_cfg.get("enabled", False):
                criteria = radar_cfg

            if criteria:
                max_d   = int(criteria.get("max_diff", 5))
                start_q = int(criteria.get("start_quarter", 4))
                c_alert = criteria.get("alert_type", radar_alert_type)
                if quarter >= start_q and diff <= max_d:
                    label = "Favorite Team Alert!" if "team_name" in criteria else "NBA Radar – Close Game!"
                    if _fire_once(f"{game_id}_game", token):
                        log.info("RADAR ALARM  fire   | %s | Q%d diff=%d | %s | %s", game_name, quarter, diff, label, _tid(push_token))
                        _send_push_notification(
                            push_token=push_token,
                            title=label,
                            body=f"{game_name}\n{_period_label(quarter)} | Diff: {diff} pts",
                            sound_channel=sound_channel,
                            alert_type=c_alert,
                        )

        # ── Player-level alerts ──────────────────────────────────────────────
        if not needs_boxscore:
            continue

        # Fetch summary lazily; result is cached in `summaries` for other users
        if game_id not in summaries:
            try:
                s_resp = requests.get(ESPN_SUMMARY_URL, params={"event": game_id}, timeout=10)
                s_resp.raise_for_status()
                summaries[game_id] = s_resp.json()
            except Exception as exc:
                log.warning("RADAR: summary fetch failed for %s: %s", game_id, exc)
                summaries[game_id] = None

        summary = summaries.get(game_id)
        if not summary:
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

                    # ── Favorite Player Alerts ───────────────────────────────
                    if player_id in fav_players and not _has_manual_player_alarm(player_id):
                        fav_cfg      = fav_players[player_id]
                        p_alert      = fav_cfg.get("alert_type", "silent")
                        total_pts_thr = fav_cfg.get("total_pts")
                        early_pts_q  = fav_cfg.get("early_pts_q")
                        early_pts    = fav_cfg.get("early_pts")
                        td_watch     = bool(fav_cfg.get("td_watch", False))

                        if total_pts_thr is not None and pts >= int(total_pts_thr):
                            if _fire_once(f"{game_id}_{player_id}_total_pts", token):
                                log.info("RADAR ALARM  fire   | %s | %d pts in %s | %s", player_name, pts, game_name, _tid(push_token))
                                _send_push_notification(
                                    push_token=push_token,
                                    title="Scoring Alert!",
                                    body=f"{player_name} has {pts} pts in {game_name}!",
                                    sound_channel=sound_channel, alert_type=p_alert,
                                )

                        if early_pts_q is not None and early_pts is not None:
                            if quarter <= int(early_pts_q) and pts >= int(early_pts):
                                if _fire_once(f"{game_id}_{player_id}_early_pts_q{early_pts_q}", token):
                                    _send_push_notification(
                                        push_token=push_token,
                                        title="Early Points Alert!",
                                        body=f"{player_name}: {pts} pts by {_period_label(quarter)} in {game_name}!",
                                        sound_channel=sound_channel, alert_type=p_alert,
                                    )

                        clock_mins_td = _clock_minutes_remaining(event)
                        if td_watch and _check_triple_double(pts, reb, ast, stl, blk) \
                                and not (quarter >= 4 and clock_mins_td <= 3):
                            hot = [f"{n} {v}" for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 9]
                            if _fire_once(f"{game_id}_{player_id}_triple_double", token):
                                _send_push_notification(
                                    push_token=push_token,
                                    title="Triple-Double Watch!",
                                    body=f"{player_name}: {' / '.join(hot)} in {game_name}!",
                                    sound_channel=sound_channel, alert_type=p_alert,
                                )

                    # ── Historical Mode ──────────────────────────────────────
                    if not hist_enabled:
                        continue

                    if hist_cats.get("scoring_40_half") and pts >= 40 and quarter <= 2:
                        if _fire_once(f"{game_id}_{player_id}_scoring_40", token):
                            _send_push_notification(
                                push_token=push_token, title="Historic Scoring Pace!",
                                body=f"{player_name}: {pts} pts in {_period_label(quarter)} of {game_name}!",
                                sound_channel=sound_channel, alert_type=hist_alert_type,
                            )

                    if hist_cats.get("threes_11") and threes >= 12:
                        clock_mins = _clock_minutes_remaining(event)
                        if not (quarter >= 4 and clock_mins <= 3):
                            if _fire_once(f"{game_id}_{player_id}_threes_11", token):
                                _send_push_notification(
                                    push_token=push_token, title="Historic 3-Point Game!",
                                    body=f"{player_name}: {threes} threes in {game_name}!",
                                    sound_channel=sound_channel, alert_type=hist_alert_type,
                                )

                    if hist_cats.get("quad_double_threat"):
                        clock_mins_quad = _clock_minutes_remaining(event)
                        cats_at_8 = sum(1 for v in [pts, reb, ast, stl, blk] if v >= 8)
                        if cats_at_8 >= 4 and not (quarter >= 4 and clock_mins_quad <= 3):
                            hot = [f"{n} {v}" for n, v in zip(["PTS","REB","AST","STL","BLK"], [pts,reb,ast,stl,blk]) if v >= 8]
                            if _fire_once(f"{game_id}_{player_id}_quad", token):
                                _send_push_notification(
                                    push_token=push_token, title="Quad-Double Threat!",
                                    body=f"{player_name}: {' / '.join(hot)} in {game_name}!",
                                    sound_channel=sound_channel, alert_type=hist_alert_type,
                                )


def _prune_fired_alerts() -> None:
    """Remove dedup keys from previous UTC days to prevent unbounded memory growth."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    with _FIRED_LOCK:
        stale = {k for k in _FIRED_ALERTS if not k.startswith(today)}
        _FIRED_ALERTS.difference_update(stale)


def _run_radar_tick(users: list[dict]) -> bool:
    """
    Full radar scan for all users.
    ESPN scoreboard is fetched ONCE; game summaries are fetched lazily and
    cached so multiple users watching the same game share one HTTP request.
    Returns True on success, False if the ESPN fetch failed (used by the
    caller to track consecutive failures).
    """
    _prune_fired_alerts()

    try:
        resp = requests.get(ESPN_NBA_URL, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as exc:
        log.warning("RADAR: scoreboard fetch failed: %s", exc)
        return False

    summaries: dict = {}   # shared lazy cache: game_id → summary JSON

    for user in users:
        try:
            _run_radar_tick_for_user(
                push_token    = user["token"],
                sound_channel = user.get("channel", "classic_alarm"),
                prefs         = user["prefs"],
                events        = events,
                summaries     = summaries,
            )
        except Exception as exc:
            log.error("RADAR: error processing user %s: %s", _tid(user.get("token", "")), exc)

    return True


def _has_passive_monitoring(users: list[dict]) -> bool:
    """Return True if any user has passive features configured."""
    for user in users:
        prefs = user.get("prefs", {})
        if prefs.get("GlobalGameRadar", {}).get("enabled", False):
            return True
        if prefs.get("FavoriteTeams"):
            return True
        if prefs.get("FavoritePlayers"):
            return True
        if prefs.get("HistoricalMode", {}).get("enabled", False):
            return True
    return False


def start_radar_job() -> None:
    """
    Continuous background radar — one instance at a time, all users.
    Reads all users from DB on every tick via the registered callback.
    Stops when no user has any passive features configured.
    """
    global _radar_running
    with _radar_lock:
        if _radar_running:
            log.info("RADAR: already running – ignoring duplicate start.")
            return
        _radar_running = True

    log.info("RADAR: started.")
    espn_failures = 0
    try:
        while True:
            users = _DB_GET_ALL_USERS_FN() if _DB_GET_ALL_USERS_FN else []
            if not _has_passive_monitoring(users):
                log.info("RADAR: no active preferences for any user – stopping.")
                return
            success = _run_radar_tick(users)
            if success:
                espn_failures = 0
            else:
                espn_failures += 1
                if espn_failures >= 3:
                    log.error(
                        "RADAR_ESPN_DOWN: ESPN API has failed %d consecutive ticks – "
                        "alerts may be missed until it recovers.",
                        espn_failures,
                    )
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

_games_cache: list[dict] = []
_games_cache_time: float = 0.0
_GAMES_CACHE_TTL: int = 60  # seconds

def get_today_games() -> list[dict]:
    """Return today's NBA games from ESPN (US Eastern date), cached for 60 s."""
    global _games_cache, _games_cache_time
    now = time.time()
    if _games_cache and now - _games_cache_time < _GAMES_CACHE_TTL:
        return _games_cache
    try:
        eastern_date = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y%m%d")
        resp = requests.get(f"{ESPN_NBA_URL}?dates={eastern_date}", timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        log.info("NBA: %d games fetched for %s", len(events), eastern_date)
        _games_cache = events
        _games_cache_time = now
        return events
    except Exception as exc:
        log.warning("NBA games fetch failed: %s", exc)
        return _games_cache  # return stale cache on error rather than empty list

