"""Confidence weighting for the transition-graph corpus (wave 9,
``docs/rating_v2/07_PONDERACAO_POR_CONFIANCA.md``).

Pure athlete-level weight schemes off Rating V2 RD (``athlete_rating_states_v2``, read
with an explicit ``run_id`` per ADR-02). Low-confidence (high-RD) athletes/matches keep
contributing to aggregates — never excluded, wave 8 already settled that — but at reduced
weight relative to well-observed athletes.

Three schemes, all strictly positive and monotonically non-increasing in RD:

- ``uniform``   — baseline, weight 1 for everyone (the control / today's behaviour).
- ``precision`` — ``w = 1/RD²``, renormalised to mean 1 over the athletes present. The
  Glicko-literal "amount of information" weight (same idiom as ADR-11's node/global
  blend) — but RD shrinks with bout volume, so this amplifies whoever already dominates
  the corpus by volume. Measured, not defaulted to; see the report.
- ``bounded``   — ``w = 1/(1 + (RD/RD_ref)²)``, ``RD_ref=200`` (the wave-8 editorial
  confidence cutoff). ``w -> 1`` as ``RD -> 0``, ``w -> 0`` as ``RD -> inf``, always in
  ``(0, 1]`` — caps the amplification ``precision`` causes.

A 4th scheme (shrinkage by ``bouts_observed``) was considered and skipped: it answers the
same question ``bounded`` already answers (temper high-uncertainty athletes) through a
different knob, without giving the report a materially distinct comparison point — not
worth a fourth column. Named here, not built, per YAGNI.

Athletes absent from the run get ``rd_floor`` (matches ``analysis.rating_v2.config.
EngineConfig.initial_rd`` — the seed RD for an athlete the engine has never observed),
never a weight of zero: zero weight is exclusion wearing a weighting hat, exactly what
wave 8 ruled out.
"""

from __future__ import annotations

from collections.abc import Iterable

RD_FLOOR_DEFAULT = 250.0  # analysis.rating_v2.config.EngineConfig.initial_rd
RD_REF_DEFAULT = 200.0  # wave-8 editorial confidence cutoff

SCHEMES = ("uniform", "precision", "bounded")


def precision_weight(rd: float) -> float:
    """``w = 1/RD²``. Strictly positive, strictly decreasing in RD (RD > 0)."""
    return 1.0 / (rd * rd)


def bounded_weight(rd: float, rd_ref: float = RD_REF_DEFAULT) -> float:
    """``w = 1/(1 + (RD/RD_ref)²)``. In ``(0, 1]``; ``->1`` as RD->0, ``->0`` as RD->inf."""
    ratio = rd / rd_ref
    return 1.0 / (1.0 + ratio * ratio)


def athlete_weights(
    athlete_ids: Iterable[str],
    rd_by_athlete: dict[str, float],
    scheme: str,
    rd_floor: float = RD_FLOOR_DEFAULT,
    rd_ref: float = RD_REF_DEFAULT,
) -> dict[str, float]:
    """One weight per athlete in ``athlete_ids`` under ``scheme`` (``SCHEMES``).

    Missing RD -> ``rd_floor`` (never a zero weight). ``precision`` is renormalised to
    mean 1 over the athletes present, so its scale is comparable to the uniform baseline;
    ``bounded`` is already in ``(0, 1]`` by construction and left as-is.
    """
    ids = list(dict.fromkeys(athlete_ids))  # stable order, de-duped
    if scheme == "uniform":
        return dict.fromkeys(ids, 1.0)
    if scheme not in ("precision", "bounded"):
        raise ValueError(f"unknown weight scheme: {scheme!r}")

    rds = {aid: rd_by_athlete.get(aid, rd_floor) for aid in ids}
    if scheme == "precision":
        raw = {aid: precision_weight(rd) for aid, rd in rds.items()}
        mean = sum(raw.values()) / len(raw) if raw else 1.0
        return {aid: (w / mean if mean else 1.0) for aid, w in raw.items()}
    return {aid: bounded_weight(rd, rd_ref) for aid, rd in rds.items()}
