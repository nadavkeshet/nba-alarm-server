# Clutch Alarm — App Guide (for the Android build)

A self-contained guide to what the app does and the contracts it runs on, written
for an engineer building/maintaining the **Android** client against the **shared**
backend. The iOS and Android apps are thin clients over one FastAPI server
(auto-deploys to Railway) and Firebase Cloud Messaging. Most "features" are server
behaviors the client just renders — so matching the API contracts below buys you
most of parity.

> Reference base URLs (dev): `https://nba-alarm-server-dev.up.railway.app`. The
> client picks dev vs prod in one config constant. Flip to prod before release.

---

## 1. What the app is

An alarm app for live **basketball (NBA + European)** and **football (soccer)**.
Users follow teams / leagues / players, or set per-match alarms, and get a push the
moment a game gets dramatic — a close finish, a comeback, a red card, a goal, a
"Drama Radar" threshold crossing, etc. Pushes are **data-only FCM** so the client
controls presentation.

**Three tabs:** Favorites (personalized feed) · Sports (all schedules) · Active
(your live alarms). Settings + Manage Favorites live behind an avatar/drawer, not a
tab.

**Two id schemes flow through everything** (they overlap numerically, so the prefix
matters):
- `bsd-<id>` — Bzzoiro Sports Data (the normalized primary source).
- `s365-<id>` — 365scores (primary for a couple of leagues, fallback for the rest).

Always pass the source-tagged canonical id back to the server verbatim; never strip
the prefix.

---

## 2. Architecture & data flow

```
Android client  ──HTTP──▶  FastAPI backend  ──▶  BSD REST + WebSocket, 365scores
       ▲                          │  ──▶  PostgreSQL (alarms, favorites, snapshots)
       │                          │  ──▶  soccer_monitor / monitor radar threads
       └────data-only FCM push────┘       (fire alerts; client renders them)
```

The client never talks to BSD/365 directly and never opens the live WebSocket — the
**server** owns the WS and exposes its state via REST (`/soccer/live/*`). The client
just polls.

---

## 3. Server API contract (the important part)

All responses JSON. Times are ISO-8601 UTC unless noted; the client renders local.
Hebrew names ride alongside English as `*_he` fields (see §9).

### Schedule / feed
- `GET /feed?from=YYYYMMDD&to=YYYYMMDD&football=<slugs,csv>&tz=<IANA>` — **batch**
  schedule for a date range across basketball + football. This is what the lists use.
- `GET /soccer/games?league=<slug>&date=YYYYMMDD&tz=<IANA>` — one league/day.
- `GET /soccer/live?leagues=<slugs,csv>` — all currently-live matches.
- Basketball has the analogous `/basketball/*` (and the `/feed` already includes it).

Cache hint: a feed day with any live game is fresh ~25s; "today" ~75s in the
**request tz**; past 12h; future 6h. Poll live days ~every 25s while foregrounded.

### Match detail
`GET /soccer/match/{canonical_id}` → full match:
```
{ id, status, minute, period, round_name, round_name_he?,
  home/away: { id, name, name_he?, score, score_pens?, xg, crest },
  momentum: [{m, v}]            // per-minute signed momentum (+ home, − away)
  xg_per_minute, shotmap, average_positions,
  incidents: [ {type, minute, added_time, is_home, player, assist,
                goal_type:'penalty'|'own_goal'|null, card_type, player_in/out} ],
  stats: { home:{...40+ counters}, away:{...} },
  livedata: { side, situation, x, y, commentary },   // present when live (WS)
  odds, lineups?, groups? }
```
`status` ∈ scheduled / live / ht / et / pens / ft. Incidents include **penalty
goals** (`goal_type:'penalty'`), own goals, plus live-only `penalty`/`var`
situations injected from the WS. Render penalty/own goals with a "(pen.)"/"(o.g.)"
tag; render a standalone `penalty` incident as "Penalty awarded" (live only — a
finished match keeps only the resulting penalty goal).

Other detail endpoints:
- `GET /soccer/match/{id}/lineups` — formation, XI+subs, jersey/position, pre-match
  AI score, post-match rating, `team_name_he?`.
- `GET /soccer/match/{id}/prediction` — `win_prob`, `underdog`/`favorite`
  (with `team_name_he?`), `alert_chance`, `stakes`. BSD-only.
- `GET /soccer/league/{slug}/standings` — table or grouped (`groups[]`), `team_name_he?`.
- `GET /soccer/team/{id}/matches`, `/squad`. `GET /soccer/player/{id}` (+ `?source=`),
  `/player/{id}/stats`.

### Live state (WS-backed, REST-exposed)
`GET /soccer/live/{canonical_id}` → lightweight, zero-REST snapshot:
```
{ ws_active, score, minute, status, period, period_started_at_uts,  // clock anchor
  injury_time, stats, momentum_pressure (−1..+1, + = home pressing),
  livedata:{side,situation,x,y}, odds, win_prob:[h,d,a], win_prob_swing }
```
`ws_active:false` → no live snapshot; fall back to `/soccer/match`. Use
`period_started_at_uts` to tick a second-accurate clock locally. **During a
half-time break the server suppresses the WS clock minute** (it would otherwise read
the break-elapsed, e.g. "11" at HT) — trust `minute`/`status` as given.

### Drama
- `GET /soccer/drama/{id}` → `{ minute, status, drama:{ total(0-10), state, momentum,
  events, stakes, odds, chances, upset, qualification } }`. This is the **Drama
  Meter** number — show `total`. (See §6.)
- `GET /soccer/drama/{id}/timeline` → per-minute history for a graph.

### Favorites (CRUD; POST upserts by PK)
- `…/soccer/favorites/teams` (GET/POST/DELETE), `…/competitions`, `…/players`.
- `GET /soccer/favorites` — combined teams+leagues+players in one call (use for the
  Favorites screen). Each carries its `config` (the criteria blob, §5), `alert_type`,
  `drama_threshold`, `notify_at_kickoff`, `paused`, and `*_he` names.

### Alarms (per-match)
- `POST /soccer/alarm` (alarm_type, match_id, competition_id, player_id, threshold,
  **config**, alert_type), `DELETE /soccer/alarm/{id}`, `GET /soccer/alarms`
  (returns `match_name`, `match_name_he?`).

### Search
- `GET /search?q=&limit=` → `{teams, leagues, players}`. **Matches Hebrew queries**
  (team + league names) as well as English; player search is Latin-only (no Hebrew
  player names exist upstream). Render team rows with `name_he` and league rows with
  your localized catalog label.

### Calendar (ICS subscription)
- `POST /calendar/subscribe` (scope: favorites|world_cup|custom|all, params) → returns
  `webcal://` + `https://` URLs. `GET /calendar/{token}.ics` serves an RFC-5545 feed
  (today…+60d, UTC). Polling sync (~hourly), no push. Hand the user the webcal URL.

### Token registration
- `POST /register-token` (token, old_token, language, sound_channel) — call on FCM
  token rotation (pass `old_token` so the server migrates the user's alarms/favorites)
  **and whenever the app language changes** (so notification text comes localized).

---

## 4. Sports, leagues, ids

Leagues are catalogued server-side (`competition_map.json`) and mirrored in the
client for display order/icons/colors. Football slugs: `premier_league, la_liga,
serie_a, bundesliga, ligue_1, ucl, europa_league, world_cup, euros, ligat_haal,
friendlies`. Basketball: `nba, euroleague, eurocup, bcl, winner_league`.

`world_cup` is "featured" (pinned to the front of lists) when active. Source per
league is BSD-first except `euros`/`ligat_haal` (365scores). The client doesn't pick
sources — it just consumes canonical ids.

---

## 5. Alarms & the criteria config

A favorite **team** or **league** follow, and a **per-match** alarm, all carry the
same `config` criteria blob (snake_case keys). The server expands a follow into
per-match alarms; the client just builds/reads `config`.

```jsonc
{
  "drama_radar":  { "enabled": bool, "threshold": 0..10 },     // Drama Meter ≥ threshold
  "late_drama":   { "enabled": bool, "max_diff": 0..3, "last_minutes": 1..45 },
  "match_moments":{ "enabled": bool,
                    "kickoff","goals","halftime","second_half",
                    "red_cards","penalties","match_end","lineups": bool,
                    "reminder_minutes": int,                    // pre-kickoff reminder, 0=off
                    "alert_type": "alarm"|"silent" },           // ← see note
  "card_penalty": { "enabled": bool, "red_card": bool, "penalty": bool },
  "sensation":    { "enabled": bool },                          // underdog leading
  "comeback":     { "enabled": bool },                          // trailed ≥2, now within 1
  "big_chance":   { "enabled": bool, "sensitivity": "low"|"medium"|"high" }
}
```

Each follow/alarm also has a top-level `alert_type` (**alarm** = loud sound, **silent**
= no sound) and `notify_at_kickoff`. "Just Follow" = a follow with `alert_type:silent`
and criteria off (display-only).

**Match Moments has its OWN independent `alert_type`** (inside its sub-config), because
it's the noisiest criterion (fires for kickoff, every goal, half-time, 2nd half, red
cards, penalties, full-time, lineups). It **overrides** the follow's overall mode for
those moments and **defaults to `silent`**. The UI must:
- show an alarm/silent control inside the Match Moments card (default Silent),
- when the user picks **Alarm**, show a warning that it plays a loud sound for *every*
  moment and is a lot of interruptions.
Server already honors `match_moments.alert_type` (falls back to the follow's mode when
absent, for legacy configs).

**Other alarm types** (server-side, fired by the radar; no extra client config):
match (kickoff), knockout_et, penalty, red_card (legacy), player_alert
(goal/brace/hat-trick/cards), and opt-in **historic moments** (hat-tricks, routs,
comebacks… via `POST /settings/soccer-historic`).

---

## 6. The Drama Engine (server-side)

A 0–10 "is this worth waking up for?" score, computed live. The same score drives the
card meter, the alert firing, and the history graph. Formula:
```
core  = min(7, max(0, state + momentum + events + odds + chances))
total = core × stakes ; total = min(10, total + upset + qualification)
# a LIVE competitive match (diff ≤ 1) floors at 1.0 — never reads a literal 0
```
- **state**: scoreline closeness × lateness (0-0 at 89' is dramatic; at 5' is not).
- **momentum**: the trailing team pressing adds drama (up to +4); a **leading team
  dominating subtracts** drama (down to −1.5) — "1-0 with the leader cruising isn't
  worth waking up".
- **events / odds / chances / upset / qualification / stakes**: late goals & cards,
  market uncertainty + favorite-flip, near-misses/saves, underdog-leading, WC group
  jeopardy, competition×round×derby multiplier.

Client: just show `total` as a 0–10 meter (e.g. red ≥8, orange 5–8, muted <5).

---

## 7. Live data the client renders

From `/soccer/live` (+ the WS overlay already merged into `/soccer/match`): a
second-accurate ticking clock (anchor on `period_started_at_uts`), a win-probability
bar (+ swing pulse), a momentum bar/meter (`momentum_pressure`), a per-minute momentum
**area chart** (green above the zero line = home pressing, navy below = away; filter
out fractional period markers like 45.5/90.5), a commentary line, and a ball-position
tracker. All gated on `ws_active`.

---

## 8. Notifications — semantics & the honest truth

Pushes are **data-only FCM**; the client builds the local notification. The payload
data carries `title`, `body` (already localized server-side), `alert_type`
(`alarm`|`silent`), and `sound`/`channel_id`.

- **alarm** → play the chosen loud alarm sound; high-importance channel.
- **silent** → no sound; quiet channel.

**Do NOT claim the alerts bypass silent mode or auto-pass Do Not Disturb.** They are
time-sensitive notifications:
- They do **not** override the device's silent/mute switch — if the ringer is off,
  no sound. (Tell users to keep the ringer on; the app has an explicit "Silent Mode
  Is On" warning for this.)
- They only surface during Focus/Do Not Disturb **if** the user allows it. Provide a
  DND setup guide ("add Clutch Alarm under Settings → Focus / app notification
  categories"), but frame it as an opt-in the user enables — not an automatic bypass.

Android specifics to replicate the iOS behavior: a high-importance notification
channel for `alarm`, a low/silent channel for `silent`, full-screen-intent + screen
wake for the loud alarm (the Android app already has Expo config plugins for alarm
audio attributes, full-screen intent, screen wake, and bundled sound files), and the
FCM token migration on `/register-token`.

---

## 9. Localization (English + Hebrew, RTL)

- **UI strings**: full EN + HE catalogs. RTL follows the locale; keep **score rows
  LTR** so "2 - 1" reads correctly.
- **Team names**: the server stamps `name_he` / `team_name_he` (and `name_he` on
  standings, lineups, player club + national team, prediction, search, favorites,
  alarms `match_name_he`). Prefer the `*_he` field in Hebrew mode; fall back to the
  English name. **Player names stay Latin** (no Hebrew source data).
- **Round/stage names**: `round_name_he` (Final→גמר, Group Stage→שלב הבתים…).
- **Search**: works in Hebrew for teams + leagues (server-side); player search is
  Latin-only.
- **Notifications**: localized server-side per the user's registered language — so
  re-register the token on language change (§3).

---

## 10. Gotchas & contracts (the things that bite)

1. **Id prefixes are load-bearing** — `bsd-*` vs `s365-*` overlap numerically; pass
   canonical ids back verbatim. For player ids, pass `?source=bsd|s365` when known.
2. **Match Moments independent alert mode** defaults to **silent** and overrides the
   follow's mode; warn on switching to alarm (§5).
3. **No silent-mode / no auto-DND bypass** — never claim it (§8).
4. **Penalty as a distinct "awarded" event exists live only**; finished matches keep
   only the penalty **goal** (`goal_type:'penalty'`). Tag penalty/own goals.
5. **Half-time clock**: trust the server `minute`/`status`; don't compute from the WS
   break clock.
6. **Drama never reads a literal 0** for a live competitive match (floored at 1.0) —
   that's intentional, not a bug.
7. **Caching**: poll live days ~25s; "today" is resolved in the request tz; don't hold
   stale schedules.
8. **`/feed` is the batch list source**; the per-league endpoints are for drill-downs.

---

*This guide reflects the current shared backend (football v3). The iOS app is the
reference implementation; the server is the source of truth for all behavior above.*
