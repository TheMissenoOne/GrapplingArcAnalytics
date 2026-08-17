"""Category-level trend analysis: distribution, three-reading average profile, lift vs a
baseline corpus, concentration, bootstrap stability, marginal coverage value.

**PURO** — no DB, no file, no network. Every function takes plain lists of already-resolved
events (or bouts, where noted). This mirrors ``analysis.network_metrics.network_from_sequences``:
pure in, pure out, unit-testable without a fixture or a session.

Event shape (one athlete's own action): ``{"athlete": str, "label": str, "type": str,
"successful": bool | None, "bout_id": str | None}``. Two flatteners turn the bout shape that
``analysis.scouting_report``/``scripts.scouting_division_report`` already produce
(``{"participants": [...], "events": [{"label","type","actor","successful"}, ...]}``) into this:
``flatten_own_events`` (per-athlete corpus, keyed by roster name) for the CATEGORY side, and
``flatten_bout_events`` (any bout list, every actor counted) for the BASELINE side.

Weighting symmetry is structural, not a convention to remember: every function that compares
category against baseline (``lift_table``, ``lift_with_stability``) takes ONE ``weighting`` and
applies it to both sides via the same share function — there is no code path that can compare an
athlete-balanced share on one side against an event-weighted share on the other.

No import from ``analysis.rating_v2`` — see ``docs/rating_v2/README.md``. Rating, metagame and
game structure are three separate layers; this module is the metagame layer only.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from analysis.names import athlete_key

Event = Mapping[str, Any]

#: Below this raw count in the category, a label is excluded from the numeric lift ranking
#: and reported as "amostra insuficiente" instead — a 1-occurrence label cannot carry a lift.
MIN_LIFT_N = 3

#: A division/scope "amostra insuficiente para leitura de categoria" gate (sections 2-9):
#: fewer distinct bouts, or events from fewer than 2 athletes, and a category profile would
#: just be describing one athlete's dossier under a division's name.
MIN_DIVISION_BOUTS = 5

#: Time section (8) only renders with a time base for at least this share of events.
MIN_TIME_COVERAGE = 0.3

BOOTSTRAP_ITERATIONS = 200
#: Fraction of bootstrap resamples that must agree in sign with the observed lift for a label
#: to be labelled "alta" stability.
BOOTSTRAP_HIGH_STABILITY = 0.7


def _athlete_of(event: Event) -> str:
    return str(event.get("athlete") or "")


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


# ── flattening (bout shape -> flat event shape) ─────────────────────────────────────────

def flatten_own_events(
    bouts_by_athlete: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Category side: bouts already collected per roster athlete (as
    ``scripts.scouting_division_report._division_tables`` builds) -> each athlete's own events
    only. An intra-category bout is stored once under each participant's name; this only ever
    reads the event whose ``actor`` matches that name, so it is never double-counted."""
    out: list[dict[str, Any]] = []
    for athlete, bouts in bouts_by_athlete.items():
        for bout in bouts:
            for event in bout.get("events", []):
                if event.get("actor") != athlete:
                    continue
                out.append({
                    "athlete": athlete, "label": str(event.get("label") or ""),
                    "type": str(event.get("type") or ""),
                    "successful": event.get("successful"), "bout_id": bout.get("bout_id"),
                })
    return out


def flatten_bout_events(bouts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Baseline side (or any generic bout list): every event, tagged by its own actor — the
    baseline has no fixed roster, so unlike ``flatten_own_events`` there is nothing to filter
    against, only an actor to require."""
    out: list[dict[str, Any]] = []
    for bout in bouts:
        for event in bout.get("events", []):
            actor = event.get("actor")
            if not actor:
                continue
            out.append({
                "athlete": str(actor), "label": str(event.get("label") or ""),
                "type": str(event.get("type") or ""),
                "successful": event.get("successful"), "bout_id": bout.get("bout_id"),
            })
    return out


def exclude_roster_from_baseline(
    baseline_bouts: Sequence[Mapping[str, Any]], roster: Sequence[str]
) -> list[Mapping[str, Any]]:
    """Drop any baseline bout where a participant is one of the category's own roster athletes
    — otherwise the category partially compares against itself."""
    roster_keys = {athlete_key(name) for name in roster}
    return [
        bout for bout in baseline_bouts
        if not any(athlete_key(str(p)) in roster_keys for p in bout.get("participants", []))
    ]


# ── section 1: concentration ─────────────────────────────────────────────────────────────

def concentration(events: Sequence[Event]) -> dict[str, Any]:
    """Top-1 athlete share + Herfindahl-Hirschman index over per-athlete own-event counts.
    HHI here is on the [0, 1] share scale (perfectly split across N athletes -> 1/N; one
    athlete alone -> 1.0). This gates whether the rest of the report can be read as "the
    category" at all."""
    counts = Counter(_athlete_of(e) for e in events)
    total = sum(counts.values())
    if total == 0:
        return {"total_eventos": 0, "atletas": 0, "top1_atleta": None, "top1_share": 0.0,
                "hhi": 0.0}
    shares = {a: c / total for a, c in counts.items()}
    top1_atleta, top1_share = max(shares.items(), key=lambda kv: kv[1])
    hhi = sum(s * s for s in shares.values())
    return {
        "total_eventos": total, "atletas": len(counts), "top1_atleta": top1_atleta,
        "top1_share": round(top1_share, 4), "hhi": round(hhi, 4),
    }


def sufficient_sample(events: Sequence[Event], *, min_bouts: int = MIN_DIVISION_BOUTS) -> bool:
    """Gate for sections 2-9: at least ``min_bouts`` distinct bouts AND events from at least 2
    athletes. Below this a "category" reading is really just one athlete wearing a division's
    name (the gi-scope divisions: 1 and 3 bouts total)."""
    bout_ids = {e.get("bout_id") for e in events if e.get("bout_id")}
    athletes = {_athlete_of(e) for e in events} - {""}
    return len(bout_ids) >= min_bouts and len(athletes) >= 2


# ── section 2: three-reading average profile ─────────────────────────────────────────────

def _by_athlete(events: Sequence[Event]) -> dict[str, list[Event]]:
    out: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        out[_athlete_of(e)].append(e)
    return dict(out)


def _shares(events: Sequence[Event], key: str) -> dict[str, float]:
    counts = Counter(str(e.get(key) or "") for e in events)
    total = sum(counts.values())
    return {k: _safe_div(c, total) for k, c in counts.items()}


def profile_by_type(events: Sequence[Event], *, key: str = "type") -> dict[str, Any]:
    """Three readings of the category's composition (by ``key``, default event ``type``):
    equal weight per athlete (``peso_igual``, the PRIMARY reading), pooled by event
    (``ponderado_por_evento``, SECONDARY), and equal-weight with the top-volume athlete removed
    (``leave_one_out`` — robustness check on PRIMARY, never paired against a baseline)."""
    per_athlete = _by_athlete(events)
    keys = sorted({str(e.get(key) or "") for e in events})
    if not per_athlete:
        empty = dict.fromkeys(keys, 0.0)
        return {"peso_igual": dict(empty), "ponderado_por_evento": dict(empty),
                "leave_one_out": dict(empty), "atleta_excluida": None}

    per_athlete_shares = {a: _shares(evs, key) for a, evs in per_athlete.items()}
    n = len(per_athlete_shares)
    peso_igual = {k: round(sum(s.get(k, 0.0) for s in per_athlete_shares.values()) / n, 4)
                  for k in keys}
    ponderado = {k: round(v, 4) for k, v in _shares(events, key).items()}
    ponderado = {k: ponderado.get(k, 0.0) for k in keys}

    dominant = max(per_athlete.items(), key=lambda kv: len(kv[1]))[0]
    rest = {a: s for a, s in per_athlete_shares.items() if a != dominant}
    if rest:
        loo = {k: round(sum(s.get(k, 0.0) for s in rest.values()) / len(rest), 4) for k in keys}
    else:
        loo = dict.fromkeys(keys, 0.0)

    return {"peso_igual": peso_igual, "ponderado_por_evento": ponderado,
            "leave_one_out": loo, "atleta_excluida": dominant}


def profile_diverges(profile: Mapping[str, Mapping[str, float]], *, threshold: float = 0.1) -> bool:
    """True when PRIMARY and SECONDARY readings disagree by more than ``threshold`` on any
    key — the report must say so in prose instead of only showing the chart."""
    primary, secondary = profile["peso_igual"], profile["ponderado_por_evento"]
    keys = set(primary) | set(secondary)
    return any(abs(primary.get(k, 0.0) - secondary.get(k, 0.0)) >= threshold for k in keys)


# ── section 3: distribution ───────────────────────────────────────────────────────────────

def distribution(
    events: Sequence[Event], *, top: int = 15, key: str = "label",
) -> list[dict[str, Any]]:
    """Top-``n`` labels by raw frequency (event-weighted — a ranking is about what actually
    showed up, not a per-athlete average), ``n`` always attached."""
    counts = Counter(str(e.get(key) or "") for e in events)
    counts.pop("", None)
    total = sum(counts.values())
    return [{"label": label, "n": n, "share": round(_safe_div(n, total), 4)}
            for label, n in counts.most_common(top)]


# ── section 4: lift vs baseline (with weighting symmetry) ────────────────────────────────

def _label_shares_athlete_balanced(events: Sequence[Event], key: str) -> dict[str, float]:
    per_athlete = _by_athlete(events)
    per_athlete_shares = [s for s in (_shares(evs, key) for evs in per_athlete.values()) if s]
    if not per_athlete_shares:
        return {}
    n = len(per_athlete_shares)
    labels = {label for shares in per_athlete_shares for label in shares}
    return {label: sum(s.get(label, 0.0) for s in per_athlete_shares) / n for label in labels}


def _label_shares_event_weighted(events: Sequence[Event], key: str) -> dict[str, float]:
    return _shares(events, key)


#: The only two weighting schemes a comparison may use, each applying the SAME share function
#: to category and baseline — this dict is what makes crossing PRIMARY/SECONDARY impossible.
_SHARE_FN = {
    "athlete_balanced": _label_shares_athlete_balanced,
    "event_weighted": _label_shares_event_weighted,
}


def lift_table(
    category_events: Sequence[Event], baseline_events: Sequence[Event], *,
    weighting: str, key: str = "label", min_n: int = MIN_LIFT_N,
) -> dict[str, Any]:
    """``log2(p_categoria / p_baseline)`` per label, both sides computed with the SAME
    ``weighting`` ("athlete_balanced" = PRIMARY, "event_weighted" = SECONDARY — never mixed).

    A label with a raw category count below ``min_n`` never enters the numeric ranking (a
    single occurrence cannot carry a lift) — it lands in ``amostra_insuficiente``. A label the
    category has but the baseline has never seen never produces an infinite lift — it lands in
    ``inedito_no_baseline`` with its ``n``, not a numerical rank.
    """
    if weighting not in _SHARE_FN:
        raise ValueError(
            f"weighting desconhecido: {weighting!r} (use athlete_balanced|event_weighted)"
        )
    share_fn = _SHARE_FN[weighting]
    category_counts = Counter(str(e.get(key) or "") for e in category_events)
    category_counts.pop("", None)
    category_share = share_fn(category_events, key)
    baseline_share = share_fn(baseline_events, key)

    ranking: list[dict[str, Any]] = []
    insuficiente: list[dict[str, Any]] = []
    inedito: list[dict[str, Any]] = []
    for label, n in category_counts.items():
        if n < min_n:
            insuficiente.append({"label": label, "n": n})
            continue
        p_cat = category_share.get(label, 0.0)
        p_base = baseline_share.get(label, 0.0)
        if p_base == 0.0:
            inedito.append({"label": label, "n": n, "p_categoria": round(p_cat, 4)})
            continue
        lift = math.log2(p_cat / p_base) if p_cat > 0 else 0.0
        ranking.append({"label": label, "n": n, "p_categoria": round(p_cat, 4),
                         "p_baseline": round(p_base, 4), "log2_lift": round(lift, 3),
                         "estabilidade": None})
    ranking.sort(key=lambda row: row["log2_lift"], reverse=True)
    return {
        "weighting": weighting,
        "ranking": ranking,
        "amostra_insuficiente": sorted(insuficiente, key=lambda r: -r["n"]),
        "inedito_no_baseline": sorted(inedito, key=lambda r: -r["n"]),
    }


def bootstrap_lift_stability(
    category_events: Sequence[Event], baseline_events: Sequence[Event], label: str, *,
    weighting: str, key: str = "label", iterations: int = BOOTSTRAP_ITERATIONS,
    min_n: int = MIN_LIFT_N, seed: int = 0,
) -> dict[str, Any]:
    """Resample athletes (with replacement, same athlete count as the observed category),
    recompute ``label``'s lift each draw. A technique spread across several athletes keeps
    showing up with the same lift sign almost every draw; one concentrated in a single athlete
    frequently loses that athlete (or gets her 2-3x, which a naive per-name regroup would
    silently merge back into "1 athlete" — each draw is relabelled to a synthetic per-draw
    identity so athlete-balanced share is a real per-draw average, not a merge).

    ``estabilidade`` = "alta" when >= ``BOOTSTRAP_HIGH_STABILITY`` of resamples keep the label
    present at ``n >= min_n`` with the observed lift's sign, else "baixa".
    """
    per_athlete = _by_athlete(category_events)
    athletes = sorted(per_athlete)
    if not athletes:
        return {"label": label, "estabilidade": "baixa", "fracao_estavel": 0.0}

    share_fn = _SHARE_FN[weighting]
    baseline_share = share_fn(baseline_events, key)
    p_base = baseline_share.get(label, 0.0)
    observed = lift_table(category_events, baseline_events, weighting=weighting, key=key,
                          min_n=min_n)
    observed_row = next((r for r in observed["ranking"] if r["label"] == label), None)
    if observed_row is None or p_base == 0.0:
        return {"label": label, "estabilidade": "baixa", "fracao_estavel": 0.0}
    observed_sign = 1 if observed_row["log2_lift"] >= 0 else -1

    rng = random.Random(seed)
    stable = 0
    for _ in range(iterations):
        draws = [rng.choice(athletes) for _ in athletes]
        resampled = [
            {**e, "athlete": f"{a}#{i}"}
            for i, a in enumerate(draws) for e in per_athlete[a]
        ]
        n = sum(1 for e in resampled if str(e.get(key) or "") == label)
        if n < min_n:
            continue
        p_cat = share_fn(resampled, key).get(label, 0.0)
        if p_cat <= 0:
            continue
        sign = 1 if math.log2(p_cat / p_base) >= 0 else -1
        if sign == observed_sign:
            stable += 1
    fracao = round(stable / iterations, 3)
    estabilidade = "alta" if fracao >= BOOTSTRAP_HIGH_STABILITY else "baixa"
    return {"label": label, "estabilidade": estabilidade, "fracao_estavel": fracao}


def lift_with_stability(
    category_events: Sequence[Event], baseline_events: Sequence[Event], *,
    weighting: str, key: str = "label", min_n: int = MIN_LIFT_N, limit: int = 20,
    iterations: int = BOOTSTRAP_ITERATIONS, seed: int = 0,
) -> dict[str, Any]:
    """``lift_table`` with ``estabilidade`` attached to the ``limit`` labels with the largest
    |lift| (bootstrap is only run for what the report actually shows — see
    ``bootstrap_lift_stability`` for the cost per label)."""
    table = lift_table(category_events, baseline_events, weighting=weighting, key=key, min_n=min_n)
    ranking = table["ranking"]
    interesting = sorted(ranking, key=lambda r: abs(r["log2_lift"]), reverse=True)[:limit]
    stability = {
        row["label"]: bootstrap_lift_stability(
            category_events, baseline_events, row["label"], weighting=weighting, key=key,
            iterations=iterations, min_n=min_n, seed=seed,
        )["estabilidade"]
        for row in interesting
    }
    for row in ranking:
        row["estabilidade"] = stability.get(row["label"])
    return table


# ── section 7: effectiveness (resolved base only) ────────────────────────────────────────

def effectiveness(events: Sequence[Event], *, key: str = "label") -> list[dict[str, Any]]:
    """Success rate computed only over resolved outcomes (``successful is True/False``), with
    ``resolvido_pct`` reporting how much of that label's base was actually apurada — no rate is
    ever computed over an unresolved base, and a label with 0 resolved simply has no
    ``taxa_sucesso`` key rather than a fabricated 0%."""
    by_label: dict[str, Counter[str]] = defaultdict(Counter)
    for e in events:
        label = str(e.get(key) or "")
        if not label:
            continue
        outcome = e.get("successful")
        bucket = "true" if outcome is True else "false" if outcome is False else "unknown"
        by_label[label][bucket] += 1
    rows = []
    for label, c in by_label.items():
        total = sum(c.values())
        resolved = c["true"] + c["false"]
        row: dict[str, Any] = {"label": label, "n": total,
                               "resolvido_pct": round(_safe_div(resolved, total), 4)}
        if resolved:
            row["taxa_sucesso"] = round(c["true"] / resolved, 4)
        rows.append(row)
    return sorted(rows, key=lambda r: -r["n"])


# ── section 8: time gate ──────────────────────────────────────────────────────────────────

def time_available(
    cobertura: Mapping[str, Any], *, min_coverage: float = MIN_TIME_COVERAGE,
) -> bool:
    """Reuses ``analysis.scouting_tables.build_tables``'s own ``cobertura`` (no re-derivation):
    section 8 only renders when at least ``min_coverage`` of events have a resolved time
    bucket."""
    total = int(cobertura.get("eventos", 0))
    if total == 0:
        return False
    sem_tempo = int(cobertura.get("eventos_sem_tempo", 0))
    return _safe_div(total - sem_tempo, total) >= min_coverage


# ── section 9: marginal coverage value ────────────────────────────────────────────────────

def coverage_marginal_value(roster: Sequence[str], events: Sequence[Event]) -> list[dict[str, Any]]:
    """Which roster athletes, if analysed further, would most reduce concentration — ranked by
    who has the fewest own events today (zero first). Derived from the corpus itself, never
    from an external inventory."""
    counts = Counter(_athlete_of(e) for e in events)
    rows = [{"atleta": name, "eventos_proprios": counts.get(name, 0)} for name in roster]
    return sorted(rows, key=lambda r: r["eventos_proprios"])
