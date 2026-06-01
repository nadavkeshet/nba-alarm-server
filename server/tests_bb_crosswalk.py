# server/tests_bb_crosswalk.py
import bb_team_crosswalk as x

def main():
    assert x.bsd_for_365("631") == "33", x.bsd_for_365("631")
    assert x.s365_for_bsd("33") == "631", x.s365_for_bsd("33")
    assert x.norm_name("Maccabi TA") == x.norm_name("Maccabi Tel Aviv")
    assert x.norm_name("Maccabi TA") == "maccabi tel aviv"
    assert x.bsd_for_365("999999") is None
    print("OK")

if __name__ == "__main__":
    main()
