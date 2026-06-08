"""Hand-rolled RFC 5545 (iCalendar) writer for match subscriptions.

No external deps. Renders a VCALENDAR of VEVENTs from normalized match dicts
(soccer- or basketball-shaped, each tagged with `_sport`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import country_flags

DUR_SOCCER_MIN = 115
DUR_BBALL_MIN = 150

# League slug -> human display name for DESCRIPTION (extend as needed; fall back
# to a title-cased slug).
_LEAGUE_NAMES = {
    "world_cup": "World Championship",
    "premier_league": "Premier League",
    "la_liga": "La Liga",
    "serie_a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue_1": "Ligue 1",
    "ucl": "Champions League",
    "uel": "Europa League",
    "nba": "NBA",
    "euroleague": "EuroLeague",
    "eurocup": "EuroCup",
    "bcl": "Basketball Champions League",
    "winner_league": "Winner League",
}


def _league_name(slug: str | None) -> str:
    if not slug:
        return ""
    return _LEAGUE_NAMES.get(slug, slug.replace("_", " ").title())


def _parse_utc(s: str | None) -> datetime | None:
    """Parse an ISO8601 UTC timestamp (…Z or +00:00) into a tz-aware UTC datetime."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _ics_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    """Escape a TEXT value per RFC5545: backslash, semicolon, comma, newline."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n").replace("\r", ""))


def _fold(line: str) -> str:
    """Fold a content line at 75 OCTETS (UTF-8), continuation lines start with a
    space. Never split a multibyte UTF-8 character (matters for emoji)."""
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    chunks = []
    while len(b) > 75:
        cut = 75
        while cut > 0 and (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        chunks.append(b[:cut])
        b = b" " + b[cut:]
    chunks.append(b)
    return b"\r\n".join(chunks).decode("utf-8")


def _team_label(name_en: str, name_he: str | None, lang: str) -> str:
    """English name, or its Hebrew form in he mode (explicit name_he → dict → en)."""
    if lang != "he":
        return name_en
    if name_he:
        return name_he
    try:
        import team_names_he
        return team_names_he.hebrew_name(name_en) or name_en
    except Exception:
        return name_en


def _normalize(m: dict, lang: str = "en") -> dict | None:
    """Unify a soccer/basketball match dict → a flat event dict, or None if it
    lacks a usable start time. Team names follow `lang` (Hebrew when available)."""
    sport = m.get("_sport")
    if sport == "soccer":
        start = _parse_utc(m.get("kickoff"))
        if not start:
            return None
        home_en = (m.get("home") or {}).get("name") or "?"
        away_en = (m.get("away") or {}).get("name") or "?"
        home = _team_label(home_en, (m.get("home") or {}).get("name_he"), lang)
        away = _team_label(away_en, (m.get("away") or {}).get("name_he"), lang)
        sep = "נגד" if lang == "he" else "vs"
        comp = m.get("competition") or ""
        venue = m.get("venue") or {}
        location = venue.get("name") if isinstance(venue, dict) else None
        status = (m.get("status") or "").replace("notstarted", "Scheduled").title()
        if comp == "world_cup":
            fh, fa = country_flags.country_flag(home_en), country_flags.country_flag(away_en)
            summary = " ".join(x for x in [fh, home, sep, away, fa] if x).strip()
        else:
            summary = f"{home} {sep} {away}"
        dur = DUR_SOCCER_MIN
    elif sport == "basketball":
        start = _parse_utc(m.get("date"))
        if not start:
            return None
        comps = (m.get("competitions") or [{}])[0].get("competitors") or []
        home = away = "?"
        for c in comps:
            nm = _team_label((c.get("team") or {}).get("displayName") or "?", None, lang)
            if c.get("homeAway") == "home":
                home = nm
            elif c.get("homeAway") == "away":
                away = nm
        comp = m.get("league") or ""
        location = None
        status = ((m.get("status") or {}).get("type") or {}).get("detail") or ""
        summary = f"{away} נגד {home}" if lang == "he" else f"{away} @ {home}"
        dur = DUR_BBALL_MIN
    else:
        return None

    return {
        "uid": f'{m.get("id")}@clutchalarm.com',
        "start": start,
        "end": start + timedelta(minutes=dur),
        "summary": summary,
        "location": location,
        "description": " • ".join(x for x in [_league_name(comp), status] if x),
    }


def build_calendar(matches: list[dict], *, cal_name: str = "Clutch Alarm", lang: str = "en") -> str:
    """Render a VCALENDAR string from normalized (soccer/basketball) match dicts."""
    now = datetime.now(timezone.utc)
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Clutch Alarm//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold("X-WR-CALNAME:" + _esc(cal_name)),
        _fold("NAME:" + _esc(cal_name)),
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]
    for m in matches:
        ev = _normalize(m, lang)
        if not ev:
            continue
        lines.append("BEGIN:VEVENT")
        lines.append(_fold("UID:" + ev["uid"]))
        lines.append("DTSTAMP:" + _ics_dt(now))
        lines.append("DTSTART:" + _ics_dt(ev["start"]))
        lines.append("DTEND:" + _ics_dt(ev["end"]))
        lines.append(_fold("SUMMARY:" + _esc(ev["summary"])))
        if ev["location"]:
            lines.append(_fold("LOCATION:" + _esc(ev["location"])))
        if ev["description"]:
            lines.append(_fold("DESCRIPTION:" + _esc(ev["description"])))
        lines.append("STATUS:CONFIRMED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
