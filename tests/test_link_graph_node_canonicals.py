"""Unit test for the label->canonical matcher, pt-BR linkage-gap cases (no DB — pure functions)."""

from scripts.link_graph_node_canonicals import find_matches, load_label_index, resolve_label


def _index():
    return load_label_index()


def test_resolves_pt_br_translation_to_canonical_node_key():
    index = _index()
    assert resolve_label("Guarda Fechada", index) == "closed guard"


def test_resolves_curated_variant_spelling():
    index = _index()
    # "50/50 Guard" en, "Guarda 50-50" pt, "5050" is a listed variant.
    assert resolve_label("5050", index) == "5050 guard"


def test_leaves_garbage_label_unresolved():
    index = _index()
    assert resolve_label("submissionmtjebujc5nch", index) is None


def test_leaves_empty_label_unresolved():
    index = _index()
    assert resolve_label("", index) is None


def test_find_matches_splits_rows_into_matched_and_unmatched_counts():
    index = _index()
    rows = [
        ("g1", "guarda fechada", "Guarda Fechada"),
        ("g2", "submissionmtjebujc5nch", "submissionmtjebujc5nch"),
        ("g3", "guarda fechada", "Guarda Fechada"),  # same node_key, different graph
    ]
    matched, unmatched = find_matches(rows, index)

    assert matched == [
        ("g1", "guarda fechada", "closed guard"),
        ("g3", "guarda fechada", "closed guard"),
    ]
    assert unmatched == {"submissionmtjebujc5nch": 1}
