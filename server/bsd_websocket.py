"""Bzzoiro Sports Data (BSD) live WebSocket client.

Persistent background thread that keeps a single WS connection to
`wss://sports.bzzoiro.com/ws/live/` open for the server's lifetime.
Maintains an in-memory cache of the latest snapshot per subscribed
event_id, so consumers (monitor.py, alarm engines, future REST handlers)
can read live state at zero-latency instead of polling REST.

Why:
  The previous design polled BSD REST every 5–15 s per active alarm.
  WS pushes deltas as soon as BSD's data engine produces them — that
  drops alarm latency from 5–15 s to ~1 s and removes a *lot* of
  outbound HTTP traffic.

Design:
  - One connection, max 10 concurrent subscriptions per BSD docs.
    Sharded into multiple sockets if we ever exceed that.
  - Auto-reconnect with exponential backoff (1 → 2 → 4 → ... → 30 s).
  - 30 s client ping to keep middleboxes from killing the connection.
  - Thread-safe cache + callbacks.

Reference: https://sports.bzzoiro.com/docs/websocket/
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import deque
from typing import Any, Callable

import websocket  # websocket-client

import win_probability

logger = logging.getLogger(__name__)

WS_URL = "wss://sports.bzzoiro.com/ws/live/"

# Per-situation attacking-threat weight for the live-surge signal. Benign
# situations (possession / safe / throw-in / goal-kick / offside / card /
# substitution) are absent → weight 0. Values are the live `livedata.situation`
# vocabulary observed off the wire.
_SITUATION_THREAT: dict[str, float] = {
    "goal": 1.0, "penalty": 1.0,
    "bigchance": 0.9, "big_chance": 0.9,
    "shotontarget": 0.7,
    "goalkeeper_saved": 0.6, "shotsaved": 0.6,
    "shotofftarget": 0.45, "shotblocked": 0.45, "shot": 0.5,
    "dangerous": 0.4,
    "corner": 0.3, "freekick": 0.15, "attack": 0.12,
}
MAX_SUBS_PER_SOCKET = 10  # BSD limit
PING_INTERVAL_S = 30.0
RECONNECT_BACKOFF_INITIAL = 1.0
RECONNECT_BACKOFF_MAX = 30.0


def _token() -> str:
    tok = os.environ.get("BZZOIRO_API_TOKEN", "").strip()
    if not tok:
        raise RuntimeError(
            "BZZOIRO_API_TOKEN env var is not set — WS client cannot start."
        )
    return tok


# Callback shape: invoked on every fresh snapshot the server pushes.
#   on_event_update(event_id, kind, payload)
# where kind ∈ {"event", "livedata", "odds", "odds_book"} and payload is the
# raw message dict from the WS.
EventCallback = Callable[[int, str, dict], None]


class BSDLiveClient:
    """Single persistent socket. For ≤10 concurrent subscriptions; if you
    need more, instantiate additional clients and shard event_ids across
    them — the cache below is shared so consumers don't need to know which
    socket served which event."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop = threading.Event()

        # event_id → desired subscription state (True = should be subscribed)
        self._desired: set[int] = set()
        # event_id → True if the server has confirmed `subscribed` frame
        self._confirmed: set[int] = set()

        # event_id → latest snapshot of each frame type
        self._state_event:    dict[int, dict] = {}
        self._state_livedata: dict[int, dict] = {}
        self._state_odds:     dict[int, dict] = {}
        # event_id → bounded history of (monotonic_ts, p_home, p_draw, p_away)
        # derived from odds frames, for win-probability swing detection.
        self._winprob_history: dict[int, deque] = {}
        # event_id → bounded history of (monotonic_ts, side, situation) derived
        # from livedata frames, for the live attacking-surge signal.
        self._livedata_history: dict[int, deque] = {}

        self._callbacks: list[EventCallback] = []

        self._connected = threading.Event()
        self._connect_lock = threading.Lock()
        self._backoff = RECONNECT_BACKOFF_INITIAL

        # When the server tells us our token can't open a WS at all
        # (subscription required, auth failure), we set this and stop
        # reconnect-thrashing. The singleton can be re-started after the
        # subscription is activated by calling start() again.
        self._fatal_auth_error: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self) -> None:
        """Spawn the connection thread + the ping thread. Idempotent."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_forever, name="bsd-ws-client", daemon=True,
            )
            self._thread.start()
            self._ping_thread = threading.Thread(
                target=self._ping_loop, name="bsd-ws-ping", daemon=True,
            )
            self._ping_thread.start()
            logger.info("BSDLiveClient started")

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._ws is not None:
                try: self._ws.close()
                except Exception: pass
            self._connected.clear()
        logger.info("BSDLiveClient stop requested")

    # ------------------------------------------------------------------
    # Subscriptions

    def subscribe(self, event_id: int) -> bool:
        """Mark `event_id` as desired. Sends a subscribe frame immediately
        if connected; otherwise the connect handler picks it up. Returns
        False if we're already at the BSD cap."""
        with self._lock:
            if event_id in self._desired:
                return True
            if len(self._desired) >= MAX_SUBS_PER_SOCKET:
                logger.warning("WS sub cap (%d) reached, skipping %d",
                               MAX_SUBS_PER_SOCKET, event_id)
                return False
            self._desired.add(event_id)
        self._send_if_connected({"action": "subscribe", "event_id": event_id})
        return True

    def unsubscribe(self, event_id: int) -> None:
        with self._lock:
            self._desired.discard(event_id)
            self._confirmed.discard(event_id)
            self._state_event.pop(event_id, None)
            self._state_livedata.pop(event_id, None)
            self._state_odds.pop(event_id, None)
            self._winprob_history.pop(event_id, None)
        self._send_if_connected({"action": "unsubscribe", "event_id": event_id})

    def sync_subscriptions(self, desired_ids: set[int]) -> None:
        """Bring the connection's subscription set into alignment with
        `desired_ids`. Caller is responsible for keeping under MAX_SUBS."""
        with self._lock:
            to_remove = self._desired - desired_ids
            to_add    = desired_ids - self._desired
        for eid in to_remove:
            self.unsubscribe(eid)
        for eid in to_add:
            self.subscribe(eid)

    def desired_subscriptions(self) -> set[int]:
        with self._lock:
            return set(self._desired)

    # ------------------------------------------------------------------
    # Cache reads (thread-safe, hot path)

    def get_event_snapshot(self, event_id: int) -> dict | None:
        with self._lock:
            return self._state_event.get(event_id)

    def get_livedata_snapshot(self, event_id: int) -> dict | None:
        with self._lock:
            return self._state_livedata.get(event_id)

    def get_odds_snapshot(self, event_id: int) -> dict | None:
        with self._lock:
            return self._state_odds.get(event_id)

    def get_winprob_snapshot(self, event_id: int) -> tuple[float, float, float] | None:
        """Latest implied (p_home, p_draw, p_away) for this event, or None."""
        with self._lock:
            hist = self._winprob_history.get(event_id)
            if not hist:
                return None
            _, ph, pd, pa = hist[-1]
            return (ph, pd, pa)

    def get_winprob_swing(self, event_id: int, window_s: float = 300.0) -> float | None:
        """Max change in the favorite's win probability (max(p_home, p_away))
        over the last `window_s` seconds, in [0, 1]. A big swing means a goal /
        red card flipped the match — a clutch moment. None if < 2 samples."""
        cutoff = time.monotonic() - window_s
        with self._lock:
            hist = self._winprob_history.get(event_id)
            if not hist:
                return None
            leads = [max(ph, pa) for (ts, ph, pd, pa) in hist if ts >= cutoff]
        if len(leads) < 2:
            return None
        return max(leads) - min(leads)

    def get_attack_surge(self, event_id: int, window_s: float = 180.0) -> float | None:
        """Live attacking-surge asymmetry over the last `window_s` seconds, in
        [-1, +1] (+1 = home throwing everything forward, -1 = away). Built from
        the `livedata` situation stream (dangerous attacks, shots, big chances…)
        weighted by threat — a faster, finer momentum pulse than the cumulative
        dangerous-attack %. None when there's too little attacking activity to be
        meaningful (so it never manufactures pressure out of a quiet spell)."""
        cutoff = time.monotonic() - window_s
        with self._lock:
            hist = self._livedata_history.get(event_id)
            if not hist:
                return None
            recent = [(side, sit) for (ts, side, sit) in hist if ts >= cutoff]
        home_w = away_w = 0.0
        for side, sit in recent:
            w = _SITUATION_THREAT.get((sit or "").lower(), 0.0)
            if w <= 0:
                continue
            if side == "home":
                home_w += w
            elif side == "away":
                away_w += w
        if home_w + away_w < 0.6:        # noise floor — not enough threat to score
            return None
        return (home_w - away_w) / (home_w + away_w)

    def recent_situations(self, event_id: int, window_s: float = 150.0) -> list[tuple[str, str]]:
        """Recent (side, situation) frames seen in the last `window_s` seconds.
        Used to surface WS-only events — penalty awarded, red card — that the REST
        incidents lag or omit entirely, so alerts can fire on the live stream."""
        cutoff = time.monotonic() - window_s
        with self._lock:
            hist = self._livedata_history.get(event_id)
            if not hist:
                return []
            return [(side, (sit or "").lower()) for (ts, side, sit) in hist if ts >= cutoff]

    def _record_livedata(self, event_id: int, payload: dict) -> None:
        """Append a (ts, side, situation) sample for the attacking-surge signal.
        Best-effort — never raise on the WS thread. Caller holds self._lock."""
        try:
            side = payload.get("side")
            situation = payload.get("situation")
            if not situation:
                return
            hist = self._livedata_history.get(event_id)
            if hist is None:
                hist = deque(maxlen=80)
                self._livedata_history[event_id] = hist
            hist.append((time.monotonic(), side, situation))
        except Exception:
            pass

    def _record_winprob(self, event_id: int, odds_payload: dict) -> None:
        """Append the implied win-prob derived from an odds frame. Best-effort —
        never raise on the WS thread. Caller holds self._lock."""
        try:
            probs = win_probability.win_prob_from_snapshot(odds_payload)
            if probs is None:
                return
            hist = self._winprob_history.get(event_id)
            if hist is None:
                hist = deque(maxlen=40)
                self._winprob_history[event_id] = hist
            hist.append((time.monotonic(), probs[0], probs[1], probs[2]))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Callbacks

    def add_callback(self, cb: EventCallback) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def _fire_callbacks(self, event_id: int, kind: str, payload: dict) -> None:
        # Copy under lock so callback execution doesn't hold it.
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(event_id, kind, payload)
            except Exception as exc:
                logger.warning("WS callback %s failed: %s", cb, exc)

    # ------------------------------------------------------------------
    # WS lifecycle internals

    def _run_forever(self) -> None:
        while not self._stop.is_set():
            if self._fatal_auth_error:
                logger.error(
                    "BSD WS disabled: %s. Activate the BSD WebSocket "
                    "subscription ($3/month, https://sports.bzzoiro.com/websocket/) "
                    "and restart the process.",
                    self._fatal_auth_error,
                )
                return
            try:
                self._connect_and_block()
            except Exception as exc:
                logger.warning("BSD WS run loop crash: %s", exc)
            if self._stop.is_set():
                break
            # Exponential backoff + jitter
            sleep_s = self._backoff + random.random()
            self._backoff = min(self._backoff * 2, RECONNECT_BACKOFF_MAX)
            logger.info("BSD WS reconnecting in %.1fs", sleep_s)
            time.sleep(sleep_s)

    def _connect_and_block(self) -> None:
        token = _token()
        url = f"{WS_URL}?token={token}"
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        # ping_interval here is the lower-level TCP/WebSocket frame ping,
        # *not* the BSD application ping. We also send an app-level
        # {action: ping} from _ping_loop because BSD's own protocol speaks
        # JSON messages, not WS control frames, for liveness.
        self._ws.run_forever(ping_interval=20, ping_timeout=10)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("BSD WS connected")
        self._connected.set()
        self._backoff = RECONNECT_BACKOFF_INITIAL
        # Re-subscribe to everything we wanted before the disconnect.
        with self._lock:
            desired = list(self._desired)
            self._confirmed.clear()
        for eid in desired:
            self._send_now({"action": "subscribe", "event_id": eid})

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            logger.warning("BSD WS bad JSON frame: %s", raw[:200])
            return

        msg_type = msg.get("type") or msg.get("action") or ""
        event_id = msg.get("event_id")

        if msg_type == "subscribed":
            with self._lock:
                if event_id is not None:
                    self._confirmed.add(event_id)
            # The subscribed frame bundles the most recent snapshots
            # under top-level keys named after the frame types they
            # mirror — *not* under "initial" as the docs imply.
            initial_event = msg.get("event")
            if isinstance(initial_event, dict):
                self._store("event", event_id, initial_event)
                self._fire_callbacks(event_id, "event", initial_event)
            # livedata in the subscribed frame is the last ~30 frames
            # (per the docs). Store the most recent so consumers have
            # a starting point.
            initial_livedata = msg.get("livedata")
            if isinstance(initial_livedata, list) and initial_livedata:
                last = initial_livedata[-1]
                if isinstance(last, dict):
                    self._store("livedata", event_id, last)
                    self._fire_callbacks(event_id, "livedata", last)
            elif isinstance(initial_livedata, dict):
                self._store("livedata", event_id, initial_livedata)
                self._fire_callbacks(event_id, "livedata", initial_livedata)
            initial_odds = msg.get("odds")
            if isinstance(initial_odds, dict):
                self._store("odds", event_id, initial_odds)
                self._fire_callbacks(event_id, "odds", initial_odds)
            return

        if msg_type == "event":
            self._store("event", event_id, msg)
            self._fire_callbacks(event_id, "event", msg)
            return

        if msg_type == "livedata":
            self._store("livedata", event_id, msg)
            self._fire_callbacks(event_id, "livedata", msg)
            return

        if msg_type == "odds":
            self._store("odds", event_id, msg)
            self._fire_callbacks(event_id, "odds", msg)
            return

        if msg_type == "odds_book":
            # No global cache for per-bookmaker odds — too high cardinality.
            self._fire_callbacks(event_id, "odds_book", msg)
            return

        if msg_type == "pong":
            return

        # Undocumented diagnostics frame BSD sometimes pushes; ignore.
        if msg_type == "ingest_debug":
            return

        if msg_type == "error":
            code = (msg.get("code") or "").lower()
            fatal_codes = {"subscription_required", "auth_failed", "invalid_token"}
            if code in fatal_codes:
                self._fatal_auth_error = f"{code}: {msg.get('message', '')}"
                logger.warning("BSD WS fatal: %s", self._fatal_auth_error)
                # Close cleanly — the run_forever loop will see the
                # flag and exit instead of reconnecting.
                if self._ws is not None:
                    try: self._ws.close()
                    except Exception: pass
                return
            logger.warning("BSD WS error frame: %s", msg)
            return

        # Unknown type — log once for visibility
        logger.debug("BSD WS unknown msg type %r: %s", msg_type, raw[:200])

    def _on_error(self, ws: websocket.WebSocketApp, err: Exception) -> None:
        logger.warning("BSD WS error: %s", err)

    def _on_close(self, ws: websocket.WebSocketApp, code: int | None, reason: str | None) -> None:
        logger.info("BSD WS closed: code=%s reason=%s", code, reason)
        self._connected.clear()
        with self._lock:
            self._confirmed.clear()

    def _store(self, kind: str, event_id: int | None, payload: dict) -> None:
        if event_id is None:
            return
        with self._lock:
            if kind == "event":
                self._state_event[event_id] = payload
            elif kind == "livedata":
                self._state_livedata[event_id] = payload
                self._record_livedata(event_id, payload)
            elif kind == "odds":
                self._state_odds[event_id] = payload
                self._record_winprob(event_id, payload)

    # ------------------------------------------------------------------
    # Send helpers

    def _send_if_connected(self, msg: dict) -> None:
        if not self._connected.is_set():
            return
        self._send_now(msg)

    def _send_now(self, msg: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            ws.send(json.dumps(msg))
        except Exception as exc:
            logger.warning("BSD WS send failed (%s): %s", msg, exc)

    # ------------------------------------------------------------------
    # Ping loop (application-level)

    def _ping_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(PING_INTERVAL_S)
            if self._connected.is_set():
                self._send_now({"action": "ping"})


# ---------------------------------------------------------------------------
# Module singleton (lazy)

_singleton_lock = threading.Lock()
_singleton: BSDLiveClient | None = None


def client() -> BSDLiveClient:
    """Return the process-wide singleton WS client. Starts it on first call."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BSDLiveClient()
            _singleton.start()
        return _singleton


def shutdown() -> None:
    """Stop the singleton (used by tests and clean uvicorn shutdowns)."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.stop()
            _singleton = None


def get_live_event_state(event_id: int) -> dict | None:
    """Return the most recent WS-pushed event snapshot for `event_id`,
    or None if we haven't received one yet (or the singleton isn't running).

    Consumers (monitor.py, alarm engine, REST handlers) can use this in
    front of a REST call to short-circuit polling — if the cached snapshot
    is fresh, no upstream HTTP is needed."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            return None
    return _singleton.get_event_snapshot(event_id)


def get_winprob_swing(event_id: int, window_s: float = 300.0) -> float | None:
    """Module-level convenience: favorite win-prob swing for `event_id`, or None."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            return None
    return _singleton.get_winprob_swing(event_id, window_s)


def get_attack_surge(event_id: int, window_s: float = 180.0) -> float | None:
    """Module-level convenience: live attacking-surge asymmetry, or None."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            return None
    return _singleton.get_attack_surge(event_id, window_s)


def recent_situations(event_id: int, window_s: float = 150.0) -> list[tuple[str, str]]:
    """Module-level convenience: recent (side, situation) frames, or []."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            return []
    return _singleton.recent_situations(event_id, window_s)


def is_subscription_active() -> bool:
    """True iff the WS handshake succeeded and the singleton is operational.
    Useful for `/health`-style endpoints."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            return False
        return _singleton._fatal_auth_error is None and _singleton._connected.is_set()
