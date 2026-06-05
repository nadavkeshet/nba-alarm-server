# Football "Match Moments" alert — design

**Date:** 2026-06-05
**Status:** Approved, pending implementation

## Context
A featured, default-on "regular alerts" bundle for football favorite TEAMS (and opt-in for favorite leagues): fires on kickoff, goals, HT start, HT end (2nd-half kickoff), red cards, penalties, match end, and confirmed lineups, plus a user-set pre-match reminder (default 30 min). Must work for BOTH BSD- and 365/Sofascore-sourced matches. The BSD WebSocket (`BSDLiveClient`, already connected) is used as a low-latency accelerator; the actual firing is a single universal REST evaluator so 365 matches are covered identically.

## Decisions (settled)
- **Single bundle** (one toggle = all events); only extra control is reminder minutes.
- **Default-ON for teams, OFF (opt-in) for leagues.**
- **All in one go** (live events + pre-match pass).

## iOS (`Views/SoccerCriteria.swift`, `TeamAlarmConfigSheet.swift`, `SoccerService.swift`, strings)
- In `MatchCategory`, move `.matchMoments` to **first**; render it as a **featured** card (accent border/tint).
- Description → "Regular alerts like all other sports apps." (`soccer.alarm.matchMomentsSub`).
- Collapse the per-event sub-toggles (kickoff/halftime/extra-time/penalties) into the bundle — the only control inside the card is a **stepper "Remind __ min before kickoff"** bound to a new `SoccerCriteria.mmReminderMinutes: Int = 30` (range 0…120, step 5; 0 = off).
- Default criteria: teams → `enabled` includes `.matchMoments`; **leagues → `.matchMoments` NOT in `enabled` by default** (featured-first but off). Set per follow-type in `TeamAlarmConfigSheet`.
- `SoccerFollowConfig.MatchMoments` → `{ enabled: Bool, reminder_minutes: Int }` (drop the kickoff/halftime/extra_time/penalties keys, or keep them ignored for back-compat). `followConfig()` serializes accordingly.
- Localize new strings EN/HE.

## Server — universal live evaluator (`soccer_monitor.py`)
Expand the `match_moments` branch in `_evaluate_alarm` to fire the FULL set from the polled match `detail` (works for BSD + 365 + Sofa). When `cfg.enabled`, fire (state-encoded dedup keys via `_fire`/`_fire_once`, `one_shot=False`):
- **kickoff** — status→live/1h → `mm_kickoff`.
- **goal** — `home.score`/`away.score` (or a new goal incident) → `mm_goal_{h}-{a}` (each scoreline once). Body: "GOAL — {home} {h}-{a} {away}".
- **HT start** — status==ht → `mm_ht`.
- **HT end / 2nd half** — status==live & minute≥46 (period 2h) → `mm_2h`.
- **red card** — `_find_recent_red_card(detail)` → `mm_red_{minute}`.
- **penalty** — `_recent_incident(detail, types=("penalty",), window_min=2)` → `mm_pen_{minute}`.
- **match end** — status in (ft, aet) → `mm_ft` (fire BEFORE the terminal-status sweep deactivates the row).
- Keep gating on `cfg.get("enabled")`; ignore the legacy sub-flags (bundle = all).
**WS accelerator:** ensure each active favorite/live match is subscribed on `BSDLiveClient` (so its `event`/`livedata` frames wake the radar → immediate re-evaluate for BSD; ~5s). 365 has no WS → fires on the normal ~25s poll (same evaluator). Verify the wake callback path and that favorite matches are subscribed (likely already via `get_soccer_match_detail`'s WS use; add an explicit `sync_subscriptions` of the watched ids if not).

## Server — pre-match pass (REST, universal) (`soccer_monitor.py`)
For each favorite TEAM with match_moments enabled, track its NEXT upcoming match (cache per team, refresh ~10 min, via the team's fixtures / feed):
- **reminder** — when `0 < kickoff−now ≤ reminder_minutes` → fire once `mm_reminder`. Body: "{home} vs {away} starts in {n} min".
- **confirmed lineups** — when within ~90 min of kickoff, poll `event_lineups(canonical)` (BSD `bzzoiro_adapter.event_lineups` / Sofa `get_sofa_lineups`); when `lineup_status == "confirmed"` fire once `mm_lineups`. Body: "Lineups confirmed — {home} vs {away}".
Bound cost with per-team caches + only polling within the pre-match window.

## Server — i18n (`i18n.py`)
Add titles + body builders (EN+HE) for: goal, ht_start, ht_end, match_end, lineups_confirmed, pre_match_reminder. Reuse existing kickoff / halftime / red_card / penalty. Wire imports in `soccer_monitor.py`.

## Verification
- **Server pytest** (`tests/test_soccer_alerts.py` style, monkeypatched): the expanded match_moments fires each event once for a sequence of match states (goal scoreline, ht, 2h, red, pen, ft) for BOTH a BSD-shaped and a 365-shaped detail; dedup keys stable; reminder fires at the threshold; lineups fire on confirmed.
- **Live**: against a real in-progress match (there are live games), confirm the evaluator detects goal/status/red/penalty from the polled detail; confirm the WS frame wakes the radar for a BSD match.
- **iOS**: `xcodebuild`; the Match Moments card is first/featured/default-on for a team, off for a league, with the reminder stepper.

## Critical files
- `Server/soccer_monitor.py` (live evaluator + pre-match pass + WS subscribe), `Server/i18n.py` (texts)
- `mobile/iOS/NBA Smart Alarm/Views/SoccerCriteria.swift`, `Views/TeamAlarmConfigSheet.swift`, `Services/SoccerService.swift`, en/he `Localizable.strings`
