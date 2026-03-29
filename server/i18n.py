"""
Notification text translations for NBA Smart Alarm.
"""

# Hebrew team name map (English full name → Hebrew)
_TEAM_HE = {
    "Atlanta Hawks": "אטלנטה הוקס", "Boston Celtics": "בוסטון סלטיקס",
    "Brooklyn Nets": "ברוקלין נטס", "Charlotte Hornets": "שרלוט הורנטס",
    "Chicago Bulls": "שיקגו בולס", "Cleveland Cavaliers": "קליבלנד קאבלירס",
    "Dallas Mavericks": "דאלאס מאבריקס", "Denver Nuggets": "דנבר נאגטס",
    "Detroit Pistons": "דטרויט פיסטונס", "Golden State Warriors": "גולדן סטייט ווריירס",
    "Houston Rockets": "יוסטון רוקטס", "Indiana Pacers": "אינדיאנה פייסרס",
    "LA Clippers": "לוס אנג'לס קליפרס", "Los Angeles Lakers": "לוס אנג'לס לייקרס",
    "Memphis Grizzlies": "ממפיס גריזליס", "Miami Heat": "מיאמי היט",
    "Milwaukee Bucks": "מילווקי באקס", "Minnesota Timberwolves": "מינסוטה טימברוולבס",
    "New Orleans Pelicans": "ניו אורלינס פליקנס", "New York Knicks": "ניו יורק ניקס",
    "Oklahoma City Thunder": "אוקלהומה סיטי ת'אנדר", "Orlando Magic": "אורלנדו מאג'יק",
    "Philadelphia 76ers": "פילדלפיה סיקסרס", "Phoenix Suns": "פיניקס סאנס",
    "Portland Trail Blazers": "פורטלנד טרייל בלייזרס", "Sacramento Kings": "סקרמנטו קינגס",
    "San Antonio Spurs": "סן אנטוניו ספרס", "Toronto Raptors": "טורנטו רפטורס",
    "Utah Jazz": "יוטה ג'אז", "Washington Wizards": "וושינגטון וויזארדס",
}

# Hebrew abbreviation map
_ABBR_HE = {
    "ATL": "הוקס", "BOS": "סלטיקס", "BKN": "נטס", "CHA": "הורנטס",
    "CHI": "בולס", "CLE": "קאבס", "DAL": "מאבס", "DEN": "נאגטס",
    "DET": "פיסטונס", "GS": "ווריירס", "HOU": "רוקטס", "IND": "פייסרס",
    "LAC": "קליפרס", "LAL": "לייקרס", "MEM": "גריזליס", "MIA": "היט",
    "MIL": "באקס", "MIN": "וולבס", "NO": "פליקנס", "NY": "ניקס",
    "OKC": "ת'אנדר", "ORL": "מאג'יק", "PHI": "סיקסרס", "PHX": "סאנס",
    "POR": "בלייזרס", "SAC": "קינגס", "SA": "ספרס", "TOR": "רפטורס",
    "UTAH": "ג'אז", "WSH": "וויזארדס",
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
