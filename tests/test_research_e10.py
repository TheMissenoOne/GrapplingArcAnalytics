"""PoC-E10 — the criterion, on synthetic corpora with answers known in advance.

A harness that decides whether production gets a clock is only worth a verdict if it can be
shown to refuse a corpus with no clock in it and to accept one that is nothing but clock. These
assert exactly that, plus the two identities the pre-registration leans on
(``docs/research/2026-09-14_e10_terminal_hazard_prereg.md`` §3 and §5/A1). No DB, no files.
"""

from __future__ import annotations

from scripts.research.e10_terminal_hazard import (
    CUT,
    LANDMARKS,
    POINTS,
    SUB,
    Prep,
    TermBout,
    fit_hazards,
    p_points,
    permutation_null,
    piece_of,
    run_primary,
    to_bouts,
)


class _Row:
    """The three attributes ``to_bouts`` reads off an ``e9_markov.BoutRow``."""

    def __init__(self, key: object, elapsed: list[float] | None, terminal: str | None) -> None:
        self.key = key
        self.elapsed = elapsed
        self.terminal = terminal


def _clocked() -> list[TermBout]:
    """Submissions land early, points late — the crossover, planted."""
    return ([TermBout(("s", i), 60.0 + 1.0 * i, SUB) for i in range(240)]
            + [TermBout(("p", i), 700.0 + 3.0 * i, POINTS) for i in range(120)])


def _clockless() -> list[TermBout]:
    """Same durations, cause alternating with the index — no time trend to find."""
    return [TermBout((i,), 90.0 + 5.0 * i, POINTS if i % 2 else SUB) for i in range(200)]


# ── the two registered identities ───────────────────────────────────────────────
def test_homogeneous_model_is_exactly_the_train_marginal_at_every_landmark() -> None:
    """Prereg §3: with one piece there is nothing for the clock to do, so M_hom must return the
    same number at t=120 and t=720 — and that number must be the train marginal over causes.
    This is why "homogeneous chain" and "train marginal" are one comparator, not two."""
    hom = fit_hazards(_clocked(), cuts=())
    hp, hs = hom.h[0]
    marginal = hp / (hp + hs)
    for t in LANDMARKS:
        assert abs(p_points(hom, t) - marginal) < 1e-9


def test_cells_are_assigned_to_a_piece_by_their_start_time() -> None:
    """The 600 s boundary cell belongs to the LATE piece; the cell before it does not."""
    assert piece_of(19, (CUT,)) == 0
    assert piece_of(20, (CUT,)) == 1
    assert piece_of(0, ()) == 0


# ── does the criterion see a clock, and refuse one that isn't there? ─────────────
def test_piecewise_model_recovers_the_planted_crossover() -> None:
    piece = fit_hazards(_clocked(), cuts=(CUT,))
    assert p_points(piece, 120.0) < 0.5 < p_points(piece, 720.0)


def test_verdict_accepts_a_corpus_that_is_nothing_but_clock() -> None:
    r = run_primary(_clocked(), _clocked(), n_boot=300, n_perm=50)
    assert r.d_ll[0] > 0 and r.d_ll[1] > 0
    assert r.perm.p <= 0.05
    assert r.passes


def test_verdict_refuses_a_corpus_with_no_clock_in_it() -> None:
    r = run_primary(_clockless(), _clockless(), n_boot=300, n_perm=50)
    assert not r.passes


def test_degenerate_interval_is_never_a_win() -> None:
    """E9 amendment 1, inherited: a zero-width bootstrap interval is the signature of two
    models differing by a constant offset, not by what they predict."""
    r = run_primary(_clocked(), _clocked(), n_boot=300, n_perm=50)
    flat = type(r)(**{**r.__dict__, "d_ll": (0.005, 0.005, 0.005)})
    assert not flat.passes


# ── the null control, and why A1 had to change it ───────────────────────────────
def test_joint_permutation_destroys_the_signal_it_is_meant_to_destroy() -> None:
    """Prereg §5 + A1: shuffling causes over train AND eval and re-fitting leaves the clock
    carrying nothing, so an observed Δ of the planted corpus' size must sit far in the upper
    tail — and a Δ of 0 must not."""
    clocked = _clocked()
    obs = run_primary(clocked, clocked, n_boot=200, n_perm=0).d_ll[0]
    hit = permutation_null(clocked, clocked, obs, (CUT,), LANDMARKS, 2400.0, 30.0, n_perm=50)
    assert hit.p <= 0.05
    miss = permutation_null(clocked, clocked, 0.0, (CUT,), LANDMARKS, 2400.0, 30.0, n_perm=50)
    assert miss.p > 0.05
    assert hit.mean < obs


# ── corpus preparation counts every drop ────────────────────────────────────────
def test_to_bouts_drops_and_counts_rather_than_defaulting() -> None:
    rows = [
        _Row(("keep",), [0.0, 100.0, 300.0], "END/points"),
        _Row(("no-ts",), None, "END/submission"),
        _Row(("no-term",), [0.0, 50.0], None),
        _Row(("draw",), [0.0, 400.0], "END/draw"),
        _Row(("too-long",), [0.0, 20000.0], "END/points"),
        _Row(("jumbled",), [0.0, 300.0, 100.0], "END/submission"),
    ]
    bouts, prep = to_bouts(rows)
    assert prep == Prep(gated=6, no_ts=1, no_terminal=1, draw=1, over_span=1,
                        non_monotonic=1, kept=2)
    assert {b.key for b in bouts} == {("keep",), ("jumbled",)}
    # duration is max(elapsed), so a non-monotonic ts is counted but does not change the clock
    assert next(b for b in bouts if b.key == ("jumbled",)).duration == 300.0


def test_merging_draws_into_points_keeps_them_as_points() -> None:
    rows = [_Row("d", [0.0, 400.0], "END/draw")]
    bouts, prep = to_bouts(rows, merge_draw=True)
    assert prep.draw == 0 and prep.kept == 1
    assert bouts[0].cause == POINTS
