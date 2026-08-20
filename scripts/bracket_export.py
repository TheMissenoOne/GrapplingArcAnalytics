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

    uv run python -m scripts.bracket_export

Inputs and output all default to their tracked locations, so the published report rebuilds from
a clean checkout with no arguments. Needs DATABASE_URL for the embedding, video, correlation,
rating and radar layers; the method layer is pure Python over the records file.
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

from analysis.names import (  # noqa: E402
    athlete_key,
    reviewed_verdict,
    roster_aliases,
)
from analysis.stats_rigor import (  # noqa: E402
    MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE,
    MIN_EFFECTIVE_N_FOR_CATEGORY_ESTIMATE,
    MIN_N_FOR_ANY_GRADE,
    Coverage,
    benjamini_hochberg,
    compare_proportions,
    coverage,
    heterogeneity,
    inverse_hhi_concentration,
    survives,
    wilson,
)
from db.base import get_engine  # noqa: E402

SCOUTING = REPO / "data" / "scouting"
MANIFEST = SCOUTING / "adcc_2026_women.json"
# The three inputs, tracked beside the manifest. They lived in a scratch directory for a while,
# which meant the published data.json was not reproducible from this repo at all -- delete the
# scratch and the report could never be rebuilt. They are defaults rather than a line in the
# docstring because a default runs and a docstring drifts.
RECORDS = SCOUTING / "adcc_2026_women_records.json"
SEQUENCES = SCOUTING / "adcc_2026_women_sequences.json"
LINKS = REPO / "data" / "frame_pdf" / "women_65_links.json"
FRAMES = REPO / "data" / "frame_pdf" / "out"

# ── vocabulary ──────────────────────────────────────────────────────────────────
LEG = r"heel hook|toe hold|kneebar|knee bar|ankle|foot lock|footlock|aoki|estima|calf|leg lock|50/50"
STRANGLE = (r"rnc|choke|strangle|guillotine|triangle|katagatame|ezekiel|bow and arrow|darce|"
            r"d'arce|anaconda|brabo|clock|loop|baseball|north.?south|von flue")
JOINT = r"armbar|arm bar|kimura|americana|omoplata|wrist ?lock|choi bar|monoplata|arm ?lock"
SLICER = r"slicer|twister|crucifix"
POINTS = r"^pts|^points|advantage|^adv|^\d+x\d+"
DECISION = r"referee decision|decision|judge"
VOID = r"^dq|disqualif|injur|withdraw|n/a|^-+$|walkover|forfeit"
DIVISIONS = ("65 kg", "+65 kg")
# Nested recency windows, newest last. Fixed years rather than "N years back from today" so a
# rebuild in six months does not silently re-slice the report.
# Similarity floor for surfacing a spelling pair. Editorial: high enough that unrelated
# names do not crowd the panel, low enough to catch a nickname ("Mo" for "Morgan"), which
# lands almost exactly on it.
MIN_DUPLICATE_RATIO = 0.80
RECENCY_WINDOWS = (2019, 2022, 2024)
# What the page opens on. Recent no-gi is the question the bracket asks; the full career is a
# secondary view, one selection away.
OPENING_CUT = "no_gi+since:2024"
SUB_FAMILIES = ("strangle", "joint", "leg", "slicer", "sub_other")
# An SD-based outlier rule needs enough members for the SD to mean anything. Stated here so it
# is arguable rather than buried in the layer that applies it.
MIN_USABLE_FOR_OUTLIER = 6
FAMILIES = ("points", "decision", *SUB_FAMILIES, "void")

# A compound name is decided by whichever family regex is tested first, which gets these wrong:
# "calf slicer" is a slicer that LEG claims through `calf`, and "triangle armlock" is a joint
# lock that STRANGLE claims through `triangle`. Left to the fallback, the `slicer` family is
# unreachable — it scored k=0 in both divisions not because nobody hits one but because no
# string can ever reach the branch. Entries here win over the regexes; the regexes stay for
# labels this table has not seen. A corpus string that matches two family regexes and is NOT
# listed here is a test failure, not a silent coin flip.
METHOD_FAMILY: dict[str, str] = {
    "calf slicer": "slicer",
    "triangle armlock": "joint",
    "triangle armbar": "joint",
}


def _vocab(*tokens: str) -> re.Pattern[str]:
    """One alternation for a vocabulary of event-name fragments.

    Each token is anchored at its START on a word boundary and deliberately left open at the
    end, because several are prefixes: "grappling ind" for Grappling Industries, "world champ"
    for World Championship, "sacramento o" for Sacramento Open. The leading anchor is the whole
    point — without it `open` fires inside "Copenhagen" and `gi ` fires inside "nogi ", the
    second being harmless today only because NO_GI happens to be tested before GI.
    """
    return re.compile("|".join(
        (r"\b" if t[:1].isalnum() else "") + re.escape(t) for t in tokens))


# Uniform. Gi and no-gi are different sports for this purpose: the grips, the pace and which
# submissions are even available all change, so pooling them produces an average of two
# distributions that describes neither.
NO_GI = _vocab("no-gi", "no gi", "nogi", "adcc", "polaris", "wno", "who's number one", "cji",
               "quintet", "subversiv", "f2w", "fight to win", "grapplefest", "adxc", "ufc fpi",
               "ufc fp", "pgf", "sub", "queen of mats", "emerald city", "main character",
               "spokane subs", "pit series", "grappling ind",
               # BJJ Heroes abbreviations that spell the uniform out: NGO/NGN/NNG/NGGP are
               # No-Gi Open / Nationals / Grand Prix. "Sacramento WO" sitting beside
               # "Sacramento NGO" in the same record is what makes this a reading of the
               # source's own convention rather than a guess about the event.
               "ng", "nng", "sngo", "fngo", "mcharacter")
# `"gi "` used to sit in here. It classified nothing correctly -- every one of the 38 bouts it
# matched across the records and all 110 corpus events was a NO-GI event ("nogi worlds",
# "no gi pan am."), reached only because NO_GI happens to be tested first. A token whose every
# hit is a false positive it never gets blamed for is a trap, not a rule.
GI = _vocab("world champ", "pan american", "european champ", "brasileiro", "world pro", "copa",
            "ibjjf gi", "jiu-jitsu champ", "san jose open", "sacramento o", "bjj stars",
            "adgs", "grand slam", "ajp",
            # An IBJJF "Open" is the gi bracket. The no-gi one is named No-Gi Open (NGO), and
            # NO_GI is tested first, so it claims that one before this line is reached.
            "open")

# Rulesets. Sourced from data/scouting/rulesets.json, which records decision_model per family.
# They differ in exactly the way that changes how a bout ends: ADCC has a negative-points window
# then overtime, IBJJF scores throughout with advantages, AJP scores on its own table with
# advantages and runs its events in the gi, and superfights are commonly submission-only with a
# judges' card as the fallback.
#
# `ajp` is listed FIRST because `ruleset` returns on the first family that matches and dicts
# iterate in insertion order: Abu Dhabi Grand Slam, ADGS and World Pro are AJP/UAEJJF events, and
# filing them under `adcc` put 95 bouts — every one of them in +65 kg — under the rules of a
# different organisation. Half of what the page called "no-gi under ADCC rules" was gi.
RULESETS = {
    "ajp": _vocab("adgs", "grand slam", "world pro", "ajp", "uaejj"),
    "adcc": _vocab("adcc"),
    "ibjjf": _vocab("world champ", "pan american", "european", "no-gi worlds", "world nogi",
                    "world no-gi", "nogi pan", "pan nogi", "no gi pan", "brasileiro", "open",
                    "jiu-jitsu champ", "world champ."),
    "superfight": _vocab("polaris", "wno", "who's number one", "cji", "f2w", "fight to win",
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
    if s in METHOD_FAMILY:
        return METHOD_FAMILY[s]
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
    if NO_GI.search(s):
        return "no_gi"
    if GI.search(s):
        return "gi"
    return "unknown"


def ruleset(event: str | None) -> str:
    s = _fold(event)
    for name, pat in RULESETS.items():
        if pat.search(s):
            return name
    return "other"


def est(k: int, n: int) -> dict[str, Any]:
    return wilson(k, n).to_dict()


def gated(k: int, n: int, cov: Coverage) -> dict[str, Any]:
    """A category estimate, or an explicit refusal to make one.

    The observed proportion survives either way -- ``31% of the events we recorded were
    control`` is a fact about the corpus and stays true. What the gate withholds is the
    INTERVAL, because an interval is a claim about a population, and below the coverage gate
    there is no population being sampled: there is one athlete being described.

    `reason` is what the page renders in place of the bar, so the reader learns why the number
    stopped rather than finding a blank.
    """
    d = est(k, n)
    if cov.estimable:
        return {**d, "estimable": True, "coverage": cov.grade}
    return {**d, "lo": None, "hi": None, "half": None, "grade": "none",
            "estimable": False, "coverage": cov.grade, "reason": cov.reason,
            "reason_code": cov.reason_code, "clusters": cov.clusters,
            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE,
            "top_share": round(cov.top_share, 3),
            "effective_n": round(cov.effective_n, 3)}


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
            {**r, "athlete": nm, "uniform": uniform(r["comp"]), "ruleset": ruleset(r["comp"]),
             "family": family(r["method"])}
            for nm, v in records.items() if v["division"] == div
            for r in (v.get("rows") or [])
        ]
        cuts: dict[str, Any] = {}
        for cut_name, subset in _cuts(rows):  # noqa: B007
            w = [r for r in subset if r["wl"] == "W"]
            loss = [r for r in subset if r["wl"] == "L"]
            # A bout that is neither. One draw exists (Helena Crevar x Adele Fornarino, CJI 2
            # 2025, method "---"), and it used to vanish from both columns while still counting
            # toward `n`, which is why wins + losses did not add up to the cut size.
            undecided = [r for r in subset if r["wl"] not in ("W", "L")]
            fw, fl = Counter(r["family"] for r in w), Counter(r["family"] for r in loss)
            subs_w = sum(fw[f] for f in SUB_FAMILIES)
            subs_l = sum(fl[f] for f in SUB_FAMILIES)
            cov_w = coverage(list(Counter(r["athlete"] for r in w).values()))
            cov_l = coverage(list(Counter(r["athlete"] for r in loss).values()))
            bouts = _distinct_bouts(subset)
            # How a BOUT ended, each counted once, whoever won. This is the question the panel
            # asks by its name, and it was the one distribution missing: a bout ends exactly
            # one way, so splitting by outcome answers something else.
            fb = Counter(r["family"] for r in bouts)
            cov_b = coverage(list(Counter(r["athlete"] for r in bouts).values()))
            cuts[cut_name] = {
                "n": len(subset), "w": len(w), "l": len(loss), "undecided": len(undecided),
                "by_bout": {f: gated(fb[f], len(bouts), cov_b) for f in FAMILIES},
                # The headline of the per-bout view: what share of bouts ended in a submission
                # at all. Belongs here rather than only beside the roster's own record, where
                # it silently became "how often these athletes finish".
                "bout_finish_rate": gated(sum(fb[f] for f in SUB_FAMILIES), len(bouts), cov_b),
                "bout_coverage": cov_b.to_dict(),
                # The win and loss tables are NOT two views of one set of bouts. A bout with a
                # rostered athlete on both sides would appear in both, and only this many do;
                # for every other bout exactly one side is on the roster, so the two tables are
                # drawn from disjoint bouts and describe this roster's offence and its defence
                # rather than one distribution seen twice.
                "sides_share_bouts": len(subset) - len(bouts),
                # `n` counts athlete-perspective ROWS. A bout between two rostered athletes
                # produces two of them, one W and one L, and there are 39 such rows across the
                # roster. `bouts` is the count of distinct events that actually happened.
                "bouts": len(bouts), "rows_from_shared_bouts": len(subset) - len(bouts),
                "coverage": {"win": cov_w.to_dict(), "loss": cov_l.to_dict()},
                "win_by": {f: gated(fw[f], len(w), cov_w) for f in FAMILIES},
                "loss_by": {f: gated(fl[f], len(loss), cov_l) for f in FAMILIES},
                "finish_rate": gated(subs_w, len(w), cov_w),
                "conceded_finish_rate": gated(subs_l, len(loss), cov_l),
                "leg_contrast": _leg_contrast(w, loss, fw, fl, cov_w, cov_l, subset, bouts),
                "named_applied": Counter(_fold(r["method"]) for r in w
                                         if r["family"] in SUB_FAMILIES).most_common(14),
                "named_conceded": Counter(_fold(r["method"]) for r in loss
                                          if r["family"] in SUB_FAMILIES).most_common(14),
            }
        # Where each athlete's record came from. Eight of the sixteen have no record of their
        # own -- seven are absent from BJJ Heroes' A-Z list entirely and one has a profile with
        # no match table -- so theirs is reconstructed from the pages of athletes they fought
        # and from bouts already in the corpus. That reconstruction is biased in a knowable
        # direction: it can only contain opponents notable enough to have their own page, so it
        # over-represents strong opposition. Publishing the mix beats implying every record was
        # gathered the same way.
        provenance = {
            nm: {"division": v.get("division"),
                 "record_source": v.get("record_source", "own" if v.get("rows") else "none"),
                 "rows": len(v.get("rows") or []),
                 "by_source": dict(Counter(r.get("source", "own_record")
                                           for r in (v.get("rows") or []))),
                 # A record can be missing a whole CLASS of results, not just rows. Helena
                 # Crevar's carries 48 bouts of which 45 are no-gi and ZERO are IBJJF gi: her
                 # gi career is absent wholesale. Any gi/no-gi cut inherits that, so the
                 # per-athlete split ships alongside the classification it feeds.
                 "by_uniform": dict(Counter(uniform(r.get("comp"))
                                            for r in (v.get("rows") or [])))}
            for nm, v in records.items() if v.get("division") == div
        }
        out[div] = {"record_provenance": provenance,
                    "cuts": cuts,
                    "uniform_split": Counter(r["uniform"] for r in rows),
                    "ruleset_split": Counter(r["ruleset"] for r in rows),
                    "by_year": _year_series(rows),
                    "recency": _recency(rows),
                    "opening_cut": OPENING_CUT,
                    "ruleset_test": _ruleset_test(rows),
                    # Only possible now that AJP is its own family. Filing it under ADCC made
                    # the gi comparison meaningless: the two organisations score differently
                    # and the whole question is whether that changes how bouts end.
                    "ruleset_test_gi": _ruleset_test(rows, "gi", ("ajp", "ibjjf")),
                    "uniform_test": _uniform_test(rows)}
    return out


def _bout_key(r: Mapping[str, Any]) -> tuple[Any, ...]:
    """Identity of the EVENT, not of one athlete's view of it."""
    pair = tuple(sorted((athlete_key(r.get("athlete") or ""), athlete_key(r.get("opp") or ""))))
    return (pair, _fold(r.get("comp")), r.get("year"), _fold(r.get("stage")))


def _is_mirror(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Are these two rows the same bout, seen from opposite corners?

    Provable, not guessed: each names the other as the opponent, and one won while the other
    lost. Nothing weaker will do -- a shared key alone is not evidence of a duplicate, because
    the same two athletes routinely meet twice at one event.
    """
    return (athlete_key(a.get("athlete") or "") == athlete_key(b.get("opp") or "")
            and athlete_key(b.get("athlete") or "") == athlete_key(a.get("opp") or "")
            and {a.get("wl"), b.get("wl")} == {"W", "L"})


def _distinct_bouts(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Collapse the mirror pair, and ONLY the mirror pair.

    The records are per athlete, so a bout between two rostered athletes appears twice -- once
    as a W and once as a L. Both views are legitimate for the descriptive `win_by`/`loss_by`
    tables, which have separate denominators. They are NOT two pieces of evidence about how
    bouts end, which is what the contrasts and the ruleset tests ask, and the pair is
    anti-correlated by construction: it deflates the variance of exactly the comparison whose
    n it inflates.

    A first version of this keyed on (pair, competition, year) and collapsed anything that
    matched. On the real records that merged 25 pairs in +65 kg that were not mirrors at all --
    Yara Soares beat Isabely Lemos on points in the semi-final and by submission in the final
    of the same event, and round-robin formats produce two legitimate meetings by design.
    Rematches are bouts. Only a mirror is a duplicate.
    """
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[_bout_key(r)].append(r)

    out: list[Mapping[str, Any]] = []
    for group in groups.values():
        paired: set[int] = set()
        for i, r in enumerate(group):
            if i in paired:
                continue
            keep = r
            for j in range(i + 1, len(group)):
                if j not in paired and _is_mirror(r, group[j]):
                    paired.add(j)
                    keep = r if r.get("wl") == "W" else group[j]
                    break
            out.append(keep)
    return out


def _leg_contrast(w: Sequence[Mapping[str, Any]], loss: Sequence[Mapping[str, Any]],
                  fw: Counter[str], fl: Counter[str], cov_w: Coverage, cov_l: Coverage,
                  subset: Sequence[Mapping[str, Any]],
                  bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Leg locks conceded against leg locks applied, refused when it cannot be earned."""
    shared = len(subset) - len(bouts)
    blocked = []
    for side, c in (("win", cov_w), ("loss", cov_l)):
        if not c.estimable:
            blocked.append({"side": side, "code": c.reason_code, "clusters": c.clusters,
                            "effective_n": round(c.effective_n, 3),
                            "top_share": round(c.top_share, 3),
                            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE})
    if blocked:
        return {"available": False, "blocked": blocked,
                "observed": {"applied": fw["leg"], "of_wins": len(w),
                             "conceded": fl["leg"], "of_losses": len(loss)}}
    out = compare_proportions(fl["leg"], len(loss), fw["leg"], len(w)).to_dict()
    # The two arms are not disjoint when a rostered athlete beat another: that bout sits on
    # both sides at once, so the comparison is partly against itself.
    out["shared_bouts_between_arms"] = shared
    return out


def _cuts(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    cuts = [("all", list(rows))]
    for u in ("no_gi", "gi", "unknown"):
        cuts.append((f"uniform:{u}", [r for r in rows if r["uniform"] == u]))
    for rs in ("adcc", "ajp", "ibjjf", "superfight", "other"):
        cuts.append((f"ruleset:{rs}", [r for r in rows if r["ruleset"] == rs]))
    # the cut that actually matters: ruleset WITHIN no-gi, so uniform is not a confounder
    for rs in ("adcc", "ibjjf", "superfight"):
        cuts.append((f"no_gi+{rs}",
                     [r for r in rows if r["uniform"] == "no_gi" and r["ruleset"] == rs]))
    # Recency. The +65 kg records reach back to 2008, and a gi bout from then carries the same
    # weight as a 2025 no-gi one in any career-wide number -- which describes a career, not the
    # bracket. The page opens on `no_gi+since:2024` for that reason.
    for y0 in RECENCY_WINDOWS:
        cuts.append((f"since:{y0}", [r for r in rows if (r.get("year") or 0) >= y0]))
        cuts.append((f"no_gi+since:{y0}",
                     [r for r in rows if r["uniform"] == "no_gi" and (r.get("year") or 0) >= y0]))
    return [(k, v) for k, v in cuts if v]


def _recency(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Does the finding survive when only recent evidence is used?

    One statistic -- the finish rate in no-gi -- across four nested windows. Surviving all four
    is far stronger evidence than any single interval, and failing to survive is the finding:
    it says the career number is describing an era the bracket is not in.
    """
    bouts = _distinct_bouts(rows)
    out: list[dict[str, Any]] = []
    for label, y0 in (("carreira", None), *((f"{y}+", y) for y in RECENCY_WINDOWS)):
        sub = [r for r in bouts if r["uniform"] == "no_gi"
               and (y0 is None or (r.get("year") or 0) >= y0)]
        w = [r for r in sub if r["wl"] == "W"]
        cov = coverage(list(Counter(r["athlete"] for r in w).values()))
        subs = sum(1 for r in w if r["family"] in SUB_FAMILIES)
        out.append({"window": label, "from_year": y0, "bouts": len(sub), "wins": len(w),
                    "finish_rate": gated(subs, len(w), cov), "coverage": cov.to_dict()})

    # "Stable" means every window that produced an interval overlaps every other one. Windows
    # the gate refused cannot agree or disagree, so they are counted, not judged.
    bands = [(e["finish_rate"]["lo"], e["finish_rate"]["hi"]) for e in out
             if e["finish_rate"].get("estimable")]
    stable = all(a[0] <= b[1] and b[0] <= a[1] for a in bands for b in bands)
    return {"basis": "no-gi, lutas distintas, taxa de finalização entre as vitórias",
            "windows": out, "estimable_windows": len(bands),
            "stable": stable if len(bands) > 1 else None}


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


def _ruleset_test(rows: Sequence[Mapping[str, Any]], uniform_cut: str = "no_gi",
                  groups: Sequence[str] = ("adcc", "ibjjf", "superfight")) -> dict[str, Any]:
    """Does the way a bout ends change with the ruleset?

    Held within ONE uniform so gi/no-gi is not doing the work the ruleset is being credited
    with, and run over distinct bouts rather than athlete-perspective rows: a bout between two
    rostered athletes would otherwise enter the table twice, once from each corner.

    Reported with the chi-square's own reliability flag, because these tables are small enough
    that the approximation fails regularly and saying so is cheaper than pretending.
    """
    ng = [r for r in _distinct_bouts(rows) if r["uniform"] == uniform_cut]
    groups = list(groups)
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
    qs = benjamini_hochberg(ps)
    it = iter(zip(qs, survives(qs), strict=True))
    for pr in pairs:
        if pr["contrast"]["p_value"] is None:
            pr["q_value"], pr["survives_bh"] = None, False
            continue
        pr["q_value"], pr["survives_bh"] = next(it)
    return {"uniform": uniform_cut, "groups": labels, "pairs": pairs,
            "family": f"{uniform_cut} ruleset finish-rate pairs", "m": len(ps), "alpha": 0.05,
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
    bout_events = own_events = 0          # for the perspective block below
    gaps: Counter[int] = Counter()
    won: list[list[str]] = []
    lost: list[list[str]] = []
    type_by_outcome: dict[str, Counter[str]] = {"win": Counter(), "loss": Counter()}
    per_athlete: Counter[str] = Counter()
    # Split by outcome as well as pooled: the win side and the loss side of a division are not
    # backed by the same athletes, and in +65 kg the entire loss side is one event from one
    # bout-side. A single pooled coverage number would have hidden that behind the win side.
    per_athlete_outcome: dict[str, Counter[str]] = {"win": Counter(), "loss": Counter()}
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
            per_athlete_outcome[outcome][b[f"{side}_id"]] += len(ev)
            # How much of the bout is hers, and how much the chain below jumps over. A
            # transition A -> B means "her next OWN event after A was B", not that nothing
            # happened in between -- and in one of these divisions a third of the arrows skip
            # at least one opponent event.
            aid = b[f"{side}_id"]
            idx = [i for i, e in enumerate(b["seq"]) if e.get("actor_id") == aid]
            bout_events += len(b["seq"])
            own_events += len(idx)
            for i, j in zip(idx, idx[1:]):
                gaps[j - i - 1] += 1
            chain: list[str] = [str(e["label"]) for e in ev if e.get("label")]
            for e in ev:
                nodes[e.get("label") or "?"] += 1
                type_by_outcome[outcome][e.get("type") or "?"] += 1
            if chain:
                openers[outcome][chain[0]] += 1
                finishers[outcome][chain[-1]] += 1
                (won if outcome == "win" else lost).append(chain)

    conc = inverse_hhi_concentration(list(per_athlete.values()))
    cov_all = coverage(list(per_athlete.values()))
    cov = {k: coverage(list(c.values())) for k, c in per_athlete_outcome.items()}
    return {
        "bouts": len(mine),
        "events_own": sum(per_athlete.values()),
        "athletes_with_events": len(per_athlete),
        "concentration": conc,
        "coverage": {**{k: v.to_dict() for k, v in cov.items()}, "all": cov_all.to_dict()},
        # WHOSE events these are. Every node, chain and transition in this layer is filtered to
        # events where the rostered athlete is the ACTOR -- and `actor` here means the fighter
        # whose game the node belongs to, not whoever is ahead: a guard node belongs to the
        # player on the bottom, the pass to the one passing. Naming a node without naming its
        # actor is the highest-cost ambiguity available in this data, because "back control"
        # read from the wrong corner inverts the whole reading.
        "perspective": {
            "actor": "roster_athlete",
            "own_events": own_events,
            "bout_events": bout_events,
            "own_share": round(own_events / bout_events, 4) if bout_events else None,
            "transitions": sum(gaps.values()),
            "transitions_adjacent": gaps.get(0, 0),
            "opponent_events_skipped": sum(g * c for g, c in gaps.items()),
        },
        "path_to_victory": _path(won),
        "path_to_defeat": _path(lost),
        "type_by_outcome": {
            k: {t: gated(v, sum(c.values()), cov[k]) for t, v in c.most_common()}
            for k, c in type_by_outcome.items()},
        "type_contrast": _type_contrast(type_by_outcome, cov),
        "openers": {k: c.most_common(8) for k, c in openers.items()},
        "finishers": {k: c.most_common(8) for k, c in finishers.items()},
        "top_nodes": [[k, v, gated(v, sum(nodes.values()), cov_all)]
                      for k, v in nodes.most_common(20)],
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
        # `"n": c` used to sit here and was silently overwritten by est()'s own `n`, which is
        # the denominator. The raw count survived only as `k`, and the page's branch sort --
        # `sort((a, b) => b.n - a.n)` -- was therefore comparing a value identical across every
        # sibling edge, leaving the tree unordered.
        {"from": x, "to": y, "of": out_deg[x], **est(c, out_deg[x])}
        for (x, y), c in trans.most_common(80)]
    return {
        "chains": len(chains),
        "mean_len": round(sum(lens) / len(lens), 2) if lens else None,
        "median_len": sorted(lens)[len(lens) // 2] if lens else None,
        "edges": edges,
        "bigrams": [[f"{x} → {y}", c] for (x, y), c in bigrams.most_common(12)],
        "trigrams": [[f"{x} → {y} → {z}", c] for (x, y, z), c in trigrams.most_common(10)],
    }


def _type_contrast(by: Mapping[str, Counter[str]],
                   cov: Mapping[str, Coverage]) -> dict[str, Any]:
    """Which event types separate winning from losing, with multiplicity controlled.

    A contrast needs BOTH sides to be estimable, so this refuses as a block rather than row by
    row. In +65 kg the loss side is a single event from a single bout-side, and comparing a
    171-event win side against it produced a table of confident-looking rows built on n=1.
    """
    w, loss = by["win"], by["loss"]
    nw, nl = sum(w.values()), sum(loss.values())
    blocked = [k for k in ("win", "loss") if not cov[k].estimable]
    if blocked:
        return {"available": False,
                # Structured, not a sentence: the page renders in Portuguese and cannot
                # translate prose it was handed.
                "blocked": [{"side": k, "code": cov[k].reason_code,
                             "clusters": cov[k].clusters,
                             "effective_n": round(cov[k].effective_n, 3),
                             "top_share": round(cov[k].top_share, 3),
                             "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE}
                            for k in blocked],
                "observed": [{"type": t, "win": w[t], "loss": loss[t]}
                             for t in sorted(set(w) | set(loss))],
                "n_win": nw, "n_loss": nl}

    rows: list[dict[str, Any]] = []
    for t in sorted(set(w) | set(loss)):
        c = compare_proportions(w[t], nw, loss[t], nl)
        rows.append({"type": t, "contrast": c.to_dict()})
    ps = [r["contrast"]["p_value"] for r in rows if r["contrast"]["p_value"] is not None]
    qs = benjamini_hochberg(ps)
    it = iter(zip(qs, survives(qs), strict=True))
    for r in rows:
        if r["contrast"]["p_value"] is None:
            r["q_value"], r["survives_bh"] = None, False
            continue
        r["q_value"], r["survives_bh"] = next(it)
    return {"available": True, "rows": rows, "family": "sequence type contrast", "m": len(ps),
            "alpha": 0.05, "n_win": nw, "n_loss": nl}


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
        sd = (math.sqrt(sum((s - mu) ** 2 for s in sims) / (len(sims) - 1))
              if sims and mu is not None and len(sims) > 1 else None)
        # Scored against a centroid that EXCLUDES the athlete being scored. With three usable
        # graphs, a member is a third of the mean she is being measured against, which pulls
        # the centroid toward her and hides exactly the athlete the rule is looking for.
        for m in members:
            loo = _centroid([o["vec"] for o in members if o is not m and o.get("vec")])
            m["to_centroid_loo"] = (round(_cos(m["vec"], loo), 4)
                                    if m["vec"] and loo else None)
        loo_sims = [m["to_centroid_loo"] for m in members if m["to_centroid_loo"] is not None]
        loo_mu = sum(loo_sims) / len(loo_sims) if loo_sims else None
        loo_sd = (math.sqrt(sum((x - loo_mu) ** 2 for x in loo_sims) / (len(loo_sims) - 1))
                  if loo_sims and loo_mu is not None and len(loo_sims) > 1 else None)
        # Below this many usable vectors the mean and the spread are estimated from so few
        # numbers that a "mean - 1 sd" cut is decoration. The field is null rather than false:
        # false would read as "checked, and she is not one".
        enough = len(vecs) >= MIN_USABLE_FOR_OUTLIER
        for m in members:
            m["outlier"] = bool(
                enough and loo_sd is not None and loo_mu is not None
                and m["to_centroid_loo"] is not None
                and m["to_centroid_loo"] < loo_mu - loo_sd) if enough else None
        conc = inverse_hhi_concentration([m["edges"] for m in members])
        per_div[div] = {
            "members": [{k: v for k, v in m.items() if k != "vec"} for m in members],
            "usable": len(vecs), "with_graph": len(members),
            "edge_concentration": conc,
            "outlier_rule": {"basis": "cosine to leave-one-out category centroid",
                             "cut": "mean - 1 sd", "min_usable": MIN_USABLE_FOR_OUTLIER,
                             "applied": enough,
                             "why_not": None if enough else
                             f"{len(vecs)} usable vector(s); the rule needs "
                             f"{MIN_USABLE_FOR_OUTLIER}",
                             "mean": round(loo_mu, 4) if loo_mu else None,
                             "sd": round(loo_sd, 4) if loo_sd else None, "n": len(loo_sims),
                             "pooled_mean": round(mu, 4) if mu else None,
                             "pooled_sd": round(sd, 4) if sd else None},
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
    links = json.loads(LINKS.read_text(encoding="utf-8"))["bouts"] if LINKS.exists() else {}
    out = []
    for d in sorted(FRAMES.iterdir()) if FRAMES.exists() else []:
        if not (d / "frames.jsonl").exists():
            continue
        readme = (d / "README.md")
        title = readme.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() \
            if readme.exists() else d.name
        # `events.json` is written only by a human at the registrar. Its absence is the honest
        # state, not a pending job: no machine pass fills it, so "not registered" always means
        # nobody has read this bout yet.
        events = 0
        if (d / "events.json").exists():
            try:
                events = len(json.loads((d / "events.json").read_text())["events"])
            except (json.JSONDecodeError, KeyError):
                events = -1
        out.append({"slug": d.name, "title": title, "source": "local clip",
                    "clip": (d / "clip.mp4").exists(),
                    "registered_events": events,
                    "registered": events > 0,
                    "links": links.get(d.name, [])})
    rows = conn.execute(text("""
        select a.name, b.name, m.event, m.year, m.video_url, m.video_start_seconds,
               jsonb_array_length(coalesce(m.sequence,'[]'::jsonb))
          from matches m join athletes a on a.id=m.athlete_a_id
                         join athletes b on b.id=m.athlete_b_id
         where m.status='final' and m.video_url is not null
    """)).fetchall()
    # A bout can be BOTH a local clip and a corpus row -- five of these were downloaded even
    # though the corpus already held them. Listing it twice inflates the count and makes the
    # same fight look like two, so the corpus row is folded into the local one and its
    # sequence count reported there: "we hold the footage AND it already has events".
    def _names(text_: str) -> set[str]:
        folded = _fold(text_)
        return {w for w in re.sub(r"[^a-z ]", " ", folded).split() if len(w) > 2}

    local_names = [(o, _names(o["title"])) for o in out]
    for na, nb, ev, yr, url, start, n in rows:
        if not (roster.get(athlete_key(na)) or roster.get(athlete_key(nb))):
            continue
        title = f"{na} vs {nb}, {ev or '?'} {yr or ''}".strip()
        mine = _names(title)
        hit = next((o for o, names in local_names if len(mine & names) >= 4), None)
        if hit is not None:
            hit["corpus_events"] = int(n)
            hit.setdefault("links", []).append(
                {"host": "YouTube · corpus", "url": url}) if url and not any(
                    l.get("url") == url for l in hit.get("links", [])) else None
            continue
        out.append({"slug": None, "title": title,
                    "source": "published link", "url": url, "start": start,
                    "events": int(n), "uniform": uniform(ev), "ruleset": ruleset(ev),
                    "host": "YouTube" if "youtube" in (url or "") else "link publicado"})
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
    per_athlete: Counter[str] = Counter()
    n_bouts = 0
    for b in bouts:
        if not any(k in _fold(b.get("event")) for k in ELITE):
            continue
        kept = 0
        for e in b["seq"]:
            if e.get("actor_id") in roster_ids:
                continue
            pool[e.get("type") or "?"] += 1
            per_athlete[str(e.get("actor_id"))] += 1
            kept += 1
        # Counted only if it survived the roster exclusion. A bout where every event belongs
        # to a rostered athlete contributes nothing to the pool, and counting it inflates the
        # apparent size of the comparator.
        n_bouts += 1 if kept else 0
    total = sum(pool.values())
    cov = coverage(list(per_athlete.values()))
    out: dict[str, Any] = {"bouts": n_bouts, "events": total,
                           "coverage": cov.to_dict(),
                           "mix": {t: gated(v, total, cov) for t, v in pool.most_common()},
                           "roster_excluded": True}
    for div, seq in per_div.items():
        own: Counter[str] = Counter()
        for outcome in ("win", "loss"):
            for t, e in seq["type_by_outcome"][outcome].items():
                own[t] += e["k"]
        n_own = sum(own.values())
        own_cov = seq.get("coverage", {}).get("all", {})
        blocked = []
        if not own_cov.get("estimable"):
            blocked.append({"side": "category", "code": own_cov.get("reason_code"),
                            "clusters": own_cov.get("clusters"),
                            "effective_n": own_cov.get("effective_n"),
                            "top_share": own_cov.get("top_share"),
                            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE})
        if not cov.estimable:
            blocked.append({"side": "baseline", "code": cov.reason_code,
                            "clusters": cov.clusters,
                            "effective_n": round(cov.effective_n, 3),
                            "top_share": round(cov.top_share, 3),
                            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE})
        if blocked:
            out[div] = {"n_own": n_own, "available": False, "blocked": blocked,
                        "observed": [{"type": t, "own": own[t], "pool": pool[t]}
                                     for t in sorted(set(own) | set(pool))]}
            continue
        rows2: list[dict[str, Any]] = []
        for t in sorted(set(own) | set(pool)):
            rows2.append({"type": t,
                          "contrast": compare_proportions(own[t], n_own, pool[t], total).to_dict()})
        ps2 = [r["contrast"]["p_value"] for r in rows2 if r["contrast"]["p_value"] is not None]
        qs2 = benjamini_hochberg(ps2)
        it2 = iter(zip(qs2, survives(qs2), strict=True))
        for r in rows2:
            if r["contrast"]["p_value"] is None:
                r["q_value"], r["survives_bh"] = None, False
                continue
            r["q_value"], r["survives_bh"] = next(it2)
        out[div] = {"n_own": n_own, "available": True, "rows": rows2,
                    "family": "baseline event mix", "m": len(ps2), "alpha": 0.05}
    return out


# ── layer 7: the division radar ─────────────────────────────────────────────────
# Spokes and pooling ported from the App (`services/radarRings.ts`), so a division's shape and
# an athlete's shape are read on the same axes. `sweep` pools into `guard` -- a sweep is how a
# guard scores -- which is also what lets every ring sit on identical spokes.
RADAR_AXES = ("pass", "control", "submission", "escape", "guard", "takedown")
ELO_SCALE = 400.0


def _axis_of(event_type: str | None) -> str | None:
    t = (event_type or "").lower()
    if t == "sweep":
        return "guard"
    return t if t in RADAR_AXES else None


def _expected(rating: float, reference: float) -> float:
    """Elo expected score: 0.5 means level with the reference.

    The App scores each axis as a win probability against the athlete's own mean, so the
    polygon has a SHAPE instead of every strong athlete reading full on every axis. The same
    trick is what makes a division's radar informative: without it, a division of strong
    athletes is a big circle and says nothing about which parts of its game lead.
    """
    return 1.0 / (1.0 + 10 ** ((reference - rating) / ELO_SCALE))


def radar_layer(conn: Any, bouts: Sequence[Mapping[str, Any]],
                roster: Mapping[str, str]) -> dict[str, Any]:
    """Three rings per division on one set of spokes: usage, rating, rating against the corpus.

    **The rating ring is athlete-level, not node-level.** Node ratings would be the right
    input and the table holding them is empty (see `correlation_layer`), so each axis is
    weighted by the ratings of the athletes who actually produced its events. An axis whose
    contributors are unrated has no rating ring at all, and says so rather than falling back
    to the division mean and looking like a measurement.
    """
    from analysis.rating_v2.config import SITE_RATING_RUN_ID
    from analysis.stats_rigor import wilson

    rated = {athlete_key(r[0]): float(r[1]) for r in conn.execute(text("""
        select a.name, s.rating from athlete_rating_states_v2 s
          join athletes a on a.id = s.athlete_id where s.run_id = :run
    """), {"run": SITE_RATING_RUN_ID}).fetchall()}
    corpus_mean = sum(rated.values()) / len(rated) if rated else 1500.0

    id_key = {}
    for b in bouts:
        id_key[b["a_id"]] = athlete_key(b["a"])
        id_key[b["b_id"]] = athlete_key(b["b"])

    out: dict[str, Any] = {}
    for div in ("65 kg", "+65 kg"):
        counts: Counter[str] = Counter()
        weighted: dict[str, list[tuple[float, int]]] = defaultdict(list)
        own_ids = set()
        for b in bouts:
            for side in ("a", "b"):
                if (b["div_a"] if side == "a" else b["div_b"]) != div:
                    continue
                own_ids.add(b[f"{side}_id"])
        per_axis_athletes: dict[str, set[str]] = defaultdict(set)
        # Contributors, counted separately from RATED contributors. These were the same set
        # until now, because the add() sat inside the `in rated` branch -- so an axis fed by
        # three athletes of whom one carried a rating reported `contributors: 1` and drew the
        # dashed "1 atleta" ray. The warning was misreporting the thing it exists to warn about.
        per_axis_all: dict[str, set[str]] = defaultdict(set)
        per_athlete_axis: dict[str, Counter[str]] = defaultdict(Counter)
        for b in bouts:
            for e in b["seq"]:
                aid = e.get("actor_id")
                if aid not in own_ids:
                    continue
                axis = _axis_of(e.get("type"))
                if not axis:
                    continue
                counts[axis] += 1
                key = id_key.get(aid) or str(aid)
                per_axis_all[axis].add(key)
                per_athlete_axis[key][axis] += 1
                if key in rated:
                    weighted[axis].append((rated[key], 1))
                    per_axis_athletes[axis].add(key)
        total = sum(counts.values())
        div_ratings = [rated[k] for k in {id_key.get(i) for i in own_ids} if k and k in rated]
        div_mean = sum(div_ratings) / len(div_ratings) if div_ratings else None

        axes = []
        for axis in RADAR_AXES:
            n = counts[axis]
            w = weighted[axis]
            axis_rating = sum(r for r, _ in w) / len(w) if w else None
            # Pooled share and the mean of per-athlete shares answer different questions, and
            # with one athlete holding most of the events they are very different numbers. The
            # pooled one describes the corpus; the athlete mean is the closer estimator of "the
            # typical athlete", which is what a radar labelled with a category name implies.
            shares = [c[axis] / sum(c.values()) for c in per_athlete_axis.values()
                      if sum(c.values())]
            axis_ratings = [rated[k] for k in per_axis_athletes[axis]]
            axes.append({
                "axis": axis,
                "usage": {**wilson(n, total).to_dict()},
                "usage_athlete_mean": round(sum(shares) / len(shares), 4) if shares else None,
                "coverage": coverage([c[axis] for c in per_athlete_axis.values()
                                      if c[axis]]).to_dict(),
                "n_events": n,
                "rated_events": len(w),
                "contributors": len(per_axis_all[axis]),
                "rated_contributors": len(per_axis_athletes[axis]),
                # Event-weighted, deliberately: an axis mostly produced by a strong athlete is
                # a strong athlete's axis. The unweighted mean over distinct contributors sits
                # beside it so the weighting is visible instead of implied.
                "rating": round(axis_rating) if axis_rating else None,
                "rating_athlete_mean": (round(sum(axis_ratings) / len(axis_ratings))
                                        if axis_ratings else None),
                # 0.5 = level with this division's own mean, so the polygon has a shape
                "vs_self": round(_expected(axis_rating, div_mean), 4)
                           if axis_rating and div_mean else None,
                # 0.5 = level with the whole rated corpus
                "vs_corpus": round(_expected(axis_rating, corpus_mean), 4)
                             if axis_rating else None,
            })
        out[div] = {
            "axes": axes, "events": total,
            "division_mean_rating": round(div_mean) if div_mean else None,
            "corpus_mean_rating": round(corpus_mean),
            "rated_athletes_in_division": len(div_ratings),
            "note": "usage is the share of this division's own events on that axis; the two "
                    "rating rings are athlete-level (node-level ratings do not exist) and "
                    "weight each axis by whoever produced its events.",
        }
    return {"axes": list(RADAR_AXES), "pools": {"sweep": "guard"},
            "corpus_mean_rating": round(corpus_mean), "divisions": out}


# ── layer 8: data quality ───────────────────────────────────────────────────────
def data_quality(conn: Any, roster: Mapping[str, str],
                 display: Mapping[str, str]) -> dict[str, Any]:
    """Roster athletes who appear in the corpus under more than one spelling.

    Reported as CANDIDATES, never merged here. Merging two athlete rows is a decision that
    needs corroboration from outside the string -- this corpus has already had a real bout
    collapsed into a self-match by a merge that looked obvious -- so the page names the pair,
    says what it costs, and stops.

    It costs something specific and checkable: a split athlete's rating is computed from only
    the bouts filed under one of her spellings, which inflates her RD and can push her past
    the publish gate for no reason but a typo.

    Each candidate carries its VERDICT where one exists, because "nobody has looked at this"
    and "this was looked at and cannot be settled" are different findings and used to render
    identically. And the count of splits already CLOSED ships alongside, so the panel reports
    a state of the data rather than an open-ended worry: a page that only ever lists what is
    left reads the same the day before the work is done and the day after.
    """
    import difflib

    rows = conn.execute(text("""
        select a.name, count(m.id) from athletes a
          left join matches m on (m.athlete_a_id = a.id or m.athlete_b_id = a.id)
               and m.status = 'final'
         group by 1
    """)).fetchall()
    names = [(r[0], int(r[1])) for r in rows]
    roster_keys = set(roster)
    pairs = []
    for i, (n1, c1) in enumerate(names):
        for n2, c2 in names[i + 1:]:
            if not c1 or not c2:
                continue
            k1, k2 = athlete_key(n1), athlete_key(n2)
            if k1 == k2:
                continue
            if not (k1 in roster_keys or k2 in roster_keys):
                continue
            ratio = difflib.SequenceMatcher(None, k1, k2).ratio()
            if ratio >= MIN_DUPLICATE_RATIO:
                verdict = reviewed_verdict(k1, k2)
                pairs.append({"a": n1, "a_bouts": c1, "b": n2, "b_bouts": c2,
                              "ratio": round(ratio, 3),
                              "verdict": verdict[0] if verdict else None,
                              "note": verdict[1] if verdict else None})
    resolved = roster_aliases(roster_keys)
    return {"duplicate_candidates": sorted(pairs, key=lambda p: -(p["a_bouts"] + p["b_bouts"])),
            # Structured, so the page states the rule in its own language instead of printing
            # an English sentence it was handed.
            "rule": {"min_ratio": MIN_DUPLICATE_RATIO, "basis": "canonical_key",
                     "requires_roster_side": True, "requires_bouts_both_sides": True},
            "resolved": {"spellings": len(resolved),
                         "athletes": len(set(resolved.values())),
                         "into": sorted({display[k] for k in set(resolved.values())
                                         if k in display})}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--records", type=Path, default=RECORDS,
                    help="BJJ Heroes records JSON (division + rows per athlete)")
    ap.add_argument("--sequences", type=Path, default=SEQUENCES, help="corpus bouts JSON")
    ap.add_argument("--out", type=Path, default=REPO.parent / "BracketAnalysis" / "data.json")
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
    # Membership in a tracked division, not mere truthiness of the label. The truthy test
    # treated any athlete carrying any non-empty division string as roster, and so excluded
    # them from the baseline the roster is supposed to be compared against.
    roster_ids = {b[f"{s}_id"] for b in bouts for s in ("a", "b")
                  if (b["div_a"] if s == "a" else b["div_b"]) in DIVISIONS}

    eng = get_engine()
    with eng.connect() as conn:
        emb = embedding_layer(conn, roster)
        vids = video_layer(conn, roster)
        corr = correlation_layer(conn, bouts)
        rating = rating_layer(conn, roster)
        radar = radar_layer(conn, bouts, roster)
        dq_ = data_quality(conn, roster, display)

    doc = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        # Every number the page compares against. They were literals inside app.js -- `eff < 2`,
        # `usable < 5`, `events < 300`, `contributors < 2`, a hard-coded roster size of 16 --
        # which meant the renderer was quietly deciding things while its own header claimed it
        # computed nothing. It compares now; it does not decide.
        "thresholds": {
            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE,
            "min_effective_n": MIN_EFFECTIVE_N_FOR_CATEGORY_ESTIMATE,
            "min_n_for_grade": MIN_N_FOR_ANY_GRADE,
            "min_usable_for_outlier": MIN_USABLE_FOR_OUTLIER,
            "thin_axis_contributors": 2,
            "roster_size": len(roster),
        },
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
        "radar": radar,
        "data_quality": dq_,
        "rd": {**rating,
               "athletes": {display[k]: v for k, v in rating["athletes"].items()}},
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
            "why": "A tabela athlete_node_rating_states_v2 existe mas não tem nenhuma linha; "
                   "o trabalho de constelações que a preenche está em aberto (rating_v2 "
                   "ADR-03/ADR-08). Rating por nó contra tipo de vitória é, portanto, não "
                   "computável — e nada é apresentado no lugar sob esse nome.",
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
                "Condicionado a vencer: toda linha é uma luta que alguém venceu, então isto "
                "descreve COMO se vence, não quem vence.",
                "Rating de atleta, não de nó — a pergunta era sobre um nó.",
                "Força da adversária só parcialmente controlada: o gap de rating é reportado "
                "à parte, e a luta cuja derrotada não tem rating sai desse corte.",
                "Corpus inteiro, não só o roster: restringir às 16 atletas deixa lutas "
                "ranqueadas de menos para formar qualquer banda.",
                "Bandas de rating escolhidas à mão. O padrão alto-baixo-baixo-alto é o que a "
                "tabela mostra; ele não estabelece que a relação seja um U.",
            ],
        },
        "score_vs_outcome": {
            "available": False,
            "why": "A tabela matches não tem coluna de placar e nenhum evento de sequência "
                   "carrega `points`. Os únicos placares existentes são os finais, dentro das "
                   "strings de método publicadas, e só existem para lutas decididas NO "
                   "placar — exatamente o complemento da pergunta. O que aparece no lugar é a "
                   "fatia de eventos observados, que é proxy de volume posicional, não placar.",
            "event_share_auc": share_sep.to_dict() if share_sep else None,
            "n_bouts": len(shares),
        },
    }


if __name__ == "__main__":
    sys.exit(main())
