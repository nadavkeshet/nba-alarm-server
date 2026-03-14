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

try:
    from nba_api.stats.endpoints import playerprofilev2 as _nba_profile
    from nba_api.stats.static import players as _nba_players
    _NBA_API_AVAILABLE = True
except ImportError:
    _NBA_API_AVAILABLE = False

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
_RECORDS_FILE = _SERVER_DIR / "player_records.json"
print(f"DEBUG: Data directory = {_SERVER_DIR}", flush=True)
_FILE_LOCK    = threading.Lock()

_DEFAULT_PREFS: dict = {
    "GlobalGameRadar": {
        "enabled":       False,
        "max_diff":      5,
        "start_quarter": 4,
    },
    "FavoriteTeams":   [],   # [{team_id, team_name, max_diff, start_quarter}]
    "FavoritePlayers": [],   # [{player_id, name, monitor_career_high}]
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
    return prefs


def save_preferences(prefs: dict) -> None:
    _save_json(_PREFS_FILE, prefs)


def save_push_token(push_token: str, sound_channel: str = "classic_alarm") -> None:
    """Persist the most-recently-seen push token so the radar can auto-start on reboot."""
    if not push_token:
        return
    prefs = get_preferences()
    prefs["_push_token"]    = push_token
    prefs["_sound_channel"] = sound_channel
    save_preferences(prefs)


def get_saved_push_token() -> tuple[str, str]:
    """Return (push_token, sound_channel) saved from the last alarm registration."""
    prefs = get_preferences()
    return prefs.get("_push_token", ""), prefs.get("_sound_channel", "classic_alarm")


# ---------------------------------------------------------------------------
# Anti-spam: one alert per (key + calendar date UTC)
# ---------------------------------------------------------------------------

_FIRED_ALERTS: set[str] = set()
_FIRED_LOCK   = threading.Lock()


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

_active_manual_alarms: dict[str, dict] = {}   # game_id  → alarm info
_active_player_alarms: dict[str, dict] = {}   # player_id → alarm info
_alarm_stop_events:    dict[str, threading.Event] = {}
_player_stop_events:   dict[str, threading.Event] = {}
_manual_lock = threading.Lock()


def _register_manual_alarm(
    game_id: str, game_name: str, max_diff: int, start_quarter: int,
    alert_type: str = "alarm",
) -> threading.Event:
    stop = threading.Event()
    with _manual_lock:
        _active_manual_alarms[game_id] = {
            "game_id":       game_id,
            "game_name":     game_name,
            "max_diff":      max_diff,
            "start_quarter": start_quarter,
            "alert_type":    alert_type,
            "status":        "scheduled",   # "scheduled" → "live" once polling starts
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
    with _manual_lock:
        _active_player_alarms[player_id] = {
            "game_id":       game_id,
            "game_name":     game_name,
            "player_id":     player_id,
            "player_name":   player_name,
            "condition":     condition,
            "pts_threshold": pts_threshold,
            "alert_type":    alert_type,
            "status":        "scheduled",   # "scheduled" → "live" once polling starts
            "started_at":    datetime.now(timezone.utc).isoformat(),
        }
        _player_stop_events[player_id] = stop
    return stop


def _update_alarm_status(game_id: str, status: str) -> None:
    with _manual_lock:
        if game_id in _active_manual_alarms:
            _active_manual_alarms[game_id]["status"] = status


def _update_player_alarm_status(player_id: str, status: str) -> None:
    with _manual_lock:
        if player_id in _active_player_alarms:
            _active_player_alarms[player_id]["status"] = status


def _unregister_manual_alarm(game_id: str) -> None:
    with _manual_lock:
        _active_manual_alarms.pop(game_id, None)
        _alarm_stop_events.pop(game_id, None)


def _unregister_player_alarm(player_id: str) -> None:
    with _manual_lock:
        _active_player_alarms.pop(player_id, None)
        _player_stop_events.pop(player_id, None)


def _has_manual_alarm(game_id: str) -> bool:
    with _manual_lock:
        return game_id in _active_manual_alarms


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
        event = _player_stop_events.get(player_id)
        if event:
            event.set()
            return True
    return False


# ---------------------------------------------------------------------------
# Career-high loader  (NBA API → ESPN gamelog fallback)
# ---------------------------------------------------------------------------

def _get_nba_id_by_name(player_name: str) -> int | None:
    """
    Map an ESPN display name (e.g. "Stephen Curry") to an NBA Stats player ID
    using the nba_api static player list.  Prefers the active player when
    multiple matches exist (e.g. father/son with the same name).
    Returns None if nba_api is unavailable or no match found.
    """
    if not _NBA_API_AVAILABLE:
        return None
    try:
        results = _nba_players.find_players_by_full_name(player_name)
        if not results:
            # Try last name only as a fallback
            last = player_name.split()[-1]
            results = _nba_players.find_players_by_full_name(last)
            results = [r for r in results
                       if player_name.lower() in r["full_name"].lower()]
        if not results:
            log.warning("NBA ID lookup: no match found for '%s'", player_name)
            return None
        active = [r for r in results if r.get("is_active")]
        chosen = (active or results)[0]
        log.info("NBA ID lookup: '%s' → NBA ID %s", player_name, chosen["id"])
        return chosen["id"]
    except Exception as exc:
        log.warning("NBA ID lookup failed for '%s': %s", player_name, exc)
        return None


def _get_career_high_pts(player_id: str, player_name: str) -> int | None:
    """
    Return the all-time single-game career-high PTS for player_id.

    Primary source  : NBA Stats API (PlayerProfileV2 → CareerHighs dataset).
                      Gives the true career high across every season.
    Fallback source : ESPN gamelog for the current season (season high).

    Result is cached in player_records.json so the external API is only
    hit once per player per server deployment.
    Returns None if unavailable (career-high alert is skipped for that player).
    """
    print(f"DEBUG: _get_career_high_pts called for {player_name} (espn_id={player_id})", flush=True)
    print(f"DEBUG: player_records.json path = {_RECORDS_FILE} (exists={_RECORDS_FILE.exists()})", flush=True)

    records = _load_json(_RECORDS_FILE, {})
    if player_id in records:
        cached = records[player_id].get("career_high_pts")
        print(f"DEBUG: {player_name} Career High is {cached} (cache hit)", flush=True)
        log.info("DEBUG: %s Career High is %s (cache hit)", player_name, cached)
        return cached

    career_high: int | None = None

    # ── Primary: NBA Stats API – real all-time career high ───────────────────
    _NBA_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer":            "https://www.nba.com",
        "Accept":             "application/json, text/plain, */*",
        "Accept-Language":    "en-US,en;q=0.9",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token":  "true",
    }

    nba_id = _get_nba_id_by_name(player_name)
    if nba_id:
        try:
            print(f"[CareerHigh] Fetching NBA Stats for {player_name} (NBA ID {nba_id})...", flush=True)
            profile = _nba_profile.PlayerProfileV2(
                player_id=nba_id,
                timeout=30,
                headers=_NBA_HEADERS,
            )
            df      = profile.career_highs.get_data_frame()
            pts_row = df[df["STAT"] == "PTS"]
            if not pts_row.empty:
                career_high = int(pts_row.iloc[0]["STAT_VALUE"])
                print(f"INFO: Verified REAL Career High for {player_name}: {career_high} points.", flush=True)
                log.info(
                    "INFO: Verified REAL Career High for %s: %d points.",
                    player_name, career_high,
                )
            else:
                print(f"[CareerHigh] PTS row not found in CareerHighs dataset for {player_name}. Columns: {list(df.columns)}", flush=True)
        except Exception as exc:
            print(f"[CareerHigh] NBA API FAILED for {player_name} (NBA ID {nba_id}): {exc}", flush=True)
            log.warning(
                "NBA API career high failed for %s (NBA ID %s): %s",
                player_name, nba_id, exc,
            )

    # ── Fallback: ESPN gamelog – season high (max PTS in any game this season)
    if career_high is None:
        try:
            url  = f"{ESPN_ATHLETE_STATS_URL}/{player_id}/gamelog"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data    = resp.json()
            names   = data.get("names", [])
            pts_idx = names.index("PTS") if "PTS" in names else -1
            if pts_idx >= 0:
                max_pts = 0
                for event in data.get("events", {}).values():
                    raw = event.get("stats", [])
                    try:
                        val = int(float(raw[pts_idx] or 0))
                        if val > max_pts:
                            max_pts = val
                    except (IndexError, ValueError, TypeError):
                        pass
                if max_pts > 0:
                    career_high = max_pts
                    log.info(
                        "DEBUG: %s – NBA API unavailable, using ESPN season-high: %s pts",
                        player_name, career_high,
                    )
        except Exception as exc:
            log.warning("ESPN gamelog fallback failed for %s: %s", player_name, exc)

    log.info("DEBUG: %s Career High is %s", player_name, career_high)

    # Cache result (even if None) so we don't hammer the API on every radar tick
    records[player_id] = {"career_high_pts": career_high, "name": player_name}
    _save_json(_RECORDS_FILE, records)
    return career_high


def get_player_records() -> dict:
    """Return the full player_records.json cache (player_id → {name, career_high_pts})."""
    return _load_json(_RECORDS_FILE, {})


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
    if not push_token:
        log.warning("No push token – skipping notification.")
        return

    ios_sound = "default" if sound_channel == "default" else f"{sound_channel}.wav"

    payload = {
        "to":               push_token,
        "title":            title,
        "body":             body,
        "sound":            ios_sound,
        "priority":         "high",
        "channelId":        sound_channel,
        # iOS: content-available wakes the app in background so the
        # notification-response listener can fire without user interaction
        "mutableContent":   True,
        "data":             {"alert_type": alert_type},
    }
    try:
        resp = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        log.info("Push sent → %s", resp.json())
    except Exception as exc:
        log.error("Push notification failed: %s", exc)


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
                return

            log.info(
                "No alarm yet (need Q>=%d and diff<=%d). Next check in %ds.",
                start_quarter, max_diff, POLL_INTERVAL,
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
        _update_player_alarm_status(player_id, "live")
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
                return

            log.info("No trigger yet. Next check in %ds.", POLL_INTERVAL)
            stop_event.wait(timeout=POLL_INTERVAL)
            if stop_event.is_set():
                log.info("Player alarm for %s was cancelled.", player_name)
                return

    finally:
        _unregister_player_alarm(player_id)


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
    fav_players     = {p["player_id"]: p for p in prefs.get("FavoritePlayers", [])}
    if not fav_players:
        print("DEBUG: Radar is ticking but 'active_players' list is EMPTY.", flush=True)
    else:
        print(f"DEBUG: Radar watching {len(fav_players)} favorite player(s): {[p['player_name'] if 'player_name' in p else p.get('name','?') for p in prefs.get('FavoritePlayers', [])]}", flush=True)
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

                    # ── Career High (Favorite Players only) ─────────────────
                    if player_id in fav_players and fav_players[player_id].get("monitor_career_high", True):
                        ch = _get_career_high_pts(player_id, player_name)
                        if ch and pts >= (ch - 5):
                            if _fire_once(f"{game_id}_{player_id}_career_high"):
                                _send_push_notification(
                                    push_token=push_token,
                                    title="Career High Alert!",
                                    body=(
                                        f"{player_name}: {pts} pts in {game_name}\n"
                                        f"Only {ch - pts} away from career high of {ch}!"
                                    ),
                                    sound_channel=sound_channel,
                                    alert_type=fav_players[player_id].get("alert_type", "silent"),
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
    if any(p.get("monitor_career_high", True) for p in prefs.get("FavoritePlayers", [])):
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
