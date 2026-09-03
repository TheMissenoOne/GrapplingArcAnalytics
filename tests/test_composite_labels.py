"""`analysis.composite_labels.expand_composite` — N2 (docs/taxonomy/04_ONTOLOGIA_CANONICA.md).

Fixture-only: no DB, no network. Proves the three curated shapes decompose correctly, that a
non-composite label passes through untouched, and that the chain compiler still only ever sees
atomic events after expansion (the "160 phantom actions" regression this module exists to not
repeat — state inference belongs in the ingestion, never in `chain_compiler`).
"""
from __future__ import annotations

import json

from analysis.composite_labels import expand_composite


def test_non_composite_label_passes_through_unchanged() -> None:
    event = {"label": "Armbar", "type": "submission", "actor": "a", "ts": 12}
    assert expand_composite(event) == [event]


def test_action_to_concrete_state_splits_into_two_events() -> None:
    event = {"label": "Guard Pass to Mount", "type": "pass", "actor": "a",
              "ts": 90, "successful": True}
    out = expand_composite(event)
    assert len(out) == 2
    assert out[0]["label"] == "Guard Pass"
    assert out[1]["label"] == "Mount"
    for ev in out:
        assert ev["actor"] == "a"
        assert ev["ts"] == 90
        assert ev["successful"] is True
        assert ev["source_label"] == "Guard Pass to Mount"


def test_action_to_generic_orientation_drops_the_second_event() -> None:
    """'Escape to Standing' -> neutral: the target is vague, so only the action survives —
    the existing exit-orientation anchor inference (chain_compiler) already supplies the
    generic landing spot; splicing a literal 'neutral' node would just reinvent it."""
    event = {"label": "Escape to Standing", "type": "escape", "actor": "b"}
    out = expand_composite(event)
    assert len(out) == 1
    assert out[0]["label"] == "Escape"
    assert out[0]["source_label"] == "Escape to Standing"


def test_state_then_action_order_is_state_first() -> None:
    event = {"label": "Leg Entanglement / Heel Hook Entry", "type": "control", "actor": "a"}
    out = expand_composite(event)
    assert [e["label"] for e in out] == ["Leg Entanglement", "Heel Hook Entry"]


def test_perspective_keeps_the_original_label() -> None:
    """N2 invariant: perspective is metadata, never a second name for the state
    (04_ONTOLOGIA_CANONICA.md S2). 'Top Half Guard' stays 'Top Half Guard'."""
    event = {"label": "Top Half Guard", "type": "guard", "actor": "a", "successful": True}
    out = expand_composite(event)
    assert len(out) == 1
    assert out[0]["label"] == "Top Half Guard"
    assert out[0]["perspective"] == {"actor": "top"}
    assert out[0]["successful"] is True


def test_split_events_carry_every_field_except_label_and_type() -> None:
    event = {"label": "Guard Pass to Mount", "type": "pass", "actor": "athlete-1",
              "ts": 5, "successful": False, "points": 3}
    out = expand_composite(event)
    for ev in out:
        assert ev["actor"] == "athlete-1"
        assert ev["ts"] == 5
        assert ev["successful"] is False
        assert ev["points"] == 3


def test_chain_compiler_never_sees_a_composite_label_from_the_real_table() -> None:
    """Every curated label expands to atomic (non-composite) sub-labels — a compiler reading
    post-expansion output never has to re-derive a state, matching the invariant that state
    inference lives only in the ingestion (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md)."""
    from analysis.composite_labels import TABLE_PATH

    raw = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    composite_keys = {" to ", " / "}
    for label, spec in raw.items():
        if label.startswith("_"):
            continue
        for sub in expand_composite({"label": label, "type": "control", "actor": "a"}):
            assert not any(sep in sub["label"].lower() for sep in composite_keys), (
                label, spec, sub)
