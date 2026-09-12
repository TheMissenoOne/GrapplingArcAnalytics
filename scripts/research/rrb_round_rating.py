"""RRB-derived partner strength vs the difficulty/intensity sliders — the runner.

Pre-registration: ``docs/research/rrb_round_rating_prereg.md``, written BEFORE any arm was
scored. Report: ``docs/research/rrb_round_rating_study.md``. Outputs: ``out/rrb_study/``.

Two datasets, two questions, never mixed:

(a) **owner rounds** — PRIVATE, one ``owner_id``, pulled from ``user_sessions.data``. Question:
    does RRB dominance explain the recorded round assessment better than ``difficulty`` /
    ``intensity``? Internal-consistency only (prereg §0) — the label is typed by the same
    person in the same form.
(b) **public corpus** — ``matches`` where ``status='final'``, privacy class A. Question: does a
    rating updated by an RRB arm predict the NEXT year's winners better than the production
    global track?

Nothing here writes to the database, to ``site/``, to an artefact, or to any centroid. The only
files written are under ``out/`` (gitignored).

Usage::

    uv run python -m scripts.research.rrb_round_rating --owner-fixture   # pull + cache (a)
    uv run python -m scripts.research.rrb_round_rating --owner           # score (a)
    uv run python -m scripts.research.rrb_round_rating --corpus          # score (b)
    uv run python -m scripts.research.rrb_round_rating --sweep           # A2 grid on (b)
    uv run python -m scripts.research.rrb_round_rating --all
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analysis.lamas_chain import lamas_state
from analysis.markov_weights import block_for_family, load_markov_weights, relative_shares
from analysis.rating_v2.glicko2 import expected_score, update_period
from analysis.rating_v2.models import Observation, RatingState
from analysis.stats_rigor import auc as auc_ci

OUT = Path(__file__).resolve().parents[2] / "out" / "rrb_study"

#: The one owner this study may read. Private data, owner's own product decision (root CLAUDE.md).
OWNER_ID = "0c555ab2-b5a7-44f5-8a6d-b9a901f87959"

SEED = 20260820
N_BOOT = 4000

# Production V2 constants, mirrored from GrapplingArcApp/src/services/rating/ratingV2Calibration.ts
ELO_PER_DIFFICULTY_POINT = 70.0
DIFFICULTY_BASELINE = 5.0
ATTEMPT_WEIGHT = 0.1
GLOBAL_RD_SEED = 220.0
VOLATILITY_SEED = 0.06
#: Absolute seed is irrelevant to every number here — ``expected_score`` reads the DIFFERENCE and
#: every arm's virtual opponent is expressed as an offset from this same value.
GLOBAL_SEED = 1500.0

SUB_FAMILY = ("SUBA", "SUB")


# ── pure: the value table ────────────────────────────────────────────────────────


def marginal_submission_share(doc: Mapping[str, Any], family: str = "global") -> float:
    """The n-weighted mix of ``SUBA``/``SUB`` in one block of the weights artefact.

    The pilot's "terminal marginalised at 0.5582". Recomputed from the artefact's OWN
    ``provenance.actions`` counts rather than quoted, so it moves when the artefact does.
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
    """Per-code dominance value, under one of the three pre-registered terminal settings.

    ``landed`` — the block as published (``SUB`` keeps its partly-circular 0.8065).
    ``marginal`` — both submission codes take the family's n-weighted mix.
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


# ── pure: the dominance score ────────────────────────────────────────────────────


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0


def signed_log_odds(
    steps: Sequence[tuple[str | None, bool]],
    values: Mapping[str, float],
    *,
    gamma: float = 1.0,
    temperature: float = 1.0,
) -> tuple[float, int]:
    """``(Z, n_mapped)`` — the prereg §3 statistic.

    ``steps`` is ``(lamas code or None, is_own)`` in sequence order. Unmapped codes and codes
    absent from ``values`` are DROPPED, not given the block mean: the mean is the no-information
    value for a WEIGHT, and averaging it into a VALUE invents a reading of who was winning.

    ``gamma`` is the length exponent: ``Z = Σ z_i / (n^γ · T)``. γ=1 is the pilot's mean, γ=0 its
    "full compounding" (a plain sum, which is the length artefact), γ=0.5 the √n middle.
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


def p_own(z: float) -> float:
    """Dominance as a probability. ``P_partner = 1 − p_own(z)`` (the pilot's orientation)."""
    return sigmoid(z)


def elo_offset(p: float) -> float:
    """Elo points the virtual partner sits ABOVE the athlete. ``p`` is the athlete's dominance,
    so a dominant round puts the partner BELOW (negative offset)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(p / (1.0 - p))


def competitiveness(p: float) -> float:
    """``C = 1 − 2|p − 0.5|`` — 1 when the round was even, 0 when one side owned it."""
    return 1.0 - 2.0 * abs(p - 0.5)


K_SHAPES = ("linear", "quadratic", "sqrt")


def k_mult(p: float, *, shape: str = "linear", lam: float = 1.0) -> float:
    """The pilot's competitiveness K multiplier, ``λ · shape(C)``."""
    c = competitiveness(p)
    if shape == "linear":
        s = c
    elif shape == "quadratic":
        s = c * c
    elif shape == "sqrt":
        s = math.sqrt(c)
    else:
        raise ValueError(f"shape desconhecido: {shape!r}")
    return lam * s


def budget_lambda(ps: Sequence[float], *, shape: str = "linear") -> float:
    """λ that makes ``mean(k_mult) == 1`` over ``ps`` — prereg §3b's "keep the K budget"."""
    vals = [k_mult(p, shape=shape, lam=1.0) for p in ps]
    m = sum(vals) / len(vals) if vals else 0.0
    return 1.0 / m if m > 0 else 1.0


def self_cancellation_residual(p: float, rd: float = GLOBAL_RD_SEED) -> float:
    """``s − E`` when the SAME round's dominance is both the score and the opponent offset.

    Pre-registered as an exact identity (prereg §2b): it is 0 for every ``p``, because the offset
    is ``elo_offset(p)`` in the Elo scale and Glicko-2's expected score inverts it. Returned as a
    number so a test can assert it rather than a comment claim it.
    """
    e = expected_score(GLOBAL_SEED, rd, GLOBAL_SEED + elo_offset(p), 0.0)
    return p - e


# ── pure: metrics ────────────────────────────────────────────────────────────────


def log_loss(ps: Sequence[float], ys: Sequence[int]) -> float:
    if not ps:
        return float("nan")
    return -sum(
        math.log(max(min(p, 1 - 1e-12), 1e-12)) if y else math.log(max(min(1 - p, 1 - 1e-12), 1e-12))
        for p, y in zip(ps, ys, strict=True)
    ) / len(ps)


def brier(ps: Sequence[float], ys: Sequence[int]) -> float:
    if not ps:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps)


def _auc_point(scores: Sequence[float], ys: Sequence[int]) -> float:
    pos = [s for s, y in zip(scores, ys, strict=True) if y]
    neg = [s for s, y in zip(scores, ys, strict=True) if not y]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def paired_delta_ci(
    a: Sequence[float],
    b: Sequence[float],
    ys: Sequence[int],
    stat: str,
    *,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> dict[str, float]:
    """Paired percentile bootstrap of ``stat(a) − stat(b)``, resampling the UNIT (round/bout).

    Both arms are scored on the same resampled units, which is what makes the interval paired.
    """
    fn = {"auc": _auc_point, "logloss": log_loss, "brier": brier}[stat]
    n = len(ys)
    obs = fn(a, ys) - fn(b, ys)
    if n == 0:
        return {"delta": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        ya = [ys[i] for i in idx]
        if stat == "auc" and (all(ya) or not any(ya)):
            continue
        draws.append(fn([a[i] for i in idx], ya) - fn([b[i] for i in idx], ya))
    draws = sorted(d for d in draws if not math.isnan(d))
    if not draws:
        return {"delta": obs, "lo": float("nan"), "hi": float("nan")}
    lo = draws[int(0.025 * len(draws))]
    hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return {"delta": obs, "lo": lo, "hi": hi}


def spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman ρ, point estimate only (the interval comes from ``stats_rigor.spearman`` where a
    verdict needs one; here ρ is a death-rule threshold, §8.4)."""
    n = len(xs)
    if n < 3:
        return float("nan")

    def rank(v: Sequence[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


# ── data (a): the owner's own rounds — PRIVATE ───────────────────────────────────


@dataclass(frozen=True)
class Round:
    order: tuple[str, int]  # (session createdAt, round index) — the chronological key
    difficulty: float
    intensity: float
    outcome: str
    steps: tuple[tuple[str | None, bool], ...]  # (lamas code, is_own)
    n_entries: int
    n_own: int


def pull_owner_rounds(path: Path) -> dict[str, Any]:
    """READ-ONLY pull of ONE owner's sessions into a local fixture. No other owner is touched.

    Only the fields this study scores are kept: the chronological key, the two steppers, the
    outcome, and per-entry ``(type, label, actor, successful)``. No round text, no reflection, no
    notes, no media, no session title.
    """
    from sqlalchemy import text

    from db.base import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, data, created_at FROM user_sessions "
                "WHERE owner_id = :o AND deleted_at IS NULL AND data IS NOT NULL "
                "ORDER BY created_at, id"
            ),
            {"o": OWNER_ID},
        ).mappings().all()

    sessions = []
    for r in rows:
        d = r["data"] or {}
        rounds = []
        for rd in d.get("rounds") or []:
            rounds.append(
                {
                    "difficulty": rd.get("difficulty"),
                    "intensity": rd.get("intensity"),
                    "outcome": rd.get("outcome"),
                    "entries": [
                        {
                            "type": e.get("type"),
                            "label": e.get("label"),
                            "actor": e.get("actor"),
                            "successful": e.get("successful"),
                        }
                        for e in (rd.get("entries") or [])
                    ],
                }
            )
        sessions.append({"created_at": str(d.get("createdAt") or r["created_at"]), "rounds": rounds})
    sessions.sort(key=lambda s: s["created_at"])
    doc = {"owner_scope": "single owner, private, owner's own study", "sessions": sessions}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return doc


def owner_rounds(doc: Mapping[str, Any]) -> list[Round]:
    out: list[Round] = []
    for s in doc.get("sessions") or []:
        for i, rd in enumerate(s.get("rounds") or []):
            ents = rd.get("entries") or []
            steps = tuple(
                (
                    lamas_state(
                        {"type": e.get("type"), "label": e.get("label"), "successful": e.get("successful")}
                    ),
                    e.get("actor") != "partner",
                )
                for e in ents
            )
            out.append(
                Round(
                    order=(str(s.get("created_at")), i),
                    difficulty=float(rd.get("difficulty") or DIFFICULTY_BASELINE),
                    intensity=float(rd.get("intensity") or 0.0),
                    outcome=str(rd.get("outcome") or ""),
                    steps=steps,
                    n_entries=len(ents),
                    n_own=sum(1 for e in ents if e.get("actor") != "partner"),
                )
            )
    out.sort(key=lambda r: r.order)
    return out


def own_entry_weights(rd: Mapping[str, Any], block: Mapping[str, float] | None) -> list[float]:
    """Production's own-entry observation weights — ``ATTEMPT_WEIGHT × share × n``, mean-1."""
    own = [e for e in (rd.get("entries") or []) if e.get("actor") != "partner"]
    if not own:
        return []
    shares = relative_shares(
        [
            lamas_state({"type": e.get("type"), "label": e.get("label"), "successful": e.get("successful")})
            for e in own
        ],
        block,
    )
    return [ATTEMPT_WEIGHT * s * len(own) for s in shares]


# ── data (b): the public corpus ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Bout:
    bout_id: str
    year: int
    created_at: str
    a: str
    b: str
    winner: str | None
    family: str
    steps: tuple[tuple[str | None, bool], ...]  # own = athlete_a's side


def load_corpus_bouts(min_events: int = 4) -> list[Bout]:
    """Gated public bouts. Same gate as ``analysis.poc.e9_markov.load_corpus``, restated because
    this cell also needs ``winner_id``. READ-ONLY, one ``select``."""
    from sqlalchemy import text

    from analysis.attribution import bout_flags
    from analysis.ruleset_scoring import family_of
    from db.base import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, athlete_a_id, athlete_b_id, winner_id, year, event, created_at, sequence "
                "FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )
        ).mappings().all()

    out: list[Bout] = []
    for r in rows:
        seq = [dict(e) for e in (r["sequence"] or []) if isinstance(e, dict)]
        if len(seq) < min_events:
            continue
        a, b = str(r["athlete_a_id"]), str(r["athlete_b_id"])
        if not bout_flags(seq, a, b)["perspective_reliable"]:
            continue
        out.append(
            Bout(
                bout_id=str(r["id"]),
                year=int(r["year"] or 0),
                created_at=str(r["created_at"]),
                a=a,
                b=b,
                winner=str(r["winner_id"]) if r["winner_id"] else None,
                family=family_of(r["event"]),
                steps=tuple((lamas_state(e), str(e.get("actor_id") or "") == a) for e in seq),
            )
        )
    out.sort(key=lambda x: (x.year, x.created_at, x.bout_id))
    return out


# ── arms ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cell:
    """One point of the pilot's adjustment space."""

    gamma: float = 1.0
    temperature: float = 1.0
    terminal: str = "marginal"
    shape: str = "linear"
    lam: float = 0.549

    def label(self) -> str:
        return f"g{self.gamma}_T{self.temperature}_{self.terminal}_{self.shape}_l{self.lam}"


def dominance(
    steps: Sequence[tuple[str | None, bool]], values: Mapping[str, float], cell: Cell
) -> tuple[float, float, int]:
    """``(Z, P_own, n_mapped)`` for one round/bout under one cell."""
    z, n = signed_log_odds(steps, values, gamma=cell.gamma, temperature=cell.temperature)
    if n == 0:
        return float("nan"), float("nan"), 0
    return z, p_own(z), n


# ── (a) owner: does RRB explain the recorded assessment? ─────────────────────────


def run_owner(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    block = block_for_family("other", weights_doc) or {}
    marginal = marginal_submission_share(weights_doc)
    rounds = owner_rounds(doc)
    labelled = [r for r in rounds if r.outcome in ("succeeded", "failed")]
    ys = [1 if r.outcome == "succeeded" else 0 for r in labelled]

    coverage_all = Counter()
    for r in rounds:
        for code, _own in r.steps:
            coverage_all[code] += 1
    n_entries = sum(coverage_all.values())
    n_mapped_entries = n_entries - coverage_all[None]

    both_sides = [
        r
        for r in labelled
        if any(c is not None and o for c, o in r.steps) and any(c is not None and not o for c, o in r.steps)
    ]

    res: dict[str, Any] = {
        "n_rounds": len(rounds),
        "n_labelled": len(labelled),
        "base_rate": sum(ys) / len(ys) if ys else float("nan"),
        "coverage_entries": n_mapped_entries / n_entries if n_entries else 0.0,
        "n_labelled_both_sides": len(both_sides),
        "marginal_submission_share": marginal,
        "arms": {},
        "sub_share_of_mapped": sum(coverage_all[c] for c in SUB_FAMILY) / max(n_mapped_entries, 1),
    }

    def add_arm(name: str, scores: list[float], note: str = "") -> None:
        ok = [(s, y, r) for s, y, r in zip(scores, ys, labelled, strict=True) if not math.isnan(s)]
        if not ok:
            res["arms"][name] = {"estimable": False, "reason": "no scored unit"}
            return
        sc = [s for s, _, _ in ok]
        yy = [y for _, y, _ in ok]
        sep = auc_ci(sc, [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
        lens = [float(sum(1 for c, _ in r.steps if c is not None)) for _, _, r in ok]
        res["arms"][name] = {
            "estimable": True,
            "n": len(ok),
            "coverage": len(ok) / len(labelled),
            "auc": sep.auc,
            "auc_lo": sep.lo,
            "auc_hi": sep.hi,
            "verdict": sep.verdict,
            # ρ is only meaningful for the signed-dominance arms; a raw stepper has no |Z|.
            "rho_len": spearman_rho([abs(s) for s in sc], lens) if name.startswith("A") else float("nan"),
            "note": note,
        }

    # N1 — marginal rate (constant); AUC undefined by construction, reported as 0.5.
    res["arms"]["N1_marginal"] = {
        "estimable": True,
        "n": len(labelled),
        "coverage": 1.0,
        "auc": 0.5,
        "auc_lo": 0.5,
        "auc_hi": 0.5,
        "verdict": "chance",
        "rho_len": float("nan"),
        "note": "constant predictor at the base rate",
    }
    add_arm("N2_difficulty", [r.difficulty for r in labelled], "raw difficulty stepper")
    add_arm("N3_intensity", [r.intensity for r in labelled], "raw intensity stepper")
    # ORIENTATION (deviation from the prereg, declared in the report's §deviations). AUC is
    # direction-sensitive and the prereg fixed no sign for the steppers. The sign is not free
    # here: the slider's own UI semantics are "how hard was this round", so "harder ⇒ less
    # likely to have succeeded" is a PRIOR, not a reading of the table. The raw arms stay in
    # the table so the flip is visible rather than silently absorbed.
    add_arm("N2_difficulty_oriented", [-r.difficulty for r in labelled], "−difficulty (see orientation note)")
    add_arm("N3_intensity_oriented", [-r.intensity for r in labelled], "−intensity (see orientation note)")
    add_arm(
        "N4_length",
        [float(sum(1 for c, _ in r.steps if c is not None)) for r in labelled],
        "mapped-sequence length alone",
    )

    for terminal in ("landed", "marginal", "drop"):
        vals = action_values(block, terminal=terminal, marginal=marginal)
        for gamma in (0.0, 0.5, 1.0):
            cell = Cell(gamma=gamma, terminal=terminal)
            zs = [dominance(r.steps, vals, cell)[0] for r in labelled]
            add_arm(f"A1_g{gamma}_{terminal}", zs, f"signed log-odds, γ={gamma}, terminal={terminal}")

    # Both-sides subset (death rule 2) for the headline cell.
    vals_m = action_values(block, terminal="marginal", marginal=marginal)
    if both_sides:
        sub_scores, sub_ys = [], []
        for r in both_sides:
            z, _, n = dominance(r.steps, vals_m, Cell())
            if n:
                sub_scores.append(z)
                sub_ys.append(1 if r.outcome == "succeeded" else 0)
        if sub_scores and 0 < sum(sub_ys) < len(sub_ys):
            sep = auc_ci(sub_scores, [bool(y) for y in sub_ys], n_boot=N_BOOT, seed=SEED)
            res["both_sides_auc"] = {"n": len(sub_scores), "auc": sep.auc, "lo": sep.lo, "hi": sep.hi}
        else:
            res["both_sides_auc"] = {"n": len(sub_scores), "auc": float("nan")}

    # H1 / H1b — paired ΔAUC vs difficulty on the common subset.
    def paired_vs_difficulty(terminal: str) -> dict[str, float]:
        vals = action_values(block, terminal=terminal, marginal=marginal)
        a, b, yy = [], [], []
        for r, y in zip(labelled, ys, strict=True):
            z, _, n = dominance(r.steps, vals, Cell(terminal=terminal))
            if n == 0:
                continue
            a.append(z)
            b.append(-r.difficulty)  # oriented, see the N2 orientation note above
            yy.append(y)
        return paired_delta_ci(a, b, yy, "auc") | {"n": len(yy)}

    res["H1"] = paired_vs_difficulty("marginal")
    res["H1b"] = paired_vs_difficulty("drop")

    # H2 / H2n — prequential Glicko-2, chronological, ex-post conditional (prereg §6).
    res["prequential"] = run_owner_prequential(doc, block, marginal)
    res["self_cancellation_max_abs_residual"] = max(
        abs(self_cancellation_residual(p)) for p in (0.05, 0.2, 0.4, 0.5, 0.6, 0.8, 0.95)
    )
    return res


def run_owner_prequential(
    doc: Mapping[str, Any],
    block: Mapping[str, float],
    marginal: float,
    *,
    library: bool = False,
    granularity: str = "actions",
    offsets: Mapping[int, float] | None = None,
    score_mode: str = "flag",
) -> dict[str, Any]:
    """A0 / A0n / A4 as forecasters of the round assessment, state advancing chronologically.

    The STATE only ever sees earlier rounds. The OFFSET for round *t* is read from round *t*'s own
    input — ``difficulty`` for A0, nothing for A0n, the round's own actions for A4 — so all three
    arms are conditional on one round-*t* input and the comparison is like-for-like. Labelled
    ex-post in the report because A4's input is only knowable after the round.
    """
    vals = action_values(block, terminal="marginal", marginal=marginal)
    raw = [(s, i, rd) for s in (doc.get("sessions") or []) for i, rd in enumerate(s.get("rounds") or [])]
    raw.sort(key=lambda t: (str(t[0].get("created_at")), t[1]))

    arms = ("A0_difficulty", "A0n_zero", "A4_rrb")
    # `offsets` (prereg §C6 head-to-head 2) substitutes an externally computed per-round offset —
    # keyed by CHRONOLOGICAL round index, the same order this loop walks — into the A4 slot, so
    # the two candidates are compared through identical Glicko-2 machinery.
    state = {a: RatingState(GLOBAL_SEED, GLOBAL_RD_SEED, VOLATILITY_SEED) for a in arms}
    n_obs_total: Counter[str] = Counter()
    ps: dict[str, list[float]] = {a: [] for a in arms}
    ys: list[int] = []
    skipped_no_rrb = 0

    for _round_i, (_s, _i, rd) in enumerate(raw):
        outcome = str(rd.get("outcome") or "")
        ents = rd.get("entries") or []
        own = [e for e in ents if e.get("actor") != "partner"]
        steps = tuple((canonical_code(e, library=library), e.get("actor") != "partner") for e in ents)
        z_dom, n_mapped = granular_score(steps, vals, granularity=granularity, gamma=1.0)
        p_dom = p_own(z_dom) if n_mapped else float("nan")
        # NOT named `offsets`: that is the PARAMETER, and rebinding it here made every round after
        # the first read the per-arm dict instead of the caller's per-round table (silently
        # collapsing the A4 arm onto A0n_zero). Caught by checking the arm's numbers against the
        # offsets that were actually passed in.
        arm_offsets = {
            "A0_difficulty": ELO_PER_DIFFICULTY_POINT
            * (float(rd.get("difficulty") or DIFFICULTY_BASELINE) - DIFFICULTY_BASELINE),
            "A0n_zero": 0.0,
            "A4_rrb": (
                offsets.get(_round_i, 0.0) if offsets is not None
                else (elo_offset(p_dom) if n_mapped else 0.0)
            ),
        }
        if not n_mapped:
            skipped_no_rrb += 1

        if outcome in ("succeeded", "failed"):
            ys.append(1 if outcome == "succeeded" else 0)
            for a in arms:
                st = state[a]
                ps[a].append(expected_score(st.rating, st.deviation, st.rating + arm_offsets[a], st.deviation))

        if outcome == "no_attempt" or not own:
            continue
        ws = own_entry_weights(rd, block)
        # §D2: the entry's score comes from the chosen mode — the manual flag, the sequence's own
        # inference, or inference with the flag as fallback. An UNRESOLVED entry under
        # `inferred_only` produces NO observation at all (never an invented 0 or 1).
        inf_all = entry_success_table(ents)
        own_inf = [inf_all[k] for k, e in enumerate(ents) if e.get("actor") != "partner"]
        scored = [
            entry_score(e, inf, score_mode, is_last=(k == len(own) - 1))
            for k, (e, inf) in enumerate(zip(own, own_inf, strict=True))
        ]
        for a in arms:
            st = state[a]
            obs = [
                Observation(
                    opponent_rating=st.rating + arm_offsets[a],
                    opponent_deviation=st.deviation,
                    score=sc,
                    weight=w,
                )
                for (sc, _src), w in zip(scored, ws, strict=True)
                if sc is not None
            ]
            n_obs_total[a] += len(obs)
            state[a] = update_period(st, obs, tau=0.5, center=GLOBAL_SEED)

    base = sum(ys) / len(ys) if ys else float("nan")
    rows = {
        a: {
            "logloss": log_loss(ps[a], ys),
            "brier": brier(ps[a], ys),
            "auc": _auc_point(ps[a], ys),
            "final_rating": state[a].rating,
            "final_rd": state[a].deviation,
        }
        for a in arms
    }
    rows["N1_marginal"] = {
        "logloss": log_loss([base] * len(ys), ys),
        "brier": brier([base] * len(ys), ys),
        "auc": 0.5,
        "final_rating": float("nan"),
        "final_rd": float("nan"),
    }
    return {
        "n": len(ys),
        "base_rate": base,
        "rounds_without_rrb": skipped_no_rrb,
        "observations_scored": dict(n_obs_total),
        "_ps": ps["A0_difficulty"],
        "_ys": ys,
        "arms": rows,
        "H2_A4_vs_A0": paired_delta_ci(ps["A4_rrb"], ps["A0_difficulty"], ys, "logloss"),
        "H2n_A4_vs_A0n": paired_delta_ci(ps["A4_rrb"], ps["A0n_zero"], ys, "logloss"),
        "H2x_A0_vs_A0n": paired_delta_ci(ps["A0_difficulty"], ps["A0n_zero"], ys, "logloss"),
    }


# ── (b) corpus: winner identification + next-year prediction ─────────────────────


def run_corpus_q1(bouts: Sequence[Bout], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """H3/H3b — does bout-level dominance identify the recorded winner?"""
    marginal = marginal_submission_share(weights_doc)
    decided = [b for b in bouts if b.winner in (b.a, b.b)]
    ys = [1 if b.winner == b.a else 0 for b in decided]
    out: dict[str, Any] = {
        "n_gated": len(bouts),
        "n_decided": len(decided),
        "base_rate_a_wins": sum(ys) / len(ys) if ys else float("nan"),
        "arms": {},
    }

    def add(name: str, scores: list[float], lens: list[float]) -> None:
        ok = [(s, y, n) for s, y, n in zip(scores, ys, lens, strict=True) if not math.isnan(s)]
        if not ok:
            out["arms"][name] = {"estimable": False}
            return
        sc = [s for s, _, _ in ok]
        yy = [bool(y) for _, y, _ in ok]
        sep = auc_ci(sc, yy, n_boot=N_BOOT, seed=SEED)
        out["arms"][name] = {
            "estimable": True,
            "n": len(ok),
            "coverage": len(ok) / len(decided),
            "auc": sep.auc,
            "auc_lo": sep.lo,
            "auc_hi": sep.hi,
            "verdict": sep.verdict,
            "rho_len": spearman_rho([abs(s) for s in sc], [n for _, _, n in ok]),
        }

    lens_all = [float(sum(1 for c, _ in b.steps if c is not None)) for b in decided]
    add("N4_length", lens_all, lens_all)
    for terminal in ("landed", "marginal", "drop"):
        block = block_for_family("other", weights_doc) or {}
        vals = action_values(block, terminal=terminal, marginal=marginal)
        for gamma in (0.0, 0.5, 1.0):
            cell = Cell(gamma=gamma, terminal=terminal)
            zs, ns = [], []
            for b in decided:
                z, _, n = dominance(b.steps, vals, cell)
                zs.append(z)
                ns.append(float(n))
            add(f"A1_g{gamma}_{terminal}", zs, ns)
    return out


def _replay_fold(
    train: Sequence[Bout],
    test: Sequence[Bout],
    arm: str,
    vals: Mapping[str, float],
    cell: Cell,
    granularity: str = "actions",
) -> dict[str, Any]:
    """One rolling-origin fold. Replay ``train``, then prequentially predict+update ``test``.

    Every arm PREDICTS the same way — ``expected_score`` on the two real athlete states — so the
    arms differ only in how they UPDATE. That is the comparison the owner's question needs.
    """
    st: dict[str, RatingState] = {}

    def get(x: str) -> RatingState:
        return st.setdefault(x, RatingState(GLOBAL_SEED, 350.0, VOLATILITY_SEED))

    def update(b: Bout) -> None:
        sa, sb = get(b.a), get(b.b)
        win_a = 1.0 if b.winner == b.a else 0.0
        z, n = granular_score(b.steps, vals, granularity=granularity, gamma=cell.gamma)
        p = p_own(z) if n else float("nan")
        if arm == "A0g_winner":
            score_a, opp_a, opp_b, w = win_a, sb, sa, 1.0
        elif arm == "A3_rrb_real_opponent":
            if n == 0:
                return
            score_a, opp_a, opp_b, w = p, sb, sa, k_mult(p, shape=cell.shape, lam=cell.lam)
        elif arm == "A1_rrb_selfanchored":
            if n == 0:
                return
            off = elo_offset(p)
            score_a = p
            opp_a = RatingState(sa.rating + off, sa.deviation, sa.volatility)
            opp_b = RatingState(sb.rating - off, sb.deviation, sb.volatility)
            w = 1.0
        elif arm == "A4_blend":
            if n == 0:
                score_a, opp_a, opp_b, w = win_a, sb, sa, 1.0
            else:
                score_a, opp_a, opp_b, w = 0.5 * win_a + 0.5 * p, sb, sa, 1.0
        else:
            raise ValueError(arm)
        na = update_period(
            sa, [Observation(opp_a.rating, opp_a.deviation, score_a, w)], tau=0.5, center=GLOBAL_SEED
        )
        nb = update_period(
            sb, [Observation(opp_b.rating, opp_b.deviation, 1.0 - score_a, w)], tau=0.5, center=GLOBAL_SEED
        )
        st[b.a], st[b.b] = na, nb

    for b in train:
        update(b)
    ps, ys = [], []
    for b in test:
        sa, sb = get(b.a), get(b.b)
        ps.append(expected_score(sa.rating, sa.deviation, sb.rating, sb.deviation))
        ys.append(1 if b.winner == b.a else 0)
        update(b)
    return {"ps": ps, "ys": ys, "n": len(ys), "n_athletes": len(st)}


def run_corpus_q2(
    bouts: Sequence[Bout],
    weights_doc: Mapping[str, Any],
    cutoffs: Sequence[int] = (2023, 2024, 2025),
) -> dict[str, Any]:
    """H4 — rolling-origin next-year winner prediction, per fold, never pooled."""
    marginal = marginal_submission_share(weights_doc)
    block = block_for_family("other", weights_doc) or {}
    vals = action_values(block, terminal="marginal", marginal=marginal)
    cell = Cell()
    decided = [b for b in bouts if b.winner in (b.a, b.b)]
    # (label, update rule, granularity) — every granularity goes through A3's rule (RRB score
    # against the REAL opponent), which prereg §2b's identity leaves as the only live RRB update.
    arms: tuple[tuple[str, str, str], ...] = (
        ("A0g_winner", "A0g_winner", "actions"),
        ("A1_rrb_selfanchored", "A1_rrb_selfanchored", "actions"),
        ("A3_rrb_real_opponent", "A3_rrb_real_opponent", "actions"),
        ("A4_blend", "A4_blend", "actions"),
        ("A3_states", "A3_rrb_real_opponent", "states"),
        ("A3_states_occ", "A3_rrb_real_opponent", "states_occ"),
        ("A3_edges", "A3_rrb_real_opponent", "edges"),
        ("A3_edges_states", "A3_rrb_real_opponent", "edges_states"),
    )

    folds = []
    for cut in cutoffs:
        train = [b for b in decided if b.year <= cut]
        test = [b for b in decided if b.year == cut + 1]
        if not train or not test:
            folds.append({"cutoff": cut, "skipped": "no train or no test bouts"})
            continue
        row: dict[str, Any] = {
            "cutoff": cut,
            "test_year": cut + 1,
            "train_bouts": len(train),
            "test_bouts": len(test),
            "arms": {},
        }
        got = {}
        for label, rule, gran in arms:
            r = _replay_fold(train, test, rule, vals, cell, gran)
            got[label] = r
            a = label
            row["arms"][a] = {
                "logloss": log_loss(r["ps"], r["ys"]),
                "brier": brier(r["ps"], r["ys"]),
                "auc": _auc_point(r["ps"], r["ys"]),
            }
        ys = got["A0g_winner"]["ys"]
        base = sum(ys) / len(ys)
        row["null_coinflip"] = {"logloss": math.log(2), "brier": 0.25, "auc": 0.5}
        row["N1_marginal"] = {
            "logloss": log_loss([base] * len(ys), ys),
            "brier": brier([base] * len(ys), ys),
            "auc": 0.5,
        }
        for a in [x[0] for x in arms[1:]]:
            row["arms"][a]["delta_logloss_vs_A0g"] = paired_delta_ci(
                got[a]["ps"], got["A0g_winner"]["ps"], ys, "logloss"
            )
        folds.append(row)
    return {"n_decided": len(decided), "folds": folds}


def run_sweep(bouts: Sequence[Bout], weights_doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A2 — the 324-cell grid, exploratory, no verdict (prereg §4)."""
    marginal = marginal_submission_share(weights_doc)
    block = block_for_family("other", weights_doc) or {}
    decided = [b for b in bouts if b.winner in (b.a, b.b)]
    ys = [1 if b.winner == b.a else 0 for b in decided]
    rows = []
    for terminal in ("landed", "marginal", "drop"):
        vals = action_values(block, terminal=terminal, marginal=marginal)
        for gamma in (0.0, 0.5, 1.0):
            for temperature in (0.75, 1.0, 1.5):
                cell0 = Cell(gamma=gamma, temperature=temperature, terminal=terminal)
                scored = [dominance(b.steps, vals, cell0) for b in decided]
                ok = [(p, y, n) for (_z, p, n), y in zip(scored, ys, strict=True) if n]
                if not ok:
                    continue
                pp = [p for p, _, _ in ok]
                yy = [bool(y) for _, y, _ in ok]
                a = _auc_point(pp, [y for _, y, _ in ok])
                ll = log_loss(pp, [y for _, y, _ in ok])
                br = brier(pp, [y for _, y, _ in ok])
                rho = spearman_rho([abs(_logit(p)) for p in pp], [float(n) for _, _, n in ok])
                for shape in K_SHAPES:
                    blam = budget_lambda(pp, shape=shape)
                    for lam in (0.4, 0.549, 0.7, 1.0):
                        rows.append(
                            {
                                "gamma": gamma,
                                "temperature": temperature,
                                "terminal": terminal,
                                "shape": shape,
                                "lam": lam,
                                "n": len(ok),
                                "coverage": len(ok) / len(decided),
                                "auc": a,
                                "logloss": ll,
                                "brier": br,
                                "rho_len": rho,
                                "budget_lambda": blam,
                                "mean_k_mult": sum(k_mult(p, shape=shape, lam=lam) for p in pp) / len(pp),
                            }
                        )
                _ = yy
    rows.sort(key=lambda r: (r["terminal"], r["gamma"], r["temperature"], r["shape"], r["lam"]))
    return rows


# ── figures ──────────────────────────────────────────────────────────────────────


def _figures(owner: Mapping[str, Any] | None, q1: Mapping[str, Any] | None, sweep: Sequence[Mapping[str, Any]]) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = []
    if q1:
        names = sorted(k for k, v in (q1.get("arms") or {}).items() if v.get("estimable"))
        fig, ax = plt.subplots(figsize=(8, max(3, 0.32 * len(names))))
        ys = range(len(names))
        ax.errorbar(
            [q1["arms"][n]["auc"] for n in names],
            list(ys),
            xerr=[
                [q1["arms"][n]["auc"] - q1["arms"][n]["auc_lo"] for n in names],
                [q1["arms"][n]["auc_hi"] - q1["arms"][n]["auc"] for n in names],
            ],
            fmt="o",
            color="#1f77b4",
        )
        ax.axvline(0.5, color="#999", ls="--", lw=1)
        ax.set_yticks(list(ys))
        ax.set_yticklabels(names, fontsize=8)
        ax.set_xlabel("AUC — identifies the recorded winner (public corpus)")
        fig.tight_layout()
        p = OUT / "corpus_q1_auc.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p.name)
    if sweep:
        cells = sorted({(r["terminal"], r["gamma"]) for r in sweep})
        fig, ax = plt.subplots(figsize=(7, 3.4))
        for terminal in ("landed", "marginal", "drop"):
            xs = [g for t, g in cells if t == terminal]
            vals = [
                next(r["auc"] for r in sweep if r["terminal"] == terminal and r["gamma"] == g)
                for g in xs
            ]
            rhos = [
                next(r["rho_len"] for r in sweep if r["terminal"] == terminal and r["gamma"] == g)
                for g in xs
            ]
            ax.plot(xs, vals, marker="o", label=f"AUC · {terminal}")
            ax.plot(xs, rhos, marker="x", ls=":", label=f"ρ(|Z|,len) · {terminal}")
        ax.axhline(0.5, color="#999", ls="--", lw=1)
        ax.set_xlabel("γ (length exponent)")
        ax.legend(fontsize=7, ncol=2)
        fig.tight_layout()
        p = OUT / "sweep_gamma_terminal.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p.name)
    if owner:
        arms = sorted(k for k, v in (owner.get("arms") or {}).items() if v.get("estimable"))
        fig, ax = plt.subplots(figsize=(8, max(3, 0.32 * len(arms))))
        ys = range(len(arms))
        ax.errorbar(
            [owner["arms"][n]["auc"] for n in arms],
            list(ys),
            xerr=[
                [owner["arms"][n]["auc"] - owner["arms"][n]["auc_lo"] for n in arms],
                [owner["arms"][n]["auc_hi"] - owner["arms"][n]["auc"] for n in arms],
            ],
            fmt="o",
            color="#d62728",
        )
        ax.axvline(0.5, color="#999", ls="--", lw=1)
        ax.set_yticks(list(ys))
        ax.set_yticklabels(arms, fontsize=8)
        ax.set_xlabel("AUC — round assessment (owner's own rounds, aggregate only)")
        fig.tight_layout()
        p = OUT / "owner_arm_auc.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        made.append(p.name)
    return made


# ── CLI ──────────────────────────────────────────────────────────────────────────


def _write(name: str, payload: Any) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return p


def _csv(name: str, rows: Sequence[Mapping[str, Any]]) -> Path:
    import csv

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    if rows:
        with p.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner-fixture", action="store_true", help="pull the owner's rounds into out/")
    ap.add_argument("--owner", action="store_true", help="score dataset (a)")
    ap.add_argument("--corpus", action="store_true", help="score dataset (b)")
    ap.add_argument("--sweep", action="store_true", help="A2 grid on dataset (b)")
    ap.add_argument("--addendum", action="store_true", help="prereg §A1-A5: granularity arms + T2")
    ap.add_argument("--section-b", action="store_true", help="prereg §B: full catalogued-node chain")
    ap.add_argument("--section-c", action="store_true", help="prereg §C: personal layers + coherence")
    ap.add_argument("--section-c6", action="store_true", help="prereg §C6: agreed four-layer arm")
    ap.add_argument("--section-d", action="store_true", help="prereg §D: inferred per-action success")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    if args.all:
        args.owner_fixture = args.owner = args.corpus = args.sweep = args.addendum = args.section_b = args.section_c = args.section_c6 = args.section_d = True
    if not any((args.owner_fixture, args.owner, args.corpus, args.sweep, args.addendum, args.section_b, args.section_c, args.section_c6, args.section_d)):
        ap.print_help()
        return 0

    weights_doc = load_markov_weights()
    if weights_doc is None:
        print("data/rating/markov_action_weights.json ausente — nada a medir")
        return 1

    fixture = OUT / "owner_rounds.json"
    owner_res = q1 = q2 = None
    sweep_rows: list[dict[str, Any]] = []

    if args.owner_fixture:
        doc = pull_owner_rounds(fixture)
        print(f"fixture: {fixture} ({len(doc['sessions'])} sessions)")
    if args.owner:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        owner_res = run_owner(doc, weights_doc)
        print("owner:", _write("owner_results.json", owner_res))
    if args.corpus:
        bouts = load_corpus_bouts()
        q1 = run_corpus_q1(bouts, weights_doc)
        q2 = run_corpus_q2(bouts, weights_doc)
        print("corpus q1:", _write("corpus_q1.json", q1))
        print("corpus q2:", _write("corpus_q2.json", q2))
    if args.sweep:
        bouts = load_corpus_bouts()
        sweep_rows = run_sweep(bouts, weights_doc)
        print("sweep:", _csv("sweep.csv", sweep_rows), len(sweep_rows), "cells")
    if args.addendum:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        print("addendum:", _write("addendum.json", run_addendum(doc, weights_doc)))
    if args.section_b:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        print("section B:", _write("section_b.json", run_section_b(doc, weights_doc)))
    if args.section_c:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        print("section C:", _write("section_c.json", run_section_c(doc, weights_doc)))
    if args.section_c6:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        print("section C6:", _write("section_c6.json", run_section_c6(doc, weights_doc)))
    if args.section_d:
        doc = json.loads(fixture.read_text(encoding="utf-8"))
        print("section D:", _write("section_d.json", run_section_d(doc, weights_doc)))
    figs = _figures(owner_res, q1, sweep_rows)
    if figs:
        print("figures:", ", ".join(figs))
    return 0



# ══ ADDENDUM (prereg §A1–A5) ════════════════════════════════════════════════════
# Canonical label mapping, the granularity arms, and the objective finish-side target.


GRANULARITIES = ("actions", "states", "states_occ", "edges", "actions_states", "edges_states")


def canonical_code(entry: Mapping[str, Any], *, library: bool = True) -> str | None:
    """Lamas code for an APP entry, optionally through the canonical library first.

    The owner logs in pt-BR (``control/Costas``, ``transition/Puxada para Guarda``) and
    ``lamas_chain``'s label rules match English tokens, so reading the raw label loses the single
    largest action in the log. ``technique_match.clean_label`` is the existing pt/variant →
    canonical-English resolver; routing through it takes the owner's coverage from 28.75 % to
    50.00 % (prereg §A1). ``library=False`` reproduces the pre-addendum numbers.
    """
    from analysis.technique_match import clean_label

    label = str(entry.get("label") or "")
    if library:
        label = clean_label(label, str(entry.get("type") or ""))
    return lamas_state({"type": entry.get("type"), "label": label, "successful": entry.get("successful")})


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
    """``(Z, n_used)`` under one evidence granularity (prereg §A3).

    ``actions``    Σ z_i / n^γ — repetition-weighted (the pilot's statistic).
    ``states``     z of the LAST mapped step — a position, length-free by construction.
    ``states_occ`` mean z over DISTINCT ``(code, actor)`` pairs — occupancy, not repetition.
    ``edges``      Σ (z_{i+1} − z_i) / (n−1)^γ — the PROGRESSION (VAEP/xT form). At γ=0 it
                   telescopes to ``z_n − z_1``, so it is length-free in the strongest sense.
    ``*_states``   the mean of the two component Zs; coherent because both are log-odds.
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
        za, na = granular_score(steps, values, granularity=first, gamma=gamma, temperature=temperature)
        zb, nb = granular_score(steps, values, granularity="states", gamma=gamma, temperature=temperature)
        if na == 0 or nb == 0:
            return float("nan"), 0
        return (za + zb) / 2.0, max(na, nb)
    raise ValueError(f"granularity desconhecida: {granularity!r}")


# ── targets ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Unit:
    """One scored unit, target-agnostic: a prefix of steps and a binary label."""

    steps: tuple[tuple[str | None, bool], ...]
    label: int
    difficulty: float
    intensity: float


def owner_units(doc: Mapping[str, Any], target: str, *, library: bool = True) -> list[Unit]:
    """T1 = round assessment (succeeded/failed). T2 = finish-side from the PREFIX.

    T2 keeps only rounds whose LAST entry is a landed submission, deletes that entry, and labels
    by whose it was. No self-report enters the label.
    """
    units: list[Unit] = []
    for s in doc.get("sessions") or []:
        for rd in s.get("rounds") or []:
            ents = list(rd.get("entries") or [])
            diff = float(rd.get("difficulty") or DIFFICULTY_BASELINE)
            inten = float(rd.get("intensity") or 0.0)
            if target == "T1":
                if str(rd.get("outcome") or "") not in ("succeeded", "failed"):
                    continue
                steps = tuple((canonical_code(e, library=library), e.get("actor") != "partner") for e in ents)
                units.append(Unit(steps, 1 if rd["outcome"] == "succeeded" else 0, diff, inten))
            elif target == "T2":
                if not ents:
                    continue
                last = ents[-1]
                if str(last.get("type") or "") != "submission" or last.get("successful") is False:
                    continue
                steps = tuple(
                    (canonical_code(e, library=library), e.get("actor") != "partner") for e in ents[:-1]
                )
                units.append(Unit(steps, 1 if last.get("actor") != "partner" else 0, diff, inten))
            else:
                raise ValueError(target)
    return units


def corpus_units(bouts: Sequence[Bout], target: str, win_types: Mapping[str, str] | None = None) -> list[Unit]:
    """T1 = recorded winner from the whole bout. T2 = finish-side from the prefix of SUBMISSION bouts."""
    units: list[Unit] = []
    for b in bouts:
        if b.winner not in (b.a, b.b):
            continue
        label = 1 if b.winner == b.a else 0
        if target == "T1":
            units.append(Unit(b.steps, label, float("nan"), float("nan")))
        elif target == "T2":
            if (win_types or {}).get(b.bout_id) != "SUBMISSION":
                continue
            idx = [i for i, (c, _) in enumerate(b.steps) if c == "SUB"]
            if not idx:
                continue
            units.append(Unit(tuple(b.steps[: idx[-1]]), label, float("nan"), float("nan")))
        else:
            raise ValueError(target)
    return units


def score_arms(
    units: Sequence[Unit],
    weights_doc: Mapping[str, Any],
    *,
    production: Sequence[float] | None,
    gammas: Sequence[float] = (0.0, 1.0),
) -> dict[str, Any]:
    """Every granularity × terminal × γ arm on one target, each with its paired Δ vs production.

    ``production`` is the production V2 signal for this target on the SAME units, already oriented
    (−difficulty on the owner's rounds). ``None`` where the target has no owner-side production
    signal. An arm "beats production" only when the paired ΔAUC interval excludes 0 (prereg §A5).
    """
    block = block_for_family("other", weights_doc) or {}
    marginal = marginal_submission_share(weights_doc)
    ys = [u.label for u in units]
    out: dict[str, Any] = {
        "n_units": len(units),
        "base_rate": sum(ys) / len(ys) if ys else float("nan"),
        "arms": {},
    }
    if production is not None:
        prod_ok = [(p, y) for p, y in zip(production, ys, strict=True) if not math.isnan(p)]
        if prod_ok and 0 < sum(y for _, y in prod_ok) < len(prod_ok):
            sep = auc_ci([p for p, _ in prod_ok], [bool(y) for _, y in prod_ok], n_boot=N_BOOT, seed=SEED)
            out["arms"]["PROD_difficulty_oriented"] = {
                "n": len(prod_ok),
                "coverage": len(prod_ok) / len(units),
                "auc": sep.auc,
                "auc_lo": sep.lo,
                "auc_hi": sep.hi,
                "verdict": sep.verdict,
                "rho_len": float("nan"),
                "beats_production": None,
            }

    for terminal in ("marginal", "drop"):
        vals = action_values(block, terminal=terminal, marginal=marginal)
        for gran in GRANULARITIES:
            for gamma in gammas:
                # γ does not enter `states`/`states_occ`; run one point and label it.
                if gran in ("states", "states_occ") and gamma != gammas[0]:
                    continue
                name = f"{gran}_{terminal}" + ("" if gran in ("states", "states_occ") else f"_g{gamma}")
                rows = []
                for i, u in enumerate(units):
                    z, k = granular_score(u.steps, vals, granularity=gran, gamma=gamma)
                    if k == 0 or math.isnan(z):
                        continue
                    rows.append((i, z, u.label, float(sum(1 for c, _ in u.steps if c is not None))))
                if not rows or not (0 < sum(r[2] for r in rows) < len(rows)):
                    out["arms"][name] = {"estimable": False, "n": len(rows)}
                    continue
                sc = [r[1] for r in rows]
                yy = [r[2] for r in rows]
                sep = auc_ci(sc, [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
                entry: dict[str, Any] = {
                    "n": len(rows),
                    "coverage": len(rows) / len(units),
                    "auc": sep.auc,
                    "auc_lo": sep.lo,
                    "auc_hi": sep.hi,
                    "verdict": sep.verdict,
                    "rho_len": spearman_rho([abs(s) for s in sc], [r[3] for r in rows]),
                    "brier": brier([p_own(s) for s in sc], yy),
                    "logloss": log_loss([p_own(s) for s in sc], yy),
                }
                if production is not None:
                    pa = [production[r[0]] for r in rows]
                    if not any(math.isnan(p) for p in pa):
                        d = paired_delta_ci(sc, pa, yy, "auc")
                        entry["delta_auc_vs_production"] = d
                        entry["beats_production"] = bool(d["lo"] > 0)
                out["arms"][name] = entry
    return out


def run_addendum(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Prereg §A1–A5 in one pass: mapper contrast, T1/T2 on both datasets, λ and ρ adjudication."""
    from sqlalchemy import text

    from db.base import get_engine

    res: dict[str, Any] = {}

    # A1 — the mapper defect, both readings.
    cov: dict[str, Any] = {}
    for lib in (False, True):
        codes: Counter[str | None] = Counter()
        for s in doc.get("sessions") or []:
            for rd in s.get("rounds") or []:
                for e in rd.get("entries") or []:
                    codes[canonical_code(e, library=lib)] += 1
        tot = sum(codes.values())
        cov["library" if lib else "raw"] = {
            "coverage": (tot - codes[None]) / tot if tot else 0.0,
            "n_entries": tot,
            "codes": {str(k): v for k, v in sorted(codes.items(), key=lambda kv: str(kv[0])) if k},
        }
    res["A1_mapper"] = cov

    # T1 / T2 on the owner's rounds, canonical mapper.
    for target in ("T1", "T2"):
        units = owner_units(doc, target, library=True)
        prod = [-u.difficulty for u in units]
        res[f"owner_{target}"] = score_arms(units, weights_doc, production=prod)
        res[f"owner_{target}"]["intensity_oriented_auc"] = (
            auc_ci([-u.intensity for u in units], [bool(u.label) for u in units], n_boot=N_BOOT, seed=SEED).auc
            if units and 0 < sum(u.label for u in units) < len(units)
            else float("nan")
        )
    # Raw-mapper contrast on T1 only (the pre-addendum reading).
    units_raw = owner_units(doc, "T1", library=False)
    res["owner_T1_rawmapper"] = score_arms(
        units_raw, weights_doc, production=[-u.difficulty for u in units_raw]
    )

    # T1 / T2 on the corpus.
    bouts = load_corpus_bouts()
    with get_engine().connect() as conn:
        wt = {
            str(r["id"]): str(r["win_type"] or "")
            for r in conn.execute(text("SELECT id, win_type FROM matches WHERE status='final'")).mappings()
        }
    for target in ("T1", "T2"):
        units = corpus_units(bouts, target, wt)
        res[f"corpus_{target}"] = score_arms(units, weights_doc, production=None)

    # A4 — the two adjudications.
    block = block_for_family("other", weights_doc) or {}
    vals = action_values(block, terminal="marginal", marginal=marginal_submission_share(weights_doc))
    ps, intens = [], []
    for s in doc.get("sessions") or []:
        for rd in s.get("rounds") or []:
            steps = tuple(
                (canonical_code(e, library=True), e.get("actor") != "partner") for e in rd.get("entries") or []
            )
            z, k = granular_score(steps, vals, granularity="actions", gamma=1.0)
            if k:
                ps.append(p_own(z))
                intens.append(float(rd.get("intensity") or 0.0))
    res["A4_lambda"] = {
        "definition": "λ such that mean(λ · shape(1 − 2|P − 0.5|)) = 1 over the scored units",
        "n": len(ps),
        "budget_lambda": {sh: budget_lambda(ps, shape=sh) for sh in K_SHAPES},
        "mean_competitiveness": sum(competitiveness(p) for p in ps) / len(ps) if ps else float("nan"),
    }
    res["prequential_library"] = {
        gran: run_owner_prequential(doc, block, marginal_submission_share(weights_doc),
                                    library=True, granularity=gran)
        for gran in ("actions", "states", "states_occ", "edges_states")
    }
    res["A4_rho_competitiveness_intensity"] = spearman_rho(
        [competitiveness(p) for p in ps], [float(i) for i in intens]
    )
    return res


# ══ ADDENDUM §B — full catalogued-node chain + high-confidence inference ════════
# Owner decision 2026-09-12: replace the Lamas 12-state space with the whole catalogued node
# vocabulary, and splice in the production chain compiler's inferred actions. Prereg §B.

MIN_NODE_COUNT_REPORTED = 5
FIN_OWN, FIN_OPP, END_OTHER = "\x00FIN_OWN", "\x00FIN_OPP", "\x00END"


def node_key_of(label: str, etype: str = "") -> str:
    """The repo's canonical node key, with ``clean_label`` in front.

    ``canonicalize(_normalize_name(...))`` is exactly ``node_rating.node_key_of``; the
    ``clean_label`` prefix is what makes a pt-BR entry and an English corpus event land on ONE key
    (prereg §B1). Never invents its own normalisation.
    """
    from analysis.names import _normalize_name, canonicalize
    from analysis.technique_match import clean_label

    return canonicalize(_normalize_name(clean_label(str(label or ""), str(etype or ""))))


def _compiled_steps(
    events: Sequence[Mapping[str, Any]],
    side_of: Any,
    *,
    actor_readable: bool,
) -> tuple[list[tuple[str, str]], int]:
    """``([(node_key, side)], n_inferred)`` with the compiler's INFERRED actions spliced in.

    ``compile_two_sided`` compiles each side independently and rewrites every observed action's
    ``source_event_index`` back to its ORIGINAL position, so the two sides are merged back into one
    ordered stream on that index. An inferred action carries no index (it was never logged) and is
    placed immediately before the observed step it precedes, which is the position the compiler
    itself spliced it into (prereg §B3: no reordering).

    Contracts taken from the compiler and NOT re-implemented: an inferred action never becomes a
    state, and the Fase 2 redundancy rule suppresses an inference an observed action already
    explains.
    """
    from analysis.chain_compiler import compile_two_sided

    chains = compile_two_sided(events, side_of, actor_readable=actor_readable)
    merged: list[tuple[float, str, str]] = []
    n_inferred = 0
    for side in ("a", "b"):
        chain = chains.get(side)
        if chain is None:
            continue
        pos = -1.0
        if chain.states:
            merged.append((pos, chain.states[0].node_key, side))
        for edge in chain.edges:
            for k, act in enumerate(edge.actions):
                if act.source_event_index is not None:
                    pos = float(act.source_event_index)
                else:
                    n_inferred += 1
                    pos = pos + 1e-3 * (k + 1)  # keep the compiler's order, no reordering
                merged.append((pos, act.key, side))
            merged.append((pos + 1e-6, edge.target_key, side))
    merged.sort(key=lambda t: (t[0], t[2]))
    return [(k, sd) for _p, k, sd in merged if k], n_inferred


def canonicalized_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``events`` with each ``label`` replaced by its canonical English library label.

    **Why this exists, and what it is NOT.** ``chain_compiler.compile_chain`` keys its states from
    ``_normalize_name(label)`` with no ``clean_label`` in front, so running it on a pt-BR user log
    emits ``costas`` / ``montada`` / ``meia guarda`` — a node key space DISJOINT from the English
    one the corpus produces (measured 2026-09-12: 108 ``costas`` for the same entries the direct
    mapper keys as ``back control``). That is a defect in PRODUCTION code, not in this study, and
    it is reported as one rather than patched here: this helper only makes the §B inference arm
    scoreable by handing the compiler labels it can key consistently.
    """
    from analysis.technique_match import clean_label

    out = []
    for ev in events:
        d = dict(ev)
        d["label"] = clean_label(str(ev.get("label") or ""), str(ev.get("type") or ""))
        out.append(d)
    return out


def bout_node_steps(
    events: Sequence[Mapping[str, Any]],
    side_of: Any,
    *,
    infer: bool,
    actor_readable: bool = True,
) -> tuple[list[tuple[str, str]], int]:
    """One unit's ordered ``(node_key, side)`` stream, with or without inference."""
    if infer:
        return _compiled_steps(canonicalized_events(events), side_of, actor_readable=actor_readable)
    out = []
    for ev in events:
        side = side_of(ev)
        if side not in ("a", "b"):
            continue
        key = node_key_of(ev.get("label"), ev.get("type"))
        if key:
            out.append((key, side))
    return out, 0


def absorption_values(
    chains: Sequence[tuple[Sequence[tuple[str, str]], str | None]],
    *,
    alpha: float = 1.0,
) -> tuple[dict[str, float], dict[str, int]]:
    """``v(node)`` by absorption on the empirical two-sided transition matrix (prereg §B1).

    ``chains`` is ``(steps, finish_side)`` per bout; ``finish_side`` is ``'a'``/``'b'`` when the
    bout ended in that side's submission, ``None`` otherwise. Each bout is walked TWICE, once per
    reference athlete, so the lifted state space is exactly mirror-symmetric and
    ``v(node, own) = 1 − v(node, opp)`` holds by construction — asserted by the caller rather than
    assumed.

    ``v = B_own / (B_own + B_opp)``: the probability the reference side's finish comes first,
    CONDITIONAL on reaching a finish at all. Non-finish endings are a third absorbing state whose
    mass is divided out — the same conditional ``lamas_chain.rrb`` publishes.

    Laplace/Dirichlet ``alpha`` is spread over every destination INCLUDING the two finishes, so a
    node seen once shrinks toward 0.5 instead of inheriting a singleton's outcome.
    """
    import numpy as np

    counts: dict[tuple[str, bool], Counter[Any]] = defaultdict(Counter)
    seen: Counter[str] = Counter()
    for steps, finish in chains:
        for ref in ("a", "b"):
            lifted = [(k, sd == ref) for k, sd in steps]
            if not lifted:
                continue
            for k, _own in lifted:
                seen[k] += 1
            for i in range(len(lifted) - 1):
                counts[lifted[i]][lifted[i + 1]] += 1
            if finish == ref:
                end: Any = FIN_OWN
            elif finish in ("a", "b"):
                end = FIN_OPP
            else:
                end = END_OTHER
            counts[lifted[-1]][end] += 1
    # `seen` double-counts (two reference passes); report the real per-node occurrence.
    seen = Counter({k: v // 2 for k, v in seen.items()})

    states = sorted({s for s in counts} | {d for c in counts.values() for d in c if isinstance(d, tuple)})
    if not states:
        return {}, {}
    index = {s: i for i, s in enumerate(states)}
    n = len(states)
    q = np.zeros((n, n))
    r = np.zeros((n, 2))  # columns: FIN_OWN, FIN_OPP
    for s, row in counts.items():
        i = index[s]
        total = sum(row.values()) + alpha * (n + 3)
        for dest, c in row.items():
            if isinstance(dest, tuple):
                q[i, index[dest]] += c
            elif dest == FIN_OWN:
                r[i, 0] += c
            elif dest == FIN_OPP:
                r[i, 1] += c
        q[i, :] += alpha
        r[i, :] += alpha
        q[i, :] /= total
        r[i, :] /= total
    # Rows that were never a SOURCE still need the smoothing prior, or (I−Q) is singular-ish.
    for s in states:
        i = index[s]
        if s not in counts:
            total = alpha * (n + 3)
            q[i, :] = alpha / total
            r[i, :] = alpha / total
    b = np.linalg.solve(np.eye(n) - q, r)
    denom = b[:, 0] + b[:, 1]
    vals: dict[str, float] = {}
    for s, i in index.items():
        key, own = s
        if not own or denom[i] <= 0:
            continue
        vals[key] = float(b[i, 0] / denom[i])
    return vals, dict(seen)


def mirror_error(
    chains: Sequence[tuple[Sequence[tuple[str, str]], str | None]], *, alpha: float = 1.0
) -> float:
    """Max |v(node, own) + v(node, opp) − 1| — 0 by the two-reference construction (prereg §B1)."""
    import numpy as np

    flipped = [([(k, "b" if sd == "a" else "a") for k, sd in st],
                None if f is None else ("b" if f == "a" else "a")) for st, f in chains]
    a, _ = absorption_values(chains, alpha=alpha)
    b, _ = absorption_values(flipped, alpha=alpha)
    shared = sorted(set(a) & set(b))
    return float(np.max([abs(a[k] - b[k]) for k in shared])) if shared else 0.0


def frequency_values(seen: Mapping[str, int]) -> dict[str, float]:
    """Control 1 (prereg §B4): the node's FREQUENCY percentile in place of its value.

    Same vocabulary, same mapping, same coverage, no value. An arm that cannot beat this is a
    coverage result wearing a rating costume.
    """
    order = sorted(seen, key=lambda k: (seen[k], k))
    n = len(order)
    return {k: (i + 1) / (n + 1) for i, k in enumerate(order)} if n else {}


def corpus_chains(
    bouts: Sequence[Bout],
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    win_types: Mapping[str, str],
    *,
    infer: bool,
    years: Any = None,
) -> tuple[list[tuple[list[tuple[str, str]], str | None]], int]:
    """Corpus-only chains for the absorption estimate. PUBLIC data only, by construction.

    ``years`` restricts to ``year <= cutoff`` for the leakage-free fold estimate (prereg §B4.2).
    There is NO code path from here to ``user_sessions`` — the owner's rounds can only ever be
    SCORED against the result (prereg §B4.3).
    """
    out: list[tuple[list[tuple[str, str]], str | None]] = []
    n_inf = 0
    for b in bouts:
        if years is not None and b.year > years:
            continue
        seq = raw.get(b.bout_id) or []
        if not seq:
            continue

        def side_of(ev: Mapping[str, Any], _a: str = b.a) -> str | None:
            aid = str(ev.get("actor_id") or "")
            return "a" if aid == _a else ("b" if aid else None)

        steps, k = bout_node_steps(seq, side_of, infer=infer, actor_readable=True)
        n_inf += k
        finish = None
        if win_types.get(b.bout_id) == "SUBMISSION" and b.winner in (b.a, b.b):
            finish = "a" if b.winner == b.a else "b"
        if steps:
            out.append((steps, finish))
    return out, n_inf


def _owner_side_of(entry: Mapping[str, Any]) -> str:
    return "b" if entry.get("actor") == "partner" else "a"


def owner_node_units(
    doc: Mapping[str, Any], target: str, *, infer: bool
) -> tuple[list[tuple[list[tuple[str, str]], int, float, float]], int]:
    """``[(steps, label, difficulty, intensity)]`` for T1/T2 in the full-node space."""
    units = []
    n_inf = 0
    for s in doc.get("sessions") or []:
        for rd in s.get("rounds") or []:
            ents = list(rd.get("entries") or [])
            diff = float(rd.get("difficulty") or DIFFICULTY_BASELINE)
            inten = float(rd.get("intensity") or 0.0)
            if target == "T1":
                if str(rd.get("outcome") or "") not in ("succeeded", "failed"):
                    continue
                use, label = ents, 1 if rd["outcome"] == "succeeded" else 0
            else:
                if not ents:
                    continue
                last = ents[-1]
                if str(last.get("type") or "") != "submission" or last.get("successful") is False:
                    continue
                use, label = ents[:-1], 1 if last.get("actor") != "partner" else 0
            steps, k = bout_node_steps(use, _owner_side_of, infer=infer, actor_readable=True)
            n_inf += k
            units.append((steps, label, diff, inten))
    return units, n_inf


def _signed_own_share(
    steps: Sequence[tuple[str, str]], values: Mapping[str, float]
) -> tuple[float, int]:
    """``((n_own − n_other)/n, n)`` over the MAPPED nodes — the flat-value limit of
    ``fullchain_nodes``.

    When every ``|logit(v)|`` is roughly the same small constant *c* (measured: sd = 0.012 on this
    corpus), ``Σ ±logit(v_i)/n`` IS ``c·(n_own − n_other)/n`` up to that constant. This control
    isolates that term so the report can say whether the node VALUE adds anything over "what share
    of the round did I log as mine".
    """
    mapped = [sd for k, sd in steps if k in values]
    n = len(mapped)
    if n == 0:
        return float("nan"), 0
    own = sum(1 for sd in mapped if sd == "a")
    return (2 * own - n) / n, n


def _fullchain_z(
    steps: Sequence[tuple[str, str]],
    values: Mapping[str, float],
    *,
    arm: str,
    gamma: float = 1.0,
) -> tuple[float, int]:
    """``(Z, n_used)`` for one §B arm. Sign: ``side == 'a'`` is the reference (own)."""
    zs = [
        (_logit(values[k]) if sd == "a" else -_logit(values[k]))
        for k, sd in steps
        if k in values
    ]
    n = len(zs)
    if arm == "fullchain_nodes":
        return (sum(zs) / (n**gamma), n) if n else (float("nan"), 0)
    if arm == "fullchain_last":
        return (zs[-1], n) if n else (float("nan"), 0)
    if arm == "fullchain_edges":
        if n < 2:
            return float("nan"), 0
        return sum(zs[i + 1] - zs[i] for i in range(n - 1)) / ((n - 1) ** gamma), n
    raise ValueError(arm)


def run_section_b(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Prereg §B, end to end. Read-only; nothing private reaches the corpus chain."""
    from sqlalchemy import text

    from db.base import get_engine

    bouts = load_corpus_bouts()
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, win_type, sequence FROM matches WHERE status='final' AND sequence IS NOT NULL")
        ).mappings().all()
    raw = {str(r["id"]): [e for e in (r["sequence"] or []) if isinstance(e, dict)] for r in rows}
    win_types = {str(r["id"]): str(r["win_type"] or "") for r in rows}

    block = block_for_family("other", weights_doc) or {}
    lam_vals = action_values(block, terminal="marginal", marginal=marginal_submission_share(weights_doc))
    res: dict[str, Any] = {"alpha_grid": [0.5, 1.0], "variants": {}}

    for infer in (False, True):
        ch, corpus_inf = corpus_chains(bouts, raw, win_types, infer=infer)
        tag = "inferred" if infer else "observed"
        v_by_alpha = {}
        seen: dict[str, int] = {}
        for alpha in (0.5, 1.0):
            v, seen = absorption_values(ch, alpha=alpha)
            v_by_alpha[str(alpha)] = v
        freq = frequency_values(seen)
        v_ref = v_by_alpha["1.0"]
        spread = sorted(_logit(x) for x in v_ref.values())
        mid = len(spread) // 2
        var = {
            "corpus_bouts_in_chain": len(ch),
            "corpus_inferred_actions": corpus_inf,
            "vocabulary": len(v_ref),
            "vocabulary_ge5": sum(1 for k in v_ref if seen.get(k, 0) >= MIN_NODE_COUNT_REPORTED),
            "mirror_max_error": mirror_error(ch[:200], alpha=1.0),
            "v_sd_logit": (
                (sum((x - sum(spread) / len(spread)) ** 2 for x in spread) / len(spread)) ** 0.5
                if spread else float("nan")
            ),
            "v_median_logit": spread[mid] if spread else float("nan"),
            "v_alpha_max_abs_diff": max(
                (abs(v_by_alpha["0.5"][k] - v_by_alpha["1.0"][k]) for k in v_ref), default=float("nan")
            ),
            "v_examples": {
                k: {"v": round(v_ref.get(k, float("nan")), 4), "n": seen.get(k, 0)}
                for k in ("mount", "half guard", "turtle", "back control", "closed guard",
                          "guard pass", "sweep", "armbar", "heel hook", "triangle choke")
                if k in v_ref
            },
            "v_top": [
                {"node": k, "v": round(v_ref[k], 4), "n": seen[k]}
                for k in sorted(
                    (k for k in v_ref if seen.get(k, 0) >= MIN_NODE_COUNT_REPORTED),
                    key=lambda k: (-v_ref[k], k),
                )[:8]
            ],
            "v_bottom": [
                {"node": k, "v": round(v_ref[k], 4), "n": seen[k]}
                for k in sorted(
                    (k for k in v_ref if seen.get(k, 0) >= MIN_NODE_COUNT_REPORTED),
                    key=lambda k: (v_ref[k], k),
                )[:8]
            ],
            "targets": {},
        }

        for target in ("T1", "T2"):
            units, owner_inf = owner_node_units(doc, target, infer=infer)
            ys = [u[1] for u in units]
            prod = [-u[2] for u in units]
            n_entries = sum(len(u[0]) for u in units)
            tt: dict[str, Any] = {
                "n_units": len(units),
                "owner_inferred_actions": owner_inf,
                "base_rate": sum(ys) / len(ys) if ys else float("nan"),
                "entry_coverage": (
                    sum(1 for u in units for k, _ in u[0] if k in v_ref) / n_entries
                    if n_entries else 0.0
                ),
                "arms": {},
            }
            if units and 0 < sum(ys) < len(ys):
                sep = auc_ci(prod, [bool(y) for y in ys], n_boot=N_BOOT, seed=SEED)
                tt["arms"]["PROD_difficulty_oriented"] = {
                    "n": len(units), "coverage": 1.0, "auc": sep.auc,
                    "auc_lo": sep.lo, "auc_hi": sep.hi, "rho_len": float("nan"),
                }

            def add(name: str, scorer: Any, vals: Mapping[str, float]) -> None:
                rows_: list[tuple[int, float, int, float]] = []
                for i, (steps, y, _d, _in) in enumerate(units):
                    z, k = scorer(steps, vals)
                    if k == 0 or math.isnan(z):
                        continue
                    rows_.append((i, z, y, float(sum(1 for kk, _ in steps if kk in vals))))
                if not rows_ or not (0 < sum(r[2] for r in rows_) < len(rows_)):
                    tt["arms"][name] = {"estimable": False, "n": len(rows_)}
                    return
                sc = [r[1] for r in rows_]
                yy = [r[2] for r in rows_]
                sep = auc_ci(sc, [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
                d = paired_delta_ci(sc, [prod[r[0]] for r in rows_], yy, "auc")
                tt["arms"][name] = {
                    "n": len(rows_), "coverage": len(rows_) / len(units),
                    "auc": sep.auc, "auc_lo": sep.lo, "auc_hi": sep.hi,
                    "rho_len": spearman_rho([abs(x) for x in sc], [r[3] for r in rows_]),
                    "delta_auc_vs_production": d, "beats_production": bool(d["lo"] > 0),
                    "scores": sc, "idx": [r[0] for r in rows_],
                }

            for alpha in (0.5, 1.0):
                vv = v_by_alpha[str(alpha)]
                for gamma in (0.5, 1.0):
                    add(f"fullchain_nodes_a{alpha}_g{gamma}",
                        lambda st, v, g=gamma: _fullchain_z(st, v, arm="fullchain_nodes", gamma=g), vv)
                    add(f"fullchain_edges_a{alpha}_g{gamma}",
                        lambda st, v, g=gamma: _fullchain_z(st, v, arm="fullchain_edges", gamma=g), vv)
                add(f"fullchain_last_a{alpha}",
                    lambda st, v: _fullchain_z(st, v, arm="fullchain_last"), vv)
            add("CONTROL_fullchain_freq",
                lambda st, v: _fullchain_z(st, v, arm="fullchain_nodes", gamma=1.0), freq)
            # CONTROL 3 (added before scoring §B, after measuring that sd(logit v) = 0.012):
            # a FLAT value makes `fullchain_nodes` collapse algebraically to c·(n_own − n_other)/n
            # — the signed share of entries the athlete logged as their own. If that alone scores
            # what the arm scores, §B is an actor-balance result, not a value result.
            add("CONTROL_signed_own_share",
                lambda st, v: _signed_own_share(st, v), v_by_alpha["1.0"])

            # Combination with the Lamas actions arm, on the Lamas-mapped view of the SAME rounds.
            # `owner_units` and `owner_node_units` apply identical filters in identical order, so
            # position i is the same round in both — asserted, because a silent misalignment here
            # would pair one round's Lamas score with another's full-chain score.
            lam_units = owner_units(doc, target, library=True)
            assert len(lam_units) == len(units), "§A/§B unit alignment broke"
            lam_z = {}
            for i, u in enumerate(lam_units):
                z, k = granular_score(u.steps, lam_vals, granularity="actions", gamma=1.0)
                if k:
                    lam_z[i] = z
            base = tt["arms"].get("fullchain_nodes_a1.0_g1.0")
            if base and base.get("n"):
                pair = [(i, (z + lam_z[i]) / 2.0) for i, z in zip(base["idx"], base["scores"], strict=True)
                        if i in lam_z]
                if pair and 0 < sum(units[i][1] for i, _ in pair) < len(pair):
                    sc = [z for _, z in pair]
                    yy = [units[i][1] for i, _ in pair]
                    sep = auc_ci(sc, [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
                    d = paired_delta_ci(sc, [prod[i] for i, _ in pair], yy, "auc")
                    tt["arms"]["actions_fullchain"] = {
                        "n": len(pair), "coverage": len(pair) / len(units),
                        "auc": sep.auc, "auc_lo": sep.lo, "auc_hi": sep.hi,
                        "rho_len": float("nan"),
                        "delta_auc_vs_production": d, "beats_production": bool(d["lo"] > 0),
                        "idx": [i for i, _ in pair], "scores": sc,
                    }
            # Death rules 9 + the flat-value control: every arm's PAIRED Δ against each control,
            # on the units both scored. An arm that cannot clear these is measuring the
            # vocabulary or the actor balance, not the node value.
            for ctrl in ("CONTROL_fullchain_freq", "CONTROL_signed_own_share"):
                c = tt["arms"].get(ctrl) or {}
                if not c.get("n"):
                    continue
                cmap = dict(zip(c["idx"], c["scores"], strict=True))
                for name, a in tt["arms"].items():
                    if name.startswith(("CONTROL_", "PROD_")) or not a.get("n"):
                        continue
                    pair = [(i, z) for i, z in zip(a["idx"], a["scores"], strict=True) if i in cmap]
                    yy = [units[i][1] for i, _ in pair]
                    if not pair or not (0 < sum(yy) < len(yy)):
                        continue
                    a[f"delta_auc_vs_{ctrl}"] = paired_delta_ci(
                        [z for _, z in pair], [cmap[i] for i, _ in pair], yy, "auc"
                    )
            for a in tt["arms"].values():
                a.pop("scores", None)
                a.pop("idx", None)
            var["targets"][target] = tt
        res["variants"][tag] = var

    # T3 — leakage-free: v re-estimated on the TRAIN years of each fold (prereg §B4.2).
    res["T3"] = run_section_b_t3(bouts, raw, win_types)
    return res


def run_section_b_t3(
    bouts: Sequence[Bout],
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    win_types: Mapping[str, str],
    cutoffs: Sequence[int] = (2023, 2024, 2025),
) -> dict[str, Any]:
    """Rolling-origin forward prediction with the full-node value, estimated per fold on train only."""
    decided = [b for b in bouts if b.winner in (b.a, b.b)]
    node_steps = {b.bout_id: bout_node_steps(raw.get(b.bout_id) or [],
                                             lambda ev, _a=b.a: "a" if str(ev.get("actor_id") or "") == _a
                                             else ("b" if ev.get("actor_id") else None),
                                             infer=False)[0]
                  for b in decided}
    folds = []
    for cut in cutoffs:
        train = [b for b in decided if b.year <= cut]
        test = [b for b in decided if b.year == cut + 1]
        if not train or not test:
            folds.append({"cutoff": cut, "skipped": "no train or no test bouts"})
            continue
        ch, _ = corpus_chains(bouts, raw, win_types, infer=False, years=cut)
        vals, _seen = absorption_values(ch, alpha=1.0)

        st: dict[str, RatingState] = {}

        def get(x: str) -> RatingState:
            return st.setdefault(x, RatingState(GLOBAL_SEED, 350.0, VOLATILITY_SEED))

        def update(b: Bout, arm: str) -> None:
            sa, sb = get(b.a), get(b.b)
            win_a = 1.0 if b.winner == b.a else 0.0
            z, k = _fullchain_z(node_steps.get(b.bout_id) or [], vals, arm="fullchain_nodes", gamma=1.0)
            if arm == "A0g_winner":
                score = win_a
            elif k == 0:
                return
            else:
                score = p_own(z)
            st[b.a] = update_period(sa, [Observation(sb.rating, sb.deviation, score, 1.0)],
                                    tau=0.5, center=GLOBAL_SEED)
            st[b.b] = update_period(sb, [Observation(sa.rating, sa.deviation, 1.0 - score, 1.0)],
                                    tau=0.5, center=GLOBAL_SEED)

        row: dict[str, Any] = {"cutoff": cut, "test_year": cut + 1, "train_bouts": len(train),
                               "test_bouts": len(test), "vocabulary": len(vals), "arms": {}}
        got = {}
        for arm in ("A0g_winner", "B_fullchain_real_opponent"):
            st = {}
            for b in train:
                update(b, arm)
            ps, ys = [], []
            for b in test:
                sa, sb = get(b.a), get(b.b)
                ps.append(expected_score(sa.rating, sa.deviation, sb.rating, sb.deviation))
                ys.append(1 if b.winner == b.a else 0)
                update(b, arm)
            got[arm] = (ps, ys)
            row["arms"][arm] = {"logloss": log_loss(ps, ys), "brier": brier(ps, ys),
                                "auc": _auc_point(ps, ys)}
        ys = got["A0g_winner"][1]
        row["arms"]["B_fullchain_real_opponent"]["delta_logloss_vs_A0g"] = paired_delta_ci(
            got["B_fullchain_real_opponent"][0], got["A0g_winner"][0], ys, "logloss"
        )
        row["null_coinflip_logloss"] = math.log(2)
        folds.append(row)
    return {"folds": folds}


# ══ ADDENDUM §C — personalized hierarchical layers + Elo coherence ═════════════
# Prereg §C. Re-implementation from a DESCRIBED model: the owner's notebook artefacts were not
# available to us, only its reported numbers.

WARMUP_ROUNDS = 10


@dataclass(frozen=True)
class OwnerRound:
    """One round in the owner's chronological order, with everything §C needs."""

    order: tuple[str, int]
    outcome: str
    difficulty: float
    intensity: float
    steps: tuple[tuple[str, str], ...]          # (node_key, side) — full-node space, no inference
    prefix: tuple[tuple[str, str], ...]         # steps minus a terminal landed submission
    finish_side: str | None                     # 'a' (own) / 'b' (partner) / None
    # The Lamas code per entry, kept alongside the node key: `lamas_state` needs the EVENT TYPE
    # and a node_key has thrown it away, so the states_only coherence arm cannot be rebuilt from
    # `steps`. Carried at construction instead of re-derived.
    lamas: tuple[tuple[str | None, bool], ...] = ()
    # (node_key, attempt-reading Lamas code, is_own), one row per entry that has a node key —
    # ALIGNED by construction. `steps` and `lamas` are filtered independently and must never be
    # zipped together; this is the tuple §C6 reads.
    combo: tuple[tuple[str, str | None, bool], ...] = ()


def owner_rounds_c(doc: Mapping[str, Any]) -> list[OwnerRound]:
    """The owner's rounds in strict chronological order — the unit §C walks forward over."""
    out: list[OwnerRound] = []
    for s in doc.get("sessions") or []:
        for i, rd in enumerate(s.get("rounds") or []):
            ents = list(rd.get("entries") or [])
            steps, _ = bout_node_steps(ents, _owner_side_of, infer=False)
            last = ents[-1] if ents else None
            finished = bool(
                last is not None
                and str(last.get("type") or "") == "submission"
                and last.get("successful") is not False
            )
            prefix, _ = bout_node_steps(ents[:-1] if finished else ents, _owner_side_of, infer=False)
            out.append(
                OwnerRound(
                    order=(str(s.get("created_at")), i),
                    outcome=str(rd.get("outcome") or ""),
                    difficulty=float(rd.get("difficulty") or DIFFICULTY_BASELINE),
                    intensity=float(rd.get("intensity") or 0.0),
                    steps=tuple(steps),
                    prefix=tuple(prefix),
                    finish_side=(_owner_side_of(last) if finished and last else None),
                    lamas=tuple(
                        (canonical_code(e, library=True), e.get("actor") != "partner") for e in ents
                    ),
                    combo=tuple(
                        (
                            node_key_of(e.get("label"), e.get("type")),
                            lamas_state({"type": e.get("type"),
                                         "label": _clean(e.get("label"), e.get("type")),
                                         "successful": False}),
                            e.get("actor") != "partner",
                        )
                        for e in ents
                        if node_key_of(e.get("label"), e.get("type"))
                    ),
                )
            )
    out.sort(key=lambda r: r.order)
    return out


@dataclass
class HierState:
    """The walk-forward personal layers. Mutable on purpose — one pass, updated after each round."""

    label_s: dict[str, float]
    label_n: dict[str, float]
    edge_s: dict[tuple[str, str], float]
    edge_n: dict[tuple[str, str], float]

    @classmethod
    def empty(cls) -> HierState:
        return cls({}, {}, {}, {})


def _p_label(key: str, st: HierState, prior: Mapping[str, float], alpha: float) -> float:
    v0 = prior.get(key, 0.5)
    n = st.label_n.get(key, 0.0)
    s = st.label_s.get(key, 0.0)
    return (s + alpha * v0) / (n + alpha)


def hier_score(
    steps: Sequence[tuple[str, str]],
    st: HierState,
    prior: Mapping[str, float],
    *,
    alpha: float,
    lam: float,
    gate: int,
    use_edges: bool = True,
) -> tuple[float, int, int]:
    """``(Z, n_labels, n_gated_edges)`` — prereg §C1.

    Label layer is the backbone (actor-signed mean log-odds of the personal, prior-shrunk label
    value); the edge layer contributes only a RESIDUAL, and only on edges with ``>= gate``
    observations. ``use_edges=False`` is the labels-only ablation.
    """
    zs = [
        (_logit(_p_label(k, st, prior, alpha)) if sd == "a" else -_logit(_p_label(k, st, prior, alpha)))
        for k, sd in steps
    ]
    n = len(zs)
    if n == 0:
        return float("nan"), 0, 0
    z = sum(zs) / n
    n_edges = 0
    if use_edges and n >= 2:
        residuals = []
        for i in range(len(steps) - 1):
            (k1, s1), (k2, s2) = steps[i], steps[i + 1]
            if s1 != s2:
                continue  # an edge is one side's own consecutive pair (the within-actor rule)
            e = (k1, k2)
            ne = st.edge_n.get(e, 0.0)
            if ne < gate:
                continue
            p_hat = sigmoid(
                (_logit(_p_label(k1, st, prior, alpha)) + _logit(_p_label(k2, st, prior, alpha))) / 2
            )
            p_e = (st.edge_s.get(e, 0.0) + alpha * p_hat) / (ne + alpha)
            r = _logit(p_e) - _logit(p_hat)
            residuals.append(r if s1 == "a" else -r)
        if residuals:
            n_edges = len(residuals)
            z += lam * (sum(residuals) / len(residuals))
    return z, n, n_edges


def hier_observe(rd: OwnerRound, st: HierState, *, remap: Mapping[str, str] | None = None) -> None:
    """Fold ONE finished round into the personal layers. Called only AFTER that round is scored."""
    if rd.finish_side not in ("a", "b"):
        return
    y_own = 1.0 if rd.finish_side == "a" else 0.0
    key_of = (lambda k: remap.get(k, k)) if remap else (lambda k: k)
    for k, sd in rd.prefix:
        kk = key_of(k)
        st.label_n[kk] = st.label_n.get(kk, 0.0) + 1.0
        st.label_s[kk] = st.label_s.get(kk, 0.0) + (y_own if sd == "a" else 1.0 - y_own)
    for i in range(len(rd.prefix) - 1):
        (k1, s1), (k2, s2) = rd.prefix[i], rd.prefix[i + 1]
        if s1 != s2:
            continue
        e = (key_of(k1), key_of(k2))
        st.edge_n[e] = st.edge_n.get(e, 0.0) + 1.0
        st.edge_s[e] = st.edge_s.get(e, 0.0) + (y_own if s1 == "a" else 1.0 - y_own)


def walk_forward_scores(
    rounds: Sequence[OwnerRound],
    prior: Mapping[str, float],
    *,
    alpha: float,
    lam: float,
    gate: int,
    use_edges: bool,
    remap: Mapping[str, str] | None = None,
    snapshots: dict[int, HierState] | None = None,
) -> list[tuple[int, float, int, int]]:
    """``[(round_index, Z, n_labels, n_edges)]`` — STRICT walk-forward (prereg §C1).

    The state folded in before scoring round *t* contains only rounds strictly earlier in time, so
    a round can never see itself and the layers are re-fitted at every step.
    """
    st = HierState.empty()
    out = []
    for i, rd in enumerate(rounds):
        z, n, ne = hier_score(rd.prefix, st, prior, alpha=alpha, lam=lam, gate=gate, use_edges=use_edges)
        out.append((i, z, n, ne))
        if snapshots is not None:
            snapshots[i] = HierState(dict(st.label_s), dict(st.label_n),
                                     dict(st.edge_s), dict(st.edge_n))
        hier_observe(rd, st, remap=remap)
    return out


def cluster_bootstrap_delta(
    a: Sequence[float],
    b: Sequence[float],
    ys: Sequence[int],
    clusters: Sequence[Any],
    stat: str,
    *,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> dict[str, float]:
    """Paired bootstrap resampling the SESSION, not the round (prereg §C1).

    Rounds inside one session share a partner, a day and a mood; treating them as independent
    would narrow every interval in this section by pretending 140 rounds are 140 experiments.
    """
    fn = {"auc": _auc_point, "logloss": log_loss, "brier": brier}[stat]
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, c in enumerate(clusters):
        groups[c].append(i)
    keys = sorted(groups, key=str)
    obs = fn(a, ys) - fn(b, ys)
    if not keys:
        return {"delta": obs, "lo": float("nan"), "hi": float("nan")}
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        idx: list[int] = []
        for _ in keys:
            idx.extend(groups[keys[rng.randrange(len(keys))]])
        yy = [ys[i] for i in idx]
        if stat == "auc" and (all(yy) or not any(yy)):
            continue
        d = fn([a[i] for i in idx], yy) - fn([b[i] for i in idx], yy)
        if not math.isnan(d):
            draws.append(d)
    draws.sort()
    if not draws:
        return {"delta": obs, "lo": float("nan"), "hi": float("nan")}
    return {"delta": obs, "lo": draws[int(0.025 * len(draws))],
            "hi": draws[min(len(draws) - 1, int(0.975 * len(draws)))]}


def ece(ps: Sequence[float], ys: Sequence[int], bins: int = 5) -> tuple[float, list[dict[str, float]]]:
    """Expected calibration error over EQUAL-COUNT bins, plus the reliability rows.

    Equal-count rather than equal-width: with n = 42 an equal-width binning puts most rounds in one
    bucket and reports a calibration number computed from three points.
    """
    n = len(ps)
    if n == 0:
        return float("nan"), []
    order = sorted(range(n), key=lambda i: (ps[i], i))
    rows, err = [], 0.0
    for b in range(bins):
        lo, hi = b * n // bins, (b + 1) * n // bins
        chunk = order[lo:hi]
        if not chunk:
            continue
        conf = sum(ps[i] for i in chunk) / len(chunk)
        acc = sum(ys[i] for i in chunk) / len(chunk)
        rows.append({"n": len(chunk), "confidence": conf, "accuracy": acc})
        err += len(chunk) / n * abs(conf - acc)
    return err, rows


def run_section_c(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Prereg §C, end to end. Read-only; nothing leaves ``out/``."""
    sec_b = json.loads((OUT / "section_b.json").read_text(encoding="utf-8"))
    prior: dict[str, float] = {}
    # v_prior is the §B corpus absorption value — PUBLIC → private, the permitted direction.
    ch_cached = sec_b["variants"]["observed"]
    _ = ch_cached
    from sqlalchemy import text

    from db.base import get_engine

    bouts = load_corpus_bouts()
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, win_type, sequence FROM matches WHERE status='final' AND sequence IS NOT NULL")
        ).mappings().all()
    raw = {str(r["id"]): [e for e in (r["sequence"] or []) if isinstance(e, dict)] for r in rows}
    wts = {str(r["id"]): str(r["win_type"] or "") for r in rows}
    chains, _ = corpus_chains(bouts, raw, wts, infer=False)
    prior, _seen = absorption_values(chains, alpha=1.0)

    rounds = owner_rounds_c(doc)
    sessions = [r.order[0] for r in rounds]
    t2 = [i for i, r in enumerate(rounds) if r.finish_side in ("a", "b")]
    ys_t2 = [1 if rounds[i].finish_side == "a" else 0 for i in t2]
    t1 = [i for i, r in enumerate(rounds) if r.outcome in ("succeeded", "failed")]
    ys_t1 = [1 if rounds[i].outcome == "succeeded" else 0 for i in t1]

    # The warm-up: T2 rounds after the first WARMUP_ROUNDS rounds WITH a finish.
    warm = set(t2[WARMUP_ROUNDS:])
    res: dict[str, Any] = {
        "note": "re-implementation from a described model; the notebook artefacts were not available",
        "n_rounds": len(rounds),
        "n_t2": len(t2),
        "n_t1": len(t1),
        "n_sessions": len(set(sessions)),
        "warmup_rounds": WARMUP_ROUNDS,
        "n_t2_after_warmup": len(warm),
        "prior_vocabulary": len(prior),
        "grid": {},
    }

    remap = None
    keys = sorted({k for r in rounds for k, _ in r.steps})
    rng = random.Random(SEED)
    shuffled = keys[:]
    rng.shuffle(shuffled)
    remap = dict(zip(keys, shuffled, strict=True))

    def evaluate(sel: Sequence[int], ys: Sequence[int], scored: Mapping[int, float],
                 ref: Mapping[int, float] | None, tag: str) -> dict[str, Any]:
        idx = [i for i in sel if i in scored and not math.isnan(scored[i])]
        if not idx or not (0 < sum(ys[sel.index(i)] for i in idx) < len(idx)):
            return {"estimable": False, "n": len(idx)}
        yy = [ys[sel.index(i)] for i in idx]
        ps = [p_own(scored[i]) for i in idx]
        sep = auc_ci([scored[i] for i in idx], [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
        e, rel = ece(ps, yy)
        row: dict[str, Any] = {
            "n": len(idx), "coverage": len(idx) / len(sel),
            "auc": sep.auc, "auc_lo": sep.lo, "auc_hi": sep.hi,
            "brier": brier(ps, yy), "logloss": log_loss(ps, yy), "ece": e, "reliability": rel,
        }
        if ref is not None:
            pair = [i for i in idx if i in ref and not math.isnan(ref[i])]
            if pair:
                yp = [ys[sel.index(i)] for i in pair]
                cl = [sessions[i] for i in pair]
                row[f"delta_brier_vs_{tag}"] = cluster_bootstrap_delta(
                    [p_own(scored[i]) for i in pair], [p_own(ref[i]) for i in pair], yp, cl, "brier")
                row[f"delta_auc_vs_{tag}"] = cluster_bootstrap_delta(
                    [scored[i] for i in pair], [ref[i] for i in pair], yp, cl, "auc")
        return row

    # Labels-only backbone at the notebook's alpha, used as the reference for every edge cell.
    base_by_alpha: dict[float, dict[int, float]] = {}
    for alpha in (0.5, 1.0, 2.0):
        base_by_alpha[alpha] = {
            i: z for i, z, n, _ in walk_forward_scores(
                rounds, prior, alpha=alpha, lam=0.0, gate=10**9, use_edges=False) if n
        }

    grid: dict[str, Any] = {}
    for alpha in (0.5, 1.0, 2.0):
        base = base_by_alpha[alpha]
        grid[f"labels_only_a{alpha}"] = evaluate(t2, ys_t2, base, None, "")
        for gate in (1, 2, 3, 5, 10):
            for lam in (1.0, 1.5, 2.0):
                sc = {i: z for i, z, n, _ in walk_forward_scores(
                    rounds, prior, alpha=alpha, lam=lam, gate=gate, use_edges=True) if n}
                name = f"hier_a{alpha}_gate{gate}_l{lam}"
                grid[name] = evaluate(t2, ys_t2, sc, base, f"labels_only_a{alpha}")
                grid[name]["gated_edge_rounds"] = sum(
                    1 for _i, _z, n, ne in walk_forward_scores(
                        rounds, prior, alpha=alpha, lam=lam, gate=gate, use_edges=True) if n and ne
                )
    res["grid"] = grid

    # The notebook's cell, plus the capacity null and the warm-up split.
    cell = {"alpha": 1.0, "gate": 3, "lam": 1.5}
    snaps: dict[int, HierState] = {}
    real = {i: z for i, z, n, _ in walk_forward_scores(
        rounds, prior, alpha=1.0, lam=1.5, gate=3, use_edges=True, snapshots=snaps) if n}
    shuf = {i: z for i, z, n, _ in walk_forward_scores(
        rounds, prior, alpha=1.0, lam=1.5, gate=3, use_edges=True, remap=remap) if n}
    base1 = base_by_alpha[1.0]
    res["notebook_cell"] = {
        "params": cell,
        "hier": evaluate(t2, ys_t2, real, base1, "labels_only"),
        "hier_shuffled_null": evaluate(t2, ys_t2, shuf, base1, "labels_only"),
        "labels_only": evaluate(t2, ys_t2, base1, None, ""),
        "hier_vs_shuffled": None,
        "after_warmup": evaluate(sorted(warm), [1 if rounds[i].finish_side == "a" else 0
                                                for i in sorted(warm)], real, base1, "labels_only"),
        "t1": evaluate(t1, ys_t1, real, base1, "labels_only"),
    }
    idx = [i for i in t2 if i in real and i in shuf]
    if idx:
        yy = [1 if rounds[i].finish_side == "a" else 0 for i in idx]
        res["notebook_cell"]["hier_vs_shuffled"] = {
            "brier": cluster_bootstrap_delta([p_own(real[i]) for i in idx],
                                             [p_own(shuf[i]) for i in idx], yy,
                                             [sessions[i] for i in idx], "brier"),
            "auc": cluster_bootstrap_delta([real[i] for i in idx], [shuf[i] for i in idx], yy,
                                           [sessions[i] for i in idx], "auc"),
        }

    res["corpus"] = run_section_c_corpus(prior, bouts, raw, wts)
    res["coherence"] = run_coherence(rounds, sessions, prior, real, weights_doc, snaps)
    return res


def run_coherence(
    rounds: Sequence[OwnerRound],
    sessions: Sequence[str],
    prior: Mapping[str, float],
    hier: Mapping[int, float],
    weights_doc: Mapping[str, Any],
    snapshots: Mapping[int, HierState] | None = None,
) -> dict[str, Any]:
    """Prereg §C3 — descriptive only, NO verdict. Five readings of the inferred partner Elo."""
    snapshots = snapshots or {}
    block = block_for_family("other", weights_doc) or {}
    lam_vals = action_values(block, terminal="marginal",
                             marginal=marginal_submission_share(weights_doc))

    def lamas_states_occ(rd: OwnerRound, cut: int | None = None) -> float:
        steps = rd.lamas if cut is None else rd.lamas[:cut] + rd.lamas[cut + 1:]
        z, n = granular_score(steps, lam_vals, granularity="states_occ")
        return z if n else float("nan")

    def labels_only(rd: OwnerRound) -> float:
        z, n = _fullchain_z(rd.steps, prior, arm="fullchain_nodes", gamma=1.0)
        return z if n else float("nan")

    arms: dict[str, list[float]] = {
        "difficulty": [-(rd.difficulty - DIFFICULTY_BASELINE)
                       * ELO_PER_DIFFICULTY_POINT / 400.0 * math.log(10) for rd in rounds],
        "intensity": [-(rd.intensity - 5.0) * ELO_PER_DIFFICULTY_POINT / 400.0 * math.log(10)
                      for rd in rounds],
        "states_only": [lamas_states_occ(rd) for rd in rounds],
        "labels_only": [labels_only(rd) for rd in rounds],
        "hier_residual": [hier.get(i, float("nan")) for i in range(len(rounds))],
    }

    out: dict[str, Any] = {}
    order_of = {"failed": 0, "partial": 1, "succeeded": 2}
    for name, zs in arms.items():
        ps = [p_own(z) if not math.isnan(z) else float("nan") for z in zs]
        offs = [elo_offset(p) if not math.isnan(p) else float("nan") for p in ps]

        # (a) within-session dispersion + lag-1 autocorrelation
        by_sess: dict[str, list[float]] = defaultdict(list)
        for i, o in enumerate(offs):
            if not math.isnan(o):
                by_sess[sessions[i]].append(o)
        sds, acs = [], []
        for _k, v in sorted(by_sess.items()):
            if len(v) < 3:
                continue
            m = sum(v) / len(v)
            sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
            sds.append(sd)
            if sd > 1e-9:
                num = sum((v[i] - m) * (v[i + 1] - m) for i in range(len(v) - 1))
                acs.append(num / (len(v) * sd * sd))

        # (b) leave-one-entry-out sensitivity — each arm re-scored through its OWN scorer, on the
        # state it actually had at that round (the hier arm re-uses its walk-forward snapshot, so
        # this is the real layer, not the label layer standing in for it).
        loo: list[float] = []
        if name in ("states_only", "labels_only", "hier_residual"):
            for i, rd in enumerate(rounds):
                if math.isnan(zs[i]):
                    continue
                base_off = elo_offset(p_own(zs[i]))
                span = len(rd.lamas) if name == "states_only" else len(rd.steps)
                for j in range(span):
                    if name == "states_only":
                        z2 = lamas_states_occ(rd, cut=j)
                        n2 = 0 if math.isnan(z2) else 1
                    elif name == "labels_only":
                        z2, n2 = _fullchain_z(rd.steps[:j] + rd.steps[j + 1:], prior,
                                              arm="fullchain_nodes", gamma=1.0)
                    else:
                        snap = snapshots.get(i)
                        if snap is None:
                            continue
                        z2, n2, _ = hier_score(rd.prefix[:j] + rd.prefix[j + 1:], snap, prior,
                                               alpha=1.0, lam=1.5, gate=3)
                    if n2:
                        loo.append(abs(base_off - elo_offset(p_own(z2))))

        # (c) Kendall tau against the recorded assessment ordering
        pairs = [(offs[i], order_of[rounds[i].outcome])
                 for i in range(len(rounds))
                 if rounds[i].outcome in order_of and not math.isnan(offs[i])]
        conc = disc = 0
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                dx = pairs[i][0] - pairs[j][0]
                dy = pairs[i][1] - pairs[j][1]
                if dx * dy > 0:
                    conc += 1
                elif dx * dy < 0:
                    disc += 1
        tau = (conc - disc) / (conc + disc) if (conc + disc) else float("nan")

        # (d) K budget under the budget-preserving lambda
        pv = [p for p in ps if not math.isnan(p)]
        blam = budget_lambda(pv, shape="linear") if pv else float("nan")

        # (e) calibration against finish-side
        t2 = [i for i, rd in enumerate(rounds)
              if rd.finish_side in ("a", "b") and not math.isnan(ps[i])]
        e, rel = ece([ps[i] for i in t2],
                     [1 if rounds[i].finish_side == "a" else 0 for i in t2]) if t2 else (float("nan"), [])

        out[name] = {
            "n_scored": sum(1 for z in zs if not math.isnan(z)),
            "within_session_sd_mean": sum(sds) / len(sds) if sds else float("nan"),
            "within_session_lag1_autocorr": sum(acs) / len(acs) if acs else float("nan"),
            "sessions_used": len(sds),
            "loo_mean_abs_elo_shift": sum(loo) / len(loo) if loo else float("nan"),
            "loo_max_abs_elo_shift": max(loo) if loo else float("nan"),
            "kendall_tau_vs_outcome": tau,
            "budget_lambda_linear": blam,
            "mean_competitiveness": sum(competitiveness(p) for p in pv) / len(pv) if pv else float("nan"),
            "ece_vs_finish_side": e,
            "reliability": rel,
        }
    return out


def run_section_c_corpus(
    prior: Mapping[str, float],
    bouts: Sequence[Bout],
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    win_types: Mapping[str, str],
) -> dict[str, Any]:
    """§C on the PUBLIC corpus — the same hierarchy, one level up (owner clarification 2026-09-12).

    The notebook's construction, transposed: global prior → **per-athlete** label values learned
    walk-forward over that athlete's own bout history → gated edge residual. The unit that plays
    the owner's role is the ATHLETE, so the bootstrap resamples athletes.

    Why this is the decisive test of the notebook's claim: the owner has 140 rounds of personal
    history, a corpus athlete has a handful of bouts. If the personalization only works where the
    history is long, that is a property of the method that 140 rounds cannot reveal and this can.
    """
    decided = [b for b in bouts if b.winner in (b.a, b.b)]
    steps_of = {
        b.bout_id: bout_node_steps(
            raw.get(b.bout_id) or [],
            lambda ev, _a=b.a: "a" if str(ev.get("actor_id") or "") == _a
            else ("b" if ev.get("actor_id") else None),
            infer=False,
        )[0]
        for b in decided
    }

    # T2 unit: a SUBMISSION bout, prefix only, label = athlete_a landed it.
    units = []
    for b in sorted(decided, key=lambda x: (x.year, x.created_at, x.bout_id)):
        if win_types.get(b.bout_id) != "SUBMISSION":
            continue
        st = steps_of.get(b.bout_id) or []
        cut = [i for i, (k, _sd) in enumerate(st) if k in ("finish", "submission", "tap")]
        prefix = st[: cut[-1]] if cut else st[:-1]
        if not prefix:
            continue
        units.append((b, tuple(prefix), 1 if b.winner == b.a else 0))

    def as_round(b: Bout, prefix: Sequence[tuple[str, str]], y: int) -> OwnerRound:
        return OwnerRound(order=(b.created_at, 0), outcome="", difficulty=DIFFICULTY_BASELINE,
                          intensity=0.0, steps=tuple(prefix), prefix=tuple(prefix),
                          finish_side="a" if y else "b")

    def walk(alpha: float, lam: float, gate: int, use_edges: bool) -> tuple[list[float], list[int], list[str], int]:
        """Per-athlete walk-forward: athlete a's layers see only a's EARLIER bouts."""
        states: dict[str, HierState] = defaultdict(HierState.empty)
        zs: list[float] = []
        ys: list[int] = []
        cl: list[str] = []
        warm = 0
        for b, prefix, y in units:
            st = states[b.a]
            if st.label_n:
                warm += 1
            z, n, _ne = hier_score(prefix, st, prior, alpha=alpha, lam=lam, gate=gate,
                                   use_edges=use_edges)
            if n:
                zs.append(z)
                ys.append(y)
                cl.append(b.a)
            hier_observe(as_round(b, prefix, y), states[b.a])
            # b's own layers see the mirrored bout, so both athletes accumulate history.
            mirrored = tuple((k, "b" if sd == "a" else "a") for k, sd in prefix)
            hier_observe(as_round(b, mirrored, 1 - y), states[b.b])
        return zs, ys, cl, warm

    def row(zs: Sequence[float], ys: Sequence[int], cl: Sequence[str],
            ref: Sequence[float] | None) -> dict[str, Any]:
        if not zs or not (0 < sum(ys) < len(ys)):
            return {"estimable": False, "n": len(zs)}
        ps = [p_own(z) for z in zs]
        sep = auc_ci(zs, [bool(y) for y in ys], n_boot=N_BOOT, seed=SEED)
        e, rel = ece(ps, ys)
        r: dict[str, Any] = {"n": len(zs), "auc": sep.auc, "auc_lo": sep.lo, "auc_hi": sep.hi,
                             "brier": brier(ps, ys), "logloss": log_loss(ps, ys), "ece": e,
                             "reliability": rel}
        if ref is not None and len(ref) == len(zs):
            r["delta_brier_vs_labels_only"] = cluster_bootstrap_delta(
                ps, [p_own(z) for z in ref], ys, cl, "brier")
            r["delta_auc_vs_labels_only"] = cluster_bootstrap_delta(zs, ref, ys, cl, "auc")
        return r

    out: dict[str, Any] = {"n_submission_units": len(units),
                           "n_athletes": len({b.a for b, _, _ in units}), "arms": {}}
    base_zs, base_ys, base_cl, warm = walk(1.0, 0.0, 10**9, False)
    out["units_with_prior_history"] = warm
    out["arms"]["personal_labels_only"] = row(base_zs, base_ys, base_cl, None)

    # The pure global-prior arm: no personal layer at all (§B's fullchain_nodes, same units).
    gz, gy = [], []
    for _b, prefix, y in units:
        z, n = _fullchain_z(prefix, prior, arm="fullchain_nodes", gamma=1.0)
        if n:
            gz.append(z)
            gy.append(y)
    out["arms"]["global_prior_only"] = row(gz, gy, [b.a for b, _, _ in units][: len(gz)], None)

    for gate in (1, 2, 3, 5, 10):
        zs, ys, cl, _ = walk(1.0, 1.5, gate, True)
        out["arms"][f"hier_gate{gate}_l1.5"] = row(zs, ys, cl, base_zs if len(zs) == len(base_zs) else None)
    return out


def _clean(label: Any, etype: Any) -> str:
    from analysis.technique_match import clean_label

    return clean_label(str(label or ""), str(etype or ""))


def hier4_prior(
    combo: Sequence[tuple[str, str | None, bool]],
    lamas_values: Mapping[str, float],
    absorption: Mapping[str, float],
) -> dict[str, float]:
    """Layer 1 (prereg §C6): node_key -> its GLOBAL prior value.

    The Lamas value of the key's ATTEMPT code first — the reading the published weights were
    actually derived under — then the §B corpus absorption value, then 0.5. Reading a state under
    a different attempt/success convention returns a number that was never measured for it.
    """
    out: dict[str, float] = {}
    for key, code, _own in combo:
        if key in out:
            continue
        if code is not None and code in lamas_values:
            out[key] = lamas_values[code]
        elif key in absorption:
            out[key] = absorption[key]
        else:
            out[key] = 0.5
    return out


def hier4_score(
    rd: OwnerRound,
    st: HierState,
    prior: Mapping[str, float],
    lam_vals: Mapping[str, float],
    *,
    alpha: float,
    lam: float,
    gate: int,
    w_ctx: float,
    personal: bool = True,
) -> tuple[float, int]:
    """The agreed four-layer score (prereg §C6). ``personal=False`` is the removed-layer ablation."""
    steps = [(k, "a" if own else "b") for k, _c, own in rd.combo]
    st_eff = st if personal else HierState.empty()
    z_labels, n, _ne = hier_score(steps, st_eff, prior, alpha=alpha, lam=0.0, gate=10**9,
                                  use_edges=False)
    if n == 0:
        return float("nan"), 0
    z_ctx, n_ctx = granular_score(rd.lamas, lam_vals, granularity="states_occ")
    if n_ctx == 0 or math.isnan(z_ctx):
        z_ctx, w = 0.0, 0.0
    else:
        w = w_ctx
    z = (1.0 - w) * z_labels + w * z_ctx
    residuals = []
    for i in range(len(steps) - 1):
        (k1, s1), (k2, s2) = steps[i], steps[i + 1]
        if s1 != s2:
            continue
        e = (k1, k2)
        if st_eff.edge_n.get(e, 0.0) < gate:
            continue
        p_hat = sigmoid((_logit(_p_label(k1, st_eff, prior, alpha))
                         + _logit(_p_label(k2, st_eff, prior, alpha))) / 2)
        p_e = (st_eff.edge_s.get(e, 0.0) + alpha * p_hat) / (st_eff.edge_n.get(e, 0.0) + alpha)
        r = _logit(p_e) - _logit(p_hat)
        residuals.append(r if s1 == "a" else -r)
    if residuals:
        z += lam * (sum(residuals) / len(residuals))
    return z, n


def hier4_walk(
    rounds: Sequence[OwnerRound],
    prior: Mapping[str, float],
    lam_vals: Mapping[str, float],
    *,
    alpha: float,
    lam: float,
    gate: int,
    w_ctx: float,
    personal: bool = True,
    remap: Mapping[str, str] | None = None,
) -> dict[int, float]:
    """Strict walk-forward over ``hier4_score``: round *t* sees only rounds strictly earlier."""
    st = HierState.empty()
    out: dict[int, float] = {}
    for i, rd in enumerate(rounds):
        z, n = hier4_score(rd, st, prior, lam_vals, alpha=alpha, lam=lam, gate=gate,
                           w_ctx=w_ctx, personal=personal)
        if n:
            out[i] = z
        hier_observe(rd, st, remap=remap)
    return out


def run_section_c6(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Prereg §C6 — the agreed four-layer arm, head to head against `actions_states` and V2."""
    sec_c = json.loads((OUT / "section_c.json").read_text(encoding="utf-8"))
    _ = sec_c
    block = block_for_family("other", weights_doc) or {}
    lam_vals = action_values(block, terminal="marginal",
                             marginal=marginal_submission_share(weights_doc))

    from sqlalchemy import text

    from db.base import get_engine

    bouts = load_corpus_bouts()
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, win_type, sequence FROM matches WHERE status='final' AND sequence IS NOT NULL")
        ).mappings().all()
    raw = {str(r["id"]): [e for e in (r["sequence"] or []) if isinstance(e, dict)] for r in rows}
    wts = {str(r["id"]): str(r["win_type"] or "") for r in rows}
    chains, _ = corpus_chains(bouts, raw, wts, infer=False)
    absorption, _ = absorption_values(chains, alpha=1.0)

    rounds = owner_rounds_c(doc)
    sessions = [r.order[0] for r in rounds]
    combo_all = [c for rd in rounds for c in rd.combo]
    prior = hier4_prior(combo_all, lam_vals, absorption)

    t2 = [i for i, r in enumerate(rounds) if r.finish_side in ("a", "b")]
    y2 = [1 if rounds[i].finish_side == "a" else 0 for i in t2]
    t1 = [i for i, r in enumerate(rounds) if r.outcome in ("succeeded", "failed")]
    y1 = [1 if rounds[i].outcome == "succeeded" else 0 for i in t1]

    # Reference: §A's `actions_states` on the same rounds, same index space.
    ref: dict[str, dict[int, float]] = {}
    for tag, sel in (("T1", t1), ("T2", t2)):
        r = {}
        for i in sel:
            steps = rounds[i].lamas if tag == "T1" else tuple(
                (c, own) for c, own in rounds[i].lamas
            )[: len(rounds[i].lamas) - (1 if rounds[i].finish_side else 0)]
            z, n = granular_score(steps, lam_vals, granularity="actions_states", gamma=1.0)
            if n:
                r[i] = z
        ref[tag] = r

    def score_row(sel: Sequence[int], ys: Sequence[int], sc: Mapping[int, float],
                  against: Mapping[str, Mapping[int, float]]) -> dict[str, Any]:
        idx = [i for i in sel if i in sc and not math.isnan(sc[i])]
        yy = [ys[sel.index(i)] for i in idx]
        if not idx or not (0 < sum(yy) < len(yy)):
            return {"estimable": False, "n": len(idx)}
        ps = [p_own(sc[i]) for i in idx]
        sep = auc_ci([sc[i] for i in idx], [bool(y) for y in yy], n_boot=N_BOOT, seed=SEED)
        e, _rel = ece(ps, yy)
        row: dict[str, Any] = {"n": len(idx), "coverage": len(idx) / len(sel), "auc": sep.auc,
                               "auc_lo": sep.lo, "auc_hi": sep.hi, "brier": brier(ps, yy),
                               "logloss": log_loss(ps, yy), "ece": e}
        for name, other in against.items():
            pair = [i for i in idx if i in other and not math.isnan(other[i])]
            if not pair:
                continue
            yp = [ys[sel.index(i)] for i in pair]
            if not (0 < sum(yp) < len(yp)):
                continue
            cl = [sessions[i] for i in pair]
            row[f"delta_auc_vs_{name}"] = cluster_bootstrap_delta(
                [sc[i] for i in pair], [other[i] for i in pair], yp, cl, "auc")
            row[f"delta_brier_vs_{name}"] = cluster_bootstrap_delta(
                [p_own(sc[i]) for i in pair], [p_own(other[i]) for i in pair], yp, cl, "brier")
        return row

    keys = sorted({k for rd in rounds for k, _c, _o in rd.combo})
    rng = random.Random(SEED)
    shuffled = keys[:]
    rng.shuffle(shuffled)
    remap = dict(zip(keys, shuffled, strict=True))

    grid: dict[str, Any] = {}
    best_cell = None
    for gate in (2, 3, 5):
        for alpha in (0.5, 1.0, 2.0):
            for lam in (1.0, 1.5):
                for w in (0.0, 0.5, 1.0):
                    sc = hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=lam, gate=gate, w_ctx=w)
                    name = f"hier4_g{gate}_a{alpha}_l{lam}_w{w}"
                    grid[name] = score_row(t2, y2, sc, {"actions_states": ref["T2"]})
                    b = grid[name].get("brier")
                    if b is not None and (best_cell is None or b < best_cell[1]):
                        best_cell = ((gate, alpha, lam, w), b)
    out: dict[str, Any] = {"n_rounds": len(rounds), "grid": grid,
                           "best_cell_by_brier": best_cell[0] if best_cell else None}

    gate, alpha, lam, w = best_cell[0] if best_cell else (3, 1.0, 1.5, 0.5)
    sc = hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=lam, gate=gate, w_ctx=w)
    abl = {
        "no_personal": hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=lam, gate=gate,
                                  w_ctx=w, personal=False),
        "no_context": hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=lam, gate=gate, w_ctx=0.0),
        "no_residual": hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=0.0, gate=10**9, w_ctx=w),
        "shuffled_null": hier4_walk(rounds, prior, lam_vals, alpha=alpha, lam=lam, gate=gate,
                                    w_ctx=w, remap=remap),
    }
    out["headline"] = {
        "cell": {"gate": gate, "alpha": alpha, "lam": lam, "w_ctx": w},
        "T2": score_row(t2, y2, sc, {"actions_states": ref["T2"], **abl}),
        "T1": score_row(t1, y1, sc, {"actions_states": ref["T1"], **abl}),
        "ablations_T2": {k: score_row(t2, y2, v, {"actions_states": ref["T2"]})
                         for k, v in abl.items()},
        "actions_states_T2": score_row(t2, y2, ref["T2"], {}),
        "actions_states_T1": score_row(t1, y1, ref["T1"], {}),
    }

    # Head-to-head 2: production V2's prequential Glicko-2 protocol, hier4's P as the offset.
    offs = {i: elo_offset(p_own(z)) for i, z in sc.items()}
    out["prequential"] = run_owner_prequential(
        doc, block, marginal_submission_share(weights_doc), library=True,
        granularity="actions", offsets=offs)
    return out


# ══ ADDENDUM §D — inferred per-action success ══════════════════════════════════
# Owner rule 2026-09-12: success is READ OFF THE SEQUENCE where the transition makes it
# observable, and is UNKNOWN otherwise. Never auto-success, never auto-failure (ADR-06 one level
# down, the same rule `node_rating.py` applies to a NULL flag).

SUCCESS_SOURCES = ("flag", "inferred", "unresolved")


def _state_role(label: str, etype: str) -> str:
    """The topological role of a STATE, from production's own curated table."""
    from analysis.attribution import classify

    return classify(etype or None, label or None).actor_role


def infer_entry_success(
    source: tuple[str, str] | None,
    action: tuple[str, str],
    target: tuple[str, str] | None,
    *,
    terminal: bool,
    table: Mapping[str, Any],
) -> tuple[float | None, str]:
    """``(score, success_source)`` for ONE observed action — prereg §D2.

    ``source``/``target`` are the ``(label, type)`` of the states the action sits between, or
    ``None`` when the chain has no state on that side. Reuses
    ``data/taxonomy/inference_table.json``'s ``action_exit_orientation`` and
    ``attribution.classify``'s curated role; invents no second table.

    Three resolutions and one refusal:

    * target role == the action's declared exit orientation  -> **1.0**, ``inferred``
      (Half Guard -> Sweep -> Top: the sweep landed where a sweep is supposed to land);
    * target state == source state                            -> **0.0**, ``inferred``
      (Back Control -> RNC attempt -> Back Control: nothing moved, the attempt did not finish);
    * a terminal submission closing the chain                 -> **1.0**, ``inferred``;
    * anything else                                           -> ``None``, ``unresolved``.

    A ``neutral`` exit orientation can only ever resolve by the return-to-source rule: "neutral"
    means the table makes NO claim about where the action lands, and reading no-claim as a success
    would manufacture the result this rule exists to refuse. Exit orientation may SUPPRESS a
    reading, never create one.
    """
    a_label, a_type = action
    orient = str(
        (table.get("action_exit_orientation") or {}).get(a_type)
        or (table.get("action_exit_orientation") or {}).get("*")
        or "neutral"
    )
    if terminal and a_type == "submission":
        return 1.0, "inferred"
    if target is None:
        return None, "unresolved"
    if source is not None and _normalize_key(source[0]) == _normalize_key(target[0]):
        return 0.0, "inferred"
    if orient == "neutral":
        return None, "unresolved"
    role = _state_role(target[0], target[1])
    if role == orient:
        return 1.0, "inferred"
    if role in ("top", "bottom") and role != orient:
        return 0.0, "inferred"
    return None, "unresolved"


def _normalize_key(label: str) -> str:
    from analysis.names import _normalize_name

    return _normalize_name(str(label or ""))


def entry_success_table(entries: Sequence[Mapping[str, Any]]) -> list[tuple[float | None, str]]:
    """``[(score, success_source)]``, one row per input entry, aligned by index.

    Walks the entries as a state/action alternation using production's own
    ``taxonomy_kind.kind_of_entry`` classifier, then applies :func:`infer_entry_success` to every
    ACTION using the states on either side of it. A STATE entry is not an action and carries
    ``('unresolved')`` — states are not scored anywhere in this study.
    """
    from analysis.taxonomy_kind import kind_of_entry, load_inference_table

    table = load_inference_table()
    kinds = []
    for e in entries:
        lab = _clean(e.get("label"), e.get("type"))
        kinds.append((kind_of_entry(lab, str(e.get("type") or "")), lab, str(e.get("type") or "")))

    out: list[tuple[float | None, str]] = []
    for i, (kind, lab, etype) in enumerate(kinds):
        if kind != "action":
            out.append((None, "unresolved"))
            continue
        src = next(((kinds[j][1], kinds[j][2]) for j in range(i - 1, -1, -1)
                    if kinds[j][0] == "state"), None)
        tgt_i = next((j for j in range(i + 1, len(kinds)) if kinds[j][0] == "state"), None)
        tgt = (kinds[tgt_i][1], kinds[tgt_i][2]) if tgt_i is not None else None
        terminal = tgt_i is None and i == len(kinds) - 1
        out.append(infer_entry_success((src[0], src[1]) if src else None, (lab, etype), tgt,
                                       terminal=terminal, table=table))
    return out


def entry_score(
    entry: Mapping[str, Any], inferred: tuple[float | None, str], mode: str,
    *, is_last: bool = False,
) -> tuple[float | None, str]:
    """``(score or None, source)`` under one of prereg §D2/§D5's scoring modes.

    ``is_last`` marks the LAST node of the sequence, which is the only entry the owner's refined
    rule keeps a manual flag on: the final action has no following transition, so the sequence
    cannot resolve it (the D7 anchor rule). Every internal action is inferred or NULL.
    """
    score, src = inferred
    if mode == "last_flag_only":
        if is_last:
            return (0.0 if entry.get("successful") is False else 1.0), "flag"
        return (score, src) if score is not None else (None, "unresolved")
    if mode == "flag":
        return (0.0 if entry.get("successful") is False else 1.0), "flag"
    if mode == "inferred_only":
        return (score, src) if score is not None else (None, "unresolved")
    if mode == "inferred_then_flag":
        if score is not None:
            return score, src
        return (0.0 if entry.get("successful") is False else 1.0), "flag"
    raise ValueError(mode)


def run_section_d(doc: Mapping[str, Any], weights_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Prereg §D: resolution counts + the three scoring modes in the production V2 slot."""
    from sqlalchemy import text

    from db.base import get_engine

    block = block_for_family("other", weights_doc) or {}
    marginal = marginal_submission_share(weights_doc)

    # Resolution counts, owner side.
    counts: Counter[str] = Counter()
    per_type: dict[str, Counter[str]] = defaultdict(Counter)
    n_entries = 0
    for sess in doc.get("sessions") or []:
        for rd in sess.get("rounds") or []:
            ents = rd.get("entries") or []
            own = [e for e in ents if e.get("actor") != "partner"]
            last_own = own[-1] if own else None
            for e, (sc, src) in zip(ents, entry_success_table(ents), strict=True):
                n_entries += 1
                counts[src if sc is not None else "unresolved"] += 1
                per_type[str(e.get("type") or "")][src if sc is not None else "unresolved"] += 1
                if e is last_own:
                    counts["last_own_node"] += 1
                    if sc is not None:
                        counts["last_own_node_also_inferable"] += 1

    # Resolution counts, corpus side.
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT id, sequence FROM matches WHERE status='final' AND sequence IS NOT NULL")
        ).mappings().all()
    ccounts: Counter[str] = Counter()
    c_entries = 0
    for r in rows:
        seq = [e for e in (r["sequence"] or []) if isinstance(e, dict)]
        if len(seq) < 4:
            continue
        for e, (sc, src) in zip(seq, entry_success_table(seq), strict=True):
            c_entries += 1
            ccounts[src if sc is not None else "unresolved"] += 1
            if e.get("successful") is not None:
                ccounts["has_flag"] += 1

    res: dict[str, Any] = {
        "owner": {
            "n_entries": n_entries,
            "resolved_by_inference": counts["inferred"],
            "unresolved": counts["unresolved"],
            "resolution_rate": counts["inferred"] / n_entries if n_entries else 0.0,
            "by_type": {k: dict(v) for k, v in sorted(per_type.items())},
            "last_own_nodes": counts["last_own_node"],
            "last_own_nodes_also_inferable": counts["last_own_node_also_inferable"],
        },
        "corpus": {
            "n_entries": c_entries,
            "resolved_by_inference": ccounts["inferred"],
            "unresolved": ccounts["unresolved"],
            "resolution_rate": ccounts["inferred"] / c_entries if c_entries else 0.0,
            "entries_with_a_flag": ccounts["has_flag"],
        },
        "modes": {},
    }

    for mode in ("flag", "inferred_only", "inferred_then_flag", "last_flag_only"):
        res["modes"][mode] = run_owner_prequential(
            doc, block, marginal, library=True, granularity="actions", score_mode=mode
        )
    # Paired deltas against today's behaviour, on the rounds all three modes scored.
    base = res["modes"]["flag"]
    for mode in ("inferred_only", "inferred_then_flag", "last_flag_only"):
        m = res["modes"][mode]
        if m.get("_ps") and base.get("_ps") and len(m["_ps"]) == len(base["_ps"]):
            res["modes"][mode]["delta_logloss_vs_flag"] = paired_delta_ci(
                m["_ps"], base["_ps"], base["_ys"], "logloss")
            res["modes"][mode]["delta_auc_vs_flag"] = paired_delta_ci(
                m["_ps"], base["_ps"], base["_ys"], "auc")
    for m in res["modes"].values():
        m.pop("_ps", None)
        m.pop("_ys", None)
    return res


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
