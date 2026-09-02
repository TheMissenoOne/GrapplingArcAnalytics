"""Generate the public *design* site's data + detail pages from the DB.

The static design bundle (``../GrapplingArc/site``) renders client-side from three
generated data files plus one detail page per match / qualifying fighter:

    site/breakdowns-data.js   window.GA_BREAKDOWNS  (all sequence bouts, graph.js-shaped)
    site/fighters-data.js     window.GA_FIGHTERS    (>=3-bout dossiers, card graphs)
    site/elo-data.js          window.GA_ELO         (per-discipline leaderboards: {grappling,mma,wrestling})
    site/breakdown-<slug>.html   per-bout article  (stats + momentum + graph + prose)
    site/grapple-<slug>.html     per-fighter dossier (career graph + signature + prose)

It adapts the app-shaped ``{nodes,edges}`` the breakdown/career exporters emit into the
``{nodes:[{id,label,cat,size,fighter}],links:[{from,to,fighter,weight}]}`` shape
``site/graph.js`` consumes, and uses ``export.narrative`` for the editorial copy — so the
words and the numbers come from the same source.

    uv run python -m export.site_data            # -> ../GrapplingArc/site
    uv run python -m export.site_data --out /tmp/site
    uv run python -m export.site_data --branding-only --out ../GrapplingArc/site
"""
# ruff: noqa: E501  (HTML/JS template strings are content, not wrappable code)

from __future__ import annotations

import argparse
import copy
import html
import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.athlete_systems import (
    build_system_profile,
    compare_profiles,
    from_career_graphview,
    profile_to_dict,
)
from analysis.corpus_paths import OCEAN_FOLD_GROUP_BUDGET, aggregate_bouts, path_payload
from analysis.counter_moves import counter_moves
from analysis.defense_rate import defense_profile
from analysis.event_profile import build_event_profile, event_names
from analysis.gendered_text import Gender, pick
from analysis.names import _normalize_name, canonical_label, canonicalize
from analysis.network_metrics import edge_arrow, edge_dashed, network_from_sequences
from analysis.path_to_victory import dilemmas, path_to_victory
from analysis.rating_v2.config import (
    SITE_MIN_CONFIDENCE_RD,
    SITE_RATING_RUN_ID,
    EngineConfig,
)
from analysis.style_profile import (
    MIN_DOSSIER_EVENTS,
    PROFILE_VERSION,
    build_style_profile,
    qualifies,
)
from db.models import Archetype, Athlete, Match
from db.repository import get_matches_for_athlete
from export.incremental import ItemCache, item_hash
from export.match_breakdown import (
    _final_matches,
    _headline,
    build_match_breakdown,
    export_fighter_graph,
    match_slug,
    slugify,
)
from export.narrative import (
    archetype_label,
    event_narrative,
    match_narrative,
    profile_narrative,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}

# Grapple-Like radar = the App analytics tab's axes (SpiderChart categoryOrder,
# clockwise from the top), so the site and the app read the same fingerprint.
_RADAR_AXES = ["pass", "control", "submission", "escape", "guard", "sweep", "takedown"]
_RADAR_LABELS = ["Pass", "Control", "Submission", "Escape", "Guard", "Sweep", "Takedown"]

# ── Wave 8: publish-confidence gate ─────────────────────────────────────────────
# rating_v2 ADR-02 (docs/rating_v2/01_DECISOES.md): every V2 state read is keyed by an
# explicit run_id — there is no "current" state, reading without one is a defect. Pin the
# run this site's confidence gate reads (and, since wave 9, the same run `ranked_pools`
# reads for the grappling pool -- one pinned run for the whole site).
#
# SITE_RATING_RUN_ID / SITE_MIN_CONFIDENCE_RD live in analysis/rating_v2/config.py (wave
# 9): analysis/ must not import from export/, and analysis.discipline.ranked_pools needs
# this same run_id as its default. Imported above (with EngineConfig) so every existing
# use in this module keeps working unchanged — see that module for the full pinning
# comment.

# Minimum bouts in the corpus before an athlete may appear on a PUBLISHED leaderboard.
# Editorial, like SITE_MIN_CONFIDENCE_RD, and deliberately separate from it: RD answers
# "how sure are we of this rating", this answers "is there enough record to rank them at
# all". Chosen against the measured board (RD<=200 alone seated 3- and 4-bout athletes at
# #5-#8); 10 leaves 21 eligible athletes. Ranking-only — it must never gate a dossier
# page, a percentile denominator, or an analysis weight.
MIN_BOARD_BOUTS = 10

# Floor uncertainty for an athlete with no row in the pinned run — the same seed RD a
# fresh Glicko-2 state carries (ADR-02), never a bare "unconfident"/zero-weight default.
_SEED_RD = EngineConfig().initial_rd

# This gate controls ONLY whether a breakdown-/grapple- page gets written (and whether a
# bout counts as "confident enough to link"). It must NEVER thin the data any other part
# of the export reads: GA_OCEAN, GA_ELO, GA_EVENTS counts, and every network/PageRank/
# community/technique-frequency aggregate are built from the full corpus regardless of
# this gate (see export_site — none of those builders take a `trusted` argument).


def _load_rating_deviations(session: Session, run_id: str | None) -> dict[str, float] | None:
    """athlete_id -> rating_deviation for one persisted rating_v2 run. The only place in
    the site exporter allowed to read AthleteRatingStateV2, and it never queries without
    an explicit run_id (ADR-02). Returns None (not {}) when no run is pinned, so callers
    can tell "no run configured" from "run has nobody in it" and fall back to
    content-only confidence instead of silently trusting nobody."""
    if not run_id:
        logger.warning(
            "SITE_RATING_RUN_ID is unset -- the publish-confidence gate falls back to "
            "content-only (qualifies() + MIN_DOSSIER_EVENTS); rating_v2 confidence is "
            "NOT applied to this export.")
        return None
    from db.models import AthleteRatingStateV2

    rows = session.execute(
        select(AthleteRatingStateV2.athlete_id, AthleteRatingStateV2.rating_deviation)
        .where(AthleteRatingStateV2.run_id == run_id)
    ).all()
    return dict(rows)


def is_confident(content_ok: bool, athlete_id: str, rd_by_athlete: dict[str, float] | None) -> bool:
    """Wave 8 publish-confidence gate — both legs, logical AND:

    1. ``content_ok`` (caller-computed): >=MIN_SEQUENCE_BOUTS sequence bouts AND
       >=MIN_DOSSIER_EVENTS own events — the gate that already decided dossier
       eligibility (``analysis.style_profile.qualifies`` + the grappling_events check).
    2. confidence: rating_deviation <= SITE_MIN_CONFIDENCE_RD on the pinned run.
       ``rd_by_athlete is None`` means no run is pinned (see ``_load_rating_deviations``)
       — the confidence leg is skipped and content alone decides, so a missing run
       degrades the site instead of breaking it. An athlete simply absent from a real
       run's dict gets the engine's seed RD (``_SEED_RD``) as a floor, never a bare
       "no" — today that floor is > SITE_MIN_CONFIDENCE_RD so it reads as unconfident
       in practice, but the semantics stay correct if this ever backs a weighted
       aggregate instead of a yes/no gate.
    """
    if not content_ok:
        return False
    if rd_by_athlete is None:
        return True
    rd = rd_by_athlete.get(athlete_id, _SEED_RD)
    return rd <= SITE_MIN_CONFIDENCE_RD


def _bout_href(match_id: str, slug_by_match: dict[str, str]) -> str | None:
    """href to a bout's breakdown- page, or None if the confidence gate didn't publish
    one (Wave 8: neither competitor was confident enough for an individual reading).
    The one place allowed to decide this — every caller renders a link by calling this,
    never by formatting ``f"breakdown-{slug}.html"`` itself, so a hidden bout can never
    end up linked from somewhere else on the site (dossier "recent bouts", event card,
    footage credit)."""
    slug = slug_by_match.get(match_id)
    return f"breakdown-{slug}.html" if slug else None


def _dossier_href(slug: str, dossier_slugs: frozenset[str]) -> str | None:
    """href to a fighter's grapple- dossier, or None if the confidence gate didn't
    publish one (Wave 8). The one place allowed to decide this — see ``_bout_href``."""
    return f"grapple-{slug}.html" if slug in dossier_slugs else None


# ── Withheld athletes: published corpus, unpublished person ─────────────────────
# Names held back from INDIVIDUAL publication. Editorial like SITE_MIN_CONFIDENCE_RD, but
# answering a different question: RD asks "are we sure enough about this rating", this asks
# "should this competitor be read individually in public right now". The answer is the
# coach's, not a property of the data — so it lives here, in git, with the reason written
# down, rather than as a column someone can flip without leaving a trace.
#
#   Livia Barasine — the coach's own competitor (data/scouting/adcc_2026_women.json lists
#                    her as "Atleta do técnico", 65 kg, ADCC 2026). Publishing her dossier
#                    publishes her game to the division she is about to compete in.
#   Yara Soares    — held alongside her, same request.
#
# Withholding is STRICTLY STRONGER than failing the confidence gate. Failing the gate only
# costs an athlete their own dossier; their bouts still publish whenever the OTHER side is
# trusted, which would put the withheld athlete's game on the site as the opponent's match
# analysis. So a withheld competitor suppresses the whole bout — see build_breakdowns.
#
# It never thins the corpus. GA_ELO, GA_OCEAN, GA_EVENTS, PageRank, the transition network
# and every technique-frequency aggregate are still built from all 865 bouts — the same
# contract `trusted` already carries. This decides pages, never data.
#
# Matched on a fold of the name (accents stripped, case-folded) so "Lívia"/"Livia" and any
# other diacritic spelling of the same person are one entry, not a game of whack-a-mole.
WITHHELD_ATHLETE_NAMES = frozenset({"yara soares", "livia barasine"})


def _fold_name(name: str) -> str:
    """Accent-stripped, case-folded, whitespace-trimmed — only for matching a name against
    WITHHELD_ATHLETE_NAMES. Deliberately NOT `analysis.names._normalize_name`: that one is
    the char-for-char node-key contract with the App (graphSync.ts:normalizeLabel) and must
    not grow a second, unrelated caller whose needs could pull it out of sync."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().casefold()


def _withheld_athlete_ids(session: Session) -> frozenset[str]:
    """athlete_ids matching WITHHELD_ATHLETE_NAMES. Empty when nobody is withheld, and
    empty is the normal state — a name in the set that matches no athlete is not an error
    (Livia Barasine has no bouts in the corpus yet; the entry is what makes sure she never
    silently publishes on the import that first gives her some)."""
    return frozenset(
        a.id for a in session.execute(select(Athlete)).scalars()
        if _fold_name(a.name) in WITHHELD_ATHLETE_NAMES
    )


def _compute_trusted_athletes(session: Session) -> set[str]:
    """Wave 8: athlete_ids allowed a dossier page, and whose presence in a bout is
    enough to publish that bout's breakdown page. Same two-condition AND as
    ``is_confident`` — content (qualifies() + MIN_DOSSIER_EVENTS own events) AND
    rating_deviation <= SITE_MIN_CONFIDENCE_RD on the pinned run.

    Runs once, up front (before build_breakdowns needs the answer), over every athlete
    who appears in a final sequence bout. The content leg is a light re-walk of each
    candidate's own event count — NOT the full style profile (build_fighters builds
    that separately, cached, only for athletes that pass here) — so paying for it twice
    (once here, once in build_fighters for the trusted subset) is cheaper than making
    build_breakdowns wait on build_fighters's heavy per-fighter analytics.

    Athletes in ``WITHHELD_ATHLETE_NAMES`` are dropped from the candidate set before any
    of that runs — no dossier, whatever their record says.
    """
    from db.repository import _perspective_view

    rd_by_athlete = _load_rating_deviations(session, SITE_RATING_RUN_ID)
    withheld = _withheld_athlete_ids(session)
    candidate_ids = {aid for m in _final_matches(session)
                      for aid in (m.athlete_a_id, m.athlete_b_id)} - withheld
    trusted: set[str] = set()
    for aid in candidate_ids:
        if not qualifies(aid, session):
            continue
        own_events = 0
        for m in get_matches_for_athlete(aid, session):
            if m.status != "final" or not m.sequence:
                continue
            pv = _perspective_view(m, aid)
            # `str(e.get("label", ""))` truthiness, not a bare `e.get("label")` — matches
            # build_style_profile's own_events counter char-for-char (analysis/style_profile.py)
            # so this precomputed decision and the profile it gates never disagree.
            own_events += sum(
                1 for e in pv.sequence
                if e.get("actor") == "you" and str(e.get("label", "")))
            if own_events >= MIN_DOSSIER_EVENTS:
                break
        content_ok = own_events >= MIN_DOSSIER_EVENTS
        if is_confident(content_ok, aid, rd_by_athlete):
            trusted.add(aid)
    return trusted


# ── small helpers ────────────────────────────────────────────────────────────
def _clamp3(n: int) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name[:2]).upper()


def _name_break(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]}<br/>{' '.join(parts[1:])}"
    return name


def _result_short(meta: dict[str, Any]) -> str:
    wt = (meta.get("win_type") or "").upper()
    if wt == "SUBMISSION" and meta.get("submission"):
        return f"SUB · {meta['submission']}"
    if wt == "SUBMISSION":
        return "SUB"
    if wt:
        return wt[:3]
    return "N/C"


# Per-export memo: _archetype runs 2 remote queries/call and is hit ~1000× (2–4×/match) for
# only ~200 distinct athletes. Cleared at the top of export_site so a fresh run re-reads.
_ARCH_CACHE: dict[str, str | None] = {}


def _prime_arch_cache(session: Session) -> None:
    """Bulk-load every athlete's emergent archetype in one query so ``_archetype`` never
    round-trips per athlete (it's hit for both sides of every breakdown + every dossier)."""
    from db.models import Graph
    for oid, name in session.execute(
        select(Graph.owner_id, Archetype.name)
        .join(Archetype, Graph.archetype_id == Archetype.id)
        .where(Graph.owner_kind == "athlete", Graph.archetype_id.isnot(None))
    ).all():
        _ARCH_CACHE.setdefault(oid, name)


def _archetype(athlete: Athlete | None, session: Session) -> str | None:
    """Emergent archetype name for an athlete — stored on their Graph (deviance v3 pipeline
    assigns Graph.archetype_id, not Athlete.archetype_id). Memoized per athlete per export."""
    if athlete is None:
        return None
    if athlete.id in _ARCH_CACHE:
        return _ARCH_CACHE[athlete.id]
    from db.models import Graph

    aid = session.execute(
        select(Graph.archetype_id)
        .where(Graph.owner_kind == "athlete", Graph.owner_id == athlete.id,
               Graph.archetype_id.isnot(None))
        .limit(1)
    ).scalar_one_or_none()
    arch = session.get(Archetype, aid) if aid is not None else None
    name = arch.name if arch else None
    _ARCH_CACHE[athlete.id] = name
    return name


def _to_graphview(
    app_graph: dict[str, Any], default_side: str = "a", video_id: str | None = None
) -> dict[str, Any]:
    """app-shaped ``{nodes,edges}`` → the ``graph.js`` ``{nodes,links}`` contract.
    Includes ts (timestamp) and vid (video id) for seek functionality."""
    nodes = []
    for n in app_graph.get("nodes", []):
        d = n.get("data", {})
        typ = str(d.get("type", ""))
        node = {
            "id": n["id"], "label": n.get("label", n["id"]),
            "cat": typ if typ in _CATS else "control",
            "size": _clamp3(int(d.get("usageCount", 1))),
            "fighter": d.get("side", default_side),
        }
        if "ts" in d:
            node["ts"] = d["ts"]
        if video_id:
            node["vid"] = video_id
        # carried through when classified; omitted otherwise so the bundle stays unchanged
        # for the majority of nodes still awaiting mapping review (card 017)
        if d.get("taxonomyId"):
            node["tax"] = d["taxonomyId"]
        nodes.append(node)
    links = []
    for e in app_graph.get("edges", []):
        d = e.get("data", {})
        links.append({
            "from": e["source"], "to": e["target"],
            "fighter": d.get("side", default_side),
            "weight": _clamp3(int(d.get("count", 1))),
            # per-bout breakdown = a recorded timeline fact, always a plain arrow, never
            # dashed/volume-gated (that's aggregate-graph territory — see _career_graphview).
            "arrow": True, "dashed": False,
        })
    return {"nodes": nodes, "links": links}


def _truncate_graph(g: dict[str, Any], limit: int) -> dict[str, Any]:
    """Keep the ``limit`` busiest nodes (+ their links) so cards/dossiers stay legible."""
    nodes = sorted(g["nodes"], key=lambda n: n["size"], reverse=True)[:limit]
    keep = {n["id"] for n in nodes}
    links = [lk for lk in g["links"] if lk["from"] in keep and lk["to"] in keep]
    return {"nodes": nodes, "links": links}


def _direct_career_links(
    links: list[dict[str, Any]], node_type: dict[str, str], net: Any | None,
) -> list[dict[str, Any]]:
    """Collapse reciprocal pairs + orient/dash career links (aggregate graph, rule 1+2) against
    the fighter's own transition ``net`` (raw within-actor counts). Node ids are ``node_key``s;
    ``net`` node labels are canonical library names, so pairing goes through ``_normalize_name``."""
    # ponytail: synonym collision keeps last label seen (~6-pair list, rare) — only affects
    # which raw net label backs the weight lookup below, not the node ids themselves.
    label_by_key: dict[str, str] = (
        {canonicalize(_normalize_name(lbl)): lbl for lbl in net.nodes} if net is not None else {}
    )
    seen: set[frozenset[str]] = set()
    out: list[dict[str, Any]] = []
    for lk in links:
        a, b = lk["from"], lk["to"]
        pair = frozenset((a, b))
        if pair in seen:
            continue
        seen.add(pair)
        la, lb = label_by_key.get(a), label_by_key.get(b)
        f = net[la][lb]["weight"] if net is not None and la and lb and net.has_edge(la, lb) else 0
        r = net[lb][la]["weight"] if net is not None and la and lb and net.has_edge(lb, la) else 0
        arrow = edge_arrow(f, r)
        frm, to = (a, b) if f >= r else (b, a)
        weight = max(f, r)
        dashed = False
        if net is not None and weight > 0:
            maj_from, maj_to = (la, lb) if f >= r else (lb, la)
            ok = net[maj_from][maj_to].get("ok", 0)
            dashed = edge_dashed(weight, ok, node_type.get(to, ""))
        out.append({
            "from": frm, "to": to,
            "weight": _clamp3(weight) if weight else lk["weight"],
            "fighter": lk.get("fighter", "a"), "arrow": arrow, "dashed": dashed,
        })
    return out


def _career_graphview(athlete: Athlete, profile: dict[str, Any], session: Session,
                      limit: int = 12, net: Any | None = None) -> dict[str, Any]:
    """Fighter's career graph (adapted + truncated), falling back to signature transitions.
    Links are directed/dashed against ``net`` (the fighter's own transition network, reused
    from ``_fighter_forks`` — see ``build_fighters``)."""
    g = export_fighter_graph(athlete, session)
    if g and g.get("nodes"):
        gv = _truncate_graph(_to_graphview(g, "a"), limit)
    else:
        nodes: dict[str, dict[str, Any]] = {}
        links = []
        for t in profile.get("signature_transitions", []):
            for lb in (t["from"], t["to"]):
                key = canonicalize(_normalize_name(lb))
                nodes.setdefault(key, {"id": key, "label": canonical_label(key, lb),
                                       "cat": "control", "size": 2, "fighter": "a"})
            frm = canonicalize(_normalize_name(t["from"]))
            to = canonicalize(_normalize_name(t["to"]))
            if frm == to:
                continue  # synonym collapse turned this into a self-loop
            links.append({"from": frm, "to": to,
                          "fighter": "a", "weight": _clamp3(int(t["count"]))})
        gv = {"nodes": list(nodes.values()), "links": links}
    node_type = {n["id"]: n.get("cat", "") for n in gv["nodes"]}
    gv["links"] = _direct_career_links(gv["links"], node_type, net)
    return gv


# ── data files ───────────────────────────────────────────────────────────────
def _corpus_bouts(session: Session) -> list[list[dict[str, Any]]]:
    """Every final bout as two-sided compiler input. Public data only (``matches.sequence``)."""
    bouts: list[list[dict[str, Any]]] = []
    for m in _final_matches(session):
        rows: list[dict[str, Any]] = []
        for e in (m.sequence or []):
            if not isinstance(e, dict):
                continue
            aid = e.get("actor_id")
            side = "a" if aid == m.athlete_a_id else ("b" if aid == m.athlete_b_id else None)
            if side is None:
                continue
            rows.append({
                "label": str(e.get("label", "")), "type": str(e.get("type", "")), "side": side,
                **({"successful": e["successful"]} if "successful" in e else {}),
            })
        if rows:
            bouts.append(rows)
    return bouts


def _athlete_path_graph(athlete_id: str, matches: list[Any]) -> dict[str, Any]:
    """The dossier's "edge = path" map: this athlete's OWN chains across their bouts.

    One-sided on purpose — the career graph has always been the athlete's execution graph, and
    a dossier that also drew what was done TO them would be a different claim. Their events are
    relabelled side ``a`` (the compiler works per side, so dropping the opponent's changes
    nothing about the athlete's own chain), which also makes the anchors read from their
    perspective without a second rule.

    Public only: ``Match.sequence`` is competition footage. No ``graphs`` row is touched here.
    """
    bouts: list[list[dict[str, Any]]] = []
    for m in matches:
        own = [
            {"label": str(e.get("label", "")), "type": str(e.get("type", "")), "side": "a",
             **({"successful": e["successful"]} if "successful" in e else {})}
            for e in (m.sequence or [])
            if isinstance(e, dict) and e.get("actor_id") == athlete_id
        ]
        if own:
            bouts.append(own)
    # §17 (Fase 5e): the dossier draws CONCENTRIC RINGS — Finish at the centre, radius =
    # strokes to a finish. Owner's call on the variant-17 demo, 2026-09-01. The OCEAN stays on
    # the flow (its own caller below) — measured, a corpus-scale disc is one band.
    return path_payload(aggregate_bouts(bouts), layout="ring")


def _featured_stats(bd: dict[str, Any]) -> list[dict[str, Any]]:
    sa, sb = bd["stats"]["a"], bd["stats"]["b"]
    return [
        {"k": "Positional conversion", "va": _pct(sa["positional_conversion"]),
         "vb": _pct(sb["positional_conversion"])},
        {"k": "Control positions", "va": sa["controls"], "vb": sb["controls"]},
        {"k": "Sub attempts", "va": sa["submission_attempts"], "vb": sb["submission_attempts"]},
        {"k": "Transitions", "va": sa["transitions"], "vb": sb["transitions"]},
    ]


# Bumped whenever the SHAPE of a cached breakdown changes, not just its inputs. `item_hash`
# covers the bout's DB fields, which is the right contract for data — but a code change that
# adds a key (2: `path_graph`) leaves every cached item valid and silently missing it, and the
# renderer then falls back for the whole corpus. Same precedent as `PROFILE_VERSION`.
# 4 -> 5 (§5d, docs/taxonomy/03_ARESTA_COMO_CAMINHO.md §FASE 5d): `path_graph` gained the
# additive `folded` field and its `min_count` drop became the ranked `max_variants` budget — a
# cached breakdown built before this would silently render with the old static gate's shape.
# 5 -> 6 (docs §12, 2026-09-01, Ocean's second ceiling): every `folded[i]` row gained `drawn`
# and `stats` gained `undrawn` — additive on breakdowns too (they never set `max_fold_groups`,
# so `drawn` is always `True` here), but still a new key a stale cached item would lack.
# 6 -> 7 (§17, Fase 5e, 2026-09-01): `path_graph` is laid out as concentric RINGS and gained
# `layout`/`rings`/`ringCentre` plus a `ring` index on every state node. Every x/y in a cached
# breakdown is from the old frame, so this is not merely a missing key — it is a different
# picture, and the cache has to miss.
# 7 -> 8 (owner follow-up, 2026-09-02, product cut of the variant-20 demo): the three generic
# anchors (Top/Neutral/Bottom) drop the fixed pole and join the reverse-BFS ring/sector
# computation like any other state. No new key, but every anchor's x/y moves.
BREAKDOWN_VERSION = 8

# --only previews keep their own cache so a partial run can never overwrite the real one.
_PREVIEW_CACHE_DIR = Path(__file__).resolve().parent.parent / ".export_cache" / "preview"


def build_breakdowns(
    session: Session,
    cache: ItemCache | None = None,
    trusted: frozenset[str] = frozenset(),
    withheld: frozenset[str] = frozenset(),
    only: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]], dict[str, Any] | None, int]:
    """Returns (GA_BREAKDOWNS rows, [(slug, full breakdown)], GA_FEATURED, omitted_count)
    for sequence bouts where at least one side is in ``trusted`` (Wave 8 publish-
    confidence gate — see ``_compute_trusted_athletes``) and NEITHER side is in
    ``withheld``. A bout skipped either way contributes no row/page/href, but
    ``corpus_g``/``ptv_v`` below are still built from the FULL corpus — the gate never
    thins what feeds momentum/PageRank/the transition network for the bouts that DO
    publish.

    The two conditions are not the same shape, on purpose: ``trusted`` needs ONE side to
    pass, ``withheld`` is vetoed by EITHER side. A withheld competitor's game must not
    reach the site as their opponent's match analysis (see WITHHELD_ATHLETE_NAMES).

    The featured bout = the decided match with the highest combined opponent rank_elo, so the
    homepage spotlight is real (names, method, mini-stats) and auto-updates with the data.
    """
    standings = _elo_standings(session)
    rows: list[dict[str, Any]] = []
    full: list[tuple[str, dict[str, Any]]] = []
    featured: dict[str, Any] | None = None
    best_score = -1.0
    omitted = 0

    # Build corpus PtV once for all breakdowns (pass through to each for momentum calculation).
    # Full corpus, unfiltered by `trusted` — see the docstring above.
    from analysis.network_metrics import build_transition_network
    corpus_g = build_transition_network(session)
    ptv_v = path_to_victory(corpus_g)

    # Strong-ref every athlete so per-match lookups hit memory. The identity map is weak-ref'd,
    # so a discarded list(select(Athlete)) gets GC'd → session.get re-queries per match remotely.
    athletes_by_id: dict[str, Athlete] = {
        a.id: a for a in session.execute(select(Athlete)).scalars()}

    # The same pair can meet twice in one year (two divisions of one card, two weeks of a
    # league). dump_import keeps both bouts, but match_slug is (a, b, year) — so without a
    # qualifier the second page overwrites the first and the dossier links both bouts to
    # whichever survived. Disambiguate deterministically, by stage then match id.
    slug_taken: set[str] = set()
    _SLUG_BY_MATCH.clear()

    for match in _final_matches(session):
        a = athletes_by_id.get(match.athlete_a_id)
        b = athletes_by_id.get(match.athlete_b_id)
        if a is None or b is None:
            continue
        if a.id in withheld or b.id in withheld:
            # Held back by name, not by confidence — one side is enough to veto the whole
            # bout, because publishing it would publish their game as the other side's
            # reading. Counted as omitted so the transparency note stays honest about how
            # many bouts have no page.
            omitted += 1
            continue
        if a.id not in trusted and b.id not in trusted:
            # Wave 8: neither side confident enough for an individual reading — no
            # breakdown page/row/href for THIS bout. It's still counted upstream (this
            # loop is the only thing skipping it: corpus_g/ptv_v above, GA_OCEAN,
            # GA_ELO and GA_EVENTS are all built independently of this loop).
            omitted += 1
            continue
        slug = match_slug(a, b, match.year)
        if slug in slug_taken:
            stage = slugify(str(match.stage or "").strip())
            candidate = f"{slug}-{stage}" if stage else ""
            if not candidate or candidate in slug_taken:
                candidate = f"{slug}-{match.id[:8]}"
            slug = candidate
        slug_taken.add(slug)
        _SLUG_BY_MATCH[match.id] = slug
        if only is not None and slug not in only:
            continue   # --only preview: skip the per-bout analysis entirely (see export_site)
        if cache is None:
            bd = build_match_breakdown(match, a, b, ptv_v=ptv_v)
        else:
            h = item_hash(BREAKDOWN_VERSION,
                          match.sequence, match.timeline, match.year, match.winner_id,
                          match.win_type, match.event, match.video_url,
                          a.rank_elo, b.rank_elo, a.name, b.name)
            bd = cache.get_or_compute(
                match.id, h, lambda: build_match_breakdown(match, a, b, ptv_v=ptv_v))
        bd["fighters"]["a"]["elo_pct"] = standings.get(a.id)
        bd["fighters"]["b"]["elo_pct"] = standings.get(b.id)
        # Extract YouTube video ID if available
        video_id = None
        if match.video_url:
            import re
            m = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})", match.video_url)
            if m:
                video_id = m.group(1)
        gv = _to_graphview(bd["transition_graph"], video_id=video_id)
        rows.append({
            "id": slug, "href": f"breakdown-{slug}.html",
            "event": bd["meta"]["event"] or "Match",
            "result": _result_short(bd["meta"]), "date": str(match.year or ""),
            "title_en": _headline(bd), "title_pt": _headline(bd),
            "a": {"name": a.name, "code": _initials(a.name), "record": "",
                  "style": _archetype(a, session) or "—"},
            "b": {"name": b.name, "code": _initials(b.name), "record": "",
                  "style": _archetype(b, session) or "—"},
            "graph": gv,
        })
        full.append((slug, bd))
        score = (a.rank_elo or 0.0) + (b.rank_elo or 0.0)
        if bd["meta"]["winner"] and score > best_score:
            best_score = score
            win = bd["meta"]["winner"]
            featured = {
                "slug": slug, "href": f"breakdown-{slug}.html",
                "event": bd["meta"]["event"] or "Match", "method": bd["meta"]["method"],
                "winner": win["name"],
                "a": {"name": a.name, "code": _initials(a.name),
                      "style": _archetype(a, session) or "—"},
                "b": {"name": b.name, "code": _initials(b.name),
                      "style": _archetype(b, session) or "—"},
                "headline": _headline(bd), "stats": _featured_stats(bd),
            }
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows, full, featured, omitted


def _node_video_refs(
    aid: str, matches: list[Any], session: Session
) -> dict[str, dict[str, Any]]:
    """node_key → {vid, ts, slug}: the first timestamped use of each technique by this
    athlete across their filmed bouts, so a career-graph node can play the actual footage.

    ``slug`` is only ever this athlete's OWN bout — since ``aid`` is trusted whenever this
    runs (build_fighters only calls it for trusted fighters), every one of their bouts
    trivially has >=1 confident side (themselves) and always has a page. Routed through
    ``_bout_href`` anyway (never format the href by hand — Wave 8), and it doubles as the
    fix for the pre-existing same-pair-twice-in-a-year slug collision (``_SLUG_BY_MATCH``
    carries the disambiguated slug that was actually written; the un-disambiguated
    ``match_slug(a, b, year)`` used here before could point at the wrong file)."""
    refs: dict[str, dict[str, Any]] = {}
    for m in matches:
        ref = _video_ref(getattr(m, "video_url", None))
        if ref is None:
            continue
        vid, _ = ref
        # _bout_href is the single source of truth for "does this bout's page exist";
        # gate through it even though it's expected to always resolve here (see docstring).
        slug = _SLUG_BY_MATCH.get(m.id) if _bout_href(m.id, _SLUG_BY_MATCH) else None
        for e in m.sequence or []:
            if not isinstance(e, dict) or e.get("actor_id") != aid:
                continue
            ts = e.get("ts")
            key = canonicalize(_normalize_name(str(e.get("label", ""))))
            if ts is None or not key or key in refs:
                continue
            refs[key] = {"vid": vid, "ts": int(ts), "slug": slug}
    return refs


def _fighter_forks(aid: str, athlete_matches: list[Any], session: Session,
                   net: Any | None = None) -> dict[str, Any]:
    """The network-heavy per-fighter block: top-3 dilemmas, top counter-moves, and the defense
    profile. Isolated so the export can cache it by fighter hash (its inputs are the athlete's
    bouts + opponents, which the hash covers). ``net`` reuses the transition network already
    built by the caller (``build_fighters``) instead of recomputing it here."""
    seqs = [m.sequence for m in athlete_matches if m.sequence]
    dilemmas_list: list[dict[str, Any]] = []
    counters_list: list[dict[str, Any]] = []
    if seqs:
        try:
            g = net if net is not None else network_from_sequences(seqs)
            ptv = path_to_victory(g)
            dilemmas_list = [
                {"node": d["node"], "branches": [[b, p] for b, p in d["branches"]]}
                for d in dilemmas(g, ptv)[:3]
            ]
            cm = counter_moves(g, ptv, top_k=2, min_count=2)
            top_cm = sorted(cm.items(), key=lambda kv: kv[1][0]["ptv"], reverse=True)[:5]
            counters_list = [
                {"technique": node,
                 "counters": [{"move": c["counter"], "leads_to": c["leads_to"]} for c in cs]}
                for node, cs in top_cm
            ]
        except Exception:
            pass  # graceful fallback if graph is too sparse
    try:
        defense = defense_profile(aid, athlete_matches, session)
    except Exception:
        defense = None
    return {"dilemmas": dilemmas_list, "counters": counters_list, "defense": defense}


def _bout_dict(m: Any) -> dict[str, Any]:
    """A ``Match`` ORM row as the plain dict ``analysis.lamas_chain.chain_of`` expects."""
    return {"id": m.id, "win_type": m.win_type, "winner": m.winner_id,
            "a_id": m.athlete_a_id, "b_id": m.athlete_b_id, "seq": m.sequence or []}


def _rrb_progression_rows(all_finals: list[Any]) -> tuple[dict[str, dict[str, Any]], Any]:
    """Per-athlete RRB progression (``analysis/rrb_progression.py``), computed once over the
    WHOLE final-bout corpus — the same corpus and the same ``value_table`` construction as
    ``scripts/build_markov_action_weights.block``, kept in sync with it deliberately (n_boot=0
    for the corpus-wide value table; the per-athlete bootstrap only runs for athletes that
    clear the coverage gate, ~17 of 441 measured 2026-08-26).

    Returns ``({athlete_id: row}, values)`` — only rows with ``gated`` True are kept, so a
    dossier for anyone else finds nothing here and gets no section (honest absence, per root
    CLAUDE.md). ``values`` (the corpus value table) is returned alongside so a caller can pull
    one concrete state-pair example off the athlete's own bouts without recomputing it.

    ponytail: re-derives the value table from ``matches`` instead of reading the committed
    ``data/rating/markov_action_weights.json`` artifact, so this can never drift out of sync
    with a stale artifact file. If this pass becomes a measurable share of export time, cache
    it by the same corpus digest the artifact already carries.
    """
    from analysis.lamas_chain import chain_of, reward_risk, rrb
    from analysis.rrb_progression import athlete_progression, value_table

    chains = [chain_of(_bout_dict(m)) for m in all_finals]
    values = value_table(rrb(chains, n_boot=0), reward_risk(chains, n_boot=0))
    pairs = [(ref, ch) for m, ch in zip(all_finals, chains)
             for ref in (m.athlete_a_id, m.athlete_b_id) if ref]
    agg = athlete_progression(pairs, values)
    rows = {r["athlete"]: {**r, "_mixed_source": bool(values.get("mixed_source"))}
            for r in agg["rows"] if r["gated"]}
    return rows, values


def _progression_example(
    aid: str, athlete_matches: list[Any], values: Any
) -> dict[str, Any] | None:
    """One concrete state-pair from this athlete's OWN bouts — the single largest-magnitude
    transition in their signed RRB position, read against the corpus-wide ``values`` table.
    Descriptive illustration only (narrative.py never quotes the delta itself), so picking the
    biggest swing rather than a "typical" one is the honest choice: it is the one transition a
    reader can actually verify against a real bout."""
    from analysis.lamas_chain import chain_of
    from analysis.rrb_progression import trajectory

    best: tuple[float, dict[str, Any]] | None = None
    for m in athlete_matches:
        ch = chain_of(_bout_dict(m))
        if not ch.actor_reliable:
            continue
        t = trajectory(ch, aid, values)
        steps = t["steps"]
        for i, d in enumerate(t["deltas"]):
            if d is None:
                continue
            s0, s1 = steps[i]["state"], steps[i + 1]["state"]
            # SUB carries by far the largest magnitude (rrb_progression.py: its value is
            # "partly circular", mostly the chain's OWN terminal step) and would otherwise
            # dominate every max-|delta| search — turning "concrete example" into "he
            # finished" for anyone with a submission win. Excluded so the example actually
            # illustrates a mid-fight swing.
            if "SUB" in (s0, s1):
                continue
            if best is None or abs(d) > abs(best[0]):
                best = (d, {"from_state": s0, "to_state": s1})
    return best[1] if best else None


# Same job as BREAKDOWN_VERSION, for the dossier's cached items (1: `:pg`, the path map).
# Separate from PROFILE_VERSION because that one is style_profile's own contract, not ours.
# 3 -> 4 (§5d): same `folded`/`max_variants` shape change as BREAKDOWN_VERSION, above.
# 4 -> 5 (docs §12, 2026-09-01): same `drawn`/`undrawn` shape change as BREAKDOWN_VERSION 5 -> 6.
# 5 -> 6 (§17, Fase 5e): same ring-layout change as BREAKDOWN_VERSION 6 -> 7 — every cached
# position is from the old frame, so the cache has to miss, not merely gain a key.
# 6 -> 7: same anchor-into-ring change as BREAKDOWN_VERSION 7 -> 8.
DOSSIER_VERSION = 7


def build_fighters(
    session: Session,
    cache: ItemCache | None = None,
    trusted: frozenset[str] = frozenset(),
    only: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Returns (GA_FIGHTERS card rows, {slug: {athlete, profile, career}}) for fighters in
    ``trusted`` (Wave 8 publish-confidence gate — see ``_compute_trusted_athletes``: same
    content gate this function used to apply itself, now precomputed once up front, AND
    rating_deviation <= SITE_MIN_CONFIDENCE_RD). The profile + career graph are computed
    once and reused for the dossier."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    system_profiles: dict[str, Any] = {}
    # Strong-ref every athlete once so lookups hit memory. NB: SQLAlchemy's identity map is
    # weak-ref'd — a discarded `list(select(Athlete))` gets GC'd and every session.get re-queries
    # remotely; holding this dict keeps them resident (and we look opponents up from it directly).
    athletes_by_id: dict[str, Athlete] = {
        a.id: a for a in session.execute(select(Athlete)).scalars()}
    # All final bouts once → per-athlete index, so the per-fighter dilemma build below reuses
    # them instead of a remote SELECT per fighter (was an N+1 over remote Supabase).
    all_finals = _final_matches(session)
    matches_by_athlete: dict[str, list[Any]] = {}
    for _m in all_finals:
        matches_by_athlete.setdefault(_m.athlete_a_id, []).append(_m)
        matches_by_athlete.setdefault(_m.athlete_b_id, []).append(_m)
    # Corpus-wide once, not per fighter — see _rrb_progression_rows docstring. Only the ~17
    # gated athletes carry a row; everyone else's dossier gets no Progression section at all.
    progression_rows, progression_values = _rrb_progression_rows(all_finals)
    for match in all_finals:
        for aid in (match.athlete_a_id, match.athlete_b_id):
            if aid in seen:
                continue
            seen.add(aid)
            athlete = athletes_by_id.get(aid)
            if athlete is None or aid not in trusted:
                continue
            if only is not None and slugify(athlete.name) not in only:
                continue   # --only preview: skip build_style_profile (the export's slowest call)
            # Fighter cache key: everything the profile + career depend on — the athlete's own
            # fields and every one of their bouts, plus each opponent's rank_elo (defense_profile
            # scores against it, so an opponent's rating change must invalidate this dossier too).
            fh = None
            if cache is not None:
                _ams = get_matches_for_athlete(aid, session)
                _opp = [athletes_by_id.get(m.athlete_b_id if m.athlete_a_id == aid
                                           else m.athlete_a_id) for m in _ams]
                fh = item_hash(PROFILE_VERSION, DOSSIER_VERSION,
                               aid, athlete.name, athlete.rank_elo, athlete.weight_class,
                               [(m.id, m.sequence, m.timeline, m.year, m.winner_id, m.win_type)
                                for m in _ams],
                               [o.rank_elo if o else None for o in _opp])
                profile = cache.get_or_compute(
                    f"{aid}:p", fh, lambda: build_style_profile(athlete, session))
            else:
                profile = build_style_profile(athlete, session)
            # Surface the real emergent archetype (RF01, deviance v3) instead of "Grappler".
            profile["archetype"] = _archetype(athlete, session) or profile.get("archetype")
            # NB: no MIN_DOSSIER_EVENTS check here — `aid in trusted` above already
            # required it (Wave 8), computed the same way (own-event walk), so a second
            # check here would just be the same formula run twice and risk drifting.
            # This fighter's own transition net — built once, shared by the career graph's
            # arrow/dash orientation AND _fighter_forks below (don't recompute). Lazy: the
            # cache thunks below only call it on a miss, so a cache HIT still skips the build.
            athlete_matches = matches_by_athlete.get(aid, [])[:30]
            _net_box: list[Any] = []

            def _net() -> Any | None:
                if not _net_box:
                    _seqs = [m.sequence for m in athlete_matches if m.sequence]
                    _net_box.append(network_from_sequences(_seqs) if _seqs else None)
                return _net_box[0]

            if cache is not None:
                career = cache.get_or_compute(
                    f"{aid}:c", fh,
                    lambda: _career_graphview(athlete, profile, session, 12, _net()))
                path_graph = cache.get_or_compute(
                    f"{aid}:pg", fh, lambda: _athlete_path_graph(aid, athlete_matches))
            else:
                career = _career_graphview(athlete, profile, session, 12, _net())
                path_graph = _athlete_path_graph(aid, athlete_matches)
            card = _truncate_graph(career, 8)
            slug = slugify(athlete.name)
            rec = profile["fighter"]["record"]
            rank = profile["fighter"]["elo_rank"]
            sub = f"{rec['wins']}–{rec['losses']}"
            if rank:
                sub += f" · #{rank} ELO"
            elif athlete.weight_class:
                sub += f" · {athlete.weight_class}"
            arche = profile.get("archetype") or "Grappler"
            cards.append({
                "slug": slug, "name": _name_break(athlete.name),
                "arch_en": arche, "arch_pt": archetype_label("pt", arche), "rec": sub,
                "href": f"grapple-{slug}.html",
                "nodes": card["nodes"], "links": card["links"],
                "_rank": rank or 9999,
            })
            ag = from_career_graphview(athlete.name, career)
            system_profile = build_system_profile(athlete.name, ag)
            system_profiles[slug] = system_profile

            # Per-fighter forks (dilemmas + counters + defense) — the network-heavy block.
            if cache is not None:
                forks = cache.get_or_compute(
                    f"{aid}:f", fh, lambda: _fighter_forks(aid, athlete_matches, session, _net()))
                videos = cache.get_or_compute(
                    f"{aid}:v", fh, lambda: _node_video_refs(aid, athlete_matches, session))
            else:
                forks = _fighter_forks(aid, athlete_matches, session, _net())
                videos = _node_video_refs(aid, athlete_matches, session)

            # RRB progression — only the gated ~17/441 carry a row (see _rrb_progression_rows);
            # the one-example lookup is cheap enough to skip caching for that small a set.
            progression = None
            prog_row = progression_rows.get(aid)
            if prog_row is not None:
                example = _progression_example(aid, athlete_matches, progression_values)
                progression = {**prog_row, "_example": example}

            details[slug] = {
                "athlete": athlete,
                "profile": profile,
                "career": career,
                "path_graph": path_graph,
                "_systems": profile_to_dict(system_profile),
                "_dilemmas": forks["dilemmas"],
                "_counters": forks["counters"],
                "_defense": forks["defense"],
                "_videos": videos,
                "_progression": progression,
            }

    # Compute N×N nearest analogues per athlete
    all_profiles = list(system_profiles.values())
    for slug, profile_dict in details.items():
        if slug in system_profiles:
            sp = system_profiles[slug]
            nearest = compare_profiles(sp, all_profiles, k=5)
            profile_dict["analogues"] = nearest

    cards.sort(key=lambda r: r["_rank"])
    for r in cards:
        del r["_rank"]
    return cards, details


# A percentile pool smaller than this can't say "Top X%" honestly — leave its athletes
# unranked (renderers already show "Unranked" / omit the chip for missing ids).
_MIN_POOL = 5


def _elo_standings(session: Session) -> dict[str, int]:
    """athlete_id → Grappling-ELO percentile (top X%) within the athlete's own
    discipline pool (mma / grappling / wrestling). Tiny pools stay unranked."""
    from analysis.discipline import ranked_pools

    out: dict[str, int] = {}
    for rows in ranked_pools(session).values():
        n = len(rows)
        if n < _MIN_POOL:
            continue
        out.update({aid: max(1, round((i + 1) / n * 100)) for i, (aid, _, _) in enumerate(rows)})
    return out


def _bout_counts(session: Session) -> dict[str, int]:
    """athlete_id -> how many bouts they appear in, either side.

    Counts the RAW corpus, deliberately not the publishable subset: the question this
    answers is "is there enough record to rank this person at all", which is about the
    evidence the rating saw, not how much of it the site chose to publish. Used only by
    ``build_elo``'s board floor.

    Counted through the ORM columns rather than raw SQL on purpose — ``Athlete.id`` goes
    through a type decorator, so a ``text()`` aggregate returns keys that don't compare
    equal to the ids ``ranked_pools`` yields and every lookup silently misses. Two
    columns over the match table is cheap enough that avoiding that trap costs nothing.
    """
    counts: Counter[str] = Counter()
    for a_id, b_id in session.execute(select(Match.athlete_a_id, Match.athlete_b_id)):
        if a_id:
            counts[a_id] += 1
        if b_id:
            counts[b_id] += 1
    return dict(counts)


def build_elo(session: Session, limit: int = 8,
              min_bouts: int = MIN_BOARD_BOUTS) -> dict[str, list[list[Any]]]:
    """Per-discipline leaderboards, rows as RELATIVE values (% of that board's #1
    rating) — never the raw number. Shape: {discipline: [[rank, name, "NN%", NN], …]}.

    Wave 9: the confidence filter lives HERE, not inside ``ranked_pools`` — the pool is
    the percentile denominator, and thinning it by confidence would inflate everyone
    else's percentile (see ``ranked_pools`` docstring). But publishing a ranked name on
    this board that the site refuses to give a dossier page to (Wave 8's
    ``SITE_MIN_CONFIDENCE_RD`` gate) is the site contradicting its own gate, so the
    published top N — and only the top N — is filtered before it's cut.

    The grappling board carries a SECOND cut the page gate doesn't have: a floor on how
    many bouts an athlete has in the corpus (``MIN_BOARD_BOUTS``). RD alone cannot do
    this job, because RD conflates "few bouts" with "many bouts but inactive lately" —
    measured on the pinned run, RD<=200 alone put athletes with 3, 4 and 4 bouts at #5-#8
    while #1 had 114. A dossier is a statement about one athlete with its confidence
    attached; a top-8 board is a ranking, the single most rating-sensitive artefact on
    the site, and 3 bouts cannot support a #7 claim. The floor leaves 21 eligible
    athletes, so the board still fills with room to spare."""
    from analysis.discipline import ranked_pools

    rd_by_athlete = _load_rating_deviations(session, SITE_RATING_RUN_ID)
    bouts_by_athlete = _bout_counts(session) if rd_by_athlete is not None and min_bouts else {}
    boards: dict[str, list[list[Any]]] = {}
    for d, rows in ranked_pools(session).items():
        if d == "grappling" and rd_by_athlete is not None:
            rows = [
                r for r in rows
                if rd_by_athlete.get(r[0], _SEED_RD) <= SITE_MIN_CONFIDENCE_RD
                and bouts_by_athlete.get(r[0], 0) >= min_bouts
            ]
        rows = rows[:limit]
        out: list[list[Any]] = []
        if rows:
            top = rows[0][2]
            for i, (_, name, score) in enumerate(rows):
                rel = round(score / top * 100)
                out.append([str(i + 1), name, f"{rel}%", rel])
        boards[d] = out
    return boards


# ── HTML chrome ──────────────────────────────────────────────────────────────
def _nav(active: str) -> str:
    def cls(key: str) -> str:
        return ' class="on"' if key == active else ""
    return f"""<div class="beltline"></div>
<header class="site-head"><div class="wrap">
  <a class="brand" href="index.html" aria-label="GrapplingArc"><img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/><span class="brand-wordmark">Grappling<span class="brand-wordmark-accent">Arc</span></span></a>
  <button class="nav-toggle" aria-label="Menu" aria-expanded="false" aria-controls="siteNav" onclick="this.setAttribute('aria-expanded',document.body.classList.toggle('nav-open'))"><span></span><span></span></button>
  <nav class="site-nav" id="siteNav">
    <a href="index.html"{cls('home')}>Home</a>
    <a href="breakdowns.html"{cls('breakdowns')}>Breakdowns</a>
    <a href="events.html"{cls('events')}>Events</a>
    <a href="grapple-like.html"{cls('grapple')}>Grapple Like</a>
    <a href="the-ocean.html"{cls('ocean')}>The Ocean</a>
    <a href="the-data.html"{cls('data')}>The Data</a>
    <div class="nav-cta">
      <div class="lang"><button data-lang="en" class="on">EN</button><button data-lang="pt">PT</button></div>
      <a class="btn app sm" href="index.html#app">Get the App</a>
    </div>
  </nav>
</div></header>"""


_FOOTER = """<footer class="site-foot"><div class="wrap">
  <a class="brand" href="index.html" aria-label="GrapplingArc"><img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/><span class="brand-wordmark">Grappling<span class="brand-wordmark-accent">Arc</span></span></a>
  <nav class="links">
    <a href="breakdowns.html">Breakdowns</a><a href="events.html">Events</a><a href="grapple-like.html">Grapple Like</a>
    <a href="the-ocean.html">The Ocean</a><a href="the-data.html">The Data</a>
    <a href="../privacy.html">Privacy</a><a href="../account-deletion.html">Data &amp; Deletion</a>
  </nav>
  <p class="copy">© 2026 GrapplingArc · generated from match data · analysis &amp; education only</p>
</div></footer>"""

# Canonical/OG base — keep in sync with _config.yml url + baseurl (+ /site).
SITE_BASE = "https://themissenoone.github.io/GrapplingArc/site"
_DEFAULT_DESC = (
    "Interactive grappling & MMA match breakdowns — transition maps, momentum, "
    "positional conversion and Grappling ELO."
)


# Fighter photos that actually exist in the output bundle. An og:image pointing at a
# missing file is not a cosmetic issue: the social card renders broken for that page, and
# there are only a handful of photos against hundreds of athletes. Populated by
# export_site() before any page is rendered; empty set = fall back everywhere.
_AVAILABLE_IMAGES: set[str] = set()

# match id → the breakdown slug actually written for it. Dossier bout rows compute
# their own slug from (a, b, year), which collides when a pair meets twice in a
# year; this is the authority.
_SLUG_BY_MATCH: dict[str, str] = {}


def _og_image(path: str) -> str:
    """``path`` if the file shipped with the bundle, else the brand card."""
    return path if path in _AVAILABLE_IMAGES else "brand-og.png"


def _head(title: str, description: str = "", path: str = "", image: str = "brand-og.png") -> str:
    """Full <head> with per-page SEO + Open Graph + Twitter card (acquisition baseline)."""
    e = html.escape
    full = f"{title} — GrapplingArc"
    desc = (description or _DEFAULT_DESC).strip()
    if len(desc) > 200:
        desc = desc[:197].rstrip() + "…"
    canonical = f"{SITE_BASE}/{path}" if path else f"{SITE_BASE}/"
    img = image if image.startswith("http") else f"{SITE_BASE}/{image}"
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{e(full)}</title>
<meta name="description" content="{e(desc)}"/>
<link rel="canonical" href="{e(canonical)}"/>
<meta property="og:type" content="website"/><meta property="og:site_name" content="GrapplingArc"/>
<meta property="og:title" content="{e(full)}"/>
<meta property="og:description" content="{e(desc)}"/>
<meta property="og:url" content="{e(canonical)}"/>
<meta property="og:image" content="{e(img)}"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="{e(full)}"/>
<meta name="twitter:description" content="{e(desc)}"/>
<meta name="twitter:image" content="{e(img)}"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800;900&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<link rel="icon" type="image/svg+xml" href="brand-mark.svg"/>
<link rel="stylesheet" href="site.css"/></head><body>"""


def _bi(en: str, pt: str) -> str:
    """One string in both languages, toggled by i18n.js."""
    return (f'<span data-lang-en>{html.escape(en)}</span>'
            f'<span data-lang-pt>{html.escape(pt)}</span>')


# Dossier section headings (English-only chrome, unlike the bilingual prose body) that
# pronoun-agree with athletes.gender. See analysis/gendered_text.pick's docstring for the
# convention — None (unknown) never reads masculine.
def _defense_heading(gender: Gender) -> str:
    return pick(gender, m="What he stops, weighted by who threw it",
                f="What she stops, weighted by who threw it",
                neutral="What gets stopped, weighted by who threw it")


def _counters_heading(gender: Gender) -> str:
    return pick(gender, m="His highest-value answer from each position",
                f="Her highest-value answer from each position",
                neutral="The highest-value answer from each position")


def _progression_heading(gender: Gender) -> str:
    return pick(gender, m="Where his sequences move him",
                f="Where her sequences move her",
                neutral="Where the sequences move")


def _signature_heading(gender: Gender) -> str:
    return pick(gender, m="What he reaches for first",
                f="What she reaches for first",
                neutral="What gets reached for first")


def _one_prose(sections: list[tuple[str, list[str]]]) -> str:
    parts = []
    for heading, paras in sections:
        parts.append(f'<h2 class="sec-label">{html.escape(heading)}</h2>')
        body = "".join(f"<p>{html.escape(p)}</p>" for p in paras)
        parts.append(f'<div class="editorial">{body}</div>')
    return "\n".join(parts)


def _prose_html(en: list[tuple[str, list[str]]],
                pt: list[tuple[str, list[str]]] | None = None) -> str:
    """Both languages, side by side, toggled by i18n.js's data-lang-* mechanism.

    Generated pages carried English only while the hand-written ones were bilingual, so
    the actual content of the site — every breakdown, dossier and card article — was
    English-only. Passing just ``en`` keeps the old behaviour for callers that have no
    translation."""
    if pt is None:
        return _one_prose(en)
    return (f'<div data-lang-en>{_one_prose(en)}</div>'
            f'<div data-lang-pt>{_one_prose(pt)}</div>')


# ── breakdown detail page ────────────────────────────────────────────────────
_BREAKDOWN_JS = """
// interactive match timeline: every event as a tick on a time axis, momentum as the
// background; click/tap a tick → seek the video. Rendered by site/timeline.js (GATimeline).
(function(){
  const el=document.getElementById('seqTimeline'); if(!el||!window.GATimeline) return;
  GATimeline.mount(el,{timeline:BD.timeline||[],momentum:(BD.stats.momentum_series||[]),
    momentumTs:(BD.stats.momentum_ts||[]),a:BD.a,b:BD.b,onSeek:gaSeek});
})();
// YT API: load script once, build player instance once, seek without reload
var gaPlayer=null;var gaPlayerReady=false;
window.onYouTubeIframeAPIReady=function(){if(BD.vid){gaPlayer=new YT.Player('ytFrame',{events:{onReady:function(){gaPlayerReady=true;}}});}};
if(BD.vid&&!window.YT){var tag=document.createElement('script');tag.src='https://www.youtube.com/iframe_api';document.head.appendChild(tag);}
// click a node with a timestamp → seek the match video to that moment
function gaSeek(t){
  if(!BD.vid||!gaPlayer||!gaPlayerReady) return;
  gaPlayer.seekTo(Math.max(0,(t|0)+(BD.start||0)-5),true);gaPlayer.playVideo();  // -5s → show the setup
  document.getElementById('ytFrame').scrollIntoView({behavior:'smooth',block:'center'});
}
// the bout's paths — positions as nodes, the techniques between them on the stroke
// ("edge = path"). Falls back to the legacy every-event-is-a-node graph if an older
// bundle has no pathGraph, so a stale page never renders empty.
var PG = BD.pathGraph && BD.pathGraph.nodes && BD.pathGraph.nodes.length ? BD.pathGraph : null;
if (PG) {
  GAGraph.mountPaths(document.getElementById('seqGraph'), {
    nodes: PG.nodes, links: PG.links, paths: PG.paths, unresolved: PG.unresolved,
    // §17 — the concentric-ring frame. Additive: an older bundle has neither field and draws as
    // the flow it was laid out in.
    layout: PG.layout, rings: PG.rings, ringCentre: PG.ringCentre,
    onLinkSelect: l => { if (l && l.ts != null) gaSeek(l.ts); },
  });
} else {
  GAGraph.mount(document.getElementById('seqGraph'),{mode:'map',swim:true,pan:true,zoom:true,nodes:BD.graph.nodes,links:BD.graph.links,
    onSelect:n=>{if(n&&n.ts!=null)gaSeek(n.ts);}});
}
const lc=[['takedown','Takedown'],['control','Control'],['guard','Guard'],['pass','Passing'],['sweep','Sweep'],['submission','Submission'],['escape','Escape'],['transition','Transition']];
const dot=c=>'<span class="dot" style="background:'+c+'"></span>';
var lgNodes=(PG||BD.graph).nodes;
document.getElementById('seqLegend').innerHTML=
  lc.filter(([k])=>lgNodes.some(n=>n.cat===k&&n.kind!=='anchor')).map(([k,l])=>'<span>'+dot(GAGraph.CAT[k])+l+'</span>').join('')
  +'<span style="margin-left:auto">'+dot('var(--blue)')+BD.a+'</span><span>'+dot('var(--orange)')+BD.b+'</span>';
"""


def _stat_row(k: str, va: Any, vb: Any) -> str:
    return (f'<div class="stat"><div class="k">{k}</div>'
            f'<div class="vrow"><span class="v a">{va}</span><span class="v b">{vb}</span></div></div>')


_YT_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|v/))([\w-]{11})")
_YT_T_RE = re.compile(r"[?&#]t=(\d+)")


def _video_ref(url: str | None) -> tuple[str, int] | None:
    """Stored video URL → (youtube id, start seconds) — None when there's no valid link."""
    if not url:
        return None
    m = _YT_RE.search(url)
    if not m:
        return None
    t = _YT_T_RE.search(url)
    return m.group(1), int(t.group(1)) if t else 0


def _youtube_embed(url: str | None) -> str:
    """Responsive 16:9 YouTube embed block — empty string when there's no valid link, so the
    section is fully hidden for matches without a video. The iframe carries ``id="ytFrame"``
    so the sequence graph can seek it (click a node → jump to that moment)."""
    ref = _video_ref(url)
    if ref is None:
        return ""
    vid, start = ref
    src = f"https://www.youtube-nocookie.com/embed/{vid}?enablejsapi=1" + (f"&start={start}" if start else "")
    return (
        '<section class="block"><div class="wrap prose"><div class="sec-label">Watch</div></div>'
        '<div class="wrap viz"><div style="position:relative;width:100%;aspect-ratio:16/9;'
        'border:1px solid var(--line);border-radius:var(--radius);overflow:hidden">'
        f'<iframe id="ytFrame" src="{src}" title="Match video" '
        'loading="lazy" frameborder="0" style="position:absolute;inset:0;width:100%;height:100%" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; '
        'picture-in-picture" allowfullscreen></iframe></div></div></section><div class="divider"></div>'
    )


def _train_this_style(
    a: dict[str, Any], b: dict[str, Any], dossier_slugs: frozenset[str]
) -> str:
    """Conversion CTA (RF02/RF15): link each fighter to their dossier (when it exists) and
    nudge toward building the style in the app — the breakdown → Grapple Like → Project loop."""
    btns = []
    for f in (a, b):
        fslug = slugify(f.get("name", "unknown"))
        href = _dossier_href(fslug, dossier_slugs)
        if href:
            btns.append(f'<a class="btn" href="{href}">'
                        f'Grapple like {html.escape(f.get("name", "unknown"))} →</a>')
    btns.append('<a class="btn app" href="index.html#app">Start a Project in the app →</a>')
    return (
        '<section class="block"><div class="wrap prose">'
        '<h2 class="sec-label">Train this style</h2>'
        '<div class="editorial"><p>Study the full game behind this performance, then build it '
        'into your own — start a Project in the GrapplingArc app and track your reps.</p></div>'
        f'<div class="flex g12 wrap-fx" style="margin-top:16px">{"".join(btns)}</div>'
        '</div></section>'
    )


def render_breakdown_page(
    slug: str, bd: dict[str, Any], dossier_slugs: frozenset[str] = frozenset()
) -> str:
    meta, stats = bd["meta"], bd["stats"]
    a, b = bd["fighters"]["a"], bd["fighters"]["b"]
    sa, sb = stats["a"], stats["b"]
    arche_a = bd.get("_arch_a") or ""
    arche_b = bd.get("_arch_b") or ""
    sections = match_narrative(bd)
    sections_pt = match_narrative(bd, lang="pt")
    winner = meta.get("winner")
    win_line = (f"{winner['name']} · {meta['method']}" if winner
                else meta["method"])
    pc = _pct
    stat_grid = "".join([
        _stat_row("Takedowns", sa["takedowns_landed"], sb["takedowns_landed"]),
        _stat_row("Positional conversion", pc(sa["positional_conversion"]),
                  pc(sb["positional_conversion"])),
        _stat_row("Transitions", sa["transitions"], sb["transitions"]),
        _stat_row("Sub attempts", sa["submission_attempts"], sb["submission_attempts"]),
        _stat_row("Control positions", sa["controls"], sb["controls"]),
    ])
    # F1: a bout with no tracked events for a fighter shows an all-zero column that reads as
    # broken. Most such bouts are genuinely thin (few refined events), not a name mismatch —
    # say so plainly instead of presenting empty stats as fact.
    _sk = ("takedowns_landed", "positional_conversion", "transitions",
           "submission_attempts", "controls")
    thin_note = (
        '<div style="font-family:var(--mono);font-size:12px;letter-spacing:1px;'
        'text-transform:uppercase;color:var(--ink-3);margin-bottom:14px">Sparse tracked-event '
        'data for this bout — the tale of the tape below may understate the action.</div>'
        if all(not sa.get(k) for k in _sk) or all(not sb.get(k) for k in _sk) else "")

    def sig_card(f: dict[str, Any], name: str) -> str:
        # Relative standing (top X%) + a % move — never the raw rating.
        pct = f.get("elo_pct")
        value = f"Top {pct}%" if pct else "Unranked"
        d = f.get("elo_delta_pct")
        delta = ""
        if d is not None:
            cls = "up" if d >= 0 else "down"
            arrow = "▲" if d >= 0 else "▼"
            delta = f'<div class="delta {cls}">{arrow} {d:+.1f} pp this bout</div>'
        return (f'<div class="sig-card"><div class="k">{html.escape(name)} · Grappling ELO</div>'
                f'<div class="v">{value}</div>{delta}</div>')

    ref = _video_ref(meta.get("video_url"))
    start = ref[1] if ref else 0
    # Convert broadcast-absolute ts → match-relative (subtract start offset)
    def sub_start(ts: int | None) -> int | None:
        if ts is None:
            return None
        return max(0, ts - start)
    tgraph = bd["transition_graph_gv"]
    for n in tgraph.get("nodes", []):
        if "ts" in n:
            n["ts"] = sub_start(n["ts"])
    # The path map's timestamps ride the ACTIONS (an action is what happened at a moment; in
    # this model the action lives on the edge), so the same broadcast→bout-relative shift has
    # to reach them. Copied, never mutated in place — `bd` may come straight from the
    # incremental cache and a second run would then subtract `start` twice.
    pgraph = copy.deepcopy(bd.get("path_graph") or {"nodes": [], "links": [], "paths": []})
    for lk in pgraph.get("links", []):
        if lk.get("ts") is not None:
            lk["ts"] = sub_start(lk["ts"])
        for act in lk.get("actions", []):
            if act.get("ts") is not None:
                act["ts"] = sub_start(act["ts"])
    timeline = bd.get("event_timeline", [])
    for e in timeline:
        if "ts" in e:
            e["ts"] = sub_start(e["ts"])
    momentum_ts_adj = [sub_start(t) for t in (stats.get("momentum_ts") or [])]
    payload = {
        "a": a["name"], "b": b["name"],
        "graph": tgraph,
        "pathGraph": pgraph,
        "stats": {"momentum_series": stats.get("momentum_series", []),
                  "momentum_ts": momentum_ts_adj},
        "timeline": timeline,
        "vid": ref[0] if ref else None,
        "start": start,
    }
    has_seek = bool(ref) and any(lk.get("ts") is not None for lk in pgraph.get("links", []))
    seq_hint = (
        "Each node is a POSITION; each stroke is the run of techniques that got from one to the "
        "next, in order. A stroke shared by several sequences is drawn once and thicker — that "
        "is where the game repeats. A short dash is a link the model read from the gap, never a "
        "logged event. Click a position or a stroke to light the whole sequence it belongs to"
        + ("; clicking a stroke also jumps the video to that moment." if has_seek else ".")
    )
    body = f"""{_nav('breakdowns')}
<section class="art-hero" role="img" aria-label="{html.escape(a['name'])} vs {html.escape(b['name'])}"><div class="wrap">
  <div class="center"><a href="breakdowns.html" class="tag" style="text-decoration:none">← Breakdowns</a></div>
  <div class="bout">
    <div class="corner a"><span class="av">{_initials(a['name'])}</span>
      <span class="nm">{html.escape(_name_break(a['name'])).replace('&lt;br/&gt;', '<br/>')}</span>
      <span class="rc">{html.escape(arche_a)}</span></div>
    <span class="vsbig">VS</span>
    <div class="corner b"><span class="av">{_initials(b['name'])}</span>
      <span class="nm">{html.escape(_name_break(b['name'])).replace('&lt;br/&gt;', '<br/>')}</span>
      <span class="rc">{html.escape(arche_b)}</span></div>
  </div>
  <div class="result-bar">
    <span class="tag">{html.escape(meta['event'] or 'Match')}</span>
    {'<span class="tag">' + html.escape(meta['weight_class']) + '</span>' if meta.get('weight_class') else ''}
    <span class="tag" style="color:var(--cat-submission);border-color:#3a2020">{html.escape(win_line)}</span>
  </div>
  <h1 class="art-title">{html.escape(_headline(bd))}</h1>
  <div class="prose"><p class="lead art-sum">{_bi(sections[0][1][0], sections_pt[0][1][0])}</p></div>
</div></section>
{_youtube_embed(meta.get('video_url'))}
<article class="art">
  <section class="block"><div class="wrap viz">{thin_note}<div class="statgrid">{stat_grid}</div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><h2 class="sec-label">Momentum &amp; timeline</h2>
      <p class="editorial">Every action of the bout on one axis — momentum runs behind, each tick is
      an event. Click a tick to jump the video to five seconds before it.</p></div>
    <div class="wrap viz"><div class="mtl"><div id="seqTimeline"
      style="position:relative;width:100%;height:100%"></div></div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><h2 class="sec-label">The decisive sequence</h2>
      <p class="editorial">{seq_hint}</p></div>
    <div class="wrap viz"><div class="graph-card seq-card"><canvas id="seqGraph" class="graph-canvas"></canvas>
      <div class="graph-legend" id="seqLegend"></div></div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose">{_prose_html(sections[1:], sections_pt[1:])}</div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><h2 class="sec-label">Rating &amp; significance</h2></div>
    <div class="wrap viz"><div class="sig-cards">
      {sig_card(a, a['name'])}{sig_card(b, b['name'])}
      <div class="sig-card"><div class="k">Method</div><div class="v">{html.escape(meta['method'])}</div></div>
    </div></div></section>
  <div class="divider"></div>
  {_train_this_style(a, b, dossier_slugs)}
</article>
{_FOOTER}
<script src="graph.js"></script><script src="timeline.js"></script><script src="i18n.js"></script>
<script>const BD = {json.dumps(payload, ensure_ascii=False)};
{_BREAKDOWN_JS}</script></body></html>"""
    desc = (f"{win_line}. Interactive transition map, momentum and the decisive sequence — "
            f"every claim traces to an edge you can hover.")
    img = _og_image(f"assets/fighters/{slugify(a['name'])}.jpg")
    return _head(meta["title"], description=desc, path=f"breakdown-{slug}.html", image=img) + body


# ── dossier detail page ──────────────────────────────────────────────────────
_PROFILE_JS = """
// radar fingerprint — same axes as the App analytics tab (pass/control/submission/
// escape/guard/sweep/takedown), auto-scaled so the strongest category fills the web.
(function(){
  const c=document.getElementById('radar'); if(!c) return;
  const labels=P.radar.labels, raw=P.radar.values, N=labels.length;
  const mx=Math.max(0.0001,...raw), vals=raw.map(v=>Math.max(0.03,v/mx));
  const wrap=c.parentElement;
  function draw(){
    // size from the container (capped at the 320px design width), DPR-aware
    const w=Math.min(320,wrap.clientWidth||320), h=w*300/320, s=w/320;
    const dpr=Math.min(devicePixelRatio||1,2);
    c.width=w*dpr;c.height=h*dpr;c.style.width=w+'px';c.style.height=h+'px';
    const x=c.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);
    const cx=160*s,cy=150*s,R=98*s;
    function poly(v,fill,stroke,lw){x.beginPath();labels.forEach((_,i)=>{const ang=-Math.PI/2+i/N*Math.PI*2,r=R*v[i],
      px=cx+Math.cos(ang)*r,py=cy+Math.sin(ang)*r;i?x.lineTo(px,py):x.moveTo(px,py);});x.closePath();
      if(fill){x.fillStyle=fill;x.fill();}x.strokeStyle=stroke;x.lineWidth=lw;x.stroke();}
    [0.25,0.5,0.75,1].forEach(g=>poly(labels.map(()=>g),null,'rgba(255,255,255,.10)',1));
    labels.forEach((_,i)=>{const ang=-Math.PI/2+i/N*Math.PI*2;x.strokeStyle='rgba(255,255,255,.10)';
      x.beginPath();x.moveTo(cx,cy);x.lineTo(cx+Math.cos(ang)*R,cy+Math.sin(ang)*R);x.stroke();});
    poly(vals,'rgba(126,168,255,.20)','#7ea8ff',2);
    x.fillStyle='#cdd2e0';x.font="600 "+Math.max(9,10.5*s)+"px 'Spline Sans Mono',monospace";
    x.textAlign='center';x.textBaseline='middle';
    labels.forEach((l,i)=>{const ang=-Math.PI/2+i/N*Math.PI*2;x.fillText(l,cx+Math.cos(ang)*(R+20*s),cy+Math.sin(ang)*(R+15*s));});
  }
  draw();new ResizeObserver(draw).observe(wrap);
})();
// click a career node → play the first filmed use of that position (P.videos: key→{vid,ts,slug}).
// Same video already loaded → seek via the YT iframe API (postMessage), no new embed. A fresh
// iframe/src swap re-triggers YouTube's pre-roll ad, so only a genuinely different video id
// tears down and rebuilds the player.
var gaDsPlayer=null,gaDsReady=false,gaDsVid=null,gaDsPending=null;
function gaDsSeek(t){
  if(gaDsReady){gaDsPlayer.seekTo(t,true);gaDsPlayer.playVideo();}else gaDsPending=t;
}
window.onYouTubeIframeAPIReady=function(){
  if(gaDsVid&&!gaDsPlayer&&document.getElementById('dsFrame')){
    gaDsPlayer=new YT.Player('dsFrame',{events:{onReady:function(){
      gaDsReady=true;if(gaDsPending!=null){gaDsSeek(gaDsPending);gaDsPending=null;}
    }}});
  }
};
function gaWatch(ref){
  const wrap=document.getElementById('dossierVideo'); if(!wrap||!ref) return;
  wrap.style.display='block';
  const start=Math.max(0,ref.ts|0);
  if(ref.vid===gaDsVid&&gaDsPlayer){
    gaDsSeek(start);
    wrap.scrollIntoView({behavior:'smooth',block:'center'});
    return;
  }
  gaDsVid=ref.vid;gaDsReady=false;gaDsPlayer=null;gaDsPending=null;
  wrap.innerHTML='<div style="position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:inherit">'
    +'<iframe id="dsFrame" src="https://www.youtube-nocookie.com/embed/'+ref.vid+'?start='+start+'&autoplay=1&enablejsapi=1"'
    +' title="Technique footage" frameborder="0" style="position:absolute;inset:0;width:100%;height:100%"'
    +' allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>'
    +(ref.slug?'<p class="graph-hint" style="padding:8px 12px;margin:0">Footage from <a href="breakdown-'+ref.slug+'.html" style="color:var(--ink-2);text-decoration:underline">this bout</a></p>':'');
  wrap.scrollIntoView({behavior:'smooth',block:'center'});
  if(window.YT&&window.YT.Player){
    gaDsPlayer=new YT.Player('dsFrame',{events:{onReady:function(){gaDsReady=true;}}});
  }else if(!window.__gaYtApiLoading){
    window.__gaYtApiLoading=true;
    var tag=document.createElement('script');tag.src='https://www.youtube.com/iframe_api';document.head.appendChild(tag);
  }
}
// career map — positions as nodes, the techniques between them on the stroke ("edge = path").
// Falls back to the legacy every-event-is-a-node graph when an older bundle has no pathGraph.
var CPG = P.pathGraph && P.pathGraph.nodes && P.pathGraph.nodes.length ? P.pathGraph : null;
if (CPG) {
  GAGraph.mountPaths(document.getElementById('careerGraph'), {
    nodes: CPG.nodes, links: CPG.links, paths: CPG.paths, unresolved: CPG.unresolved,
    // §17 — the concentric-ring frame (additive; see the breakdown's own note).
    layout: CPG.layout, rings: CPG.rings, ringCentre: CPG.ringCentre,
    // footage still hangs off the TECHNIQUE, which is now an action on the stroke
    onLinkSelect: l => { if (l && P.videos) { for (const a of (l.actions||[])) { if (P.videos[a.key]) { gaWatch(P.videos[a.key]); return; } } } },
    onSelect: n => { if (n && P.videos && n.stateKey) gaWatch(P.videos[n.stateKey]); },
  });
} else {
  GAGraph.mount(document.getElementById('careerGraph'),{mode:'map',swim:true,pan:true,zoom:true,nodes:P.graph.nodes,links:P.graph.links,
    onSelect:n=>{if(n&&P.videos)gaWatch(P.videos[n.id]);}});
}
const lg=[['guard','Guard'],['pass','Passing'],['control','Control'],['submission','Submission'],['takedown','Takedown'],['transition','Transition']];
document.getElementById('legend').innerHTML=lg.map(([k,l])=>'<span><span class="dot" style="background:'+GAGraph.CAT[k]+'"></span>'+l+'</span>').join('');
// signature frequency
document.getElementById('sigFreq').innerHTML=P.signature.map(s=>{const pct=Math.round(s.pct*100);
  return '<div class="freq-row"><div class="top"><span class="name">'+s.label+'</span><span class="pct">'+pct+'%</span></div>'
    +'<div class="freq-track"><div class="freq-fill" style="width:'+Math.max(6,pct)+'%"></div></div></div>';}).join('');
// linked matches — the METHOD line colored win/loss (draws/NC stay neutral); "result" is
// always "def. <opp>" / "lost to <opp>" / "drew <opp>" (analysis/style_profile.py), so a
// prefix check is enough, no separate outcome field needed. Name stays neutral by request.
document.getElementById('linked').innerHTML=P.bouts.slice(0,3).map(m=>{
  const cls=m.result.indexOf('def. ')===0?'win':(m.result.indexOf('lost to ')===0?'loss':'');
  return '<a class="mcard" href="breakdown-'+m.slug+'.html"><div class="ev">'+(m.year||'')+'</div><div class="op">'+m.result+'</div><div class="rs '+cls+'">'+(m.win_type||'')+'</div></a>';}).join('');
if(document.body.classList.contains('lang-pt')) GALang.set('pt');
"""


def render_profile_page(profile: dict[str, Any]) -> str:
    f = profile["fighter"]
    gender: Gender = f.get("gender")
    fin = profile["finishing"]
    fam = fin.get("submission_family", {})
    sections = profile_narrative(profile)
    sections_pt = profile_narrative(profile, lang="pt")
    rec = f["record"]
    rank = f.get("elo_rank")
    mix = profile["style_mix"]
    # Radar values per axis = the move-mix share, but an axis with NO node populated is
    # assumed by the fighter's Grappling ELO (their overall level) rather than plotted as
    # zero — so unmeasured categories sit at their standing, not as a false weakness.
    pctile = profile["fighter"].get("elo_percentile")
    elo_strength = max(0.1, 1 - (pctile - 1) / 99) if pctile else 0.5  # top% → 0..1
    populated = [mix.get(k, 0.0) for k in _RADAR_AXES if mix.get(k, 0.0) > 0]
    mean_pop = (sum(populated) / len(populated)) if populated else 0.1
    radar_values = []
    for k in _RADAR_AXES:
        v = mix.get(k, 0.0)
        if v <= 0:
            v = round(mean_pop * elo_strength, 3)  # assume by grappler ELO
        radar_values.append(round(v, 3))
    # Career-graph nodes keep the category palette — per-system (constellation) node
    # colouring was tried and removed by owner request 2026-08-26; constellations stay
    # visible through the systems cards, not painted onto the graph.
    payload = {
        "radar": {"labels": _RADAR_LABELS, "values": radar_values},
        "graph": profile["_career_gv"],
        "pathGraph": profile.get("_path_gv") or {"nodes": [], "links": [], "paths": []},
        "signature": profile["signature_techniques"],
        # link each bout to the page that was actually written, not the slug the profile
        # computed — they differ when a pair met twice in one year. Every one of THIS
        # (trusted, or this dossier wouldn't exist) athlete's own bouts trivially has
        # >=1 confident side and so always has a page — but still routed through
        # _bout_href/_SLUG_BY_MATCH (the one source of truth) instead of trusting that
        # invariant blindly; a bout that somehow has none is dropped, not linked broken.
        "bouts": [{**b, "slug": _SLUG_BY_MATCH[b["match_id"]]}
                  for b in profile["bouts"]
                  if _bout_href(b.get("match_id", ""), _SLUG_BY_MATCH)],
        "videos": profile.get("_videos") or {},
    }
    graph_hint = "Drag to pan, scroll to zoom · touch to swim · hover to isolate a pathway"
    if payload["videos"]:
        graph_hint += " · click a position to watch it in a real bout"

    # Decision Flow — second view over the same career, when the athlete has enough
    # observed exchanges to compile one (see analysis/dossier_decision_flow eligibility).
    # Not eligible → the career section stays exactly the single Game Map card it always was.
    game_map_card = f'''
  <div class="graph-card"><canvas id="careerGraph" class="graph-canvas" style="height:440px"></canvas>
    <div class="graph-legend" id="legend"></div></div>
  <p class="graph-hint">{graph_hint}</p>
  <div id="dossierVideo" class="graph-card" style="display:none;margin-top:14px"></div>'''
    df_tabs_html = ""
    df_panel_html = game_map_card
    df_payload_js = ""
    df_head_includes = ""
    df_patterns_raw = profile.get("decision_flow_patterns") or []
    if df_patterns_raw:
        # local: analysis.decision_flow ← analysis.network_metrics ← this module (cycle)
        from analysis.decision_flow import DecisionPattern, PatternEvidence
        from analysis.dossier_decision_flow import build_decision_flow_from_patterns

        # style_profile stores these as plain dicts (ItemCache round-trips through JSON);
        # rebuild the dataclasses the compiler expects. condition_indexes is a tuple there.
        df_patterns = [
            DecisionPattern(
                **{k: v for k, v in p.items() if k != "evidence"},
                evidence=[
                    PatternEvidence(**{**e, "condition_indexes": tuple(e["condition_indexes"])})
                    for e in p.get("evidence", [])
                ],
            )
            for p in df_patterns_raw
        ]
        roots = Counter(p.source_position_key for p in df_patterns if p.source_position_key)
        root_pos_key = roots.most_common(1)[0][0] if roots else "closed guard"
        df_slug = slugify(f["name"])
        df_payload = build_decision_flow_from_patterns(
            athlete_name=f["name"],
            athlete_key=df_slug,
            patterns=df_patterns,
            root_position_key=root_pos_key,
            root_position_label=canonical_label(root_pos_key, root_pos_key.title()),
        )
        if df_payload:
            df_global = f"GA_DECISION_FLOW_{df_slug.upper().replace('-', '_')}"
            df_tabs_html = '''
<div class="game-view-tabs" role="tablist">
  <button role="tab" id="tab-game-map" aria-selected="true" aria-controls="panel-game-map">Game Map</button>
  <button role="tab" id="tab-decision-flow" aria-selected="false" aria-controls="panel-decision-flow">Decision Flow</button>
</div>'''
            df_panel_html = f'''
<div role="tabpanel" id="panel-game-map" aria-labelledby="tab-game-map">{game_map_card}
</div>
<div role="tabpanel" id="panel-decision-flow" aria-labelledby="tab-decision-flow" hidden>
  <div class="flowchart-section dossier-embed">
    <div class="flowchart-stage dossier-embed" id="flowchart-stage-{df_slug}" role="group" aria-label="Decision flow chart"></div>
  </div>
</div>'''
            # _head() emits a bare <body>, so grapple-like.js's dataset auto-init can't fire —
            # boot it explicitly instead of threading body attrs through every page's <head>.
            df_payload_js = f"""
<script>
window.{df_global} = {json.dumps(df_payload, ensure_ascii=False, separators=(',', ':'))};
document.addEventListener('DOMContentLoaded', function(){{
  window.GAGrappleLike.initDecisionFlowTab({json.dumps(df_slug)}, {json.dumps(f['name'])});
}});
</script>"""
            # flowchart.js defines window.GAFlowchart, which grapple-like.js mounts through.
            df_head_includes = (
                '<link rel="stylesheet" href="flowchart.css"/>'
                '<link rel="stylesheet" href="grapple-like.css"/>'
                '<script src="flowchart.js"></script>'
                '<script src="grapple-like.js"></script>'
            )
    sub_lines = []
    for k, v in fam.get("shares", {}).items():
        sub_lines.append(f"{k} {round(v * 100)}%")
    fincards = "".join([
        f'<div class="fincard"><div class="k">Finish rate</div><div class="v sub">{round(fin["finish_rate"] * 100)}%</div><div class="cap">of wins by submission</div></div>',
        f'<div class="fincard"><div class="k">Submission family</div><div class="v">{html.escape(fam.get("dominant") or "—")}</div><div class="cap">{html.escape(", ".join(sub_lines))}</div></div>',
        f'<div class="fincard"><div class="k">Decision rate</div><div class="v">{round(fin["decision_rate"] * 100)}%</div><div class="cap">of decided bouts</div></div>',
        f'<div class="fincard"><div class="k">vs Top-10 Grappling ELO</div><div class="v" style="color:var(--good)">{fin["record_vs_elite"]["wins"]}–{fin["record_vs_elite"]["losses"]}</div><div class="cap">elite opposition</div></div>',
    ])
    # Systems section — community decomposition stashed by build_fighters as
    # _systems (profile_to_dict) + _analogues (compare_profiles rows). Rendered
    # server-side like the fincards; absent data → no section.
    systems_html = ""
    sysd = profile.get("_systems") or {}
    if sysd.get("systems"):
        elos = [s["system_elo"] for s in sysd["systems"] if s.get("system_elo")]
        top_elo = max(elos) if elos else None
        cards = []
        for s in sysd["systems"][:6]:
            strength = ""
            if top_elo and s.get("system_elo"):
                # relative to the athlete's strongest system — never a raw rating
                strength = f'<span class="sys-str">{round(s["system_elo"] / top_elo * 100)}%</span>'
            cards.append(
                f'<div class="syscard"><div class="top"><span class="k">{html.escape(s["name"])}</span>{strength}</div>'
                f'<div class="hub">{html.escape(s["hub"])}</div>'
                f'<div class="meta">{s["size"]} techniques · {s["transition_count"]} internal transitions</div></div>'
            )
        # Dilemma forks (path-to-victory model) — structure only, raw PtV never shown.
        forks = "".join(
            f'<div class="fork"><span class="fk">{html.escape(d["node"])}</span>'
            + '<span class="or">forces</span>'
            + '<span class="or">·</span>'.join(
                f'<span class="fbr">{html.escape(b[0])}</span>'
                for b in d.get("branches", [])[:2]
            )
            + "</div>"
            for d in (profile.get("_dilemmas") or [])[:3]
            if len(d.get("branches", [])) >= 2
        )
        forks_html = (f'<div class="forks"><span class="kicker">Dilemma forks</span>'
                      f'<div class="fork-rows">{forks}</div></div>') if forks else ""
        chips = "".join(
            f'<a class="chip" href="grapple-{slugify(a["athlete"])}.html">{html.escape(a["athlete"])}'
            f'<span class="sim">{round(a["aggregate_similarity"] * 100)}%</span></a>'
            for a in (profile.get("_analogues") or [])[:5]
        )
        ana_html = (f'<div class="ana"><span class="kicker">Grapples most like</span>'
                    f'<div class="chips">{chips}</div></div>') if chips else ""
        sys_prose = next((sec for sec in sections if sec[0] == "The systems"), None)
        prose = (f'<div class="editorial sys-lead"><p>{html.escape(sys_prose[1][0])}</p></div>'
                 if sys_prose else "")
        n = sysd["system_count"]
        hint = ('<p class="graph-hint">System strength relative to the athlete\'s '
                'strongest system</p>') if top_elo else ""
        systems_html = f"""<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">The systems</span>
    <h2 class="h-lg mt16">{n} game{'s' if n != 1 else ''} inside the game</h2></div>
  {prose}
  <div class="sysgrid">{''.join(cards)}</div>
  {hint}{forks_html}{ana_html}
</div></section>"""
    sub_meta = f"<span><b>{rec['wins']}–{rec['losses']}</b> record</span>"
    sub_meta += f"<span><b>{round(f['finish_rate'] * 100)}%</b> finish rate</span>"
    if rank:
        sub_meta += f"<span><b>#{rank}</b> Grappling ELO</span>"
    pctile = f.get("elo_percentile")
    if pctile:
        sub_meta += f"<span><b>Top {pctile}%</b> overall</span>"
    arche = profile.get("archetype") or "Grappler"
    bio = (_bi(sections[0][1][0], sections_pt[0][1][0]) if sections else "")
    # Per-athlete lead background: their photo (assets/fighters/<slug>.jpg) over a
    # name-seeded gradient fallback, desaturated to B&W by the .hero-bg CSS filter.
    slug = f["slug"]
    h1 = sum(ord(c) for c in slug) % 360  # deterministic (hash() is per-process salted)
    h2 = (h1 + 40) % 360
    hero_bg = (f"background-image:url('assets/fighters/{slug}.jpg'),"
               f"linear-gradient(135deg,hsl({h1},38%,16%),hsl({h2},32%,7%))")

    # ELO-adjusted Defense Rate — share of opponents' attempts stuffed, opp-ELO weighted.
    defense = profile.get("_defense") or None
    defense_html = ""
    if defense and defense.get("categories"):
        dcats = [(c, v) for c, v in defense["categories"].items() if v.get("rate") is not None]
        if dcats:
            ov = defense.get("overall")
            ov_card = (
                f'<div class="fincard"><div class="k">Overall defense</div>'
                f'<div class="v" style="color:var(--good)">{round(ov * 100)}%</div>'
                f'<div class="cap">ELO-weighted</div></div>'
            ) if ov is not None else ""
            dcards = "".join(
                f'<div class="fincard"><div class="k">{html.escape(c.title())} defense</div>'
                f'<div class="v">{round(v["rate"] * 100)}%</div>'
                f'<div class="cap">{v["attempts"]} faced · avg opp {round(v["elo_wt"])}</div></div>'
                for c, v in dcats
            )
            defense_html = f"""<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">Defense</span>
    <h2 class="h-lg mt16">{html.escape(_defense_heading(gender))}</h2></div>
  <div class="fingrid">{ov_card}{dcards}</div>
  <p class="graph-hint">Share of opponents' attempts stuffed, each weighted by that
  opponent's Grappling ELO — defending an elite is worth more than defending a novice.</p>
</div></section>"""

    # Counter Moves — highest-value response per position (PtV of where it lands).
    counters = profile.get("_counters") or []
    counters_html = ""
    if counters:
        rows = "".join(
            f'<div class="fork"><span class="fk">{html.escape(cm["technique"])}</span>'
            f'<span class="or">→</span>'
            + '<span class="or">·</span>'.join(
                f'<span class="fbr">{html.escape(c["move"])}'
                + (f' <span class="or">leads to</span> {html.escape(c["leads_to"])}'
                   if c.get("leads_to") else "")
                + '</span>'
                for c in cm["counters"]
            )
            + "</div>"
            for cm in counters
        )
        counters_html = f"""<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow orange">Counter moves</span>
    <h2 class="h-lg mt16">{html.escape(_counters_heading(gender))}</h2></div>
  <div class="forks"><div class="fork-rows">{rows}</div></div>
  <p class="graph-hint">Ranked by Path-to-Victory value of where the response lands.</p>
</div></section>"""

    # Progression — RRB-derived positional movement (analysis/rrb_progression.py). Present
    # only for the ~17/441 athletes whose row clears the corpus's gates; absent is the honest
    # answer for everyone else, not a filler section (root CLAUDE.md).
    progression_html = ""
    prog_sec = next((sec for sec in sections if sec[0] == "Progression"), None)
    prog_sec_pt = next((sec for sec in sections_pt if sec[0] == "Progressão"), None)
    if prog_sec and prog_sec_pt:
        progression_html = f"""<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">Progression</span>
    <h2 class="h-lg mt16">{html.escape(_progression_heading(gender))}</h2></div>
  <div class="editorial"><p>{_bi(prog_sec[1][0], prog_sec_pt[1][0])}</p></div>
</div></section>"""

    body = f"""{_nav('grapple')}
<section class="dossier">
  <div class="hero-bg" style="{hero_bg}"></div>
  <div class="wrap">
  <div class="flex ac g12" style="margin-bottom:22px">
    <a href="grapple-like.html" class="tag" style="text-decoration:none">← Grapple Like</a>
    <span class="kicker">Athlete dossier</span>
  </div>
  <div class="dhead">
    <div class="athlete">
      <span class="arch">● {_bi(arche, archetype_label("pt", arche))}</span>
      <h1>{_name_break(html.escape(f['name']))}</h1>
      <div class="sub">{sub_meta}</div>
      <p class="editorial bio">{bio}</p>
    </div>
    <div class="radar-card"><div class="rt">Style fingerprint</div>
      <div class="radar-wrap"><canvas id="radar" width="320" height="300"></canvas></div></div>
  </div>
</div></section>
<section class="wrap career">
  <div class="sec-head" style="margin-bottom:14px">
    <span class="eyebrow">The system, not the match</span>
    <h2 class="h-lg mt16">One graph for an entire grappling game</h2></div>
  {df_tabs_html}
  {df_panel_html}
</section>
{systems_html}
<section class="mod"><div class="wrap"><div class="mod-grid">
  <div class="mod-intro"><span class="eyebrow">Signature game</span>
    <h2 class="mt16">{html.escape(_signature_heading(gender))}</h2>
    <div class="editorial">{_prose_html([sections[1]], [sections_pt[1]]) if len(sections) > 1 else ''}</div></div>
  <div class="freq" id="sigFreq"></div>
</div></div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">Finishing profile</span>
    <h2 class="h-lg mt16">Where the matches end</h2></div>
  <div class="fingrid">{fincards}</div>
</div></section>
{defense_html}
{counters_html}
{progression_html}
<section class="mod"><div class="wrap">
  <div class="sec-head flex jb ac wrap-fx" style="gap:14px"><div>
    <span class="eyebrow">From abstract to concrete</span>
    <h2 class="h-lg mt16">See the system in action</h2></div>
    <a class="btn" href="breakdowns.html">All breakdowns →</a></div>
  <div class="mgrid" id="linked"></div>
  <p class="graph-hint" style="margin-top:30px">Lead photo via <a href="https://commons.wikimedia.org/" style="color:var(--ink-3);text-decoration:underline">Wikimedia Commons</a> (CC BY) — see <a href="assets/fighters/LICENSES.md" style="color:var(--ink-3);text-decoration:underline">credits</a>.</p>
</div></section>
<section class="sec-pad-sm"><div class="wrap"><div class="appstrip">
  <div style="flex:1">
    <h2 class="h-lg">Grapple like {html.escape(f['name'])}</h2>
    <p class="muted mt8" style="max-width:48ch">Turn this game into a Project in the GrapplingArc app — it maps {html.escape(f['name'].split()[0])}'s signature entries against your own graph and shows exactly which positions to add.</p>
  </div>
  <a class="btn app lg" href="index.html#app">Start this Project →</a>
</div></div></section>
{_FOOTER}
{df_head_includes}
{df_payload_js}
<script src="graph.js"></script><script src="i18n.js"></script>
<script>const P = {json.dumps(payload, ensure_ascii=False)};
{_PROFILE_JS}</script></body></html>"""
    pslug = slugify(f["name"])
    arche = profile.get("archetype") or "grappler"
    desc = (f"How {f['name']} wins, mapped from match data — a {arche} dossier: signature "
            f"entries, response patterns and finishing profile. The system, not the match.")
    return _head("Grapple Like " + f["name"], description=desc,
                 path=f"grapple-{pslug}.html",
                 image=_og_image(f"assets/fighters/{pslug}.jpg")) + body


# ── event (card) pages ───────────────────────────────────────────────────────
def build_events(
    session: Session,
    cache: ItemCache | None = None,
    only: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Returns (GA_EVENTS card rows, [(slug, event profile)]) for cards with ≥3 bouts."""
    rows: list[dict[str, Any]] = []
    details: list[tuple[str, dict[str, Any]]] = []
    ev_matches: dict[str, list[Any]] = {}
    if cache is not None:  # index this-event's bouts once, to hash each event by its contents
        for m in _final_matches(session):
            if m.event:
                ev_matches.setdefault(m.event, []).append(m)
    for name in event_names(session):
        if only is not None and slugify(name) not in only:
            continue   # --only preview: skip build_event_profile (a full-corpus scan per event)
        if cache is not None:
            ms = sorted(ev_matches.get(name, []), key=lambda m: m.id)
            # ponytail: keyed on the bouts (id/seq/result/participants); a bare athlete rename
            # or ELO drift with no bout change self-heals on a --full run.
            eh = item_hash(name, [(m.id, m.sequence, m.year, m.winner_id, m.win_type,
                                   m.athlete_a_id, m.athlete_b_id) for m in ms])
            ep = cache.get_or_compute(
                f"ev:{slugify(name)}", eh, lambda: build_event_profile(name, session))
        else:
            ep = build_event_profile(name, session)
        slug = slugify(name)
        hb = ep.get("headline_bout")
        rows.append({
            "slug": slug, "href": f"event-{slug}.html", "name": name,
            "year": str(ep["year"] or ""), "bouts": ep["bout_count"],
            "finishes": _pct(ep["finish_rate"]),
            "headline": f"{hb['a']} vs {hb['b']}" if hb else "",
            "names": ep["headliners"][:3],
        })
        details.append((slug, ep))
    rows.sort(key=lambda r: (r["year"], r["bouts"]), reverse=True)
    return rows, details


def render_event_page(
    slug: str, ep: dict[str, Any], slug_by_match: dict[str, str] | None = None
) -> str:
    slug_by_match = slug_by_match or {}
    sections = event_narrative(ep)
    sections_pt = event_narrative(ep, lang="pt")
    name = ep["event"]
    tags = ([str(ep["year"])] if ep["year"] else []) + [f"{ep['bout_count']} bouts"]
    if ep["decided"]:
        tags.append(f"{_pct(ep['finish_rate'])} finishes")
    tagrow = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in tags)
    sub_finishes = sum(c for _, c in ep["submissions"])
    stat_cards = "".join(
        f'<div class="sig-card"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{html.escape(str(v))}</div></div>'
        for k, v in [("Bouts", ep["bout_count"]),
                     ("Finishes", f"{ep['finishes']}/{ep['decided']}"),
                     ("Submissions", sub_finishes),
                     ("Athletes", ep["participant_count"])]
    )
    # Wave 8 (render step only — ep["bouts"]/bout_count/stat_cards above stay computed
    # from the FULL card): a bout only gets a card+link here if its breakdown page was
    # actually published (_bout_href is the single source of truth for that).
    visible_bouts = [b for b in ep["bouts"] if _bout_href(b.get("match_id", ""), slug_by_match)]
    omitted = len(ep["bouts"]) - len(visible_bouts)
    bout_cards = "".join(
        f'<a class="mcard" href="{_bout_href(b["match_id"], slug_by_match)}">'
        f'<div class="ev">{html.escape(str(b["year"] or ""))}</div>'
        f'<div class="op">{html.escape(b["a"] + " vs " + b["b"])}</div>'
        f'<div class="rs">{html.escape((b["winner"] or "—") + " · " + b["method"])}</div></a>'
        for b in visible_bouts
    )
    omitted_note = ""
    if omitted:
        n = omitted
        en = (f"{n} bout{'s' if n != 1 else ''} from this card {'are' if n != 1 else 'is'} "
              "already counted in the numbers above and in the site's aggregate stats, "
              "but don't get a card here — there isn't yet enough evidence about either "
              "competitor for an individual reading.")
        pt = (f"{n} luta{'s' if n != 1 else ''} deste evento já {'estão' if n != 1 else 'está'} "
              "contadas nos números acima e nas estatísticas agregadas do site, mas não "
              "aparecem como card aqui — ainda não há evidência suficiente sobre nenhum dos "
              "dois competidores para uma leitura individual.")
        omitted_note = f'<p class="graph-hint">{_bi(en, pt)}</p>'
    body = f"""{_nav('events')}
<section class="art-hero"><div class="wrap">
  <div class="center"><a href="events.html" class="tag" style="text-decoration:none">← Events</a></div>
  <h1 class="art-title">{html.escape(name)}</h1>
  <div class="result-bar">{tagrow}</div>
  <div class="prose"><p class="lead art-sum">{_bi(sections[0][1][0], sections_pt[0][1][0])}</p></div>
</div></section>
<article class="art">
  <section class="block"><div class="wrap viz"><div class="sig-cards">{stat_cards}</div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose">{_prose_html(sections[1:], sections_pt[1:])}</div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><h2 class="sec-label">Every bout</h2></div>
    <div class="wrap viz"><div class="mgrid">{bout_cards}</div>{omitted_note}</div></section>
</article>
{_FOOTER}
<script src="i18n.js"></script></body></html>"""
    ev_desc = (ep.get("headline") or f"{name}: every bout mapped — transition graphs, "
               f"finishes and Grappling-ELO swings.")
    return _head(name, description=ev_desc, path=f"event-{slug}.html") + body


# ── The Ocean (full technique force graph) ───────────────────────────────────
_OCEAN_STYLE = """<style>
.ocean-stage{position:relative;height:calc(100vh - 58px);overflow:hidden;border-top:1px solid var(--line)}
.ocean-canvas{position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none}
.ocean-hud{position:absolute;top:18px;left:18px;z-index:2;max-width:340px;display:flex;flex-direction:column;gap:12px;pointer-events:none}
.ocean-hud>*{pointer-events:auto}
.ocean-h h1{font-size:30px;margin:0;letter-spacing:-.6px}
.ocean-search{width:100%;padding:9px 12px;background:rgba(12,12,17,.85);border:1px solid var(--line);border-radius:10px;color:var(--ink);font-size:13px;font-family:var(--mono)}
.ocean-legend{display:flex;flex-wrap:wrap;gap:6px}
.ocean-chip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;color:var(--ink-2);background:rgba(12,12,17,.8);border:1px solid var(--line);border-radius:20px;padding:3px 9px}
.ocean-chip i{width:9px;height:9px;border-radius:50%;display:inline-block}
.ocean-panel{position:absolute;top:0;right:0;height:100%;width:340px;background:var(--panel);border-left:1px solid var(--line);z-index:3;padding:24px 22px;overflow:auto;box-shadow:-22px 0 44px rgba(0,0,0,.32)}
.ocean-panel[hidden]{display:none}
.ocean-close{position:absolute;top:2px;right:2px;width:44px;height:44px;display:flex;align-items:center;justify-content:center;background:none;border:none;color:var(--ink-3);font-size:23px;cursor:pointer;line-height:1}
.ocean-panel h2{font-size:21px;margin:0 30px 8px 0;letter-spacing:-.3px}
.op-metrics{margin-top:18px;display:flex;flex-direction:column;gap:12px}
.op-metric .op-mh{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:5px}
.op-bar{height:7px;background:#1a1a22;border-radius:5px;overflow:hidden}
.op-fill{height:100%;background:linear-gradient(90deg,var(--blue),var(--orange));border-radius:5px}
.op-sec{font-family:var(--mono);font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--ink-3);margin:18px 0 8px}
.op-tags{display:flex;flex-wrap:wrap;gap:6px}
.muted{color:var(--ink-3);font-size:12px}
.ocean-signals{position:absolute;bottom:18px;left:18px;z-index:2;display:flex;flex-direction:column;gap:10px;max-width:280px;pointer-events:none}
.ocean-signals>*{pointer-events:auto}
.sig-block{background:rgba(12,12,17,.85);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.sig-block h3{font-family:var(--mono);font-size:10.5px;letter-spacing:1px;text-transform:uppercase;color:var(--ink-3);margin:0 0 8px}
.sig-row{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11.5px;padding:3px 0}
.sig-row .arrow{color:var(--ink-3)}
.sig-row .delta{font-family:var(--mono)}
.sig-row .up{color:var(--good)}.sig-row .down{color:var(--bad)}
.sig-note{font-size:10px;color:var(--ink-3);margin-top:6px;line-height:1.4}
@media(max-width:600px){
  .ocean-panel{top:auto;bottom:0;height:auto;max-height:52vh;width:100%;border-left:none;border-top:1px solid var(--line);box-shadow:0 -22px 44px rgba(0,0,0,.4)}
  .ocean-hud{max-width:none;right:18px}
  .ocean-signals{display:none}
}
</style>"""

_OCEAN_BODY = """<section class="ocean-stage">
  <canvas id="oceanGraph" class="ocean-canvas"></canvas>
  <div class="ocean-hud">
    <div class="ocean-h"><h1>The Ocean</h1><p class="muted" id="oceanMeta"></p></div>
    <input id="oceanSearch" class="ocean-search" placeholder="find a technique…" autocomplete="off"/>
    <div id="oceanLegend" class="ocean-legend"></div>
  </div>
  <div id="oceanSignals" class="ocean-signals"></div>
  <aside id="oceanPanel" class="ocean-panel" hidden>
    <button id="oceanClose" class="ocean-close" aria-label="close">&times;</button>
    <h2 id="opName"></h2><div id="opMeta"></div>
    <div id="opMetrics" class="op-metrics"></div>
    <div id="opNeighbours"></div><div id="opEdges"></div><div id="opUndrawn"></div>
  </aside>
</section>"""

_OCEAN_JS = """
var O = window.GA_OCEAN || {nodes:[],links:[],regions:[],meta:{},markov:{},elo:{}};
var byId = {}; O.nodes.forEach(function(n){ byId[n.id]=n; });
var __pg = O.pathGraph && O.pathGraph.stats;
// §5d counter: `paths` is what draws with its own stroke, `foldedGroups` is how many
// category strokes stand in for everything past the budget — nothing here was dropped,
// `foldedVariants` (of `variants` total) is still there, just folded. §oceano's SECOND ceiling
// (docs §12, 2026-09-01): `undrawn.groups` of those fold groups don't even get a stroke — they
// still ride in `pathGraph.folded` (`drawn:false`) for onSelect() to list, per state, below.
var __ud = __pg && __pg.undrawn;
document.getElementById('oceanMeta').textContent = __pg
  ? (__pg.states+' positions · '+__pg.paths+' technique paths drawn individually · '
     +__pg.foldedGroups+' folded groups covering '+__pg.foldedVariants+' more (of '+__pg.variants+' total)'
     +(__ud && __ud.groups ? ' · '+__ud.groups+' paths not drawn' : '')+' · '
     +__pg.segments+' strokes · '+__pg.sharedActionPct+'% of the ink is shared')
  : ((O.meta.positions||0)+' of '+(O.meta.total_positions||O.meta.positions||0)+' techniques (top slice) · '+
     (O.meta.transitions||0)+' transitions · '+(O.regions||[]).length+' regions');
document.getElementById('oceanLegend').innerHTML = (O.regions||[]).map(function(r){
  return '<span class="ocean-chip"><i style="background:'+r.color+'"></i>'+r.name+'</span>'; }).join('');
var panel = document.getElementById('oceanPanel');
// The map is now the corpus's PATHS: a node is a position, a stroke is the run of techniques
// that gets from one to the next. The panel/search/regions still key on node_key — a state
// node carries it as `stateKey`, which is what onSelect/locate() resolve through. Older
// bundles with no pathGraph fall back to the force graph so a stale page never renders empty.
var PGO = O.pathGraph && O.pathGraph.nodes && O.pathGraph.nodes.length ? O.pathGraph : null;
var g = PGO
  ? GAGraph.mountPaths(document.getElementById('oceanGraph'), {
      nodes: PGO.nodes, links: PGO.links, paths: PGO.paths, unresolved: PGO.unresolved,
      // the HUD floats OVER the canvas — reserve the space it really occupies so the map is
      // never fitted underneath it. MEASURED, not a constant: the region legend grows a row
      // per detected system, and below 760px the HUD is a full-width top band instead of a
      // left column (see the .ocean-hud media query), so a fixed number is wrong in both
      // directions. Re-read on every fit, which is where a resize lands.
      inset: function(w){
        var h = document.querySelector('.ocean-hud');
        var r = h ? h.getBoundingClientRect() : {right:0, bottom:0};
        return w >= 760 ? {left: Math.round(r.right) + 18} : {top: Math.round(r.bottom) + 18};
      },
      onSelect: onSelect, onLinkSelect: onLinkSelect})
  : GAGraph.mount(document.getElementById('oceanGraph'), {mode:'map',
  // x/y/imp: the importance-radial seed from export/ocean.py — deterministic across loads,
  // and imp (0..1) also scales gravity pull so the big central nodes stay central (graph.js).
  nodes:O.nodes.map(function(n){return {id:n.id,label:n.label,cat:n.type,size:n.size,color:n.color,
    x:n.x,y:n.y,imp:n.imp};}),
  links:O.links, onSelect:onSelect,
  pan:true, zoom:true, zoomOnSelect:true,        // drag to pan, wheel to zoom, click zooms in
  collide:true, swim:true,                       // no node overlap; mobile = zoomed-in thick-water nav
  charge:7000, linkDist:64, gravity:0.0009, bounded:false});  // spread out, no border tension
// Markov backbone + ELO distribution — the two corpus-wide signals the map itself can't show.
(function renderSignals(){
  var host = document.getElementById('oceanSignals');
  var M = O.markov || {}, E = O.elo || {};
  var mv = (M.top||[]).slice(0, 6).map(function(t){
    return '<div class="sig-row"><span>'+t.from+' <span class="arrow">&rarr;</span> '+t.to+'</span>'+
      '<span class="muted">'+Math.round((t.prob||0)*100)+'%</span></div>'; }).join('');
  // ratios cluster tight (the corpus's own real spread, ~±5%) — a signed delta reads that
  // honestly; a proportional bar would make every row look the same length.
  var ev = (E.buckets||[]).map(function(b){
    var pct = Math.round((b.ratio - 1) * 100), cls = pct >= 0 ? 'up' : 'down';
    return '<div class="sig-row"><span>'+b.type+'</span><span class="delta '+cls+'">'+
      (pct >= 0 ? '▲ +' : '▼ ')+pct+'%</span></div>'; }).join('');
  host.innerHTML =
    (mv ? '<div class="sig-block"><h3>Markov backbone</h3>'+mv+
      '<div class="sig-note">'+(M.n_bouts||0)+' bouts · action-level, corpus-wide (Lamas et al. 2024 states)</div></div>' : '') +
    (ev ? '<div class="sig-block"><h3>Grappling ELO · relative spread</h3>'+ev+
      '<div class="sig-note">ratio vs. corpus mean, athlete graphs only — never a raw rating</div></div>' : '');
})();
function bar(title, m){
  if(!m) return '';
  var top = 100 - m.pct;
  var note = (m.ratio && m.ratio>0) ? (' · ×'+m.ratio+' avg') : '';
  return '<div class="op-metric"><div class="op-mh"><span>'+title+'</span>'+
    '<span class="muted">top '+top+'%'+note+'</span></div>'+
    '<div class="op-bar"><div class="op-fill" style="width:'+Math.max(3,m.pct)+'%"></div></div></div>';
}
function hidePanel(){
  var s=document.getElementById('oceanSearch'), lg=document.getElementById('oceanLegend');
  panel.hidden=true; if(s) s.style.display=''; if(lg) lg.style.display='';
}
// a stroke selected: name the run of techniques and how many chains share it. No metrics —
// those are a POSITION's, and this is what happens between two of them.
function onLinkSelect(link){
  if(!link){ hidePanel(); return; }
  var s=document.getElementById('oceanSearch'), lg=document.getElementById('oceanLegend');
  if(s){ s.style.display='none'; s.blur(); }
  if(lg){ lg.style.display='none'; }
  // §5d — a folded category stroke stands in for `variantCount` real variants; selecting it
  // expands the panel to list every one of them (unabridged, `folded.variants`), never just a
  // count. The map itself keeps drawing ONE thicker stroke — the expansion is the reading, not
  // a redraw.
  document.getElementById('opUndrawn').innerHTML = '';  // that section is a NODE's, not a stroke's
  if(link.folded){
    var f = link.folded;
    document.getElementById('opName').textContent = f.label;
    document.getElementById('opMeta').innerHTML =
      '<span class="muted">'+f.variantCount+' folded variant'+(f.variantCount===1?'':'s')+
      ' · '+f.count+' occurrence'+(f.count===1?'':'s')+' total</span>';
    document.getElementById('opMetrics').innerHTML = '';
    document.getElementById('opNeighbours').innerHTML =
      '<div class="op-sec">Folded variants</div><div class="op-tags">'+
      (f.variants||[]).map(function(v){
        return '<span class="tag">'+v.actions.join(' → ')+
          (v.count>1?' ×'+v.count:'')+'</span>'; }).join('')+'</div>';
    document.getElementById('opEdges').innerHTML = '';
    panel.hidden=false;
    return;
  }
  var acts=(link.actions||[]).map(function(a){
    return '<span class="tag"'+(a.inferred?' style="opacity:.6;border-style:dashed"':'')+'>'+a.label+'</span>'; }).join('');
  document.getElementById('opName').textContent = (link.actions||[]).map(function(a){return a.label;}).join(' → ') || 'Transition';
  document.getElementById('opMeta').innerHTML =
    '<span class="muted">seen '+(link.count||1)+'× · '+((link.pathIds||[]).length)+
    ' sequence'+(((link.pathIds||[]).length)===1?'':'s')+' share this stroke</span>';
  document.getElementById('opMetrics').innerHTML = '<div class="op-tags">'+acts+'</div>';
  document.getElementById('opNeighbours').innerHTML =
    (link.actions||[]).some(function(a){return a.inferred;})
      ? '<div class="op-sec">Note</div><div class="muted">A dashed technique was never logged anywhere in the corpus — the model read it from the gap.</div>' : '';
  document.getElementById('opEdges').innerHTML = '';
  panel.hidden=false;
}
function onSelect(node){
  var s=document.getElementById('oceanSearch'), lg=document.getElementById('oceanLegend');
  var key = node ? (node.stateKey || node.id) : null;
  if(!node || !byId[key]){ hidePanel(); return; }
  if(s){ s.style.display='none'; s.blur(); }   // hide search + region legend while a node is focused
  if(lg){ lg.style.display='none'; }
  var n = byId[key], mt = n.metrics||{};
  var region = ((O.regions||[])[n.region]||{}).name || 'Unclustered';
  var nb = (n.neighbours||[]).map(function(x){var t=byId[x.node_key];
    return '<span class="tag">'+(t?t.label:x.node_key)+'</span>';}).join('');
  var outs = O.links.filter(function(e){return e.from===n.id;}).map(function(e){
    var t=byId[e.to];return t?t.label:e.to;}).slice(0,8).join(', ');
  if(PGO){  // in the path model "leads to" is the TECHNIQUE that gets you there, not the node
    var seen={}, runs=[];
    PGO.links.forEach(function(l){
      var src=PGO.nodes.filter(function(x){return x.id===l.from;})[0];
      if(!src||src.stateKey!==n.id) return;
      // §5d: a folded stroke carries no `actions[]` — its own category label ("Submissions
      // ×4") is the text to show, same as any other run.
      var txt=l.folded ? l.label : (l.actions||[]).map(function(a){return a.label;}).join(' → ');
      if(txt&&!seen[txt]){ seen[txt]=1; runs.push({t:txt,w:l.count||1}); }
    });
    runs.sort(function(x,y){return y.w-x.w;});
    outs = runs.slice(0,8).map(function(r){return r.t;}).join(', ');
  }
  document.getElementById('opName').textContent = n.label;
  document.getElementById('opMeta').innerHTML =
    '<span class="tag" style="border-color:'+n.color+';color:'+n.color+'">'+region+'</span> '+
    '<span class="muted">'+n.type+' · seen '+n.occ+'×</span>';
  document.getElementById('opMetrics').innerHTML =
    bar('Frequency',mt.frequency)+bar('Centrality',mt.centrality)+bar('Bridging',mt.bridging)+
    bar('Favorability',mt.favorability)+bar('Effectiveness',mt.effectiveness);
  document.getElementById('opNeighbours').innerHTML = nb ? '<div class="op-sec">Similar positions</div><div class="op-tags">'+nb+'</div>' : '';
  document.getElementById('opEdges').innerHTML = outs ? '<div class="op-sec">Leads to</div><div class="muted">'+outs+'</div>' : '';
  // Ocean's second ceiling (docs §12, 2026-09-01): a fold group past `max_fold_groups` gets no
  // stroke, but its data still rode in `pathGraph.folded` (`drawn:false`). Reveal it here, on
  // the state it touches — no re-layout needed, `folded[i].source`/`.target` already carry the
  // point id this node draws at.
  var ud = '';
  if(PGO){
    var selPoint = PGO.nodes.filter(function(x){return x.stateKey===n.id;})[0];
    var pid = selPoint ? selPoint.id : null;
    var undrawn = pid ? (PGO.folded||[]).filter(function(f){
      return f.drawn===false && (f.source===pid || f.target===pid); }) : [];
    if(undrawn.length){
      // `f.label` already carries its own variant count ("Submissions ×3"); only add the
      // occurrence total when it says something the label doesn't (more than one occurrence).
      ud = '<div class="op-sec">Not drawn ('+undrawn.length+')</div><div class="muted">'+
        undrawn.map(function(f){return f.label+(f.count>1?' ('+f.count+'×)':'');}).join(', ')+
        '</div>';
    }
  }
  document.getElementById('opUndrawn').innerHTML = ud;
  panel.hidden=false;
}
document.getElementById('oceanClose').addEventListener('click', function(){ panel.hidden=true; g.select(null); });
function locate(){
  var q=(document.getElementById('oceanSearch').value||'').toLowerCase().trim(); if(!q) return;
  // search the DRAWN map first (a position that is on screen is the one a click can find);
  // fall back to the metric index for a technique that is now an action on a stroke.
  if(PGO){
    var pn = PGO.nodes.filter(function(n){return n.label&&n.label.toLowerCase().indexOf(q)>=0&&n.stateKey;})
      .sort(function(a,b){return (b.size||1)-(a.size||1);})[0];
    if(pn){ g.select(pn.stateKey); return; }
  }
  var hit = O.nodes.filter(function(n){return n.label.toLowerCase().indexOf(q)>=0;})
    .sort(function(a,b){return (b.metrics.centrality.pct)-(a.metrics.centrality.pct);})[0];
  if(hit) g.select(hit.id);
}
var os=document.getElementById('oceanSearch');
os.addEventListener('change', locate);
os.addEventListener('keydown', function(e){ if(e.key==='Enter') locate(); });
"""


def render_ocean_page() -> str:
    """The Ocean — full-screen technique force graph, region legend, search, node dialog."""
    return (
        _head("The Ocean", description="The global grappling position map — every technique as a "
              "node, transitions as edges, clustered into regions with centrality, bridging and "
              "effectiveness metrics.", path="the-ocean.html")
        + _OCEAN_STYLE + _nav("ocean") + _OCEAN_BODY + _FOOTER +
        '<script src="graph.js"></script><script src="i18n.js"></script>'
        '<script src="ocean-data.js"></script><script>' + _OCEAN_JS + "</script></body></html>"
    )


# ── orchestration ────────────────────────────────────────────────────────────
def _js_file(var: str, data: Any) -> str:
    return f"/* generated by export.site_data — do not edit */\nwindow.{var} = {json.dumps(data, ensure_ascii=False)};\n"


_LEGACY_BRAND_LOCKUP = (
    '<a class="brand" href="index.html"><span class="mark">GA</span>'
    'Grappling<span class="o">Arc</span></a>'
)
_PREVIOUS_BRAND_LOCKUP = (
    '<a class="brand" href="index.html" aria-label="GrapplingArc">'
    '<img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/>'
    '<span class="brand-wordmark">GrapplingArc</span></a>'
)
_BRAND_LOCKUP = (
    '<a class="brand" href="index.html" aria-label="GrapplingArc">'
    '<img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/>'
    '<span class="brand-wordmark">Grappling'
    '<span class="brand-wordmark-accent">Arc</span></span></a>'
)
_DEFAULT_BRAND_IMAGE_RE = re.compile(
    r'(<meta (?:property="og:image"|name="twitter:image") content="[^"]*?)logo\.svg("/>)'
)
_LEGACY_ORB_RE = re.compile(r'(?m)^[ \t]*<div class="orb">GA</div>\r?\n?')


def _migrate_branding_html(source: str) -> str:
    migrated = source.replace(_LEGACY_BRAND_LOCKUP, _BRAND_LOCKUP)
    migrated = migrated.replace(_PREVIOUS_BRAND_LOCKUP, _BRAND_LOCKUP)
    migrated = migrated.replace(
        '<link rel="icon" type="image/svg+xml" href="logo.svg"/>',
        '<link rel="icon" type="image/svg+xml" href="brand-mark.svg"/>',
    )
    migrated = _DEFAULT_BRAND_IMAGE_RE.sub(r"\1brand-og.png\2", migrated)
    return _LEGACY_ORB_RE.sub("", migrated)


def migrate_branding(out: Path) -> dict[str, int]:
    """Surgically update branding in existing generated detail pages; never query or prune."""
    if not out.is_dir():
        raise FileNotFoundError(f"Site output directory does not exist: {out}")
    pages = {
        *out.glob("breakdown-*.html"),
        *(page for page in out.glob("grapple-*.html") if page.name != "grapple-like.html"),
        *out.glob("event-*.html"),
    }
    ocean = out / "the-ocean.html"
    if ocean.is_file():
        pages.add(ocean)
    if not pages:
        raise ValueError(f"No generated detail pages found in {out}")

    changed = 0
    for page in sorted(pages):
        source = page.read_bytes().decode("utf-8")
        migrated = _migrate_branding_html(source)
        if migrated != source:
            page.write_bytes(migrated.encode("utf-8"))
            changed += 1
    return {"scanned": len(pages), "changed": changed}


def export_site(session: Session, out: Path, full: bool = False,
                only: frozenset[str] | None = None) -> dict[str, int]:
    """Regenerate the site bundle into ``out``.

    ``only`` is a PREVIEW mode: build and render just these detail-page slugs (breakdown /
    dossier / event, matched on the slug). It exists because a full run is a ~10-12 min N+1
    over remote Supabase (see the ``site-export-perf-campaign`` skill) and iterating on a
    renderer against that loop is not workable. The globals it writes are PARTIAL by
    construction, so ``main()`` refuses to point it at the real site directory — a preview must
    never be mistaken for the bundle. ``only=frozenset()`` builds no detail page at all, which
    is the cheapest way to regenerate just ``the-ocean.html`` + ``ocean-data.js``.
    """
    from time import perf_counter as _pc

    _AVAILABLE_IMAGES.clear()
    _AVAILABLE_IMAGES.update(
        f"assets/fighters/{p.name}" for p in (out / "assets/fighters").glob("*.jpg")
    )

    def _phase(label: str, t0: float) -> float:
        logger.info("  [export] %s: %.1fs", label, _pc() - t0)
        return _pc()

    out.mkdir(parents=True, exist_ok=True)
    _ARCH_CACHE.clear()  # fresh archetype reads per export run
    # Load every match once → the build phases' per-athlete lookups (get_matches_for_athlete,
    # called ~5×/fighter) hit memory instead of a remote query each. Cleared after the builds.
    from db.repository import clear_match_cache, prime_match_cache
    prime_match_cache(session)
    _prime_arch_cache(session)
    # Prune stale generated detail pages so hidden fighters / dropped bouts don't orphan
    # (keep the hand-written static grapple-like.html index).
    for old in (*out.glob("breakdown-*.html"), *out.glob("grapple-*.html"),
                *out.glob("event-*.html")):
        if old.name != "grapple-like.html":
            old.unlink()
    cache_dir = Path(__file__).resolve().parent.parent / ".export_cache"
    # A --only preview computes a HANDFUL of items. Saving that back would REPLACE the shared
    # cache with those few and turn the next real export into a cold 10-12 min run, so a
    # preview reads the cache and never writes it.
    cache_dir = _PREVIEW_CACHE_DIR if only is not None else cache_dir
    bd_cache = ItemCache(cache_dir / "breakdowns.json", full=full)
    ft_cache = ItemCache(cache_dir / "fighters.json", full=full)
    ev_cache = ItemCache(cache_dir / "events.json", full=full)
    _t = _pc()
    # Wave 8 publish-confidence gate — computed once, before any page-list build, off the
    # FULL corpus (see _compute_trusted_athletes). Everything this touches downstream is
    # page/href emission only; GA_OCEAN/GA_ELO/GA_EVENTS counts and the corpus-wide
    # transition network stay unfiltered (see build_breakdowns/build_fighters docstrings).
    trusted = frozenset(_compute_trusted_athletes(session))
    withheld = _withheld_athlete_ids(session)
    _t = _phase(f"compute_trusted_athletes ({len(trusted)} trusted, {len(withheld)} withheld)", _t)
    rows, full_bds, featured, omitted_bouts = build_breakdowns(
        session, cache=bd_cache, trusted=trusted, withheld=withheld, only=only)
    bd_cache.save()
    _t = _phase(f"build_breakdowns ({bd_cache.hits} cached, {bd_cache.misses} rebuilt)", _t)
    fighters, details = build_fighters(session, cache=ft_cache, trusted=trusted, only=only)
    ft_cache.save()
    _t = _phase(f"build_fighters ({ft_cache.hits} cached, {ft_cache.misses} rebuilt)", _t)
    events, event_details = build_events(session, cache=ev_cache, only=only)
    ev_cache.save()
    _t = _phase(f"build_events ({ev_cache.hits} cached, {ev_cache.misses} rebuilt)", _t)
    elo = build_elo(session)
    _t = _phase("build_elo", _t)

    bd_js = _js_file("GA_BREAKDOWNS", rows)
    bd_js += f"window.GA_FEATURED = {json.dumps(featured, ensure_ascii=False)};\n"
    bouts_total = len(full_bds) + omitted_bouts
    transparency = {
        "bouts_total": bouts_total, "bouts_published": len(full_bds),
        "bouts_hidden": omitted_bouts, "trusted_athletes": len(trusted),
        "min_confidence_rd": SITE_MIN_CONFIDENCE_RD,
        "note_en": (
            f"{omitted_bouts} of {bouts_total} bouts in the corpus don't have their own "
            "breakdown page — not because the data is missing, but because there isn't "
            "yet enough evidence about either competitor for an individual reading. "
            "They're still counted in every aggregate stat, chart and rating on this site."
        ),
        "note_pt": (
            f"{omitted_bouts} das {bouts_total} lutas do acervo não têm página própria — "
            "não porque falte o dado, mas porque ainda não há evidência suficiente sobre "
            "nenhum dos dois competidores para uma leitura individual. Elas continuam "
            "contadas em toda estatística, gráfico e rating agregado deste site."
        ),
    }
    bd_js += f"window.GA_TRANSPARENCY = {json.dumps(transparency, ensure_ascii=False)};\n"
    (out / "breakdowns-data.js").write_text(bd_js, encoding="utf-8")
    (out / "fighters-data.js").write_text(_js_file("GA_FIGHTERS", fighters), encoding="utf-8")
    (out / "events-data.js").write_text(_js_file("GA_EVENTS", events), encoding="utf-8")
    (out / "elo-data.js").write_text(_js_file("GA_ELO", elo), encoding="utf-8")

    from analysis.ocean import build_ocean
    ocean = build_ocean(session)
    # The Ocean's own "edge = path" map: the WHOLE public corpus, actors collapsed (A's mount
    # and B's mount are the same corpus fact here — this is the technique space, not a bout).
    # ADDITIVE to GA_OCEAN: `nodes`/`links`/`regions`/`metrics` are untouched, so the panel,
    # the search and the region legend keep reading exactly what they always read; the new
    # `pathGraph` is what the canvas draws, and its state nodes carry `stateKey` (the node_key)
    # so the panel lookup still resolves.
    # §5d superseded the old static `min_count=2` drop (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md
    # §FASE 5d) — hiding a path hides the object of study (§10.7), and `min_count=2` did exactly
    # that: 391/2370 paths kept, most of the long chains that carry the branching gone with them.
    # `path_payload`'s own default budget (~60 variants, ranked by support/strength) now decides
    # what draws individually; the corpus still bundles in under a second regardless, so this was
    # never a perf gate. Everything past the budget FOLDS into a category stroke instead of
    # dropping — see `path_payload`'s docstring and `analysis.corpus_paths._fold_overflow`.
    # `max_fold_groups` is the Ocean's SECOND ceiling (only this caller sets it — a dossier/
    # breakdown never folds enough to need one): 877 fold groups measured over the full corpus
    # would still put 937 strokes on one canvas, more than the old static gate it replaced.
    # Nothing is dropped — every group past the budget rides in `pathGraph.folded` flagged
    # `drawn=False`, and `pathGraph.stats.undrawn` is the aggregate the meta line reads.
    ocean["pathGraph"] = path_payload(
        aggregate_bouts(_corpus_bouts(session), collapse_actors=True),
        max_fold_groups=OCEAN_FOLD_GROUP_BUDGET,
    )
    (out / "ocean-data.js").write_text(_js_file("GA_OCEAN", ocean), encoding="utf-8")
    (out / "the-ocean.html").write_text(render_ocean_page(), encoding="utf-8")
    _t = _phase("build_ocean + data.js", _t)
    clear_match_cache()  # renders below use precomputed data; don't leak the cache past the builds

    # per-match detail pages (attach archetypes + adapted graph for the template)
    dossier_slugs = frozenset(details)  # fighters that actually have a Grapple Like dossier
    slow = ("", 0.0)
    for slug, bd in full_bds:
        _s = _pc()
        bd["_arch_a"] = next((r["a"]["style"] for r in rows if r["id"] == slug), "")
        bd["_arch_b"] = next((r["b"]["style"] for r in rows if r["id"] == slug), "")
        bd["transition_graph_gv"] = _to_graphview(bd["transition_graph"])
        (out / f"breakdown-{slug}.html").write_text(
            render_breakdown_page(slug, bd, dossier_slugs), encoding="utf-8")
        if _pc() - _s > slow[1]:
            slow = (slug, _pc() - _s)
    logger.info("  [export] render breakdowns: %.1fs (slowest %s %.2fs)", _pc() - _t, *slow)
    _t = _pc()

    # per-fighter dossiers (reuse the profile + career graph computed above)
    slow = ("", 0.0)
    for slug, d in details.items():
        _s = _pc()
        profile = d["profile"]
        profile["_career_gv"] = d["career"]
        profile["_path_gv"] = d.get("path_graph") or {"nodes": [], "links": [], "paths": []}
        profile["_systems"] = d.get("_systems") or {}
        profile["_analogues"] = d.get("analogues") or []
        profile["_videos"] = d.get("_videos") or {}
        profile["_counters"] = d.get("_counters") or []
        profile["_defense"] = d.get("_defense") or None
        profile["_progression"] = d.get("_progression") or None
        (out / f"grapple-{slug}.html").write_text(
            render_profile_page(profile), encoding="utf-8")
        if _pc() - _s > slow[1]:
            slow = (slug, _pc() - _s)
    logger.info("  [export] render dossiers: %.1fs (slowest %s %.2fs)", _pc() - _t, *slow)
    _t = _pc()

    # per-event card pages
    for slug, ep in event_details:
        (out / f"event-{slug}.html").write_text(
            render_event_page(slug, ep, _SLUG_BY_MATCH), encoding="utf-8")
    _t = _phase("render events", _t)

    # robots.txt + sitemap.xml (acquisition baseline — the site was invisible to crawlers).
    static_pages = ["index.html", "breakdowns.html", "events.html", "grapple-like.html",
                    "the-data.html", "the-ocean.html"]
    urls = static_pages + [f"breakdown-{s}.html" for s, _ in full_bds] \
        + [f"grapple-{s}.html" for s in details] + [f"event-{s}.html" for s, _ in event_details]
    locs = "\n".join(f"  <url><loc>{SITE_BASE}/{u}</loc></url>" for u in urls)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{locs}\n</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n", encoding="utf-8")

    return {"breakdowns": len(full_bds), "fighters": len(details),
            "events": len(event_details), "elo": sum(len(rows) for rows in elo.values()),
            "ocean": len(ocean["nodes"])}


def run(out: Path, full: bool = False, only: frozenset[str] | None = None) -> int:
    from db.base import db_session
    with db_session() as session:
        counts = export_site(session, out, full=full, only=only)
    logger.info("Generated %d breakdowns, %d dossiers, %d events, %d ELO rows, %d ocean nodes → %s",
                counts["breakdowns"], counts["fighters"], counts["events"],
                counts["elo"], counts["ocean"], out)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Generate the design site's data + detail pages")
    ap.add_argument("--out", type=Path, help="site output dir")
    ap.add_argument("--full", action="store_true",
                    help="ignore the incremental cache and rebuild every page")
    ap.add_argument("--branding-only", action="store_true",
                    help="update branding in existing generated detail pages without the DB")
    ap.add_argument("--only", metavar="SLUG", nargs="*",
                    help="PREVIEW: build only these detail-page slugs (breakdown/dossier/event). "
                         "Pass with no value to build the ocean + globals alone. Requires an "
                         "explicit --out — the bundle it writes is partial.")
    args = ap.parse_args()
    if args.branding_only:
        if args.out is None:
            ap.error("--branding-only requires an explicit --out directory")
        counts = migrate_branding(args.out)
        logger.info("Updated branding in %d/%d generated detail pages → %s",
                    counts["changed"], counts["scanned"], args.out)
        return 0
    only = None
    if args.only is not None:
        if args.out is None or args.out.resolve() == _DEFAULT_OUT.resolve():
            ap.error("--only writes a PARTIAL bundle; give it a scratch --out, never the site")
        only = frozenset(args.only)
    return run(args.out or _DEFAULT_OUT, full=args.full, only=only)


if __name__ == "__main__":
    raise SystemExit(main())
