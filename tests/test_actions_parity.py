"""Parity tests for the ``actions[]`` migration, Phase 0 (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md).

These tests lock the compiler's behaviour BEFORE any real multi-action edge exists: today
``ChainEdge.actions`` is always a single-element tuple, and the scalar
``action_key``/``action_label``/``action_type``/``actor``/``inferred``/``source_event_index``
properties are the compatibility adapter every existing consumer still reads. Nothing here may
start failing until a later phase deliberately changes the compiler's output shape.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from analysis.chain_compiler import ChainAction, ChainEdge, compile_chain, compile_two_sided
from analysis.rating_v2.node_rating import observations_for_side
from analysis.taxonomy_kind import load_inference_table
from scripts.export_actions_parity_fixtures import action_multiset
from scripts.render_map_prototypes import Aggregate

TABLE = load_inference_table()
GOLDEN_PATH = Path(__file__).resolve().parents[1] / "data" / "rating" / "actions_parity_golden.json"
MOCK_BUNDLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
)


def _ev(label: str, type_: str, actor: str = "a", **kw: Any) -> dict[str, Any]:
    return {"label": label, "type": type_, "actor": actor, **kw}


# ── P1 — canonical multiset invariance ───────────────────────────────────────────────────


@pytest.mark.skipif(
    not MOCK_BUNDLE_PATH.exists(),
    reason="sibling GrapplingArcApp checkout not present (CI runner) — mock bundle unreadable",
)
def test_p1_action_multiset_matches_golden() -> None:
    """The App's mock bundle, compiled today, produces the same
    ``(action_key, actor, inferred) -> count`` multiset the golden fixture pins
    (``scripts/export_actions_parity_fixtures.py``). The tally walks ``edge.actions`` (never
    ``edge.action_key``), so this stays a real proof once an edge carries more than one."""
    bundle = json.loads(MOCK_BUNDLE_PATH.read_text(encoding="utf-8"))
    tally = action_multiset(bundle)
    got = sorted(
        [{"action_key": k, "actor": a, "inferred": i, "count": c}
         for (k, a, i), c in tally.items()],
        key=lambda r: (r["action_key"], r["actor"], r["inferred"]),
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert got == golden["multiset"]


@pytest.mark.skipif(
    not MOCK_BUNDLE_PATH.exists(),
    reason="sibling GrapplingArcApp checkout not present (CI runner) — mock bundle unreadable",
)
def test_p1_observed_actions_are_the_invariant_the_inference_rule_may_never_move() -> None:
    """The half of P1 that is a real invariant, split out so it cannot be regenerated away.
    OBSERVED actions come one-for-one from the log: 22 occurrences on the mock bundle, before
    Fase 0 and after every phase since. A rule change may add or re-key INFERRED actions (Fase 2
    took the mock bundle's inferred count from 2 to 5 by naming three sweeps that the type-only
    table read as `guard exit`); it may never touch this number, and a regenerated golden that
    quietly did would fail here."""
    bundle = json.loads(MOCK_BUNDLE_PATH.read_text(encoding="utf-8"))
    observed = sum(c for (_k, _a, inferred), c in action_multiset(bundle).items() if not inferred)
    assert observed == 22


# ── P2 — rating parity (regression guard, must NEVER change across any actions[] phase) ────


def test_p2_observations_for_side_is_byte_identical_pinned() -> None:
    """``observations_for_side`` reads the bout's RAW ``sequence`` events directly — it never
    touches ``chain_compiler``/``ChainEdge``/``actions[]`` at all, which is exactly why it is
    unaffected by this migration (see the module's own docstring and root CLAUDE.md's
    'Cross-Module Contracts' row on Rating V2). This golden pin must stay true through EVERY
    phase of the actions/states migration — if it ever needs updating, something reached into
    this function's input path, which the migration plan says must never happen."""
    seq = [
        {"actor_id": "ath1", "label": "Closed Guard", "type": "guard", "successful": None},
        {"actor_id": "ath1", "label": "Armbar", "type": "submission", "successful": True},
        {"actor_id": "ath1", "label": "Triangle Choke", "type": "submission", "successful": False},
        {"actor_id": "ath2", "label": "Guard Pass", "type": "pass", "successful": True},
    ]
    obs = observations_for_side(seq, "ath1", None)
    assert obs == (
        _node_obs("armbar", 1.0, 0.25),
        _node_obs("triangle choke", 0.0, 0.25),
    )


def _node_obs(node_key: str, score: float, weight: float) -> Any:
    from analysis.rating_v2.node_rating import NodeObservation
    return NodeObservation(node_key=node_key, score=score, weight=weight)


# ── P3 — index independence ──────────────────────────────────────────────────────────────

_OBS_1 = ChainAction(key="armbar", label="Armbar", type="submission", actor="you",
                      inferred=False, source_event_index=0)
_INFERRED_MID = ChainAction(key="guard recovery", label="Guard Recovery", type="transition",
                             actor="you", inferred=True, source_event_index=None)
_OBS_2 = ChainAction(key="guard pass", label="Guard Pass", type="pass", actor="you",
                      inferred=False, source_event_index=2)


def _edge(actions: tuple[ChainAction, ...]) -> ChainEdge:
    return ChainEdge(source_key="mount", target_key="side control", actions=actions,
                      terminal=False)


def test_p3_full_action_multiset_reader_is_order_independent() -> None:
    """A consumer that reads the WHOLE ``actions`` tuple (the shape every future-phase
    consumer must move to, per the contract doc) sees the same multiset regardless of where
    the inferred action sits among the two observed ones."""
    canonical = _edge((_OBS_1, _INFERRED_MID, _OBS_2))       # inferred in the MIDDLE
    scrambled = _edge((_INFERRED_MID, _OBS_2, _OBS_1))       # same 3 actions, different order

    def multiset(edge: ChainEdge) -> Counter[tuple[str, str, bool]]:
        return Counter((a.key, a.actor or "", a.inferred) for a in edge.actions)

    assert multiset(canonical) == multiset(scrambled)
    # and both endpoints are untouched by the reordering — the relation, not the path, still
    # identifies (source, target)
    canonical_ends = (canonical.source_key, canonical.target_key)
    scrambled_ends = (scrambled.source_key, scrambled.target_key)
    assert canonical_ends == scrambled_ends


def test_p3_observations_for_side_never_sees_actions_or_position() -> None:
    """Restates P2's point as an index-independence claim: this consumer takes the bout's raw
    event list, never a ``ChainEdge``, so there is no ``actions[]`` position for it to depend
    on in the first place — it is structurally exempt, not merely observed to pass."""
    import inspect

    from analysis.rating_v2 import node_rating
    src = inspect.getsource(node_rating.observations_for_side)
    assert "actions" not in src and "ChainEdge" not in src


def test_p3_variant_identity_is_the_observed_subsequence_not_the_full_sequence() -> None:
    """SUPERSEDES the Phase-1 "whole sequence" key (§13.2/§13.3 of the contract doc, decision
    2026-09-01): a variant's identity is the OBSERVED subsequence — the inferred filler is
    annotation of the gap, never identity. Two edges whose OBSERVED actions agree, in order,
    dedupe into ONE variant even when the inferred action sits at a different position between
    them (`_INFERRED_MID` first vs. spliced between the two observed actions); an edge whose
    observed actions actually differ still lands in a different bucket, for the right reason."""
    same_observed_a = _edge((_OBS_1, _INFERRED_MID, _OBS_2))
    same_observed_b = _edge((_INFERRED_MID, _OBS_1, _OBS_2))  # inferred moved, same 2 observed

    agg = Aggregate()
    agg.add_edge(same_observed_a)
    agg.add_edge(same_observed_b)
    assert len(agg.edges) == 1  # observed subsequence (armbar, guard pass) is identical
    (row,) = agg.edges.values()
    assert row["count"] == 2

    different_observed = _edge((_OBS_2, _INFERRED_MID, _OBS_1))  # observed order reversed
    agg2 = Aggregate()
    agg2.add_edge(same_observed_a)
    agg2.add_edge(different_observed)
    assert len(agg2.edges) == 2  # different observed sequences -> different buckets, correctly
    key_a, key_b = sorted(agg2.edges.keys())
    assert key_a[2] == (_OBS_1.key, _OBS_2.key)
    assert key_b[2] == (_OBS_2.key, _OBS_1.key)


# ── P4 — compatibility adapter ───────────────────────────────────────────────────────────


def test_p4_legacy_scalar_fields_round_trip_through_the_actions_adapter() -> None:
    """A record built the way ``ChainEdge`` used to be constructed directly — one scalar
    action (``action_key``/``action_label``/``action_type``/``actor``/``inferred``/
    ``source_event_index``) — reads back through the SAME six properties with no loss, and
    ``actions`` holds exactly that one action. This is the whole of the compatibility
    adapter: every existing caller reads the scalar view and gets exactly what it read
    before ``actions[]`` existed."""
    legacy: dict[str, Any] = {
        "action_key": "armbar", "action_label": "Armbar", "action_type": "submission",
        "actor": "you", "inferred": False, "source_event_index": 7,
    }
    action = ChainAction(
        key=legacy["action_key"], label=legacy["action_label"], type=legacy["action_type"],
        actor=legacy["actor"], inferred=legacy["inferred"],
        source_event_index=legacy["source_event_index"],
    )
    edge = ChainEdge(source_key="mount", target_key="finish", actions=(action,), terminal=True)

    assert len(edge.actions) == 1
    assert edge.action_key == legacy["action_key"]
    assert edge.action_label == legacy["action_label"]
    assert edge.action_type == legacy["action_type"]
    assert edge.actor == legacy["actor"]
    assert edge.inferred == legacy["inferred"]
    assert edge.source_event_index == legacy["source_event_index"]

    # downstream: aggregation off a single-action edge is unaffected by the adapter existing.
    agg = Aggregate()
    agg.add_edge(edge)
    (row,) = agg.edges.values()
    assert row["action_key"] == legacy["action_key"]
    assert row["actor"] == legacy["actor"]
    assert row["inferred"] == legacy["inferred"]


def test_p4_compile_chain_output_is_still_single_action_edges() -> None:
    """The compiler itself is the other half of the adapter claim: every edge it emits today
    carries exactly one action — Phase 0 is additive, not a behaviour change."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)
    assert all(len(e.actions) == 1 for e in chain.edges)


# ── §13 — family / variant / unresolved occurrence (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md) ──


def test_p5_wholly_inferred_occurrence_propagates_family_support_only() -> None:
    """P5 (§13.4), on the PUBLIC aggregator (`analysis.corpus_paths.PathAggregate`): an
    occurrence with no observed action contributes 0 to any variant's own `count`, 0 rating
    observations, and +1 to the family's `support` — never a phantom trunk of its own."""
    from analysis.corpus_paths import PathAggregate, _metrics_by_path

    concrete = _edge((_OBS_1,))    # mount -> side control, 1 observed action -> a real variant
    ghost = _edge((_INFERRED_MID,))  # SAME relation, wholly inferred -> never its own variant

    agg = PathAggregate()
    agg.add_edge(concrete, "a")
    agg.add_edge(ghost, "a")
    agg.finalize()

    assert len(agg.edges) == 1                     # the ghost created no second variant
    (row,) = agg.edges.values()
    assert row["count"] == 1                       # 0 contributed to the variant's own count
    assert agg.unresolved[("mount", "side control", "a")]["count"] == 1

    metrics = _metrics_by_path(agg, lambda _k: 1500.0)
    (m,) = metrics.values()
    assert m.support == 2                          # +1 family support from the ghost
    assert m.observed == 1                         # rating (path_metrics) never sees the ghost


def test_p5_private_aggregate_mirrors_the_same_routing() -> None:
    """Same invariant, on the PRIVATE aggregator (`scripts.render_map_prototypes.Aggregate`) —
    the App's `mapAggregate.ts` mirrors this one."""
    concrete = _edge((_OBS_1,))
    ghost = _edge((_INFERRED_MID,))

    agg = Aggregate()
    agg.add_edge(concrete)
    agg.add_edge(ghost)
    agg.finalize()

    assert len(agg.edges) == 1
    (row,) = agg.edges.values()
    assert row["count"] == 1
    # both fixtures share this actor — the family key below relies on it
    assert _OBS_1.actor == "you"
    assert agg.unresolved[("mount", "side control", "you")]["count"] == 1


def test_p5_a_family_with_no_concrete_variant_gets_one_disguised_placeholder() -> None:
    """§13.3's other branch: when a relation has NO concrete variant at all, every wholly-
    inferred occurrence folds into ONE placeholder edge (still drawable) instead of `unresolved`
    — the placeholder IS `unresolved`, disguised, not a resolved technique."""
    sweep_guess = ChainAction(key="sweep", label="Sweep", type="sweep", actor="you",
                               inferred=True, source_event_index=None)
    reversal_guess = ChainAction(key="reversal", label="Reversal", type="transition", actor="you",
                                  inferred=True, source_event_index=None)

    from analysis.corpus_paths import PathAggregate

    agg = PathAggregate()
    agg.add_edge(_edge((sweep_guess,)), "a")
    agg.add_edge(_edge((sweep_guess,)), "a")
    agg.add_edge(_edge((reversal_guess,)), "a")
    agg.finalize()

    assert len(agg.edges) == 1               # one disguised placeholder, not two ghost variants
    assert agg.unresolved == {}              # no concrete variant exists to attach `unresolved` to
    (row,) = agg.edges.values()
    assert row["count"] == 3
    assert row["unresolved_guesses"] == {"reversal": 1, "sweep": 2}
    assert row["actions"] == ("sweep",)      # majority guess is the representative label


def test_provenance_matches_inferred_at_both_compiler_construction_sites() -> None:
    """§13.5: additive `provenance`, `inferred=True` iff `provenance == 'inferred'`. Checked at
    the two places `chain_compiler` builds a `ChainAction` (the spliced-in generic and the
    observed action off a real event) rather than validated at construction, so a caller
    building fixtures directly (this file's own `_OBS_1`/`_INFERRED_MID`) is unaffected."""
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Side Control", "control"),  # empty buffer -> exactly one inferred action
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)
    assert chain.edges  # sanity: the fixture actually produced something to check
    for edge in chain.edges:
        for action in edge.actions:
            assert action.inferred == (action.provenance == "inferred"), action


# ── Fase 1b — anchor coverage invariant (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md) ─────────────


def test_no_empty_endpoint_edges_and_no_generic_out_degrees_the_real_graph() -> None:
    """The defect Fase 1b closes: before it, a chain end the D2 table couldn't declaratively
    resolve emitted an edge with ``source_key``/``target_key`` == ``""`` — 269 of them on the
    owner's 281-bout corpus, the SECOND-highest-degree node in the whole graph. Fase 1b's
    orientation-by-role fallback (``resolve_anchor_by_role``) guarantees every chain end
    resolves to a real generic anchor, so this phantom node cannot exist any more, and no
    generic anchor should out-rank the graph's largest REAL node (the owner's own acceptance
    criterion). Skips when the owner's private corpus dump isn't present (never committed —
    LGPD, same convention as the App-sibling-repo tests)."""
    from scripts.shadow_chain_compiler import DEFAULT_EXPORT, _side_of

    if not DEFAULT_EXPORT.is_file():
        pytest.skip("corpus privado do dono não está presente (_analytics_export.json)")

    matches = json.loads(DEFAULT_EXPORT.read_text(encoding="utf-8"))
    table = load_inference_table()
    generic_keys = set(table["generic_states"])
    degree: Counter[str] = Counter()
    empty_endpoint_edges = 0
    observed_actions = 0
    inferred_actions = 0

    for m in matches:
        seq = m.get("sequence") or []
        if not seq:
            continue
        result = compile_two_sided(seq, _side_of(m), inference_table=table)
        for side in ("a", "b"):
            for e in result[side].edges:
                observed_actions += sum(1 for a in e.actions if not a.inferred)
                inferred_actions += sum(1 for a in e.actions if a.inferred)
                if e.source_key == "" or e.target_key == "":
                    empty_endpoint_edges += 1
                degree[e.source_key] += 1
                degree[e.target_key] += 1

    assert empty_endpoint_edges == 0
    # OBSERVED is the invariant: one action per logged action event, unchanged by Fase 0/1/1b/2.
    # Moved ONCE, by N0's authority swap (docs/taxonomy/04_ONTOLOGIA_CANONICA.md): 1390 -> 1548,
    # the net of five reclassified labels on this dump — `control/Back Take` (128) and
    # `control/Escape to Turtle` (47) and `guard/Jump Guard` (2) became actions, `control/Body
    # Lock` (3) and `control/Clinch Knees` (1) became states. The exact set is pinned in
    # `data/taxonomy/audit_baseline.json` under `reclassified`; any OTHER movement is the
    # regression this number exists to catch.
    assert observed_actions == 1548
    # INFERRED is the rule's own output and moves with it. 399 before Fase 2; 433 after, and the
    # +34 are all inversions the endpoints prove and no observed action explains (28 appended,
    # 4 at the head, 2 spliced BETWEEN observed actions). N0 takes it to 321: 128 `control/Back
    # Take` and 47 `control/Escape to Turtle` events stopped being STATES, so the generic bridges
    # the compiler had to invent around them are no longer needed. Change the rule and change this
    # number deliberately — never to make a red test green.
    assert inferred_actions == 321
    max_real_degree = max(
        (d for key, d in degree.items() if key not in generic_keys and key != ""), default=0
    )
    for key in generic_keys & set(degree):
        assert degree[key] <= max_real_degree, f"generic {key!r} out-ranks the largest real node"
