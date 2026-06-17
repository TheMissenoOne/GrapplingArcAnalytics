"""User vs pro benchmarking — compare user's session data against ADCC baselines."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from analysis.names import _normalize_adcc_sub

logger = logging.getLogger(__name__)


def user_submission_profile(bundle: Any) -> pd.DataFrame:
    """Extract per-technique attempt/success counts from user sessions.

    Iterates session rounds where actor == "you". Normalizes technique names.
    Returns DataFrame: technique, attempts, successes, user_share.
    """
    from collections import Counter

    attempts: Counter[str] = Counter()
    successes: Counter[str] = Counter()

    if not bundle.sessions:
        logger.warning("No sessions in bundle")
        return pd.DataFrame(columns=["technique", "attempts", "successes", "user_share"])

    for session in bundle.sessions:
        for rnd in getattr(session, "rounds", []):
            for entry in getattr(rnd, "entries", []):
                if getattr(entry, "actor", "") != "you":
                    continue
                label = getattr(entry, "label", "")
                if not label:
                    continue
                norm = _normalize_adcc_sub(label)
                attempts[norm] += 1
                if getattr(entry, "successful", True) is not False:
                    successes[norm] += 1

    if not attempts:
        return pd.DataFrame(columns=["technique", "attempts", "successes", "user_share"])

    total = sum(attempts.values())
    rows = []
    for tech in sorted(attempts.keys()):
        rows.append({
            "technique": tech,
            "attempts": attempts[tech],
            "successes": successes[tech],
            "user_share": attempts[tech] / total if total else 0.0,
        })

    return pd.DataFrame(rows)


def pro_baseline(adcc_df: pd.DataFrame) -> pd.DataFrame:
    """Compute ADCC submission distribution as pro baseline.

    Uses submission_frequency from technique_freq — returns per-technique share.
    """
    from analysis.technique_freq import submission_frequency

    freq = submission_frequency(adcc_df, by="year")
    # Aggregate across all years — mean share per technique
    pro = freq.mean(axis=1).reset_index()
    pro.columns = ["technique", "pro_share"]
    pro["pro_share"] = pro["pro_share"].fillna(0.0).round(4)
    return pro.sort_values("pro_share", ascending=False).reset_index(drop=True)


def compare(
    user_profile: pd.DataFrame, pro_baseline_df: pd.DataFrame
) -> pd.DataFrame:
    """Compare user profile against pro baseline.

    Per technique: user_share vs pro_share, ratio, flag.
    Techniques in user but not in pro flagged with no_pro_data=True.
    """
    merged = user_profile.merge(pro_baseline_df, on="technique", how="left")

    merged["no_pro_data"] = merged["pro_share"].isna()
    merged["pro_share"] = merged["pro_share"].fillna(0.0)

    eps = 1e-6
    merged["ratio"] = (merged["user_share"] / (merged["pro_share"] + eps)).round(3)

    merged["emphasis"] = merged["ratio"].apply(
        lambda r: "high" if r > 2.0 else ("low" if r < 0.5 else "normal")
    )

    return merged.sort_values("user_share", ascending=False).reset_index(drop=True)
