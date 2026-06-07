# Clutch Alarm — Football (Soccer) Features, End to End

A complete map of every football feature in the app: data sourcing, the schedule
feed, live match depth, the Drama Engine, the full alarm/alert system, favorites,
the calendar, notifications, search, and the iOS surfaces — plus what is *not*
built. Basketball is implemented side-by-side but is out of scope here except
where shared.

**Stack:** SwiftUI iOS app (`mobile/iOS`) ↔ FastAPI backend (`Server/`,
auto-deploys to Railway). The iOS app points at the dev backend while v3 soccer is
iterated; data-only FCM pushes let the app control alert presentation.

---

## 1. Architecture at a glance

```
iOS (SwiftUI)                         FastAPI backend                       Data sources
─────────────                         ───────────────                       ────────────
SportsView / FavoritesFeedView  ──►   /soccer/games, /feed            ──►   BSD REST  (bzzoiro_adapter)
MatchSummaryView                ──►   /soccer/match/{id}              ──►   BSD WebSocket (live)
  live insights / momentum      ──►   /soccer/live/{id}               ──►   365scores (score365_*_adapter)
  drama meter / timeline        ──►   /soccer/drama/{id}[/timeline]
Alarm config sheets             ──►   /soccer/favorites/*, alarms     ◄──   soccer_monitor radar thread
Calendar sheet                  ──►   /calendar/*  (ICS feed)               drama_engine (scoring)
AppDelegate (FCM)               ◄──   data-only push (i18n EN/HE)           PostgreSQL (alarms, favorites, snapshots)
```

Two id schemes flow through everything (they overlap numerically, so the prefix
matters):
- **`bsd-<id>`** — Bzzoiro Sports Data (the normalized primary source).
- **`s365-<id>`** — 365scores (primary for a couple of leagues, fallback for the rest).

---

## 2. Data sources & league coverage

### Sources
| Source | File | Role | Transport |
|---|---|---|---|
| **BSD (Bzzoiro)** | `bzzoiro_adapter.py` | Primary for 8 leagues | REST (`/api/v2`) + WebSocket (`/ws/live/`) |
| **365scores** | `score365_soccer_adapter.py` | Primary for 2 leagues, fallback for all | REST |
| **Router** | `soccer_source.py` | Picks source per league, normalizes both shapes, fans out detail calls, overlays WS | — |

Auth: `BZZOIRO_API_TOKEN` (Token header). The router cascades **BSD → 365scores**
on failure/empty for BSD-primary leagues.

### Competition map (`config/competition_map.json`)
Each slug → `{ name_en, name_he, primary_source, bsd_id, score365_id }`. The single
source of truth for source selection.

| Slug | Primary | Notes |
|---|---|---|
| premier_league, la_liga, serie_a, bundesliga, ligue_1, ucl, europa_league, world_cup, **friendlies** | BSD | BSD → 365 fallback (friendlies use BSD WebSocket too) |
| **euros** | 365scores | 365 only (no BSD id) |
| **ligat_haal** (Israeli Premier) | 365scores | 365 only |

The iOS league catalog (`AppearanceManager.swift` → `LeagueCatalog`) is the display
source of truth: order, icon, color, sport, season-start floor, and "featured"
ranking (World Cup is promoted when active).

---

## 3. Schedule & feed

| Endpoint | Purpose |
|---|---|
| `GET /soccer/games?league=&date=&tz=` | Matches for one league/date |
| `GET /feed?from=&to=&football=&tz=` | One-shot batch schedule (soccer + basketball) — what the lists actually use |
| `GET /soccer/live?leagues=` | All currently-live matches |

**Caching (server, `_feed_cache`):** TTL resolves "today" in the **request tz**
(not US-Eastern — a fix this iteration; the Eastern bug made boundary matches read
as future and cache 6 h). Policy: past 12 h, today 75 s, future 6 h, and **any live
day → 25 s**. An empty result is only cached if the source confirmed it
("trustworthy"), so a transient upstream error never hides real fixtures.

**Caching (iOS, `ScheduleCache` actor):** stale-while-revalidate keyed on
`(league, date)`; past 24 h, today 90 s, future 6 h. `schemaVersion` bumps drop
stale persisted entries on wire-format change.

**iOS surfaces:**
- **Sports tab** (`SportsView`): sport pills (All / Football / Basketball), date nav
  (−5…+15 days, "load earlier/later"), sections by date→league (featured →
  favorited → canonical), live polling every 25 s, star to follow a league.
- **Favorites tab** (`FavoritesFeedView`): same grouping but only favorited
  teams/leagues/players (+ manual alarms surface here); World Cup promo when the
  user has no favorites yet.
- **Match card** (`SoccerMatchCardView`): status pill + ticking live clock, score
  (or "vs"/"–" in no-spoiler), favorite stars, compact **drama meter** (red ≥8 /
  orange 5–8 / muted <5), xG-pressure fallback.

---

## 4. Match detail & live data

`GET /soccer/match/{canonical_id}` → `get_soccer_match_detail()` fans out (parallel)
event detail + stats + incidents, normalizes, enriches incident names (short →
full via player lookups), derives shootout scores from the shotmap, then overlays
the live WebSocket snapshot. Returned shape includes:

- **Core:** status, minute, period, score (+ shootout), competition, round, derby,
  kickoff, Hebrew team names (`name_he`).
- **`momentum`** — per-minute signed momentum (+ home / − away). Drives the
  momentum area chart. (BSD emits 45.5/90.5 period markers — filtered client-side.)
- **`xg_per_minute`**, **`shotmap`** (per-shot xG, location, body part, situation,
  outcome), **`stats`** (40+ per-team counters), **`average_positions`**.
- **`incidents`** — goals (with **`goal_type`**: penalty / own_goal), cards, subs,
  VAR, penalties, period/injury markers; goal build-up `sequence` for the shotmap
  assist line.
- **`lineups`** — formation, XI + subs, jersey/position, pre-match AI score,
  post-match rating, photos. (`/soccer/match/{id}/lineups`.)
- **`odds`**, **`livedata`** (ball position + situation), **standings/groups**.

### Live (WebSocket) layer — `bsd_websocket.py` + `/soccer/live/{id}`
The WS is a **live-only enrichment layer** (a past match returns only its final
snapshot — confirmed by test; no historical play-by-play). It maintains in-memory
snapshots and bounded histories:

- **Clock anchor** (`period_started_at_uts`) → second-accurate ticking clock on the
  client. During a **break (half-time/ET interval)** the WS clock counts the break
  elapsed, so the overlay no longer overwrites the real minute with it (a fix this
  iteration — it was reporting "minute 11" at HT and breaking the drama score).
- **`momentum_pressure`** (`blend_pressure` of cumulative dangerous-attack % + a
  live situation-stream **attack surge**).
- **`win_prob`** + **`win_prob_swing`** (implied from ~12-bookmaker odds).
- **Situation stream** (`recent_situations`): penalty_rewarded, red_card, goal, var.
  These are surfaced as **incidents** in the overlay (penalty awards / red cards
  live only on the WS, not REST), and the radar **wakes** on them.

**iOS live insights panel** (`LiveMatchWidgets`): live clock, win-probability bar
(with swing pulse), momentum bar (presser arrow + team abbrevs), commentary ticker,
and a **landscape live ball-tracker pitch**. Gated on `wsActive`. The full
**momentum area chart** (green above / navy below the zero line) lives in the
Details tab and renders from the per-minute `momentum` series.

---

## 5. The Drama Engine (`drama_engine.py`)

A 0–10 "is this worth waking up for?" score. **One shared computation** powers the
card meter, the radar that fires alerts, and the history graph — via
`soccer_source.enrich_drama_ctx()` (unified this iteration; the card endpoint and
the radar previously used different signal subsets and disagreed).

### Formula
```
core  = min(7, max(0, state + momentum + events + odds + chances))
total = core × stakes
total = min(10, total + upset + qualification)
# live competitive match (diff ≤ 1, live-like) floors at 1.0 — never a literal 0
```

### Components
- **state (0–7):** scoreline closeness × lateness. Tied base 1.5 (late-max 2.5),
  1-goal 2.0, 2-goal 1.0, blowout 0.3; knockout raises late-max (elimination
  jeopardy); ET≈5.5–6.5, pens 8.5; HT ×0.6, FT ×0.4. **Earliness damper** below
  25′ — phase-gated so a later phase is never treated as "early" (fix this
  iteration).
- **momentum (−1.5…+4):** the user's "pressure vs scoreline" insight. A **trailing**
  team pressing is dramatic (up to +4); a **leading team dominating subtracts**
  drama (down to −1.5) — "1-0 with the leader cruising isn't worth waking up".
- **events (0–4.5):** late goals, equalizers, penalties awarded (1.8), red cards
  (1.6), missed penalties (1.2), VAR, big chances — each with a lateness bonus.
- **odds (0–2):** market uncertainty (coin-flip = tense) + favorite-flip swing.
- **chances (0–1.5):** big chances, end-to-end, near-misses/woodwork, big saves,
  errors-leading-to-goals.
- **upset (0–2.5 spike):** underdog leading (pre-match odds + FIFA-rank fallback).
- **qualification (0–2.5 spike):** World Cup group best-thirds jeopardy from live
  group tables.
- **stakes (0.6–2.0×):** competition base (WC 1.55 … Liga Israel 0.85) × round
  (final 1.40, semi 1.25, QF 1.10…) × derby 1.1.

### Drama surfaces
- `GET /soccer/drama/{id}` — total + full breakdown (the meter).
- `GET /soccer/drama/{id}/timeline` — per-minute history; **real snapshots** logged
  every radar tick to the `drama_snapshots` table merged with reconstruction fill,
  so the graph is 1:1 with what the engine actually scored.

---

## 6. Alarms & alerts (`soccer_monitor.py`)

### The radar loop
A single background thread. `POLL_INTERVAL = 25 s`, short-circuited by a **WS wake**
on score/penalty/red/goal/var frames. Two passes per tick:
- **Pass 1 — per-match alarms:** group by match, fetch detail once, evaluate.
- **Pass 2 — competition/team follows:** per followed comp, fetch live matches,
  compute drama **once** (shared across all followers), evaluate synthesized alarms.
- **Pre-match pass (~60 s):** kickoff reminders + confirmed-lineup alerts for
  favorite teams' next match.

**Retroactive guard:** `_armed_minute[alarm:match]` records the minute first seen;
a moment fires only if `minute ≥ armed − _ARM_GRACE` (grace = 2). This stops a
mid-match arm from replaying old events — and the grace is essential because BSD
reports the opening minute as "1", so without it **kickoff was suppressed on every
match** (fixed this iteration). **Dedup:** in-memory set + `fired_alerts` DB table,
per-signal, daily-scoped. **Drama suppression:** `_drama_suppressed` fires a
threshold crossing once, re-arming only after the score cools back under.

### Alarm types
| Type | Fires on |
|---|---|
| `match` | Kickoff (one-shot) |
| `match_moments` | Modular — see below |
| `card_penalty` | Red card and/or penalty awarded |
| `late_drama` | ≥ (90 − last_minutes)′ and \|diff\| ≤ max_diff |
| `drama_radar` | Drama total ≥ threshold (default 7.0) |
| `sensation` | Resolved underdog leading |
| `comeback` | A side trailed ≥2 and is now within ≤1 |
| `big_chance` | xG burst over last ~15′ crosses sensitivity (low 1.3 / med 1.0 / high 0.7) |
| `knockout_et` | Enters extra time / shootout |
| `penalty` | Penalty in last 2′ (can repeat in a shootout) |
| `red_card` (legacy) | Red card, close game, last 20′ |
| `player_alert` | Per-player goal / brace / hat-trick / cards |

**`match_moments` sub-options:** kickoff, goals (per-scoreline), halftime,
second_half, red_cards, penalties, match_end, **lineups** (confirmed), and a
pre-match **reminder_minutes**.

### Follow model
Favorite **teams** and **leagues** carry a full `SoccerFollowConfig` that
`_expand_follow_criteria()` synthesizes into per-match alarms (1:1 with a per-match
alarm). A league follow (`team_ids` empty) applies to every match in the comp; a
team follow only to that team's matches. Each follow has a drama threshold,
`alert_type` (**alarm** vs **silent / "Just Follow"**), and `notify_at_kickoff`.

### iOS alarm config (`SoccerCriteria` / `TeamAlarmConfigSheet`)
Cards for: **Drama Radar, Late Drama, Match Moments, Card/Penalty, Sensation,
Comeback, Big-Chance Burst**. "Set Alert" vs a big "Just Follow" (silent). Editing
an existing follow loads its saved config fresh and the CTA reads **"Update Alert"**
/ the page shows **"Edit Current Alerts"** (this iteration). A live match's
set-alert collapses to just the config screen; a future match keeps Lineups +
Standings.

---

## 7. Historic moments (`soccer_historic.py`)

Stateless, idempotent detector of rare feats, opt-in per category
(`POST /settings/soccer-historic`, stored in `soccer_historic_prefs`). Groups:
player feats (hat-trick, poker, fast/penalty hat-trick, 4+ involvements), team
(rout, goal feast, end-to-end, blitz, lightning goal, last-gasp winner, historic
comeback), discipline (early red, mass reds, penalty barrage, stoppage penalty),
rarities (own goal(s), keeper goal, defender brace), shootout (marathon, decided).
Fired by the radar with per-moment dedup; bilingual text via `i18n.t_shist`.

---

## 8. Calendar (ICS) — `calendar_ics.py`, `calendar_scope.py`

A subscribable iCalendar feed (soccer + basketball unified).

| Endpoint | Purpose |
|---|---|
| `POST /calendar/subscribe` | Mint a token for a scope; returns `webcal://` + `https://` URLs |
| `GET /calendar/{token}.ics` | RFC-5545 feed (5-min cache) |
| `POST /calendar/revoke`, `GET /calendar/subscriptions` | Manage |

**Scopes:** `favorites` (followed teams/leagues + players via national-team
resolution, optional `include_players`), `world_cup`, `custom` (chosen
leagues/teams), `all`. **Window:** today … +60 days, upcoming only, all times UTC
(client renders local); World Cup titles embed flag emoji. **Sync:** polling —
calendar apps refresh ~hourly (`X-PUBLISHED-TTL: PT1H`); no push. Parallel fan-out
over ~61 days × leagues, per-render cached. (No `VALARM` reminders yet.)

**iOS:** `CalendarSubscriptionSheet` (mode-first: Favorites / World Cup / Custom /
All) + `CalendarPickerSheet` (team multi-select) + a target chooser
(local/iCloud/Google).

---

## 9. Notifications (`i18n.py`, `monitor.py`)

Data-only FCM so the app controls presentation. Per-type EN/HE builders
(`t_soccer_*`) cover goal (scorer appended best-effort, full-match title), kickoff,
halftime/second-half/match-end (with score), late drama, drama radar (score in
body), sensation, comeback, big chance, red card, penalty, lineups, reminder, plus
player goal/brace/hat-trick/cards and historic moments. **`alert_type`:** `alarm`
→ chosen loud sound channel + time-sensitive interruption (bypasses silent mode);
`silent` → muted. **Language:** `_get_user_language` per push; the iOS app
re-registers the token on language change so text arrives localized. Unregistered
tokens are pruned. iOS `AppDelegate` re-displays incoming pushes as local
notifications with `.timeSensitive` and the selected alarm sound.

---

## 10. Search, players, teams, standings

- **Search** (`GET /search`, `search_index.py`): in-memory fuzzy search over soccer
  teams (accumulated as matches are fetched; resets on restart), the static league
  catalog, and a live BSD/365 player proxy (interleaved so both sports survive the
  limit). Hebrew names stamped. iOS `TopSearchBar` → Team/League/Player pages.
- **Player** (`/soccer/player/{id}[/stats]`): bio + club + national team (source
  disambiguated via `source=bsd|s365`), recent games with ratings. Photos via
  `TeamLogos` (365 athlete CDN). iOS `PlayerPageView`.
- **Team** (`/soccer/team/{id}/matches`, `/squad`): season fixtures across comps;
  World Cup national squad. iOS `TeamPageView` (follow/edit/unfollow).
- **Standings** (`/soccer/league/{slug}/standings`): table or grouped
  (WC/Euros groups), Hebrew names. iOS `LeaguePageView` + the summary Standings tab.
- **National teams / flags / FIFA ranks** (`national_team.py`, `country_flags.py`,
  `fifa_rankings.py`): resolve a player's nationality → national-team id (so
  favoriting Mbappé surfaces France), flags for WC titles, ranks as the
  underdog-detection fallback.

---

## 11. Localization & presentation

- **Languages:** English + Hebrew (system/en/he). Hebrew team names come from the
  server (`name_he` via `team_names_he.py`) and a curated client map; Hebrew prefers
  the full name. RTL supported; team rows forced LTR for score alignment.
- **No-spoiler mode** (`hideScores`): masks live/final scores on cards.
- **Theme:** system/light/dark. Analytics via TelemetryDeck (no PII).

---

## 12. Database (soccer-relevant tables)

`soccer_alarms` (per-match + radar), `soccer_favorites` (players),
`soccer_favorite_teams`, `soccer_favorite_competitions` (each with
`config`/`drama_threshold`/`alert_type`/`notify_at_kickoff`/`paused`),
`soccer_historic_prefs`, `fired_alerts` (dedup, daily), `drama_snapshots`
(per-minute timeline), `notification_history` (Trophy Room). Threads + fired alerts
recover from the DB on restart.

---

## 13. Known gaps / not implemented

- **No soccer broadcast/EPG** — `epg.py` (TV "where to watch") is **basketball-only**
  (Israeli channels).
- **No head-to-head, form/trend, or honors** endpoints.
- **Penalty *award* on past matches:** only the resulting **penalty goal**
  (`goal_type=penalty`) is retained by REST; the distinct "penalty awarded" event
  exists only live (WS) and is injected as an incident for live matches.
- **Local xG estimator** (`xg_fallback.py`) is a stub (lib unavailable on Py 3.14);
  the engine falls back to chances/near-miss heuristics.
- **Calendar:** no `VALARM` reminders; polling sync only.
- **Search/team accumulator** resets on server restart.
