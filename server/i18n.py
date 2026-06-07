"""
Notification text translations for Clutch Alarm.
"""
import re as _re
from typing import Optional

# Hebrew team name map (English full name → Hebrew full name)
_TEAM_HE = {
    # NBA
    "Atlanta Hawks": "אטלנטה הוקס", "Boston Celtics": "בוסטון סלטיקס",
    "Brooklyn Nets": "ברוקלין נטס", "Charlotte Hornets": "שרלו�� הורנטס",
    "Chicago Bulls": "שיקגו בולס", "Cleveland Cavaliers": "קליבלנד קאבלירס",
    "Dallas Mavericks": "דאלאס מאבריקס", "Denver Nuggets": "דנבר נאגטס",
    "Detroit Pistons": "דטרויט פיסטונס", "Golden State Warriors": "גולדן סטייט ווריירס",
    "Houston Rockets": "��וסטון רוקטס", "Indiana Pacers": "אינדיאנה פייסרס",
    "LA Clippers": "לוס אנג'לס קליפרס", "Los Angeles Lakers": "לוס אנג'לס לייקרס",
    "Memphis Grizzlies": "ממפיס גריזליס", "Miami Heat": "מיאמי היט",
    "Milwaukee Bucks": "מילווקי באקס", "Minnesota Timberwolves": "מינסוטה טימברוולבס",
    "New Orleans Pelicans": "ניו אורלינס פליקנס", "New York Knicks": "ניו יורק ניקס",
    "Oklahoma City Thunder": "אוקלהומה סיטי ת'אנדר", "Orlando Magic": "אורלנדו מאג'יק",
    "Philadelphia 76ers": "פילדלפיה סיקסרס", "Phoenix Suns": "פיניקס סאנס",
    "Portland Trail Blazers": "פורטלנד טרייל בלייז��ס", "Sacramento Kings": "סקרמנטו קינגס",
    "San Antonio Spurs": "סן אנטוניו ספרס", "Toronto Raptors": "טורנט�� רפטורס",
    "Utah Jazz": "יוטה ג'אז", "Washington Wizards": "וושינגטון וויזארדס",
    # Euroleague
    "Olympiacos": "אולימפיאקוס", "Anadolu Efes": "אנאדולו אפס",
    "Bayern Munich": "באיירן מינכן", "Baskonia": "באסקוניה",
    "FC Barcelona": "ברצלונה", "Dubai BC": "דובאי",
    "Crvena Zvezda": "הכוכב האדום בלגרד", "Hapoel Tel Aviv": "הפועל תל אביב",
    "LDLC ASVEL Villeurbanne": "וילרבאן", "Virtus Bologna": "וירטוס בולוניה",
    "Valencia Basket": "ולנסיה", "Zalgiris Kaunas": "ז'לגיריס קובנה",
    "AS Monaco": "מונאקו", "Olimpia Milano": "מילאנו",
    "Maccabi Tel Aviv": "מכבי תל אביב", "Panathinaikos": "פנאתינייקוס",
    "Fenerbahce": "פנרבחצ'ה", "Paris Basketball": "פריז בסקטבול",
    "Partizan": "פרטיזן בלגרד", "Real Madrid": "ריאל מדריד",
    # EuroCup
    "JL Bourg": "ז'אן לוק בורז'", "Besiktas": "בשיקטאש איסטנבול",
    "Bahçeşehir Koleji": "בהצ'שהיר קולג'י", "Cluj Napoca": "קלוז' נאפוקה",
    "KK Budućnost": "בודוצ'נוסט פודגוריצה", "Cedevita Olimpija": "צדוויטה אולימפיה",
    "Baxi Manresa": "באסקי מאנרסה", "Reyer Venezia": "ריאר ונציה",
    "Turk Telekom": "טורק טלקום אנקרה", "Ratiopharm Ulm": "ראטיופארם אולם",
    "Dolomiti Energia Trento": "דולומיטי טרנטו", "Bv Chemnitz 99": "בוו חמניץ 99",
    "London Lions": "לונדון ליונס", "Lietkabelis": "לייטקבליס פנבז'יס",
    "Hamburg Towers": "המבורג טאוורס", "Neptūnas": "נפטונאס קלייפדה",
    "Aris BSA": "אריס סלוניקי", "Slask Wroclaw": "סלאסק ורוצלב",
    "Panionios": "פנאוניוס אתונה",
    # FIBA BCL
    "ČEZ Nymburk": "נימבורק", "AEK Athens": 'אא"ק אתונה',
    "Promitheas Patras": "פרומיתאוס פטראס", "Alba Berlin": "אלבה ברלין",
    "Cholet Basket": "שולה בסקט", "Galatasaray": "גלאטסראיי איסטנבול",
    "Gran Canaria": "גראן קנריה", "Joventut Badalona": "חובנטוט בדלונה",
    "La Laguna Tenerife": "לגונה טנריפה", "Tofas Bursa": "טופאש בורסה",
    "Unicaja Málaga": "אוניקחה מלגה", "USC Heidelberg": "היידלברג",
    "Wurzburg Baskets": "וירצבורג", "Rytas Vilnius": "ריטאס וילנה",
    "Le Mans": "לה מאן", "As Karditsas": "אס קארדיצה",
    "Pallacanestro Trieste": "טריאסטה", "Chalon/Saone": "שאלון סור סון",
    "KK Spartak Subotica": "ספרטק סובוטיצה", "Levice": "לביצה",
    "Mersin Buyuksehir": "מרסין", "Szolnoki Olajbanyasz": "סולנוק",
    # Winner League (Israel)
    "Hapoel Jerusalem": "הפועל ירושלים", "Hapoel Holon": "הפועל חולון",
    "Bnei Herzliya": "בני הרצליה", "Ironi Ness Ziona": "עירוני נס ציונה",
    "Maccabi Ironi Ramat Gan": "מכבי עירוני רמת גן", "Hapoel Galil Elyon": "הפועל גליל עליון",
    "Ironi Kiryat Ata": "עירוני קרית אתא", "Hapoel Be'er Sheva/Dimona": "הפועל באר שבע/דימונה",
    "Elitzur Netanya": "אליצור נתניה", "Hapoel HaEmek": "הפועל העמק",
    "Maccabi Rishon LeZion": "מכבי ראשון לציון", "Maccabi Ironi Ra'anana": "מכבי עירוני רעננה",
}

# Hebrew abbreviation map
_ABBR_HE = {
    # NBA
    "ATL": "הוקס", "BOS": "סלטיקס", "BKN": "נטס", "CHA": "הורנטס",
    "CHI": "בולס", "CLE": "קאבס", "DAL": "מאבס", "DEN": "נאגטס",
    "DET": "פיסטונס", "GS": "ווריירס", "HOU": "רוקטס", "IND": "פייסרס",
    "LAC": "קליפרס", "LAL": "לייקרס", "MEM": "גריזליס", "MIA": "היט",
    "MIL": "באקס", "MIN": "וולבס", "NO": "פליקנס", "NY": "ניקס",
    "OKC": "ת'אנדר", "ORL": "מאג'יק", "PHI": "סיקסרס", "PHX": "סאנס",
    "POR": "בלייזרס", "SAC": "קינגס", "SA": "ספרס", "TOR": "רפטורס",
    "UTAH": "ג'אז", "WSH": "וויזארדס",
    # Euroleague
    "OLY": "אולימפיאקוס", "EFE": "אנאדולו אפס", "BAY": "באיירן",
    "BAS": "באסקוניה", "BAR": "ברצלונה", "DUB": "דובאי",
    "CZV": "הכוכב האדום", "HTA": 'הפועל ת"א', "LYN": "וילרבאן",
    "VIR": "וירטוס", "VAL": "ולנסיה", "ZAL": "ז'לגיריס",
    "MON": "מונאקו", "MLN": "מילאנו", "MTA": 'מכבי ת"א', "PAN": "פנאתינייקוס",
    "FEN": "פנרבחצ'ה", "PAR": "פריז", "PAT": "פרטיזן",
    "RMA": "ריאל מדריד",
    # EuroCup
    "BOU": "בורז'", "BES": "בשיקטאש", "BHK": "בהצ'שהיר",
    "CLJ": "קלוז'", "BUD": "בודוצ'נוסט", "CED": "צדוויטה",
    "MAN": "מאנרסה", "RVN": "ריאר ונציה", "TUR": "טורק טלקום",
    "ULM": "אולם", "TRE": "טרנטו", "CHM": "חמניץ", "LON": "לונדון",
    "LKB": "לייטקבליס", "HAM": "המבורג", "NEP": "נפטונאס",
    "ARI": "אריס", "SLW": "סלאסק", "PNI": "פנאוניוס",
    # FIBA BCL
    "NYM": "נימבורק", "AEK": 'אא"ק', "PRO": "פרומיתאוס",
    "ALB": "אלבה ברלין", "CHO": "שולה", "GLT": "גלאטסראיי",
    "GRA": "גראן קנריה", "JOV": "חובנטוט", "TEN": "טנריפה",
    "TOF": "טופאש", "UNI": "אוניקחה", "HEI": "היידלברג",
    "WUR": "וירצבורג", "RYT": "ריטאס", "LMN": "לה מאן",
    "KAR": "קארדיצה", "TRI": "טריאסטה", "CHL": "שאלון",
    "SUB": "סובוטיצה", "LEV": "לביצה", "MER": "מרסין", "SZO": "סולנוק",
    # Winner League (Israel)
    "JER": "הפועל ירושלים", "HOL": "הפועל חולון",
    "HER": "בני הרצליה", "NES": "נס ציונה",
    "RMG": "מכבי רמת גן", "GAL": "גליל עליון",
    "ATA": "קרית אתא", "BSV": "באר שבע/דימונה",
    "NET": "אליצור נתניה", "EMK": "הפועל העמק",
    "RSH": 'ראשל"צ', "RNA": "רעננה",
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
        "smart_alarm":      "It's Getting Dramatic!",
        "period_starting":  "Period Starting!",
        "scoring_alert":    "Scoring Alert!",
        "early_points":     "Early Points Alert!",
        "triple_double":    "Triple-Double Watch!",
        "fav_team":         "Favorite Team Alert!",
        "radar":            "Radar – Close Game!",
        "historic_scoring": "Historic Scoring Pace!",
        "historic_threes":  "Historic 3-Point Game!",
        "quad_double":      "Quad-Double Threat!",
        "test":             "Test Notification",
        "player_alert":     "Player Alert!",
        # Soccer titles (v3)
        "soccer_late_drama":   "⚡ Late Drama Brewing",
        "soccer_kickoff":      "🟢 Match Kicking Off",
        "soccer_halftime":     "⏸ Half-Time",
        "soccer_sensation":    "😱 Sensation Brewing",
        "soccer_knockout_et":  "⏱ Extra Time Underway!",
        "soccer_penalty":      "🎯 Penalty Awarded",
        "soccer_red_card":     "🟥 Red Card Drama",
        "soccer_comeback":     "🔥 Comeback In Progress",
        "soccer_hat_trick":    "⚽ Hat-Trick Watch",
        "soccer_big_chance":   "💥 Chances Pouring In",
        "soccer_drama_radar":  "⚡ Drama Brewing",
        # Soccer match-moments (v3)
        "soccer_goal":         "⚽ GOAL",
        "soccer_second_half":  "Second half statrted",
        "soccer_match_end":    "Full-time",
        "soccer_lineups":      "Lineups available",
        "soccer_reminder":     "Match starting soon",
        # Soccer player alerts
        "soccer_player_goal":     "⚽ Goal!",
        "soccer_player_clutch_goal": "⚽ Clutch Goal!",
        "soccer_player_brace":    "🔥 Brace Alert!",
        "soccer_player_hattrick": "🎩 HAT-TRICK!",
        "soccer_player_yellow":   "⚠️ Booked",
        "soccer_player_red":      "🟥 Sent Off!",
    },
    "he": {
        "smart_alarm":      "!הולך להיות דרמטי",
        "period_starting":  "!הרבע מתחיל",
        "scoring_alert":    "!התראת ניקוד",
        "early_points":     "!התראת נקודות מוקדמות",
        "triple_double":    "!מעקב טריפל דאבל",
        "fav_team":         "!התראת קבוצה מועדפת",
        "radar":            "!רדאר – משחק צמוד",
        "historic_scoring":  "!קצב ניקוד היסטורי",
        "historic_threes":  "!משחק שלשות היסטורי",
        "quad_double":      "!איום קוואדרופל דאבל",
        "test":             "התראת בדיקה",
        "player_alert":     "!התראת שחקן",
        # Soccer titles (v3)
        "soccer_late_drama":   "⚡ דרמה לקראת הסיום",
        "soccer_kickoff":      "🟢 המשחק יוצא לדרך",
        "soccer_halftime":     "⏸ מחצית",
        "soccer_sensation":    "😱 הפתעה בדרך",
        "soccer_knockout_et":  "⏱ הארכה במשחק!",
        "soccer_penalty":      "🎯 פנדל!",
        "soccer_red_card":     "🟥 כרטיס אדום",
        "soccer_comeback":     "🔥 תחילתו של קאמבק",
        "soccer_hat_trick":    "⚽ שלושער בדרך",
        "soccer_big_chance":   "💥 הזדמנויות בכמויות",
        "soccer_drama_radar":  "⚡ דרמה בדרך",
        # Soccer match-moments (v3)
        "soccer_goal":         "⚽ גול",
        "soccer_second_half":  "המחצית השנייה יצאה לדרך",
        "soccer_match_end":    "סיום משחק",
        "soccer_lineups":      "פורסמו ההרכבים",
        "soccer_reminder":     "המשחק עומד להתחיל",
        # Soccer player alerts
        "soccer_player_goal":     "⚽ שער!",
        "soccer_player_clutch_goal": "⚽ שער קריטי!",
        "soccer_player_brace":    "🔥 צמד!",
        "soccer_player_hattrick": "🎩 שלושער!",
        "soccer_player_yellow":   "⚠️ כרטיס צהוב",
        "soccer_player_red":      "🟥 אדום!",
    },
}


_LEAGUE_DISPLAY = {
    "nba":          {"en": "NBA",         "he": "NBA"},
    "euroleague":   {"en": "Euroleague",  "he": "יורוליג"},
    "eurocup":      {"en": "EuroCup",     "he": "יורוקאפ"},
    "bcl":          {"en": "BCL",         "he": "BCL"},
    "winner_league":{"en": "Winner",      "he": "ליגת וינר"},
}


def t_title(key: str, lang: str, league: str = "nba") -> str:
    # Competition/league is intentionally NOT shown in alert text. The `league`
    # arg is kept for call-site compatibility but no longer substituted into the
    # title (radar / favorite-team titles stay league-free).
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
        return "ה-Clutch Alarm שלך עובד!"
    return "Your Clutch Alarm is working!"


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


# ── Soccer notifications (v3) ──────────────────────────────────────────────────
# All soccer bodies are SPOILER-SAFE by default — they describe the *moment*,
# not the score. Score is revealed only when the user opens the app.

_SOCCER_COMPETITION_HE = {
    "premier_league": "פרמייר ליג",
    "ucl":            "ליגת האלופות",
    "europa_league":  "ליגה אירופית",
    "la_liga":        "לה ליגה",
    "serie_a":        "סריה א",
    "bundesliga":     "בונדסליגה",
    "ligue_1":        "ליג 1",
    "world_cup":      "מונדיאל",
    "euros":          "אליפות אירופה",
    "ligat_haal":     "ליגת ווינר",
}

_SOCCER_COMPETITION_EN = {
    "premier_league": "Premier League",
    "ucl":            "Champions League",
    "europa_league":  "Europa League",
    "la_liga":        "LaLiga",
    "serie_a":        "Serie A",
    "bundesliga":     "Bundesliga",
    "ligue_1":        "Ligue 1",
    "world_cup":      "World Cup",
    "euros":          "Euros",
    "ligat_haal":     "Ligat Ha'al",
}


def soccer_competition_name(slug: str, lang: str) -> str:
    table = _SOCCER_COMPETITION_HE if lang == "he" else _SOCCER_COMPETITION_EN
    return table.get(slug, slug.replace("_", " ").title())


def t_round_name(round_name: Optional[str], lang: str) -> Optional[str]:
    """Hebrew for a competition round/stage label ('Final', 'Group Stage',
    'Round of 16', 'Matchweek 12'…). Substring-matched against the common stages;
    returns the original for anything unmatched (so obscure labels stay English)."""
    if not round_name or lang != "he":
        return round_name
    n = round_name.strip().lower()
    # Order matters: 'semi'/'quarter' before 'final' (they contain 'final').
    for kw, he in (
        ("semi", "חצי גמר"),
        ("quarter", "רבע גמר"),
        ("round of 16", "שמינית גמר"),
        ("last 16", "שמינית גמר"),
        ("round of 32", "שלב ה-32"),
        ("third place", "מקום שלישי"),
        ("final", "גמר"),
        ("group", "שלב הבתים"),
        ("play-off", "פלייאוף"),
        ("playoff", "פלייאוף"),
        ("knockout", "שלב הנוקאאוט"),
        ("qualif", "מוקדמות"),
    ):
        if kw in n:
            return he
    m = _re.search(r"(?:matchweek|matchday|round|week)\s*(\d+)", n)
    if m:
        return f"מחזור {m.group(1)}"
    return round_name


def soccer_match_label(home_name: str, away_name: str, lang: str) -> str:
    """'Home vs Away' (EN) / 'בית נגד חוץ' (HE) — used in notification bodies."""
    if lang == "he":
        return f"{home_name} נגד {away_name}"
    return f"{home_name} vs {away_name}"


def t_soccer_title_goal(home: str, away: str, competition: str, lang: str) -> str:
    """Goal alert TITLE: the goal marker + the full match (the score + scorer
    live in the body). e.g. '⚽ Venezuela vs Türkiye'."""
    return f"⚽ {soccer_match_label(home, away, lang)}"


def t_soccer_body_late_drama(home: str, away: str, competition: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nדקה {minute} · משחק צמוד בסיום"
    return f"{m}\n{minute}' · close game late on"


def t_soccer_body_score_gap(home: str, away: str, competition: str,
                             threshold: int, from_minute: int, diff: int, lang: str) -> str:
    m    = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        score_desc = "שוויון" if diff == 0 else f"הפרש {diff}"
        return f"{m}\n{comp} · {score_desc} מדקה {from_minute}"
    score_desc = "level" if diff == 0 else f"gap of {diff}"
    return f"{m}\n{comp} · {score_desc} from minute {from_minute}"


def t_soccer_body_kickoff(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nהמשחק התחיל"
    return f"{m}\nkicking off now"


def t_soccer_body_halftime(home: str, away: str, home_score: int, away_score: int, lang: str) -> str:
    if lang == "he":
        return f"מחצית: {home} {home_score}-{away_score} {away}"
    return f"Half-time: {home} {home_score}-{away_score} {away}"


def t_soccer_body_sensation(underdog_name: str, competition: str, minute: int, lang: str) -> str:
    """Names the underdog and that they're ahead, without the exact scoreline —
    the 1-bit 'underdog leading' spoiler is the whole point of the alert."""
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{underdog_name} מובילים 😱\nדקה {minute} · האנדרדוג מוביל"
    return f"{underdog_name} are ahead 😱\n{minute}' · the underdog is leading"


def t_soccer_body_knockout_et(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nהולכים להארכה!"
    return f"{m}\nheading to extra time!"


def t_soccer_body_penalty(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nפנדל!"
    return f"{m}\npenalty!"


def t_soccer_body_red_card(home: str, away: str, competition: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nדקה {minute} · כרטיס אדום במשחק צמוד"
    return f"{m}\n{minute}' · red card in a close game"


def t_soccer_body_comeback(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nתחילתו של קאמבק"
    return f"{m}\ncomeback in progress"


def t_soccer_body_hat_trick(player_name: str, competition: str, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{player_name} עם 2 שערים\nשלשוער בדרך"
    return f"{player_name} on 2 goals\nhat-trick in reach"


def t_soccer_body_big_chance(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nמלא הזדמנויות"
    return f"{m}\nbig chances pouring in"


def t_soccer_body_player_goal(player_name: str, home_score: int, away_score: int,
                              minute: int, competition: str, context: str, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    score = f"{home_score}-{away_score}"
    if lang == "he":
        ctx_map = {"equalizer": "משווה", "go-ahead": "הובלה", "comeback": "קאמבק"}
        ctx_str = f" ({ctx_map.get(context, context)})" if context else ""
        return f"{player_name} עם שער ⚽ · דקה {minute} · {score}{ctx_str}"
    ctx_str = f" ({context})" if context else ""
    return f"{player_name} scores ⚽ · {minute}' · {score}{ctx_str}"


def t_soccer_body_player_brace(player_name: str, competition: str, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{player_name} עם שני שערים! 🔥\nשלשוער בדרך"
    return f"{player_name} bags a brace! 🔥\nhat-trick in reach"


def t_soccer_body_player_hattrick(player_name: str, competition: str, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{player_name} משלים שלושער! 🎩"
    return f"{player_name} completes the hat-trick! 🎩"


def t_soccer_body_player_yellow(player_name: str, home: str, away: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    if lang == "he":
        return f"{player_name} קיבל כרטיס צהוב ⚠️ · דקה {minute}\n{m} · סכנת השעיה"
    return f"{player_name} booked ⚠️ · {minute}'\n{m} · suspension risk"


def t_soccer_body_player_red(player_name: str, home: str, away: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    if lang == "he":
        return f"{player_name} הורחק 🟥 · דקה {minute}\n{m}"
    return f"{player_name} sent off 🟥 · {minute}'\n{m}"


def t_soccer_body_drama_radar(home: str, away: str, competition: str, drama: float | None = None, lang: str = "en") -> str:
    """Spoiler-safe Drama Radar body. Surfaces the drama score (rounded
    to one decimal) so users can decide whether to look."""
    # Accept legacy 4-positional callers: (home, away, comp, lang)
    if isinstance(drama, str):
        lang, drama = drama, None

    m    = soccer_match_label(home, away, lang)
    drama_tag = f"{drama:.1f}/10 · " if drama is not None else ""
    if lang == "he":
        return f"{m}\n{drama_tag}שווה לפתוח שידור"
    return f"{m}\n{drama_tag}worth opening a broadcast"


# ── Soccer match-moments (v3) ──────────────────────────────────────────────────
# Unlike the spoiler-safe drama alerts above, match-moment alerts (goal, full-time)
# DO reveal the scoreline — the user opted in to follow the match's flow.

def t_soccer_body_goal(home: str, away: str, home_score: int, away_score: int,
                       scored_side: str | None, lang: str, scorer: str | None = None) -> str:
    """Goal body: the real score with the SCORING side's tally parenthesized,
    then the team that scored (the title already says "GOAL"). Appends the
    goalscorer when it's already available — the REST incident with the scorer
    lags the live score, so it's included best-effort and omitted otherwise.
    Plain scoreline when the scorer side can't be attributed."""
    if scored_side == "home":
        base = f"({home_score}) - {away_score}  {home}"
    elif scored_side == "away":
        base = f"{home_score} - ({away_score})  {away}"
    else:
        base = f"{home_score} - {away_score}"
    return f"{base} · {scorer}" if scorer else base


def t_soccer_body_second_half(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\nהמחצית השנייה יצאה לדרך"
    return f"{m}\nsecond half started"


def t_soccer_body_match_end(home: str, away: str, competition: str,
                            home_score: int, away_score: int, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"סיום משחק: {home} {home_score}-{away_score} {away}"
    return f"Full-time: {home} {home_score}-{away_score} {away}"


def t_soccer_body_lineups(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"ההרכבים פורסמו — {m}"
    return f"Confirmed lineups are out — {m}"


def t_soccer_body_reminder(home: str, away: str, competition: str,
                           minutes: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m} מתחיל בעוד {minutes} דקות"
    return f"{m} starts in {minutes} min"


# ---------------------------------------------------------------------------
# Soccer "Historic Moments" (see soccer_historic.py)
# ---------------------------------------------------------------------------
#
# One bilingual (title, body) template per category key in
# soccer_historic.CATEGORY_KEYS. Bodies pull from a `params` dict; every lookup
# uses params.get(...) so a missing field degrades to "" rather than KeyError.

def _shist_ctx(params: dict, lang: str) -> dict:
    """Common derived fields used by the templates."""
    p = params or {}
    comp = soccer_competition_name(str(p.get("competition") or ""), lang)
    return {
        "player": p.get("player") or "",
        "team": p.get("team") or "",
        "opp": p.get("opp") or "",
        "home_name": p.get("home_name") or "",
        "away_name": p.get("away_name") or "",
        "home": p.get("home_score", p.get("home", 0)),
        "away": p.get("away_score", p.get("away", 0)),
        "minute": p.get("minute", 0),
        "count": p.get("count", 0),
        "total": p.get("total", 0),
        "margin": p.get("margin", 0),
        "assists": p.get("assists", 0),
        "goals": p.get("goals", 0),
        "comp": comp,
    }


def t_shist(category: str, lang: str, params: dict) -> tuple[str, str]:
    """Return (title, body) for a soccer historic-moment category in 'en'/'he'."""
    lang = "he" if lang == "he" else "en"
    c = _shist_ctx(params, lang)
    he = lang == "he"

    # vs-line: "Home-Away vs Opp" — built per language for RTL friendliness.
    def score():
        return f"{c['home']}-{c['away']}"

    if category == "hat_trick":
        if he:
            return ("🎩 שלושער!",
                    f"{c['player']} עשה שלושער עבור {c['team']}! {score()} מול {c['opp']} · {c['comp']}")
        return ("🎩 Hat-Trick!",
                f"{c['player']} has a hat-trick for {c['team']}! {score()} vs {c['opp']} · {c['comp']}")

    if category == "poker":
        if he:
            return ("🃏 ארבעה שערים!",
                    f"{c['player']} כבש 4 שערים עבור {c['team']}! {score()} מול {c['opp']} · {c['comp']}")
        return ("🃏 Four Goals!",
                f"{c['player']} scores 4 for {c['team']}! {score()} vs {c['opp']} · {c['comp']}")

    if category == "five_plus":
        if he:
            return ("🌟 ערב היסטורי!",
                    f"{c['player']} כבש {c['count']} שערים עבור {c['team']}! {score()} · {c['comp']}")
        return ("🌟 Historic Haul!",
                f"{c['player']} scores {c['count']} for {c['team']}! {score()} · {c['comp']}")

    if category == "fast_hat_trick":
        if he:
            return ("⚡ שלושער מהיר!",
                    f"{c['player']} עשה שלושער בזק עבור {c['team']}! {score()} מול {c['opp']} · {c['comp']}")
        return ("⚡ Lightning Hat-Trick!",
                f"{c['player']} fires a rapid hat-trick for {c['team']}! {score()} vs {c['opp']} · {c['comp']}")

    if category == "penalty_hat_trick":
        if he:
            return ("🎯 שלושער פנדלים!",
                    f"{c['player']} כבש {c['count']} פנדלים עבור {c['team']}! {score()} · {c['comp']}")
        return ("🎯 Penalty Hat-Trick!",
                f"{c['player']} converts {c['count']} penalties for {c['team']}! {score()} · {c['comp']}")

    if category == "goal_involvements":
        if he:
            return ("🔗 מעורבות מלאה!",
                    f"{c['player']} עם {c['goals']} שערים ו-{c['assists']} בישולים עבור {c['team']}! {c['comp']}")
        return ("🔗 Total Influence!",
                f"{c['player']}: {c['goals']} goals & {c['assists']} assists for {c['team']}! {c['comp']}")

    if category == "rout":
        if he:
            return ("💥 מחיקה!",
                    f"הפרש של {c['margin']} שערים! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("💥 Total Rout!",
                f"A {c['margin']}-goal margin! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "goal_feast":
        if he:
            return ("🍿 חגיגת שערים!",
                    f"{c['total']} שערים במשחק! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("🍿 Goal Feast!",
                f"{c['total']} goals in this match! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "end_to_end":
        if he:
            return ("🔄 משחק פתוח!",
                    f"שני הצדדים כבשו 3+! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("🔄 End to End!",
                f"Both sides hit 3+! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "goal_blitz":
        if he:
            return ("🔥 מטח שערים!",
                    f"3 שערים בתוך דקות! {c['home_name']} {score()} {c['away_name']} · דקה {c['minute']} · {c['comp']}")
        return ("🔥 Goal Blitz!",
                f"3 goals in minutes! {c['home_name']} {score()} {c['away_name']} · {c['minute']}' · {c['comp']}")

    if category == "lightning_goal":
        if he:
            return ("⚡ שער בזק!",
                    f"{c['player']} כבש בדקה {c['minute']} עבור {c['team']}! {score()} · {c['comp']}")
        return ("⚡ Lightning Goal!",
                f"{c['player']} scores in minute {c['minute']} for {c['team']}! {score()} · {c['comp']}")

    if category == "last_gasp_winner":
        if he:
            return ("⏱️ שער ניצחון בסיום!",
                    f"{c['player']} כבש בדקה {c['minute']} והפך את התוצאה ל-{c['team']}! {score()} מול {c['opp']} · {c['comp']}")
        return ("⏱️ Last-Gasp Winner!",
                f"{c['player']} strikes in the {c['minute']}' to put {c['team']} ahead! {score()} vs {c['opp']} · {c['comp']}")

    if category == "historic_comeback":
        if he:
            return ("🔙 קאמבק היסטורי!",
                    f"{c['team']} חזרו מפיגור של 3+ שערים! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("🔙 Historic Comeback!",
                f"{c['team']} battle back from 3+ down! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "early_red":
        if he:
            return ("🟥 אדום מוקדם!",
                    f"{c['player']} ({c['team']}) קיבל כרטיס אדום בדקה {c['minute']}! {c['comp']}")
        return ("🟥 Early Red!",
                f"{c['player']} ({c['team']}) sent off in minute {c['minute']}! {c['comp']}")

    if category == "mass_red_cards":
        if he:
            return ("🟥 כאוס כרטיסים אדומים!",
                    f"{c['count']} כרטיסים אדומים במשחק! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("🟥 Red-Card Chaos!",
                f"{c['count']} red cards in this match! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "penalty_barrage":
        if he:
            return ("🎯 מטר פנדלים!",
                    f"{c['count']} פנדלים נפסקו במשחק! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("🎯 Penalty Barrage!",
                f"{c['count']} penalties awarded in this match! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "stoppage_penalty":
        if he:
            return ("⏱️ פנדל בתוספת הזמן!",
                    f"{c['player']} כבש פנדל בדקה {c['minute']} עבור {c['team']}! {score()} · {c['comp']}")
        return ("⏱️ Stoppage-Time Penalty!",
                f"{c['player']} converts a penalty in the {c['minute']}' for {c['team']}! {score()} · {c['comp']}")

    if category == "own_goal":
        if he:
            return ("😬 שער עצמי!",
                    f"{c['player']} ({c['team']}) כבש שער עצמי בדקה {c['minute']}! {score()} · {c['comp']}")
        return ("😬 Own Goal!",
                f"{c['player']} ({c['team']}) turns it into his own net in the {c['minute']}'! {score()} · {c['comp']}")

    if category == "multiple_own_goals":
        if he:
            return ("😬 ריבוי שערים עצמיים!",
                    f"{c['count']} שערים עצמיים במשחק! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")
        return ("😬 Own-Goal Madness!",
                f"{c['count']} own goals in this match! {c['home_name']} {score()} {c['away_name']} · {c['comp']}")

    if category == "goalkeeper_goal":
        if he:
            return ("🥅 שוער כובש!",
                    f"השוער {c['player']} כבש עבור {c['team']}! {score()} · {c['comp']}")
        return ("🥅 Keeper Goal!",
                f"Goalkeeper {c['player']} scores for {c['team']}! {score()} · {c['comp']}")

    if category == "defender_brace":
        if he:
            return ("🛡️ צמד של בלם!",
                    f"הבלם {c['player']} כבש {c['count']} שערים עבור {c['team']}! {score()} · {c['comp']}")
        return ("🛡️ Defender's Brace!",
                f"Defender {c['player']} bags {c['count']} for {c['team']}! {score()} · {c['comp']}")

    if category == "marathon_shootout":
        if he:
            return ("🥵 דו-קרב פנדלים מרתוני!",
                    f"הכרעת הפנדלים הגיעה למוות פתאומי — {c['count']} בעיטות! {c['home_name']} מול {c['away_name']} · {c['comp']}")
        return ("🥵 Marathon Shootout!",
                f"The shootout reaches sudden death — {c['count']} kicks! {c['home_name']} vs {c['away_name']} · {c['comp']}")

    if category == "shootout_decided":
        if he:
            return ("🎲 הוכרע בפנדלים!",
                    f"המשחק הוכרע בדו-קרב פנדלים! {c['home_name']} מול {c['away_name']} · {c['comp']}")
        return ("🎲 Decided on Penalties!",
                f"The match is settled in a shootout! {c['home_name']} vs {c['away_name']} · {c['comp']}")

    # Unknown category — degrade gracefully.
    if he:
        return ("⚽ רגע היסטורי", f"{c['home_name']} מול {c['away_name']} · {c['comp']}")
    return ("⚽ Historic Moment", f"{c['home_name']} vs {c['away_name']} · {c['comp']}")
