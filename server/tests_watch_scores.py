# server/tests_watch_scores.py — pure-logic tests for the smarter watch scores.
import win_probability as wp
import drama_engine as de
import watch_score as ws


def t_win_probability():
    # Normalizes to 1 with overround removed.
    p = wp.implied_probs_1x2(2.0, 3.0, 4.0)
    assert p is not None and abs(sum(p) - 1.0) < 1e-9, p
    assert p[0] > p[1] > p[2], p                       # 2.0 is the shortest price
    # Invalid legs → None.
    assert wp.implied_probs_1x2(1.0, 3.0, 4.0) is None
    assert wp.implied_probs_1x2(None, 3.0, 4.0) is None
    # Real BSD odds-snapshot shape (home_win/draw/away_win).
    snap = {"event_id": 1, "odds": {"home_win": 2.29, "draw": 2.29, "away_win": 2.5}}
    pp = wp.win_prob_from_snapshot(snap)
    assert pp is not None and abs(sum(pp) - 1.0) < 1e-9, pp
    assert wp.win_prob_from_snapshot({}) is None
    assert wp.win_prob_from_snapshot(None) is None
    assert round(wp.lead_prob(pp), 3) == round(max(pp[0], pp[2]), 3)


def t_drama_odds():
    base = {"minute": 75, "status": "live", "competition": "ucl",
            "home": {"score": 1}, "away": {"score": 1}}
    coin = dict(base, win_prob=(0.34, 0.32, 0.34), win_prob_swing=0.5)
    decided = dict(base, win_prob=(0.9, 0.07, 0.03), win_prob_swing=None)
    none = dict(base)
    s_coin = de.compute_drama(coin)
    s_dec = de.compute_drama(decided)
    s_none = de.compute_drama(none)
    assert s_coin.odds > s_dec.odds > 0.0, (s_coin.odds, s_dec.odds)   # coin-flip + swing wins
    assert s_none.odds == 0.0                                          # no odds → no contribution
    assert s_coin.total > s_none.total                                # odds lifts the total
    for s in (s_coin, s_dec, s_none):
        assert 0.0 <= s.total <= 10.0


def t_soccer_lightweight():
    # Every started match now gets a score (badge shows on ALL matches), but the
    # number still ranks drama: a FT comeback in a final is a must-watch, while a
    # 0-0 or a blowout lands low.
    comeback = ws_soccer("ft", 90, 3, 2, slug="ucl", round_name="Final",
                         home_score_ht=1, away_score_ht=2)
    goalless = ws_soccer("ft", 90, 0, 0, slug="premier_league")
    blowout  = ws_soccer("ft", 90, 4, 0, slug="ligue_1")
    tight    = ws_soccer("ft", 90, 2, 1, slug="premier_league")
    # No match returns None anymore.
    for v in (comeback, goalless, blowout, tight):
        assert v is not None and 0.0 <= v <= 10.0, v
    assert comeback >= 8.0, comeback
    # Drama ordering holds: comeback > tight > blowout > goalless.
    assert comeback > tight > blowout > goalless, (comeback, tight, blowout, goalless)


def t_soccer_finished_wc_spread():
    # Regression: a FINISHED match's badge rates how good it was to WATCH (the
    # FT-quality formula: goals + closeness + comeback + xG, × stakes), NOT a
    # summary of the live drama timeline. A competitive game's drama curve rises
    # into a late tied/one-goal tension peak and stays high through the closing
    # minutes, so both the old peak AND the top-15% average — × the 1.55 World
    # Cup stakes + the group qualification spike — rated even a 0-0 bore draw
    # ~8.8-9.3. A finished WC slate must show a real spread instead.
    goalless = ws_soccer("ft", 90, 0, 0, slug="world_cup", round_name="Group A")
    onenil   = ws_soccer("ft", 90, 1, 0, slug="world_cup", round_name="Group A")
    thriller = ws_soccer("ft", 90, 3, 2, slug="world_cup", round_name="Group A",
                         home_score_ht=0, away_score_ht=2)
    assert goalless < 3.0, goalless
    assert thriller > onenil > goalless, (thriller, onenil, goalless)


def ws_soccer(status, minute, hs, aw, **kw):
    import soccer_source as ss
    return ss._soccer_watch_score("bsd", 999, status, minute, hs, aw, **kw)


def t_basketball_units():
    # periods_score deltas → running totals; live partial summed in.
    detail = {"status": "inprogress",
              "home_team": {"id": 1, "short_name": "AAA"},
              "away_team": {"id": 2, "short_name": "BBB"},
              "home_score": 38, "away_score": 25,
              "periods_score": {"period1": [27, 19], "period2": [11, 6],
                                "period3": [-1, -1], "period4": [-1, -1]}}
    plays, info = ws._bb_synthesize_plays_from_periods(detail, 600)
    assert len(plays) == 2, plays                       # the two played quarters
    assert (plays[-1].home_score, plays[-1].away_score) == (38, 25)
    assert info["home_abbr"] == "AAA"

    # Standout: a triple-double maxes the boost; nothing → 0.
    box = {"home_box": [{"points": 12, "rebounds": 11, "assists": 10,
                         "steals": 1, "blocks": 0}], "away_box": []}
    assert ws._bb_standout_boost(box) == 0.6
    assert ws._bb_standout_boost({"home_box": [{"points": 41}], "away_box": []}) == 0.5
    assert ws._bb_standout_boost({}) == 0.0

    # Efficiency neutral on missing stats.
    assert ws._bb_efficiency_factor({}) == 1.0

    # Avdija boost: only fires for Portland + Deni; caps at 2.5.
    pdx = {"home_team": {"name": "Portland Trail Blazers"},
           "away_team": {"name": "Phoenix Suns"},
           "home_score": 110, "away_score": 100}
    bx = {"home_box": [{"name": "Deni Avdija", "minutes": 33, "points": 30,
                        "rebounds": 11, "assists": 10, "steals": 2, "blocks": 1}],
          "away_box": []}
    boost, dbg = ws._compute_avdija_boost_bsd(pdx, bx)
    assert 0 < boost <= 2.5 and dbg["team_won"] is True, (boost, dbg)
    # Non-Portland → no boost.
    nope = {"home_team": {"name": "Phoenix Suns"}, "away_team": {"name": "LA Lakers"}}
    assert ws._compute_avdija_boost_bsd(nope, bx) == (0.0, {})


def t_wc_drama():
    import drama_engine as de
    K = de._is_knockout
    assert K("Final") and K("Quarter-final") and K("Round of 16") and K("Semi-final")
    assert not K("Group A") and not K("Group Stage") and not K(None)

    def ctx(**kw):
        base = {"minute": 80, "status": "live", "competition": "world_cup",
                "home": {"score": 0}, "away": {"score": 0}}
        base.update(kw); return base

    # UPSET — a pre-match underdog leading late in a knockout screams; the
    # favorite leading does not.
    up = de.compute_drama(ctx(round_name="Round of 16", home={"score": 1}, away={"score": 0},
                              pre_match_win_prob=(0.12, 0.20, 0.68)))   # home was a big underdog
    fav = de.compute_drama(ctx(round_name="Round of 16", home={"score": 1}, away={"score": 0},
                               pre_match_win_prob=(0.68, 0.20, 0.12)))  # home was the favorite
    assert up.upset >= 1.5 and fav.upset == 0.0, (up.upset, fav.upset)
    assert up.total > fav.total
    # Live market dissonance also triggers an upset (market still favors the trailer).
    dis = de.compute_drama(ctx(home={"score": 1}, away={"score": 0}, win_prob=(0.30, 0.20, 0.50)))
    assert dis.upset > 0.0

    # KNOCKOUT peaks — a shootout is ~10; a late 1-goal knockout beats a late
    # 1-goal group game.
    shootout = de.compute_drama(ctx(status="pens", round_name="Final",
                                    home={"score": 1}, away={"score": 1}))
    assert shootout.total >= 9.5, shootout.total
    ko = de.compute_drama(ctx(minute=88, round_name="Quarter-final",
                              home={"score": 1}, away={"score": 0})).state
    grp = de.compute_drama(ctx(minute=88, round_name="Group A",
                               home={"score": 1}, away={"score": 0})).state
    assert ko > grp, (ko, grp)

    # COMEBACK — a late equalizer adds event drama.
    eq = de.compute_drama(ctx(minute=85, home={"score": 1}, away={"score": 1},
        recent_events=[{"type": "goal", "minute": 84, "team": "away",
                        "home_score": 1, "away_score": 1}])).events
    assert eq > 1.5, eq

    # CHANCES — end-to-end (both ends) beats one-sided.
    end2end = de._score_chances({"home": {"big_chances": 4}, "away": {"big_chances": 4}})
    oneside = de._score_chances({"home": {"big_chances": 6}, "away": {"big_chances": 0}})
    assert end2end > oneside and de._score_chances({}) == 0.0

    # QUALIFICATION — late close group game boosts; knockout / early / non-group = 0.
    qg = de.compute_drama(ctx(minute=88, round_name="Group C",
                              home={"score": 1}, away={"score": 1})).qualification
    assert qg > 0.0
    assert de.compute_drama(ctx(minute=88, round_name="Final",
                                home={"score": 1}, away={"score": 1})).qualification == 0.0
    assert de.compute_drama(ctx(minute=20, round_name="Group C")).qualification == 0.0

    # Robustness: empty ctx and full ctx both clamp 0–10, never crash.
    for c in ({}, ctx(status="pens", round_name="Final",
                      pre_match_win_prob=(0.05, 0.15, 0.80), win_prob=(0.1, 0.2, 0.7),
                      home={"score": 2, "big_chances": 5}, away={"score": 1, "big_chances": 4})):
        assert 0.0 <= de.compute_drama(c).total <= 10.0


def main():
    t_win_probability()
    t_drama_odds()
    t_soccer_lightweight()
    t_soccer_finished_wc_spread()
    t_basketball_units()
    t_wc_drama()
    print("OK")


if __name__ == "__main__":
    main()
