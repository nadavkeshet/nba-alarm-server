# Hebrew Localization Audit — Football, End to End

> **Status: gaps fixed.** Server (`9561b96`) now attaches `name_he` on lineups,
> player detail (club + national team), player stats (opponent), prediction
> (underdog/favorite), WC squad (club), the alarms list (`match_name_he`), and
> translates round/stage names (`round_name_he`). iOS (`c8d74ce`) localizes the
> Settings/Drawer literals, the search league rows, and consumes `round_name_he`;
> lineup + player-page Hebrew names light up from the server change. Player names
> remain Latin (no Hebrew source data) by design. The body below is the original
> audit for reference.

Scope: does Hebrew work across **everything** — favorites, alarms, search, match
summary, notifications, calendar, player/team/league pages? Audited the static
string catalogs, the server notification builders, the dynamic team-name
translation flow (`Translators.json` → `team_names_he`), and every iOS surface.
Server gaps were verified against the live backend.

## Verdict

**Hebrew is ~90% complete.** The static UI, all notifications, and the *primary*
team-name surfaces are fully localized. The gaps are concentrated in (a) a handful
of **secondary server endpoints that don't attach `name_he`** (so soccer team names
show in English there), (b) **round names** never translated, and (c) **~5
hardcoded English literals** in Settings/Drawer/Search that bypass the catalog.

---

## ✅ What works (Hebrew confirmed)

### Static UI string catalog — complete
- **824 `en` keys / 822 `he` keys, 0 missing, 0 orphans.** Every screen's labels,
  buttons, alarm criteria, follow intros, statuses, league names, settings, guides,
  empty states — translated.
- (Minor: 5 duplicate keys in `en.lproj` — `guide.setupTitle`,
  `guide.welcome.body/title`, `settings.title`, `soccer.alarm.comeback` — last
  definition wins; both langs translated, just untidy.)

### Notifications — complete (server-built, iOS just displays)
- `i18n.py`: **every** soccer builder has a real `lang=="he"` branch — goal,
  kickoff, half-time, second half, match end, late drama, drama radar, sensation,
  comeback, big chance, red card, penalty, lineups, reminder, knockout/ET, plus all
  player builders (goal/brace/hat-trick/yellow/red) and **all 30 historic-moment
  categories**. Competition names translated (`פרמייר ליג`, `ליגת האלופות`, …).
- iOS `AppDelegate` displays the server-provided title/body verbatim and only picks
  the sound — no client-side English. Token re-registers on language change so text
  arrives in the right language.

### Team names — translated on the PRIMARY surfaces
- **Flow:** `Translators.json` (2.1 MB, 6 providers, Hebrew→Latin aliases) →
  `team_names_he.py` reverse index (**11,274 teams**, English→Hebrew, diacritic +
  affix-normalized) → stamped as `name_he` via the `@_with_hebrew` decorator.
- **iOS:** `SoccerTeamSide.displayName` returns `nameHe` in Hebrew mode (the curated
  client `TeamNames` map is basketball-only, so soccer relies on the server's
  `name_he`).
- **Surfaces that show Hebrew team names** (server attaches + iOS uses `displayName`):
  feed / `/soccer/games`, match cards (`SoccerMatchCardView`), Sports rows,
  Favorites feed & list, match-summary **header**, **standings** (`team_name_he`),
  Team page, League page, `/soccer/live`, `/soccer/favorites/teams`, `/search`.

### League / competition names — complete
Everywhere via `LeagueCatalog.info(for:).label` (Localizable key) on the client and
`competition_map.json` `name_he` / `i18n` on the server.

### RTL — correct
Follows the locale (no global force). Score rows are deliberately pinned LTR
(`.environment(\.layoutDirection, .leftToRight)`) so "2 - 1" reads correctly — the
right call. No broken RTL views found.

### Calendar & alarm-config sheets — localized
Criteria cards, follow intros, calendar mode chooser all use the (complete) catalog.

---

## ❌ Gaps (Hebrew missing)

### A. Server endpoints that DON'T attach `name_he` → soccer team names render in English
Verified live against the dev backend:

| Endpoint / surface | Evidence | Impact |
|---|---|---|
| **Lineups tab** `/soccer/match/{id}/lineups` | `team_name: "Argentina"`, `team_name_he: <MISSING>` | Match-summary **Lineups tab** shows team names in English |
| **Player page** `/soccer/player/{id}` | `team_name: "Inter"` & `national_team_name: "Argentina"`, both no `_he` | Player detail club + national team in English |
| **Prediction** `/soccer/match/{id}/prediction` | underdog `team_name: "Honduras"`, no `_he` | Sensation/odds names in English |
| `/soccer-alarms` (list) | team names, no `name_he` | Active-alarms list team names in English |
| `/soccer/favorites/players` | club `team_name`, no `team_name_he` | Favorited-player club in English |
| `/soccer/team/{id}/squad` | player club + national team, no `_he` | WC squad screen in English |
| `/soccer/player/{id}/stats` | opponent names, no `_he` | Recent-games opponents in English |

> Compounding factor: because the iOS curated `TeamNames` map has **no soccer
> entries**, when the server omits `name_he` the client has nothing to fall back to,
> so it shows the raw English name. The fix is server-side: attach `name_he` on
> these endpoints (the translator + dict already exist — they're just not called).

### B. Round names — never translated (app-wide, tournaments)
`round_name` ("Group Stage", "Final", "Quarter-Final", …) is returned verbatim from
the provider with no Hebrew mapping anywhere. Shows English in Hebrew mode wherever
the round is displayed (match header, league page).

### C. iOS hardcoded English literals (bypass the catalog)

| File:line | Literal | Severity |
|---|---|---|
| `SettingsView.swift:45` | "Feed Options" (section header) | **HIGH** |
| `SettingsView.swift:49` | "Show Favorite Player Matches" (toggle) | **HIGH** |
| `SettingsView.swift:52` | "Include matches for players you've favorited in your feed." | **HIGH** |
| `DrawerMenuView.swift:41` | "Never miss a clutch moment" (tagline) | MEDIUM |
| `SearchOverlay` (league row) | shows raw server `lg.name` instead of `LeagueCatalog.label` | MEDIUM |
| `ActiveView` / `MatchSummaryView` | "LIVE" badge hardcoded (other statuses are localized) | MEDIUM |
| `MatchSummaryView:~1016` | "Team" (table header) | LOW |
| `DrawerMenuView` / `DNDGuideSheet` | "Clutch Alarm", "Apple ID" | non-issue (brand / proper noun) |
| `ConditionCard.swift:65` | "Target" | non-issue (SwiftUI preview only) |
| `DebugView` (×5) | various | non-issue (internal debug screen) |

### D. Player names — always Latin
Soccer player names (lineups, incidents, search, player page) are shown in Latin
because the data sources carry no Hebrew player names. **Expected/acceptable** — not
a regression, just a data limitation.

---

## Per-area scorecard

| Area | Hebrew status |
|---|---|
| Favorites (teams, leagues) | ✅ team/league names + all UI in Hebrew |
| Favorites (players) | ⚠️ UI Hebrew; **club name English** (G-A) |
| Alarms — config sheets | ✅ fully localized |
| Alarms — active list | ⚠️ UI Hebrew; **team names English** (G-A) |
| Search | ⚠️ teams/players get Hebrew; **league rows show English name** (G-C) |
| Match card / feed / Sports | ✅ fully Hebrew |
| Match summary — header/details/stats | ✅ Hebrew (team names + labels) |
| Match summary — **Lineups tab** | ❌ **team names English** (G-A) |
| Match summary — Standings tab | ✅ Hebrew |
| Player page | ❌ **club + national team English** (G-A); player name Latin (expected) |
| League / Team pages | ✅ Hebrew |
| Notifications (all types) | ✅ fully Hebrew |
| Calendar | ✅ localized UI |
| Round names (tournaments) | ❌ **always English** (G-B) |
| Settings / Drawer | ⚠️ mostly Hebrew; **~4 hardcoded English strings** (G-C) |
| RTL layout | ✅ correct |

---

## Recommended fixes (small, well-scoped)

1. **Attach `name_he` on the gap endpoints** — call `team_names_he.attach()` /
   `hebrew_name()` in: lineups, player detail (club + national team), prediction,
   alarms-list, favorites/players, squad, player-stats. (~7 spots; translator + dict
   already exist.)
2. **Translate `round_name`** — add a small `i18n` map (Group Stage/Final/Semi/QF/
   Round of 16…) and a `round_name_he` field.
3. **Localize the Settings/Drawer literals** — 4 new keys + swap the hardcoded
   `Text("…")` for `String(localized:)`.
4. **Search league rows** — render `LeagueCatalog.info(for: lg.id).label` instead of
   `lg.name`.
5. (Optional) De-dupe the 5 repeated `en.lproj` keys; localize the "LIVE" badge.
