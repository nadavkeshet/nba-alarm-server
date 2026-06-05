"""Tests for the Football Historic Moments detector + i18n accessor."""
import soccer_historic as sh
from i18n import t_shist


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _match(*, status="live", incidents=None, home_score=0, away_score=0,
           home="Home FC", away="Away FC", competition="premier_league",
           stats=None):
    return {
        "status": status,
        "competition": competition,
        "home": {"name": home, "score": home_score},
        "away": {"name": away, "score": away_score},
        "incidents": incidents or [],
        "stats": stats or {},
    }


def _goal(minute, pid, *, is_home=True, player="P", assist=None,
          goal_type=None, type="goal"):
    inc = {"type": type, "minute": minute, "is_home": is_home,
           "player": player, "player_id": pid}
    if assist is not None:
        inc["assist"] = assist
    if goal_type is not None:
        inc["goal_type"] = goal_type
    return inc


def _card(minute, pid, *, is_home=True, card_type="red", player="P"):
    return {"type": "card", "minute": minute, "is_home": is_home,
            "player": player, "player_id": pid, "card_type": card_type}


def _cats(moments):
    return {m["category"] for m in moments}


def _by_cat(moments, cat):
    return [m for m in moments if m["category"] == cat]


# ---------------------------------------------------------------------------
# GROUP A — player goal feats
# ---------------------------------------------------------------------------

def test_hat_trick_fires_and_is_distinct_from_poker():
    m = _match(home_score=3, incidents=[
        _goal(10, 7, player="Salah"),
        _goal(30, 7, player="Salah"),
        _goal(60, 7, player="Salah"),
    ])
    out = sh.detect_historic(m)
    assert "hat_trick" in _cats(out)
    assert "poker" not in _cats(out)
    ht = _by_cat(out, "hat_trick")[0]
    assert ht["dedup"] == "hat_trick_7"
    assert ht["params"]["player"] == "Salah"


def test_poker_distinct_from_hat_trick():
    m = _match(home_score=4, incidents=[_goal(i, 7) for i in (5, 20, 40, 70)])
    out = sh.detect_historic(m)
    assert "poker" in _cats(out)
    assert "hat_trick" not in _cats(out)
    assert _by_cat(out, "poker")[0]["dedup"] == "poker_7"


def test_five_plus():
    m = _match(home_score=5, incidents=[_goal(i, 9) for i in (5, 10, 20, 30, 40)])
    out = sh.detect_historic(m)
    fp = _by_cat(out, "five_plus")
    assert fp and fp[0]["dedup"] == "five_9_5"
    assert "hat_trick" not in _cats(out) and "poker" not in _cats(out)


def test_fast_hat_trick():
    fast = _match(home_score=3, incidents=[_goal(20, 7), _goal(25, 7), _goal(29, 7)])
    assert "fast_hat_trick" in _cats(sh.detect_historic(fast))
    slow = _match(home_score=3, incidents=[_goal(5, 7), _goal(40, 7), _goal(80, 7)])
    assert "fast_hat_trick" not in _cats(sh.detect_historic(slow))


def test_penalty_hat_trick():
    m = _match(home_score=3, incidents=[
        _goal(10, 7, goal_type="penalty"),
        _goal(40, 7, type="penalty"),
        _goal(70, 7, goal_type="penalty"),
    ])
    out = sh.detect_historic(m)
    assert "penalty_hat_trick" in _cats(out)
    assert _by_cat(out, "penalty_hat_trick")[0]["dedup"] == "pen_ht_7"


def test_goal_involvements():
    m = _match(home_score=4, incidents=[
        _goal(10, 7, player="Kane"),
        _goal(30, 7, player="Kane"),
        _goal(50, 8, player="Son", assist="Kane"),
        _goal(70, 9, player="Maddison", assist="Kane"),
    ])
    out = sh.detect_historic(m)
    inv = _by_cat(out, "goal_involvements")
    assert inv, "expected goal_involvements"
    kane = [m for m in inv if m["params"]["player"] == "Kane"]
    assert kane and kane[0]["params"]["total"] == 4
    assert kane[0]["params"]["assists"] == 2


def test_no_player_feats_when_absent():
    m = _match(home_score=1, incidents=[_goal(10, 7)])
    assert not (_cats(sh.detect_historic(m)) & {
        "hat_trick", "poker", "five_plus", "fast_hat_trick",
        "penalty_hat_trick", "goal_involvements"})


# ---------------------------------------------------------------------------
# GROUP B — scoreline / team
# ---------------------------------------------------------------------------

def test_rout():
    assert "rout" in _cats(sh.detect_historic(_match(home_score=5, away_score=0)))
    out = sh.detect_historic(_match(home_score=6, away_score=1))
    assert _by_cat(out, "rout")[0]["dedup"] == "rout_5"
    assert "rout" not in _cats(sh.detect_historic(_match(home_score=4, away_score=0)))


def test_goal_feast():
    out = sh.detect_historic(_match(home_score=4, away_score=3))
    assert "goal_feast" in _cats(out)
    assert _by_cat(out, "goal_feast")[0]["dedup"] == "feast_7"
    assert "goal_feast" not in _cats(sh.detect_historic(_match(home_score=3, away_score=3)))


def test_end_to_end():
    assert "end_to_end" in _cats(sh.detect_historic(_match(home_score=3, away_score=3)))
    assert "end_to_end" not in _cats(sh.detect_historic(_match(home_score=3, away_score=2)))


def test_goal_blitz():
    m = _match(home_score=3, incidents=[
        _goal(40, 1, is_home=True),
        _goal(42, 2, is_home=False),
        _goal(44, 3, is_home=True),
    ])
    out = sh.detect_historic(m)
    blitz = _by_cat(out, "goal_blitz")
    assert blitz and blitz[0]["dedup"] == "blitz_44"
    spread = _match(home_score=3, incidents=[_goal(10, 1), _goal(40, 2), _goal(80, 3)])
    assert "goal_blitz" not in _cats(sh.detect_historic(spread))


def test_lightning_goal():
    out = sh.detect_historic(_match(home_score=1, incidents=[_goal(1, 7, player="Bobby")]))
    lg = _by_cat(out, "lightning_goal")
    assert lg and lg[0]["dedup"] == "lightning_7_1"
    assert "lightning_goal" not in _cats(sh.detect_historic(
        _match(home_score=1, incidents=[_goal(3, 7)])))


def test_last_gasp_winner():
    # 1-1 then a 93rd-minute home goal -> home now lead.
    m = _match(home_score=2, away_score=1, incidents=[
        _goal(20, 1, is_home=True),
        _goal(50, 2, is_home=False),
        _goal(93, 3, is_home=True, player="Hero"),
    ])
    out = sh.detect_historic(m)
    lg = _by_cat(out, "last_gasp_winner")
    assert lg and lg[0]["dedup"] == "lastgasp_93"
    # An equaliser late on (not a lead-changer) does not fire.
    m2 = _match(home_score=1, away_score=1, incidents=[
        _goal(50, 2, is_home=False),
        _goal(92, 3, is_home=True),
    ])
    assert "last_gasp_winner" not in _cats(sh.detect_historic(m2))


def test_historic_comeback():
    # Away go 0-3 up, home claw back to 3-3.
    m = _match(home_score=3, away_score=3, incidents=[
        _goal(5, 1, is_home=False),
        _goal(15, 2, is_home=False),
        _goal(25, 3, is_home=False),
        _goal(55, 4, is_home=True),
        _goal(65, 5, is_home=True),
        _goal(75, 6, is_home=True),
    ])
    out = sh.detect_historic(m)
    cb = _by_cat(out, "historic_comeback")
    assert cb and cb[0]["dedup"] == "comeback_home"
    # Never down by 3 -> no comeback.
    m2 = _match(home_score=2, away_score=2, incidents=[
        _goal(5, 1, is_home=False), _goal(15, 2, is_home=False),
        _goal(55, 3, is_home=True), _goal(65, 4, is_home=True)])
    assert "historic_comeback" not in _cats(sh.detect_historic(m2))


# ---------------------------------------------------------------------------
# GROUP C — discipline
# ---------------------------------------------------------------------------

def test_early_red():
    out = sh.detect_historic(_match(incidents=[_card(8, 4, player="Defender")]))
    er = _by_cat(out, "early_red")
    assert er and er[0]["dedup"] == "early_red_4"
    assert "early_red" not in _cats(sh.detect_historic(_match(incidents=[_card(40, 4)])))


def test_mass_red_cards():
    m = _match(incidents=[_card(20, 1), _card(30, 2, is_home=False), _card(80, 3)])
    out = sh.detect_historic(m)
    mr = _by_cat(out, "mass_red_cards")
    assert mr and mr[0]["dedup"] == "mass_red_3"
    assert "mass_red_cards" not in _cats(sh.detect_historic(
        _match(incidents=[_card(20, 1), _card(30, 2)])))


def test_penalty_barrage():
    m = _match(home_score=2, away_score=1, incidents=[
        _goal(10, 1, goal_type="penalty"),
        _goal(40, 2, is_home=False, goal_type="penalty"),
        _goal(70, 3, goal_type="penalty"),
    ])
    out = sh.detect_historic(m)
    pb = _by_cat(out, "penalty_barrage")
    assert pb and pb[0]["dedup"] == "pen_barrage_3"


def test_stoppage_penalty():
    out = sh.detect_historic(_match(home_score=1, incidents=[
        _goal(92, 7, goal_type="penalty", player="Cool")]))
    sp = _by_cat(out, "stoppage_penalty")
    assert sp and sp[0]["dedup"] == "stoppage_pen_92"
    assert "stoppage_penalty" not in _cats(sh.detect_historic(
        _match(home_score=1, incidents=[_goal(50, 7, goal_type="penalty")])))


# ---------------------------------------------------------------------------
# GROUP D — rarities
# ---------------------------------------------------------------------------

def test_own_goal():
    og = {"type": "owngoal", "minute": 33, "is_home": True,
          "player": "Smith", "player_id": 11}
    out = sh.detect_historic(_match(home_score=1, incidents=[og]))
    o = _by_cat(out, "own_goal")
    assert o and o[0]["dedup"] == "og_33_11"
    assert "multiple_own_goals" not in _cats(out)


def test_multiple_own_goals():
    incs = [
        {"type": "owngoal", "minute": 33, "is_home": True, "player": "A", "player_id": 11},
        {"type": "owngoal", "minute": 55, "is_home": False, "player": "B", "player_id": 12},
    ]
    out = sh.detect_historic(_match(home_score=1, away_score=1, incidents=incs))
    mo = _by_cat(out, "multiple_own_goals")
    assert mo and mo[0]["dedup"] == "multi_og_2"


def test_gk_and_defender_skipped_without_positions():
    m = _match(home_score=2, incidents=[_goal(10, 50), _goal(60, 50)])
    out = sh.detect_historic(m, positions=None)
    assert "goalkeeper_goal" not in _cats(out)
    assert "defender_brace" not in _cats(out)


def test_goalkeeper_goal_with_positions():
    m = _match(home_score=1, incidents=[_goal(95, 50, player="Keeper")])
    out = sh.detect_historic(m, positions={"50": "G"})
    gk = _by_cat(out, "goalkeeper_goal")
    assert gk and gk[0]["dedup"] == "gk_goal_50"


def test_defender_brace_with_positions():
    m = _match(home_score=2, incidents=[_goal(10, 4, player="Van Dijk"),
                                         _goal(70, 4, player="Van Dijk")])
    out = sh.detect_historic(m, positions={"4": "DC"})
    db = _by_cat(out, "defender_brace")
    assert db and db[0]["dedup"] == "def_brace_4"


# ---------------------------------------------------------------------------
# GROUP E — shootout
# ---------------------------------------------------------------------------

def test_marathon_shootout():
    # 10 shootout kicks at minute >= 121.
    kicks = [_goal(121, 100 + i, is_home=(i % 2 == 0)) for i in range(10)]
    m = _match(status="pens", home_score=1, away_score=1, incidents=kicks)
    out = sh.detect_historic(m)
    ms = _by_cat(out, "marathon_shootout")
    assert ms and ms[0]["dedup"] == "marathon_shootout"
    # Fewer than 10 kicks -> no marathon.
    few = _match(status="pens", incidents=[_goal(121, 1), _goal(121, 2)])
    assert "marathon_shootout" not in _cats(sh.detect_historic(few))


def test_shootout_decided():
    m = _match(status="ap", incidents=[_goal(121, 1), _goal(121, 2)])
    out = sh.detect_historic(m)
    sd = _by_cat(out, "shootout_decided")
    assert sd and sd[0]["dedup"] == "shootout_decided"
    # ft with shootout kicks also counts.
    m2 = _match(status="ft", incidents=[_goal(121, 1)])
    assert "shootout_decided" in _cats(sh.detect_historic(m2))
    # plain ft, no shootout -> not decided on pens.
    assert "shootout_decided" not in _cats(sh.detect_historic(_match(status="ft")))


# ---------------------------------------------------------------------------
# Stability / robustness
# ---------------------------------------------------------------------------

def test_dedup_is_stable_across_identical_snapshots():
    m = _match(home_score=3, incidents=[_goal(10, 7), _goal(30, 7), _goal(60, 7)])
    a = {x["dedup"] for x in sh.detect_historic(m)}
    b = {x["dedup"] for x in sh.detect_historic(m)}
    assert a == b


def test_malformed_match_never_raises():
    assert sh.detect_historic({}) == []
    assert sh.detect_historic({"incidents": ["junk", 5, None, {}]}) == []
    assert sh.detect_historic({"home": None, "away": None, "incidents": [
        {"type": "goal"}, {"type": "card", "minute": None}]}) is not None
    assert sh.detect_historic(None) == []


# ---------------------------------------------------------------------------
# i18n coverage
# ---------------------------------------------------------------------------

def test_t_shist_covers_every_category_in_both_languages():
    params = {
        "player": "Player", "team": "Team A", "opp": "Team B",
        "home_name": "Team A", "away_name": "Team B",
        "home_score": 3, "away_score": 1, "minute": 90,
        "count": 3, "total": 4, "margin": 5, "goals": 2, "assists": 2,
        "competition": "premier_league",
    }
    for cat in sh.CATEGORY_KEYS:
        for lang in ("en", "he"):
            title, body = t_shist(cat, lang, params)
            assert isinstance(title, str) and title.strip(), (cat, lang, "title")
            assert isinstance(body, str) and body.strip(), (cat, lang, "body")


def test_t_shist_degrades_on_missing_params():
    for cat in sh.CATEGORY_KEYS:
        title, body = t_shist(cat, "en", {})
        assert title.strip() and body.strip()
        title_he, body_he = t_shist(cat, "he", {})
        assert title_he.strip() and body_he.strip()


def test_category_keys_unique_and_complete():
    assert len(sh.CATEGORY_KEYS) == len(set(sh.CATEGORY_KEYS))
    assert len(sh.CATEGORY_KEYS) == 23
