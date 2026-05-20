"""
Notification text translations for Clutch Alarm.
"""

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
        # Soccer titles (v3)
        "soccer_late_drama":   "⚡ Late Drama Brewing",
        "soccer_kickoff":      "🟢 Match Kicking Off",
        "soccer_knockout_et":  "⏱ Extra Time Underway!",
        "soccer_penalty":      "🎯 Penalty Awarded",
        "soccer_red_card":     "🟥 Red Card Drama",
        "soccer_comeback":     "🔥 Comeback In Progress",
        "soccer_hat_trick":    "⚽ Hat-Trick Watch",
        "soccer_big_chance":   "💥 Chances Pouring In",
        "soccer_drama_radar":  "⚡ Drama Brewing",
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
        # Soccer titles (v3)
        "soccer_late_drama":   "⚡ דרמה לקראת הסיום",
        "soccer_kickoff":      "🟢 המשחק יוצא לדרך",
        "soccer_knockout_et":  "⏱ הארכה במשחק!",
        "soccer_penalty":      "🎯 פנדל!",
        "soccer_red_card":     "🟥 דרמת כרטיס אדום",
        "soccer_comeback":     "🔥 קאמבק בתהליך",
        "soccer_hat_trick":    "⚽ מעקב האט-טריק",
        "soccer_big_chance":   "💥 הזדמנויות בכמויות",
        "soccer_drama_radar":  "⚡ דרמה בדרך",
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
    title = _TITLES.get(lang, _TITLES["en"]).get(key, _TITLES["en"].get(key, key))
    if key in ("radar", "fav_team") and league and league != "nba":
        league_name = _LEAGUE_DISPLAY.get(league, {}).get(lang, league.upper())
        # Replace the hardcoded "NBA" in the title with the actual league name
        title = title.replace("NBA", league_name)
    return title


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


def soccer_match_label(home_name: str, away_name: str, lang: str) -> str:
    """'Home vs Away' (EN) / 'בית נגד חוץ' (HE) — used in notification bodies."""
    if lang == "he":
        return f"{home_name} נגד {away_name}"
    return f"{home_name} vs {away_name}"


def t_soccer_body_late_drama(home: str, away: str, competition: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · דקה {minute} · משחק צמוד בסיום"
    return f"{m}\n{comp} · {minute}' · close game late on"


def t_soccer_body_kickoff(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · המשחק התחיל"
    return f"{m}\n{comp} · kicking off now"


def t_soccer_body_knockout_et(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · הולכים להארכות!"
    return f"{m}\n{comp} · heading to extra time!"


def t_soccer_body_penalty(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · פנדל!"
    return f"{m}\n{comp} · penalty!"


def t_soccer_body_red_card(home: str, away: str, competition: str, minute: int, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · דקה {minute} · כרטיס אדום במשחק צמוד"
    return f"{m}\n{comp} · {minute}' · red card in a close game"


def t_soccer_body_comeback(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · קאמבק בעיצומו"
    return f"{m}\n{comp} · comeback in progress"


def t_soccer_body_hat_trick(player_name: str, competition: str, lang: str) -> str:
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{player_name} עם 2 שערים\n{comp} · האט-טריק במרחק נגיעה"
    return f"{player_name} on 2 goals\n{comp} · hat-trick in reach"


def t_soccer_body_big_chance(home: str, away: str, competition: str, lang: str) -> str:
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · גל של הזדמנויות"
    return f"{m}\n{comp} · big chances pouring in"


def t_soccer_body_drama_radar(home: str, away: str, competition: str, lang: str) -> str:
    """Generic spoiler-safe Drama Radar body."""
    m = soccer_match_label(home, away, lang)
    comp = soccer_competition_name(competition, lang)
    if lang == "he":
        return f"{m}\n{comp} · שווה לפתוח את האפליקציה"
    return f"{m}\n{comp} · worth opening the app"
