"""Markov action weights: loader, fallbacks, the distribution invariant, and the fixture.

The property that matters most here is the IDENTITY: with the artefact absent, or with a
block whose weights are all equal, ``replay_matches`` must produce byte-identical numbers to
the ones it produced before this feature existed. Everything else is a knob; that one is the
guarantee that nothing moves until someone decides it should.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from analysis.athlete_elo import replay_matches
from analysis.lamas_chain import STATES, lamas_state
from analysis.markov_weights import (
    MarkovWeightsError,
    block_for_family,
    load_markov_weights,
    relative_shares,
    unmapped_weight,
    weight_of,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "rating" / "markov_weights_golden.json"
APP_GOLDEN = (
    ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__"
    / "markovWeightsGolden.json"
)


def _match(sequence: list[dict[str, Any]], won: bool = True,
           win_type: str = "POINTS") -> SimpleNamespace:
    return SimpleNamespace(id="m1", sequence=sequence, won=won, win_type=win_type, date=None)


# ── loader ──────────────────────────────────────────────────────────────────────
def test_absent_artifact_is_none_not_an_error(tmp_path: Path) -> None:
    assert load_markov_weights(str(tmp_path / "nope.json")) is None


def test_present_but_broken_artifact_raises(tmp_path: Path) -> None:
    """A file that exists and is wrong is a defect, not an absence. Degrading past it would
    hide the breakage behind numbers that look unchanged."""
    bad = tmp_path / "bad.json"
    bad.write_text('{"global": {}}', encoding="utf-8")
    with pytest.raises(MarkovWeightsError):
        load_markov_weights(str(bad))

    negative = tmp_path / "neg.json"
    negative.write_text('{"global": {"SUB": -1}}', encoding="utf-8")
    with pytest.raises(MarkovWeightsError):
        load_markov_weights(str(negative))


# ── family selection ────────────────────────────────────────────────────────────
def test_family_block_selection_and_fallback() -> None:
    doc = {"global": {"SUB": 1.0}, "adcc": {"SUB": 9.0}}
    assert block_for_family("adcc", doc) == {"SUB": 9.0}
    # ibjjf did not clear the gates → absent → global, never a neighbouring family's table.
    assert block_for_family("ibjjf", doc) == {"SUB": 1.0}
    assert block_for_family("cji", doc) == {"SUB": 1.0}
    assert block_for_family("unknown", doc) == {"SUB": 1.0}
    assert block_for_family(None, doc) == {"SUB": 1.0}
    assert block_for_family("adcc", None) is None


# ── rule 1: unmapped is the mean, never zero ────────────────────────────────────
def test_unmapped_takes_the_block_mean() -> None:
    block = {"SUB": 4.0, "GPS": 2.0}
    assert unmapped_weight(block) == 3.0
    assert weight_of(None, block) == 3.0
    assert weight_of("TKD", block) == 3.0      # code the block does not carry
    assert weight_of("SUB", block) == 4.0


# ── rule 2 / the identity ───────────────────────────────────────────────────────
def test_flat_block_and_absent_block_are_both_uniform() -> None:
    codes = ["SUB", None, "GPSA", "TKD"]
    flat = dict.fromkeys(STATES, 7.0)
    assert relative_shares(codes, flat) == pytest.approx([0.25] * 4)
    assert relative_shares(codes, None) == pytest.approx([0.25] * 4)
    assert relative_shares(codes, {}) == pytest.approx([0.25] * 4)
    # An all-zero block carries no information either; uniform, not a division by zero.
    assert relative_shares(codes, dict.fromkeys(STATES, 0.0)) == pytest.approx([0.25] * 4)
    assert relative_shares([], flat) == []


def test_shares_sum_to_one() -> None:
    block = {c: float(i + 1) for i, c in enumerate(STATES)}
    shares = relative_shares(["SUB", "TKDA", None, "GPS"], block)
    assert sum(shares) == pytest.approx(1.0)


# ── the replay invariant ────────────────────────────────────────────────────────
_SEQ: list[dict[str, Any]] = [
    {"label": "Single Leg", "type": "takedown", "actor": "you", "successful": True},
    {"label": "Leg Drag", "type": "pass", "actor": "you", "successful": True},
    {"label": "Rear Naked Choke", "type": "submission", "actor": "you", "successful": True},
    {"label": "Half Guard", "type": "guard", "actor": "you"},
]


def _replay(weights: dict[str, float] | None) -> dict[str, float]:
    graph, _ = replay_matches(
        "X", [_match(_SEQ)], rank_target=1400.0, opp_elos=[1000.0], belt="black",
        action_weights=None if weights is None else [weights],
    )
    return {k: float(n.computed_elo or 0.0) for k, n in graph.nodes.items()}


def test_flat_weights_reproduce_the_unweighted_replay_exactly() -> None:
    """THE guarantee. Equal weights ⇒ identical numbers, node for node."""
    baseline = _replay(None)
    flat = _replay(dict.fromkeys(STATES, 3.5))
    assert flat.keys() == baseline.keys()
    for key, value in baseline.items():
        assert flat[key] == pytest.approx(value, abs=1e-9)


def test_weighting_moves_the_split_but_not_the_graph_mean() -> None:
    """The converge-clamp predicts the mean from ``len(unique)``; the weighting must not
    contradict it. Same total movement, different distribution."""
    baseline = _replay(None)
    skewed = dict.fromkeys(STATES, 1.0)
    skewed["SUB"] = 40.0
    weighted = _replay(skewed)

    assert sum(weighted.values()) == pytest.approx(sum(baseline.values()), abs=1e-6)
    # The finish took the lion's share; the unmapped guard posture kept only a sliver.
    rnc = next(k for k in weighted if "rear naked" in k)
    guard = next(k for k in weighted if "half guard" in k)
    assert weighted[rnc] > baseline[rnc]
    assert weighted[guard] < baseline[guard]


def test_headline_athlete_elo_is_invariant_across_a_whole_career() -> None:
    """**The blast-radius claim, asserted rather than argued.**

    Because the shares have mean exactly 1, every match adds ``delta * scale * len(unique)``
    to the sum of node ELOs whatever the weights are — so the graph MEAN after each match is
    unchanged, and with it the new-node seed, the expected score, the gap-driven K and the
    converge-clamp. The whole trajectory is therefore identical: ``athlete.elo`` and
    ``athlete.elo_series`` do not move. Only the split ACROSS nodes does.

    This is what bounds a full replay: the leaderboard, the ELO series on every dossier and
    every athlete-level number on the public site are untouched; per-node ratings, the edge
    ELOs derived from them, and everything downstream of those (``analysis/deviance`` →
    ``export/tech_library``'s ``eloPercentile``, ``athlete_systems``) are not.
    """
    seqs: list[list[dict[str, Any]]] = [
        [{"label": "Single Leg", "type": "takedown", "actor": "you", "successful": True},
         {"label": "Leg Drag", "type": "pass", "actor": "you", "successful": True}],
        [{"label": "Closed Guard", "type": "guard", "actor": "you"},
         {"label": "Butterfly Sweep", "type": "sweep", "actor": "you", "successful": True},
         {"label": "Rear Naked Choke", "type": "submission", "actor": "you",
          "successful": True}],
        [{"label": "Leg Drag", "type": "pass", "actor": "you", "successful": True},
         {"label": "Back Control", "type": "control", "actor": "you", "successful": True},
         {"label": "Half Guard", "type": "guard", "actor": "you"}],
    ]
    matches = [_match(s, won=(i != 1), win_type="POINTS") for i, s in enumerate(seqs)]
    opp = [1000.0, 1200.0, 1500.0]
    real = block_for_family("global", load_markov_weights())

    plain, snaps_plain = replay_matches("X", matches, 1400.0, opp, belt="black")
    weighted, snaps_weighted = replay_matches(
        "X", matches, 1400.0, opp, belt="black",
        action_weights=[real] * len(matches),
    )

    assert snaps_weighted == pytest.approx(snaps_plain, abs=1e-9)
    assert weighted.user_elo == pytest.approx(plain.user_elo, abs=1e-9)
    # ...and the per-node split really did move, so the assertion above is not vacuous.
    if real is not None:
        per_node_plain = {k: n.computed_elo for k, n in plain.nodes.items()}
        per_node_weighted = {k: n.computed_elo for k, n in weighted.nodes.items()}
        assert per_node_weighted != per_node_plain


def test_repeated_node_is_not_a_heavier_node() -> None:
    """``participating`` carries one entry per event; a technique used twice must not take
    twice the share, or the equal-weights identity above would not hold."""
    seq: list[dict[str, Any]] = [
        {"label": "Leg Drag", "type": "pass", "actor": "you", "successful": True},
        {"label": "Leg Drag", "type": "pass", "actor": "you", "successful": True},
        {"label": "Rear Naked Choke", "type": "submission", "actor": "you",
         "successful": True},
    ]
    graph, _ = replay_matches(
        "X", [_match(seq)], rank_target=1400.0, opp_elos=[1000.0], belt="black",
        action_weights=[dict.fromkeys(STATES, 2.0)],
    )
    elos = {k: float(n.computed_elo or 0.0) for k, n in graph.nodes.items()}
    assert len(elos) == 2
    assert len(set(round(v, 9) for v in elos.values())) == 1


# ── the cross-repo fixture ──────────────────────────────────────────────────────
def test_golden_fixture_matches_this_implementation() -> None:
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for case in doc["cases"]:
        codes = [lamas_state(e) for e in case["events"]]
        assert codes == case["expected_codes"], case["name"]
        shares = relative_shares(codes, case["block"])
        assert shares == pytest.approx(case["expected_shares"], abs=1e-12), case["name"]


def test_both_repos_carry_the_same_fixture_bytes() -> None:
    """A contract that lives on one side only is not a contract. Regenerate with
    ``uv run python -m scripts.export_markov_weight_fixtures``."""
    if not APP_GOLDEN.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    assert APP_GOLDEN.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")
