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
from collections import Counter
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
    state = {a: RatingState(GLOBAL_SEED, GLOBAL_RD_SEED, VOLATILITY_SEED) for a in arms}
    ps: dict[str, list[float]] = {a: [] for a in arms}
    ys: list[int] = []
    skipped_no_rrb = 0

    for _s, _i, rd in raw:
        outcome = str(rd.get("outcome") or "")
        ents = rd.get("entries") or []
        own = [e for e in ents if e.get("actor") != "partner"]
        steps = tuple((canonical_code(e, library=library), e.get("actor") != "partner") for e in ents)
        z_dom, n_mapped = granular_score(steps, vals, granularity=granularity, gamma=1.0)
        p_dom = p_own(z_dom) if n_mapped else float("nan")
        offsets = {
            "A0_difficulty": ELO_PER_DIFFICULTY_POINT
            * (float(rd.get("difficulty") or DIFFICULTY_BASELINE) - DIFFICULTY_BASELINE),
            "A0n_zero": 0.0,
            "A4_rrb": elo_offset(p_dom) if n_mapped else 0.0,
        }
        if not n_mapped:
            skipped_no_rrb += 1

        if outcome in ("succeeded", "failed"):
            ys.append(1 if outcome == "succeeded" else 0)
            for a in arms:
                st = state[a]
                ps[a].append(expected_score(st.rating, st.deviation, st.rating + offsets[a], st.deviation))

        if outcome == "no_attempt" or not own:
            continue
        ws = own_entry_weights(rd, block)
        for a in arms:
            st = state[a]
            obs = [
                Observation(
                    opponent_rating=st.rating + offsets[a],
                    opponent_deviation=st.deviation,
                    score=0.0 if e.get("successful") is False else 1.0,
                    weight=w,
                )
                for e, w in zip(own, ws, strict=True)
            ]
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
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    if args.all:
        args.owner_fixture = args.owner = args.corpus = args.sweep = args.addendum = True
    if not any((args.owner_fixture, args.owner, args.corpus, args.sweep, args.addendum)):
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

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
