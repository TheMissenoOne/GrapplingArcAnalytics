"""Everything the BracketAnalysis site renders, computed once, with an interval on every number.

One exporter, one `data.json`. The site does no statistics: it draws what this file decides,
so a number can never differ between the page and the analysis that produced it.

Scope is the CATEGORY, never an athlete. Per-athlete rows exist only where the question is
itself about distribution across athletes -- coverage, concentration, archetype outliers --
and they carry the athlete's own uncertainty so a thin record cannot masquerade as a profile.

Layers are kept apart on purpose and never fill each other in:

  method    602 bouts of published records -- how a bout ENDED, nothing about the path
  sequence   corpus bouts with event-by-event data -- the path, on a much smaller sample
  embedding  graph vectors and archetypes -- shape of a game, thinnest layer of the three

Privacy class: **A, public competition data.** Every graph query filters
`owner_kind='athlete'` explicitly. App-fed user graphs (6 rows, 3 embedded) are excluded by
construction, never by luck -- an unfiltered `select(Graph)` here would put a user's private
game into a public category centroid.

    uv run python -m scripts.bracket_export --out ../BracketAnalysis/data.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from sqlalchemy import text  # noqa: E402

from analysis.names import athlete_key  # noqa: E402
from analysis.stats_rigor import (  # noqa: E402
    benjamini_hochberg,
    compare_proportions,
    heterogeneity,
    shannon_concentration,
    wilson,
)
from db.base import get_engine  # noqa: E402

MANIFEST = REPO / "data" / "scouting" / "adcc_2026_women.json"
FRAMES = REPO / "data" / "frame_pdf" / "out"

# ── vocabulary ──────────────────────────────────────────────────────────────────
LEG = r"heel hook|toe hold|kneebar|knee bar|ankle|foot lock|footlock|aoki|estima|calf|leg lock|50/50"
STRANGLE = (r"rnc|choke|strangle|guillotine|triangle|katagatame|ezekiel|bow and arrow|darce|"
            r"d'arce|anaconda|brabo|clock|loop|baseball|north.?south|von flue")
JOINT = r"armbar|arm bar|kimura|americana|omoplata|wrist ?lock|choi bar|monoplata|arm ?lock"
SLICER = r"slicer|twister|crucifix"
POINTS = r"^pts|^points|advantage|^adv|^\d+x\d+"
DECISION = r"referee decision|decision|judge"
VOID = r"^dq|disqualif|injur|withdraw|n/a|^-$|walkover|forfeit"
SUB_FAMILIES = ("strangle", "joint", "leg", "slicer", "sub_other")
FAMILIES = ("points", "decision", *SUB_FAMILIES, "void")

# Uniform. Gi and no-gi are different sports for this purpose: the grips, the pace and which
# submissions are even available all change, so pooling them produces an average of two
# distributions that describes neither.
NO_GI = ("no-gi", "no gi", "nogi", "adcc", "polaris", "wno", "who's number one", "cji",
         "quintet", "subversiv", "f2w", "fight to win", "grapplefest", "adxc", "ufc fpi",
         "ufc fp", "pgf", "sub", "queen of mats", "emerald city", "main character",
         "spokane subs", "pit series", "grappling ind", "adgs", "grand slam")
GI = ("world champ", "pan american", "european champ", "brasileiro", "world pro", "copa",
      "gi ", "ibjjf gi", "jiu-jitsu champ", "san jose open", "sacramento o", "bjj stars")

# Rulesets. Sourced from data/scouting/rulesets.json, which records decision_model per family.
# The three differ in exactly the way that changes how a bout ends: ADCC has a negative-points
# window then overtime, IBJJF scores throughout with advantages, and superfights are commonly
# submission-only with a judges' card as the fallback.
RULESETS = {
    "adcc": ("adcc", "adgs", "grand slam", "world pro"),
    "ibjjf": ("world champ", "pan american", "european", "no-gi worlds", "world nogi",
              "world no-gi", "nogi pan", "pan nogi", "no gi pan", "brasileiro", "open",
              "jiu-jitsu champ", "world champ."),
    "superfight": ("polaris", "wno", "who's number one", "cji", "f2w", "fight to win",
                   "subversiv", "grapplefest", "adxc", "queen of mats", "bjj stars",
                   "main character", "spokane subs", "pit series", "ufc fp", "quintet"),
}


def _fold(s: str | None) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def family(method: str | None) -> str:
    s = _fold(method)
    if not s or re.search(VOID, s):
        return "void"
    if re.search(POINTS, s):
        return "points"
    if re.search(DECISION, s):
        return "decision"
    if re.search(LEG, s):
        return "leg"
    if re.search(STRANGLE, s):
        return "strangle"
    if re.search(JOINT, s):
        return "joint"
    if re.search(SLICER, s):
        return "slicer"
    return "sub_other"


def uniform(event: str | None) -> str:
    s = _fold(event)
    if any(w in s for w in NO_GI):
        return "no_gi"
    if any(w in s for w in GI):
        return "gi"
    return "unknown"


def ruleset(event: str | None) -> str:
    s = _fold(event)
    for name, keys in RULESETS.items():
        if any(k in s for k in keys):
            return name
    return "other"


def est(k: int, n: int) -> dict[str, Any]:
    return wilson(k, n).to_dict()


# ── layer 0: rating confidence ──────────────────────────────────────────────────
def rating_layer(conn: Any, roster: Mapping[str, str]) -> dict[str, Any]:
    """Glicko-2 rating and RD per rostered athlete, matched by CANONICAL KEY.

    Not by ``lower(name)``. The roster spells "Sarah Galvão" and the corpus spells "Sarah
    Galvao", and a case-insensitive compare is still accent-sensitive -- so the first version
    of this reported an athlete with a rating of 1965 at RD 157, the second most confident in
    her division, as having no rating at all. `athlete_key` is the same normalisation the rest
    of this codebase uses for identity, and using anything else here reintroduces exactly the
    silent name-mismatch class this corpus has been repeatedly bitten by.

    High RD does NOT mean a weak athlete. It means the rating exists and is not settled enough
    to carry a comparison, which is a statement about evidence, not ability -- and the page
    says so beside the number.
    """
    from analysis.rating_v2.config import SITE_MIN_CONFIDENCE_RD, SITE_RATING_RUN_ID

    rows = conn.execute(text("""
        select a.name, s.rating, s.rating_deviation, s.volatility, s.bout_count
          from athlete_rating_states_v2 s join athletes a on a.id = s.athlete_id
         where s.run_id = :run
    """), {"run": SITE_RATING_RUN_ID}).fetchall()
    by_key = {athlete_key(r[0]): r for r in rows}

    out: dict[str, Any] = {}
    unmatched: list[str] = []
    for key, div in roster.items():
        hit = by_key.get(key)
        out[key] = {"division": div, "rated": bool(hit),
                    "db_name": hit[0] if hit else None,
                    "rating": round(float(hit[1])) if hit else None,
                    "rd": round(float(hit[2])) if hit else None,
                    "bouts": int(hit[4]) if hit else 0,
                    "passes_gate": bool(hit and float(hit[2]) <= SITE_MIN_CONFIDENCE_RD)}
        if not hit:
            unmatched.append(key)
    return {"gate": SITE_MIN_CONFIDENCE_RD, "run": SITE_RATING_RUN_ID,
            "matched_on": "analysis.names.athlete_key (accent- and case-folded)",
            "rated": sum(1 for v in out.values() if v["rated"]),
            "passing_gate": sum(1 for v in out.values() if v["passes_gate"]),
            "unmatched": unmatched, "athletes": out}


# ── layer 1: published match records ────────────────────────────────────────────
def method_layer(records: Mapping[str, Any]) -> dict[str, Any]:
    """Counts of how bouts ended, cut by division, uniform and ruleset.

    Every cut is reported with its own n. A cell computed off four bouts is not hidden; it is
    graded `insufficient` and rendered dimmed, because knowing the cut is empty is itself the
    finding for half of this roster.
    """
    out: dict[str, Any] = {}
    for div in ("65 kg", "+65 kg"):
        rows = [
            {**r, "uniform": uniform(r["comp"]), "ruleset": ruleset(r["comp"]),
             "family": family(r["method"])}
            for nm, v in records.items() if v["division"] == div
            for r in (v.get("rows") or [])
        ]
        cuts: dict[str, Any] = {}
        for cut_name, subset in _cuts(rows):  # noqa: B007
            w = [r for r in subset if r["wl"] == "W"]
            loss = [r for r in subset if r["wl"] == "L"]
            fw, fl = Counter(r["family"] for r in w), Counter(r["family"] for r in loss)
            subs_w = sum(fw[f] for f in SUB_FAMILIES)
            subs_l = sum(fl[f] for f in SUB_FAMILIES)
            cuts[cut_name] = {
                "n": len(subset), "w": len(w), "l": len(loss),
                "win_by": {f: est(fw[f], len(w)) for f in FAMILIES},
                "loss_by": {f: est(fl[f], len(loss)) for f in FAMILIES},
                "finish_rate": est(subs_w, len(w)),
                "conceded_finish_rate": est(subs_l, len(loss)),
                "leg_contrast": compare_proportions(
                    fl["leg"], len(loss), fw["leg"], len(w)).to_dict(),
                "named_applied": Counter(_fold(r["method"]) for r in w
                                         if r["family"] in SUB_FAMILIES).most_common(14),
                "named_conceded": Counter(_fold(r["method"]) for r in loss
                                          if r["family"] in SUB_FAMILIES).most_common(14),
            }
        out[div] = {"cuts": cuts,
                    "uniform_split": Counter(r["uniform"] for r in rows),
                    "ruleset_split": Counter(r["ruleset"] for r in rows),
                    "by_year": _year_series(rows),
                    "ruleset_test": _ruleset_test(rows),
                    "uniform_test": _uniform_test(rows)}
    return out


def _cuts(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    cuts = [("all", list(rows))]
    for u in ("no_gi", "gi", "unknown"):
        cuts.append((f"uniform:{u}", [r for r in rows if r["uniform"] == u]))
    for rs in ("adcc", "ibjjf", "superfight", "other"):
        cuts.append((f"ruleset:{rs}", [r for r in rows if r["ruleset"] == rs]))
    # the cut that actually matters: ruleset WITHIN no-gi, so uniform is not a confounder
    for rs in ("adcc", "ibjjf", "superfight"):
        cuts.append((f"no_gi+{rs}",
                     [r for r in rows if r["uniform"] == "no_gi" and r["ruleset"] == rs]))
    return [(k, v) for k, v in cuts if v]


def _year_series(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by: dict[int, dict[str, Counter[str]]] = defaultdict(
        lambda: {"gi": Counter(), "no_gi": Counter(), "all": Counter()})
    for r in rows:
        if not r.get("year"):
            continue
        by[r["year"]]["all"][r["family"]] += 1
        if r["uniform"] in ("gi", "no_gi"):
            by[r["year"]][r["uniform"]][r["family"]] += 1
    out = {}
    for y, d in sorted(by.items()):
        if y < 2018:
            continue
        row = {}
        for scope, c in d.items():
            n = sum(c.values())
            row[scope] = {"n": n, "finish": est(sum(c[f] for f in SUB_FAMILIES), n),
                          "points": est(c["points"], n), "decision": est(c["decision"], n)}
        out[str(y)] = row
    return out


def _ruleset_test(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Does the way a bout ends change with the ruleset? Restricted to no-gi so uniform is
    not doing the work, and reported with the chi-square's own reliability flag."""
    ng = [r for r in rows if r["uniform"] == "no_gi"]
    groups = ["adcc", "ibjjf", "superfight"]
    table: list[list[int]] = []
    labels: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for g in groups:
        sub = [r for r in ng if r["ruleset"] == g]
        if len(sub) < 5:
            continue
        c = Counter(r["family"] for r in sub)
        subs = sum(c[f] for f in SUB_FAMILIES)
        table.append([subs, c["points"], c["decision"]])
        labels.append({"ruleset": g, "n": len(sub), "finish": est(subs, len(sub)),
                       "points": est(c["points"], len(sub)),
                       "decision": est(c["decision"], len(sub))})
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            pairs.append({"a": a["ruleset"], "b": b["ruleset"],
                          "contrast": compare_proportions(
                              a["finish"]["k"], a["n"], b["finish"]["k"], b["n"]).to_dict()})
    ps = [p["contrast"]["p_value"] for p in pairs if p["contrast"]["p_value"] is not None]
    keep = benjamini_hochberg(ps, 0.05)
    it = iter(keep)
    for p in pairs:
        p["survives_bh"] = next(it) if p["contrast"]["p_value"] is not None else False
    return {"groups": labels, "pairs": pairs,
            "heterogeneity": heterogeneity(table).to_dict() if len(table) > 1 else None}


def _uniform_test(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    g = [r for r in rows if r["uniform"] == "gi"]
    n = [r for r in rows if r["uniform"] == "no_gi"]
    if len(g) < 5 or len(n) < 5:
        return {"available": False, "gi_n": len(g), "no_gi_n": len(n)}
    fg = sum(1 for r in g if r["family"] in SUB_FAMILIES)
    fn = sum(1 for r in n if r["family"] in SUB_FAMILIES)
    return {"available": True, "gi_n": len(g), "no_gi_n": len(n),
            "gi_finish": est(fg, len(g)), "no_gi_finish": est(fn, len(n)),
            "contrast": compare_proportions(fn, len(n), fg, len(g)).to_dict()}


# ── layer 2: event sequences ────────────────────────────────────────────────────
def _own(bout: Mapping[str, Any], side: str) -> list[dict[str, Any]]:
    aid = bout[f"{side}_id"]
    return [e for e in bout["seq"] if e.get("actor_id") == aid]


def sequence_layer(bouts: Sequence[Mapping[str, Any]], div: str) -> dict[str, Any]:
    """The path, not just the ending.

    ``path_to_victory`` and ``path_to_defeat`` are the transition distributions of the winner's
    and loser's OWN events, kept apart. Averaging the two would describe a bout nobody fought:
    the winner's chain and the loser's chain are different games happening at the same time.
    """
    mine = [b for b in bouts if (b["div_a"] == div or b["div_b"] == div) and b["seq"]]
    won: list[list[str]] = []
    lost: list[list[str]] = []
    type_by_outcome: dict[str, Counter[str]] = {"win": Counter(), "loss": Counter()}
    per_athlete: Counter[str] = Counter()
    nodes: Counter[str] = Counter()
    openers: dict[str, Counter[str]] = {"win": Counter(), "loss": Counter()}
    finishers: dict[str, Counter[str]] = {"win": Counter(), "loss": Counter()}

    for b in mine:
        for side in ("a", "b"):
            if (b["div_a"] if side == "a" else b["div_b"]) != div:
                continue
            if not b.get("winner"):
                continue
            outcome = "win" if b["winner"] == b[f"{side}_id"] else "loss"
            ev = _own(b, side)
            if not ev:
                continue
            per_athlete[b[f"{side}_id"]] += len(ev)
            chain: list[str] = [str(e["label"]) for e in ev if e.get("label")]
            for e in ev:
                nodes[e.get("label") or "?"] += 1
                type_by_outcome[outcome][e.get("type") or "?"] += 1
            if chain:
                openers[outcome][chain[0]] += 1
                finishers[outcome][chain[-1]] += 1
                (won if outcome == "win" else lost).append(chain)

    conc = shannon_concentration(list(per_athlete.values()))
    return {
        "bouts": len(mine),
        "events_own": sum(per_athlete.values()),
        "athletes_with_events": len(per_athlete),
        "concentration": conc,
        "path_to_victory": _path(won),
        "path_to_defeat": _path(lost),
        "type_by_outcome": {
            k: {t: est(v, sum(c.values())) for t, v in c.most_common()}
            for k, c in type_by_outcome.items()},
        "type_contrast": _type_contrast(type_by_outcome),
        "openers": {k: c.most_common(8) for k, c in openers.items()},
        "finishers": {k: c.most_common(8) for k, c in finishers.items()},
        "top_nodes": [[k, v, est(v, sum(nodes.values()))] for k, v in nodes.most_common(20)],
        "heatmap": _heatmap(won + lost),
    }


def _path(chains: Sequence[Sequence[str]]) -> dict[str, Any]:
    """The average path, stated as what it is: a transition distribution, not one route.

    There is no single 'average sequence' -- chains differ in length and content, and picking
    the modal one throws away everything else. What is reported instead is, for each node, the
    distribution of what came next, plus the most frequent contiguous pairs and triples. Those
    are countable, and each carries an interval.
    """
    out_deg: Counter[str] = Counter()
    trans: Counter[tuple[str, str]] = Counter()
    bigrams: Counter[tuple[str, str]] = Counter()
    trigrams: Counter[tuple[str, str, str]] = Counter()
    lens: list[int] = []
    for ch in chains:
        lens.append(len(ch))
        for x, y in zip(ch, ch[1:]):
            trans[(x, y)] += 1
            out_deg[x] += 1
            bigrams[(x, y)] += 1
        for x, y, z in zip(ch, ch[1:], ch[2:]):
            trigrams[(x, y, z)] += 1
    edges: list[dict[str, Any]] = [
        {"from": x, "to": y, "n": c, "of": out_deg[x], **est(c, out_deg[x])}
        for (x, y), c in trans.most_common(80)]
    return {
        "chains": len(chains),
        "mean_len": round(sum(lens) / len(lens), 2) if lens else None,
        "median_len": sorted(lens)[len(lens) // 2] if lens else None,
        "edges": edges,
        "bigrams": [[f"{x} → {y}", c] for (x, y), c in bigrams.most_common(12)],
        "trigrams": [[f"{x} → {y} → {z}", c] for (x, y, z), c in trigrams.most_common(10)],
    }


def _type_contrast(by: Mapping[str, Counter[str]]) -> list[dict[str, Any]]:
    """Which event types separate winning from losing, with multiplicity controlled."""
    w, loss = by["win"], by["loss"]
    nw, nl = sum(w.values()), sum(loss.values())
    rows: list[dict[str, Any]] = []
    for t in sorted(set(w) | set(loss)):
        c = compare_proportions(w[t], nw, loss[t], nl)
        rows.append({"type": t, "contrast": c.to_dict()})
    ps = [r["contrast"]["p_value"] for r in rows if r["contrast"]["p_value"] is not None]
    keep = benjamini_hochberg(ps, 0.05)
    it = iter(keep)
    for r in rows:
        r["survives_bh"] = next(it) if r["contrast"]["p_value"] is not None else False
    return rows


def _heatmap(chains: Sequence[Sequence[str]], top: int = 12) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for ch in chains:
        counts.update(ch)
    labels = [k for k, _ in counts.most_common(top)]
    idx = {k: i for i, k in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for ch in chains:
        for x, y in zip(ch, ch[1:]):
            if x in idx and y in idx:
                m[idx[x]][idx[y]] += 1
    return {"labels": labels, "matrix": m,
            "row_totals": [sum(r) for r in m], "grand": sum(sum(r) for r in m)}


# ── layer 3: graph embeddings and archetypes ────────────────────────────────────
def embedding_layer(conn: Any, roster: Mapping[str, str]) -> dict[str, Any]:
    """Shape of a game as a vector, and how far each athlete sits from the category's centre.

    **Every query filters `owner_kind='athlete'`.** The App's user graphs live in the same
    table under `owner_kind='user'`, and an unfiltered read here would fold a private,
    app-fed game into a public category centroid. That is the one thing this project's data
    rules forbid outright, and the filter is load-bearing rather than defensive.

    This is the thinnest of the three layers and is reported as such: `usable` counts the
    roster graphs that actually carry a vector, and `edge_concentration` says how much of the
    category's graph evidence comes from a single athlete.
    """
    rows = conn.execute(text("""
        select a.name, g.id::text, g.archetype_id::text,
               (g.embedding is not null) as has_emb,
               (select count(*) from graph_edges e where e.graph_id = g.id) as edges,
               g.embedding::text
          from graphs g join athletes a on a.id = g.owner_id
         where g.owner_kind = 'athlete'
    """)).fetchall()
    arche = {r[0]: {"name": r[1], "vec": _vec(r[2])} for r in conn.execute(text(
        "select id::text, name, embedding::text from archetypes")).fetchall()}

    mine = []
    for name, gid, arch, has, edges, emb in rows:
        div = roster.get(athlete_key(name))
        if not div:
            continue
        mine.append({"athlete": name, "division": div, "graph_id": gid,
                     "archetype_id": arch, "edges": int(edges),
                     "vec": _vec(emb) if has else None})

    per_div: dict[str, Any] = {}
    for div in ("65 kg", "+65 kg"):
        members = [m for m in mine if m["division"] == div]
        vecs = [m["vec"] for m in members if m["vec"]]
        centroid = _centroid(vecs)
        for m in members:
            m["to_centroid"] = round(_cos(m["vec"], centroid), 4) if m["vec"] and centroid else None
            m["archetypes"] = sorted(
                ({"id": aid, "name": a["name"], "cos": round(_cos(m["vec"], a["vec"]), 4)}
                 for aid, a in arche.items() if m["vec"] and a["vec"]),
                key=lambda x: -x["cos"])[:3] if m["vec"] else []
        sims = [m["to_centroid"] for m in members if m["to_centroid"] is not None]
        # Outlier rule, stated rather than tuned: more than one standard deviation BELOW the
        # mean similarity to the category centroid. With n this small it is a pointer for a
        # human to look, never a classification -- which is why `n` travels with the flag.
        mu = sum(sims) / len(sims) if sims else None
        sd = math.sqrt(sum((s - mu) ** 2 for s in sims) / len(sims)) if sims and len(sims) > 1 else None
        for m in members:
            m["outlier"] = bool(
                sd is not None and mu is not None and m["to_centroid"] is not None
                and m["to_centroid"] < mu - sd)
        conc = shannon_concentration([m["edges"] for m in members])
        per_div[div] = {
            "members": [{k: v for k, v in m.items() if k != "vec"} for m in members],
            "usable": len(vecs), "with_graph": len(members),
            "edge_concentration": conc,
            "outlier_rule": {"basis": "cosine to category centroid", "cut": "mean - 1 sd",
                             "mean": round(mu, 4) if mu else None,
                             "sd": round(sd, 4) if sd else None, "n": len(sims)},
            "centroid_neighbours": _centroid_neighbours(conn, centroid),
        }
    return {"divisions": per_div,
            "archetypes": [{"id": k, "name": v["name"]} for k, v in sorted(arche.items())],
            "note": "owner_kind='athlete' filtered explicitly; user graphs excluded by query"}


def _vec(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        return [float(x) for x in raw.strip("[]").split(",")]
    except ValueError:
        return None


def _centroid(vs: Sequence[Sequence[float]]) -> list[float] | None:
    if not vs:
        return None
    n = len(vs[0])
    return [sum(v[i] for v in vs) / len(vs) for i in range(n)]


def _cos(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _centroid_neighbours(conn: Any, centroid: Sequence[float] | None, k: int = 14) -> list[Any]:
    """The technique nodes nearest the category's mean game vector — the 'average node graph'
    the centroid implies, read out of the shared technique library rather than invented."""
    if not centroid:
        return []
    lit = "[" + ",".join(f"{x:.6f}" for x in centroid) + "]"
    rows = conn.execute(text("""
        select label, node_type, 1 - (embedding <=> cast(:v as vector)) as cos
          from technique_nodes where embedding is not null
         order by embedding <=> cast(:v as vector) limit :k
    """), {"v": lit, "k": k}).fetchall()
    return [[r[0], r[1], round(float(r[2]), 4)] for r in rows]


# ── layer 4: footage we hold ────────────────────────────────────────────────────
def video_layer(conn: Any, roster: Mapping[str, str]) -> list[dict[str, Any]]:
    """Every bout of these categories we can actually watch, local clip or published link."""
    out = []
    for d in sorted(FRAMES.iterdir()) if FRAMES.exists() else []:
        if not (d / "frames.jsonl").exists():
            continue
        readme = (d / "README.md")
        title = readme.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() \
            if readme.exists() else d.name
        events = 0
        if (d / "events.json").exists():
            try:
                events = len(json.loads((d / "events.json").read_text())["events"])
            except (json.JSONDecodeError, KeyError):
                events = -1
        out.append({"slug": d.name, "title": title, "source": "local clip",
                    "clip": (d / "clip.mp4").exists(),
                    "thumbs": sum(1 for _ in (d / "frames.jsonl").open()),
                    "registered_events": events})
    rows = conn.execute(text("""
        select a.name, b.name, m.event, m.year, m.video_url, m.video_start_seconds,
               jsonb_array_length(coalesce(m.sequence,'[]'::jsonb))
          from matches m join athletes a on a.id=m.athlete_a_id
                         join athletes b on b.id=m.athlete_b_id
         where m.status='final' and m.video_url is not null
    """)).fetchall()
    for na, nb, ev, yr, url, start, n in rows:
        if not (roster.get(athlete_key(na)) or roster.get(athlete_key(nb))):
            continue
        out.append({"slug": None, "title": f"{na} vs {nb}, {ev or '?'} {yr or ''}".strip(),
                    "source": "published link", "url": url, "start": start,
                    "events": int(n), "uniform": uniform(ev), "ruleset": ruleset(ev)})
    return out


# ── layer 5: the elite no-gi baseline ───────────────────────────────────────────
ELITE = ("adcc", "polaris", "wno", "who's number one", "cji", "quintet", "subversiv",
         "world no-gi", "world nogi", "no-gi worlds")


def baseline_layer(bouts: Sequence[Mapping[str, Any]], roster_ids: set[str],
                   per_div: Mapping[str, Any]) -> dict[str, Any]:
    """The category against elite no-gi at large.

    The roster is REMOVED from the baseline before comparing. Leaving it in compares a group
    against a pool that contains it, which drags the baseline toward the group and shrinks any
    real difference — an error that always flatters the null.
    """
    pool: Counter[str] = Counter()
    n_bouts = 0
    for b in bouts:
        if not any(k in _fold(b.get("event")) for k in ELITE):
            continue
        n_bouts += 1
        for e in b["seq"]:
            if e.get("actor_id") in roster_ids:
                continue
            pool[e.get("type") or "?"] += 1
    total = sum(pool.values())
    out: dict[str, Any] = {"bouts": n_bouts, "events": total,
                           "mix": {t: est(v, total) for t, v in pool.most_common()},
                           "roster_excluded": True}
    for div, seq in per_div.items():
        own: Counter[str] = Counter()
        for outcome in ("win", "loss"):
            for t, e in seq["type_by_outcome"][outcome].items():
                own[t] += e["k"]
        n_own = sum(own.values())
        rows2: list[dict[str, Any]] = []
        for t in sorted(set(own) | set(pool)):
            rows2.append({"type": t,
                          "contrast": compare_proportions(own[t], n_own, pool[t], total).to_dict()})
        ps2 = [r["contrast"]["p_value"] for r in rows2 if r["contrast"]["p_value"] is not None]
        it2 = iter(benjamini_hochberg(ps2, 0.05))
        for r in rows2:
            r["survives_bh"] = next(it2) if r["contrast"]["p_value"] is not None else False
        out[div] = {"n_own": n_own, "rows": rows2}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--records", type=Path, required=True,
                    help="BJJ Heroes records JSON (division + rows per athlete)")
    ap.add_argument("--sequences", type=Path, required=True, help="corpus bouts JSON")
    ap.add_argument("--derived", type=Path, required=True, help="opponent-derived records JSON")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    roster = {athlete_key(x if isinstance(x, str) else x["name"]): d["name"]
              for d in manifest["divisions"] for x in d["athletes"]}
    display = {athlete_key(x if isinstance(x, str) else x["name"]):
               (x if isinstance(x, str) else x["name"])
               for d in manifest["divisions"] for x in d["athletes"]}
    records = json.loads(a.records.read_text(encoding="utf-8"))
    bouts = json.loads(a.sequences.read_text(encoding="utf-8"))

    seq = {d: sequence_layer(bouts, d) for d in ("65 kg", "+65 kg")}
    roster_ids = {b[f"{s}_id"] for b in bouts for s in ("a", "b")
                  if (b["div_a"] if s == "a" else b["div_b"])}

    eng = get_engine()
    with eng.connect() as conn:
        emb = embedding_layer(conn, roster)
        vids = video_layer(conn, roster)
        corr = correlation_layer(conn, bouts)
        rating = rating_layer(conn, roster)

    doc = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "confidence": {"z": 1.959963984540054, "interval": "Wilson 95%",
                       "ratio_interval": "delta method on log RR",
                       "multiplicity": "Benjamini-Hochberg, alpha 0.05",
                       "grades": {"adequate": "half-width <= 0.08",
                                  "moderate": "<= 0.15", "low": "> 0.15",
                                  "insufficient": "n < 5"}},
        "method": method_layer(records),
        "sequence": seq,
        "embedding": emb,
        "baseline": baseline_layer(bouts, roster_ids, seq),
        "videos": vids,
        "correlations": corr,
        "rd": {**rating,
               "athletes": {display[k]: v for k, v in rating["athletes"].items()}},
        "derived": json.loads(a.derived.read_text(encoding="utf-8")),
        "roster": manifest["divisions"],
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8")
    kb = a.out.stat().st_size / 1024
    print(f"wrote {a.out} ({kb:.0f} KB)")
    for d, s in seq.items():
        print(f"  {d}: {s['bouts']} bouts, {s['events_own']} own events, "
              f"effective_n {s['concentration']['effective_n']:.2f}")
    print(f"  videos: {len(vids)}   embedded graphs: "
          f"{sum(v['usable'] for v in emb['divisions'].values())}")
    return 0




# ── layer 6: correlations ───────────────────────────────────────────────────────
def correlation_layer(conn: Any, bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The two correlations that are computable, and an explicit note on the two that are not.

    **Node-level ELO does not exist.** ``athlete_node_rating_states_v2`` is created by a
    migration and holds zero rows -- the constellation work that would fill it is still open
    (rating_v2 ADR-03/ADR-08). So "does a high rating ON A NODE predict a kind of victory"
    cannot be answered at all, and is reported as unavailable rather than approximated by
    something else wearing its name.

    **Running score does not exist either.** ``matches`` has no score column, and zero
    sequence events carry the ``points`` field. The only scores in reach are the final ones
    embedded in published method strings ("Pts: 4x2"), which exist ONLY for bouts decided on
    points -- the exact complement of what a score-versus-outcome question needs.

    What replaces them is stated as what it is: athlete-level rating instead of node-level,
    and share of observed events instead of score.
    """
    from analysis.rating_v2.config import SITE_RATING_RUN_ID
    from analysis.stats_rigor import auc, heterogeneity, spearman, wilson

    node_rows = conn.execute(text(
        "select count(*) from athlete_node_rating_states_v2")).scalar()

    # ── rating of the winner against how they won ───────────────────────────────
    rows = conn.execute(text("""
        select w.rating, w.rating_deviation, l.rating, m.win_type
          from matches m
          join athlete_rating_states_v2 w
            on w.athlete_id = m.winner_id and w.run_id = :run
          left join athlete_rating_states_v2 l
            on l.athlete_id = case when m.winner_id = m.athlete_a_id
                                   then m.athlete_b_id else m.athlete_a_id end
           and l.run_id = :run
         where m.status = 'final' and m.win_type is not null
           and w.rating_deviation <= 200
    """), {"run": SITE_RATING_RUN_ID}).fetchall()

    bands = [("<1700", 0, 1700), ("1700–1900", 1700, 1900),
             ("1900–2100", 1900, 2100), ("≥2100", 2100, 10000)]
    by_band, table = [], []
    for label, lo, hi in bands:
        sub = [r for r in rows if lo <= float(r[0]) < hi]
        sub_n = sum(1 for r in sub if r[3] == "SUBMISSION")
        other = len(sub) - sub_n
        if sub:
            by_band.append({"band": label, "n": len(sub),
                            "submission": wilson(sub_n, len(sub)).to_dict()})
            table.append([sub_n, other])
    ratings = [float(r[0]) for r in rows]
    is_sub = [r[3] == "SUBMISSION" for r in rows]
    rating_vs_sub = spearman(ratings, [1.0 if s else 0.0 for s in is_sub])

    gapped = [(float(r[0]) - float(r[2]), r[3] == "SUBMISSION") for r in rows if r[2] is not None]
    gap_sep = auc([g for g, _ in gapped], [s for _, s in gapped]) if gapped else None
    gap_corr = spearman([g for g, _ in gapped], [1.0 if s else 0.0 for _, s in gapped]) \
        if len(gapped) > 3 else None

    # ── event share against who won, for bouts NOT decided on points ────────────
    shares, labels = [], []
    for b in bouts:
        if not b["seq"] or not b.get("winner") or b.get("win_type") == "POINTS":
            continue
        a_ev = sum(1 for e in b["seq"] if e.get("actor_id") == b["a_id"])
        b_ev = sum(1 for e in b["seq"] if e.get("actor_id") == b["b_id"])
        if a_ev + b_ev < 4:
            continue
        shares.append(a_ev / (a_ev + b_ev))
        labels.append(b["winner"] == b["a_id"])
    share_sep = auc(shares, labels) if shares else None

    return {
        "node_elo": {
            "available": bool(node_rows),
            "rows": int(node_rows or 0),
            "why": "athlete_node_rating_states_v2 exists but holds no rows; the constellation "
                   "work that fills it is open (rating_v2 ADR-03/ADR-08). Node-level rating "
                   "against method of victory is therefore not computable, and no substitute "
                   "is presented under its name.",
        },
        "rating_vs_method": {
            "substitute_for": "node-level ELO",
            "bands": by_band,
            "heterogeneity": heterogeneity(table).to_dict() if len(table) > 1 else None,
            "spearman": rating_vs_sub.to_dict(),
            "gap_auc": gap_sep.to_dict() if gap_sep else None,
            "gap_spearman": gap_corr.to_dict() if gap_corr else None,
            "n": len(rows),
            "caveats": [
                "Conditioned on winning: every row is a bout someone won, so this describes "
                "HOW winners win, not who wins.",
                "Athlete-level rating, not node-level — the question asked was about a node.",
                "Opponent strength is only partly controlled: the rating gap is reported "
                "separately, and a bout whose loser is unrated drops out of that cut.",
                "Corpus-wide, not roster-only: restricting to these 16 athletes leaves too "
                "few rated bouts to band at all.",
            ],
        },
        "score_vs_outcome": {
            "available": False,
            "why": "matches has no score column and zero sequence events carry `points`. The "
                   "only scores that exist are final ones inside published method strings, and "
                   "they exist only for bouts decided ON points — the complement of the "
                   "question. What is shown instead is share of observed events, which is a "
                   "proxy for positional volume, not a score.",
            "event_share_auc": share_sep.to_dict() if share_sep else None,
            "n_bouts": len(shares),
        },
    }


if __name__ == "__main__":
    sys.exit(main())
