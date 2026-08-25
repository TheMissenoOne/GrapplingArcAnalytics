"""ATHLETE_ALIASES entries used by rating_layer / athlete_key to match roster spellings
against DB spellings. One case per merge decision recorded in analysis/names.py."""
from analysis.names import athlete_key

# (db/alt spelling, roster/canonical spelling) -> must resolve to the same key
PAIRS = [
    # DB carries "Jocelyn Molina"; the ADCC 2026 women's manifest spells "Joslyn Molina" --
    # same human, same single bout (WNO 31 2025). Without this, rating_layer's roster join
    # silently reports her as unrated despite having an eligible bout in athlete_rating_states_v2.
    ("Jocelyn Molina", "Joslyn Molina"),
]


def test_athlete_key_resolves_known_alias_pairs() -> None:
    for alt, canonical in PAIRS:
        assert athlete_key(alt) == athlete_key(canonical), (alt, canonical)


if __name__ == "__main__":
    test_athlete_key_resolves_known_alias_pairs()
    print("athlete-aliases self-check OK")
