"""PoC-E4 — the γ/shaping sweep and the Markov-order probe, checked before they are read.

The load-bearing test here is ``test_production_config_reproduces_e8``: E4 exists to sweep
around a config PoC-E8 already measured, so if the sweep's production cell ever stops
matching E8's number, the sweep is measuring something else and every row in
``docs/research/poc/e4.md`` is about a different model than the one that ships.
"""

from __future__ import annotations

import math
from typing import Any

import networkx as nx

from analysis.path_to_victory import _SHAPING_W, GAMMA, path_to_victory
from analysis.poc import e8_interaction_graph as e8
from analysis.poc.e4_ptv_eval import (
    ALPHA_GRID,
    GAMMA_ABLATION,
    GAMMA_GRID,
    MIN_CONTEXT,
    PROD_KERNEL,
    SHAPING_GRID,
    chosen,
    dedupe_by_key,
    lamas_anchor,
    markov_order,
    own_transitions,
    production_row,
    row_groups,
    run_pass,
    verdict,
)
from analysis.poc.e8_interaction_graph import (
    Bout,
    actionflow_kernel,
    fixture_bouts,
    interaction_kernel,
)


def _ev(label: str, actor: str, typ: str = "position", ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def _chain_bout(labels: list[str], key: Any, actor: str = "A") -> Bout:
    return Bout(key=key, sequence=[_ev(x, actor) for x in labels], perspective=actor)


# ── the sweep ───────────────────────────────────────────────────────────────────
def test_sweep_table_shape() -> None:
    """Two kernels × the pre-registered grid × shaping, plus the labelled γ=0 ablation."""
    p = run_pass("t", fixture_bouts(), n_boot=40, n_boot_delta=20)
    assert len(p.rows) == 2 * (len(GAMMA_GRID) + 1) * len(SHAPING_GRID)
    for kernel in {r.cfg.kernel for r in p.rows}:
        rows = [r for r in p.rows if r.cfg.kernel == kernel]
        assert sum(r.cfg.is_production for r in rows) == 1
        assert {r.cfg.gamma for r in rows} == {*GAMMA_GRID, GAMMA_ABLATION}
        # the ablation is outside the grid and can never be "chosen"
        assert all(r.cfg.ablation == (r.cfg.gamma == GAMMA_ABLATION) for r in rows)
        best = chosen(p.rows, kernel)
        assert best is not None and not best.cfg.ablation
        assert best.auc == max(r.auc for r in rows if not r.cfg.ablation)


def test_production_config_reproduces_e8() -> None:
    """γ=0.8 + shaping on IS PoC-E8's config — same rows, same value model, same AUC.

    The AUC point estimate is the full-sample statistic (``bootstrap_ci`` returns
    ``statistic(values)`` untouched), so it does not move with ``n_boot`` and the two
    runners are comparable at any bootstrap budget.
    """
    bouts = fixture_bouts()
    ours = run_pass("t", bouts, n_boot=40, n_boot_delta=20)
    train, held = e8.temporal_split(bouts)
    theirs, _ = e8.evaluate_kernels(train, held,
                                    [actionflow_kernel(), interaction_kernel()], n_boot=40)
    for res in theirs:
        row = production_row(ours.rows, res.name)
        assert row is not None, res.name
        assert math.isclose(row.auc, res.auc, rel_tol=0, abs_tol=1e-12), res.name
        assert row.cold == res.cold_rows


def test_seed_gives_identical_output() -> None:
    a = run_pass("t", fixture_bouts(), n_boot=40, n_boot_delta=20)
    b = run_pass("t", fixture_bouts(), n_boot=40, n_boot_delta=20)
    assert [(r.cfg, r.auc, r.lo, r.hi, r.clo, r.chi, r.d_auc, r.d_lo, r.d_hi) for r in a.rows] \
        == [(r.cfg, r.auc, r.lo, r.hi, r.clo, r.chi, r.d_auc, r.d_lo, r.d_hi) for r in b.rows]
    assert [(o.kernel, o.alpha, o.ll1, o.ll2, o.delta, o.lo, o.hi) for o in a.orders] \
        == [(o.kernel, o.alpha, o.ll1, o.ll2, o.delta, o.lo, o.hi) for o in b.orders]


def test_production_row_is_the_only_zero_delta_and_shaping_off_is_a_different_model() -> None:
    p = run_pass("t", fixture_bouts(), n_boot=40, n_boot_delta=20)
    prod = production_row(p.rows, PROD_KERNEL)
    assert prod is not None and (prod.d_auc, prod.d_lo, prod.d_hi) == (0.0, 0.0, 0.0)
    off = next(r for r in p.rows if r.cfg.kernel == PROD_KERNEL
               and r.cfg.gamma == GAMMA and not r.cfg.shaping)
    assert off.auc != prod.auc      # shaping off is a real ablation, not a relabel


def test_row_groups_align_with_eval_rows() -> None:
    """The cluster labels must be one-per-eval-row, in order — otherwise the bout-level
    interval is silently computed over a shuffled partition."""
    bouts = fixture_bouts()
    _, held = e8.temporal_split(bouts)
    assert len(row_groups(held)) == len(e8.eval_rows(held))
    assert set(row_groups(held)) <= {b.key for b in held}


def test_shaping_weight_is_a_knob_and_production_default_is_unchanged() -> None:
    g = nx.DiGraph()
    g.add_edge("back control", "rear naked choke", weight=3)
    for n, typ in (("back control", "control"), ("rear naked choke", "submission")):
        g.nodes[n].update({"type": typ, "occ": 3, "ok_count": 2, "denom": 3,
                           "reward": 1, "risk": 0})
    assert path_to_victory(g) == path_to_victory(g, gamma=GAMMA, shaping_w=_SHAPING_W)
    assert path_to_victory(g, shaping_w=0.0) != path_to_victory(g)


# ── memorylessness probe ────────────────────────────────────────────────────────
def test_second_order_wins_when_the_context_actually_carries_information() -> None:
    """A→B→C and D→B→E: from B alone the successor is a coin flip, from (A,B) it is certain."""
    train = [_chain_bout(["Mount", "Back Control", "Armbar"], (i,)) for i in range(6)]
    train += [_chain_bout(["Half Guard", "Back Control", "Triangle Choke"], (10 + i,))
              for i in range(6)]
    held = [_chain_bout(["Mount", "Back Control", "Armbar"], (100,)),
            _chain_bout(["Half Guard", "Back Control", "Triangle Choke"], (101,))]
    o = markov_order(actionflow_kernel(), train, held, n_boot=200)
    assert o.n_steps == 2 and o.n_second_order == 2
    assert o.contexts == 2                      # both (prev, cur) contexts cleared MIN_CONTEXT
    assert o.ll2 > o.ll1 and o.delta > 0
    assert o.material is True


def test_second_order_is_exactly_neutral_when_it_adds_nothing() -> None:
    """One deterministic chain: the (prev, cur) context predicts what (cur) already did."""
    train = [_chain_bout(["Mount", "Back Control", "Armbar"], (i,)) for i in range(12)]
    held = [_chain_bout(["Mount", "Back Control", "Armbar"], (100,))]
    o = markov_order(actionflow_kernel(), train, held, n_boot=50)
    assert o.n_second_order == 1
    assert math.isclose(o.delta, 0.0, abs_tol=1e-12)
    assert o.material is False


def test_thin_contexts_back_off_instead_of_guessing() -> None:
    """Below MIN_CONTEXT the second-order model IS the first-order one — the probe must
    measure successor structure, never coverage."""
    train = [_chain_bout(["Mount", "Back Control", "Armbar"], (i,))
             for i in range(MIN_CONTEXT - 1)]
    held = [_chain_bout(["Mount", "Back Control", "Armbar"], (100,))]
    o = markov_order(actionflow_kernel(), train, held, n_boot=20)
    assert o.contexts == 0 and o.n_second_order == 0
    assert o.ll1 == o.ll2 and o.delta == 0.0


def test_both_orders_score_the_same_steps() -> None:
    """Second order starts at index 2 so it is never given positions the first order
    cannot see — the whole comparison depends on it."""
    train = [_chain_bout(["Mount", "Back Control", "Armbar", "Mount"], (i,)) for i in range(6)]
    held = [_chain_bout(["Mount", "Back Control", "Armbar", "Mount"], (100,))]
    o = markov_order(actionflow_kernel(), train, held, n_boot=20)
    assert o.n_steps == 2       # a 4-state chain has 2 steps with a (prev, cur) context


def test_mirror_is_deduped_before_the_probe() -> None:
    """Corpus bouts enter twice (one per perspective). Left in, MIN_CONTEXT=5 would be
    2.5 distinct observations."""
    b = _chain_bout(["Mount", "Back Control", "Armbar"], (1,))
    mirrored = [b, Bout(key=b.key, sequence=b.sequence, perspective="B")]
    assert len(dedupe_by_key(mirrored)) == 1
    train = [x for i in range(6) for x in
             (_chain_bout(["Mount", "Back Control", "Armbar"], (i,)),
              Bout(key=(i,), sequence=_chain_bout(["Mount", "Back Control", "Armbar"],
                                                  (i,)).sequence, perspective="B"))]
    held = [_chain_bout(["Mount", "Back Control", "Armbar"], (100,))]
    one = markov_order(actionflow_kernel(), train, held, n_boot=20)
    plain = markov_order(actionflow_kernel(),
                         [_chain_bout(["Mount", "Back Control", "Armbar"], (i,))
                          for i in range(6)], held, n_boot=20)
    assert (one.ll1, one.ll2, one.n_steps) == (plain.ll1, plain.ll2, plain.n_steps)


def test_alpha_grid_is_swept_on_both_kernels() -> None:
    p = run_pass("t", fixture_bouts(), n_boot=40, n_boot_delta=20)
    assert len(p.orders) == 2 * len(ALPHA_GRID)
    assert {o.alpha for o in p.orders} == set(ALPHA_GRID)


# ── external anchor ─────────────────────────────────────────────────────────────
def test_own_transitions_keep_self_loops_and_stay_within_one_actor() -> None:
    """``network_from_sequences`` drops A→A; Lamas' guard-pass→guard-pass IS that cell,
    so the anchor must not be read off the graph."""
    b = Bout(key=(1,), sequence=[_ev("Smash Pass", "A", "pass"), _ev("Smash Pass", "A", "pass"),
                                 _ev("Mount", "B", "control")], perspective="A")
    pairs = own_transitions([b])
    assert len(pairs) == 1                       # B's single event has no own successor
    assert pairs[0][0]["type"] == pairs[0][1]["type"] == "pass"


def test_lamas_anchor_counts_what_it_says() -> None:
    seq = [_ev("Back Control", "A", "control"), _ev("Rear Naked Choke", "A", "submission"),
           _ev("Back Control", "A", "control"), _ev("Mount", "A", "control")]
    rows = {r["name"]: r for r in lamas_anchor([Bout(key=(1,), sequence=seq, perspective="A")])}
    back = rows["back control → submission"]
    assert (back["k"], back["n"]) == (1, 2)      # two exits from back control, one to a sub
    assert back["est"].p == 0.5
    assert back["agrees"] is False or back["est"].lo <= back["lamas"] <= back["est"].hi


# ── verdict ─────────────────────────────────────────────────────────────────────
def test_verdict_needs_the_corpus() -> None:
    v = verdict(None)
    assert v["decided"] is False and "UNDECIDED" in v["text"]


def test_verdict_reads_the_clustered_interval_and_names_a_consequence() -> None:
    p = run_pass("t", fixture_bouts(), n_boot=60, n_boot_delta=20)
    v = verdict(p)
    assert v["decided"] is True
    prod = production_row(p.rows, PROD_KERNEL)
    assert prod is not None
    assert v["demoted"] is not prod.separates      # demotion is exactly "does not separate"
    assert ("ACCEPT" in v["text"]) is prod.separates
    assert "site prose" in v["text"] and "Memorylessness" in v["text"]
