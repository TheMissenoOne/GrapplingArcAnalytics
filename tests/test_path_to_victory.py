"""Path-to-Victory engine — discounted Markov-reward value iteration + derived metrics.

Pure tests off fixture sequences (same style as test_network_metrics). Design note:
docs/path_to_victory.md.
"""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import network_from_sequences
from analysis.path_to_victory import (
    continuity,
    control_score,
    countering_nodes,
    decision_funnel,
    dilemmas,
    edge_ptv,
    node_elo_deviance,
    path_to_victory,
    ptv_momentum,
)


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


BC, RNC, CG, TRI = "Back Control", "Rear Naked Choke", "Closed Guard", "Triangle Choke"
MT, HG = "Mount", "Half Guard"  # canonical (clean_label folds "Full Mount" → "Mount")


def _sequences() -> list[list[dict[str, Any]]]:
    back_to_sub = [_e(BC, "control", "A"), _e(RNC, "submission", "A", True)]
    return [
        back_to_sub, back_to_sub, back_to_sub,
        [_e(CG, "guard", "B"), _e(BC, "control", "A"), _e(RNC, "submission", "A", True)],
        [_e(CG, "guard", "B"), _e(TRI, "submission", "B", True)],
    ]


def _g():
    return network_from_sequences(_sequences())


class TestPathToVictory:
    def test_converges_and_is_deterministic(self) -> None:
        g = _g()
        v1 = path_to_victory(g)
        v2 = path_to_victory(g)
        assert v1 == v2
        assert set(v1) == set(g.nodes)
        assert all(-1.0 <= x <= 1.0 for x in v1.values())

    def test_sure_finish_is_terminal_plus_one(self) -> None:
        # Back Control's next own action is ALWAYS a finished RNC → p_reward = 1 → +1.
        v = path_to_victory(_g())
        assert v[BC] == 1.0

    def test_sure_get_finished_is_minus_one(self) -> None:
        # A's half guard is ALWAYS immediately followed by B finishing → p_risk = 1 → −1.
        seqs = [[_e(HG, "guard", "A"), _e(TRI, "submission", "B", True)]] * 3
        v = path_to_victory(network_from_sequences(seqs))
        assert v[HG] == -1.0

    def test_gamma_zero_reduces_to_reward_risk_ordering(self) -> None:
        g = _g()
        v = path_to_victory(g, gamma=0.0)
        # 1-step case: sure-finish node beats the contested guard.
        assert v[BC] > v[CG]

    def test_value_propagates_upstream(self) -> None:
        # Mount → Back Control → finish: mount has no 1-step reward but inherits
        # discounted downstream value; a dead-end node does not.
        seqs = [
            [_e(MT, "control", "A"), _e(BC, "control", "A"),
             _e(RNC, "submission", "A", True)],
        ] * 3 + [[_e(HG, "guard", "A"), _e(CG, "guard", "A")]] * 3
        v = path_to_victory(network_from_sequences(seqs))
        assert v[MT] > 0.5          # γ·v(BC) ≈ 0.8 (+ shaping)
        assert v[MT] > v[HG]


class TestDerivedMetrics:
    def test_dilemma_detects_a_fork(self) -> None:
        # Mount forks into two high-value finishing branches → dilemma;
        # Half Guard has a single branch → not a dilemma.
        seqs = (
            [[_e(MT, "control", "A"), _e(RNC, "submission", "A", True)]] * 3
            + [[_e(MT, "control", "A"), _e(TRI, "submission", "A", True)]] * 3
            + [[_e(HG, "guard", "A"), _e(RNC, "submission", "A", True)]] * 3
        )
        g = network_from_sequences(seqs)
        v = path_to_victory(g)
        found = dilemmas(g, v)
        by_node = {d["node"] for d in found}
        assert MT in by_node
        assert HG not in by_node
        mount = next(d for d in found if d["node"] == MT)
        assert len(mount["branches"]) >= 2

    def test_countering_detects_a_dominance_flip(self) -> None:
        # B is being controlled, then sweeps — dominance flips at the sweep.
        seqs = [[
            _e(MT, "control", "A"),
            _e("Scissor Sweep", "sweep", "B", True),
            _e(MT, "control", "B"),
        ]] * 3
        scores = countering_nodes(seqs)
        assert scores.get("Scissor Sweep", 0.0) > 0.0
        assert scores.get("Scissor Sweep", 0.0) > scores.get(MT, 0.0)

    def test_momentum_series_is_share_shaped(self) -> None:
        g = _g()
        v = path_to_victory(g)
        # breakdown-shaped sequence: side tags present (the C2 call site)
        seq = [
            {**_e(CG, "guard", "B"), "side": "b"},
            {**_e(BC, "control", "A"), "side": "a"},
            {**_e(RNC, "submission", "A", True), "side": "a"},
        ]
        series = ptv_momentum(seq, v)
        assert len(series) == 3
        assert all(0.0 <= x <= 1.0 for x in series)
        # A takes back control then finishes → momentum ends on A's side.
        assert series[-1] > 0.5

    def test_continuity_funnel_control_bounded(self) -> None:
        g = _g()
        v = path_to_victory(g)
        for d in (continuity(g), decision_funnel(g, v), control_score(g, v)):
            assert d, "metric returned nothing"
            assert all(isinstance(x, float) for x in d.values())
        assert all(0.0 <= x <= 1.0 for x in continuity(g).values())
        assert all(0.0 <= x <= 1.0 for x in decision_funnel(g, v).values())

    def test_edge_ptv_prefers_the_finishing_branch(self) -> None:
        g = _g()
        v = path_to_victory(g)
        e = edge_ptv(g, v)
        # within-actor edges only: B's own flow CG → TRI (their landed finish)
        assert e[(CG, TRI)] > 0.5
        assert e[(BC, RNC)] > 0.5


class TestEloDeviance:
    def test_centered_clamped_and_keyed_by_node_key(self) -> None:
        g = _g()
        dev = node_elo_deviance(g)
        # keyed by node_key (normalized), not display label
        assert "back control" in dev
        assert all(isinstance(x, int) for x in dev.values())
        assert all(-150 <= x <= 150 for x in dev.values())
        # occ-weighted centering: corpus mean offset ≈ 0
        from analysis.names import _normalize_name
        total_occ = sum(d["occ"] for _, d in g.nodes(data=True))
        weighted = sum(dev[_normalize_name(n)] * d["occ"] for n, d in g.nodes(data=True))
        assert abs(weighted / total_occ) < 20  # rounding slack
        # the sure finisher sits above the contested guard
        assert dev["back control"] > dev["closed guard"]
