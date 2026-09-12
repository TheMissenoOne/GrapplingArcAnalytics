"""RRB-derived round dominance — the production signal (ADR-17, ``docs/rating_v2/``).

Extracted from ``scripts/research/rrb_round_rating.py`` (prereg §E) so a cross-repo contract
artefact (``data/rating/rrb_dominance_calibration.json`` +
``data/fixtures/rrbDominanceGolden.json``) does not depend on a research script. The research
script and both generators (``scripts/build_rrb_dominance_calibration.py``,
``scripts/export_rrb_dominance_fixtures.py``) import the math from HERE — this module is the
single source, they are callers.

**What this computes, exactly the artefact's ``definition``:**

1. Each entry's ``label`` → ``analysis.technique_match.clean_label`` (pt-BR/variant resolver) →
   ``analysis.lamas_chain.lamas_state`` (Lamas 12-code action space). Unmapped entries are
   DROPPED, never averaged in.
2. Actor-signed log-odds per mapped code: ``+logit(value[code])`` for the round's own actor,
   ``-logit(value[code])`` for the partner.
3. ``actions_states`` granularity (γ=1): the mean of the plain-mean-of-steps statistic and the
   LAST mapped step's statistic — occupancy, not pure repetition (prereg §A3).
4. Raw dominance ``P = sigmoid(Z)``; CALIBRATED ``P`` via the artefact's fitted method
   (temperature/platt/isotonic — study §E1 selected temperature).
5. Elo offset for the Glicko-2 virtual opponent: ``-400·log10(P/(1-P))``, clamped to
   ``±clamp_elo`` (study's ``hier4`` precedent, ±400).

**What this is NOT.** Dominance is the OPPONENT offset (``E``), never the observation SCORE
(``s``) — the study's self-cancellation identity (§4) is exactly why: the same round's ``P`` used
as both would cancel to zero information. ``round_dominance`` returns no "score" field; the
caller's own ``successful`` flag stays ``s``, unchanged (ADR-17).

Per-technique Glicko-2 credit (splitting one round's evidence across its actions) was tried and
rejected — study §E3, death rule 17: none of ``equal_split``/``last_action_only``/``contribution``
beat the technique's static prior with a clean interval. This module carries the value TABLE
(the static prior) and ``contribution_shares`` (diagnostic only, prereg §E3's ``c_k``), never a
per-action rating update.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from analysis.lamas_chain import lamas_state
from analysis.technique_match import clean_label

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALIBRATION_PATH = REPO_ROOT / "data" / "rating" / "rrb_dominance_calibration.json"

SUB_FAMILY = ("SUBA", "SUB")

CALIBRATION_METHODS = ("temperature", "platt", "isotonic")
#: Tie-break tolerance for the study's method selection (prereg §E1), fixed before any number
#: was seen.
CALIBRATION_TIE_NATS = 0.005


# ── value table: label → Lamas code → dominance value ────────────────────────────


def canonical_code(entry: Mapping[str, Any], *, library: bool = True) -> str | None:
    """Lamas code for one APP entry, through the canonical label resolver first.

    The owner logs in pt-BR (``control/Costas``, ``transition/Puxada para Guarda``) and
    ``lamas_chain``'s label rules match English tokens, so reading the raw label loses
    coverage. ``clean_label`` is the existing pt/variant → canonical-English resolver;
    routing through it takes coverage from 28.75 % to 50.00 % (study §1). ``library=False``
    reproduces the pre-resolver numbers (research-only, kept for the historical arms).
    """
    label = str(entry.get("label") or "")
    if library:
        label = clean_label(label, str(entry.get("type") or ""))
    return lamas_state(
        {"type": entry.get("type"), "label": label, "successful": entry.get("successful")}
    )


def marginal_submission_share(doc: Mapping[str, Any], family: str = "global") -> float:
    """The n-weighted mix of ``SUBA``/``SUB`` in one block of the Markov weights artefact.

    Recomputed from the artefact's OWN ``provenance.actions`` counts, not a pasted constant, so
    it moves when the weights artefact does.
    """
    rows = ((doc.get("provenance") or {}).get("actions") or {}).get(family) or {}
    num = den = 0.0
    for code in SUB_FAMILY:
        row = rows.get(code) or {}
        n = float(row.get("n") or 0.0)
        w = float(row.get("weight") or 0.0)
        num += n * w
        den += n
    return num / den if den else 0.5


def action_values(
    block: Mapping[str, float], *, terminal: str, marginal: float
) -> dict[str, float]:
    """Per-code dominance value, under one of the three terminal-handling settings.

    ``landed`` — the block as published (``SUB`` keeps its partly-circular value).
    ``marginal`` — both submission codes take the family's n-weighted mix (the shipped setting).
    ``drop`` — the submission family is removed entirely (the leakage control).
    """
    vals = {str(k): float(v) for k, v in block.items()}
    if terminal == "landed":
        return vals
    if terminal == "marginal":
        return {k: (marginal if k in SUB_FAMILY else v) for k, v in vals.items()}
    if terminal == "drop":
        return {k: v for k, v in vals.items() if k not in SUB_FAMILY}
    raise ValueError(f"terminal desconhecido: {terminal!r}")


# ── the dominance statistic ───────────────────────────────────────────────────────


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0


def p_own(z: float) -> float:
    """Dominance as a probability. ``P_partner = 1 − p_own(z)``."""
    return sigmoid(z)


def elo_offset(p: float) -> float:
    """Elo points the virtual partner sits ABOVE the athlete, UNCLAMPED. ``p`` is the athlete's
    dominance, so a dominant round puts the partner BELOW (negative offset). Callers that feed a
    Glicko-2 slot must clamp — see ``clamp_elo`` — this is the raw math the artefact/goldens
    snapshot verbatim (do not clamp here, it would change every existing golden number)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(p / (1.0 - p))


def clamp_elo(offset: float, limit: float) -> float:
    """Clamp an Elo offset to ``±limit`` — the Glicko-2 virtual-opponent guard (study's ``hier4``
    precedent, ±400, ``clamp_elo`` in the calibration artefact)."""
    return max(-limit, min(limit, offset))


def signed_log_odds(
    steps: Sequence[tuple[str | None, bool]],
    values: Mapping[str, float],
    *,
    gamma: float = 1.0,
    temperature: float = 1.0,
) -> tuple[float, int]:
    """``(Z, n_mapped)``. ``steps`` is ``(lamas code or None, is_own)`` in sequence order.

    Unmapped codes and codes absent from ``values`` are DROPPED, not given the block mean: the
    mean is the no-information value for a WEIGHT, and averaging it into a VALUE invents a
    reading of who was winning.

    ``gamma`` is the length exponent: ``Z = Σ z_i / (n^γ · T)``.
    """
    zs = [
        (_logit(values[code]) if own else -_logit(values[code]))
        for code, own in steps
        if code is not None and code in values
    ]
    n = len(zs)
    if n == 0:
        return float("nan"), 0
    denom = (n**gamma) * temperature
    return sum(zs) / denom if denom else float("nan"), n


def _zs(steps: Sequence[tuple[str | None, bool]], values: Mapping[str, float]) -> list[float]:
    return [
        (_logit(values[c]) if own else -_logit(values[c]))
        for c, own in steps
        if c is not None and c in values
    ]


def _keyed_zs(
    steps: Sequence[tuple[str | None, bool]], values: Mapping[str, float]
) -> list[tuple[tuple[str, bool], float]]:
    return [
        ((c, own), _logit(values[c]) if own else -_logit(values[c]))
        for c, own in steps
        if c is not None and c in values
    ]


def granular_score(
    steps: Sequence[tuple[str | None, bool]],
    values: Mapping[str, float],
    *,
    granularity: str = "actions",
    gamma: float = 1.0,
    temperature: float = 1.0,
) -> tuple[float, int]:
    """``(Z, n_used)`` under one evidence granularity.

    ``actions``    Σ z_i / n^γ — repetition-weighted.
    ``states``     z of the LAST mapped step — a position, length-free by construction.
    ``states_occ`` mean z over DISTINCT ``(code, actor)`` pairs — occupancy, not repetition.
    ``edges``      Σ (z_{i+1} − z_i) / (n−1)^γ — the PROGRESSION (VAEP/xT form).
    ``*_states``   the mean of the two component Zs; coherent because both are log-odds. The
                   shipped artefact uses ``actions_states`` at γ=1 (the occupancy rule).
    """
    zs = _zs(steps, values)
    n = len(zs)
    if granularity == "actions":
        if n == 0:
            return float("nan"), 0
        return sum(zs) / ((n**gamma) * temperature), n
    if granularity == "states":
        if n == 0:
            return float("nan"), 0
        return zs[-1] / temperature, n
    if granularity == "states_occ":
        kz = _keyed_zs(steps, values)
        if not kz:
            return float("nan"), 0
        uniq: dict[tuple[str, bool], float] = {}
        for k, z in kz:
            uniq.setdefault(k, z)
        return sum(uniq.values()) / (len(uniq) * temperature), len(uniq)
    if granularity == "edges":
        if n < 2:
            return float("nan"), 0
        deltas = [zs[i + 1] - zs[i] for i in range(n - 1)]
        return sum(deltas) / (((n - 1) ** gamma) * temperature), n
    if granularity in ("actions_states", "edges_states"):
        first = "actions" if granularity == "actions_states" else "edges"
        za, na = granular_score(
            steps, values, granularity=first, gamma=gamma, temperature=temperature
        )
        zb, nb = granular_score(
            steps, values, granularity="states", gamma=gamma, temperature=temperature
        )
        if na == 0 or nb == 0:
            return float("nan"), 0
        return (za + zb) / 2.0, max(na, nb)
    raise ValueError(f"granularity desconhecida: {granularity!r}")


def contribution_shares(
    steps: Sequence[tuple[str | None, bool]], vals: Mapping[str, float]
) -> list[tuple[str, bool, float]]:
    """``(code, own, c_k)`` — the actor-signed SHARE of the round's ``actions`` log-odds mass,
    ``c_k = z_k / Σ|z_j|`` so ``Σ|c_k| = 1``. Mapped steps only. Diagnostic (study §E3) — never a
    per-action rating update (death rule 17, see module docstring)."""
    kz = _keyed_zs(steps, vals)
    total = sum(abs(z) for _, z in kz)
    if total <= 0:
        return []
    return [(code, own, z / total) for (code, own), z in kz]


# ── calibration ────────────────────────────────────────────────────────────────────


def calibration_fit(zs: Sequence[float], ys: Sequence[float], method: str) -> dict[str, Any]:
    """One calibration candidate, fit on ``(Z, label)`` pairs (study §E1)."""
    if method == "temperature":
        from scipy.optimize import minimize_scalar

        ys_int = [int(y) for y in ys]

        def nll(t: float) -> float:
            t = max(t, 1e-3)
            preds = [sigmoid(z / t) for z in zs]

            def term(p: float, y: int) -> float:
                q = max(min(p, 1 - 1e-12), 1e-12)
                return math.log(q) if y else math.log(1 - q)

            return -sum(term(p, y) for p, y in zip(preds, ys_int, strict=True)) / len(preds)

        r = minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded")
        return {"method": "temperature", "T": float(r.x)}
    if method == "platt":
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit([[z] for z in zs], ys)
        return {"method": "platt", "a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        ps = [sigmoid(z) for z in zs]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
        iso.fit(ps, ys)
        return {
            "method": "isotonic",
            "x_thresholds": [round(float(x), 6) for x in iso.X_thresholds_],
            "y_thresholds": [round(float(y), 6) for y in iso.y_thresholds_],
        }
    raise ValueError(f"método de calibração desconhecido: {method!r}")


def _isotonic_interp(p: float, xt: Sequence[float], yt: Sequence[float]) -> float:
    """Piecewise-linear read of a serialised isotonic fit — no sklearn object in the artefact."""
    if not xt:
        return p
    if p <= xt[0]:
        return yt[0]
    if p >= xt[-1]:
        return yt[-1]
    for i in range(1, len(xt)):
        if p <= xt[i]:
            x0, x1, y0, y1 = xt[i - 1], xt[i], yt[i - 1], yt[i]
            if x1 == x0:
                return y1
            return y0 + (p - x0) / (x1 - x0) * (y1 - y0)
    return yt[-1]


def calibration_apply(zs: Sequence[float], params: Mapping[str, Any]) -> list[float]:
    """Calibrated ``P`` for a list of raw ``Z`` (logit-scale ``actions_states``), one method."""
    method = params["method"]
    if method == "temperature":
        t = params["T"]
        return [sigmoid(z / t) for z in zs]
    if method == "platt":
        a, b = params["a"], params["b"]
        return [sigmoid(a * z + b) for z in zs]
    if method == "isotonic":
        xt, yt = params["x_thresholds"], params["y_thresholds"]
        return [_isotonic_interp(sigmoid(z), xt, yt) for z in zs]
    raise ValueError(f"método de calibração desconhecido: {method!r}")


def load_calibration(path: Path | None = None) -> dict[str, Any]:
    """Load ``data/rating/rrb_dominance_calibration.json`` (or an override path)."""
    p = path or DEFAULT_CALIBRATION_PATH
    doc: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return doc


# ── the composed production signal ─────────────────────────────────────────────────


def round_dominance(
    entries: Sequence[Mapping[str, Any]],
    *,
    weights: Mapping[str, float],
    calibration: Mapping[str, Any],
) -> dict[str, Any]:
    """One round's calibrated dominance, from raw App entries to a Glicko-2 opponent offset.

    ``weights`` — the code → value table (``calibration["definition"]["value_table"]``,
    typically). ``calibration`` — the loaded artefact document (``load_calibration()``): reads
    ``calibration["calibration"]["parameters"]``/``["method"]`` and, if present,
    ``calibration["clamp_elo"]["value"]``.

    Returns ``{p, p_calibrated, elo_offset, contributions, coverage}``. ``p``/``p_calibrated``/
    ``elo_offset`` are ``None`` and ``contributions`` is ``[]`` when no entry maps to a Lamas
    code — a caller falls back to no virtual-opponent adjustment. ``coverage`` is always a float
    (``0.0`` when ``entries`` is empty), the fraction of raw entries that mapped.

    This is the OPPONENT offset only. The caller's own ``successful`` flag stays the Glicko-2
    observation SCORE — dominance never substitutes for it (module docstring, study §4's
    self-cancellation identity).
    """
    steps = [(canonical_code(e, library=True), e.get("actor") != "partner") for e in entries]
    n_entries = len(entries)
    z, n_mapped = granular_score(steps, weights, granularity="actions_states", gamma=1.0)
    coverage = (n_mapped / n_entries) if n_entries else 0.0

    if n_mapped == 0:
        return {
            "p": None,
            "p_calibrated": None,
            "elo_offset": None,
            "contributions": [],
            "coverage": coverage,
        }

    raw_p = p_own(z)
    params = calibration["calibration"]["parameters"]
    calibrated_p = calibration_apply([z], params)[0]
    offset = elo_offset(calibrated_p)
    limit = (calibration.get("clamp_elo") or {}).get("value")
    if limit is not None:
        offset = clamp_elo(offset, limit)
    contributions = [
        {"code": code, "own": own, "c_k": c_k}
        for code, own, c_k in contribution_shares(steps, weights)
    ]
    return {
        "p": raw_p,
        "p_calibrated": calibrated_p,
        "elo_offset": offset,
        "contributions": contributions,
        "coverage": coverage,
    }
