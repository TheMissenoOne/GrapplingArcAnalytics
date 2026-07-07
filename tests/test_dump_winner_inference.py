"""build_matches infers a winner from a finishing successful submission when the dump has none."""
from scripts.dump_import import _build_timeline, build_matches


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


def test_timeline_keeps_all_events_with_actor_mapping():
    # the graph drops strikes/resets/unknown-actor; the timeline keeps EVERYTHING, actor→a/b/None.
    raw = [
        {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones", "timestamp": "1:05"},
        {"label": "Jab", "type": "strike", "actor": "Kyle Bame"},
        {"label": "Reset", "type": "reset", "actor": "Referee"},
        {"label": "Stalling", "type": "penalty", "actor": "Craig Jones"},
    ]
    tl = _build_timeline("Craig Jones", "Kyle Bame", raw)
    assert len(tl) == 4  # nothing dropped
    assert [e["actor"] for e in tl] == ["a", "b", None, "a"]
    assert [e["type"] for e in tl] == ["guard", "strike", "reset", "penalty"]
    assert tl[0]["ts"] == 65  # "1:05" → seconds


if __name__ == "__main__":
    test_infers_winner_from_successful_submission()
    test_no_winner_for_failed_or_absent_finish()
    test_timeline_keeps_all_events_with_actor_mapping()
    print("winner-inference + timeline self-check OK")
