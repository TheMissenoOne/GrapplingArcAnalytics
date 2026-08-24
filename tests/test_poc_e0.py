"""PoC-E0 harness — the instrument has to be trustworthy before any engine is judged by it."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from analysis.poc.e0_rating_eval import (
    K_SWEEP,
    AthleteEloEngine,
    Bout,
    ConstantBaseline,
    EloEngine,
    Glicko2PerBout,
    Glicko2Yearly,
    Scored,
    WinRateBaseline,
    _metrics,
    default_engines,
    evaluate,
    load_db_bouts,
    load_scouting_bouts,
    method_win_type,
    run_k_sweep,
)


# ── metric sanity ───────────────────────────────────────────────────────────────
def test_constant_half_scores_ln2() -> None:
    rows = [Scored(0.5, s, 2024, True) for s in (1.0, 0.0, 1.0, 0.0)]
    m = _metrics(rows, n_boot=100)
    assert math.isclose(m["log_loss"], math.log(2), rel_tol=1e-9)
    assert math.isclose(m["brier"], 0.25, rel_tol=1e-9)
    assert m["accuracy"] == 0.5  # p == 0.5 earns half credit, not a coin flip


def test_perfect_predictions_score_near_zero() -> None:
    rows = [Scored(0.999999, 1.0, 2024, True), Scored(0.000001, 0.0, 2024, True)]
    m = _metrics(rows, n_boot=100)
    assert m["log_loss"] < 1e-4
    assert m["accuracy"] == 1.0


# ── corpus construction ─────────────────────────────────────────────────────────
def test_scouting_loader_folds_mirrors_and_orients_blind() -> None:
    bouts, dropped = load_scouting_bouts()
    # 725 athlete-perspective rows, 1 draw; mirror folding keeps distinct bouts only
    assert dropped["draws"] == 1
    assert dropped["mirrors_folded"] > 0
    assert len(bouts) == 724 - dropped["mirrors_folded"]
    # orientation must carry no label: 'a' is the lexicographically smaller key,
    # so score_a must be a genuine mix, not all 1.0
    scores = {b.score_a for b in bouts}
    assert scores == {0.0, 1.0}
    for b in bouts:
        assert b.a < b.b


def test_loader_is_deterministic() -> None:
    b1, _ = load_scouting_bouts()
    b2, _ = load_scouting_bouts()
    assert b1 == b2


def test_method_win_type_families() -> None:
    assert method_win_type("Pts: 4x0") == "points"
    assert method_win_type("Referee Decision") == "decision"
    assert method_win_type("Kneebar") == "submission"
    assert method_win_type("DQ") == "points"  # neutral multiplier, not a finish
    assert method_win_type(None) == "points"


# ── the harness separates skill from noise on a known corpus ────────────────────
def _synthetic(n_rounds: int = 40) -> list[Bout]:
    """3 strong vs 3 weak; strong wins deterministically-pseudorandomly ~85%.
    No RNG import: a fixed hash pattern keeps the corpus identical every run."""
    strong = ["s1", "s2", "s3"]
    weak = ["w1", "w2", "w3"]
    bouts = []
    i = 0
    for year in (2020, 2021, 2022, 2023):
        for _ in range(n_rounds // 4):
            for s in strong:
                for w in weak:
                    i += 1
                    upset = (i * 2654435761) % 100 < 15  # ~15% upsets, deterministic
                    a, b = sorted((s, w))
                    strong_is_a = a == s
                    score_a = (0.0 if strong_is_a else 1.0) if upset \
                        else (1.0 if strong_is_a else 0.0)
                    bouts.append(Bout(a, b, score_a, year, "Pts: 2x0", "F", f"E{i}"))
    bouts.sort(key=lambda x: (x.year, x.comp, x.stage, x.a, x.b))
    return bouts


def test_rating_engines_beat_baselines_on_separable_corpus() -> None:
    bouts = _synthetic()
    reports = {r.name: r for r in evaluate(bouts, default_engines(taus=(0.5,)), n_boot=100)}
    const = reports["constant-0.5"].overall["log_loss"]
    winrate = reports["win-rate"].overall["log_loss"]
    elo = reports["elo-k40"].overall["log_loss"]
    glicko = reports["glicko2-tau0.5"].overall["log_loss"]
    assert winrate < const, "win-rate must beat the coin on a separable corpus"
    assert elo < const and glicko < const
    # both rating engines should call the direction right nearly always
    assert reports["elo-k40"].overall["accuracy"] > 0.8
    assert reports["glicko2-tau0.5"].overall["accuracy"] > 0.8


def test_evaluation_is_prequential_not_leaky() -> None:
    """An engine that only ever predicts 0.5 must score exactly ln 2 — if the
    harness leaked outcomes into predictions, this would drift."""
    bouts = _synthetic()
    rep = evaluate(bouts, [ConstantBaseline()], n_boot=100)[0]
    assert math.isclose(rep.overall["log_loss"], math.log(2), rel_tol=1e-9)


def test_burn_in_year_is_not_scored() -> None:
    bouts = _synthetic()
    rep = evaluate(bouts, [ConstantBaseline()], n_boot=100)[0]
    years_in_corpus = {b.year for b in bouts}
    scored_n = rep.overall["n"]
    first_year_n = sum(1 for b in bouts if b.year == min(years_in_corpus))
    assert scored_n == len(bouts) - first_year_n


def test_glicko_periods_close_on_year_boundaries() -> None:
    g = Glicko2Yearly(tau=0.5)
    b = Bout("a", "b", 1.0, 2020, "Pts", "F", "E1")
    p_seed = g.predict("a", "b")
    g.observe(b)
    # same year: prediction unchanged (period not closed)
    assert g.predict("a", "b") == p_seed
    g.close_year()
    assert g.predict("a", "b") > p_seed, "a's period win must raise P(a) after the close"


def test_elo_zero_sum_and_direction() -> None:
    e = EloEngine(use_mults=False)
    e.observe(Bout("a", "b", 1.0, 2020, "Pts", "F", "E1"))
    assert e.r["a"] > 1000.0 > e.r["b"]
    assert math.isclose(e.r["a"] + e.r["b"], 2000.0, rel_tol=1e-12)


def test_winrate_baseline_moves_with_evidence() -> None:
    w = WinRateBaseline()
    assert w.predict("a", "b") == 0.5
    w.observe(Bout("a", "b", 1.0, 2020, "Pts", "F", "E1"))
    assert w.predict("a", "b") > 0.5


# ── --source db loader ───────────────────────────────────────────────────────────
def _db_row(
    a: str = "a1", b: str = "b1", winner: str | None = "a1", year: int | None = 2020,
    win_type: str | None = "SUBMISSION", stage: str = "F", event: str = "E1",
    status: str = "final", created_at: datetime | None = None,
    sequence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "athlete_a_id": a, "athlete_b_id": b, "winner_id": winner, "year": year,
        "win_type": win_type, "stage": stage, "event": event, "status": status,
        "sequence": sequence if sequence is not None else [], "created_at": created_at,
    }


def test_db_loader_filters_draws_dq_and_non_final() -> None:
    rows = [
        _db_row(win_type="SUBMISSION"),                    # eligible
        _db_row(win_type="DRAW", winner=None),              # excluded win_type
        _db_row(win_type="DQ"),                              # excluded win_type
        _db_row(win_type="DECISION", winner=None),           # no winner
        _db_row(status="draft"),                             # not final
        _db_row(year=None),                                  # no year
        _db_row(win_type="POINTS"),                          # eligible
    ]
    bouts, dropped = load_db_bouts(rows)
    assert len(bouts) == 2
    assert dropped == {"not_final": 1, "excluded_win_type": 2, "no_winner": 1, "no_year": 1}


def test_db_loader_orients_blind_and_scores_winner() -> None:
    rows = [_db_row(a="zzz", b="aaa", winner="zzz")]
    bouts, _ = load_db_bouts(rows)
    (b,) = bouts
    assert b.a == "aaa" and b.b == "zzz"  # a is the lexicographically smaller id
    assert b.score_a == 0.0  # zzz (== b) won, not a


def test_db_loader_orders_by_year_then_created_at() -> None:
    rows = [
        _db_row(a="a", b="b1", year=2021, created_at=datetime(2021, 1, 1)),
        _db_row(a="a", b="b2", year=2020, created_at=datetime(2020, 6, 1)),
        _db_row(a="a", b="b3", year=2020, created_at=datetime(2020, 1, 1)),
    ]
    bouts, _ = load_db_bouts(rows)
    assert [b.year for b in bouts] == [2020, 2020, 2021]
    assert bouts[0].b == "b3" and bouts[1].b == "b2"  # earlier created_at first, within year


def test_db_loader_carries_sequence_for_the_graph_engine() -> None:
    events = [{"label": "armbar", "type": "submission", "actor_id": "a1"}]
    rows = [_db_row(sequence=events)]
    bouts, _ = load_db_bouts(rows)
    assert bouts[0].sequence == tuple(events)


# ── per-bout-period Glicko-2 ─────────────────────────────────────────────────────
def test_glicko_per_bout_closes_without_close_year() -> None:
    g = Glicko2PerBout(tau=0.5)
    b = Bout("a", "b", 1.0, 2020, "Pts", "F", "E1")
    p_seed = g.predict("a", "b")
    g.observe(b)
    # unlike Glicko2Yearly, the period is already closed -- no close_year() needed
    assert g.predict("a", "b") > p_seed


def test_default_engines_include_both_glicko_period_granularities() -> None:
    names = {e.name for e in default_engines(taus=(0.5,))}
    assert "glicko2-tau0.5" in names          # yearly
    assert "glicko2-perbout-tau0.5" in names  # per-bout


# ── athlete_elo graph-growth engine (db-only) ────────────────────────────────────
def test_athlete_elo_engine_predicts_and_grows_toward_winner() -> None:
    events = [{"label": "armbar", "type": "submission", "actor_id": "a1", "successful": True}]
    engine = AthleteEloEngine(rank_targets={"a1": 900.0, "b1": 900.0})
    p_seed = engine.predict("a1", "b1")
    assert 0.0 <= p_seed <= 1.0
    engine.observe(Bout("a1", "b1", 1.0, 2020, "SUBMISSION", "F", "E1", sequence=tuple(events)))
    assert "a1" in engine.mean  # winner's side contributed a node -> graph mean recorded


def test_athlete_elo_engine_skips_bouts_without_sequence() -> None:
    engine = AthleteEloEngine(rank_targets={})
    engine.observe(Bout("a1", "b1", 1.0, 2020, "SUBMISSION", "F", "E1"))
    assert engine.mean == {}  # no sequence -> nothing to replay, no crash


# ── K sweep ───────────────────────────────────────────────────────────────────────
def test_k_sweep_covers_full_grid_flat_and_mult() -> None:
    bouts = _synthetic()
    rows = run_k_sweep(bouts, n_boot=50)
    assert len(rows) == len(K_SWEEP) * 2
    assert {k for k, _, _ in rows} == set(K_SWEEP)
    assert {label for _, label, _ in rows} == {"flat", "mult"}
    for _, _, rep in rows:
        assert rep.overall["n"] > 0
        assert "log_loss" in rep.overall


# ── committed user-export fixture (owner-consented; see analysis/poc/fixtures.py) ──
def test_user_export_fixture_shape() -> None:
    from analysis.poc.fixtures import load_user_export_slice, user_export_rounds
    data = load_user_export_slice()
    assert set(data) >= {"scope", "selected_current_node_metrics", "rounds"}
    rounds = user_export_rounds()
    assert len(rounds) == 27
    events = [e for r in rounds for e in r.get("events", [])]
    assert len(events) == 136
    assert all(e["actor"] in ("you", "partner") for e in events)
    assert all(r["outcome"] in ("succeeded", "failed", "partial", "no_attempt")
               for r in rounds)
    # the app export is V2-scale: userElo in the Glicko band, not the 100-700 V1 band
    assert 1000 < data["scope"]["full_export_graph_summary"]["userElo"] < 1800
