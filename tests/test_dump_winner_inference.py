"""build_matches infers a winner from a finishing successful submission when the dump has none."""
from scripts.dump_import import build_matches


def _dump(events):
    # opponent field lets _derive_opponent recover the other side when events are one-sided.
    return [{("Craig Jones", 2025): {"winner": None, "method": None,
                                     "opponent": "Kyle Bame", "events": events}}]


def test_infers_winner_from_successful_submission():
    evs = [
        {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones"},
        {"label": "Heel Hook", "type": "submission", "actor": "Craig Jones", "successful": True},
    ]
    m = build_matches(_dump(evs), clean=False)[0]
    assert m.winner_name == "Craig Jones"
    assert m.win_type == "SUBMISSION"
    assert m.submission == "Heel Hook"


def test_no_winner_for_failed_or_absent_finish():
    evs = [
        {"label": "Toe Hold", "type": "submission", "actor": "Craig Jones", "successful": False},
        {"label": "Half Guard", "type": "guard", "actor": "Kyle Bame"},
    ]
    m = build_matches(_dump(evs), clean=False)[0]
    assert m.winner_name is None  # a defended attempt is not a finish — stay NULL, don't guess


if __name__ == "__main__":
    test_infers_winner_from_successful_submission()
    test_no_winner_for_failed_or_absent_finish()
    print("winner-inference self-check OK")
