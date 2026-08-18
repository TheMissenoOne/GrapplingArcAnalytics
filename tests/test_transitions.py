"""Tests for the shared transitions layer (wave 4, docs/rating_v2/)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import network_from_sequences as network_metrics_builder
from analysis.transitions.build_graph import network_from_sequences
from analysis.transitions.normalize import (
    athlete_balanced_category_graph,
    category_graph_raw,
    normalize_athlete_graph,
)

BC, RNC, CG, TRI = "Back Control", "Rear Naked Choke", "Closed Guard", "Triangle Choke"


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def _sequences() -> list[list[dict[str, Any]]]:
    back_to_sub = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    return [
        back_to_sub, back_to_sub, back_to_sub,
        [_e(CG, "guard", "B"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)],
    ]


def test_network_metrics_reexports_the_extracted_builder() -> None:
    # Proves the network_metrics extraction didn't change behaviour: same object.
    assert network_metrics_builder is network_from_sequences


def test_build_graph_matches_original_shape() -> None:
    g = network_from_sequences(_sequences())
    assert {BC, RNC, CG, TRI} <= set(g.nodes)
    assert g.nodes[BC]["occ"] == 4
    assert g[BC][RNC]["weight"] == 4
    assert g[BC][RNC]["ok"] == 4


def test_normalize_athlete_graph_sums_to_one() -> None:
    g = network_from_sequences(_sequences())
    norm = normalize_athlete_graph(g)
    total = sum(d["weight"] for _, _, d in norm.edges(data=True))
    assert abs(total - 1.0) < 1e-9
    assert norm[BC][RNC]["weight"] > 0


def test_athlete_balanced_vs_raw_diverge_when_one_athlete_dominates() -> None:
    # Athlete "whale" fought 10x more than "minnow"; whale always does BC->RNC,
    # minnow always does CG->TRI. Raw pooling lets whale's edge dominate;
    # balanced aggregation gives each athlete equal total weight.
    whale_seq = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    minnow_seq = [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)]
    athlete_sequences = {
        "whale": [whale_seq] * 20,
        "minnow": [minnow_seq] * 2,
    }
    raw = category_graph_raw(athlete_sequences)
    balanced = athlete_balanced_category_graph(athlete_sequences)

    assert raw[BC][RNC]["weight"] == 20
    assert raw[CG][TRI]["weight"] == 2

    # Balanced: each athlete's own graph is normalized to sum-1 before summing, so
    # both athletes' sole edge contributes weight 1.0 regardless of match volume.
    assert abs(balanced[BC][RNC]["weight"] - 1.0) < 1e-9
    assert abs(balanced[CG][TRI]["weight"] - 1.0) < 1e-9


def test_weight_fn_none_is_byte_identical_to_unweighted() -> None:
    g_default = network_from_sequences(_sequences())
    g_explicit_none = network_from_sequences(_sequences(), weight_fn=None)
    assert g_default.nodes(data=True) == g_explicit_none.nodes(data=True)
    assert list(g_default.edges(data=True)) == list(g_explicit_none.edges(data=True))


def test_no_cross_sequence_edge() -> None:
    # Doc 04: "never connect across independent sequenceId boundaries." Fighter A's
    # last action in bout 1 is BC; her first action in bout 2 is CG. If the builder
    # ever treated the two bouts as one continuous stream, A1->A2 would spuriously
    # appear as an edge from bout 1 into bout 2.
    bout_1 = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    bout_2 = [_e(CG, "guard", "A"), _e(TRI, "submission", "A", True)]
    g = network_from_sequences([bout_1, bout_2])
    assert not g.has_edge(BC, CG)
    assert not g.has_edge(RNC, CG)
    # within-bout edges still there
    assert g.has_edge(BC, RNC)
    assert g.has_edge(CG, TRI)


def test_reaction_metadata_preserved_between_own_actions() -> None:
    # A's own consecutive actions (BC -> RNC) with B's "sprawls" event recorded
    # between them: that's the opponent-reaction context doc 04 asks to preserve.
    seq = [_e(BC, "control", "A"), _e("Sprawl", "escape", "B"), _e(RNC, "submission", "A", True)]
    g = network_from_sequences([seq])
    assert g[BC][RNC]["reactions"] == {"Sprawl": 1}
    assert g[BC][RNC]["weight"] == 1  # metadata addition doesn't touch weight
    assert g[BC][RNC]["ok"] == 1


def test_reaction_metadata_absent_when_no_gap() -> None:
    # Same actor, back-to-back with nothing recorded in between -> no "reactions" key
    # at all (absent means "never observed", not "checked, found nothing").
    g = network_from_sequences(_sequences())
    assert "reactions" not in g[BC][RNC]


def test_weight_fn_scales_node_and_edge_counts_by_actor() -> None:
    # actor "A" weighted 0.5, actor "B" weighted 1.0 — confidence-weighting scenario.
    weights = {"A": 0.5, "B": 1.0}
    g = network_from_sequences(_sequences(), weight_fn=weights.get)
    assert g.nodes[BC]["occ"] == 4 * 0.5  # BC only ever appears under actor A
    assert g[BC][RNC]["weight"] == 4 * 0.5
    assert g[BC][RNC]["ok"] == 4 * 0.5
    assert g.nodes[CG]["occ"] == 2 * 1.0  # CG only ever appears under actor B
    assert g[CG][TRI]["weight"] == 1 * 1.0
