# World Cup Drama Engine — perfecting the score

## Context
The Drama Engine (`server/drama_engine.py`) now powers both the live Drama Meter and the feed watch badge, and just gained a live odds/win-probability signal. For the 2026 World Cup we want the score to be unmistakable on the moments the whole world watches: **giant-killing upsets**, **knockout do-or-die** (shootouts, ET, last-minute eliminators), **late group-stage qualification swings**, and **end-to-end thrillers / comebacks**. The engine must keep its 0–10 range, never crash on partial data, and stay cheap on the feed path.

## Current architecture (unchanged core)
`compute_drama(ctx)`: `state + momentum + events + odds` clamped to 7, × `stakes` (competition × round × derby), clamped to 10. Round detection already works via `round_name` keywords; `world_cup` stakes = 1.55, final ×1.40, semi ×1.25, QF ×1.10, R16 ×1.05.

## New architecture
Split into a **live-tension core** (clamped, ×stakes) plus **WC "spike" terms** added on top so upsets and qualification-deciders push toward 10 even outside finals:

```
core  = min(7.0, state + momentum + events + odds + chances)
total = core * stakes
total = min(10.0, total + upset + qualification)
```

### Signals
1. **Knockout-aware state** (enhance `_score_state`): a `_is_knockout(round_name)` helper (knockout / round of / quarter / semi / final / playoff, not "group"). In a knockout, raise the lateness `late_max` for 1- and 2-goal games ("elimination jeopardy" — a late slender lead is agonizing). **ET** in a knockout → higher baseline (~6.5). **Penalty shootout** → peak baseline (~8.5; with WC stakes this reaches ~10).
2. **Events: late knockout winner + comeback** (enhance `_score_events`): amplify a late goal's bonus in knockouts (a 90'+ eliminator), and add a comeback bump when a goal erases a deficit (level/lead-flip), scaled by lateness. Needs the running score on goal incidents (thread `home_score`/`away_score` through `_normalize_recent_incidents`).
3. **Chances** (new `_score_chances`, 0–1, in core): end-to-end thrillers — both teams racking up `big_chances` / `big_chances_missed`. Cheap (WS snapshot).
4. **Upset** (new `_score_upset`, 0–2.5 spike, STRONG): two inputs, take the stronger —
   - *Pre-match underdog winning*: from BSD `prediction` (pre-match 1X2). If the team currently ahead was a clear underdog (low pre-match prob), boost ∝ how big the underdog was.
   - *Live market dissonance*: from live `win_prob` — the team ahead on the scoreline is NOT the market favorite (everyone expects a comeback). boost ∝ `p_trailing − p_leading`.
   Both ×lateness, ×knockout bump. 0 when no odds/prediction (graceful).
5. **Qualification** (new `_score_qualification`, 0–2 spike): group-stage only. From the group standings (top 2 of 4 advance), if a team in this match sits on the qualification bubble (≈ position 2–3, or within a result of crossing it) and the game is close late, boost ∝ lateness. Conservative: 0 whenever standings are missing or the situation is unambiguous (no jeopardy) — never mis-fires.

### Data wiring (graceful, bounded)
- `context_from_match_detail` (Drama Meter / `/soccer/drama`): also populate `pre_match_win_prob` (from `bzzoiro_adapter.event_prediction`, cached), `big_chances` (from detail stats), and — for group games — `standings` rows for the match's group (from the WC standings, cached). All best-effort; absence → that signal contributes 0.
- Feed live path (`_live_drama_ctx` in `soccer_source.py`): add `big_chances` from the WS snapshot and keep `win_prob`/swing. No prediction/standings fetch (kept cheap) → feed gets enhanced state/events/odds/chances + live-dissonance upset.
- `DramaScore` gains `upset`, `chances`, `qualification` fields (additive to `as_dict()`; iOS ignores unknown keys).

## Guards
Every new scorer wrapped/clamped; missing fields → 0; total always clamped 0–10. No new exception can 500 `/soccer/drama` or the matches list.

## Verification
- Unit tests (extend `tests_watch_scores.py` / new `tests_drama_wc.py`): underdog-leading-late → big upset spike; favorite-leading → no upset; shootout → ~10; knockout late 1-goal > group late 1-goal; comeback goal adds; qualification bubble close+late → boost, clear table → 0; everything clamped ≤10; all-empty ctx → no crash.
- Live probe with the BSD token: confirm `prediction` 1X2 keys, `big_chances` stat keys, and WC group `standings` shape before finalizing field names.
- End-to-end on dev after deploy: `/soccer/drama/{id}` shows the new breakdown fields; no 500s on partial data.
