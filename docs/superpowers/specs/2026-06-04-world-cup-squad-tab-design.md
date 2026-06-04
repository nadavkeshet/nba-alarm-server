# World Cup Squad tab — design

**Date:** 2026-06-04
**Status:** Approved design, pending spec review
**Scope:** Server (FastAPI) + iOS (SwiftUI). No Android.

## Goal

Surface FIFA World Cup 2026 national-team squad call-ups in the app. From a
World Cup nation's team page, show its official 26-man squad. Each player links
to their existing player page **and** to the club team they currently play for
("the team that's playing"). This is the player↔club↔nation **merge** the
feature is built around.

## Why this is possible (verified data chain)

The BSD feed exposes `/api/v2/worldcup/squads/` (current World Cup edition only).
Verified against live dev data on 2026-06-04:

```
WC standings row (our /soccer/league/world_cup/standings)
   → team_id 930 (BSD id; already decoded into iOS StandingRow.teamId)
      → BSD /worldcup/squads/?team=930&status=official → 26 rows
         each row: { player_id, name, jersey_number, position(GK/DF/MF/FW),
                     club, club_country, caps, goals, age, status, call_up_date }
            → bz.player_detail(player_id) → current_team_id 120 + national_team_id 930
               → our /soccer/team/120/matches → "SK Slavia Praha" (tappable club page)
            → our /soccer/player/{player_id} → resolves (PlayerPageView works unchanged)
```

The squad **row** carries the club *name* only, not an id. The club **id**
(`current_team_id`) comes from `bz.player_detail(player_id)` — the merge step.

## Merge algorithm (reused, not new)

The server already resolves a player to multiple teams in the favorites flow:
`main.py::_resolve_national_team_for_player()` calls `bz.player_detail(player_id)`
and `national_team.national_team_id(nationality)` to attach a club `team_id` +
national `alt_team_ids` to a favorite player. The squad enrichment reuses the
same `bz.player_detail()` call to obtain `current_team_id` (club) for each squad
member. No name matching, no new crosswalk.

## Architecture

### 1. Server — one new route + one adapter fn

**`bzzoiro_adapter.py`**
- Add `worldcup_squad(team_id: int) -> list[dict]`: `GET /worldcup/squads/?team={id}&status=official&limit=200`, returns `results`. Cache like other event reads (~6h; squads change rarely). Follows the existing `_cached_get` pattern.

**`main.py`** — `GET /soccer/team/{team_id}/squad`
- Calls `bz.worldcup_squad(team_id)`.
- Enrichment: for each row, attach `current_team_id` (club) + a club crest URL via `bz.player_detail(player_id)`. Fan out the 26 detail calls in a thread pool (the module already uses `ThreadPoolExecutor` for parallel reads in `soccer_source.py`); each `player_detail` is independently cached so repeat squad loads are cheap. `player_detail` failures degrade gracefully → that row keeps `club` name with `current_team_id: null` (club not tappable, never an error).
- Normalize each row (snake_case, matching existing wire style) to:
  `{ player_id, name, jersey_number, position, club, club_country, current_team_id, club_crest, caps, goals, age }`.
- Sort by position group (GK, DF, MF, FW) then `jersey_number`.
- Response: `{ team_id, count, players: [...] }`.
- Empty upstream (coverage gap / squad not announced) → `{ team_id, count: 0, players: [] }` (200, not an error).

### 2. iOS — `SoccerService.swift`

- `struct SquadPlayer: Codable, Identifiable` decoding the row
  (`id` = `player_id`; `playerId, name, jerseyNumber, position, club,
  clubCountry, currentTeamId, clubCrest, caps, goals, age`).
- `struct TeamSquad: Codable { teamId, count, players: [SquadPlayer] }`.
- `getTeamSquad(teamId:) async throws -> TeamSquad` + `getTeamSquadCached(teamId:)`
  via a small `TeamSquadCache` mirroring `TeamSeasonCache` (6h TTL, keyed by team id).

### 3. iOS — `TeamPageView` gains tabs

- Currently matches-only with no tab chrome. Add a slim segmented control
  **Matches | Squad**.
- The **Squad** segment is shown **only for World Cup national teams**:
  `team.leagues.contains("world_cup") || team.league == "world_cup"`. All other
  teams render exactly as today (no segmented control, matches only) — zero
  regression for club/basketball team pages.
- Default selected segment stays **Matches**.

### 4. iOS — squad roster view

- Header line: "26 called up" (count from response), optional call-up date.
- Players grouped into sections **Goalkeepers / Defenders / Midfielders /
  Forwards** from `position`.
- Row: shirt number · name · club (small, muted; tappable when
  `currentTeamId != nil`) · `caps`/`goals` mini-stat.
- **Row tap → `PlayerPageView`** via `SearchPlayer(id: String(playerId),
  sport: "football", …)` (existing nav payload; player route already resolves
  these ids).
- **Club tap → `TeamPageView`** via `SearchTeam(id: String(currentTeamId),
  sport: "football", league: <club league or "">, …)` — the club-merge
  destination. Reuses existing team navigation.
- States: loading (skeleton rows), empty/unavailable ("Squad not announced
  yet"), error → cached fallback then the empty state.

## Out of scope (YAGNI)

- Preliminary/projected call-ups (official 26 only).
- Reverse "called up" badge on the player page.
- Favoriting directly from the squad list (the existing star flow already works
  once you're on the player page / club page).
- Android, xG/other charts, non-World-Cup tournaments (BSD: current WC edition
  only; Euro/Copa/AFCON get separate future endpoints).

## Navigation summary

```
WC standings → tap nation → TeamPageView(team, leagues:["world_cup"])
   → Squad tab → getTeamSquad(team.id) → /soccer/team/{id}/squad
      → grouped roster
         → tap player → PlayerPageView
         → tap club   → TeamPageView (club)
```

## Deployment note

Unlike the prior negative-minute fix (iOS-only, data already present), this
feature **requires the new `/soccer/team/{id}/squad` route to be live on the dev
Railway server** before the app can use it. The server change must be deployed
(push to the `nba-alarm-server` repo that Railway auto-deploys). This is a
required, explicit step — not assumed.

## Risks / considerations

- **Team-id scheme:** Squad lookup needs the BSD numeric team id. From WC
  standings this is exactly `StandingRow.teamId`. A nation reached via global
  `/search` could carry a different id; for MVP the Squad tab only attempts the
  fetch for `world_cup`-league teams and shows the empty state if the id yields
  no squad.
- **Fan-out cost:** 26 `player_detail` calls per squad, each independently
  cached 6h and run in parallel. First load of a given nation pays ~26 upstream
  reads; subsequent loads (and other nations sharing a club) hit cache.
- **Coverage:** BSD serves only the current World Cup edition. Off-tournament or
  unannounced squads return empty → handled by the empty state.
