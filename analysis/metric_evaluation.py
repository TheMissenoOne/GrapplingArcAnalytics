"""Metric-value study — is each generated metric predictive, or redundant?

Assembles a per-athlete feature matrix of every metric we generate, correlates each
against external outcomes, ranks them by RandomForest importance, and flags redundant
pairs — then recommends Keep / Refine / Merge / Remove per metric. **Report only**: it
changes no metric and touches no other table.

Method (grounded in sports-analytics practice — Terner & Franks 2020, *Modeling Player
and Team Performance*; RF/SHAP importance per Ouyang 2025): Pearson + Spearman vs
outcomes, RandomForest feature importance, pairwise-|r| redundancy. External outcomes
(win rate, submission rate, ADCC rank-ELO) are the credible targets; PtV and positional
dominance are included but **leakage-flagged** — a feature from the same family
correlating with them is tautological, not predictive.

    uv run python -m analysis.metric_evaluation            # -> docs/metric_value_report.md
    uv run python -m analysis.metric_evaluation --min-matches 4
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.ensemble import RandomForestRegressor

logger = logging.getLogger(__name__)

REPORT = Path(__file__).resolve().parents[1] / "docs" / "metric_value_report.md"

# External, non-derived outcomes = the credible targets.
PRIMARY_OUTCOMES = ["win_rate", "sub_rate", "rank_elo"]
# Included per request but derived from the same graphs → leakage-prone.
SECONDARY_OUTCOMES = ["avg_ptv", "positional_dominance"]
OUTCOMES = PRIMARY_OUTCOMES + SECONDARY_OUTCOMES

# Feature → the outcome it is (near-)derived from; a high corr there is tautological.
LEAKAGE = {
    "mean_ptv": "avg_ptv", "max_ptv": "avg_ptv", "mean_control": "avg_ptv",
    "mean_funnel": "avg_ptv", "mean_deviance": "avg_ptv",
    "mean_attacker": "positional_dominance", "mean_defender": "positional_dominance",
    "grown_elo": "rank_elo",
}

REDUNDANCY_R = 0.85   # |Pearson r| above which two features are near-duplicates
LOW_CORR = 0.15       # |r| below which a feature carries little outcome signal
LOW_IMPORTANCE = 0.03  # RF importance below which a feature is weak

# Data-volume confound: elite athletes have more transcribed matches → bigger graphs.
# We control for it so "more logged data" isn't mistaken for a predictive metric.
CONTROL = "n_matches"


# ── per-athlete feature assembly ─────────────────────────────────────────────

def _athlete_features(athlete: Any, matches: list[Any]) -> dict[str, float] | None:
    """One row of metrics + outcomes for an athlete, or None if too little data."""
    from analysis.decision_space import position_decision_space, sequence_decision_space
    from analysis.network_metrics import network_from_sequences, node_centralities
    from analysis.path_to_victory import (
        continuity,
        control_score,
        decision_funnel,
        dilemmas,
        node_elo_deviance,
        path_to_victory,
    )

    seqs = [m.sequence for m in matches if m.sequence]
    if not seqs:
        return None
    g = network_from_sequences(seqs)
    if g.number_of_nodes() < 3:
        return None

    ptv = path_to_victory(g)
    cent = node_centralities(g)
    control = control_score(g, ptv)
    cont = continuity(g)
    funnel = decision_funnel(g, ptv)
    dev = node_elo_deviance(g, ptv)
    dils = dilemmas(g, ptv)

    def _mean(d: dict[str, float]) -> float:
        return float(np.mean(list(d.values()))) if d else 0.0

    # reward_risk is a NODE attribute set by network_from_sequences, NOT in node_centralities.
    reward_risk = [g.nodes[n].get("reward_risk", 0.0) for n in g]
    pagerank = [c.get("pagerank", 0.0) for c in cent.values()]

    # Positional dominance = mean attacker_score of the positions THIS athlete drives;
    # turning points come from the side-tagged sequence decision space.
    atk: list[float] = []
    turns = 0
    for m in matches:
        if not m.sequence:
            continue
        for e in m.sequence:
            if e.get("actor_id") == athlete.id:
                atk.append(position_decision_space(str(e.get("type", "")))["attacker_score"])
        tagged = [
            {**e, "side": "a" if e.get("actor_id") == athlete.id else "b"} for e in m.sequence
        ]
        turns += len(sequence_decision_space(tagged)["turning_points"])
    mean_atk = float(np.mean(atk)) if atk else 0.0

    # outcomes
    n = len(matches)
    wins = sum(1 for m in matches if m.winner_id == athlete.id)
    subs = sum(
        1 for m in matches
        if m.winner_id == athlete.id and (m.win_type or "").upper() == "SUBMISSION"
    )

    return {
        # ── features (metrics we generate) ──
        "mean_ptv": _mean(ptv),
        "max_ptv": max(ptv.values()) if ptv else 0.0,
        "mean_pagerank": float(np.mean(pagerank)) if pagerank else 0.0,
        "mean_reward_risk": float(np.mean(reward_risk)) if reward_risk else 0.0,
        "mean_control": _mean(control),
        "mean_continuity": _mean(cont),
        "mean_funnel": _mean(funnel),
        "mean_deviance": float(np.mean([abs(v) for v in dev.values()])) if dev else 0.0,
        "n_dilemmas": float(len(dils)),
        "n_nodes": float(g.number_of_nodes()),
        "n_edges": float(g.number_of_edges()),
        "mean_attacker": mean_atk,
        "mean_defender": 1.0 - mean_atk,
        "turning_points": float(turns),
        "grown_elo": float(athlete.elo or 0.0),
        "n_matches": float(n),  # data-volume covariate — controlled for in partial corr / RF
        # ── outcomes ──
        "win_rate": wins / n,
        "sub_rate": subs / n,
        "rank_elo": float(athlete.rank_elo) if athlete.rank_elo else np.nan,
        "avg_ptv": _mean(ptv),
        "positional_dominance": mean_atk,
    }


def build_matrix(session: Any, min_matches: int = 3) -> pd.DataFrame:
    """One row per athlete with ≥``min_matches`` logged bouts."""
    from sqlalchemy import select

    from db.models import Athlete, Match

    by_ath: dict[str, list[Any]] = defaultdict(list)
    for m in session.execute(select(Match).where(Match.status == "final")).scalars():
        by_ath[m.athlete_a_id].append(m)
        by_ath[m.athlete_b_id].append(m)

    rows: list[dict[str, float]] = []
    for ath in session.execute(select(Athlete)).scalars():
        ms = by_ath.get(ath.id, [])
        if len(ms) < min_matches:
            continue
        feats = _athlete_features(ath, ms)
        if feats is not None:
            rows.append({"athlete": ath.name, **feats})
    return pd.DataFrame(rows)


# ── analysis ─────────────────────────────────────────────────────────────────

def _features(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("athlete", *OUTCOMES)]


def _residualize(a: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Residuals of ``a`` after linearly regressing out covariate ``z`` (+ intercept)."""
    design = np.column_stack([np.ones_like(z), z])
    coef, *_ = np.linalg.lstsq(design, a, rcond=None)
    return a - design @ coef


def _partial_corr(x: pd.Series, y: pd.Series, z: pd.Series) -> float:
    """Partial Pearson r(x, y | z) — correlation of x and y after removing z from both."""
    zv = z.to_numpy(float)
    rx, ry = _residualize(x.to_numpy(float), zv), _residualize(y.to_numpy(float), zv)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(pearsonr(rx, ry)[0])


def correlate(df: pd.DataFrame) -> pd.DataFrame:
    """Raw |Pearson| + Spearman + **partial** r (controlling for n_matches) vs each outcome."""
    feats = _features(df)
    recs = []
    for f in feats:
        row: dict[str, Any] = {"metric": f}
        for out in OUTCOMES:
            # Don't duplicate the control column when the feature IS the control.
            cols = [f, out] if f == CONTROL else [f, out, CONTROL]
            sub = df[cols].dropna()
            if len(sub) >= 5 and sub[f].std() > 0 and sub[out].std() > 0:
                r, _ = pearsonr(sub[f], sub[out])
                rho, _ = spearmanr(sub[f], sub[out])
                # Partial only makes sense when the feature isn't the control itself.
                pr = np.nan if f == CONTROL else _partial_corr(sub[f], sub[out], sub[CONTROL])
            else:
                r = rho = pr = np.nan
            row[f"r_{out}"] = round(r, 3)
            row[f"rho_{out}"] = round(rho, 3)
            row[f"pr_{out}"] = round(pr, 3)
        recs.append(row)
    return pd.DataFrame(recs).set_index("metric")


def importances(df: pd.DataFrame) -> pd.DataFrame:
    """RandomForest feature importance for each outcome (rows with a known outcome)."""
    feats = _features(df)
    out = pd.DataFrame(index=feats)
    x_all = df[feats].fillna(0.0)
    for target in OUTCOMES:
        mask = df[target].notna()
        if mask.sum() < 8:
            out[f"imp_{target}"] = np.nan
            continue
        rf = RandomForestRegressor(n_estimators=200, random_state=42)
        rf.fit(x_all[mask], df[target][mask])
        out[f"imp_{target}"] = np.round(rf.feature_importances_, 3)
    return out


def redundancy(df: pd.DataFrame) -> dict[str, tuple[str, float]]:
    """Each feature → its most-correlated sibling and |r| (redundancy signal)."""
    feats = _features(df)
    corr = df[feats].corr().abs()
    out: dict[str, tuple[str, float]] = {}
    for f in feats:
        others = corr[f].drop(f)  # exclude self instead of zeroing the diagonal
        if others.notna().any():   # a constant feature yields all-NaN correlations
            out[f] = (str(others.idxmax()), round(float(others.max()), 3))
        else:
            out[f] = ("—", 0.0)
    return out


def _recommend(metric: str, corr: pd.DataFrame, imp: pd.DataFrame,
               red: dict[str, tuple[str, float]], imp_rank: dict[str, float]) -> tuple[str, str]:
    """Keep / Refine / Merge / Remove + one-line reason, from de-confounded PRIMARY signal."""
    # Partial r (controlling for n_matches) is the de-confounded signal; the control
    # variable itself has no partial r, so judge it on its raw correlation.
    col = "r_" if metric == CONTROL else "pr_"
    prim_r = max(
        (abs(corr.loc[metric, f"{col}{o}"]) for o in PRIMARY_OUTCOMES
         if not np.isnan(corr.loc[metric, f"{col}{o}"]) and LEAKAGE.get(metric) != o),
        default=0.0,
    )
    prim_imp = np.nanmean([imp.loc[metric, f"imp_{o}"] for o in PRIMARY_OUTCOMES])
    prim_imp = 0.0 if np.isnan(prim_imp) else prim_imp
    sib, sib_r = red[metric]

    pr = "raw |r|" if metric == CONTROL else "|pr|"  # control has no partial r
    if sib_r >= REDUNDANCY_R and imp_rank.get(metric, 0) < imp_rank.get(sib, 0):
        return "Merge", f"|r|={sib_r} with `{sib}` (more important) — fold in"
    if prim_r < LOW_CORR and prim_imp < LOW_IMPORTANCE:
        return "Remove", f"weak once volume-controlled ({pr}≤{prim_r:.2f}, imp≤{prim_imp:.2f})"
    if prim_r < LOW_CORR or prim_imp < LOW_IMPORTANCE:
        return "Refine", f"mixed signal ({pr}={prim_r:.2f}, imp={prim_imp:.2f})"
    return "Keep", f"predictive after control ({pr}={prim_r:.2f}, imp={prim_imp:.2f})"


def build_report(df: pd.DataFrame) -> str:
    feats = _features(df)
    corr = correlate(df)
    imp = importances(df)
    red = redundancy(df)
    imp_rank = {
        f: float(np.nanmean([imp.loc[f, f"imp_{o}"] for o in PRIMARY_OUTCOMES])) for f in feats
    }
    imp_rank = {f: (0.0 if np.isnan(v) else v) for f, v in imp_rank.items()}

    lines = [
        "# Metric Value Report",
        "",
        f"_{len(df)} athletes (≥ min matches). Report only — no metric was changed._",
        "",
        "**Method.** Pearson/Spearman vs outcomes + RandomForest importance + pairwise-|r| "
        "redundancy (Terner & Franks 2020; Ouyang 2025 SHAP). Primary outcomes "
        f"({', '.join(PRIMARY_OUTCOMES)}) are external; {', '.join(SECONDARY_OUTCOMES)} are "
        "derived — ⚠ marks a feature whose correlation there is tautological (leakage).",
        "",
        "**Data-volume control.** Elite athletes have more transcribed matches, so raw "
        "correlations reward metrics that just grow with graph size. `pr·` columns are "
        "**partial** correlations controlling for `n_matches` — the de-confounded signal that "
        "recommendations are based on. Compare `r·rankELO` (raw) to `pr·rankELO` (controlled) "
        "to see the confound collapse.",
        "",
        "| Metric | pr·win | pr·rankELO | raw·rankELO | RF imp | sibling(|r|) | Rec | Why |",
        "|---|---|---|---|---|---|---|---|",
    ]
    ranked = sorted(feats, key=lambda f: imp_rank.get(f, 0), reverse=True)
    for f in ranked:
        rec, why = _recommend(f, corr, imp, red, imp_rank)
        leak = " ⚠" if f in LEAKAGE else ""
        sib, sib_r = red[f]
        lines.append(
            f"| `{f}`{leak} | {corr.loc[f,'pr_win_rate']} | {corr.loc[f,'pr_rank_elo']} | "
            f"{corr.loc[f,'r_rank_elo']} | {imp_rank.get(f,0):.3f} | `{sib}` ({sib_r}) | "
            f"**{rec}** | {why} |"
        )
    lines += [
        "",
        "## Notes",
        "- **`pr·` = partial correlation controlling for `n_matches`.** Recommendations use it, "
        "not the raw `r·`. A metric whose `r·rankELO` is high but `pr·rankELO` collapses toward 0 "
        "was only tracking data volume (elite athletes have more transcribed bouts), not skill.",
        "- ⚠ = leakage-flagged: the metric is (near-)derived from a secondary outcome, so a "
        "high correlation there is not evidence of predictive value.",
        f"- Thresholds: redundancy |r| ≥ {REDUNDANCY_R}; low-signal |partial r| < {LOW_CORR}, "
        f"RF imp < {LOW_IMPORTANCE} (RF includes `n_matches`, so importances are volume-adjusted).",
        "- **Merge** = keep the more-important sibling and drop this one; **Remove** = weak on "
        "every external outcome; **Refine** = keep but reformulate (noisy/partial signal).",
    ]
    return "\n".join(lines)


def run(min_matches: int = 3) -> int:
    from db.base import db_session

    with db_session() as session:
        df = build_matrix(session, min_matches=min_matches)
    if df.empty:
        logger.warning("No athletes with enough data — nothing to evaluate")
        return 1
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(build_report(df), encoding="utf-8")
    logger.info("Wrote %s (%d athletes, %d metrics)", REPORT, len(df), len(_features(df)))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Metric value study (report only)")
    ap.add_argument("--min-matches", type=int, default=3)
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return run(min_matches=args.min_matches)


# ── self-check (ponytail: runnable, no DB) ────────────────────────────────────
def _demo() -> None:
    rng = np.random.RandomState(0)
    n = 60
    signal = rng.rand(n)
    volume = rng.rand(n) * 10  # the confound: data volume
    df = pd.DataFrame({
        "athlete": [f"a{i}" for i in range(n)],
        "good": signal + rng.rand(n) * 0.1,          # genuinely predictive
        "dupe": signal + rng.rand(n) * 0.1,          # redundant with `good`
        "noise": rng.rand(n),                        # useless
        "volume_proxy": volume + rng.rand(n) * 0.1,  # correlates with win ONLY via volume
        "mean_ptv": rng.rand(n),
        "mean_pagerank": rng.rand(n), "mean_reward_risk": rng.rand(n),
        "mean_control": rng.rand(n), "mean_continuity": rng.rand(n),
        "mean_funnel": rng.rand(n), "mean_deviance": rng.rand(n),
        "n_dilemmas": rng.rand(n), "n_nodes": rng.rand(n), "n_edges": rng.rand(n),
        "mean_attacker": rng.rand(n), "turning_points": rng.rand(n),
        "grown_elo": rng.rand(n), "max_ptv": rng.rand(n),
        "n_matches": volume,                          # the control variable
        "win_rate": signal + 0.05 * volume,           # depends on real signal + volume
        "sub_rate": rng.rand(n), "rank_elo": signal + rng.rand(n) * 0.1,
        "avg_ptv": rng.rand(n), "positional_dominance": rng.rand(n),
    })
    red = redundancy(df)
    assert red["good"][1] > 0.8 and red["dupe"][1] > 0.8, "should detect good↔dupe redundancy"
    corr = correlate(df)
    assert abs(corr.loc["good", "r_win_rate"]) > 0.7, "predictive feature must correlate"
    assert abs(corr.loc["noise", "r_win_rate"]) < 0.5, "noise must not correlate"
    # The confound: volume_proxy correlates with win raw, but NOT after controlling n_matches.
    assert abs(corr.loc["volume_proxy", "r_win_rate"]) > 0.4, "volume proxy correlates raw"
    assert abs(corr.loc["volume_proxy", "pr_win_rate"]) < 0.2, "…but collapses once controlled"
    # A real signal survives the control.
    assert abs(corr.loc["good", "pr_win_rate"]) > 0.5, "real signal survives the control"
    rep = build_report(df)
    assert "Metric Value Report" in rep and "partial" in rep
    print("metric_evaluation demo OK")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
    else:
        raise SystemExit(main())
