"""Verify the IDs in Server/config/competition_map.json still resolve on
365scores and Sofascore.

Run on every deploy and any time a new season starts. Output is human-readable;
exit code is non-zero if any source is broken.

Usage:
    cd Server && python scripts/verify_competitions.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cf_requests
from score365.client import Client365

MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "competition_map.json"
SOFA_HEADERS = {"Referer": "https://www.sofascore.com/"}


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


def verify_sofascore(slug: str, tournament_id: int, expected_name_en: str) -> tuple[bool, str]:
    try:
        for impersonate in ("chrome120", "safari17_0", "chrome"):
            r = cf_requests.get(
                f"https://api.sofascore.com/api/v1/unique-tournament/{tournament_id}",
                impersonate=impersonate,
                timeout=15,
                headers=SOFA_HEADERS,
            )
            if r.status_code == 200:
                ut = r.json().get("uniqueTournament", {})
                name = ut.get("name")
                country = (ut.get("category", {}) or {}).get("name")
                return True, f"id={tournament_id} name={name!r} country={country!r}"
            if r.status_code in (403, 429):
                continue
            return False, f"status {r.status_code}"
        return False, "blocked after rotating impersonate"
    except Exception as e:
        return False, f"error: {e}"


def main() -> int:
    if not MAP_PATH.exists():
        print(f"ERROR: {MAP_PATH} not found")
        return 2
    data = json.loads(MAP_PATH.read_text())

    failures = 0
    print(f"=== Verifying {len(data)} competitions ===\n")
    for slug, entry in data.items():
        s365_id = entry.get("score365_id")
        sofa_id = entry.get("sofascore_id")
        expected = entry.get("name_en", slug)

        s365_ok, s365_msg = verify_score365(slug, s365_id, expected) if s365_id else (False, "no id")
        sofa_ok, sofa_msg = verify_sofascore(slug, sofa_id, expected) if sofa_id else (False, "no id")

        s365_mark = "✓" if s365_ok else "✗"
        sofa_mark = "✓" if sofa_ok else "✗"
        print(f"  {slug:20s}  365 {s365_mark}  {s365_msg}")
        print(f"  {'':20s}  Sofa {sofa_mark}  {sofa_msg}")
        if not s365_ok:
            failures += 1
        if not sofa_ok:
            failures += 1

    if failures:
        print(f"\n!! {failures} broken mappings. Fix competition_map.json and re-run.")
        return 1
    print("\nAll mappings valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
