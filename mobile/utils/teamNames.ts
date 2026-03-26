// ─── NBA Team Hebrew Translations ─────────────────────────────────────────────

interface TeamHe {
  full:  string;   // "בוסטון סלטיקס"
  short: string;   // "סלטיקס" (nickname only, for home screen)
}

// Keyed by ESPN abbreviation (all known variants)
export const NBA_TEAMS_BY_ABBR: Record<string, TeamHe> = {
  ATL:  { full: 'אטלנטה הוקס',            short: 'הוקס'         },
  BOS:  { full: 'בוסטון סלטיקס',           short: 'סלטיקס'       },
  BKN:  { full: 'ברוקלין נטס',             short: 'נטס'          },
  CHA:  { full: 'שארלוט הורנטס',           short: 'הורנטס'       },
  CHI:  { full: 'שיקגו בולס',              short: 'בולס'         },
  CLE:  { full: 'קליבלנד קאבלירס',         short: 'קאבלירס'      },
  DAL:  { full: 'דאלאס מאבריקס',           short: 'מאבריקס'      },
  DEN:  { full: 'דנוור נאגטס',             short: 'נאגטס'        },
  DET:  { full: 'דטרויט פיסטונס',          short: 'פיסטונס'      },
  GS:   { full: 'גולדן סטייט ווריורס',     short: 'ווריורס'      },
  GSW:  { full: 'גולדן סטייט ווריורס',     short: 'ווריורס'      },
  HOU:  { full: 'יוסטון רוקטס',            short: 'רוקטס'        },
  IND:  { full: 'אינדיאנה פייסרס',         short: 'פייסרס'       },
  LAC:  { full: "לוס אנג'לס קליפרס",       short: 'קליפרס'       },
  LAL:  { full: "לוס אנג'לס לייקרס",       short: 'לייקרס'       },
  MEM:  { full: 'ממפיס גריזליס',           short: 'גריזליס'      },
  MIA:  { full: 'מיאמי היט',               short: 'היט'          },
  MIL:  { full: 'מילווקי באקס',            short: 'באקס'         },
  MIN:  { full: 'מינסוטה טימברוולבס',      short: 'טימברוולבס'   },
  NO:   { full: 'ניו אורלינס פליקנס',      short: 'פליקנס'       },
  NOP:  { full: 'ניו אורלינס פליקנס',      short: 'פליקנס'       },
  NY:   { full: 'ניו יורק ניקס',           short: 'ניקס'         },
  NYK:  { full: 'ניו יורק ניקס',           short: 'ניקס'         },
  OKC:  { full: "אוקלהומה סיטי ת'אנדר",   short: "ת'אנדר"       },
  ORL:  { full: "אורלנדו מג'יק",           short: "מג'יק"        },
  PHI:  { full: "פילדלפיה 76'",            short: "76'"          },
  PHX:  { full: 'פיניקס סאנס',             short: 'סאנס'         },
  POR:  { full: 'פורטלנד טרייל בלייזרס',  short: 'בלייזרס'      },
  SAC:  { full: 'סקרמנטו קינגס',           short: 'קינגס'        },
  SA:   { full: 'סן אנטוניו ספרס',         short: 'ספרס'         },
  SAS:  { full: 'סן אנטוניו ספרס',         short: 'ספרס'         },
  TOR:  { full: 'טורונטו ראפטורס',         short: 'ראפטורס'      },
  UTA:  { full: "יוטה ג'אז",               short: "ג'אז"         },
  UTAH: { full: "יוטה ג'אז",               short: "ג'אז"         },
  WSH:  { full: 'ושינגטון וויזארדס',       short: 'וויזארדס'     },
  WAS:  { full: 'ושינגטון וויזארדס',       short: 'וויזארדס'     },
};

// Keyed by ESPN English full name (for translating game name strings)
const NBA_TEAMS_BY_FULL: Record<string, TeamHe> = {
  'Atlanta Hawks':              NBA_TEAMS_BY_ABBR.ATL,
  'Boston Celtics':             NBA_TEAMS_BY_ABBR.BOS,
  'Brooklyn Nets':              NBA_TEAMS_BY_ABBR.BKN,
  'Charlotte Hornets':          NBA_TEAMS_BY_ABBR.CHA,
  'Chicago Bulls':              NBA_TEAMS_BY_ABBR.CHI,
  'Cleveland Cavaliers':        NBA_TEAMS_BY_ABBR.CLE,
  'Dallas Mavericks':           NBA_TEAMS_BY_ABBR.DAL,
  'Denver Nuggets':             NBA_TEAMS_BY_ABBR.DEN,
  'Detroit Pistons':            NBA_TEAMS_BY_ABBR.DET,
  'Golden State Warriors':      NBA_TEAMS_BY_ABBR.GS,
  'Houston Rockets':            NBA_TEAMS_BY_ABBR.HOU,
  'Indiana Pacers':             NBA_TEAMS_BY_ABBR.IND,
  'Los Angeles Clippers':       NBA_TEAMS_BY_ABBR.LAC,
  'Los Angeles Lakers':         NBA_TEAMS_BY_ABBR.LAL,
  'Memphis Grizzlies':          NBA_TEAMS_BY_ABBR.MEM,
  'Miami Heat':                 NBA_TEAMS_BY_ABBR.MIA,
  'Milwaukee Bucks':            NBA_TEAMS_BY_ABBR.MIL,
  'Minnesota Timberwolves':     NBA_TEAMS_BY_ABBR.MIN,
  'New Orleans Pelicans':       NBA_TEAMS_BY_ABBR.NO,
  'New York Knicks':            NBA_TEAMS_BY_ABBR.NY,
  'Oklahoma City Thunder':      NBA_TEAMS_BY_ABBR.OKC,
  'Orlando Magic':              NBA_TEAMS_BY_ABBR.ORL,
  'Philadelphia 76ers':         NBA_TEAMS_BY_ABBR.PHI,
  'Phoenix Suns':               NBA_TEAMS_BY_ABBR.PHX,
  'Portland Trail Blazers':     NBA_TEAMS_BY_ABBR.POR,
  'Sacramento Kings':           NBA_TEAMS_BY_ABBR.SAC,
  'San Antonio Spurs':          NBA_TEAMS_BY_ABBR.SA,
  'Toronto Raptors':            NBA_TEAMS_BY_ABBR.TOR,
  'Utah Jazz':                  NBA_TEAMS_BY_ABBR.UTA,
  'Washington Wizards':         NBA_TEAMS_BY_ABBR.WSH,
};

/**
 * Translate an ESPN full team name (e.g. "Atlanta Hawks") to Hebrew.
 * Returns the original string if not found.
 */
export function teamFullToHe(name: string): string {
  return NBA_TEAMS_BY_FULL[name]?.full ?? name;
}

/**
 * Translate an ESPN abbreviation (e.g. "BOS") to Hebrew short nickname.
 * Returns the original abbreviation if not found.
 */
export function teamAbbrToHe(abbr: string): string {
  return NBA_TEAMS_BY_ABBR[abbr]?.short ?? abbr;
}

/**
 * Translate an ESPN game name string like "Atlanta Hawks at Boston Celtics"
 * to Hebrew: "אטלנטה הוקס @ בוסטון סלטיקס".
 */
export function translateGameName(gameName: string): string {
  if (!gameName) return gameName;
  let result = gameName;
  for (const [eng, he] of Object.entries(NBA_TEAMS_BY_FULL)) {
    result = result.replace(eng, he.full);
  }
  // normalise connectors
  result = result.replace(/ at /g, ' @ ').replace(/ vs\.? /gi, ' @ ');
  return result;
}
