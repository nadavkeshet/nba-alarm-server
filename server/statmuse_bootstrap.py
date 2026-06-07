"""
Bootstrap NBA career-high records from StatMuse into a local SQLite DB.

Usage:
    .venv/bin/python statmuse_bootstrap.py --test     # 15 tricky names
    .venv/bin/python statmuse_bootstrap.py            # full 575-player run
    .venv/bin/python statmuse_bootstrap.py --dump     # print what's in the DB

Output: career_records.sqlite3 alongside this file.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import httpx

UA = "ClutchAlarm/2.1 (+itamar@clutchalarm.com) python-httpx/bootstrap"
BASE = "https://www.statmuse.com/nba/ask"
ROSTER_FILE = Path("/tmp/nba_roster.json")
DB_FILE = Path(__file__).resolve().parent / "career_records.sqlite3"

# (stat key in our schema, StatMuse URL fragment, metric word(s) in description)
STATS = [
    ("pts", "points",      ("point", "points")),
    ("reb", "rebounds",    ("rebound", "rebounds", "board", "boards")),
    ("ast", "assists",     ("assist", "assists")),
    ("stl", "steals",      ("steal", "steals")),
    ("blk", "blocks",      ("block", "blocks")),
    ("3pm", "3-pointers",  ("three-pointer", "three-pointers", "three", "threes")),
]

META_RE = re.compile(r'<meta name="description" content="([^"]*)"')
NUM_WITH_METRIC_RE = re.compile(
    r"with (\d+)\s+(point|points|rebound|rebounds|board|boards|assist|assists|"
    r"steal|steals|block|blocks|three-pointer|three-pointers|three|threes)",
    re.IGNORECASE,
)

# Slug overrides for players whose auto-slug doesn't match StatMuse.
# Seed sparingly — only add after seeing a verified mismatch.
SLUG_OVERRIDES: dict[str, str] = {}


# ── Slug ────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """StatMuse slug: ascii-fold, lowercase, drop punctuation, hyphenate spaces."""
    if not name:
        return ""
    s = unicodedata.normalize("NFD", name)
    s = s.encode("ascii", "ignore").decode("ascii")           # strip accents
    s = s.lower()
    s = s.replace(".", "").replace("'", "").replace("’", "")  # C.J → cj, D'Angelo → dangelo
    s = re.sub(r"[^a-z0-9\s-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "-")


# ── DB ──────────────────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS player_records (
            player_id_365  TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            statmuse_slug  TEXT,
            career_pts     INTEGER,
            career_reb     INTEGER,
            career_ast     INTEGER,
            career_stl     INTEGER,
            career_blk     INTEGER,
            career_3pm     INTEGER,
            last_attempt   TEXT,
            last_success   TEXT,
            note           TEXT
        )"""
    )
    return conn


# ── Fetch + parse ───────────────────────────────────────────────────────────

def fetch_meta(client: httpx.Client, slug: str, stat_path: str) -> str | None:
    url = f"{BASE}/{slug}-career-high-{stat_path}"
    try:
        r = client.get(url, timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"    ! {stat_path}: http {exc.__class__.__name__}")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        print(f"    ! {stat_path}: HTTP {r.status_code}")
        return None
    m = META_RE.search(r.text)
    return m.group(1) if m else None


def parse_number(desc: str, expected_words: tuple[str, ...]) -> int | None:
    """Extract the number from 'with N {metric}' if metric matches."""
    for match in NUM_WITH_METRIC_RE.finditer(desc):
        n, unit = match.group(1), match.group(2).lower()
        if unit in expected_words:
            return int(n)
    return None


def verify_name_match(desc: str, name: str) -> bool:
    """Check the meta description mentions the queried player's first name."""
    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z]", "", s.lower())      # strip ALL non-letters (hyphens, apostrophes, dots)

    first = _norm(name.split()[0])
    if not first:
        return True
    return first in _norm(desc)


def fetch_player(client: httpx.Client, name: str, slug: str, delay: float) -> dict:
    """Returns dict of career highs keyed by our stat ids. Missing stats → None."""
    out: dict[str, int | None] = {k: None for k, _, _ in STATS}
    first_desc = ""
    for key, path, words in STATS:
        desc = fetch_meta(client, slug, path)
        if not first_desc and desc:
            first_desc = desc
        if desc:
            n = parse_number(desc, words)
            if n is not None:
                out[key] = n
        time.sleep(delay)
    return {"stats": out, "first_desc": first_desc}


# ── Drivers ─────────────────────────────────────────────────────────────────

def run(players: list[dict], delay: float, label: str) -> None:
    conn = open_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ok = partial = failed = 0
    wrong_player: list[str] = []
    print(f"Bootstrapping {len(players)} players  ({label})  delay={delay}s")
    print()
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for i, p in enumerate(players, 1):
            name = p["name"]
            pid  = str(p.get("id") or "")
            slug = SLUG_OVERRIDES.get(name.lower()) or slugify(name)
            print(f"[{i:>3}/{len(players)}] {name:<28} slug={slug}")
            result = fetch_player(client, name, slug, delay)
            stats = result["stats"]
            desc = result["first_desc"]
            name_ok = verify_name_match(desc, name) if desc else False
            hits = sum(1 for v in stats.values() if v is not None)
            note = None
            if desc and not name_ok:
                note = f"slug_collision (desc mentions someone else): {desc[:100]}"
                wrong_player.append(name)
                failed += 1
            elif hits == len(STATS):
                ok += 1
            elif hits > 0:
                partial += 1
                note = f"only {hits}/{len(STATS)} stats parsed"
            else:
                failed += 1
                note = "no stats parsed"
            print(f"      hits={hits}/{len(STATS)}  name_ok={name_ok}  {note or ''}")
            # Upsert
            if name_ok or hits == 0:
                conn.execute(
                    """INSERT INTO player_records (player_id_365, name, statmuse_slug,
                       career_pts, career_reb, career_ast, career_stl, career_blk, career_3pm,
                       last_attempt, last_success, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(player_id_365) DO UPDATE SET
                         name=excluded.name,
                         statmuse_slug=excluded.statmuse_slug,
                         career_pts=COALESCE(excluded.career_pts, player_records.career_pts),
                         career_reb=COALESCE(excluded.career_reb, player_records.career_reb),
                         career_ast=COALESCE(excluded.career_ast, player_records.career_ast),
                         career_stl=COALESCE(excluded.career_stl, player_records.career_stl),
                         career_blk=COALESCE(excluded.career_blk, player_records.career_blk),
                         career_3pm=COALESCE(excluded.career_3pm, player_records.career_3pm),
                         last_attempt=excluded.last_attempt,
                         last_success=CASE WHEN excluded.career_pts IS NOT NULL
                                           THEN excluded.last_success ELSE player_records.last_success END,
                         note=excluded.note""",
                    (pid or slug, name, slug,
                     stats["pts"], stats["reb"], stats["ast"], stats["stl"], stats["blk"], stats["3pm"],
                     now, now if hits else None, note),
                )
            conn.commit()
    conn.close()
    print()
    print(f"DONE  ok={ok}  partial={partial}  failed={failed}")
    if wrong_player:
        print(f"Wrong-player matches (need manual override):")
        for n in wrong_player:
            print(f"   - {n}")


def dump() -> None:
    conn = open_db()
    rows = conn.execute(
        "SELECT name, career_pts, career_reb, career_ast, career_stl, career_blk, career_3pm, note "
        "FROM player_records ORDER BY career_pts DESC NULLS LAST LIMIT 40"
    ).fetchall()
    print(f"{'name':<26} {'PTS':>4} {'REB':>4} {'AST':>4} {'STL':>4} {'BLK':>4} {'3PM':>4}  note")
    for r in rows:
        print(f"{r[0]:<26} {r[1] or '-':>4} {r[2] or '-':>4} {r[3] or '-':>4} "
              f"{r[4] or '-':>4} {r[5] or '-':>4} {r[6] or '-':>4}  {(r[7] or '')[:60]}")
    total = conn.execute("SELECT COUNT(*), SUM(CASE WHEN career_pts IS NOT NULL THEN 1 ELSE 0 END) FROM player_records").fetchone()
    print(f"\nRows: {total[0]}   with pts: {total[1]}")
    conn.close()


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test",   action="store_true", help="15-player smoke test")
    ap.add_argument("--dump",   action="store_true", help="print DB contents")
    ap.add_argument("--delay",  type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--limit",  type=int,   default=0, help="limit full run to N players")
    args = ap.parse_args()

    if args.dump:
        dump(); return 0

    if not ROSTER_FILE.exists():
        print(f"ERROR: {ROSTER_FILE} not found. Run the roster-fetch snippet first.", file=sys.stderr)
        return 1
    roster = json.loads(ROSTER_FILE.read_text())

    if args.test:
        # Edge-case sample: accents, Jr., initials, apostrophes, single-name
        wanted = [
            "Luka Dončić", "C.J McCollum", "Michael Porter Jr.", "Nikola Vučević",
            "Deni Avdija", "LeBron James", "Victor Wembanyama", "D'Angelo Russell",
            "Shai Gilgeous-Alexander", "Bogdan Bogdanović", "Karl-Anthony Towns",
            "OG Anunoby", "RJ Barrett", "Tim Hardaway Jr.", "Nikola Jokić",
        ]
        wanted_lower = {n.lower() for n in wanted}
        subset = [p for p in roster if p["name"].lower() in wanted_lower]
        # Fall back to name match if server has slightly different names
        if len(subset) < len(wanted):
            missing = wanted_lower - {p["name"].lower() for p in subset}
            for name in missing:
                subset.append({"name": name, "id": "", "team": "?"})
        run(subset, args.delay, "SMOKE TEST")
    else:
        if args.limit:
            roster = roster[: args.limit]
        run(roster, args.delay, "FULL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
