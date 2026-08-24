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
``--source db``. ``AthleteEloEngine`` below calls the real ``athlete_elo.replay_matches``
(not a reimplementation) -- it is a batch replay toward a rank-ELO target, not natively
predict-then-update, so the adapter re-replays each athlete's own history-so-far on every
observed bout and caches the resulting graph-mean ELO, predicting via the same /400
logistic the other engines use. Bounded cost on this corpus (<= ~100 bouts for the
busiest athlete).

Usage::

    uv run python -m analysis.poc.e0_rating_eval                 # scouting corpus
    uv run python -m analysis.poc.e0_rating_eval --source db     # closed `matches` corpus
    uv run python -m analysis.poc.e0_rating_eval --source all --out docs/research/poc/e0.md
"""

from __future__ import annotations

import argparse
import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from analysis import athlete_elo
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
K_SWEEP = (10.0, 20.0, 30.0, 40.0, 60.0, 80.0)


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
    # Raw match events (``{label, type, actor_id, successful?}``), db source only --
    # needed by AthleteEloEngine to replay each athlete's own perspective. Empty for
    # the scouting records, which carry no event-level data.
    sequence: tuple[Mapping[str, Any], ...] = ()


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


DB_WIN_TYPES = ("SUBMISSION", "POINTS", "DECISION")


def load_db_bouts(
    rows: Sequence[Any] | None = None,
) -> tuple[list[Bout], dict[str, int]]:
    """Chronological bout stream from the closed ``matches`` corpus (read-only).

    Eligible: ``status == 'final'``, ``win_type`` one of SUBMISSION/POINTS/DECISION
    (drops DRAW, DQ, INJURY, NULL), and a known ``winner_id`` (drops the residual
    decisions scraped without one). Ordered by ``(year, created_at)`` -- the corpus's
    actual chronology (unlike the scouting loader, which has no ``created_at`` and
    falls back to a deterministic (comp, stage, pair) tiebreak).

    ``rows`` injectable for tests (mapping-like rows, e.g. sqlalchemy ``RowMapping``
    or plain dicts with the same keys); ``None`` reads the live DB.
    """
    resolved: Sequence[Any]
    if rows is None:
        from sqlalchemy import select

        from db.base import db_session
        from db.models import Match

        with db_session() as session:
            resolved = (
                session.execute(
                    select(
                        Match.athlete_a_id, Match.athlete_b_id, Match.winner_id,
                        Match.year, Match.win_type, Match.stage, Match.event,
                        Match.status, Match.sequence, Match.created_at,
                    )
                )
                .mappings()
                .all()
            )
    else:
        resolved = rows

    dropped = {"not_final": 0, "excluded_win_type": 0, "no_winner": 0, "no_year": 0}
    eligible: list[Any] = []
    for r in resolved:
        if r["status"] != "final":
            dropped["not_final"] += 1
            continue
        if (r["win_type"] or "").upper() not in DB_WIN_TYPES:
            dropped["excluded_win_type"] += 1
            continue
        if not r["winner_id"]:
            dropped["no_winner"] += 1
            continue
        if not r["year"]:
            dropped["no_year"] += 1
            continue
        eligible.append(r)

    eligible.sort(key=lambda r: (r["year"], r["created_at"] or datetime.min))

    bouts: list[Bout] = []
    for r in eligible:
        aid_a, aid_b = str(r["athlete_a_id"]), str(r["athlete_b_id"])
        a, b = sorted((aid_a, aid_b))
        bouts.append(Bout(
            a=a, b=b, score_a=1.0 if str(r["winner_id"]) == a else 0.0,
            year=int(r["year"]), method=str(r["win_type"] or "").upper(),
            stage=str(r["stage"] or ""), comp=str(r["event"] or ""),
            sequence=tuple(r["sequence"] or ()),
        ))
    return bouts, dropped


def load_rank_elo(rows: Sequence[Any] | None = None) -> dict[str, float]:
    """``Athlete.rank_elo`` by id -- the ADCC leaderboard target ``athlete_elo`` replays
    toward. Injectable for tests; ``None`` reads the live DB."""
    resolved: Sequence[Any]
    if rows is None:
        from sqlalchemy import select

        from db.base import db_session
        from db.models import Athlete

        with db_session() as session:
            stmt = select(Athlete.id, Athlete.rank_elo).where(Athlete.rank_elo.is_not(None))
            resolved = session.execute(stmt).mappings().all()
    else:
        resolved = rows
    return {str(r["id"]): float(r["rank_elo"]) for r in resolved}


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


class Glicko2PerBout:
    """Glicko-2 with one bout = one rating period, closed immediately after both sides
    observe it -- e0_notes' predicted fix for period granularity (each bout updates the
    state a prediction three bouts later can already see, matching Elo's per-bout update
    cadence instead of freezing a whole year at once)."""

    def __init__(self, tau: float = 0.5) -> None:
        self.tau = tau
        self.name = f"glicko2-perbout-tau{tau:g}"
        self.states: dict[str, RatingState] = {}

    def _state(self, key: str) -> RatingState:
        return self.states.get(key, RatingState(GLICKO_SEED_RATING, GLICKO_SEED_RD,
                                                GLICKO_SEED_VOL))

    def predict(self, a: str, b: str) -> float:
        sa, sb = self._state(a), self._state(b)
        return expected_score(sa.rating, sa.deviation, sb.rating, sb.deviation)

    def observe(self, bout: Bout) -> None:
        sa, sb = self._state(bout.a), self._state(bout.b)
        self.states[bout.a] = update_period(
            sa, [Observation(sb.rating, sb.deviation, bout.score_a)], tau=self.tau)
        self.states[bout.b] = update_period(
            sb, [Observation(sa.rating, sa.deviation, 1.0 - bout.score_a)], tau=self.tau)

    def close_year(self) -> None:
        pass  # periods close per-bout in observe(), not at year boundary


class AthleteEloEngine:
    """Adapter over the real graph-growth engine (``analysis.athlete_elo.replay_matches``),
    not a reimplementation. That engine batch-replays ONE athlete's own match history
    toward a rank-ELO target, so it has no native symmetric predict(a, b); this adapter
    re-replays each side's updated history-so-far on every observed bout (bounded on this
    corpus -- busiest athlete tops out around 100 bouts) and caches the resulting graph-mean
    ELO, predicting with the same /400 logistic (``athlete_elo.expected``) the other engines
    use. DB-only: needs per-bout ``sequence`` + ``Athlete.rank_elo``, neither on the
    scouting records.

    Known simplification: ``Match`` rows carry no per-bout date (only ``year`` and
    ``created_at``), so every replayed match gets ``date=None`` -- ``athlete_elo``'s
    temporal K-decay is inert here rather than biased by wall-clock run time.
    """

    name = "athlete-elo"

    def __init__(self, rank_targets: Mapping[str, float]) -> None:
        self.rank_targets = rank_targets
        self.history: dict[str, list[Any]] = defaultdict(list)
        self.opp_elos: dict[str, list[float]] = defaultdict(list)
        self.mean: dict[str, float] = {}

    def _target(self, athlete: str) -> float:
        return self.rank_targets.get(athlete, athlete_elo.BASE_BLACKBELT_ELO)

    def predict(self, a: str, b: str) -> float:
        ra = self.mean.get(a, self._target(a))
        rb = self.mean.get(b, self._target(b))
        return athlete_elo.expected(ra, rb)

    def observe(self, bout: Bout) -> None:
        if not bout.sequence:
            return
        for me, opp, won in ((bout.a, bout.b, bout.score_a == 1.0),
                             (bout.b, bout.a, bout.score_a == 0.0)):
            events = [
                {"label": e.get("label", ""), "type": e.get("type", ""),
                 "actor": "you" if e.get("actor_id") == me else "opponent"}
                for e in bout.sequence
            ]
            match = SimpleNamespace(
                sequence=events, won=won, win_type=bout.method, date=None,
                id=f"{bout.year}-{bout.comp}-{me}-{len(self.history[me])}",
            )
            self.history[me].append(match)
            self.opp_elos[me].append(self._target(opp))
            _, snapshots = athlete_elo.replay_matches(
                me, self.history[me], self._target(me), self.opp_elos[me], belt="black",
            )
            if snapshots:
                self.mean[me] = snapshots[-1]

    def close_year(self) -> None:
        pass


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
    engines.append(Glicko2PerBout(tau=0.5))
    return engines


def run_k_sweep(
    bouts: Sequence[Bout], k_values: Sequence[float] = K_SWEEP, n_boot: int = 2000,
) -> list[tuple[float, str, EngineReport]]:
    """Elo K sweep by predictive log loss -- flat and win-type/stage-multiplied,
    replacing ``calibrate_k_factor``'s target-sigma criterion (ADR-03)."""
    rows: list[tuple[float, str, EngineReport]] = []
    for k in k_values:
        for use_mults, label in ((True, "mult"), (False, "flat")):
            rep = evaluate(bouts, [EloEngine(k=k, use_mults=use_mults)], n_boot=n_boot)[0]
            rows.append((k, label, rep))
    return rows


# ── report ──────────────────────────────────────────────────────────────────────
def _fmt(m: Mapping[str, Any]) -> str:
    if not m.get("n"):
        return "| — | — | — | — |"
    return (f"| {m['n']} | {m['log_loss']:.4f} [{m['log_loss_lo']:.4f}, "
            f"{m['log_loss_hi']:.4f}] | {m['brier']:.4f} | {m['accuracy']:.3f} |")


def render_markdown(reports: Sequence[EngineReport], bouts: Sequence[Bout],
                    dropped: Mapping[str, int], source: str,
                    k_rows: Sequence[tuple[float, str, EngineReport]] | None = None) -> str:
    years = sorted({b.year for b in bouts})
    lines = [
        f"# PoC-E0 — rating-engine log-loss harness: {source} run",
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
    if k_rows:
        lines += ["", "## K sweep — Elo by log loss (flat and win-type/stage multiplied)",
                  "", "| K | variant | n | log loss [95% CI] | Brier | accuracy |",
                  "|---|---|---|---|---|---|"]
        for k, label, rep in k_rows:
            lines.append(f"| {k:g} | {label} " + _fmt(rep.overall))
        best_k, best_label, best_rep = min(k_rows, key=lambda t: t[2].overall.get("log_loss", 9))
        lines += ["", f"Best by log loss: K={best_k:g} ({best_label}), "
                      f"{best_rep.overall['log_loss']:.4f}."]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="scouting", choices=["scouting", "db", "all"],
                    help="bout stream: scouting records, closed `matches` db corpus, or both")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e0.md"))
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args(argv)

    sources = ["scouting", "db"] if args.source == "all" else [args.source]
    sections: list[str] = []
    for source in sources:
        if source == "scouting":
            bouts, dropped = load_scouting_bouts()
            engines = default_engines()
        else:
            bouts, dropped = load_db_bouts()
            engines = default_engines()
            engines.append(AthleteEloEngine(load_rank_elo()))
        reports = evaluate(bouts, engines, n_boot=args.n_boot)
        k_rows = run_k_sweep(bouts, n_boot=args.n_boot)
        sections.append(render_markdown(reports, bouts, dropped, source=source, k_rows=k_rows))

    md = "\n---\n\n".join(sections)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
