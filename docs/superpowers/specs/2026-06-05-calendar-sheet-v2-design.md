# Calendar Subscription v2 — design

**Date:** 2026-06-05
**Status:** Approved, pending implementation
**Builds on:** the shipped calendar feature (`2026-06-04-...` flow). Reuses the opaque token, `/calendar/*` endpoints, ICS writer, +60d future window, 5-min cache.

## Context

The calendar subscription sheet ships, but the user wants it to (a) feature the **World Cup** as a dedicated, first card — pick national teams *or* add all — like the WelcomeGuide WC grid, not as the generic "all matches"; (b) make **Custom** a grid of popular teams (Israeli clubs + big European clubs) with search; (c) after choosing, present a **FotMob-style calendar-app chooser** (Apple/Google/Outlook/ICS) instead of auto-opening Apple Calendar; and (d) ensure the feed includes **365scores** matches — currently favorite/custom *team* matches for Israeli/Euro teams are dropped.

## Decisions (settled)
- Calendar targets: **Apple, Google, Outlook, ICS** (all four).
- Popular-teams default: **Israeli + top clubs** (editable list).

## Backend

### 1. 365scores team-matches fix (the must-fix bug)
`calendar_scope._soccer_team_matches(team_id, league=None)`: try `soccer_source.get_team_season(team_id, league_slug=league)` first; if it returns empty **and** a `league` is known, fall back to **filtering that league's window** (`_soccer_league_window(league)`, which already cascades to 365scores) to matches whose home/away id == `team_id`. This makes Maccabi/Hapoel (ligat_haal, sofa/365) and Euro teams resolve.
- Favorites: derive the team's leagues from `SoccerFavoriteTeamDB.competitions` (comma-sep) and pass them.
- Reuse the per-render `cache` so the league window isn't refetched.

### 2. Custom soccer-team contract carries league
Change the subscribe contract so soccer custom teams carry their league (mirrors the existing `custom_bball_teams: [{id, league}]`):
- `CalendarSubscribeRequest`: add `custom_soccer_teams: list[dict] = []` (`[{"id","league"}]`); keep `custom_soccer_team_ids: list[str] = []` accepted for back-compat (treated as league-unknown).
- Stored in `params`; `calendar_scope` custom + favorites paths read `{id, league}` and call `_soccer_team_matches(id, league)`.

### 3. No new scope
- **"Choose WC teams"** → `custom` scope with `custom_soccer_teams: [{id, league:"world_cup"}, …]`.
- **"Add all World Cup"** → existing `world_cup` scope.

## iOS — `CalendarSubscriptionSheet` restructured (top → bottom)

1. **Featured World Cup card (first).** Reuse the WelcomeGuide grid: `SoccerService.getStandingsCached(leagueSlug:"world_cup")` → flatten groups → national-team tiles (`wcTeamChip` pattern: crest + name + checkmark, `C.gold` when selected), multi-select into `@State selectedWCTeams`. Two actions: **Add selected** → `subscribe(scope:"custom", soccerTeams: selectedWCTeams.map{(id,"world_cup")})`; **Add all World Cup** → `subscribe(scope:"world_cup")`.
2. **Favorites card.** Unchanged + include-players toggle.
3. **Custom card.** A grid of **curated popular tiles** + a **Search** button (existing `SearchOverlay` multi-select) merged into one selection set, then **Add selected** → `subscribe(scope:"custom", soccerTeams:[{id,league}], bballTeams:[…], leagues:[…])`. Curated default (id / primary league):
   - Israeli: Maccabi Tel Aviv `130`/ligat_haal, Hapoel Tel Aviv `567`/ligat_haal, Hapoel Be'er Sheva `579`/ligat_haal, Hapoel Haifa `575`/ligat_haal.
   - European: Real Madrid `57`/la_liga, Barcelona `44`/la_liga, Man City `110`/premier_league, Liverpool `1`/premier_league, Bayern `331`/bundesliga, Juventus `73`/serie_a.
   - (PSG/Beitar/Maccabi Haifa not in the search index yet — add when ids are known.) Each tile is a `SearchTeam` (sport "football", `league`, `leagues`, crest via `LogoBadge`/crest URL).
4. **All matches.** Kept as a smaller last row (no longer WC framing).

## iOS — calendar-app chooser (FotMob row)

`CalendarService.subscribe(...)` returns the full response (`token`, `webcalURL`, `httpsURL`). On success, present a **target chooser** (a small sheet/section, Apple · Google · Outlook · ICS) instead of auto-opening:
- **Apple** → `webcalURL` (`webcal://…`) → `UIApplication.open`.
- **Google** → `https://calendar.google.com/calendar/r?cid=<urlEncoded(webcalURL)>`.
- **Outlook** → `https://outlook.live.com/calendar/0/addfromweb?url=<urlEncoded(httpsURL)>&name=Clutch%20Alarm`.
- **ICS** → open `httpsURL` (`https://…ics`; Safari offers to add/download).
- All client-side URL builds from the response — no backend change. New `Views/CalendarTargetChooser.swift` (4 labeled icon buttons, mirrors FotMob). `CalendarService` gains `googleURL/outlookURL/icsURL(from:)` helpers.

## Data flow
pick (WC-teams / favorites / custom tiles+search / all) → `subscribe` mints token → **target chooser** → tap Apple/Google/Outlook/ICS → opens.

## Out of scope (YAGNI)
- No basketball popular-teams grid (custom basketball still via search).
- No per-target deep-link fallbacks beyond the four URLs.
- Keep the +60d future window and 5-min cache.

## Verification
- **Server pytest**: `_soccer_team_matches` fallback (BSD empty + league → league-window-filtered by id; monkeypatched); custom `{id,league}` contract round-trip; ligat_haal team yields matches via the 365-covered window.
- **Live**: subscribe `custom` with Maccabi TA `{130, ligat_haal}` → ICS has Maccabi matches; subscribe WC-teams `{<wc id>, world_cup}` → only those nations' WC matches; the four chooser URLs open the right targets.
- **iOS**: `xcodebuild` build; tap calendar icon → WC card grid loads, select + Add → chooser appears → each target opens.

## Critical files
- `Server/calendar_scope.py` (team-matches fix + custom contract read), `Server/main.py` (`CalendarSubscribeRequest` + params)
- `mobile/iOS/NBA Smart Alarm/Views/CalendarSubscriptionSheet.swift` (restructure), `Views/CalendarTargetChooser.swift` (new), `Services/CalendarService.swift` (return full response + URL builders), curated popular-teams list (new small file or in-sheet), `en/he Localizable.strings`
