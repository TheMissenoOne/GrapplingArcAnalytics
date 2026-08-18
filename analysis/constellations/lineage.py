"""Lineage between two constellation snapshots — matched by best Jaccard.

Doc 04 (``docs/rating_v2/`` bundle, ``04_CONSTELLATION_DETECTION.md``): "Do not
pretend the fingerprint is eternal identity. If historical lineage becomes a product
requirement, match consecutive snapshots by Jaccard and store lineage separately."
This module IS that separate store — it never reads or writes
``Constellation.fingerprint``'s meaning, only uses the fingerprint as a stable label
for "this exact member set at this snapshot".

Reuses ``compare.jaccard`` (the one comparator in this package) for every match — no
second Jaccard implementation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from analysis.constellations.compare import jaccard
from analysis.constellations.detect import Constellation


@dataclass
class LineageMatch:
    old_fingerprint: str
    new_fingerprint: str | None  # None = this old constellation died (no match >= min_jaccard)
    jaccard: float


@dataclass
class LineageResult:
    matches: list[LineageMatch]     # one row per OLD constellation
    births: list[str]                # new fingerprints with no matching ancestor
    deaths: list[str]                # old fingerprints with no matching descendant
    merges: dict[str, list[str]]     # new_fingerprint -> >=2 old_fingerprints that best-matched it
    splits: dict[str, list[str]]     # old_fingerprint -> >=2 new_fingerprints that best-matched it


def match_lineage(
    old: list[Constellation], new: list[Constellation], min_jaccard: float = 0.2,
) -> LineageResult:
    """Match each snapshot's constellations to the other by best-Jaccard, both
    directions (old->new for ``matches``/``deaths``, new->old for ``births``, both
    together for merge/split detection).

    ``min_jaccard`` is a calibration output (doc 04 rule — thresholds are not fixed
    plan constants), a first cut here at the same status as
    ``stability.classify_stability``'s ``stable_threshold``. Below it, a would-be
    match is treated as no relationship at all: a bare Jaccard of 0.05 between two
    unrelated constellations isn't lineage, it's coincidence.
    """
    def best_match(a: Constellation, pool: list[Constellation]) -> tuple[str | None, float]:
        best_fp, best_j = None, 0.0
        for b in pool:
            j = jaccard(set(a.members), set(b.members))
            if j > best_j:
                best_fp, best_j = b.fingerprint, j
        return (best_fp, round(best_j, 5)) if best_j >= min_jaccard else (None, round(best_j, 5))

    old_to_new = {o.fingerprint: best_match(o, new) for o in old}
    new_to_old = {n.fingerprint: best_match(n, old) for n in new}

    matches = [
        LineageMatch(old_fingerprint=fp, new_fingerprint=new_fp, jaccard=j)
        for fp, (new_fp, j) in old_to_new.items()
    ]
    deaths = sorted(fp for fp, (new_fp, _) in old_to_new.items() if new_fp is None)
    births = sorted(fp for fp, (old_fp, _) in new_to_old.items() if old_fp is None)

    # merge: >=2 old constellations both best-match the SAME new one.
    chosen_by_new: dict[str, list[str]] = defaultdict(list)
    for fp, (new_fp, _) in old_to_new.items():
        if new_fp is not None:
            chosen_by_new[new_fp].append(fp)
    merges = {new_fp: sorted(olds) for new_fp, olds in chosen_by_new.items() if len(olds) > 1}

    # split: >=2 new constellations both best-match the SAME old one.
    chosen_by_old: dict[str, list[str]] = defaultdict(list)
    for fp, (old_fp, _) in new_to_old.items():
        if old_fp is not None:
            chosen_by_old[old_fp].append(fp)
    splits = {old_fp: sorted(news) for old_fp, news in chosen_by_old.items() if len(news) > 1}

    return LineageResult(matches=matches, births=births, deaths=deaths, merges=merges, splits=splits)
