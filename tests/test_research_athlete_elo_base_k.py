"""The identity that licenses the base-K sweep, on synthetic data with no DB.

``scripts/research/athlete_elo_base_k.py`` sweeps ``athlete_elo``'s K SCHEDULE LEVEL
through the ``competitive_mult`` parameter and reports it as a base K. That relabelling is
only honest if scaling ``competitive_mult`` is arithmetically identical to scaling the
``_base_k`` ladder itself -- otherwise the study measures one knob and names another.
``test_scaling_the_base_k_ladder_equals_scaling_competitive_mult`` asserts it against the
REAL ``replay_matches``, converge-clamp and all, not against ``k_factor`` in isolation.

The rest pins the two ways the sweep could silently measure nothing: a grid that does not
contain the shipped value, and an arm whose knob never reaches the engine.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from analysis import athlete_elo
from analysis.poc.e0_rating_eval import Bout, stream_scores
from scripts.research.athlete_elo_base_k import (
    LEVEL_CASUAL,
    LEVEL_GRID,
    LEVEL_SHIPPED,
    RankTargetOnly,
    degenerate_arms,
    engine_for_level,
    level_name,
)

SEQUENCE: tuple[dict[str, Any], ...] = (
    {"label": "Closed Guard", "type": "position", "actor": "you"},
    {"label": "Armbar", "type": "submission", "actor": "you"},
    {"label": "Takedown", "type": "takedown", "actor": "opponent"},
)


def _matches(n: int) -> list[Any]:
    """``n`` duck-typed matches, alternating wins, all inside the n<=10 base-K branch."""
    return [
        SimpleNamespace(sequence=list(SEQUENCE), won=(i % 2 == 0), win_type="POINTS",
                        date=None, id=f"m{i}")
        for i in range(n)
    ]


@pytest.mark.parametrize("c", [0.125, 0.5, 2.0, 8.0])
def test_scaling_the_base_k_ladder_equals_scaling_competitive_mult(
    monkeypatch: pytest.MonkeyPatch, c: float,
) -> None:
    """K = base(n) x gap x competitive_mult x decay, and nothing else reads ``_base_k``.

    So a replay whose LADDER is scaled by ``c`` must be bit-identical to one whose
    ``competitive_mult`` is scaled by ``c``. This is the whole licence for sweeping the
    level through ``competitive_mult`` and reporting it as a base K; if a future change
    makes the ladder enter anywhere non-multiplicatively, this fails and the runner's
    tables become mislabelled rather than quietly wrong.
    """
    matches = _matches(8)
    opp = [1000.0] * len(matches)

    # Arm A: the ladder itself scaled (n<=10, so K_BASE_EARLY is the whole ladder here).
    monkeypatch.setattr(athlete_elo, "K_BASE_EARLY", athlete_elo.K_BASE_EARLY * c)
    graph_a, snaps_a = athlete_elo.replay_matches(
        "x", matches, 1200.0, opp, belt="black", competitive_mult=2.5)
    monkeypatch.undo()

    # Arm B: the multiplier scaled, ladder untouched.
    graph_b, snaps_b = athlete_elo.replay_matches(
        "x", matches, 1200.0, opp, belt="black", competitive_mult=2.5 * c)

    assert snaps_a == snaps_b
    assert graph_a.user_elo == graph_b.user_elo
    assert {k: n.computed_elo for k, n in graph_a.nodes.items()} == {
        k: n.computed_elo for k, n in graph_b.nodes.items()}


def test_scaling_is_not_a_no_op() -> None:
    """Guard on the guard above: two different levels must actually diverge, or the
    equivalence test would pass trivially on an engine that ignores K entirely."""
    matches, opp = _matches(8), [1000.0] * 8
    _, lo = athlete_elo.replay_matches("x", matches, 1200.0, opp, competitive_mult=0.3)
    _, hi = athlete_elo.replay_matches("x", matches, 1200.0, opp, competitive_mult=20.0)
    assert lo != hi
    assert hi[-1] > lo[-1]  # a bigger K climbs further toward the 1200 target


def test_level_grid_is_geometric_and_brackets_both_shipped_paths() -> None:
    """A grid whose optimum can only sit at an edge has not measured an optimum (the
    durable lesson of the 2026-09-14 plain-Elo sweep), and a grid that misses the shipped
    value has no reference to test against."""
    assert LEVEL_SHIPPED == athlete_elo.K_BASE_EARLY * athlete_elo.COMPETITIVE_K_MULT
    assert LEVEL_SHIPPED in LEVEL_GRID
    assert LEVEL_CASUAL == athlete_elo.K_BASE_EARLY
    assert LEVEL_GRID[0] < LEVEL_CASUAL < LEVEL_GRID[-1]
    ratios = [b / a for a, b in zip(LEVEL_GRID, LEVEL_GRID[1:], strict=False)]
    assert all(abs(r - 2 ** 0.5) < 0.01 for r in ratios), ratios


def test_the_swept_knob_reaches_the_engine_and_the_void_detector_fires() -> None:
    """Two failure modes that would make the whole run meaningless, both detected.

    With sequences, two extreme levels must produce different prediction vectors (the knob
    is live). Without sequences the engine never builds a graph, every level collapses onto
    the same predictions, and the runner must VOID that corpus rather than print a table of
    identical rows -- which is exactly what the scouting records do.
    """
    targets = {"a1": 1200.0, "b1": 900.0}
    seq = tuple({**e, "actor_id": "a1"} for e in SEQUENCE)
    live = [Bout("a1", "b1", 1.0, y, "POINTS", "F", "E1", sequence=seq)
            for y in (2019, 2020, 2021, 2022)]
    lo, hi = engine_for_level(12.5, targets), engine_for_level(800.0, targets)
    rows = stream_scores(live, [lo, hi, RankTargetOnly(targets)])
    assert [r.p for r in rows[lo.name]] != [r.p for r in rows[hi.name]]
    assert not degenerate_arms(rows, [lo.name, hi.name])

    bare = [Bout("a1", "b1", 1.0, y, "POINTS", "F", "E1") for y in (2019, 2020, 2021)]
    bare_rows = stream_scores(bare, [engine_for_level(x, {}) for x in (12.5, 800.0)])
    assert degenerate_arms(bare_rows, [level_name(12.5), level_name(800.0)])
    assert {r.p for r in bare_rows[level_name(12.5)]} == {0.5}


def test_rank_target_only_is_static_and_uses_the_black_belt_floor() -> None:
    """The leak control has to be genuinely static, or it stops bounding the prior."""
    arm = RankTargetOnly({"a1": 1200.0})
    before = arm.predict("a1", "b1")
    arm.observe(Bout("a1", "b1", 0.0, 2020, "POINTS", "F", "E1"))
    arm.close_year()
    assert arm.predict("a1", "b1") == before
    assert before == athlete_elo.expected(1200.0, athlete_elo.BASE_BLACKBELT_ELO)
    assert before > 0.5
