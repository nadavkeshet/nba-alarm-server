"""Live attacking-surge signal (livedata situation stream) + pressure blend."""
import collections
import bsd_websocket as ws
from drama_engine import blend_pressure


def _client_with(history):
    c = ws.BSDLiveClient()
    c._livedata_history[1] = collections.deque(history, maxlen=80)
    return c


def test_surge_favors_the_pressing_side():
    # Timestamps are monotonic() seconds; use "now" so samples fall in the window.
    import time
    t = time.monotonic()
    c = _client_with([(t, "home", "dangerous"), (t, "home", "shotontarget"),
                      (t, "home", "bigchance"), (t, "away", "possession")])
    s = c.get_attack_surge(1, window_s=180)
    assert s is not None and s > 0.8           # home strongly pressing


def test_surge_none_when_only_benign():
    import time
    t = time.monotonic()
    c = _client_with([(t, "home", "possession"), (t, "away", "throwin"),
                      (t, "home", "safe")])
    assert c.get_attack_surge(1) is None        # noise floor — no manufactured pressure


def test_surge_none_outside_window():
    import time
    # Old relative to now. Not ts=0.0: monotonic() counts from boot, and a fresh
    # CI VM can be under 180 s old, which puts 0.0 inside the window.
    c = _client_with([(time.monotonic() - 3600, "home", "goal")])
    assert c.get_attack_surge(1, window_s=180) is None


def test_surge_unknown_event_is_none():
    assert ws.BSDLiveClient().get_attack_surge(999) is None


def test_blend_pressure_weights_surge_higher():
    assert abs(blend_pressure(0.2, 0.8) - 0.56) < 1e-9   # 0.4*0.2 + 0.6*0.8
    assert blend_pressure(None, 0.5) == 0.5
    assert blend_pressure(0.3, None) == 0.3
    assert blend_pressure(None, None) is None
    assert blend_pressure(1.0, 1.0) == 1.0               # clamped
