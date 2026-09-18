"""`analysis/technique_definitions.py` — loader + shape of the curated definitions file,
including the 2026-09-18 external-review fields (`source: "external-review"`, optional
`review_note`). Real file, no fixtures — this file IS the curated content."""
from __future__ import annotations

from analysis.technique_definitions import definition_for, load_definitions

ALLOWED_SOURCES = {"draft", "external-review", "human"}


def test_load_definitions_has_211_entries() -> None:
    defs = load_definitions()
    assert len(defs) == 211


def test_definition_for_known_and_unknown_key() -> None:
    defs = load_definitions()
    sample_key, sample = next(iter(defs.items()))
    assert definition_for(sample_key) == sample
    assert definition_for("__not_a_real_key__") is None


def test_every_entry_has_required_shape_and_allowed_source() -> None:
    defs = load_definitions()
    for key, entry in defs.items():
        assert entry["en"].strip(), f"{key}: empty en"
        assert entry["pt"].strip(), f"{key}: empty pt"
        assert isinstance(entry["reviewed"], bool), f"{key}: reviewed not bool"
        assert entry["source"] in ALLOWED_SOURCES, f"{key}: bad source {entry['source']!r}"
        if "review_note" in entry:
            assert isinstance(entry["review_note"], str) and entry["review_note"].strip(), (
                f"{key}: review_note present but not a non-empty string"
            )


def test_external_review_2026_09_18_counts() -> None:
    """Regression guard for the GPT external review apply — 80 entries revised,
    21 flagged with a review_note (15 factual-correction + 6 ambiguous-term)."""
    defs = load_definitions()
    external_review = [k for k, v in defs.items() if v["source"] == "external-review"]
    with_note = [k for k, v in defs.items() if "review_note" in v]
    assert len(external_review) == 80
    assert len(with_note) == 21
    # every noted entry that's still a draft is one of the ambiguous/needs-context ones,
    # not silently re-drafted
    noted_drafts = [k for k in with_note if defs[k]["source"] == "draft"]
    assert len(noted_drafts) == 6
