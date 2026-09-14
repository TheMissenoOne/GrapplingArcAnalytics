"""PoC-E3 WHR — the math, on synthetic data with answers known in advance.

The engine is only worth a verdict if the fit is right, so these assert properties that
follow from Coulom 2008's model rather than re-deriving numbers from a previous run:
symmetry of the anchor, monotonicity in wins, the Wiener prior's behaviour in time, and
the one thing a prequential harness cannot tolerate — a prediction that has seen its own
result. Tiny corpora, no DB, no files.
"""

from __future__ import annotations

import math

from analysis.poc.e0_rating_eval import Bout, stream_scores
from scripts.research.e0_ksweep_whr import (
    ELO_SCALE,
    SharedPrediction,
    WhrEngine,
    WhrModel,
    paired_delta,
    split_years,
)


def _bout(a: str, b: str, score_a: float, year: int) -> Bout:
    return Bout(a=a, b=b, score_a=score_a, year=year, method="", stage="", comp="")


# ── the fit ─────────────────────────────────────────────────────────────────────
def test_single_game_is_mirror_symmetric_about_the_anchor() -> None:
    """Both players carry the same virtual win + virtual loss at rating 0, so the only
    asymmetry in the posterior is the result itself: the pair must mirror."""
    m = WhrModel(80.0)
    m.add_game("a", "b", 1.0, 2020)
    m.fit()
    ra, rb = m.rating["a"][2020], m.rating["b"][2020]
    assert ra > 0 > rb
    assert abs(ra + rb) < 1e-4


def test_rating_is_monotone_in_wins_and_stays_finite() -> None:
    """Undefeated is not infinite — that is exactly what the virtual loss buys."""
    ratings = []
    for n_wins in (1, 5, 20):
        m = WhrModel(80.0)
        for i in range(n_wins):
            m.add_game("champ", f"opp{i}", 1.0, 2020)
        m.fit()
        ratings.append(m.rating["champ"][2020])
    assert ratings[0] < ratings[1] < ratings[2]
    assert math.isfinite(ratings[-1]) and ratings[-1] < 10.0


def test_wiener_prior_ties_a_trajectory_tightly_when_drift_is_small() -> None:
    """w -> 0 collapses WHR toward a single time-invariant Bradley-Terry rating;
    a large w lets the same win/loss history bend into two different years."""
    def spread(w_elo: float) -> float:
        m = WhrModel(w_elo)
        for i in range(6):
            m.add_game("x", f"early{i}", 1.0, 2010)
            m.add_game("x", f"late{i}", 0.0, 2020)
        m.fit()
        return m.rating["x"][2010] - m.rating["x"][2020]

    assert spread(5.0) < 0.05
    assert spread(400.0) > spread(5.0)


def test_inactivity_preserves_the_rating_and_widens_the_variance() -> None:
    m = WhrModel(160.0)
    m.add_game("a", "b", 1.0, 2020)
    m.fit()
    r_now, v_now = m.state_at("a", 2020)
    r_later, v_later = m.state_at("a", 2030)
    assert r_later == r_now
    # the Wiener prior adds exactly w^2 * dt of drift variance
    assert math.isclose(v_later - v_now, (160.0 / ELO_SCALE) ** 2 * 10, rel_tol=1e-9)


# ── prediction ──────────────────────────────────────────────────────────────────
def test_deflation_pulls_toward_a_half_without_crossing_it() -> None:
    e = WhrEngine(80.0)
    e.observe(_bout("a", "b", 1.0, 2020))
    p_deflated = e.predict("a", "b")
    p_map = SharedPrediction(e, deflate=False, name="map").predict("a", "b")
    assert 0.5 < p_deflated < p_map < 1.0


def test_unseen_players_predict_exactly_even() -> None:
    assert WhrEngine(80.0).predict("nobody", "stranger") == 0.5


# ── the harness contract ────────────────────────────────────────────────────────
def test_prediction_never_sees_its_own_result() -> None:
    """The leak test, built so a leak and a correct fit give OPPOSITE answers.

    'a' beats 'b' in 2018 and loses to 'b' in 2019. Predicting the 2019 bout from 2018
    alone must favour 'a' (> 0.5). An engine that had already absorbed the 2019 result
    would see one win each and return exactly 0.5 — so > 0.5 is only reachable without
    the leak. Both refit cadences are checked, because they close the fit at different
    moments.
    """
    bouts = [_bout("a", "b", 1.0, 2018), _bout("a", "b", 0.0, 2019)]
    for engine in (WhrEngine(80.0, refit="year"), WhrEngine(80.0, refit="bout")):
        rows = stream_scores(bouts, [engine])[engine.name]
        assert len(rows) == 1, "first corpus year is burn-in"
        assert rows[0].p > 0.5, engine.name


def test_shared_prediction_reads_the_same_state_as_its_owner() -> None:
    """stream_scores predicts with every arm before observing with any, so the shadow
    arm's no-op observe is not a leak — it must track the owner bout for bout."""
    owner = WhrEngine(80.0)
    shadow = SharedPrediction(owner, deflate=True, name="whr-shadow")
    bouts = [_bout("a", "b", 1.0, 2019), _bout("a", "c", 1.0, 2020),
             _bout("b", "c", 0.0, 2021)]
    rows = stream_scores(bouts, [owner, shadow])
    assert [r.p for r in rows["whr-w80"]] == [r.p for r in rows["whr-shadow"]]


# ── comparison machinery ────────────────────────────────────────────────────────
def test_paired_delta_is_zero_and_tight_for_an_arm_against_itself() -> None:
    bouts = [_bout("a", "b", float(i % 2), 2018 + i) for i in range(8)]
    rows = stream_scores(bouts, [WhrEngine(80.0)])["whr-w80"]
    d = paired_delta(rows, rows, n_boot=100)
    assert d["delta"] == 0.0 and d["lo"] == 0.0 and d["hi"] == 0.0


def test_paired_delta_signs_the_better_arm_negative() -> None:
    bouts = [_bout("a", "b", 1.0, 2018 + i) for i in range(10)]
    rows = stream_scores(bouts, [WhrEngine(80.0), WhrEngine(80.0, deflate=False)])
    good, bad = rows["whr-w80"], rows["whr-w80-map"]
    # 'a' always wins, so the sharper (undeflated) arm is the better one here
    assert paired_delta(bad, good, n_boot=100)["delta"] < 0


def test_tune_test_split_is_chronological_and_non_overlapping() -> None:
    bouts = [_bout(f"a{i}", f"b{i}", 1.0, 2018 + i // 3) for i in range(18)]
    rows = stream_scores(bouts, [WhrEngine(80.0, refit="year")])["whr-w80-year"]
    tune, test = split_years(rows)
    assert tune and test
    assert max(tune) < min(test)
    assert set(tune) | set(test) == {r.year for r in rows}
