"""Verify the IDs in Server/config/competition_map.json still resolve on
BSD (bzzoiro) and 365scores.

Run on every deploy and any time a new season starts. Output is human-readable;
exit code is non-zero if any primary source is broken.

Usage:
    cd Server && python scripts/verify_competitions.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from score365.client import Client365

MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "competition_map.json"
BSD_BASE = "https://sports.bzzoiro.com/api/v2"


def verify_bsd(slug: str, league_id: int) -> tuple[bool, str]:
    token = os.environ.get("BZZOIRO_API_TOKEN", "").strip()
    if not token:
        return False, "BZZOIRO_API_TOKEN not set"
    try:
        r = httpx.get(
            f"{BSD_BASE}/leagues/{league_id}/",
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            return False, f"status {r.status_code}"
        d = r.json()
        return True, f"id={league_id} name={d.get('name')!r} country={d.get('country')!r}"
    except Exception as e:
        return False, f"error: {e}"


def verify_score365(slug: str, comp_id: int, expected_name_en: str) -> tuple[bool, str]:
    try:
        with Client365() as c:
            data = c.get("/competitions/", params={"competitions": str(comp_id), "sports": "1"})
            comps = data.get("competitions", [])
            if not comps:
                return False, "no competition returned"
            comp = next((x for x in comps if x.get("id") == comp_id), comps[0])
            return True, f"id={comp.get('id')} name={comp.get('name')!r}"
    except Exception as e:
        return False, f"error: {e}"


def main() -> int:
    if not MAP_PATH.exists():
        print(f"ERROR: {MAP_PATH} not found")
        return 2
    data = json.loads(MAP_PATH.read_text())

    failures = 0
    print(f"=== Verifying {len(data)} competitions (primary source marked ★) ===\n")
    for slug, entry in data.items():
        s365_id = entry.get("score365_id")
        bsd_id = entry.get("bsd_id")
        primary = entry.get("primary_source", "")
        expected = entry.get("name_en", slug)

        s365_ok, s365_msg = verify_score365(slug, s365_id, expected) if s365_id else (None, "—")
        bsd_ok, bsd_msg = verify_bsd(slug, bsd_id) if bsd_id else (None, "not covered")

        def mark(ok: bool | None, is_primary: bool) -> str:
            if ok is None:
                return " "
            base = "✓" if ok else "✗"
            return f"★{base}" if is_primary else f" {base}"

        print(f"  {slug:18s}  BSD  {mark(bsd_ok, primary=='bsd'):3s}  {bsd_msg}")
        print(f"  {'':18s}  365  {mark(s365_ok, primary=='score365'):3s}  {s365_msg}")

        # Only the primary must be green; secondary failures only warn.
        if primary == "bsd" and bsd_ok is not True:
            failures += 1
        elif primary == "score365" and s365_ok is not True:
            failures += 1

    if failures:
        print(f"\n!! {failures} broken PRIMARY mappings. Fix competition_map.json and re-run.")
        return 1
    print("\nAll primary mappings valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
