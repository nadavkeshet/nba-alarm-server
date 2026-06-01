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
    # FT comeback in a final is a must-watch; 0-0 and blowouts are not.
    assert ws_soccer("ft", 90, 3, 2, slug="ucl", round_name="Final",
                     home_score_ht=1, away_score_ht=2) >= 8.0
    assert ws_soccer("ft", 90, 0, 0, slug="premier_league") is None
    assert ws_soccer("ft", 90, 4, 0, slug="ligue_1") is None
    # A tight finish stays sane (lightweight path is intentionally conservative;
    # actually-watched matches get the richer peak-drama value).
    v = ws_soccer("ft", 90, 2, 1, slug="premier_league")
    assert v is None or 4.0 <= v <= 10.0, v


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


def main():
    t_win_probability()
    t_drama_odds()
    t_soccer_lightweight()
    t_basketball_units()
    print("OK")


if __name__ == "__main__":
    main()
