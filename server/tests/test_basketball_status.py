"""Regression tests for BSD basketball status normalization.

The live BSD basketball API (`/basketball/api/v2`) reports in-progress games
with `status == "live"` — NOT "inprogress" as the original docstring assumed.
An unmapped "live" fell through to the pre-game default, so live games showed
up in the schedule feed as "Scheduled" with a frozen score.
"""

from basketball_source import _bsd_status_to_state


def test_live_maps_to_in_progress():
    # The real symptom: upstream emits "live" for an in-progress game.
    assert _bsd_status_to_state("live") == ("in", "In Progress", 0)


def test_inprogress_still_maps_to_in_progress():
    # Defensive: keep honoring the legacy enum value too.
    assert _bsd_status_to_state("inprogress") == ("in", "In Progress", 0)


def test_finished_maps_to_final():
    assert _bsd_status_to_state("finished") == ("post", "Final", 4)


def test_scheduled_maps_to_pre():
    assert _bsd_status_to_state("scheduled") == ("pre", "Scheduled", 0)


def test_postponed_and_cancelled():
    assert _bsd_status_to_state("postponed") == ("pre", "Postponed", 0)
    assert _bsd_status_to_state("cancelled") == ("pre", "Cancelled", 0)
