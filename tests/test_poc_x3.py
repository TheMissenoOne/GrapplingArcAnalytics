"""PoC-X3's harness on synthetic corpora with a known answer. No database."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from analysis.poc.e8_interaction_graph import Bout, eval_rows
from analysis.poc.x3_sequence_mining import (
    BH_ALPHA,
    Chain,
    LimbA,
    _features,
    _rows_with_context,
    chains_of,
    fold_repeats,
    length_confound,
    run_limb_a,
    run_limb_b,
    verdicts,
)


def _bout(key: int, own: list[tuple[str, str, bool]]) -> Bout:
    """One bout from ``you``'s perspective: (label, type, successful) events, with a filler
    opponent event between each so the sequence looks like a real two-actor stream."""
    seq: list[dict[str, Any]] = []
    for label, typ, ok in own:
        seq.append({"label": label, "type": typ, "actor_id": "you", "successful": ok,
                    "ts": float(10 * len(seq))})
        seq.append({"label": "Guard", "type": "guard", "actor_id": "opp",
                    "successful": False, "ts": float(10 * len(seq))})
    return Bout(key=(2020, f"{key:03d}"), sequence=seq, perspective="you")


CTRL = "control"


def test_fold_repeats_matches_the_graph_builders_no_self_edge_rule() -> None:
    assert fold_repeats(["a", "a", "b", "b", "b", "a"]) == ["a", "b", "a"]
    assert fold_repeats([]) == []


def test_chain_is_truncated_before_the_first_landed_submission() -> None:
    b = _bout(0, [("Back Control", CTRL, False), ("Armbar", "submission", False),
                  ("Mount", CTRL, False), ("Triangle", "submission", True),
                  ("Celebration", CTRL, False)])
    (c,) = chains_of([b])
    assert c.items == ["Back Control", "Armbar", "Mount"]      # the landed sub and after: gone
    assert c.finished is True


def test_a_chain_with_no_landed_submission_keeps_everything_and_is_unfinished() -> None:
    b = _bout(0, [("Back Control", CTRL, False), ("Mount", CTRL, False),
                  ("Armbar", "submission", False)])
    (c,) = chains_of([b])
    assert c.items == ["Back Control", "Mount", "Armbar"]
    assert c.finished is False


def test_chains_shorter_than_the_floor_are_dropped() -> None:
    assert chains_of([_bout(0, [("Back Control", CTRL, False)])]) == []


# ── limb A ──────────────────────────────────────────────────────────────────────


def _chains(spec: list[tuple[list[str], bool]]) -> list[Chain]:
    return [Chain((2020, f"{i:03d}"), "you", list(items), fin)
            for i, (items, fin) in enumerate(spec)]


def test_limb_a_finds_a_planted_pattern_and_bh_lets_it_through() -> None:
    """40 chains: 20 carry `a → b` and all finish, 20 do not and none finish. The lift is
    as large as a lift can be, so if BH refuses THIS the family correction is broken."""
    planted = _chains([(["a", "b", "c"], True)] * 20 + [(["x", "y", "z"], False)] * 20)
    limb = run_limb_a(planted, min_support=10)
    survivors = {r.pattern for r in limb.survivors}
    assert ("a", "b") in survivors
    assert all(r.q_value <= BH_ALPHA for r in limb.survivors)
    row = next(r for r in limb.rows if r.pattern == ("a", "b"))
    assert row.rate == pytest.approx(1.0)


def test_limb_a_refuses_a_pattern_with_no_lift() -> None:
    """Every chain carries `a → b`; half finish. The pattern is frequent and useless."""
    neutral = _chains([(["a", "b", "c"], i % 2 == 0) for i in range(40)])
    limb = run_limb_a(neutral, min_support=10)
    assert limb.survivors == []


def test_limb_a_family_is_every_mined_pattern_not_a_shortlist() -> None:
    """Rows exist only for patterns with BOTH groups populated — a pattern every chain
    contains has no comparison group and is not a test, so it never enters the family."""
    spec = [(["a", "b", "c"], i % 3 == 0) for i in range(15)] \
        + [(["x", "y", "z"], i % 2 == 0) for i in range(15)]
    limb = run_limb_a(_chains(spec), min_support=5)
    assert limb.n_patterns >= len(limb.rows) >= 3
    assert all(0.0 <= r.q_value <= 1.0 for r in limb.rows)
    assert all(r.support > 0 and r.without_n > 0 for r in limb.rows)


def test_length_confound_detects_a_planted_length_artefact() -> None:
    """Nothing tactical anywhere: short chains mostly finish, long ones mostly do not, and
    the vocabulary is shared. Every pattern's risk ratio must fall with its length, and the
    diagnostic must say so — this is the fixture that makes the corpus run's ρ readable."""
    spec: list[tuple[list[str], bool]] = []
    for i in range(30):
        spec.append((["a", "b"], i % 5 != 0))                          # short, 80% finish
        spec.append((["a", "b", "c", "d", "e", "f"], i % 5 == 0))      # long, 20% finish
        spec.append((["p", "q"], i % 2 == 0))                          # neither: 50%
    limb = run_limb_a(_chains(spec), min_support=10)
    assert limb.mean_len_finished < limb.mean_len_unfinished
    # The assertion that matters is that the diagnostic FIRES — a negative correlation whose
    # interval excludes 0. The point estimate is diluted by the neutral `p → q` chains, and
    # pinning it to a threshold would pin the fixture rather than the instrument.
    assert limb.len_vs_ratio_rho < -0.3
    assert limb.len_vs_ratio_hi < 0.0


def test_length_confound_is_silent_when_there_is_nothing_to_correlate() -> None:
    limb = LimbA(min_support=2, n_chains=0, n_finished=0, base_rate=float("nan"),
                 n_patterns=0)
    length_confound(limb, [])
    assert np.isnan(limb.len_vs_ratio_rho)


# ── limb B ──────────────────────────────────────────────────────────────────────


def test_row_context_reuses_eval_rows_unchanged_and_carries_the_history() -> None:
    b = _bout(0, [("Back Control", CTRL, False), ("Mount", CTRL, False),
                  ("Armbar", "submission", True)])
    events, labels, hist, groups = _rows_with_context([b])
    assert len(events) == len(eval_rows([b]))
    assert [h[-1] for h in hist] == ["Back Control", "Mount", "Armbar"]
    assert hist[-1] == ["Back Control", "Mount", "Armbar"]      # cumulative, folded
    assert set(groups) == {b.key}
    assert labels[0] is True                                   # sub lands within k=5


def test_row_context_raises_rather_than_misaligning() -> None:
    """The guard that keeps a future `node_key`/`clean_label` divergence from silently
    attaching each row to somebody else's history."""
    b = _bout(0, [("Back Control", CTRL, False), ("Mount", CTRL, False)])
    b.sequence.append({"label": "", "type": "", "actor_id": "you", "successful": False})
    # both filters drop the empty-label event, so this bout still aligns
    assert len(_rows_with_context([b])[0]) == 2


def test_pattern_feature_fires_only_when_the_pattern_completes_here() -> None:
    events = [{"label": "Mount", "type": CTRL}, {"label": "Armbar", "type": "submission"}]
    hist = [["Back Control", "Mount"], ["Back Control", "Mount", "Armbar"]]
    vocab = ["Mount", "Armbar", "Back Control"]
    pats = [("Back Control", "Mount")]
    x = _features(events, hist, vocab, pats, with_patterns=True)
    assert x.shape == (2, 4)
    assert x[0, 3] == 1.0        # row 0: the pattern completes on `Mount`
    assert x[1, 3] == 0.0        # row 1: it happened earlier, but not HERE


def test_pattern_columns_vanish_when_patterns_are_switched_off() -> None:
    events = [{"label": "Mount", "type": CTRL}]
    x = _features(events, [["Mount"]], ["Mount"], [("Mount",)], with_patterns=False)
    assert x.shape == (1, 1)


def test_limb_b_reports_the_empty_class_instead_of_a_meaningless_auc() -> None:
    train = [_bout(i, [("Back Control", CTRL, False), ("Mount", CTRL, False)])
             for i in range(4)]
    held = [_bout(9, [("Back Control", CTRL, False), ("Mount", CTRL, False)])]
    limb = run_limb_b(train, held, [("Back Control", "Mount")], n_boot=50)
    assert limb.error is not None and "class" in limb.error


def test_limb_b_recovers_a_planted_pattern_the_current_state_cannot_see() -> None:
    """`Mount` finishes iff it was entered FROM `Back Control`. The one-hot of the current
    state is identical either way, so only the pattern column can separate them — the
    harness's positive control for limb B."""
    train: list[Bout] = []
    for i in range(60):
        train.append(_bout(2 * i, [("Back Control", CTRL, False), ("Mount", CTRL, False),
                                   ("Armbar", "submission", True)]))
        train.append(_bout(2 * i + 1, [("Half Guard", CTRL, False), ("Mount", CTRL, False),
                                       ("Knee Slice", "pass", False)]))
    held = list(train[:20])
    limb = run_limb_b(train, held, [("Back Control", "Mount")], n_boot=200)
    assert limb.error is None
    base = limb.model("state one-hot (baseline)")
    full = limb.model("state one-hot + PrefixSpan patterns")
    assert base is not None and full is not None
    assert full.auc > base.auc


def test_verdict_needs_limb_b_not_limb_a() -> None:
    """A cell where every pattern has a huge lift and none of them predicts must REJECT."""
    from analysis.poc.x3_sequence_mining import LimbB, Run, SupportPass

    a = LimbA(min_support=10, n_chains=40, n_finished=20, base_rate=0.5, n_patterns=3)
    a.survivors = [object()]  # type: ignore[list-item]
    b = LimbB(100, 50, 10, 3, delta=(0.001, -0.02, 0.02))
    run = Run("test", 40, 10, [SupportPass(0.10, a, b)])
    v = verdicts(run)
    assert v["cell"].startswith("REJECT")

    b.delta = (0.05, 0.01, 0.09)
    assert verdicts(run)["cell"] == "ACCEPT"

    b.delta = (0.05, 0.05, 0.05)          # degenerate width — PoC-E9's amendment
    assert verdicts(run)["cell"].startswith("REJECT")
