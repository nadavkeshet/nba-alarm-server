"""365scores team-stats normalization: the /game/stats/ flat list (keyed by
competitorId) → canonical {home, away} TeamStats shape the iOS reads, so 365
matches show as complete a Statistics tab as BSD ones."""
import soccer_source as ss


def _stat(id_, name, cid, value):
    return {"id": id_, "name": name, "competitorId": cid, "value": value}


def test_maps_names_to_canonical_fields_per_side():
    H, A = 100, 200
    raw = [
        _stat(10, "Possession", H, "66%"), _stat(10, "Possession", A, "34%"),
        _stat(76, "Expected Goals", H, "2.17"), _stat(76, "Expected Goals", A, "0.55"),
        _stat(21, "Total Shots", H, "17"), _stat(22, "Shots On Target", H, "7"),
        _stat(24, "Big Chances Created", H, "5"), _stat(36, "Big Chances Missed", H, "4"),
        _stat(8, "Corners", H, "6"), _stat(12, "Fouls", H, "12"), _stat(9, "Offsides", H, "1"),
        _stat(19, "Accurate Passes", H, "524"), _stat(21, "Total Passes", H, "567"),
        _stat(23, "Goalkeeper Saves", H, "3"),
        _stat(1, "Yellow Cards", H, "2"), _stat(2, "Red Cards", H, "0"),
    ]
    out = ss._normalize_365_statistics(raw, H, A)
    home = out["home"]
    assert home["ball_possession"] == 66.0 and out["away"]["ball_possession"] == 34.0
    assert home["expected_goals"] == 2.17
    assert home["total_shots"] == 17 and home["shots_on_target"] == 7
    assert home["big_chances"] == 5 and home["big_chances_missed"] == 4
    assert home["corner_kicks"] == 6 and home["fouls"] == 12 and home["offsides"] == 1
    assert home["accurate_passes"] == 524 and home["total_passes"] == 567
    assert home["goalkeeper_saves"] == 3
    assert home["yellow_cards"] == 2 and home["red_cards"] == 0
    # derived pass accuracy
    assert home["pass_accuracy_pct"] == round(524 / 567 * 100, 1)


def test_total_shots_fallback_from_inside_plus_outside():
    H, A = 1, 2
    raw = [
        _stat(146, "Shots Inside The Box", H, "9"),
        _stat(147, "Shots Outside The Box", H, "4"),
    ]
    out = ss._normalize_365_statistics(raw, H, A)
    assert out["home"]["total_shots"] == 13


def test_empty_and_unknown_return_none():
    assert ss._normalize_365_statistics([], 1, 2) is None
    # only unmappable stats → nothing usable
    assert ss._normalize_365_statistics([_stat(13, "Free Kicks", 1, "16")], 1, 2) is None


def test_ignores_other_competitors():
    H, A = 10, 20
    raw = [_stat(8, "Corners", 999, "5"), _stat(8, "Corners", H, "3")]
    out = ss._normalize_365_statistics(raw, H, A)
    assert out["home"]["corner_kicks"] == 3
    assert "corner_kicks" not in out["away"]
