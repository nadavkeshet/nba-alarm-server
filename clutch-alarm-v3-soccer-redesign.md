# Clutch Alarm v3 — Multi-Sport Redesign (Soccer)

> Companion to `clutch-alarm-brand-brief.md` and `clutch-alarm-features.md`.
> This document specifies v3: the rebrand from "NBA Smart Alarm" to "Clutch Alarm" as a multi-sport app, with soccer as the second pillar alongside basketball.
> Target: feature-complete and stable for **2026 FIFA World Cup (kickoff June 11, 2026)**.
> Status: design approved, awaiting spec review.

---

## 0. Thesis

> A scoreline is a lagging indicator. Pressure is a leading indicator.

In basketball, "close score late" ≈ "worth watching." That simple rule built the v2 product.

In soccer, it isn't true. A 0-0 can be enthralling or unwatchable. A 1-0 with the **leading** team in control is dead. A 1-0 with the **trailing** team in control is the most exciting state in football — a goal is owed, and the next 10 minutes will deliver it.

The entire v3 soccer brain is built around detecting this gap between *what the score says* and *what the match is actually doing*. Every signal, every alarm, every UI element serves this insight.

The basketball product is **unchanged** in v3: same engine, same alarms, same features, same monitoring threads.

---

## 1. Goals and non-goals

### Goals
1. Add soccer (top European leagues + national-team competitions) as a first-class second sport
2. Ship a soccer-native "Drama Score" that detects worth-watching moments using live xG and momentum, not just score
3. Rebrand from "NBA Smart Alarm" to "Clutch Alarm" — multi-sport identity
4. Keep all v2 NBA features working identically
5. World Cup-ready by 2026-06-11
6. Zero ongoing data-API cost at launch

### Non-goals
1. No new sports beyond basketball + soccer in v3
2. No social features, no betting, no streaming, no chat
3. No paid tier in v3 (monetization is a separate v4 decision)
4. No coverage of lower-tier leagues. Scope is explicitly top 5 + UEFA + national teams

### Scope of soccer coverage (launch)

| Tier | Competitions |
|---|---|
| Top 5 club leagues | Premier League, La Liga, Serie A, Bundesliga, Ligue 1 |
| UEFA club | Champions League, Europa League |
| National team | World Cup, Euros, World Cup Qualifiers, Nations League |
| Israeli | Ligat Ha'Al, Israel State Cup, Israel national team |

About 1,500 matches/year total. Local cup competitions (FA Cup, Copa del Rey, etc.) are stretch — add if data source supports cheaply.

---

## 2. Brand and app structure

### 2a. Rebrand

- **Name:** `NBA Smart Alarm` → `Clutch Alarm`
- **Tagline (HE/EN):** "מעיר אותך רק כשבאמת שווה" / "Only wakes you when it actually matters"
- **App icon:** Replace basketball motif with a sport-agnostic siren/alarm mark. (User supplying new logo.)
- **Brand colors:** Unchanged (navy/orange — sport-agnostic).
- **App Store category:** Sports / Reference.
- **App Store positioning:** "The only app that wakes you when a game is worth watching — basketball and football."

### 2b. Top-level UX

Keep the current 3-tab layout (Today / Mission / Settings). **No sport tabs.**

A **sport pill** sits at the top of Today and Mission: `All · 🏀 · ⚽`. Defaults to `All`. Tapping filters the feed by sport. The radar and alarms are inherently sport-aware in the data layer.

Settings adds:
- Sport preferences (enable/disable each sport)
- Competition picker (per sport)
- Default Drama threshold (per sport)

### 2c. Migration for existing users

One-time "What's New v3" sheet on first launch:
- "We added football. Same alarms, smarter brain."
- "Your NBA setup is untouched."
- "Pick competitions you care about — or just hit the global radar and we'll wake you for anything worth watching."

Existing favorites, alarms, preferences are preserved bit-for-bit.

---

## 3. The Drama Engine (core IP)

A single sport-agnostic service on the server: `DramaService`. Outputs a **Drama Score 0–10** per live match every 30s. The radar fires whenever any tracked match crosses the user's threshold (default 7).

### 3a. The formula

```
inner       = max(0, state + momentum + events)
DramaScore  = min(10, inner × stakes)
```

The inner sum is floored at 0 (a dead game with negative momentum doesn't become "negative drama"). The product is capped at 10 so Drama Score always lives in `[0, 10]`. All three terms are sport-specific implementations of the same shape. Soccer specifics below.

#### State (0–4) — where the match is

| Condition | Score |
|---|---|
| 1-goal diff in last 15 min | +4 |
| 1-goal diff, 60–75' | +3 |
| Drawn after 75' | +3 |
| 2+ goal diff with goal in last 10 min (comeback live) | +2 |
| Knockout extra time, any state | +5 (auto-wake) |
| Knockout penalty shootout | +5 (auto-wake) |
| 2+ goal diff, no recent goal, past 75' | 0 (dead game) |

#### Momentum (−2 to +3) — *the killer soccer feature*

This is what makes "1-0 trailing team dominating" fire and "1-0 leading team dominating" stay silent.

Inputs from Sofascore's `event/{id}/graph` (`graphPoints` array, one minute-value pair per minute, signed −100..+100 toward home/away) plus `event/{id}/statistics` (cumulative xG per team) plus `event/{id}/shotmap` (per-shot xG + coordinates).

Rolling 15-minute window math:
```
pressure_gap = mean(graphPoints[last_15min] toward trailing_team)
              + 50 × (xG_last_15min[trailing] − xG_last_15min[leading])
```

| `pressure_gap` | Momentum score | Meaning |
|---|---|---|
| > +60 | **+3** | Trailing team strongly dominating — goal incoming |
| +20 to +60 | +1 | Lean toward trailing team |
| −20 to +20 | 0 | Balanced |
| < −20 | **−2** | Leader also dominating — game is over |

If the score is drawn, `trailing_team` is interpreted as "the team currently more under pressure" (negative pressure_gap), and the same math applies.

#### Events (0–3) — just-in-time spikes

| Event | Score |
|---|---|
| Penalty awarded (just-in-time) | +3 (fire **immediately** when ref points to spot) |
| Red card in close match in last 20 min | +2 |
| Goal in last 5 min | +1 |
| Free-kick / corner in box, score within 1, last 10' | +1 |
| Star player sub-on for trailing team | +1 (uses a hard-coded "marquee players" list — Ballon d'Or top-30, top scorer of each top-5 league, plus user's favorite players. List maintained in `Server/config/marquee_players.json`, reviewed quarterly.) |

#### Stakes multiplier (1.0–1.5)

| Tier | Multiplier |
|---|---|
| Group stage / regular round | 1.0 |
| Knockout | 1.2 |
| Final / derby / Israel national team match | 1.3 |
| User's favorite team or player on the pitch | 1.5 |

### 3b. Worked examples

| Match state | State | Momentum | Events | Stakes | **Drama** | Alarm? |
|---|---|---|---|---|---|---|
| 1-0, 80', trailing team dominating xG 1.4 vs 0.6, UCL R16 | +4 | +3 | +1 | ×1.2 | **9.6** | ✅ |
| 1-0, 80', leading team also dominating | +4 | −2 | 0 | ×1.0 | **2.0** | ❌ |
| 0-0, 70', both teams creating | +3 | 0 | 0 | ×1.0 | **3.0** | ❌ |
| 0-0, 88', 4 big chances last 10', xG 1.8 vs 1.6 | +3 | +3 | +1 | ×1.2 | **8.4** | ✅ |
| 3-3, ET, knockout | +5 | +1 | +1 | ×1.3 | **9.1** | ✅ |
| 3-0, 70', no recent goal | 0 | −2 | 0 | ×1.0 | **−2 → clamped 0** | ❌ |
| Penalty awarded, 65', 1-1, group stage | +3 | (computed) | +3 | ×1.0 | **≥6 instantly** | ✅ |

Tune weights from logged `drama_snapshots` against archived matches before launch.

### 3c. Spoiler-safe notifications

Wake-up notification body never reveals the score. Examples:
- `⚡ Drama brewing — Real Madrid vs Liverpool`
- `🔥 Late tension — Premier League`
- `⏰ Extra time — UCL R16`

When the user opens the app, the Drama Meter is visible. Score is hidden behind a tap labeled "Show score" — same pattern as the existing Watch Score on basketball.

### 3d. NBA retrofit (Phase 4)

The same DramaService gets a basketball implementation. Inputs change but the formula shape doesn't:
- `state` term: existing score-gap × quarter logic
- `momentum` term: run differential over last 4 min, FG% swing, turnover delta
- `events` term: late-and-1, technical foul on close game, star injury
- `stakes` term: playoffs, finals, rivalry

This is additive — NBA alarms continue firing on their existing rules; the Drama Score becomes a new opt-in radar mode for NBA in v3.1.

---

## 4. Soccer alarm types

Alongside the smart Drama Radar, mirror the existing NBA alarm suite:

| Alarm | What it watches |
|---|---|
| **Drama Radar** *(default)* | Drama Score ≥ user threshold on any tracked competition |
| **Match Alarm** | One specific match — wake when Drama threshold met, or at kickoff |
| **Late Drama** | ≥75', score within 1, on any tracked match |
| **Knockout / ET / Pens** | Auto-wake whenever a knockout match enters ET or penalties |
| **Comeback Watch** | Favorite team trailing scores to bring it within 1 |
| **Hat-Trick Watch** | Favorite player has 2 goals — fire on every shot attempt after |
| **Red Card Drama** | Red card in close match in last 20 min |
| **Big Chance Burst** | 3+ big chances in last 10 min, score within 1 |
| **Penalty** | Penalty awarded in any tracked match — instant |

Each alarm honors:
- The existing two delivery modes (loud / silent)
- Pause-All-Favorites toggle
- Per-competition enable/disable
- Per-alarm threshold (where applicable)

---

## 5. Data architecture

### 5a. Sources (final, all free)

After P1 audit (2026-05-18), **BSD (bzzoiro) is the primary source for football**.
It's an official, documented, free, indie-friendly API that delivers everything
the Drama Engine needs in one JSON shape per match. Sofascore stays as the
secondary — it covers the two competitions BSD doesn't (Euros, Ligat Ha'al) and
acts as failover if BSD has an outage.

| Layer | Source | Access | Purpose |
|---|---|---|---|
| Live xG (cumulative + per shot), per-minute Attack Momentum, per-minute cumulative xG timeline, shotmap with coordinates+body part+situation, 40+ per-team stats, confirmed/predicted lineups, incidents, CatBoost ML predictions | **BSD (bzzoiro)** v2 API — primary for 8 of 10 competitions (top 5 + UCL + EL + World Cup) | Official REST, `Authorization: Token <BZZOIRO_API_TOKEN>` env var. Free tier with Redis-side caching (30s live window, 15min social, etc.) | Drama Engine's pressure + xG input. Verified 9/9 tests passing live |
| Same data for Euros + Ligat Ha'al | **Sofascore via `curl_cffi`** | Chrome120 TLS impersonation, no headless browser | Covers the two competitions BSD doesn't. Verified 8/8 tests passing live |
| Schedule, fixtures, events timeline (basketball-style consistency) | **365scores** (existing `Client365` — no auth) | Authoritative match listing layer. Already integrated for basketball — sport_id=1 parameterizes for football | Authoritative match state listing + fallback metadata |
| xG fallback when no provider has xG for a particular shot | **`soccer_xg`** (ML-KULeuven, Apache 2.0) — pretrained `openplay_xgboost_advanced` | Local Python | Decouples Drama Engine from any single provider — stub in P1, full impl in P3 |

**Total ongoing data cost: $0/mo.** Optional $3/mo BSD WebSocket add-on
(`wss://sports.bzzoiro.com/ws/live/`) for true real-time streaming, deferrable
until polling at 25s proves insufficient.

### 5a.i. BSD competition coverage

| Competition | BSD ID | Notes |
|---|---|---|
| Premier League | 1 | |
| La Liga | 3 | |
| Serie A | 4 | |
| Bundesliga | 5 | |
| Ligue 1 | 6 | |
| Champions League | 7 | |
| Europa League | 8 | |
| World Cup 2026 | 27 | |
| Euros | — | Use Sofascore id=1 |
| Ligat Ha'al | — | Use Sofascore id=266 |

### 5a.ii. BSD data confirmed live on Newcastle 3-1 West Ham (PL, 2026-05-17)

- `/events/{id}/stats/` returns 30 shots with per-shot xG + position, 92 minute-points of momentum, 22 entries of cumulative xG-per-minute, average positions per side, 40+ per-team aggregated counters (`expected_goals`, `big_chances`, `dangerous_attack`, `touches_in_penalty_area`, `ball_possession`, `pass_accuracy_pct`, …)
- `/events/{id}/lineups/` returns confirmed formation (4-2-3-1) + 11 starters + subs
- `/events/{id}/incidents/` returns chronological play-by-play
- `/events/{id}/prediction/` returns pre-match CatBoost prediction (match result probs, xG forecast, over/under, BTTS)

### 5b. Server modules (new files in `Server/`)

- `bzzoiro_adapter.py` — **primary** football source. httpx client with Token auth from `BZZOIRO_API_TOKEN` env var. Methods: `live_events()`, `league_events()`, `event_detail()`, `event_stats()`, `event_incidents()`, `event_lineups()`, `event_prediction()`. Helper functions: `get_team_xg()`, `momentum_pressure_window()`, `cum_xg_at_minute()`, `xg_in_window()`. Per-method TTL cache (live 20s, stats 25s, lineups 5min, prediction 10min).
- `sofascore_adapter.py` — secondary football source via `curl_cffi` with `chrome120` impersonation. Covers Euros + Ligat Ha'al (the two competitions BSD doesn't), and serves as failover for the 8 BSD-primary competitions if BSD has an outage. Same method shape as `bzzoiro_adapter` so the Drama Engine can swap sources transparently.
- `score365_soccer_adapter.py` — extends existing `score365/scraper.py` for `sport_id=1`. Methods: `soccer_games_by_date()`, `soccer_game_detail()`, `soccer_competitions()`. Reuses `Client365`. Also exports `BSD_COMPETITION_IDS`, `SOFASCORE_COMPETITION_IDS`, and `PRIMARY_SOURCE` lookup dicts.
- `drama_engine.py` — sport-agnostic scoring service (P3). Composition: `DramaState`, `DramaMomentum`, `DramaEvents`, `DramaStakes`. Each is overridden per sport in `drama_basketball.py` (existing logic adapted) and `drama_soccer.py`.
- `soccer_monitor.py` — P2 module. Mirrors `monitor.py`. One thread per soccer alarm + one global radar thread polling all live matches every 25s. Routes each match through `PRIMARY_SOURCE` to the right adapter.
- `xg_fallback.py` — thin wrapper over `soccer_xg` to compute xG locally from (x, y, body_part, situation). P1 stub; full impl in P3 once Python 3.14 compatibility is sorted.

### 5c. Database additions

```sql
CREATE TABLE soccer_alarms (
  id              SERIAL PRIMARY KEY,
  token           TEXT NOT NULL REFERENCES tokens(value),
  alarm_type      TEXT NOT NULL,  -- 'drama_radar' | 'match' | 'late_drama' | ...
  match_id        TEXT,            -- nullable for radar-style alarms
  competition_id  TEXT,
  player_id       TEXT,
  threshold       REAL,
  config          JSONB NOT NULL,  -- alarm-type-specific config
  created_at      TIMESTAMPTZ DEFAULT now(),
  fired_at        TIMESTAMPTZ
);

CREATE TABLE soccer_favorites (
  token       TEXT NOT NULL,
  player_id   TEXT NOT NULL,
  config      JSONB NOT NULL,
  PRIMARY KEY (token, player_id)
);

CREATE TABLE soccer_favorite_teams (
  token       TEXT NOT NULL,
  team_id     TEXT NOT NULL,
  threshold   REAL,
  PRIMARY KEY (token, team_id)
);

CREATE TABLE soccer_favorite_competitions (
  token           TEXT NOT NULL,
  competition_id  TEXT NOT NULL,
  PRIMARY KEY (token, competition_id)
);

CREATE TABLE soccer_match_cache (
  match_id     TEXT PRIMARY KEY,
  competition_id TEXT,
  status       TEXT,
  minute       INT,
  home_score   INT,
  away_score   INT,
  fetched_at   TIMESTAMPTZ DEFAULT now(),
  raw          JSONB NOT NULL  -- merged Sofascore + 365scores payload
);

CREATE TABLE drama_snapshots (
  id            SERIAL PRIMARY KEY,
  match_id      TEXT NOT NULL,
  sport         TEXT NOT NULL,
  minute        INT,
  state_score   REAL,
  momentum_score REAL,
  events_score  REAL,
  stakes_mult   REAL,
  drama_total   REAL NOT NULL,
  computed_at   TIMESTAMPTZ DEFAULT now()
);
```

Extend `fired_alerts` with a `signal_type` column so the same match can fire on Drama once and on Red Card once but not twice for the same reason.

### 5d. Polling strategy

- **Schedule poller** (every 5 min): pulls today + tomorrow + day-after from 365scores `/games/allscores/` for `sport_id=1`. Filters to in-scope competitions. Caches in `soccer_match_cache`.
- **Live poller** (every 25s): for each currently-live match in tracked competitions, fetches `score365.get_game_detail` + `sofascore.event_statistics` + `sofascore.event_graph`. Merges into `soccer_match_cache.raw`.
- **Drama evaluator** (after each live poll): recomputes DramaScore for each live match. Writes `drama_snapshots`. Compares against each user's threshold. Fires + dedups via `fired_alerts`.
- **Event evaluator** (after each live poll): scans new events vs previously-seen events from `sofascore.event_incidents`. Fires event-specific alarms (penalty, red card, hat-trick watch).

Expected load (worst case, UCL matchday):
- ~8 simultaneous matches × 2 endpoints (statistics + graph) × every 25s = ~38 requests/minute to Sofascore
- Well within polite scraping limits; cache buffers against bursts

---

## 6. Mobile (iOS) changes

### 6a. New files
- `Models/SoccerMatch.swift`, `SoccerPlayer.swift`, `SoccerAlarm.swift`, `Competition.swift`
- `Services/SoccerService.swift`, `DramaScoreFormatter.swift`
- `Views/SoccerMatchCardView.swift`, `SoccerAlarmSheet.swift`, `CompetitionPickerSheet.swift`, `DramaMeterView.swift` (reusable)

### 6b. Modified files
- `HomeView.swift` — add sport pill, mixed-sport feed
- `ActiveView.swift` — add sport pill
- `FavoritesView.swift` — add soccer favorite teams/players/competitions sections
- `SettingsView.swift` — add sport preferences, competition picker, drama threshold slider
- `NBA_Smart_AlarmApp.swift` — register soccer notification categories
- `NBAService.swift` → renamed/refactored to `SportsAPIClient.swift`; `NBAService` retained as facade
- `Theme/` — no changes (brand colors unchanged)
- `en.lproj` / `he.lproj` — add `soccer.*` keys

### 6c. The soccer match card

```
┌────────────────────────────────────────────────┐
│  [Crest]  Real Madrid    1   ⏱  78'           │
│  [Crest]  Liverpool      0   UCL · R16        │
│                                                │
│  Drama  ████████░░  8.4                       │
│  Pressure  ⚪ RMA ────● LIV                    │
│                                                │
│           [ ⏰ Set alarm ]                     │
└────────────────────────────────────────────────┘
```

The Drama Meter pulses orange at ≥7. The pressure dots size by xG-last-15min — instantly shows who's on top.

### 6d. Notifications

- Reuse existing alarm sound (sport-agnostic; brand-consistent)
- iOS critical alert entitlement preferred; falls back to `interruptionLevel: .timeSensitive` on builds without critical alert approval
- Body copy localized via existing string tables — add `soccer.notification.drama`, `soccer.notification.penalty`, `soccer.notification.knockout_et`, etc.

---

## 7. Watch Score for finished soccer matches

Same 1–10 brand as basketball. Computed post-match from cumulative xG, lead changes, goals, red cards, ET/pens, comeback magnitude, and stakes. Spoiler-safe by default (number visible, score hidden behind tap).

Tagline appears alongside the score: "3-3 thriller, 11 big chances, late winner."

---

## 8. Localization

- Full HE / EN coverage extends to all soccer features
- Hebrew is default for Israeli users; competition names use accepted HE spellings (ליגת האלופות, ליגת ווינר, etc.)
- Player names: FIFA-canonical Latin spellings in EN; Hebrew transliteration in HE
- RTL preserved end-to-end (same as v2)

---

## 9. Failure-mode runbook

| Failure | Detection | Mitigation |
|---|---|---|
| Sofascore returns 403 | curl_cffi response status | (1) Rotate `impersonate` value (chrome120 → safari17_0 → chrome). (2) If still 403, fall back to: 365scores events + `xg_fallback` computed xG + heuristic momentum from event density. Drama Engine continues with `momentum = 0` for affected matches and logs the degradation. |
| Sofascore returns 429 | rate-limit | Exponential backoff with jitter; reduce poll frequency for non-live matches first; only live in-scope matches stay at 25s |
| Sofascore data shape changes | Schema validation on every response | Log + alert; per-field nullable parsing; fall back to xg_fallback as in 403 case |
| 365scores `/game/?gameId=X` returns 500 | response status | Cache last-good detail for up to 60s; if missing, skip that match for one cycle |
| Railway egress IP gets banned | sustained 403 across multiple matches | (1) Add residential proxy (~$5/mo) for Sofascore-only traffic. (2) Reduce poll frequency to 60s while investigating. |
| `soccer_xg` import fails / model corrupt | startup test | Drama Engine runs without local xG fallback; alarms still fire on events + state + 365scores momentum proxies |
| FCM token migration during a live match | existing flow | Inherited from v2; no new work |

---

## 10. Phases and timing

Target: feature-complete and stable by **2026-06-11** (World Cup kickoff).
Today: **2026-05-18**. Available: ~3.5 weeks.

| Phase | Scope | Estimate | Target |
|---|---|---|---|
| **P1 — Foundations** | Rebrand (icon, name, copy), sport pill in Home/Active, data schemas (DB migrations), `sofascore_adapter.py`, `score365_soccer_adapter.py`, register BSD, verify all 7 competitions on Sofascore | 5–7 days | by 2026-05-25 |
| **P2 — Soccer MVP** | Match cards, Match/Late-Drama/Knockout alarms, soccer favorites, basic radar (events-driven, no Drama Engine yet) | 5–7 days | by 2026-06-01 |
| **P3 — Drama Engine v1** | `drama_engine.py` + `drama_soccer.py`, momentum module reading Sofascore graph + xG, Drama Score on match cards, smart radar | 7–10 days | by 2026-06-10 |
| **P4 — Polish & retrofit** | Tune weights from logged matches, Drama Meter on NBA cards (optional), Watch Score for soccer, full alarm-type matrix | After WC kickoff | post-launch |
| **P5 — Tune** | Replay logged `drama_snapshots` against archived matches, calibrate weights, false-positive audit | Ongoing | v3.1 |

P1 starts immediately. The implementation plan is the next document.

---

## 11. Open questions

1. **App rename in App Store** — does Apple require a separate listing or do we keep the current bundle ID and rename in-place? Need to confirm. Default: rename in-place, retain bundle ID.
2. **iOS critical alert entitlement** — apply now or stay on `timeSensitive`? Critical alert requires Apple approval and a justifiable use case ("life safety adjacent"); a sports alarm may not qualify. Default: keep `timeSensitive`, document the choice.
3. **Hebrew Israeli league competition ID on Sofascore** — confirm coverage in P1 (Sofascore typically covers Ligat Ha'Al but verify via a live probe during the season).
4. **Notification cap during World Cup** — 64 matches in 4 weeks; a Drama Radar threshold of 7 could fire many times per day. Add a "max alerts per night" user setting? Default: ship with no cap, observe in P3, add if users complain.
5. **bzzoiro reliability** — actually register and test in P1. If verified, demote Sofascore to "secondary" and use bzzoiro as primary (lower legal/anti-bot risk).

---

## 12. Decision log

- **2026-05-18** — User confirms full multi-sport rebrand (Approach C), with soccer as pillar #2.
- **2026-05-18** — Sportmonks rejected: €281/mo without live xG (Pressure Index & xG bundle is post-match in the affordable tier).
- **2026-05-18** — Sofascore via `curl_cffi` verified working on Manchester United 3-2 Nottingham Forest (PL, 2026-05-17). xG, momentum graph, shotmap, incidents all returned. Confirms free-stack path.
- **2026-05-18** — Initial data architecture: 365scores (existing) + Sofascore (curl_cffi) + `soccer_xg` fallback + bzzoiro (backup).
- **2026-05-18 (later)** — **BSD (bzzoiro) audited and promoted to primary** for the 8 of 10 target competitions it covers (top 5 + UCL + EL + World Cup). Verified live: per-shot xG, 92-point momentum, cumulative xG-per-minute timeline, 40+ team stats, ML predictions, confirmed lineups, full incident timeline. Official API with token auth, free tier, indie-friendly. Sofascore demoted to secondary — handles Euros + Ligat Ha'al + failover. The data quality is at parity with paid Sportmonks tiers (and ahead in some areas — BSD provides ML predictions out of the box).
