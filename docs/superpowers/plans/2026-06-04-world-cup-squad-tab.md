# World Cup Squad Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Squad tab to World Cup national-team pages showing the official 26-man roster, where each player links to their player page and to the club team they currently play for.

**Architecture:** A thin server proxy (`/soccer/team/{id}/squad`) fetches BSD's `/worldcup/squads/` and enriches each player with their club `current_team_id` via `bz.player_detail()` (the same resolver the favorites flow already uses). iOS adds a `SquadPlayer` model, a `getTeamSquad` service call, a segmented Matches/Squad control on `TeamPageView` (World-Cup teams only), and a grouped roster view that navigates to the existing `PlayerPageView` / `TeamPageView`.

**Tech Stack:** Python 3 / FastAPI / httpx / pytest (server); Swift 5 / SwiftUI / Kingfisher (iOS).

**⚠️ iOS has NO test target** (see `mobile/iOS/CLAUDE.md`). iOS tasks are verified by a successful `xcodebuild` and, where logic is involved, a Python replay of the same transform against live data. Only the **server** tasks use pytest TDD.

**Reference data (verified live 2026-06-04):** BSD team `930` = Czechia → 26 official call-ups. Player `3700` (David Zima) → `current_team_id 120` (SK Slavia Praha). BSD token is the `BZZOIRO_API_TOKEN` env var.

---

## File Structure

**Server (`/Users/itamark/Projects/Clutch Alarm/Server`)**
- Modify `bzzoiro_adapter.py` — add `worldcup_squad()` + its TTL (raw upstream fetch).
- Modify `soccer_source.py` — add `national_team_squad()` (merge/normalize/sort; the testable core).
- Modify `main.py` — add `GET /soccer/team/{team_id}/squad` route (thin pass-through).
- Modify `tests/test_bzzoiro_adapter.py` — live test for `worldcup_squad`.
- Create `tests/test_squad.py` — pure unit tests for `national_team_squad`.

**iOS (`/Users/itamark/Projects/Clutch Alarm/mobile/iOS/NBA Smart Alarm`)**
- Modify `Services/SoccerService.swift` — `SquadPlayer`, `TeamSquad`, `getTeamSquad`, in-memory cache.
- Create `Views/TeamSquadView.swift` — grouped roster view + its navigation state.
- Modify `Views/TeamPageView.swift` — segmented Matches/Squad control (WC teams only).
- Modify `en.lproj/Localizable.strings` + `he.lproj/Localizable.strings` — new UI strings.

**Note on caching (minor, deliberate deviation from spec):** the spec proposed a persistent `TeamSquadCache` mirroring `TeamSeasonCache`. To avoid a new SwiftData `@Model` + schema-migration surface for a niche screen, this plan uses a lightweight in-memory actor cache in `SoccerService`. The server already persists a 6 h cache, so cross-session loads stay fast; only the first in-session load of a nation shows a spinner.

---

## Task 1: BSD adapter — `worldcup_squad()`

**Files:**
- Modify: `Server/bzzoiro_adapter.py` (TTL dict ~line 62-83; new fn after `team()` ~line 293)
- Test: `Server/tests/test_bzzoiro_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `Server/tests/test_bzzoiro_adapter.py`:

```python
REF_WC_TEAM_ID = 930  # Czechia — 26 official call-ups (verified 2026-06-04)


@skip_if_no_token
def test_worldcup_squad_returns_official_callups() -> None:
    rows = bz.worldcup_squad(REF_WC_TEAM_ID)
    assert isinstance(rows, list)
    assert len(rows) == 26
    sample = rows[0]
    assert "player_id" in sample
    assert "position" in sample
    assert sample["status"] == "official"
    assert all(r["team_id"] == REF_WC_TEAM_ID for r in rows)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Server && BZZOIRO_API_TOKEN=$BZZOIRO_API_TOKEN python -m pytest tests/test_bzzoiro_adapter.py::test_worldcup_squad_returns_official_callups -v`
Expected: FAIL — `AttributeError: module 'bzzoiro_adapter' has no attribute 'worldcup_squad'`.

- [ ] **Step 3: Add the TTL entry**

In `Server/bzzoiro_adapter.py`, add to the `_TTL_SECONDS` dict (after the `"team": 86400.0,` line):

```python
    "worldcup_squad":     6 * 3600.0,  # squads change rarely; 6h is plenty
```

- [ ] **Step 4: Implement the adapter function**

In `Server/bzzoiro_adapter.py`, add after the `team()` function (~line 293):

```python
def worldcup_squad(team_id: int | str, *, status: str = "official") -> list[dict]:
    """Current-World-Cup squad call-ups for one national team.

    BSD covers only the current FIFA World Cup edition. `status="official"`
    is the final 26-man squad. Returns [] for an unknown team / unannounced
    squad (never raises for those — callers treat empty as "no squad yet")."""
    params = {"team": team_id, "status": status, "limit": 200}
    data = _cached_get("worldcup_squad", "/worldcup/squads/", params, team_id, status)
    if isinstance(data, dict):
        return data.get("results") or []
    return data or []
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd Server && BZZOIRO_API_TOKEN=$BZZOIRO_API_TOKEN python -m pytest tests/test_bzzoiro_adapter.py::test_worldcup_squad_returns_official_callups -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add Server/bzzoiro_adapter.py Server/tests/test_bzzoiro_adapter.py
git commit -m "feat(server): BSD worldcup_squad adapter for national-team call-ups"
```

---

## Task 2: Squad merge + normalize — `national_team_squad()`

This is the core: fetch the squad, enrich each row with the player's club
`current_team_id` (parallel `player_detail` calls), normalize the wire shape,
and sort by position group then shirt number.

**Files:**
- Modify: `Server/soccer_source.py` (uses existing `bsd` alias line 26, `_DETAIL_POOL` line 49, `_nullcontext` line 47, `_bsd_team_image_url` ~line 281)
- Test: `Server/tests/test_squad.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `Server/tests/test_squad.py`:

```python
"""Unit tests for national_team_squad — merge/normalize/sort. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soccer_source as ss


def _row(pid, name, pos, num, club="FC X"):
    return {"player_id": pid, "name": name, "position": pos,
            "jersey_number": num, "club": club, "club_country": "Spain",
            "caps": 10, "goals": 1, "age": 25, "status": "official"}


def test_squad_enriches_club_id_and_sorts(monkeypatch):
    rows = [
        _row(3, "Striker", "FW", 9),
        _row(1, "Keeper", "GK", 1),
        _row(2, "Back", "DF", 4),
    ]
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: rows)
    monkeypatch.setattr(ss.bsd, "player_detail",
                        lambda pid: {"current_team_id": 100 + int(pid)})

    out = ss.national_team_squad(930)

    assert out["team_id"] == 930
    assert out["count"] == 3
    # Sorted GK, DF, FW
    assert [p["position"] for p in out["players"]] == ["GK", "DF", "FW"]
    # Club id merged from player_detail.current_team_id
    keeper = out["players"][0]
    assert keeper["current_team_id"] == 101
    assert keeper["club_crest"] == "https://sports.bzzoiro.com/img/team/101/"
    assert keeper["name"] == "Keeper"


def test_squad_degrades_when_player_detail_fails(monkeypatch):
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: [_row(1, "A", "MF", 8)])

    def boom(pid):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(ss.bsd, "player_detail", boom)

    out = ss.national_team_squad(930)
    p = out["players"][0]
    assert p["current_team_id"] is None   # graceful: club not tappable
    assert p["club_crest"] is None
    assert p["club"] == "FC X"            # name string still shown


def test_squad_empty_upstream(monkeypatch):
    monkeypatch.setattr(ss.bsd, "worldcup_squad", lambda tid, **k: [])
    out = ss.national_team_squad(999)
    assert out == {"team_id": 999, "count": 0, "players": []}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd Server && python -m pytest tests/test_squad.py -v`
Expected: FAIL — `AttributeError: module 'soccer_source' has no attribute 'national_team_squad'`.

- [ ] **Step 3: Implement `national_team_squad`**

In `Server/soccer_source.py`, add (e.g. after `get_team_season`, near the other public soccer functions):

```python
# Position display order for national-team squads (GK → DF → MF → FW).
_SQUAD_POS_ORDER = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}


def national_team_squad(team_id: int | str) -> dict:
    """Official World Cup squad for one national team, each player merged to
    the club team they currently play for.

    Shape: {team_id, count, players: [{player_id, name, jersey_number,
    position, club, club_country, current_team_id, club_crest, caps, goals,
    age}]}. The club id comes from bsd.player_detail(player_id).current_team_id
    — the same resolver the favorites national-team flow uses. A failed
    player_detail degrades that row to current_team_id=None (club shown as a
    plain name, never an error)."""
    rows = bsd.worldcup_squad(team_id) or []
    if not rows:
        return {"team_id": team_id, "count": 0, "players": []}

    def _enrich(row: dict) -> dict:
        pid = row.get("player_id")
        club_id = None
        club_crest = None
        if pid is not None:
            try:
                det = bsd.player_detail(pid) or {}
                club_id = det.get("current_team_id")
                if club_id is not None:
                    club_crest = _bsd_team_image_url(club_id)
            except Exception as exc:
                logger.warning("squad: player_detail(%s) failed: %s", pid, exc)
        return {
            "player_id":       pid,
            "name":            row.get("name"),
            "jersey_number":   row.get("jersey_number"),
            "position":        row.get("position"),
            "club":            row.get("club"),
            "club_country":    row.get("club_country"),
            "current_team_id": club_id,
            "club_crest":      club_crest,
            "caps":            row.get("caps"),
            "goals":           row.get("goals"),
            "age":             row.get("age"),
        }

    with _nullcontext(_DETAIL_POOL) as pool:
        players = list(pool.map(_enrich, rows))

    players.sort(key=lambda p: (
        _SQUAD_POS_ORDER.get((p.get("position") or "").upper(), 9),
        p.get("jersey_number") if p.get("jersey_number") is not None else 999,
    ))
    return {"team_id": team_id, "count": len(players), "players": players}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Server && python -m pytest tests/test_squad.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add Server/soccer_source.py Server/tests/test_squad.py
git commit -m "feat(server): national_team_squad merges call-ups with club team ids"
```

---

## Task 3: Route — `GET /soccer/team/{team_id}/squad`

**Files:**
- Modify: `Server/main.py` (add route after `soccer_team_matches` ~line 3044)
- Test: `Server/tests/test_squad.py` (append a TestClient route test)

- [ ] **Step 1: Write the failing route test**

Append to `Server/tests/test_squad.py`:

```python
def test_squad_route(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    fake = {"team_id": 930, "count": 1, "players": [
        {"player_id": 3700, "name": "David Zima", "jersey_number": 4,
         "position": "DF", "club": "SK Slavia Praha", "club_country": "Czechia",
         "current_team_id": 120, "club_crest": "https://x/120/",
         "caps": 5, "goals": 0, "age": 25}]}
    monkeypatch.setattr(main.soccer_source, "national_team_squad", lambda tid: fake)

    client = TestClient(main.app)
    r = client.get("/soccer/team/930/squad")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["players"][0]["current_team_id"] == 120
```

(If `main` does not already `import soccer_source` at module top, monkeypatch the
module object instead: `import soccer_source; monkeypatch.setattr(soccer_source, "national_team_squad", lambda tid: fake)` — the route imports it lazily, same object.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd Server && python -m pytest tests/test_squad.py::test_squad_route -v`
Expected: FAIL — 404 Not Found (route not registered yet).

- [ ] **Step 3: Add the route**

In `Server/main.py`, after `soccer_team_matches` (~line 3044):

```python
@app.get("/soccer/team/{team_id}/squad")
def soccer_team_squad(team_id: str):
    """Official World Cup squad for a national team, each player merged to the
    club they currently play for. Empty for non-World-Cup ids / unannounced
    squads (BSD covers the current WC edition only)."""
    import soccer_source as _ss
    try:
        tid = int(team_id)
    except ValueError:
        return {"team_id": team_id, "count": 0, "players": []}
    return _ss.national_team_squad(tid)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd Server && python -m pytest tests/test_squad.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Live smoke check (real upstream)**

Run:
```bash
cd Server && BZZOIRO_API_TOKEN=$BZZOIRO_API_TOKEN python -c "
import soccer_source as ss, json
out = ss.national_team_squad(930)
print('count', out['count'])
p = out['players'][0]
print(p['position'], '#'+str(p['jersey_number']), p['name'], '->', p['current_team_id'])
"
```
Expected: `count 26` and a first row in the GK group with a non-null `current_team_id`.

- [ ] **Step 6: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add Server/main.py Server/tests/test_squad.py
git commit -m "feat(server): GET /soccer/team/{id}/squad route"
```

---

## Task 4: iOS service — `SquadPlayer`, `TeamSquad`, `getTeamSquad`

**Files:**
- Modify: `mobile/iOS/NBA Smart Alarm/Services/SoccerService.swift`

- [ ] **Step 1: Add the models + service call + in-memory cache**

In `SoccerService.swift`, add inside `enum SoccerService` (near the other structs/methods):

```swift
    // MARK: - World Cup squads

    /// One official World Cup call-up, merged to the club the player currently
    /// plays for (`currentTeamId` / `clubCrest`, resolved server-side from the
    /// player's BSD detail). `currentTeamId` is nil when the club couldn't be
    /// resolved — render the club as plain text in that case.
    struct SquadPlayer: Codable, Equatable, Identifiable {
        let playerId: Int
        let name: String
        let jerseyNumber: Int?
        let position: String?       // "GK" | "DF" | "MF" | "FW"
        let club: String?
        let clubCountry: String?
        let currentTeamId: Int?
        let clubCrest: String?
        let caps: Int?
        let goals: Int?
        let age: Int?

        var id: Int { playerId }

        enum CodingKeys: String, CodingKey {
            case playerId = "player_id"
            case name
            case jerseyNumber = "jersey_number"
            case position, club
            case clubCountry = "club_country"
            case currentTeamId = "current_team_id"
            case clubCrest = "club_crest"
            case caps, goals, age
        }
    }

    struct TeamSquad: Codable, Equatable {
        let teamId: Int
        let count: Int
        let players: [SquadPlayer]

        enum CodingKeys: String, CodingKey {
            case teamId = "team_id"
            case count, players
        }
    }

    /// Process-wide in-memory squad cache. Squads change rarely and the server
    /// persists a 6h cache, so an in-memory map is enough to avoid a re-fetch
    /// when the user flips back to the Squad tab within a session.
    private actor SquadMemoryCache {
        static let shared = SquadMemoryCache()
        private var store: [String: TeamSquad] = [:]
        func get(_ key: String) -> TeamSquad? { store[key] }
        func set(_ key: String, _ value: TeamSquad) { store[key] = value }
    }

    /// Fetch a national team's official World Cup squad. Returns a cached copy
    /// instantly if present, otherwise hits the network and caches the result.
    static func getTeamSquad(teamId: String) async -> TeamSquad? {
        if let hit = await SquadMemoryCache.shared.get(teamId) { return hit }
        do {
            let squad: TeamSquad = try await APIClient.shared.get("/soccer/team/\(teamId)/squad")
            await SquadMemoryCache.shared.set(teamId, squad)
            return squad
        } catch {
            return nil
        }
    }
```

- [ ] **Step 2: Build to verify it compiles**

Run:
```bash
cd "/Users/itamark/Projects/Clutch Alarm/mobile/iOS" && xcodebuild -project "Clutch Alarm.xcodeproj" -scheme "NBA Smart Alarm" -destination "platform=iOS Simulator,name=iPhone 17" build 2>&1 | tail -3
```
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add "mobile/iOS/NBA Smart Alarm/Services/SoccerService.swift"
git commit -m "feat(ios): SquadPlayer/TeamSquad models + getTeamSquad service"
```

---

## Task 5: iOS — squad roster view

**Files:**
- Create: `mobile/iOS/NBA Smart Alarm/Views/TeamSquadView.swift`

- [ ] **Step 1: Create the view**

Create `mobile/iOS/NBA Smart Alarm/Views/TeamSquadView.swift`:

```swift
import SwiftUI
import Kingfisher

/// Official World Cup squad for a national team. Players grouped by position;
/// tapping a player opens their page, tapping the club opens the club team page.
struct TeamSquadView: View {
    let team: SearchTeam

    @State private var squad: SoccerService.TeamSquad?
    @State private var isLoading = true
    @State private var navPlayer: SearchPlayer?
    @State private var navClubTeam: SearchTeam?

    private let groups: [(key: String, titleKey: String)] = [
        ("GK", "squad.goalkeepers"),
        ("DF", "squad.defenders"),
        ("MF", "squad.midfielders"),
        ("FW", "squad.forwards"),
    ]

    var body: some View {
        Group {
            if isLoading {
                ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
            } else if let squad, !squad.players.isEmpty {
                VStack(alignment: .leading, spacing: 16) {
                    Text(String(format: String(localized: "squad.calledUp"), squad.count))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(C.textMuted)
                    ForEach(groups, id: \.key) { group in
                        let rows = squad.players.filter {
                            ($0.position ?? "").uppercased() == group.key
                        }
                        if !rows.isEmpty {
                            section(titleKey: group.titleKey, rows: rows)
                        }
                    }
                }
            } else {
                EmptyStateView(
                    icon: "person.3",
                    title: String(localized: "squad.unavailable"),
                    subtitle: String(localized: "squad.unavailableSub")
                )
                .padding(.top, 24)
            }
        }
        .task { await load() }
        .sheet(item: $navPlayer) { PlayerPageView(player: $0) }
        .sheet(item: $navClubTeam) { TeamPageView(team: $0) }
    }

    private func section(titleKey: String, rows: [SoccerService.SquadPlayer]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(String(localized: String.LocalizationValue(titleKey)))
                .font(.system(size: 11, weight: .black))
                .tracking(1.5).textCase(.uppercase)
                .foregroundStyle(C.textMuted)
            ForEach(rows) { p in row(p) }
        }
    }

    private func row(_ p: SoccerService.SquadPlayer) -> some View {
        HStack(spacing: 12) {
            Text(p.jerseyNumber.map(String.init) ?? "–")
                .font(.system(size: 13, weight: .black).monospacedDigit())
                .foregroundStyle(C.textMuted)
                .frame(width: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text(p.name)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(C.textPrimary)
                    .lineLimit(1)
                clubLabel(p)
            }
            Spacer()
            if let caps = p.caps {
                Text(String(format: String(localized: "squad.capsGoals"), caps, p.goals ?? 0))
                    .font(.system(size: 11))
                    .foregroundStyle(C.textMuted)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            navPlayer = SearchPlayer(
                id: String(p.playerId), name: p.name,
                teamId: p.currentTeamId.map(String.init),
                sport: "football")
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func clubLabel(_ p: SoccerService.SquadPlayer) -> some View {
        if let club = p.club, !club.isEmpty {
            if let cid = p.currentTeamId {
                Button {
                    navClubTeam = SearchTeam(
                        id: String(cid), name: club, sport: "football",
                        logo: p.clubCrest)
                } label: {
                    Text(club)
                        .font(.system(size: 12))
                        .foregroundStyle(C.orange)
                        .lineLimit(1)
                }
                .buttonStyle(.plain)
            } else {
                Text(club)
                    .font(.system(size: 12))
                    .foregroundStyle(C.textMuted)
                    .lineLimit(1)
            }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        squad = await SoccerService.getTeamSquad(teamId: team.id)
    }
}
```

- [ ] **Step 2: Build to verify it compiles**

Run:
```bash
cd "/Users/itamark/Projects/Clutch Alarm/mobile/iOS" && xcodebuild -project "Clutch Alarm.xcodeproj" -scheme "NBA Smart Alarm" -destination "platform=iOS Simulator,name=iPhone 17" build 2>&1 | tail -3
```
Expected: `** BUILD SUCCEEDED **`. (The view isn't referenced yet; Task 6 wires it in. It must still compile.)

- [ ] **Step 3: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add "mobile/iOS/NBA Smart Alarm/Views/TeamSquadView.swift"
git commit -m "feat(ios): TeamSquadView grouped roster with player/club navigation"
```

---

## Task 6: iOS — Matches | Squad segmented control on `TeamPageView`

**Files:**
- Modify: `mobile/iOS/NBA Smart Alarm/Views/TeamPageView.swift`

- [ ] **Step 1: Add a tab enum + state**

In `TeamPageView.swift`, add near the other `@State` declarations (after line 17):

```swift
    private enum TeamTab { case matches, squad }
    @State private var tab: TeamTab = .matches

    /// Squad tab is World-Cup-only — BSD serves squad call-ups for the current
    /// World Cup edition. Other team pages keep the matches-only layout.
    private var showsSquadTab: Bool {
        team.sport == "football"
            && (team.league == "world_cup" || team.leagues.contains("world_cup"))
    }
```

- [ ] **Step 2: Render the picker + switch content**

In `TeamPageView.swift`, replace the body content block (lines 28-41, the `VStack` from `header` through the `else { matchList }`) with:

```swift
                VStack(alignment: .leading, spacing: 16) {
                    header
                    if showsSquadTab {
                        Picker("", selection: $tab) {
                            Text(String(localized: "team.matches")).tag(TeamTab.matches)
                            Text(String(localized: "team.squad")).tag(TeamTab.squad)
                        }
                        .pickerStyle(.segmented)
                    }

                    if showsSquadTab && tab == .squad {
                        TeamSquadView(team: team)
                    } else if isLoading {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                    } else if basketballGames.isEmpty && soccerMatches.isEmpty {
                        EmptyStateView(
                            icon: "calendar",
                            title: String(localized: "team.noMatches"),
                            subtitle: String(localized: "team.noMatchesSub")
                        )
                    } else {
                        matchList
                    }
                }
```

- [ ] **Step 3: Build to verify it compiles**

Run:
```bash
cd "/Users/itamark/Projects/Clutch Alarm/mobile/iOS" && xcodebuild -project "Clutch Alarm.xcodeproj" -scheme "NBA Smart Alarm" -destination "platform=iOS Simulator,name=iPhone 17" build 2>&1 | tail -3
```
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add "mobile/iOS/NBA Smart Alarm/Views/TeamPageView.swift"
git commit -m "feat(ios): Matches/Squad segmented control on World Cup team pages"
```

---

## Task 7: Localization strings

**Files:**
- Modify: `mobile/iOS/NBA Smart Alarm/en.lproj/Localizable.strings`
- Modify: `mobile/iOS/NBA Smart Alarm/he.lproj/Localizable.strings`

- [ ] **Step 1: Add English strings**

Append to `en.lproj/Localizable.strings`:

```
"team.squad" = "Squad";
"squad.calledUp" = "%d called up";
"squad.goalkeepers" = "Goalkeepers";
"squad.defenders" = "Defenders";
"squad.midfielders" = "Midfielders";
"squad.forwards" = "Forwards";
"squad.capsGoals" = "%d caps · %d goals";
"squad.unavailable" = "Squad not announced";
"squad.unavailableSub" = "Call-ups appear once the national team names its World Cup squad.";
```

- [ ] **Step 2: Add Hebrew strings**

Append to `he.lproj/Localizable.strings`:

```
"team.squad" = "סגל";
"squad.calledUp" = "%d שוחקנים בסגל";
"squad.goalkeepers" = "שוערים";
"squad.defenders" = "מגנים";
"squad.midfielders" = "קשרים";
"squad.forwards" = "חלוצים";
"squad.capsGoals" = "%d הופעות · %d שערים";
"squad.unavailable" = "הסגל טרם פורסם";
"squad.unavailableSub" = "השחקנים יופיעו לאחר שהנבחרת תכריז על סגל המונדיאל.";
```

- [ ] **Step 3: Build to verify strings parse + are reachable**

Run:
```bash
cd "/Users/itamark/Projects/Clutch Alarm/mobile/iOS" && xcodebuild -project "Clutch Alarm.xcodeproj" -scheme "NBA Smart Alarm" -destination "platform=iOS Simulator,name=iPhone 17" build 2>&1 | tail -3
```
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add "mobile/iOS/NBA Smart Alarm/en.lproj/Localizable.strings" "mobile/iOS/NBA Smart Alarm/he.lproj/Localizable.strings"
git commit -m "feat(ios): localize World Cup squad strings (EN/HE)"
```

---

## Task 8: End-to-end verification + deploy

**Files:** none (verification + deploy).

- [ ] **Step 1: Run the full server test suite**

Run: `cd Server && python -m pytest tests/test_squad.py tests/test_bzzoiro_adapter.py -v`
Expected: all pass (the two token-gated bzzoiro tests skip locally if `BZZOIRO_API_TOKEN` is unset; the squad unit + route tests pass without a token).

- [ ] **Step 2: Deploy the server**

The route is live only after the server deploys. Push the `Server/` changes to the
`nba-alarm-server` repo that Railway dev auto-deploys (the app's `baseURL` is the
dev server). Confirm the deploy, then:

Run: `curl -s "https://nba-alarm-server-dev.up.railway.app/soccer/team/930/squad" | python3 -c "import json,sys;d=json.load(sys.stdin);print('count',d['count']);print(d['players'][0])"`
Expected: `count 26` and a player object with a non-null `current_team_id`.

- [ ] **Step 3: Manual app check**

Run the app on the iPhone 17 simulator. Navigate: Sports/Favorites → World Cup
standings → tap a nation (e.g. Czechia) → **Squad** tab. Verify:
- 26 players grouped Goalkeepers / Defenders / Midfielders / Forwards.
- Tapping a player opens their player page.
- Tapping a club name opens that club's team page.
- A non-World-Cup team page (e.g. a Premier League club) shows **no** segmented control.

- [ ] **Step 4: Final commit (if any tweaks)**

```bash
cd "/Users/itamark/Projects/Clutch Alarm"
git add -A && git commit -m "chore: World Cup squad tab end-to-end verified" || echo "nothing to commit"
```

---

## Self-Review Notes

- **Spec coverage:** server route (Task 3) ✓, adapter (Task 1) ✓, merge via `current_team_id` (Task 2) ✓, iOS models/service (Task 4) ✓, squad view with player+club nav (Tasks 5–6) ✓, WC-only tab gating (Task 6) ✓, official-26-only (Task 1 `status="official"`) ✓, states loading/empty (Task 5) ✓, deploy note (Task 8) ✓, localization (Task 7) ✓.
- **Deviation:** in-memory squad cache instead of a persistent `TeamSquadCache` (documented above; avoids a SwiftData migration for a niche screen).
- **Type consistency:** `SquadPlayer`/`TeamSquad` field + CodingKey names match the server's normalized snake_case keys from Task 2; `getTeamSquad(teamId:)` signature is used identically in Tasks 5–6.
- **Out of scope (per spec):** preliminary/projected statuses, player-page reverse badge, favoriting from squad, Android.
```
