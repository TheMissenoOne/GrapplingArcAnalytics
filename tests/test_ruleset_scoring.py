"""Ruleset scoring: the point tables, the landing envelope, and every refusal that guards them.

Synthetic throughout — no DB, no corpus file. The event labels are real corpus strings, the
bouts around them are invented, and every number asserted here is hand-computable from the
events in the same test.

The load-bearing tests are the REFUSALS. A per-action scoring chance is arithmetic; what makes
it publishable is that the module declines to produce one where the corpus cannot support it —
a family with no point table, an envelope whose bounds overlap, an ADCC window with no clock to
place an action in. Each of those has a case here that fails if the refusal quietly relaxes.
"""
from __future__ import annotations

import json
from itertools import zip_longest
from typing import Any

import pytest

from analysis.lamas_chain import FAMILY, STATES
from analysis.ruleset_scoring import (
    ADCC_NEGATIVE_WINDOW,
    CENSUS_BUCKETS,
    DEFAULT_EVENT_RULESETS_PATH,
    SYMBOL_OF,
    SYMBOLS,
    Mark,
    RulesetError,
    adcc_clock_feasibility,
    annotation_coverage,
    athlete_coverage,
    bout_concentration,
    census,
    comparability,
    expected_points,
    family_of,
    family_report,
    landing_envelopes,
    load_event_rulesets,
    marks_of,
    per_action_contrast,
    points_for,
    scoring_chance,
    scoring_symbols,
    symbol_matrix,
    truncation,
    winner_agreement,
)


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    return load_event_rulesets()


def ev(type_: str, label: str, actor: str = "X", successful: bool | None = None,
       **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"type": type_, "label": label, "actor_id": actor, **extra}
    if successful is not None:
        e["successful"] = successful
    return e


def bout(*events: dict[str, Any], event: str | None = "IBJJF Worlds 2023", id_: str = "b",
         win_type: str = "DECISION", winner: str | None = "X",
         **extra: Any) -> dict[str, Any]:
    return {"id": id_, "event": event, "win_type": win_type, "winner": winner,
            "a_id": "X", "b_id": "Y", "seq": list(events), **extra}


# ── the event → family map ─────────────────────────────────────────────────────

def test_event_rulesets_file_is_wellformed(doc: dict[str, Any]) -> None:
    raw = json.loads(DEFAULT_EVENT_RULESETS_PATH.read_text(encoding="utf-8"))
    assert raw == doc
    assert set(doc["points"]) == set(doc["families"])
    for fam, table in doc["points"].items():
        assert table is None or set(table) == set(SYMBOLS), fam


def test_unknown_event_falls_to_other_never_to_a_ruleset(doc: dict[str, Any]) -> None:
    # The defect this rule exists to prevent: an unrecognised promotion silently acquiring a
    # rule book because it is not obviously the other one.
    assert family_of("Some Superfight Nobody Classified", doc) == "other"
    assert family_of(None, doc) == "unknown"
    assert family_of("ADCC Trials 2024 European", doc) == "adcc"
    assert family_of("World No-Gi 2024", doc) == "ibjjf"      # manual override, no keyword
    assert family_of("UFC 300", doc) == "non_grappling"


def test_symbol_space_covers_the_lamas_state_space() -> None:
    assert set(SYMBOL_OF) == set(STATES)
    assert set(SYMBOL_OF.values()) == set(SYMBOLS)
    for attempt, success in FAMILY.values():
        assert SYMBOL_OF[attempt] == SYMBOL_OF[success] == success


# ── the point tables ───────────────────────────────────────────────────────────

def test_the_two_tables_differ_on_exactly_one_symbol(doc: dict[str, Any]) -> None:
    """The central finding. If this ever fails the doc's headline is stale."""
    assert [s for s in SYMBOLS
            if points_for(s, "ibjjf", doc) != points_for(s, "adcc", doc)] == ["BTK"]
    assert (points_for("BTK", "ibjjf", doc), points_for("BTK", "adcc", doc)) == (4, 3)


def test_no_point_table_is_none_not_zero(doc: dict[str, Any]) -> None:
    for fam in ("cji", "other", "non_grappling", "unknown"):
        assert points_for("SUB", fam, doc) is None, fam
        assert scoring_symbols(fam, doc) == ()
    assert points_for("SUB", "ibjjf", doc) == 0        # scored at nothing, which is not None


def test_unknown_symbol_raises(doc: dict[str, Any]) -> None:
    with pytest.raises(RulesetError):
        points_for("MOUNT", "ibjjf", doc)


def test_negative_window_is_off_by_default(doc: dict[str, Any]) -> None:
    assert ADCC_NEGATIVE_WINDOW is False
    assert points_for("PGD", "adcc", doc) == 0
    assert points_for("PGD", "adcc", doc, negative_window=True) == -1
    assert points_for("PGD", "ibjjf", doc, negative_window=True) == 0   # ADCC-only penalty


# ── marks: the fold `lamas_chain` cannot carry ─────────────────────────────────

def test_marks_pair_with_the_events_they_were_mapped_from() -> None:
    b = bout(
        ev("guard", "Closed Guard"),                       # unmapped, skipped by rule 2
        ev("takedown", "Single Leg", successful=True),
        ev("escape", "Back Escape"),                       # unmapped
        ev("pass", "Knee Cut"),                            # unannotated → attempt
    )
    ms = marks_of(b)
    assert [(m.symbol, m.landed, m.annotated) for m in ms] == [
        ("TKD", True, True), ("GPS", False, False)]


def test_marks_stop_where_the_absorbing_rule_truncates() -> None:
    b = bout(
        ev("submission", "RNC", successful=True),
        ev("takedown", "Single Leg", successful=True),
        win_type="SUBMISSION",
    )
    assert [m.symbol for m in marks_of(b)] == ["SUB"]


# ── the landing envelope ───────────────────────────────────────────────────────

def test_envelope_brackets_the_complete_case_rate() -> None:
    b = bout(
        ev("takedown", "Single Leg", successful=True),
        ev("takedown", "Double Leg", successful=False),
        ev("takedown", "Ankle Pick"),
        ev("takedown", "Body Lock"),
    )
    e = landing_envelopes([marks_of(b)])["TKD"]
    assert (e["envelope"]["lo"], e["envelope"]["cc"], e["envelope"]["hi"]) == (0.25, 0.5, 0.75)
    assert e["width"] == 0.5                     # width IS the missing rate: 2 of 4


def test_envelope_width_is_zero_when_everything_is_annotated() -> None:
    b = bout(ev("takedown", "Single Leg", successful=True),
             ev("takedown", "Double Leg", successful=False))
    e = landing_envelopes([marks_of(b)])["TKD"]
    assert e["width"] == 0.0 and e["envelope"]["cc"] == 0.5


def test_cdp_and_pgd_land_by_definition_and_never_break_the_bound() -> None:
    """They have no attempt state, so `landed` is always true. The envelope must stay in
    [0, 1] anyway — the `landed or annotated` guard in `_symbol_tally` is what does it."""
    b = bout(ev("control", "Collar Tie"), ev("guard", "Guard Pull"))
    envs = landing_envelopes([marks_of(b)])
    for sym in ("CDP", "PGD"):
        e = envs[sym]["envelope"]
        assert (e["lo"], e["hi"], e["landed"], e["annotated"]) == (1.0, 1.0, 1, 1), sym


def test_interval_is_withheld_below_the_bout_cluster_gate() -> None:
    """One bout is not a sample of a ruleset. The counts survive, the interval does not —
    `lamas_chain._cell`'s rule, applied to the same corpus."""
    one = landing_envelopes([marks_of(bout(ev("takedown", "Single Leg", successful=True)))])
    assert one["TKD"]["estimable"] is False
    assert "ci_lo_bound" not in one["TKD"] and one["TKD"]["reason_code"] == "few_clusters"

    spread = [marks_of(bout(ev("takedown", "Single Leg", successful=True), id_=f"b{i}"))
              for i in range(5)]
    assert landing_envelopes(spread)["TKD"]["estimable"] is True
    assert landing_envelopes(spread)["TKD"]["ci_lo_bound"]["lo"] is not None


# ── the headline: chance of scoring, per action, per ruleset ───────────────────

def test_scoring_chance_is_the_landing_rate_where_the_action_scores(doc: dict[str, Any]) -> None:
    ms = [marks_of(bout(ev("pass", "Knee Cut", successful=True),
                        ev("pass", "Leg Drag", successful=False)))]
    row = scoring_chance(ms, "ibjjf", doc)["GPS"]
    assert row["points"] == 3
    assert row["chance"] == {"lo": 0.5, "cc": 0.5, "hi": 0.5, "deterministic": False}


def test_zero_point_action_has_a_deterministic_zero_chance(doc: dict[str, Any]) -> None:
    ms = [marks_of(bout(ev("control", "Collar Tie")))]
    assert scoring_chance(ms, "ibjjf", doc)["CDP"]["chance"] == {
        "lo": 0.0, "cc": 0.0, "hi": 0.0, "deterministic": True}


def test_a_family_without_a_point_table_has_no_scoring_chance(doc: dict[str, Any]) -> None:
    """`None`, not zero. Zero would read as 'these actions never score' instead of 'this
    question does not apply to this rule book'."""
    ms = [marks_of(bout(ev("pass", "Knee Cut", successful=True), event="CJI 2 - Day 1"))]
    chances = scoring_chance(ms, "cji", doc)
    assert all(chances[s]["chance"] is None for s in SYMBOLS)
    assert expected_points(ms, "cji", doc) is None


# ── the Markov layer ───────────────────────────────────────────────────────────

def test_matrix_rows_normalise_and_a_dead_end_row_stays_empty() -> None:
    ms = [marks_of(bout(ev("takedown", "Single Leg", successful=True),
                        ev("pass", "Knee Cut", successful=True),
                        ev("control", "Back Control", successful=True)))]
    m = symbol_matrix(ms)
    for row in m:
        total = sum(row)
        assert total == 0 or abs(total - 1.0) < 1e-9
    assert sum(m[SYMBOLS.index("BTK")]) == 0.0      # nothing follows; never made uniform


def test_expected_points_accumulate_over_the_horizon(doc: dict[str, Any]) -> None:
    """TKD → GPS → BTK, everything landed, so the chain is deterministic and the arithmetic
    is the point table: from TKD the next three actions are worth 3 + 4 + nothing."""
    ms = [marks_of(bout(ev("takedown", "Single Leg", successful=True),
                        ev("pass", "Knee Cut", successful=True),
                        ev("control", "Back Control", successful=True)))]
    exp = expected_points(ms, "ibjjf", doc, horizon=3)
    assert exp is not None
    assert (exp["TKD"]["lo"], exp["TKD"]["hi"]) == (7.0, 7.0)
    assert exp["GPS"]["lo"] == 4.0
    assert exp["BTK"]["lo"] == 0.0                  # absorbing here: nothing downstream

    # The one table difference propagates: ADCC pays 3 for the back-take, IBJJF 4.
    adcc = expected_points(ms, "adcc", doc, horizon=3)
    assert adcc is not None
    assert (adcc["TKD"]["lo"], adcc["GPS"]["lo"]) == (6.0, 3.0)


def test_expected_points_reward_lands_on_entering_not_on_occupying(doc: dict[str, Any]) -> None:
    """A self-loop pays again; a dwell does not. Two consecutive passes, horizon 1."""
    ms = [marks_of(bout(ev("pass", "Knee Cut", successful=True),
                        ev("pass", "Leg Drag", successful=True)))]
    exp = expected_points(ms, "ibjjf", doc, horizon=1)
    assert exp is not None and exp["GPS"]["lo"] == 3.0


def test_expected_points_interval_widens_with_the_annotation_gap(doc: dict[str, Any]) -> None:
    ms = [marks_of(bout(ev("takedown", "Single Leg", successful=True),
                        ev("pass", "Knee Cut")))]           # pass unannotated
    exp = expected_points(ms, "ibjjf", doc, horizon=1)
    assert exp is not None
    assert exp["TKD"]["lo"] == 0.0 and exp["TKD"]["hi"] == 3.0


# ── the falsifiable check ──────────────────────────────────────────────────────

def test_winner_agreement_counts_the_inferred_leader() -> None:
    won = bout(ev("takedown", "Single Leg", "X", successful=True),
               ev("pass", "Knee Cut", "X", successful=True), winner="X")
    r = winner_agreement([won], "ibjjf")
    assert (r["strict"]["k"], r["strict"]["n"]) == (1, 1)

    lost = bout(ev("takedown", "Single Leg", "X", successful=True), winner="Y", id_="c")
    r2 = winner_agreement([won, lost], "ibjjf")
    assert (r2["strict"]["k"], r2["strict"]["n"]) == (1, 2)


def test_winner_agreement_excludes_submissions_and_draws() -> None:
    subbed = bout(ev("takedown", "Single Leg", "X", successful=True), win_type="SUBMISSION")
    drawn = bout(ev("takedown", "Single Leg", "X", successful=True), win_type="DRAW", id_="d")
    r = winner_agreement([subbed, drawn], "ibjjf")
    assert r["strict"]["n"] == 0
    assert r["bouts_skipped"] == {"SUBMISSION": 1, "DRAW": 1}


def test_winner_agreement_strict_and_lenient_bracket_each_other() -> None:
    """X's takedown is unannotated: strict cannot see it, lenient credits it."""
    b = bout(ev("takedown", "Single Leg", "X"), winner="X")
    r = winner_agreement([b], "ibjjf")
    assert (r["strict"]["n"], r["strict"]["ties_or_scoreless"]) == (0, 1)
    assert (r["lenient"]["k"], r["lenient"]["n"]) == (1, 1)


def test_winner_agreement_refuses_a_family_with_no_point_table() -> None:
    b = bout(ev("takedown", "Single Leg", "X", successful=True), event="CJI 2 - Day 1")
    r = winner_agreement([b], "cji")
    assert r["applicable"] is False and r["reason_code"] == "family_has_no_point_table"


# ── the census ─────────────────────────────────────────────────────────────────

def test_census_buckets_are_mutually_exclusive_and_total() -> None:
    bouts = [
        bout(ev("takedown", "Single Leg", points=2), id_="a"),               # has_event_points
        bout(ev("takedown", "Single Leg"), win_type="POINTS", id_="b"),      # has_declared_score
        bout(ev("takedown", "Single Leg", successful=True), id_="c"),        # partial tally
        bout(ev("control", "Collar Tie"), id_="d"),                          # unrecoverable
    ]
    row = census(bouts)["families"]["ibjjf"]
    assert [row[k] for k in CENSUS_BUCKETS] == [1, 1, 1, 1]
    assert sum(row[k] for k in CENSUS_BUCKETS) == row["bouts"] == 4


def test_census_reads_points_from_timeline_as_well_as_sequence() -> None:
    b = bout(ev("control", "Collar Tie"), timeline=[{"type": "takedown", "points": 2}])
    assert census([b])["families"]["ibjjf"]["has_event_points"] == 1


def test_census_footage_is_a_separate_axis_not_a_bucket() -> None:
    """The feasibility column must stay visible for a bout that ALSO has a partial tally —
    folding it into the bucket ladder would hide exactly the rescuable rows."""
    b = bout(ev("takedown", "Single Leg", successful=True),
             video_url="https://y", video_start_seconds=12)
    row = census([b])["families"]["ibjjf"]
    assert row["partial_tally_possible"] == 1
    assert (row["footage"], row["footage_with_start"]) == (1, 1)
    assert row["footage_by_bucket"]["partial_tally_possible"] == 1


def test_census_families_without_a_point_table_cannot_hold_a_partial_tally() -> None:
    b = bout(ev("takedown", "Single Leg", successful=True), event="CJI 2 - Day 1")
    row = census([b])["families"]["cji"]
    assert row["has_point_table"] is False
    assert (row["partial_tally_possible"], row["unrecoverable"]) == (0, 1)


# ── comparability: the flag that governs everything downstream ─────────────────

def test_annotation_coverage_counts_presence_separately_from_truth() -> None:
    a = annotation_coverage([bout(ev("takedown", "A", successful=True),
                                  ev("takedown", "B", successful=False),
                                  ev("takedown", "C"))])
    assert (a.events, a.present, a.landed) == (3, 2, 1)
    assert (a.present_pct, a.landed_pct) == (66.7, 33.3)


def test_comparability_refuses_the_point_estimate_across_families() -> None:
    by = {"ibjjf": annotation_coverage([bout(ev("takedown", "A", successful=True))]),
          "adcc": annotation_coverage([bout(ev("takedown", "A"), event="ADCC 2024")])}
    c = comparability(by)
    assert c["landing_rate_cross_family_comparable"] is False
    assert c["envelope_cross_family_comparable"] is True
    assert c["present_pct_spread"] == 100.0
    assert "⚠️" in c["warning"]


def test_bout_concentration_sees_what_the_cluster_gate_cannot() -> None:
    """Five bouts clears the cluster gate. One of them carrying nine tenths of the events is
    what the gate cannot say, and is what this reports."""
    lopsided = [bout(*[ev("takedown", "Single Leg")] * 90, id_="big"),
                *[bout(ev("takedown", "Single Leg"), id_=f"s{i}") for i in range(10)]]
    c = bout_concentration(lopsided)
    assert (c["bouts_with_events"], c["events"], c["clusters"]) == (11, 100, 11)
    assert c["top_share"] == 0.9
    assert c["effective_n"] < 2                    # eleven fights worth less than two
    assert c["estimable"] is False and c["reason_code"] == "dominated"


def test_athlete_coverage_credits_both_corners_and_counts_unattributed() -> None:
    a = athlete_coverage([
        {"a_id": "X", "b_id": "Y", "seq": [1, 2]},
        {"a_id": None, "b_id": None, "seq": [1]},
    ])
    assert (a["athletes"], a["events_unattributed"]) == (2, 1)


def test_truncation_measures_the_one_annotation_channel_into_occupancy() -> None:
    """A submission win whose finish carries no flag is NOT truncated, so its tail survives
    into the chain. That is the only way the annotation batch reaches the 7-symbol reading."""
    flagged = bout(ev("submission", "RNC", successful=True),
                   ev("takedown", "Single Leg"), win_type="SUBMISSION", id_="f")
    unflagged = bout(ev("submission", "RNC"),
                     ev("takedown", "Single Leg"), win_type="SUBMISSION", id_="u")
    t = truncation([flagged, unflagged])
    assert t["won_by_submission"] == 2
    assert (t["bouts_truncated"], t["unflagged_finishes"]) == (1, 1)
    assert t["events_after_finish"] == 1


# ── the contrast ───────────────────────────────────────────────────────────────

def _mk(sym: str, n: int, landed: int, bouts: int, annotated: int | None = None
        ) -> list[list[Mark]]:
    """`n` appearances of `sym` over `bouts` bouts: `landed` landed, `annotated` carrying the
    flag at all (default: all of them, i.e. a zero-width envelope)."""
    annotated = n if annotated is None else annotated
    out: list[list[Mark]] = [[] for _ in range(bouts)]
    for i in range(n):
        out[i % bouts].append(Mark(f"b{i % bouts}", sym, i < landed, i < annotated, "X"))
    return out


def test_contrast_reports_disjoint_envelopes_as_the_only_landing_claim() -> None:
    # Everything annotated on both sides, so each envelope has zero width: 0.9 vs 0.1.
    a = per_action_contrast(_mk("TKD", 10, 9, 5), _mk("TKD", 10, 1, 5), "ibjjf", "adcc")
    row = next(r for r in a["rows"] if r["symbol"] == "TKD")
    assert (row["landing_a"]["lo"], row["landing_a"]["hi"]) == (0.9, 0.9)
    assert (row["landing_b"]["lo"], row["landing_b"]["hi"]) == (0.1, 0.1)
    assert row["landing_separated"] is True
    assert row["occupancy_gated"] is True


def test_contrast_refuses_the_landing_claim_when_envelopes_overlap() -> None:
    # Nothing annotated on the B side: its envelope is the whole [0, 1], so no claim survives
    # even though its complete-case rate is undefined and its point estimate looks like zero.
    a = per_action_contrast(_mk("TKD", 10, 9, 5), _mk("TKD", 10, 0, 5, annotated=0),
                            "ibjjf", "adcc")
    row = next(r for r in a["rows"] if r["symbol"] == "TKD")
    assert (row["landing_b"]["lo"], row["landing_b"]["hi"]) == (0.0, 1.0)
    assert row["landing_b"]["cc"] is None
    assert row["landing_separated"] is False
    assert row["landing_verdict"] == "envelopes overlap — no claim"


def test_contrast_ungated_when_either_side_lacks_bout_clusters() -> None:
    a = per_action_contrast(_mk("TKD", 10, 9, 5), _mk("TKD", 4, 2, 2), "ibjjf", "adcc")
    row = next(r for r in a["rows"] if r["symbol"] == "TKD")
    assert row["occupancy_gated"] is False
    assert "few_clusters" in (row["occupancy_reason_code"] or "")


def test_contrast_corrects_only_the_gated_rows_for_multiplicity() -> None:
    """An ungated row's p-value was never published, so counting it in `m` would make the
    surviving rows look better than they are."""
    def merge(*arms: list[list[Mark]]) -> list[list[Mark]]:
        return [[m for part in bouts for m in part]
                for bouts in zip_longest(*arms, fillvalue=[])]

    # TKD is gated on both sides (5 bouts each); GPS is not (2 bouts on the adcc side).
    a = per_action_contrast(
        merge(_mk("TKD", 10, 9, 5), _mk("GPS", 4, 2, 4)),
        merge(_mk("TKD", 10, 1, 5), _mk("GPS", 2, 1, 2)),
        "ibjjf", "adcc")
    rows = {r["symbol"]: r for r in a["rows"]}
    assert a["multiplicity"]["tests"] == sum(1 for r in a["rows"] if r["occupancy_gated"])
    assert a["multiplicity"]["method"] == "benjamini-hochberg"
    assert rows["GPS"]["occupancy_gated"] is False
    assert rows["GPS"]["occupancy_q"] is None
    assert rows["GPS"]["occupancy_survives_bh"] is False
    assert rows["TKD"]["occupancy_q"] >= rows["TKD"]["occupancy"]["p_value"]


def test_contrast_names_the_one_symbol_the_tables_disagree_on() -> None:
    a = per_action_contrast(_mk("BTK", 10, 5, 5), _mk("BTK", 10, 5, 5), "ibjjf", "adcc")
    assert a["points_table_differs_on"] == ["BTK"]
    assert a["primary"] == "occupancy"
    row = next(r for r in a["rows"] if r["symbol"] == "BTK")
    assert (row["points_a"], row["points_b"], row["points_differ"]) == (4, 3, True)


# ── the ADCC window refusal ────────────────────────────────────────────────────

def test_adcc_window_needs_a_stage_and_a_clock_together() -> None:
    stage_only = {"stage": "F", "ts_origin": None, "seq": [{"ts": 10}]}
    clock_only = {"stage": None, "ts_origin": "bout_relative", "seq": [{"ts": 10}]}
    f = adcc_clock_feasibility([stage_only, clock_only])
    assert (f["with_stage"], f["with_usable_clock"], f["both"]) == (1, 1, 0)
    assert f["window_applicable"] is False
    assert f["reason_code"] == "no_bout_has_both_stage_and_clock"


def test_video_absolute_needs_a_bout_start_to_count_as_a_clock() -> None:
    no_start = {"stage": "F", "ts_origin": "video_absolute", "seq": [{"ts": 10}]}
    with_start = {**no_start, "video_start_seconds": 600}
    assert adcc_clock_feasibility([no_start])["both"] == 0
    assert adcc_clock_feasibility([with_start])["both"] == 1


def test_null_ts_origin_is_never_read_as_a_default() -> None:
    """`Match.ts_origin` NULL means 'nobody established which clock'. Guessing is AA-010."""
    assert adcc_clock_feasibility(
        [{"stage": "F", "ts_origin": None, "seq": [{"ts": 10}]}])["with_usable_clock"] == 0


# ── the assembled report ───────────────────────────────────────────────────────

def test_family_report_partitions_the_bouts_and_keeps_tableless_families() -> None:
    bouts = [
        bout(ev("takedown", "Single Leg", successful=True), id_="i", event="IBJJF Worlds 2023"),
        bout(ev("pass", "Knee Cut", successful=True), id_="a", event="ADCC 2024"),
        bout(ev("control", "Back Control"), id_="c", event="CJI 2 - Day 1"),
        bout(ev("guard", "Guard Pull"), id_="u", event=None),
    ]
    r = family_report(bouts)
    assert set(r["families"]) == {"ibjjf", "adcc", "cji", "unknown"}
    assert sum(f["bouts"] for f in r["families"].values()) == r["census"]["total_bouts"] == 4
    # A tableless family is a RESULT, so it keeps its block with the answer spelled `None`.
    assert r["families"]["cji"]["expected_points"] is None
    assert r["families"]["cji"]["winner_agreement"]["applicable"] is False
    assert r["contrast"]["families"] == ["ibjjf", "adcc"]
    assert r["negative_window_applied"] is False


def test_family_report_contrast_is_none_when_an_arm_is_missing() -> None:
    r = family_report([bout(ev("takedown", "Single Leg"), event="CJI 2 - Day 1")])
    assert r["contrast"] is None
