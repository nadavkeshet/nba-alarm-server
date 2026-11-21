"""Tests for calendar_scope match enumeration (both sports, all scopes)."""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import calendar_scope as cs
import soccer_source
import basketball_source
import main


@pytest.fixture(autouse=True)
def _route_feed_to_sources(monkeypatch):
    """calendar_scope fetches through main._feed_soccer/_feed_basketball (the
    /feed cache layer). For tests, route those straight to the source functions
    the individual tests monkeypatch — bypassing the real feed cache and keeping
    every existing `monkeypatch.setattr(soccer_source, "get_soccer_matches", ...)`
    valid. Attribute lookup at call time picks up each test's fake."""
    monkeypatch.setattr(main, "_feed_soccer",
                        lambda lg, d, tz=None: soccer_source.get_soccer_matches(lg, date_iso=d))
    monkeypatch.setattr(main, "_feed_basketball",
                        lambda lg, d, tz=None: basketball_source.get_basketball_games(lg, date_iso=d))


# ── fixtures / helpers ────────────────────────────────────────────────────────

def _soccer_match(mid, slug, date_iso, home=("1", "A"), away=("2", "B")):
    return {
        "id": mid,
        "competition": slug,
        "kickoff": f"{date_iso}T18:00:00Z",
        "home": {"id": home[0], "name": home[1]},
        "away": {"id": away[0], "name": away[1]},
    }


def _bball_game(gid, slug, date_iso, home_id="10", away_id="20"):
    return {
        "id": gid,
        "league": slug,
        "date": f"{date_iso}T23:00:00Z",
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id, "displayName": "Home"}},
                {"homeAway": "away", "team": {"id": away_id, "displayName": "Away"}},
            ]
        }],
    }


_SOCCER_SLUGS = list(json.load(open(
    os.path.join(os.path.dirname(__file__), "..", "config", "competition_map.json"),
    encoding="utf-8",
)).keys())
_BBALL_SLUGS = ["nba", "euroleague", "eurocup", "bcl", "winner_league"]


# ── 1. world_cup scope ────────────────────────────────────────────────────────

def test_world_cup_scope(monkeypatch):
    calls = []

    def fake_soccer(slug, date_iso=None, tz=None):
        calls.append(slug)
        # one match on the 15th of each month so we get a handful in-window
        if date_iso.endswith("-15"):
            return [_soccer_match(f"s-{slug}-{date_iso}", slug, date_iso)]
        return []

    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_soccer)
    monkeypatch.setattr(basketball_source, "get_basketball_games",
                        lambda *a, **k: pytest.fail("bball must not be called"))

    out = cs.collect_matches(None, "world_cup", {}, "tok")

    assert calls and set(calls) == {"world_cup"}
    assert out, "expected some world_cup matches"
    assert all(m["_sport"] == "soccer" for m in out)
    # sorted ascending by kickoff
    kos = [m["kickoff"] for m in out]
    assert kos == sorted(kos)


# ── 2. all scope ──────────────────────────────────────────────────────────────

def test_all_scope_hits_every_catalog_slug(monkeypatch):
    soccer_slugs = set()
    bball_slugs = set()

    def fake_soccer(slug, date_iso=None, tz=None):
        soccer_slugs.add(slug)
        return []

    def fake_bball(slug, date_iso=None, tz=None):
        bball_slugs.add(slug)
        return []

    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_soccer)
    monkeypatch.setattr(basketball_source, "get_basketball_games", fake_bball)

    cs.collect_matches(None, "all", {}, "tok")

    assert soccer_slugs == set(_SOCCER_SLUGS)
    assert bball_slugs == set(_BBALL_SLUGS)


# ── 3. custom scope ───────────────────────────────────────────────────────────

def test_custom_scope_only_requested(monkeypatch):
    soccer_calls, bball_calls, team_calls = set(), set(), set()

    def fake_soccer(slug, date_iso=None, tz=None):
        soccer_calls.add(slug)
        return [_soccer_match(f"s-{slug}-{date_iso}", slug, date_iso)] if date_iso.endswith("-15") else []

    def fake_bball(slug, date_iso=None, tz=None):
        bball_calls.add(slug)
        # NBA window contains a game with team 10 and team 99
        if date_iso.endswith("-15"):
            return [
                _bball_game(f"b-{slug}-{date_iso}-a", slug, date_iso, home_id="10", away_id="20"),
                _bball_game(f"b-{slug}-{date_iso}-b", slug, date_iso, home_id="30", away_id="40"),
            ]
        return []

    def fake_season(team_id, league_slug=None, name=None):
        team_calls.add(team_id)
        # use an in-window date
        d = cs._window_dates()[10]
        return [_soccer_match(f"s-team-{team_id}-{d}", "premier_league", d)]

    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_soccer)
    monkeypatch.setattr(basketball_source, "get_basketball_games", fake_bball)
    monkeypatch.setattr(soccer_source, "get_team_season", fake_season)

    # Note: only a custom bball TEAM is requested (euroleague), no bball league
    # slug, so the competitor filter is observable in isolation.
    params = {
        "custom_soccer_league_slugs": ["la_liga"],
        "custom_soccer_team_ids": ["777"],
        "custom_bball_teams": [{"id": "10", "league": "euroleague"}],
    }
    out = cs.collect_matches(None, "custom", params, "tok")

    assert soccer_calls == {"la_liga"}
    assert bball_calls == {"euroleague"}, "only the team's league window should be fetched"
    assert team_calls == {"777"}

    ids = {m["id"] for m in out}
    # the bball team filter keeps only games containing team 10
    assert any(i.startswith("b-euroleague-") and i.endswith("-a") for i in ids)
    assert not any(i.endswith("-b") for i in ids), "game without team 10 must be filtered out"
    # the soccer team season match is present
    assert any(i.startswith("s-team-777") for i in ids)


# ── 4. favorites scope (in-memory SQLite) ─────────────────────────────────────

def _seed_engine():
    eng = create_engine("sqlite://")
    main._Base.metadata.create_all(eng)
    return eng


def test_favorites_scope(monkeypatch):
    eng = _seed_engine()
    d = cs._window_dates()[10]  # an in-window date

    with Session(eng) as s:
        s.add(main.SoccerFavoriteTeamDB(
            user_token="tok", team_id="club1", alt_team_ids="club2",
            competitions="premier_league",
        ))
        s.add(main.FavoriteLeagueDB(user_token="tok", league="nba"))
        s.add(main.SoccerFavoritePlayerDB(
            user_token="tok", player_id="p1", team_id="club3", alt_team_ids="nat3",
        ))
        s.commit()

    season_calls = set()
    league_calls = set()

    def fake_season(team_id, league_slug=None, name=None):
        # Only the player path (club3/nat3) routes through team-season now.
        season_calls.add(team_id)
        return [_soccer_match(f"s-team-{team_id}", "premier_league", d)]

    def fake_league(slug, date_iso=None, tz=None):
        # fav team (idset club1/club2) is matched via its competitions' window.
        if slug == "premier_league" and date_iso == d:
            return [_soccer_match("s-fav-pl", slug, date_iso, home=("club2", "Club"))]
        return []

    def fake_bball(slug, date_iso=None, tz=None):
        league_calls.add(slug)
        return [_bball_game(f"b-{slug}-{date_iso}", slug, date_iso)] if date_iso == d else []

    monkeypatch.setattr(soccer_source, "get_team_season", fake_season)
    monkeypatch.setattr(basketball_source, "get_basketball_games", fake_bball)
    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_league)

    with Session(eng) as s:
        out = cs.collect_matches(s, "favorites", {"include_players": True}, "tok")

    # Player path still uses team-season (club3 club + nat3 national).
    assert season_calls == {"club3", "nat3"}
    assert "nba" in league_calls

    ids = {m["id"] for m in out}
    assert "s-fav-pl" in ids   # fav team matched by id-set via its competition window

    sports = {m["_sport"] for m in out}
    assert sports == {"soccer", "basketball"}


def test_favorites_excludes_players_when_flag_off(monkeypatch):
    eng = _seed_engine()
    with Session(eng) as s:
        s.add(main.SoccerFavoritePlayerDB(
            user_token="tok", player_id="p1", team_id="club9", alt_team_ids="nat9",
        ))
        s.commit()

    season_calls = set()
    monkeypatch.setattr(soccer_source, "get_team_season",
                        lambda tid, **k: season_calls.add(tid) or [])
    monkeypatch.setattr(basketball_source, "get_basketball_games", lambda *a, **k: [])
    monkeypatch.setattr(soccer_source, "get_soccer_matches", lambda *a, **k: [])

    with Session(eng) as s:
        cs.collect_matches(s, "favorites", {}, "tok")

    assert season_calls == set(), "players must not be enumerated without include_players"


# ── 5. dedup ──────────────────────────────────────────────────────────────────

def test_dedup_same_id_from_two_sources(monkeypatch):
    d = cs._window_dates()[10]

    def fake_soccer(slug, date_iso=None, tz=None):
        # both la_liga and serie_a windows yield the SAME match id on date d
        if date_iso == d:
            return [_soccer_match("dup-1", slug, date_iso)]
        return []

    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_soccer)
    monkeypatch.setattr(basketball_source, "get_basketball_games", lambda *a, **k: [])

    out = cs.collect_matches(None, "custom", {
        "custom_soccer_league_slugs": ["la_liga", "serie_a"],
    }, "tok")

    assert [m["id"] for m in out].count("dup-1") == 1


# ── 6. window filter ──────────────────────────────────────────────────────────

def test_window_filter_drops_far_future(monkeypatch):
    from datetime import timedelta
    far = (cs._today() + timedelta(days=200)).isoformat()
    near = cs._window_dates()[10]

    def fake_soccer(slug, date_iso=None, tz=None):
        # the fetcher itself would only ever be called with window dates, but
        # the team-season path can return out-of-window matches; simulate via a
        # team season that returns one near + one far match.
        return []

    def fake_season(team_id, league_slug=None, name=None):
        return [
            _soccer_match("near-1", "premier_league", near),
            _soccer_match("far-1", "premier_league", far),
        ]

    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_soccer)
    monkeypatch.setattr(soccer_source, "get_team_season", fake_season)
    monkeypatch.setattr(basketball_source, "get_basketball_games", lambda *a, **k: [])

    out = cs.collect_matches(None, "custom", {"custom_soccer_team_ids": ["1"]}, "tok")
    ids = {m["id"] for m in out}
    assert "near-1" in ids
    assert "far-1" not in ids


# ── 7. memoization ────────────────────────────────────────────────────────────

def test_memoization_league_fetched_once(monkeypatch):
    # count distinct fetch invocations per (slug, date)
    counter = {"n": 0}

    def fake_bball(slug, date_iso=None, tz=None):
        counter["n"] += 1
        return []

    monkeypatch.setattr(basketball_source, "get_basketball_games", fake_bball)
    monkeypatch.setattr(soccer_source, "get_soccer_matches", lambda *a, **k: [])
    monkeypatch.setattr(soccer_source, "get_team_season", lambda *a, **k: [])

    # two custom bball teams in the SAME league → window must be fetched once
    params = {"custom_bball_teams": [
        {"id": "10", "league": "nba"},
        {"id": "20", "league": "nba"},
    ]}
    cs.collect_matches(None, "custom", params, "tok")

    # one window = len(_window_dates()) calls, not double
    assert counter["n"] == len(cs._window_dates())


def test_memoization_soccer_team_fetched_once(monkeypatch):
    counter = {"n": 0}

    def fake_season(team_id, league_slug=None, name=None):
        counter["n"] += 1
        return []

    monkeypatch.setattr(soccer_source, "get_team_season", fake_season)
    monkeypatch.setattr(soccer_source, "get_soccer_matches", lambda *a, **k: [])
    monkeypatch.setattr(basketball_source, "get_basketball_games", lambda *a, **k: [])

    # same team id referenced twice
    params = {"custom_soccer_team_ids": ["555", "555"]}
    cs.collect_matches(None, "custom", params, "tok")

    assert counter["n"] == 1


# ── 8. non-BSD team → 365 league-window fallback ──────────────────────────────

def test_team_matches_falls_back_to_365_league_window(monkeypatch):
    import calendar_scope as cs, soccer_source
    # BSD team-season empty (the Israeli-team bug)
    monkeypatch.setattr(soccer_source, "get_team_season", lambda tid, league_slug=None, name=None: [])
    # ligat_haal league window (365-covered) contains a Maccabi (id 130) match
    def fake_league(slug, date_iso=None, tz=None):
        if slug == "ligat_haal" and date_iso == cs._window_dates()[5]:
            return [{"id": f"s365-{date_iso}", "competition": "ligat_haal",
                     "kickoff": f"{date_iso}T18:00:00Z",
                     "home": {"id": "130", "name": "Maccabi Tel Aviv"},
                     "away": {"id": "999", "name": "X"}}]
        return []
    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_league)
    out = cs._soccer_team_matches("130", {}, league="ligat_haal")
    assert len(out) == 1 and out[0]["_sport"] == "soccer"
    assert out[0]["home"]["id"] == "130"


def test_custom_soccer_teams_with_league(monkeypatch):
    import calendar_scope as cs, soccer_source
    monkeypatch.setattr(soccer_source, "get_team_season", lambda tid, league_slug=None, name=None: [])
    def fake_league(slug, date_iso=None, tz=None):
        if slug == "ligat_haal" and date_iso == cs._window_dates()[3]:
            return [{"id": f"s365-{date_iso}", "competition": "ligat_haal",
                     "kickoff": f"{date_iso}T18:00:00Z",
                     "home": {"id": "567", "name": "Hapoel TA"}, "away": {"id": "1", "name": "Y"}}]
        return []
    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake_league)
    out = cs.collect_matches(None, "custom",
        {"custom_soccer_teams": [{"id": "567", "league": "ligat_haal"}]}, "tok")
    assert any(m["home"]["id"] == "567" for m in out)


# ── 9. id-set team matching across leagues ────────────────────────────────────

def test_team_matches_soccer_by_idset_across_leagues(monkeypatch):
    import calendar_scope as cs, soccer_source
    def fake(slug, date_iso=None, tz=None):
        d5 = cs._window_dates()[5]
        if slug == "europa_league" and date_iso == d5:
            return [{"id": "e1", "competition": "europa_league", "kickoff": f"{date_iso}T18:00:00Z",
                     "home": {"id": "130", "name": "Maccabi"}, "away": {"id": "9", "name": "X"}}]
        if slug == "ligat_haal" and date_iso == d5:
            return [{"id": "l1", "competition": "ligat_haal", "kickoff": f"{date_iso}T18:00:00Z",
                     "home": {"id": "566", "name": "Maccabi"}, "away": {"id": "8", "name": "Y"}}]
        return []
    monkeypatch.setattr(soccer_source, "get_soccer_matches", fake)
    out = cs._team_matches(["130", "566"], ["europa_league", "ligat_haal"], "soccer", {})
    ids = {m["id"] for m in out}
    assert ids == {"e1", "l1"}   # matched via 130 (BSD) and 566 (365)


def test_team_matches_basketball_scans_all_leagues_when_no_league(monkeypatch):
    import calendar_scope as cs, basketball_source
    d5 = cs._window_dates()[5]
    def fake(slug, date_iso=None, tz=None):
        if slug == "winner_league" and date_iso == d5:
            return [{"id": "g1", "league": "winner_league", "date": f"{date_iso}T18:00:00Z",
                     "competitions": [{"competitors": [
                         {"homeAway": "home", "team": {"id": "631", "displayName": "Maccabi"}},
                         {"homeAway": "away", "team": {"id": "633", "displayName": "Holon"}}]}]}]
        return []
    monkeypatch.setattr(basketball_source, "get_basketball_games", fake)
    out = cs._team_matches(["33", "631"], [], "basketball", {})   # no league → scan all bball
    assert [m["id"] for m in out] == ["g1"]   # matched via 631


def test_custom_idset_teams_collect(monkeypatch):
    import calendar_scope as cs, basketball_source
    d3 = cs._window_dates()[3]
    def fake(slug, date_iso=None, tz=None):
        if slug == "winner_league" and date_iso == d3:
            return [{"id": "g9", "league": "winner_league", "date": f"{date_iso}T20:00:00Z",
                     "competitions": [{"competitors": [
                         {"homeAway": "home", "team": {"id": "631", "displayName": "Maccabi"}},
                         {"homeAway": "away", "team": {"id": "1", "displayName": "Z"}}]}]}]
        return []
    monkeypatch.setattr(basketball_source, "get_basketball_games", fake)
    out = cs.collect_matches(None, "custom",
        {"custom_bball_teams": [{"ids": ["33", "631"], "leagues": []}]}, "tok")
    assert any(m["id"] == "g9" for m in out)
