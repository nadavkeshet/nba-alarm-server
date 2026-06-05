# Calendar v3 — id-set team matching + mode-first popup

**Date:** 2026-06-05
**Status:** Approved, pending implementation
**Builds on:** calendar v1/v2. Keeps opaque token, `/calendar/*`, ICS writer, 5-min cache, **future-only +60d window**, app chooser.

## Context / root cause

Custom & favorite TEAM matches are unreliable because the app's search `id` is just *one* provider's id, while match windows use different providers (BSD vs 365scores). Examples: Hapoel TA search id `567` = "Barau FC" in BSD; Man City `110` = "Lorient"; Maccabi basketball search id `33`, but its winner_league game competitor id is `631`. The search response exposes the **full `ids` array** (`Maccabi basketball ids=['33','631']`, `Maccabi football ids=['130','566']`) — so the fix is to **match by the whole id set**, and for basketball, scan a league window at all (custom basketball teams arrive with no league). Also the popular-grid logos are wrong because they were hardcoded as `sports.bzzoiro.com/img/team/{id}/` instead of using the search `logo` (which is BSD *or* 365 per team).

Israeli basketball IS in-window (winner_league has Maccabi vs Hapoel Holon on Jun 8 & 12), so future-only is correct — the bug is purely id/league resolution.

## Backend (`Server/calendar_scope.py`, `Server/main.py`)

**Match teams by id-set across their leagues' windows.**
- Contract (`CalendarSubscribeRequest`): custom teams carry id-set + leagues:
  - `custom_soccer_teams: [{ids:[str], leagues:[str]}]`
  - `custom_bball_teams: [{ids:[str], leagues:[str]}]` (leagues may be empty)
  - Back-compat: still accept the prior `{id, league}` shape and `custom_soccer_team_ids` (treat as one-id / one-league).
- New `_team_matches(ids, leagues, sport, cache)`:
  - soccer → for each league in `leagues`, scan `_soccer_league_window(league)` (memoized; already cascades to 365), include any match whose `home.id`/`away.id` ∈ `set(ids)`.
  - basketball → for each league in `leagues`, or **all `_all_bball_slugs()` when `leagues` is empty**, scan `_bball_league_window(league)`, include any game whose competitor id ∈ `set(ids)`.
  - Drop the single-id `get_team_season` matching for custom teams (it returns the wrong team for non-BSD ids). Window-scan + id-set is correct; the per-render league-window cache keeps cost bounded (shared across teams).
- `collect_matches` custom block reads `custom_soccer_teams`/`custom_bball_teams` (and legacy keys) → `_team_matches`.
- Favorites: `SoccerFavoriteTeamDB` → `ids = [team_id] + alt_team_ids`, `leagues = competitions.split(",")` → `_team_matches(ids, leagues, "soccer")`. Basketball `FavoriteTeamDB` → `_team_matches([team_id], [league], "basketball")`. Keep soccer-competition / bball-league favorite blocks as-is.

## iOS

**`CalendarService`** — custom teams carry id-set + leagues (both sports):
- Replace `SoccerTeam{id,league}` / `BBallTeam{id,league}` usage in `subscribeDetailed` with `{ids:[String], leagues:[String]}` per team (encode to `custom_soccer_teams`/`custom_bball_teams`). Built straight from `SearchTeam.ids` + `SearchTeam.leagues`.

**Mode-first sheet + second popup** (`CalendarSubscriptionSheet`):
- Main sheet shows only the **modes**: Favorites (+ include-players), World Cup, Custom, All — each a row/card.
- Tapping **World Cup** → presents a **second sheet** with the national-team grid (WelcomeGuide pattern) + *Add selected* / *Add all World Cup*.
- Tapping **Custom** → presents a **second sheet** with a **popular football-clubs grid** + a Search button (`SearchOverlay` multi-select) → *Add selected*.
- Subscribe (from either popup) → the existing **Apple/Google/Outlook/ICS chooser**.

**Logo fix + correct popular teams:**
- Popular clubs are resolved by **name via the live search** (`SearchService.search`), filtered to `sport=="football"`, best-name match — so each tile uses the app-correct `SearchTeam` (right `ids`, `leagues`, and `logo`). Tiles render `SearchTeam.logo` (via `LogoBadge`/AsyncImage), never a constructed URL.
- Curated names: Maccabi Tel Aviv, Hapoel Tel Aviv, Hapoel Be'er Sheva, Maccabi Haifa, Real Madrid, Barcelona, Manchester City, Liverpool, Bayern Munich, Juventus. (Resolution drops any name with no football result.)
- Basketball teams: added via the Search button (now resolve correctly via the id-set fix); no curated basketball grid.

## Out of scope
- No curated basketball grid; no past-window (stays future-only +60d per user).

## Verification
- **Server pytest**: `_team_matches` — soccer match by any id in the set across multiple leagues; basketball with empty leagues scans all bball leagues and matches by competitor id (monkeypatched windows incl. an id like `631`); favorites use `[team_id]+alt_team_ids` × competitions.
- **Live (the real proof)**: subscribe `custom` with Maccabi basketball `{ids:["33","631"], leagues:[]}` → ICS contains the **Jun 8 & Jun 12 winner_league games** (Maccabi vs Hapoel Holon). Subscribe Maccabi football `{ids:["130","566"], leagues:["europa_league","ligat_haal"]}` → its matches. Popular tiles show correct crests.
- **iOS**: `xcodebuild`; main sheet shows modes → WC/Custom open second popups → grids load with correct logos → subscribe → chooser.

## Critical files
- `Server/calendar_scope.py` (`_team_matches` + custom/favorites), `Server/main.py` (`CalendarSubscribeRequest`)
- `mobile/iOS/NBA Smart Alarm/Services/CalendarService.swift` (id-set body), `Views/CalendarSubscriptionSheet.swift` (mode-first), new `Views/CalendarPickerSheet.swift` (the second popup with WC/popular grids), `en/he Localizable.strings`
