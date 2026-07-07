"""clean_athlete_name strips leaked bout-label/round/annotation scaffolding (dump-validation F10)
without mangling real names. Cases are the actual malformed values found across 11 dumps."""
from analysis.names import clean_athlete_name

# malformed value (as it reaches build_matches) → expected clean athlete name
CASES = {
    # polaris_bjj_squads: leaked "Match N (" prefix + unbalanced trailing ')'
    "Match 1 (Jon Blank": "Jon Blank",
    "Match 17 (Roberto Jimenez": "Roberto Jimenez",
    "Heavyweight Super Fight (Sylvia Nastasa": "Sylvia Nastasa",
    "Dan Strauss)": "Dan Strauss",
    "Darragh O'Connail)": "Darragh O'Connail",   # apostrophe survives
    # balanced parenthetical annotations
    "Carrasco (UFC BJJ 3)": "Carrasco",
    "Bia Mesquita (Opening Round)": "Bia Mesquita",
    "Brianna Ste-Marie (Bronze Medal Match)": "Brianna Ste-Marie",  # hyphen survives
    "Jonathan Wilson (Squires)": "Jonathan Wilson",
    "Frank (Rematch)": "Frank",
    "Du Plessis (Middleweight Championship)": "Du Plessis",
    "P.J. Barch (Rematch)": "P.J. Barch",         # periods survive
    # digit disambiguators (with and without annotation)
    "Magomed Ankalaev 2": "Magomed Ankalaev",
    "Johnny Walker 2 (Encore/Replay)": "Johnny Walker",
    # colon sentence-clause leaks
    "Rafael Fiziev: This fight is featured at the beginning of the video": "Rafael Fiziev",
    "Khalil Rountree Jr.: Starts at": "Khalil Rountree Jr.",
    "Ricky Simon: This main event starts at 1": "Ricky Simon",
}

# real names that must pass through untouched (no false positives)
CLEAN = ["Gordon Ryan", "Sean O'Malley", "P.J. Barch", "Khalil Rountree Jr.",
         "Anna Karolina Vieira", "Joshua Squires", "Diogo Reis"]


def test_sanitizes_malformed_names():
    for raw, want in CASES.items():
        assert clean_athlete_name(raw) == want, f"{raw!r} → {clean_athlete_name(raw)!r} != {want!r}"


def test_real_names_untouched():
    for name in CLEAN:
        assert clean_athlete_name(name) == name, name


if __name__ == "__main__":
    test_sanitizes_malformed_names()
    test_real_names_untouched()
    print("name-sanitize self-check OK")
