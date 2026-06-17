"""Fighter similarity — cosine similarity between user and ADCC fighters.

Vector space includes:
  - Win-type mix (submission/decision/points/DQ shares)
  - Submission distribution (top-10 normalized sub shares)
  - Career stats (sub_ratio, win_ratio)
  - Favorite target (one-hot: arm/leg/neck/other)
  - ELO bucket (quantile: 0-4)

User vector from benchmark profile — missing dims are masked (not zeroed).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from analysis.names import _normalize_adcc_sub, _normalize_name

logger = logging.getLogger(__name__)

TOP_SUBS = [
    "rear naked choke", "armbar", "triangle choke", "heel hook",
    "guillotine choke", "kimura", "darce choke", "omoplata",
    "kneebar", "arm triangle",
]


def _win_type_mix(adcc_df: pd.DataFrame) -> pd.DataFrame:
    """Compute win-type share per fighter (submission, decision, points, DQ)."""
    results = []
    for fighter, grp in adcc_df.groupby("winner"):
        total = len(grp)
        if total < 3:
            continue
        sub = (grp["win_type"] == "SUBMISSION").sum() / total
        dec = (grp["win_type"] == "DECISION").sum() / total
        pts = (grp["win_type"] == "POINTS").sum() / total
        dq = (grp["win_type"] == "DQ").sum() / total
        results.append({
            "fighter": fighter,
            "sub_share": sub,
            "decision_share": dec,
            "points_share": pts,
            "dq_share": dq,
        })
    return pd.DataFrame(results) if results else pd.DataFrame()


def _sub_distribution(adcc_df: pd.DataFrame) -> pd.DataFrame:
    """Compute top-10 submission share vector per fighter."""
    subs = adcc_df.dropna(subset=["submission"]).copy()
    subs["sub_clean"] = subs["submission"].apply(_normalize_adcc_sub)

    results = []
    for fighter, grp in subs.groupby("winner"):
        total = len(grp)
        if total < 3:
            continue
        shares = {}
        for tech in TOP_SUBS:
            shares[f"sub_{tech.replace(' ', '_')}"] = (grp["sub_clean"] == tech).sum() / total
        shares["fighter"] = fighter
        results.append(shares)
    return pd.DataFrame(results) if results else pd.DataFrame()


def fighter_vectors(
    adcc_df: pd.DataFrame,
    fighters_df: pd.DataFrame,
    elo_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build one row per fighter with L2-normalized feature columns.

    Minimum 3 ADCC matches to qualify.
    Returns DataFrame with 'fighter' column + normalized numeric features.
    """
    win_mix = _win_type_mix(adcc_df)
    sub_dist = _sub_distribution(adcc_df)

    if win_mix.empty and sub_dist.empty:
        logger.warning("No fighters qualify for vector space (need ≥ 3 matches)")
        return pd.DataFrame(columns=["fighter"])

    merged = win_mix.merge(sub_dist, on="fighter", how="outer").fillna(0.0)

    if not fighters_df.empty:
        fighters_df["name_norm"] = fighters_df["fighter_name"].apply(_normalize_name)
        merged["name_norm"] = merged["fighter"].apply(_normalize_name)
        merged = merged.merge(
            fighters_df[["name_norm", "sub_ratio", "win_ratio", "favorite_target"]],
            on="name_norm",
            how="left",
        )
    else:
        merged["sub_ratio"] = 0.0
        merged["win_ratio"] = 0.0
        merged["favorite_target"] = ""

    target_dummies = pd.get_dummies(
        merged["favorite_target"].fillna(""), prefix="target", dtype=int,
    )
    merged = pd.concat([merged, target_dummies], axis=1)

    if not elo_df.empty:
        merged["name_norm"] = merged["fighter"].apply(_normalize_name)
        elo_df["name_norm"] = elo_df["fighter"].apply(_normalize_name)
        merged = merged.merge(elo_df[["name_norm", "elo"]], on="name_norm", how="left")
        merged["elo"] = merged["elo"].fillna(1000)
        merged["elo_bucket"] = (
            pd.qcut(merged["elo"], q=5, labels=False, duplicates="drop")
            .fillna(2)
            .astype(int)
        )
    else:
        merged["elo"] = 1000.0
        merged["elo_bucket"] = 2

    exclude = {"fighter", "name_norm", "favorite_target", "elo"}
    feature_cols = [c for c in merged.columns if c not in exclude]

    for col in feature_cols:
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)

    vecs = merged[feature_cols].values
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    merged[feature_cols] = vecs / norms

    merged["feature_names"] = [feature_cols] * len(merged)

    return merged[["fighter"] + feature_cols].reset_index(drop=True)


def top_similar(
    query_vec: np.ndarray,
    vectors: pd.DataFrame,
    k: int = 5,
    mask: list[str] | None = None,
) -> pd.DataFrame:
    """Masked cosine similarity — compare only shared dimensions.

    Args:
        query_vec: 1D user vector
        vectors: DataFrame with 'fighter' column + feature columns
        k: number of results
        mask: feature columns to include (None = all)

    Returns:
        DataFrame: fighter, similarity (sorted desc)
    """
    feature_cols = [c for c in vectors.columns if c != "fighter"]

    if mask:
        mask_set = set(mask)
        q = np.zeros_like(query_vec)
        for i, col in enumerate(feature_cols):
            if col in mask_set:
                q[i] = query_vec[i]
        vecs = vectors[feature_cols].values.copy()
        for i, col in enumerate(feature_cols):
            if col not in mask_set:
                vecs[:, i] = 0.0
        q_norm = np.linalg.norm(q)
        q = q / q_norm if q_norm > 0 else q
        row_norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        row_norms[row_norms == 0] = 1
        vecs = vecs / row_norms
    else:
        q = query_vec
        vecs = vectors[feature_cols].values

    sims = vecs @ q

    top_k = min(k, len(sims))
    indices = np.argsort(sims)[::-1][:top_k]

    result = pd.DataFrame({
        "fighter": vectors.iloc[indices]["fighter"].values,
        "similarity": sims[indices].round(4),
    })
    return result.reset_index(drop=True)


def user_vector(
    user_profile: pd.DataFrame,
    adcc_df: pd.DataFrame,
    feature_names: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Build user vector + available-dims mask from benchmark profile.

    Returns (vector, mask) where mask lists feature columns available
    from user data. Missing dimensions (e.g., ELO) are excluded from mask.
    """
    n_features = len(feature_names)
    vec = np.zeros(n_features, dtype=np.float64)
    mask: list[str] = []

    feature_to_idx = {name: i for i, name in enumerate(feature_names)}

    if not user_profile.empty:
        for _, row in user_profile.iterrows():
            tech = row.get("technique", "")
            col = f"sub_{tech.replace(' ', '_')}"
            if col in feature_to_idx:
                vec[feature_to_idx[col]] = row.get("user_share", 0.0)
                mask.append(col)

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    return vec, mask


def fighter_similarity(
    adcc_df: pd.DataFrame,
    fighters_df: pd.DataFrame,
    elo_df: pd.DataFrame,
    user_profile: pd.DataFrame,
    k: int = 5,
) -> pd.DataFrame:
    """Convenience: build vectors, compute user vector, return top-k similar fighters."""
    vectors = fighter_vectors(adcc_df, fighters_df, elo_df)
    if vectors.empty:
        return pd.DataFrame(columns=["fighter", "similarity"])

    feature_cols = [c for c in vectors.columns if c != "fighter"]
    query_vec, mask = user_vector(user_profile, adcc_df, feature_cols)

    return top_similar(query_vec, vectors, k=k, mask=mask)


def find_similar_fighters(
    adcc_df: pd.DataFrame,
    fighters_df: pd.DataFrame,
    elo_df: pd.DataFrame,
    target_fighter: str,
    k: int = 5,
) -> pd.DataFrame:
    """Find fighters most similar to a given ADCC fighter (for verification).

    Useful for testing: a fighter should be most similar to themselves.
    """
    vectors = fighter_vectors(adcc_df, fighters_df, elo_df)
    if vectors.empty or target_fighter not in vectors["fighter"].values:
        return pd.DataFrame(columns=["fighter", "similarity"])

    feature_cols = [c for c in vectors.columns if c != "fighter"]
    target_idx = vectors[vectors["fighter"] == target_fighter].index[0]
    query_vec = vectors.loc[target_idx, feature_cols].values.astype(np.float64)

    others = vectors.drop(index=target_idx).reset_index(drop=True)
    return top_similar(query_vec, others, k=k)
