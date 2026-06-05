"""Local xG estimator (P1 stub).

Goal: a local xG estimator the Drama Engine can fall back to when upstream xG is
down or doesn't have xG for a particular shot, compute it locally from
`(x, y, body_part, situation)`.

Status: **stub only in P1.** The `soccer-xg` library (ML-KULeuven, Apache 2.0)
that ships pretrained models doesn't install cleanly on Python 3.14 yet
(needs `pkg_resources` which 3.14 doesn't ship). Per the v3 spec §9 risk doc,
local xG fallback is deferred to P3. Until then, `estimate_xg()` returns `None`
and callers must handle the missing value.

When P3 lands the model, swap the implementation; callers don't need to change.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_warned = False


def estimate_xg(shot: dict) -> float | None:
    """Estimate xG from a shot dict.

    Expected shot shape:
        {
            "playerCoordinates": {"x": float, "y": float},
            "bodyPart": str,
            "situation": str,   # e.g. "open-play" | "set-piece" | "penalty"
        }

    Returns a float in [0, 1] or None if no model is available.
    """
    global _warned
    if not _warned:
        logger.info("xg_fallback.estimate_xg called but no model loaded (P1 stub)")
        _warned = True
    return None


def is_available() -> bool:
    """True if a local xG model is loaded. P1 stub always returns False."""
    return False
