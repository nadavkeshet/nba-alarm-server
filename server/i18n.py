"""
Notification text translations for NBA Smart Alarm.
"""

# Hebrew city-only names (no mascot names for copyright)
_TEAM_HE = {
    "Atlanta Hawks": "אטלנטה", "Boston Celtics": "בוסטון",
    "Brooklyn Nets": "ברוקלין", "Charlotte Hornets": "שרלוט",
    "Chicago Bulls": "שיקגו", "Cleveland Cavaliers": "קליבלנד",
    "Dallas Mavericks": "דאלאס", "Denver Nuggets": "דנבר",
    "Detroit Pistons": "דטרויט", "Golden State Warriors": "גולדן סטייט",
    "Houston Rockets": "יוסטון", "Indiana Pacers": "אינדיאנה",
    "LA Clippers": "ל.א.", "Los Angeles Lakers": "ל.א.",
    "Memphis Grizzlies": "ממפיס", "Miami Heat": "מיאמי",
    "Milwaukee Bucks": "מילווקי", "Minnesota Timberwolves": "מינסוטה",
    "New Orleans Pelicans": "ניו אורלינס", "New York Knicks": "ניו יורק",
    "Oklahoma City Thunder": "אוקלהומה סיטי", "Orlando Magic": "אורלנדו",
    "Philadelphia 76ers": "פילדלפיה", "Phoenix Suns": "פיניקס",
    "Portland Trail Blazers": "פורטלנד", "Sacramento Kings": "סקרמנטו",
    "San Antonio Spurs": "סן אנטוניו", "Toronto Raptors": "טורנטו",
    "Utah Jazz": "יוטה", "Washington Wizards": "וושינגטון",
}

# Hebrew abbreviation map (city only)
_ABBR_HE = {
    "ATL": "אטלנטה", "BOS": "בוסטון", "BKN": "ברוקלין", "CHA": "שרלוט",
    "CHI": "שיקגו", "CLE": "קליבלנד", "DAL": "דאלאס", "DEN": "דנבר",
    "DET": "דטרויט", "GS": "גולדן סטייט", "HOU": "יוסטון", "IND": "אינדיאנה",
    "LAC": "ל.א.", "LAL": "ל.א.", "MEM": "ממפיס", "MIA": "מיאמי",
    "MIL": "מילווקי", "MIN": "מינסוטה", "NO": "ניו אורלינס", "NY": "ניו יורק",
    "OKC": "אוקלהומה סיטי", "ORL": "אורלנדו", "PHI": "פילדלפיה", "PHX": "פיניקס",
    "POR": "פורטלנד", "SAC": "סקרמנטו", "SA": "סן אנטוניו", "TOR": "טורנטו",
    "UTAH": "יוטה", "WSH": "וושינגטון",
}


def translate_game_name(name: str, lang: str) -> str:
    """Translate game name like 'ATL @ BOS' or 'Atlanta Hawks at Boston Celtics'."""
    if lang != "he":
        return name
    result = name
    # Replace "at" / "@" with "נגד"
    result = result.replace(" at ", " נגד ")
    result = result.replace(" @ ", " נגד ")
    # Translate full names
    for en, he in _TEAM_HE.items():
        result = result.replace(en, he)
    # Translate abbreviations
    for en, he in _ABBR_HE.items():
        result = result.replace(f" {en} ", f" {he} ")
        if result.startswith(en + " "):
            result = he + result[len(en):]
        if result.endswith(" " + en):
            result = result[:-len(en)] + he
    return result


def period_label(quarter: int, lang: str) -> str:
    if quarter <= 4:
        return f"רבע {quarter}" if lang == "he" else f"Q{quarter}"
    return f"הארכה {quarter - 4}" if lang == "he" else f"OT{quarter - 4}"


# Notification text templates
_TITLES = {
    "en": {
        "smart_alarm":      "Smart Alarm! It's Getting Dramatic!",
        "period_starting":  "Period Starting!",
        "scoring_alert":    "Scoring Alert!",
        "early_points":     "Early Points Alert!",
        "triple_double":    "Triple-Double Watch!",
        "fav_team":         "Favorite Team Alert!",
        "radar":            "NBA Radar – Close Game!",
        "historic_scoring": "Historic Scoring Pace!",
        "historic_threes":  "Historic 3-Point Game!",
        "quad_double":      "Quad-Double Threat!",
        "test":             "Test Notification",
        "player_alert":     "Player Alert!",
    },
    "he": {
        "smart_alarm":      "!Smart Alarm! הולך להיות דרמטי",
        "period_starting":  "!הרבע מתחיל",
        "scoring_alert":    "!התראת ניקוד",
        "early_points":     "!התראת נקודות מוקדמות",
        "triple_double":    "!מעקב טריפל דאבל",
        "fav_team":         "!התראת קבוצה מועדפת",
        "radar":            "!NBA רדאר – משחק צמוד",
        "historic_scoring":  "!קצב ניקוד היסטורי",
        "historic_threes":  "!משחק שלשות היסטורי",
        "quad_double":      "!איום קוואדרופל דאבל",
        "test":             "התראת בדיקה",
        "player_alert":     "!התראת שחקן",
    },
}


def t_title(key: str, lang: str) -> str:
    return _TITLES.get(lang, _TITLES["en"]).get(key, _TITLES["en"].get(key, key))


def t_body_game(game_name: str, quarter: int, diff: int, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    pl = period_label(quarter, lang)
    if lang == "he":
        return f"{gn}\n{pl} | הפרש: {diff} נק׳ | משחק צמוד!"
    return f"{gn}\n{pl} | Point Diff: {diff} | CLOSE GAME!"


def t_body_period_start(game_name: str, quarter: int, diff: int, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    pl = period_label(quarter, lang)
    if lang == "he":
        return f"{gn}\n{pl} התחיל! | הפרש: {diff} נק׳"
    return f"{gn}\n{pl} is underway! | Diff: {diff} pts"


def t_body_scoring(player_name: str, pts: int, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    if lang == "he":
        return f"{player_name} עם {pts} נקודות ב-{gn}!"
    return f"{player_name} has {pts} pts in {gn}!"


def t_body_early_pts(player_name: str, pts: int, quarter: int, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    pl = period_label(quarter, lang)
    if lang == "he":
        return f"{player_name}: {pts} נק׳ ב-{pl} ב-{gn}!"
    return f"{player_name}: {pts} pts by {pl} in {gn}!"


def t_body_triple_double(player_name: str, hot_stats: str, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    if lang == "he":
        return f"{player_name}: {hot_stats} ב-{gn}!"
    return f"{player_name}: {hot_stats} in {gn}!"


def t_body_test(lang: str) -> str:
    if lang == "he":
        return "ה-NBA Smart Alarm שלך עובד!"
    return "Your NBA Smart Alarm is working!"


def t_body_historic_scoring(player_name: str, pts: int, quarter: int, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    pl = period_label(quarter, lang)
    if lang == "he":
        return f"{player_name}: {pts} נק׳ ב-{pl} של {gn}!"
    return f"{player_name}: {pts} pts in {pl} of {gn}!"


def t_body_historic_threes(player_name: str, threes: int, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    if lang == "he":
        return f"{player_name}: {threes} שלשות ב-{gn}!"
    return f"{player_name}: {threes} threes in {gn}!"


def t_body_quad(player_name: str, hot_stats: str, game_name: str, lang: str) -> str:
    gn = translate_game_name(game_name, lang)
    if lang == "he":
        return f"{player_name}: {hot_stats} ב-{gn}!"
    return f"{player_name}: {hot_stats} in {gn}!"
