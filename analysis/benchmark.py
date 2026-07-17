"""User vs pro benchmarking — compare user's session data against ADCC baselines."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from analysis.names import _normalize_adcc_sub

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# The App's 7 style-mix radar axes (STYLE_MIX_AXES in src/services/styleProfile.ts) — must
# match char-for-char, this is a cross-module contract.
STYLE_MIX_AXES: tuple[str, ...] = (
    "pass", "control", "submission", "escape", "guard", "sweep", "takedown",
)
SUB_FAMILY_KEYS: tuple[str, ...] = ("strangle", "leglock", "armlock")

# Qualifying bar for the distribution is style_profile.MIN_SEQUENCE_BOUTS (via
# style_profile.qualifies), not a separate constant here.


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


def _quartiles(values: Sequence[float]) -> dict[str, float]:
    """p25/median/p75 of a share list. Empty input → flat zeros (no signal)."""
    if not values:
        return {"p25": 0.0, "median": 0.0, "p75": 0.0}
    s = pd.Series(values, dtype=float)
    return {
        "p25": round(float(s.quantile(0.25)), 4),
        "median": round(float(s.quantile(0.5)), 4),
        "p75": round(float(s.quantile(0.75)), 4),
    }


def athlete_style_distribution_db(session: Session) -> dict[str, Any]:
    """Per-athlete style-mix + submission-family share DISTRIBUTIONS (p25/median/p75),
    DB-backed — replaces the old ADCC-outcome-only ``athlete_style_distribution``
    (removed: that corpus only has match winner/loser/win_type, so 6 of the 7
    style-mix axes came back flat zero).

    For each qualifying athlete (``style_profile.qualifies`` — >= MIN_SEQUENCE_BOUTS
    final bouts with a sequence), flattens their own-perspective events across every
    such bout (``db.repository.get_matches_for_athlete`` + ``_perspective_view``) and
    reduces them with the shared, parity-tested ``style_profile.reduce_style_events``
    to get that athlete's real 7-axis style_mix + submission-family shares. Quartiles
    are then taken across the qualifying population per axis / family.
    """
    from sqlalchemy import select

    from analysis.style_profile import _FAMILY_LABELS, qualifies, reduce_style_events
    from db.models import Athlete
    from db.repository import _perspective_view, get_matches_for_athlete

    axis_shares: dict[str, list[float]] = {axis: [] for axis in STYLE_MIX_AXES}
    family_shares: dict[str, list[float]] = {k: [] for k in SUB_FAMILY_KEYS}
    n_athletes = n_bouts = n_events = 0

    athlete_ids = list(session.execute(select(Athlete.id)).scalars())
    for athlete_id in athlete_ids:
        if not qualifies(athlete_id, session):
            continue
        matches = [
            m for m in get_matches_for_athlete(athlete_id, session)
            if m.status == "final" and m.sequence
        ]
        if not matches:
            continue

        events: list[dict[str, Any]] = []
        for m in matches:
            pv = _perspective_view(m, athlete_id)
            events.extend(pv.sequence)

        reduced = reduce_style_events(events)
        n_athletes += 1
        n_bouts += len(matches)
        n_events += sum(reduced["type_counts"].values())

        for axis in STYLE_MIX_AXES:
            axis_shares[axis].append(reduced["style_mix"].get(axis, 0.0))

        fam_counts = reduced["submission_family"]["counts"]
        fam_total = sum(fam_counts.values())
        if fam_total:
            for key in SUB_FAMILY_KEYS:
                count = fam_counts.get(_FAMILY_LABELS[key], 0)
                if count:
                    family_shares[key].append(count / fam_total)

    style_mix = {axis: _quartiles(shares) for axis, shares in axis_shares.items()}
    submission_families = {key: _quartiles(shares) for key, shares in family_shares.items()}

    return {
        "styleMix": style_mix,
        "submissionFamilies": submission_families,
        "sample": {
            "athletes": n_athletes,
            "bouts": n_bouts,
            "events": n_events,
        },
    }
