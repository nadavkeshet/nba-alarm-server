# server/tests_search_merge.py
import search_index as si
import bb_team_crosswalk as bx

def main():
    # The live search path appends a BSD sibling row that COPIES the 365 row
    # (only the id differs), so both rows share an identical name and merge
    # on the plain (sport, normalized-name) key — no alias needed here.
    rows = [
        {"id": "631", "name": "Maccabi Tel Aviv", "sport": "basketball", "score": 1.0},
        {"id": "33",  "name": "Maccabi Tel Aviv", "sport": "basketball", "score": 0.9},
    ]
    merged = si._merge_duplicate_teams(rows)
    assert len(merged) == 1, merged
    assert set(merged[0]["ids"]) == {"631", "33"}, merged[0]["ids"]

    # Distinct same-city clubs must NOT merge (guards against over-merge).
    distinct = [
        {"id": "631", "name": "Maccabi Tel Aviv", "sport": "basketball", "score": 1.0},
        {"id": "632", "name": "Hapoel Tel Aviv",  "sport": "basketball", "score": 0.9},
    ]
    assert len(si._merge_duplicate_teams(distinct)) == 2, "distinct clubs must stay separate"

    # Alias matching (used only for the basketball BSD name-merge fallback)
    # still collapses the "TA" abbreviation onto the canonical form.
    assert bx.norm_name("Maccabi TA") == bx.norm_name("Maccabi Tel Aviv")
    print("OK")

if __name__ == "__main__":
    main()
