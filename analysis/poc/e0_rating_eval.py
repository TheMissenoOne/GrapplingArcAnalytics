"""PoC-E0 — the rating-engine log-loss harness (docs/research/03_POC_PLANS.md).

ADR-03 fixed the criterion for every rating decision — out-of-sample prediction,
"spread is never a criterion" — and then no harness was ever built: the App exports
``expectedScore`` "for log-loss work" with zero callers, and ``calibrate_k_factor``
fits K to a target standard deviation, which is exactly the forbidden criterion one
module over. This is the instrument.

Protocol: **prequential (predict-then-update) walk-forward** over the corpus in
chronological order. Every engine predicts P(a wins) for a bout *before* seeing its
result, then observes it. Glicko-2 additionally closes rating periods at year
boundaries, so its predictions in year Y use states built from years <= Y-1 — the
temporal split ADR-03 asks for. Within-year bout order is not recorded in the
records corpus, so it is fixed deterministically (year, comp, stage, pair); online
engines (Elo, win rate) are mildly sensitive to that order, which is documented
rather than hidden.

Scoring: mean log loss (the headline, per ADR-03), Brier score and accuracy, each
with a percentile-bootstrap interval over bouts (``stats_rigor.bootstrap_ci``).
Sliced overall vs "experienced" (both athletes had >= EXPERIENCED_MIN_PRIOR prior
bouts at prediction time), because cold-start behaviour and steady-state behaviour
are different questions — FightMatrix's Glicko findings live entirely in the sparse
regime.

Corpus caveat, stated up front: the default source is the ADCC-2026-women scouting
records — an **ego-centric** corpus (16 rostered athletes' records plus what their
opponents' pages contribute). Opponent-vs-opponent bouts are mostly absent, so
opponent ratings are weakly informed and every engine's absolute numbers are worse
than they would be on a closed corpus like the `matches` table. The comparison
BETWEEN engines on one corpus is still fair — they all see the same stream — but
run this against the DB corpus before treating any absolute number as the engine's
quality. The graph-growth engine (`athlete_elo`) needs per-match sequences and is
therefore not comparable on a records-only corpus; it joins when run with
``--source db``.

Usage::

    uv run python -m analysis.poc.e0_rating_eval                 # scouting corpus
    uv run python -m analysis.poc.e0_rating_eval --out docs/research/poc/e0.md
"""

from __future__ import annotations

import argparse
import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.rating_v2.glicko2 import expected_score, update_period
from analysis.rating_v2.models import Observation, RatingState
from analysis.stats_rigor import bootstrap_ci

REPO = Path(__file__).resolve().parents[2]
SCOUTING_RECORDS = REPO / "data" / "scouting" / "adcc_2026_women_records.json"

EXPERIENCED_MIN_PRIOR = 5
CLIP = 1e-12  # log-loss clip; engines are allowed extreme confidence, not certainty

# Mirrors elo_calibration's felixgnwn constants -- kept local so this PoC has no
# import edge into the engine it is judging.
ELO_INITIAL = 1000.0
ELO_BASE_K = 40.0
WIN_TYPE_MULT = {"submission": 1.15, "decision": 0.85, "points": 1.0}
STAGE_MULT = {"SPF": 1.4, "F": 1.3, "SF": 1.2, "3RD": 1.15, "3PLC": 1.15}

GLICKO_SEED_RATING = 1750.0   # rating_v2 config: athlete_seed
GLICKO_SEED_RD = 250.0
GLICKO_SEED_VOL = 0.06
TAU_SWEEP = (0.2, 0.5, 0.8, 1.2)


# ── corpus ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bout:
    """One distinct bout, orientation fixed by sorted athlete keys so the label
    carries no information (a is NOT the winner; ``score_a`` says who won)."""

    a: str
    b: str
    score_a: float          # 1.0 = a won
    year: int
    method: str
    stage: str
    comp: str


def _fold(s: str | None) -> str:
    t = unicodedata.normalize("NFKD", (s or "").strip().casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def method_win_type(method: str | None) -> str:
    """Coarse win-type from a records method string. v0 heuristic, documented in
    the PoC doc: points prefixes and decision words are recognised; everything
    else that names a technique is a submission; voids get the neutral 1.0."""
    m = _fold(method)
    if not m or any(w in m for w in ("dq", "disqual", "injury", "walkover", "forfeit", "wo")):
        return "points"
    if m.startswith(("pts", "points", "pontos")) or "advantage" in m:
        return "points"
    if any(w in m for w in ("decision", "referee", "judges", "split")):
        return "decision"
    return "submission"


def load_scouting_bouts(path: Path = SCOUTING_RECORDS) -> tuple[list[Bout], dict[str, int]]:
    """Athlete-perspective rows -> distinct oriented bouts.

    Mirror folding follows `_distinct_bouts`'s provable rule: two rows are one bout
    only when each names the other as the opponent and one won while the other lost.
    Draws are excluded (win-probability engines; the corpus holds one).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for entry in data.get("athletes", {}).values():
        display = entry.get("display") or entry.get("key") or ""
        for r in entry.get("rows") or []:
            rows.append({**r, "athlete": display})

    dropped = {"draws": 0, "no_year": 0, "mirrors_folded": 0}
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("wl") == "D":
            dropped["draws"] += 1
            continue
        if not r.get("year"):
            dropped["no_year"] += 1
            continue
        pair = tuple(sorted((_fold(r.get("athlete")), _fold(r.get("opp")))))
        groups[(pair, _fold(r.get("comp")), r.get("year"), _fold(r.get("stage")))].append(r)

    bouts: list[Bout] = []
    for key, group in groups.items():
        paired: set[int] = set()
        for i, r in enumerate(group):
            if i in paired:
                continue
            keep = r
            for j in range(i + 1, len(group)):
                rj = group[j]
                if (j not in paired
                        and _fold(r.get("athlete")) == _fold(rj.get("opp"))
                        and _fold(rj.get("athlete")) == _fold(r.get("opp"))
                        and {r.get("wl"), rj.get("wl")} == {"W", "L"}):
                    paired.add(j)
                    keep = r if r.get("wl") == "W" else rj
                    dropped["mirrors_folded"] += 1
                    break
            ka, kb = _fold(keep.get("athlete")), _fold(keep.get("opp"))
            a, b = sorted((ka, kb))
            winner = ka if keep.get("wl") == "W" else kb
            bouts.append(Bout(a=a, b=b, score_a=1.0 if winner == a else 0.0,
                              year=int(keep["year"]), method=str(keep.get("method") or ""),
                              stage=str(keep.get("stage") or ""),
                              comp=str(keep.get("comp") or "")))

    bouts.sort(key=lambda x: (x.year, x.comp, x.stage, x.a, x.b))
    return bouts, dropped


# ── engines ─────────────────────────────────────────────────────────────────────
class ConstantBaseline:
    name = "constant-0.5"

    def predict(self, a: str, b: str) -> float:
        return 0.5

    def observe(self, bout: Bout) -> None:
        pass

    def close_year(self) -> None:
        pass


class WinRateBaseline:
    """Beta(1,1) posterior-mean win rates; P(a) = (p_a + (1 - p_b)) / 2."""

    name = "win-rate"

    def __init__(self) -> None:
        self.wins: dict[str, int] = defaultdict(int)
        self.bouts: dict[str, int] = defaultdict(int)

    def predict(self, a: str, b: str) -> float:
        pa = (self.wins[a] + 1) / (self.bouts[a] + 2)
        pb = (self.wins[b] + 1) / (self.bouts[b] + 2)
        return (pa + (1 - pb)) / 2

    def observe(self, bout: Bout) -> None:
        self.bouts[bout.a] += 1
        self.bouts[bout.b] += 1
        self.wins[bout.a if bout.score_a == 1.0 else bout.b] += 1

    def close_year(self) -> None:
        pass


class EloEngine:
    """felixgnwn-style corpus Elo: K = 40 x win-type x stage, /400 logistic."""

    def __init__(self, k: float = ELO_BASE_K, use_mults: bool = True) -> None:
        self.k = k
        self.use_mults = use_mults
        self.name = f"elo-k{k:g}" + ("" if use_mults else "-flat")
        self.r: dict[str, float] = defaultdict(lambda: ELO_INITIAL)

    def predict(self, a: str, b: str) -> float:
        return 1.0 / (1.0 + math.pow(10.0, (self.r[b] - self.r[a]) / 400.0))

    def observe(self, bout: Bout) -> None:
        e = self.predict(bout.a, bout.b)
        k = self.k
        if self.use_mults:
            k *= WIN_TYPE_MULT.get(method_win_type(bout.method), 1.0)
            k *= STAGE_MULT.get(bout.stage.strip().upper(), 1.0)
        delta = k * (bout.score_a - e)
        self.r[bout.a] += delta
        self.r[bout.b] -= delta

    def close_year(self) -> None:
        pass


class Glicko2Yearly:
    """Glicko-2 with calendar-year periods, matching rating_v2's ADR-04 replay:
    observations accumulate against opponents' PRE-period states, and predictions
    inside year Y use states closed through Y-1 (seed for the unseen)."""

    def __init__(self, tau: float = 0.5) -> None:
        self.tau = tau
        self.name = f"glicko2-tau{tau:g}"
        self.states: dict[str, RatingState] = {}
        self.pending: dict[str, list[Observation]] = defaultdict(list)
        self.seen: set[str] = set()

    def _state(self, key: str) -> RatingState:
        return self.states.get(key, RatingState(GLICKO_SEED_RATING, GLICKO_SEED_RD,
                                                GLICKO_SEED_VOL))

    def predict(self, a: str, b: str) -> float:
        sa, sb = self._state(a), self._state(b)
        return expected_score(sa.rating, sa.deviation, sb.rating, sb.deviation)

    def observe(self, bout: Bout) -> None:
        sa, sb = self._state(bout.a), self._state(bout.b)
        self.pending[bout.a].append(Observation(sb.rating, sb.deviation, bout.score_a))
        self.pending[bout.b].append(Observation(sa.rating, sa.deviation, 1.0 - bout.score_a))
        self.seen.update((bout.a, bout.b))

    def close_year(self) -> None:
        # Every athlete ever seen gets a period: active ones update on their
        # observations, inactive ones widen (rating_v2's deliberate choice).
        for key in sorted(self.seen):
            self.states[key] = update_period(self._state(key), self.pending.get(key, []),
                                             tau=self.tau)
        self.pending.clear()


# ── evaluation ──────────────────────────────────────────────────────────────────
@dataclass
class Scored:
    p: float
    score: float
    year: int
    experienced: bool


@dataclass
class EngineReport:
    name: str
    overall: dict[str, Any] = field(default_factory=dict)
    slices: dict[str, dict[str, Any]] = field(default_factory=dict)


def _metrics(rows: Sequence[Scored], n_boot: int = 2000) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    lls = [-(r.score * math.log(max(r.p, CLIP))
             + (1 - r.score) * math.log(max(1 - r.p, CLIP))) for r in rows]
    briers = [(r.p - r.score) ** 2 for r in rows]
    accs = [0.5 if r.p == 0.5
            else (1.0 if (r.p > 0.5) == (r.score == 1.0) else 0.0)
            for r in rows]
    def mean(xs: Sequence[float]) -> float:
        return sum(xs) / len(xs)

    ll, ll_lo, ll_hi = bootstrap_ci(lls, mean, n_boot=n_boot)
    return {"n": len(rows),
            "log_loss": ll, "log_loss_lo": ll_lo, "log_loss_hi": ll_hi,
            "brier": mean(briers), "accuracy": mean(accs)}


def evaluate(bouts: Sequence[Bout], engines: Sequence[Any],
             burn_in_years: int = 1, n_boot: int = 2000) -> list[EngineReport]:
    """Prequential evaluation. The first ``burn_in_years`` distinct years are
    observed but not scored — every engine starts ignorant, and scoring the cold
    open would mostly measure the seed constants."""
    years = sorted({b.year for b in bouts})
    scored_years = set(years[burn_in_years:])
    prior: dict[str, int] = defaultdict(int)
    per_engine: dict[str, list[Scored]] = {e.name: [] for e in engines}

    cur_year: int | None = None
    for bout in bouts:
        if cur_year is not None and bout.year != cur_year:
            for e in engines:
                e.close_year()
        cur_year = bout.year
        experienced = (prior[bout.a] >= EXPERIENCED_MIN_PRIOR
                       and prior[bout.b] >= EXPERIENCED_MIN_PRIOR)
        for e in engines:
            p = min(max(e.predict(bout.a, bout.b), 0.0), 1.0)
            if bout.year in scored_years:
                per_engine[e.name].append(Scored(p, bout.score_a, bout.year, experienced))
        for e in engines:
            e.observe(bout)
        prior[bout.a] += 1
        prior[bout.b] += 1
    for e in engines:
        e.close_year()

    reports = []
    for e in engines:
        rows = per_engine[e.name]
        rep = EngineReport(e.name, overall=_metrics(rows, n_boot))
        rep.slices["experienced"] = _metrics([r for r in rows if r.experienced], n_boot)
        rep.slices["cold_start"] = _metrics([r for r in rows if not r.experienced], n_boot)
        reports.append(rep)
    return reports


def default_engines(taus: Sequence[float] = TAU_SWEEP) -> list[Any]:
    engines: list[Any] = [ConstantBaseline(), WinRateBaseline(),
                          EloEngine(), EloEngine(use_mults=False)]
    engines += [Glicko2Yearly(tau=t) for t in taus]
    return engines


# ── report ──────────────────────────────────────────────────────────────────────
def _fmt(m: Mapping[str, Any]) -> str:
    if not m.get("n"):
        return "| — | — | — | — |"
    return (f"| {m['n']} | {m['log_loss']:.4f} [{m['log_loss_lo']:.4f}, "
            f"{m['log_loss_hi']:.4f}] | {m['brier']:.4f} | {m['accuracy']:.3f} |")


def render_markdown(reports: Sequence[EngineReport], bouts: Sequence[Bout],
                    dropped: Mapping[str, int], source: str) -> str:
    years = sorted({b.year for b in bouts})
    lines = [
        "# PoC-E0 — rating-engine log-loss harness: first run",
        "",
        f"Source: **{source}** · {len(bouts)} distinct bouts, {years[0]}–{years[-1]}"
        f" · dropped: {dict(dropped)} · burn-in: first corpus year unscored ·"
        " protocol and caveats in `analysis/poc/e0_rating_eval.py`'s docstring.",
        "",
        "Lower log loss is better; `constant-0.5` scores ln 2 ≈ 0.6931 by definition.",
        "",
        "## Overall",
        "",
        "| engine | n | log loss [95% CI] | Brier | accuracy |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(reports, key=lambda r: r.overall.get("log_loss", 9)):
        lines.append(f"| {r.name} " + _fmt(r.overall))
    for slice_name, title in (("experienced", "Both athletes ≥5 prior bouts"),
                              ("cold_start", "Cold start (either athlete <5 prior bouts)")):
        lines += ["", f"## {title}", "",
                  "| engine | n | log loss [95% CI] | Brier | accuracy |",
                  "|---|---|---|---|---|"]
        for r in sorted(reports, key=lambda r: r.slices[slice_name].get("log_loss", 9)):
            lines.append(f"| {r.name} " + _fmt(r.slices[slice_name]))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="scouting", choices=["scouting"],
                    help="bout stream (db source lands when a DATABASE_URL run is possible)")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e0.md"))
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args(argv)

    bouts, dropped = load_scouting_bouts()
    reports = evaluate(bouts, default_engines(), n_boot=args.n_boot)
    md = render_markdown(reports, bouts, dropped, source=args.source)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
