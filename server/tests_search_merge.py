# server/tests_search_merge.py
import search_index as si

def main():
    rows = [
        {"id": "631", "name": "Maccabi Tel Aviv", "sport": "basketball", "score": 1.0},
        {"id": "33",  "name": "Maccabi TA",       "sport": "basketball", "score": 0.9},
    ]
    merged = si._merge_duplicate_teams(rows)
    assert len(merged) == 1, merged
    assert set(merged[0]["ids"]) == {"631", "33"}, merged[0]["ids"]
    print("OK")

if __name__ == "__main__":
    main()
