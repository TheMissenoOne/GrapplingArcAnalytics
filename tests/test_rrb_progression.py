"""RRB progression, cycles, and the shape of the action-weights artifact.

Every trajectory case below is HAND-COMPUTABLE: the value table is written out by hand, the
chain is five events long, and the expected positions, deltas and runs are spelled in the
comment beside the assertion. That is the point — the arithmetic of this layer is simple and the
risk is entirely in the conventions (which end of a transition is charged, whether a gap is
bridged, which run has a successor), so the tests pin the CONVENTIONS rather than the numbers.

No database. The artifact tests read the committed
``data/rating/markov_action_weights.json`` and assert its invariants; whether it still matches
the corpus is a different question, answered by
``uv run python -m scripts.build_markov_action_weights --check``, which needs prod.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from analysis.lamas_chain import STATES, chain_of
from analysis.rrb_progression import (
    NEUTRAL_SHARE,
    PHASES,
    VALUE_SOURCES,
    WEIGHT_FLOOR,
    athlete_progression,
    pooled_retention,
    positions,
    trajectory,
    value_table,
    weights_from_value_table,
)

REPO = Path(__file__).resolve().parent.parent

# `scripts/` is in mypy's exclude list; importing it normally would drag it into the checked set
# (same reason and same idiom as `tests/test_bracket_export.py`).
_SPEC = importlib.util.spec_from_file_location(
    "build_markov_action_weights", REPO / "scripts" / "build_markov_action_weights.py")
assert _SPEC and _SPEC.loader
bw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bw)

ARTIFACT = REPO / "data" / "rating" / "markov_action_weights.json"


# ── the hand-written value table ────────────────────────────────────────────────
# Five states carry a value; the other seven carry none, which is what lets the gap cases below
# exercise the refusal path without touching the corpus.
HAND_VALUES: dict[str, float] = {"CDP": 0.1, "TKDA": -0.2, "BTK": 0.5, "PGD": -0.4, "GPSA": 0.0}


def hand_table() -> dict[str, Any]:
    states: dict[str, dict[str, Any]] = {
        s: {"state": s, "value": None, "source": "none", "n": 0, "bouts": 0,
            "n_terminal": 0, "balance": None, "sub_share": None,
            "reason_code": "synthetic"} for s in STATES}
    for s, v in HAND_VALUES.items():
        states[s].update(value=v, source="rrb_sub_share", sub_share=(v + 1) / 2)
    return {"states": states, "mixed_source": False}


def ev(t: str, label: str, actor: str, ok: bool | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"type": t, "label": label, "actor_id": actor}
    if ok is not None:
        e["successful"] = ok
    return e


# X clinch (CDP), Y clinch (CDP), X back take (BTK), Y guard pull (PGD), X takedown try (TKDA).
# From X's side: +0.1, −0.1, +0.5, +0.4, −0.2.
LINE = {"id": "line", "a_id": "X", "b_id": "Y", "seq": [
    ev("control", "Collar Tie", "X"),
    ev("control", "Collar Tie", "Y"),
    ev("control", "Back Control", "X", True),
    ev("guard", "Guard Pull", "Y"),
    ev("takedown", "Single Leg", "X"),
]}


def test_the_chain_this_file_reasons_about_is_the_one_lamas_chain_produces() -> None:
    """If the mapping ever moved, every hand-computed number below would be about a different
    chain. Assert the states first so a mapping change fails HERE and not as arithmetic."""
    assert [s.state for s in chain_of(LINE).steps] == ["CDP", "CDP", "BTK", "PGD", "TKDA"]


def test_position_flips_sign_with_the_side() -> None:
    """An opponent's action is worth MINUS its value to the reference athlete — the mirror
    property of the sided kernel (§8.2), which is the whole reason a signed walk is defined."""
    pos = positions(chain_of(LINE), "X", hand_table())
    assert [p.pos for p in pos] == [0.1, -0.1, 0.5, 0.4, -0.2]
    assert [p.is_ref for p in pos] == [True, False, True, False, True]
    # Y's guard pull is worth −0.4 to Y and therefore +0.4 to X: the same action, both sides.
    assert pos[3].value == -0.4 and pos[3].pos == 0.4


def test_progression_arms_are_disjoint_and_sum_to_net() -> None:
    t = trajectory(chain_of(LINE), "X", hand_table())
    assert t["deltas"] == [-0.2, 0.6, -0.1, -0.6]
    assert t["net"] == -0.3
    assert t["gained"] == 0.6 and round(t["lost"], 6) == -0.9
    assert round(t["gained"] + t["lost"], 6) == t["net"]
    assert t["per_action"] == round(-0.3 / 4, 6)


def test_net_telescopes_to_end_minus_start_when_nothing_is_missing() -> None:
    """A property, not a coincidence: with no unvalued step the sum of Δ IS the endpoint
    difference. It is asserted so that a future change to the Δ rule cannot silently break the
    identity that makes `net` readable."""
    t = trajectory(chain_of(LINE), "X", hand_table())
    assert round(float(t["end_pos"]) - float(t["start_pos"]), 6) == t["net"]


def test_the_opponents_walk_is_the_exact_mirror() -> None:
    a = trajectory(chain_of(LINE), "X", hand_table())
    b = trajectory(chain_of(LINE), "Y", hand_table())
    assert b["net"] == -a["net"]
    assert b["cycles"]["off_steps"] == a["cycles"]["def_steps"]
    assert b["cycles"]["def_steps"] == a["cycles"]["off_steps"]
    assert b["cycles"]["recoveries"] == a["cycles"]["collapses"]


def test_cycles_runs_durations_and_recovery() -> None:
    """Runs from X's side: off | def | off off | def. The trailing `def` ends the chain, so it
    has no successor and is out of the recovery denominator (build_graph's rule)."""
    c = trajectory(chain_of(LINE), "X", hand_table())["cycles"]
    assert [r["phase"] for r in c["runs"]] == ["off", "def", "off", "def"]
    assert (c["off_cycles"], c["def_cycles"]) == (2, 2)
    assert (c["off_steps"], c["def_steps"]) == (3, 2)
    assert (c["off_max_len"], c["def_max_len"]) == (2, 1)
    assert c["off_mean_len"] == 1.5 and c["def_mean_len"] == 1.0
    assert c["recoveries"] == 1 and c["def_runs_with_successor"] == 1
    assert c["collapses"] == 2 and c["off_runs_with_successor"] == 2
    assert c["valued_steps"] == 5 and c["off_share"] == 0.6


def test_a_two_phase_chain_flags_its_recovery_rate_as_degenerate() -> None:
    """With only `off` and `def` ground, runs alternate by definition and every defensive run
    with a successor is followed by an offensive one — the rate is 1.00 by construction. It is
    the ordinary case on the real corpus, so the flag ships rather than the tautology."""
    c = trajectory(chain_of(LINE), "X", hand_table())["cycles"]
    assert c["phases_present"] == ["def", "off"]
    assert c["recovery_degenerate"] is True
    assert c["recoveries"] == c["def_runs_with_successor"]
    # Neutral ground breaks the alternation and the rate starts carrying information again.
    via = {"id": "via2", "a_id": "X", "b_id": "Y", "seq": [
        ev("takedown", "Single Leg", "X"),          # TKDA −0.2  def
        ev("pass", "Knee Cut", "X"),                # GPSA  0.0  neutral
        ev("control", "Back Control", "X", True),   # BTK  +0.5  off
    ]}
    c2 = trajectory(chain_of(via), "X", hand_table())["cycles"]
    assert c2["recovery_degenerate"] is False
    assert c2["recoveries"] == 0 and c2["def_runs_with_successor"] == 1


def test_exchanges_are_actor_runs() -> None:
    t = trajectory(chain_of(LINE), "X", hand_table())
    assert t["n_exchanges"] == 5
    assert [e["is_ref"] for e in t["exchanges"]] == [True, False, True, False, True]
    assert all(e["n_steps"] == 1 for e in t["exchanges"])
    # Two consecutive actions by the same athlete are ONE exchange.
    run = {"id": "run", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"),
        ev("control", "Back Control", "X", True),
        ev("guard", "Guard Pull", "Y"),
    ]}
    t2 = trajectory(chain_of(run), "X", hand_table())
    assert t2["n_exchanges"] == 2
    assert t2["exchanges"][0]["n_steps"] == 2
    assert t2["exchanges"][0]["delta"] == 0.4      # 0.1 → 0.5


# ── the refusal path: an unvalued step is not a zero ────────────────────────────

GAP = {"id": "gap", "a_id": "X", "b_id": "Y", "seq": [
    ev("control", "Collar Tie", "X"),      # CDP  +0.1
    ev("sweep", "Butterfly Sweep", "X"),   # SWPA — no value in the hand table
    ev("control", "Collar Tie", "X"),      # CDP  +0.1
]}


def test_an_unvalued_step_leaves_the_denominator_and_is_counted() -> None:
    t = trajectory(chain_of(GAP), "X", hand_table())
    assert t["deltas"] == [None, None]
    assert t["n_valued_transitions"] == 0 and t["unvalued_transitions"] == 2
    assert t["net"] == 0 and t["per_action"] is None


def test_an_unvalued_step_is_never_bridged_over() -> None:
    """Splicing two offensive runs across a missing value would invent a dominance streak that
    did not happen. `unvalued` is its own phase and stays one."""
    c = trajectory(chain_of(GAP), "X", hand_table())["cycles"]
    assert [r["phase"] for r in c["runs"]] == ["off", "unvalued", "off"]
    assert c["off_cycles"] == 2
    assert c["unvalued_steps"] == 1 and c["valued_steps"] == 2


def test_an_unknown_actor_is_unvalued_even_when_the_state_has_a_value() -> None:
    anon = {"id": "anon", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"),
        {"type": "control", "label": "Collar Tie"},        # no actor_id
    ]}
    pos = positions(chain_of(anon), "X", hand_table())
    assert pos[1].value == 0.1 and pos[1].pos is None and pos[1].phase == "unvalued"


def test_a_recovery_needs_the_two_runs_to_be_adjacent() -> None:
    """`def → neutral → off` is not a recovery: the middle stretch is ground on which nothing
    was measured, and crediting it would read a gap as progress."""
    via = {"id": "via", "a_id": "X", "b_id": "Y", "seq": [
        ev("takedown", "Single Leg", "X"),     # TKDA −0.2  def
        ev("pass", "Knee Cut", "X"),           # GPSA  0.0  neutral
        ev("control", "Back Control", "X", True),   # BTK +0.5  off
    ]}
    c = trajectory(chain_of(via), "X", hand_table())["cycles"]
    assert [r["phase"] for r in c["runs"]] == ["def", "neutral", "off"]
    assert c["recoveries"] == 0 and c["def_runs_with_successor"] == 1
    assert set(PHASES) >= {r["phase"] for r in c["runs"]}


# ── the value table and its fallback chain ──────────────────────────────────────

def rrb_stub(rows: dict[str, tuple[bool, float | None]], estimable: bool = True,
             reason: str | None = None) -> dict[str, Any]:
    return {
        "rows": [{"state": s, "gated": rows.get(s, (False, None))[0],
                  "sub_share": rows.get(s, (False, None))[1], "balance": None,
                  "n": 10, "bouts": 6, "n_terminal": 0, "reason_code": None}
                 for s in STATES],
        "absorption": {"estimable": estimable, "reason_code": reason, "absorbing_bouts": 6},
    }


def rr_stub(rows: dict[str, tuple[bool, float | None, int, int, int]]) -> dict[str, Any]:
    """``state -> (gated, score, reward_k, risk_k, n)``. The pooled retention is derived from
    the arms, exactly as the real block's is."""
    out = []
    for s in STATES:
        g, sc, rk, kk, n = rows.get(s, (False, None, 0, 0, 0))
        out.append({"state": s, "gated": g, "score": sc, "n": n,
                    "reward": {"k": rk}, "risk": {"k": kk},
                    "coverage": {"reason_code": None if g else "few_clusters"}})
    return {"rows": out}


def test_tier_one_is_the_rrb_share_mapped_to_the_signed_range() -> None:
    v = value_table(rrb_stub({"BTK": (True, 0.75)}), rr_stub({}))
    assert v["states"]["BTK"]["source"] == "rrb_sub_share"
    assert v["states"]["BTK"]["value"] == 0.5          # 2 * 0.75 − 1
    assert v["mixed_source"] is False


def test_a_refused_corpus_cannot_use_tier_one_at_all() -> None:
    """`no_absorbing_bouts` refuses the whole corpus, not just a row — twelve zeroes would read
    as 'we measured no risk' when the truth is 'we measured nothing' (§8.4)."""
    v = value_table(rrb_stub({"BTK": (True, 0.75)}, estimable=False,
                             reason="no_absorbing_bouts"), rr_stub({}))
    assert v["corpus_estimable"] is False
    assert v["n_by_source"]["rrb_sub_share"] == 0
    assert v["states"]["BTK"]["source"] == "none" and v["states"]["BTK"]["value"] is None


def test_tier_two_is_centred_on_the_pooled_retention() -> None:
    """The uncentred `reward_risk` score has an arbitrary zero — it means 'the next action is a
    coin flip', not 'neither athlete is ahead'. Measured pooled retention is +0.41 on the real
    corpus, so an uncentred substitution would put ELEVEN of twelve states on the positive side
    and call almost every step offensive.

    Here both states score POSITIVE (+0.5 and +0.2) and the corpus's pooled retention is +0.35,
    so after centring one is above the corpus and one is below it — which is the whole claim.
    """
    rr = rr_stub({"BTK": (True, 0.5, 75, 25, 100), "PGD": (True, 0.2, 60, 40, 100)})
    assert pooled_retention(rr) == pytest.approx(0.35)
    v = value_table(rrb_stub({}), rr)
    assert v["pooled_retention"] == 0.35
    assert v["states"]["BTK"]["source"] == "reward_risk_centered"
    assert v["states"]["BTK"]["value"] == pytest.approx(0.15)
    assert v["states"]["PGD"]["value"] == pytest.approx(-0.15)
    assert v["states"]["PGD"]["rr_score"] > 0 > v["states"]["PGD"]["value"]
    assert v["mixed_source"] is True


def test_tier_two_is_clipped_into_the_value_range() -> None:
    """`score − pooled` can leave [−1, +1] (measured: TKD at −1.170 in the IBJJF family) and the
    value function is defined on that interval."""
    rr = rr_stub({"TKD": (True, -1.0, 0, 100, 100), "BTK": (True, 1.0, 100, 0, 100)})
    v = value_table(rrb_stub({}), rr)
    assert v["states"]["TKD"]["value"] == -1.0
    assert v["states"]["BTK"]["value"] == 1.0


def test_every_state_resolves_to_exactly_one_declared_source() -> None:
    v = value_table(rrb_stub({"BTK": (True, 0.75)}),
                    rr_stub({"PGD": (True, 0.2, 60, 40, 100)}))
    for s in STATES:
        row = v["states"][s]
        assert row["source"] in VALUE_SOURCES
        assert (row["value"] is None) == (row["source"] == "none")
        assert row["value"] is None or -1.0 <= row["value"] <= 1.0
    assert sum(v["n_by_source"].values()) == len(STATES)


# ── the weights transform ───────────────────────────────────────────────────────

def test_weight_is_the_value_function_mapped_back_to_the_share() -> None:
    w = weights_from_value_table(hand_table())
    assert w["BTK"]["weight"] == 0.75 and w["PGD"]["weight"] == 0.3
    assert w["GPSA"]["weight"] == 0.5
    for s, v in HAND_VALUES.items():
        assert w[s]["weight"] == pytest.approx((v + 1) / 2)


def test_a_refused_state_takes_the_documented_neutral_share() -> None:
    w = weights_from_value_table(hand_table())
    assert w["SWPA"]["source"] == "none"
    assert w["SWPA"]["weight"] == NEUTRAL_SHARE


def test_weights_are_non_negative_floored_and_normalise_safely() -> None:
    """The floor is what makes `sum(w) > 0` an INVARIANT rather than an observation, which is
    what a consumer renormalising over a subset depends on."""
    dead = {"states": {s: {"state": s, "value": -1.0, "source": "rrb_sub_share", "n": 0,
                           "bouts": 0, "n_terminal": 0, "balance": None, "sub_share": 0.0,
                           "reason_code": None} for s in STATES},
            "mixed_source": False}
    w = weights_from_value_table(dead)
    assert all(x["weight"] >= WEIGHT_FLOOR for x in w.values())
    assert sum(x["weight"] for x in w.values()) > 0


def test_weights_are_monotone_in_the_share() -> None:
    """The ORDER is the one claim the transform must never disturb; the magnitudes are the
    corpus's business."""
    order = sorted(HAND_VALUES, key=lambda s: HAND_VALUES[s])
    w = weights_from_value_table(hand_table())
    assert [w[s]["weight"] for s in order] == sorted(w[s]["weight"] for s in order)


# ── per-athlete aggregation ─────────────────────────────────────────────────────

def test_the_aggregation_refuses_bouts_the_actor_gate_refuses() -> None:
    """Every number in this module is read through `actor_id`, so a bout that files everything
    under one athlete would show a monotone climb by construction."""
    one_sided = {"id": "one", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"),
        ev("control", "Back Control", "X", True),
    ]}
    agg = athlete_progression([("X", chain_of(one_sided))], hand_table(), n_boot=0)
    assert agg["bouts_used"] == 0
    assert agg["bouts_refused"] == {"single_actor": 1}
    assert agg["rows"] == []


def test_the_aggregation_sums_the_trajectories_and_withholds_intervals_below_the_gate() -> None:
    pairs = [("X", chain_of({**LINE, "id": f"b{i}"})) for i in range(3)]
    agg = athlete_progression(pairs, hand_table(), n_boot=0)
    assert agg["bouts_used"] == 3
    row = agg["rows"][0]
    assert row["athlete"] == "X" and row["bouts"] == 3
    assert row["n_valued_transitions"] == 12
    assert row["net_total"] == pytest.approx(-0.9)
    assert row["per_action"] == pytest.approx(-0.075)
    # Three bouts is below MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE, so the counts survive and every
    # interval is withheld — the report's rule everywhere.
    assert row["gated"] is False
    assert row["off_share"]["k"] == 9 and row["off_share"]["n"] == 15
    assert row["off_share"]["lo"] is None and row["off_share"]["estimable"] is False
    assert row["per_action_lo"] is None


def test_the_aggregation_earns_intervals_once_enough_bouts_stand_behind_it() -> None:
    pairs = [("X", chain_of({**LINE, "id": f"b{i}"})) for i in range(6)]
    agg = athlete_progression(pairs, hand_table(), n_boot=200)
    row = agg["rows"][0]
    assert row["gated"] is True and row["off_share"]["estimable"] is True
    assert row["off_share"]["lo"] is not None
    assert row["per_action_lo"] is not None and row["per_action_hi"] is not None
    # Six identical bouts carry no spread, so the interval collapses onto the estimate.
    assert row["per_action_lo"] == pytest.approx(row["per_action"])
    # 6/6 — and flagged, because with only off/def ground that ratio is a tautology.
    assert row["recovery_rate"]["k"] == 6 and row["recovery_rate"]["n"] == 6
    assert row["recovery_degenerate"] is True
    assert row["mean_def_cycle_len"] == 1.0 and row["mean_off_cycle_len"] == 1.5


def test_the_aggregation_is_deterministic() -> None:
    pairs = [("X", chain_of({**LINE, "id": f"b{i}"})) for i in range(6)]
    a = athlete_progression(pairs, hand_table(), n_boot=200)
    b = athlete_progression(pairs, hand_table(), n_boot=200)
    assert a == b


# ── the artifact ────────────────────────────────────────────────────────────────

def artifact() -> dict[str, Any]:
    if not ARTIFACT.exists():          # pragma: no cover - the file is committed
        pytest.skip(f"{ARTIFACT} not built")
    doc: dict[str, Any] = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    return doc


def test_the_artifact_carries_the_contract_keys() -> None:
    doc = artifact()
    for key in ("version", "generated", "action_space", "action_codes", "method", "transform",
                "global", "provenance", "caveats"):
        assert key in doc, key
    assert isinstance(doc["version"], int)
    assert doc["action_space"] == "lamas12"
    assert doc["action_codes"] == list(STATES)


def test_every_shipped_block_covers_the_whole_action_space() -> None:
    """A consumer indexes by action code; a block missing a code would silently take that
    consumer's own fallback and make two blocks mean different things."""
    doc = artifact()
    for name in ("global", *bw.FAMILIES):
        if name not in doc:
            continue
        assert set(doc[name]) == set(STATES), name


def test_every_weight_is_non_negative_floored_and_bounded() -> None:
    doc = artifact()
    for name in ("global", *bw.FAMILIES):
        for code, w in doc.get(name, {}).items():
            assert isinstance(w, float), (name, code)
            assert WEIGHT_FLOOR <= w <= 1.0, (name, code, w)


def test_any_subset_of_a_block_renormalises_safely() -> None:
    doc = artifact()
    for name in ("global", *bw.FAMILIES):
        block = doc.get(name)
        if not block:
            continue
        for code in block:
            assert sum(block[c] for c in (code,)) > 0
        assert sum(block.values()) > 0


def test_an_omitted_family_says_why_and_names_its_fallback() -> None:
    """The whole point of omitting rather than shipping zeroes: the consumer must be able to
    fall back to `global` and a reader must be able to see which gate refused it."""
    doc = artifact()
    omitted = doc["provenance"]["families_omitted"]
    for fam, why in omitted.items():
        assert fam in bw.FAMILIES
        assert fam not in doc, f"{fam} is both shipped and omitted"
        assert why["reason_code"] and why["fallback"] == "global"
    for fam in bw.FAMILIES:
        assert (fam in doc) != (fam in omitted), fam


def test_the_artifact_publishes_the_provenance_a_refusal_needs() -> None:
    doc = artifact()
    prov = doc["provenance"]
    assert prov["corpus_digest"] and prov["privacy_class"].startswith("A")
    for name, blk in prov["families"].items():
        assert blk["estimable"] is True, name
        assert blk["absorbing_bouts"] > 0, name
        assert sum(blk["n_by_source"].values()) == len(STATES), name
    for name, actions in prov["actions"].items():
        assert set(actions) == set(STATES), name
        for code, row in actions.items():
            assert row["source"] in VALUE_SOURCES, (name, code)


def test_the_transform_block_states_the_pre_registered_definition() -> None:
    t = artifact()["transform"]
    assert t["floor"] == WEIGHT_FLOOR and t["neutral"] == NEUTRAL_SHARE
    assert t["normalized"] is False
    assert t["fallback_order"] == list(VALUE_SOURCES)
    assert "rejected" in t and t["rejected"]


# ── the generator is deterministic given its input ─────────────────────────────

def synthetic_corpus() -> list[dict[str, Any]]:
    """Six submission-won bouts with two actors each — enough absorbing clusters to clear the
    corpus gate without a database."""
    return [{"id": f"s{i}", "event": "ADCC 2024", "win_type": "SUBMISSION", "winner": "X",
             "a_id": "X", "b_id": "Y", "seq": [
                 ev("control", "Collar Tie", "Y"),
                 ev("takedown", "Trip", "X"),
                 ev("pass", "Knee Cut", "X"),
                 ev("submission", "RNC", "X", True),
             ]} for i in range(6)]


def test_the_generator_is_deterministic_given_the_same_corpus() -> None:
    """No RNG (n_boot=0) and rounded weights, so two builds of one corpus are identical — which
    is what makes `--check` a real gate rather than a diff of floating-point noise."""
    bouts = synthetic_corpus()
    a = bw.build(bouts, "2026-01-01T00:00:00Z")
    b = bw.build(list(reversed(bouts)), "2026-01-01T00:00:00Z")
    assert a == b


def test_the_corpus_digest_ignores_edits_that_change_no_lamas_action() -> None:
    """The digest exists to tell a reader whether a moved weight came from the corpus or from
    the code, so it must not move for an annotation edit no weight can see."""
    bouts = synthetic_corpus()
    same = [{**b, "seq": [*b["seq"], {"type": "guard", "label": "Closed Guard",
                                      "actor_id": "Y"}]} for b in bouts]
    assert bw.corpus_digest(same) == bw.corpus_digest(bouts)
    moved = [{**b, "win_type": "DECISION"} for b in bouts]
    assert bw.corpus_digest(moved) != bw.corpus_digest(bouts)


def test_the_generator_refuses_to_write_when_the_global_block_is_not_estimable() -> None:
    """Twelve neutral weights presented as a measurement is the failure this guard exists for."""
    with pytest.raises(SystemExit):
        bw.build([{"id": "x", "event": "ADCC 2024", "win_type": "DECISION", "winner": "X",
                   "a_id": "X", "b_id": "Y", "seq": [ev("control", "Collar Tie", "X"),
                                                     ev("takedown", "Trip", "Y")]}],
                 "2026-01-01T00:00:00Z")
