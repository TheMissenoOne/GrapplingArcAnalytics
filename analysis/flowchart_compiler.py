"""Compile decision patterns (+ optional expert input) into a FlowchartSpec.

Pure + deterministic: same inputs → same spec. No DB, no I/O. The payload
schema lives here as plain dicts (site/App consume JSON, not Python objects).

Node kinds: root-position | position | athlete-action | opponent-condition |
response | outcome | portal. Edges: action | reaction | response | results-in |
returns-to | portal.

Scoring (used only for ranking/truncation, never stored):
    support*0.35 + confidence*0.25 + outcome_value*0.20
    + athlete_specificity*0.10 + ontology_relevance*0.10

See docs/decision_flows_contract.md for the authoritative payload spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

NodeKind = Literal[
    "root-position", "position", "athlete-action",
    "opponent-condition", "response", "outcome", "portal",
]
EdgeKind = Literal["action", "reaction", "response", "results-in", "returns-to", "portal"]

_OBSERVED: Final = "observed"
_EXPERT: Final = "expert"
_HYBRID: Final = "hybrid"
SourceKind = Literal["observed", "expert", "hybrid"]

# Bump on any change to the compiled payload shape or node/edge semantics.
# 1.3.0 — two changes, both semantic:
#   * conditions now come from CHAIN conditioning (the opponent's move between two of the
#     athlete's own moves), so a flowchart shows roughly 5x the conditions it used to;
#   * branches carry support / matchCount / opponentCount so a reader can tell a pattern
#     from a single memorable exchange.
# Also invalidates the site-export cache, which is what forces regeneration.
COMPILER_VERSION: Final = "1.3.0"


@dataclass(frozen=True)
class FlowchartDefinition:
    key: str
    title: str
    athlete_key: str
    root_position_key: str
    system_key: str | None = None
    max_depth: int = 4
    max_primary_branches: int = 6
    max_conditions_per_action: int = 4
    max_responses_per_condition: int = 3
    minimum_support: int = 1
    published: bool = True


@dataclass(frozen=True)
class ExpertBranch:
    """Expert-authored (action, condition, response) triple.

    condition_key lives in the ``cond:`` space; response_key is a technique
    node_key (or ``None`` for 'denied').
    """

    action_key: str
    condition_key: str | None
    response_key: str | None = None
    source: SourceKind = _EXPERT


@dataclass(frozen=True)
class FlowchartNode:
    id: str
    key: str
    kind: NodeKind
    title: str
    subtitle: str | None = None
    # BJJ event type (sweep/submission/pass/…) — colours the node by what the move IS,
    # not by where it sits in the tree. Mirrors the app's category legend.
    category: str | None = None
    # Measured cue lines, in reading order. Never authored instruction — every line is
    # something the match data says (counts, finish rate, where it led, when it happened).
    detail: list[str] = field(default_factory=list)
    source: SourceKind = _OBSERVED
    support: int | None = None
    match_count: int | None = None
    success_rate: float | None = None
    confidence: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    warning: bool = False
    url: str | None = None


@dataclass(frozen=True)
class FlowchartEdge:
    id: str
    source: str
    target: str
    kind: EdgeKind
    label: str | None = None


@dataclass(frozen=True)
class FlowchartBranch:
    id: str
    action_key: str
    score: float
    conditions: list[str]
    depth: int = 1
    # How much evidence stands behind this branch. `score` ranks branches against each other
    # and is explicitly not stored as a meaning; these are the raw counts a reader needs to
    # judge whether a branch is a pattern or a single memorable exchange.
    # None on an expert-only branch — it has no observations, and a zero would read as
    # "observed zero times" rather than "not derived from observation".
    support: int | None = None
    match_count: int | None = None
    opponent_count: int | None = None


@dataclass(frozen=True)
class FlowchartSpec:
    schema_version: int
    key: str
    title: str
    athlete: str
    athlete_label: str
    root_position_key: str
    root_position_label: str
    nodes: list[FlowchartNode]
    edges: list[FlowchartEdge]
    branches: list[FlowchartBranch]
    exchanges: int = 0
    matches: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    compiler_version: str = COMPILER_VERSION
    layout_version: int | None = None
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _mmss(seconds: int | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    return f"{seconds // 60}:{seconds % 60:02d}"


def _opponent_from_slug(match_slug: str, athlete_key: str) -> tuple[str | None, str | None]:
    """('Kaynan Duarte', '25') from 'gordon-ryan-vs-kaynan-duarte-2025'.

    The exporter builds the slug as ``<a>-vs-<b>-<year>`` in stored order, so which
    half is the opponent depends on which side the athlete was on.
    """
    if "-vs-" not in match_slug:
        return None, None
    left, _, rest = match_slug.partition("-vs-")
    right, _, year = rest.rpartition("-")
    if not year.isdigit():
        right, year = rest, ""
    athlete_slug = athlete_key.replace(" ", "-")
    other = right if left == athlete_slug else left
    label = " ".join(w.title() for w in other.split("-") if w)
    return (label or None), (year[-2:] if year else None)


def _provenance(pattern: Any, athlete_key: str, limit: int = 2) -> list[str]:
    """Up to ``limit`` "vs Duarte '25 · 3:42" lines — where this exchange was seen.

    The reference charts cite an instructional's timestamp; ours cite the bout.
    Deduped by match so one busy bout can't fill the node.
    """
    out: list[str] = []
    seen: set[str] = set()
    for ev in pattern.evidence:
        if ev.match_id in seen:
            continue
        seen.add(ev.match_id)
        who, year = _opponent_from_slug(ev.match_slug or "", athlete_key)
        if not who:
            continue
        line = f"vs {who}" + (f" '{year}" if year else "")
        ts = _mmss(ev.timestamp_seconds)
        if ts:
            line += f" · {ts}"
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _leads_to(pool: list[Any], root_key: str, limit: int = 3) -> str | None:
    """"lands in Mount 2×, Back Control 1×" — the positions this action actually reached.

    Destinations that are already drawn as response nodes in this same branch are left
    out: the reader can see them, and repeating them here is noise, not information.
    """
    shown = {p.response_key for p in pool if p.response_key}
    tally: dict[str, int] = {}
    for p in pool:
        rp = p.resulting_position_key
        if rp and rp not in shown and not _canonical_root(rp, root_key):
            tally[rp] = tally.get(rp, 0) + p.count
    if not tally:
        return None
    top = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return "lands in " + ", ".join(f"{k.title()} {v}×" for k, v in top)


def _condition_label(cond_key: str) -> str:
    """'cond:posts-hand-and-cond:squares-hips' → 'Posts Hand and Squares Hips'.

    Bundled reactions join with ``-and-`` and each half keeps its own ``cond:``
    prefix, so stripping only the leading one leaks the raw key into the title.
    """
    parts = [p for p in cond_key.split("-and-") if p]
    words = [p.removeprefix("cond:").replace("-", " ").strip().title() for p in parts]
    return " and ".join(w for w in words if w) or "Reaction"


def _rate_line(success: int, failure: int) -> str | None:
    """Finish rate, only once enough known outcomes exist to mean anything."""
    known = success + failure
    if known < 3:
        return None
    return f"{round(success / known * 100)}% completed ({success}/{known})"


def node_id(kind: NodeKind, key: str | None) -> str:
    return f"n:{kind}:{key or 'unknown'}"


def cond_id(action_key: str, cond_key: str | None) -> str:
    return f"n:opponent-condition:{action_key}:{cond_key or 'unknown'}"


def response_id(action_key: str, cond_key: str | None, response_key: str) -> str:
    return f"n:response:{action_key}:{cond_key or 'unknown'}:{response_key}"


def outcome_id(action_key: str, cond_key: str | None, response_key: str | None) -> str:
    return f"n:outcome:{action_key}:{cond_key or 'unknown'}:{response_key or 'denied'}"


def edge_id(source: str, target: str, kind: EdgeKind) -> str:
    return f"e:{source}:{target}:{kind}"


def _r4(x: float) -> float:
    return round(x, 4)


def _canonical_root(source_position_key: str | None, root_position_key: str) -> bool:
    if not source_position_key:
        return False
    s = source_position_key.strip().lower()
    r = root_position_key.strip().lower()
    return s == r or s == f"{r} control" or r == f"{s} control"


def _outcome_value(outcome_type: str | None, response_key: str | None) -> float:
    if response_key is None:
        return 0.2  # failure / denied
    if outcome_type == "submission":
        return 1.0
    if outcome_type in ("sweep", "pass", "takedown"):
        return 0.7
    return 0.4


def _score(p: Any, *, ontology_relevance: float = 0.3) -> float:
    support = min(float(p.count), 10.0) / 10.0  # cap so one match can't dominate
    known = p.success_count + p.failure_count
    confidence = p.confidence if known >= 3 else 0.0
    support_w = 0.35 * support
    confidence_w = 0.25 * confidence
    outcome_w = 0.20 * _outcome_value(p.outcome_type, p.response_key)
    specificity_w = 0.10 * 0.5  # no corpus comparison yet
    ontology_w = 0.10 * ontology_relevance
    return _r4(support_w + confidence_w + outcome_w + specificity_w + ontology_w)


def _evidence_ids(p: Any) -> list[str]:
    return [f"m:{ev.match_slug}:i:{ev.action_index}" for ev in p.evidence]


def compile_flowchart(
    definition: FlowchartDefinition,
    patterns: list[Any],
    *,
    expert_branches: list[ExpertBranch] | None = None,
    athlete_label: str | None = None,
    root_position_label: str | None = None,
    evidence_meta: dict[str, dict[str, Any]] | None = None,
    layout_version: int | None = None,
) -> FlowchartSpec:
    """Build a spec from aggregated patterns rooted at the definition's position.

    ``evidence_meta`` maps a match_slug to {matchLabel, year, videoId} so the
    payload's evidence objects can carry display metadata the compiler itself
    cannot know (it stays pure; the builder enriches from the DB/session).
    """
    expert = expert_branches or []
    root_key = definition.root_position_key
    root_label = root_position_label or root_key.title()
    athlete_label = (athlete_label or definition.athlete_key).title()

    nodes: list[FlowchartNode] = []
    edges: list[FlowchartEdge] = []
    branches: list[FlowchartBranch] = []
    by_id: dict[str, FlowchartNode] = {}

    def add(node: FlowchartNode) -> None:
        if node.id in by_id:
            return
        nodes.append(node)
        by_id[node.id] = node

    def add_edge(source: str, target: str, kind: EdgeKind,
                 label: str | None = None) -> None:
        eid = edge_id(source, target, kind)
        if not any(e.id == eid for e in edges):
            edges.append(FlowchartEdge(id=eid, source=source, target=target,
                                       kind=kind, label=label))

    add(FlowchartNode(
        id=node_id("root-position", root_key),
        key=root_key,
        kind="root-position",
        title=root_label,
        subtitle="Starting point",
        category="position",
    ))

    expert_lookup: set[tuple[str, str | None, str | None]] = set()
    for eb in expert:
        expert_lookup.add((eb.action_key, eb.condition_key, eb.response_key))

    # rank primary branches (actions) by score, apply definition limits
    action_pool: dict[str, list[Any]] = {}
    for p in patterns:
        if not _canonical_root(p.source_position_key, root_key):
            continue
        if p.count < definition.minimum_support:
            continue
        action_pool.setdefault(p.action_key, []).append(p)

    ranked_actions = sorted(
        action_pool.items(),
        key=lambda item: (-_score(item[1][0]), item[0]),
    )[: max(0, min(definition.max_primary_branches, 10))]

    total_exchanges = 0
    match_ids: set[str] = set()
    for p in patterns:
        for ev in p.evidence:
            match_ids.add(ev.match_id)
    source_counts: dict[str, int] = {_OBSERVED: 0, _EXPERT: 0, _HYBRID: 0}

    for rank, (action_key, pool) in enumerate(ranked_actions):
        total_exchanges += sum(p.count for p in pool)
        p0 = pool[0]
        action_label = p0.action_key.title()

        # condition grouping (deterministic: score then key)
        cond_groups: dict[str | None, list[Any]] = {}
        for p in pool:
            cond_groups.setdefault(p.condition_key, []).append(p)
        ranked_conds = sorted(
            cond_groups.items(),
            key=lambda item: (-_score(item[1][0]), item[0] or ""),
        )[: max(1, min(definition.max_conditions_per_action, 10))]

        act_count = sum(p.count for p in pool)
        act_matches = len({ev.match_id for p in pool for ev in p.evidence})
        aid = node_id("athlete-action", action_key)
        add(FlowchartNode(
            id=aid,
            key=action_key,
            kind="athlete-action",
            title=action_label,
            subtitle=f"Branch {rank + 1}",
            category=p0.action_type,
            detail=[ln for ln in [
                f"{act_count}× across {_plural(act_matches, 'match', 'matches')}",
                _rate_line(sum(p.success_count for p in pool),
                           sum(p.failure_count for p in pool)),
                _leads_to(pool, root_key),
                *_provenance(p0, definition.athlete_key),
            ] if ln],
            support=act_count,
            match_count=p0.match_count,
            confidence=p0.confidence,
            evidence_ids=_evidence_ids(p0)[:8],
        ))
        add_edge(node_id("root-position", root_key), aid, "action")

        branch_conditions: list[str] = []
        for ck, cg in ranked_conds:
            cond_count = sum(p.count for p in cg)
            # No identified reaction = the athlete simply chained straight on. The
            # extractor deliberately refuses to invent a condition there, so the chart
            # must not invent a node for it either — the response hangs off the action.
            if ck is None:
                parent = aid
            else:
                cid = cond_id(action_key, ck)
                cond_matches = len({ev.match_id for p in cg for ev in p.evidence})
                add(FlowchartNode(
                    id=cid,
                    key=ck,
                    kind="opponent-condition",
                    title=_condition_label(ck),
                    subtitle="Opponent reacts",
                    detail=[f"{cond_count}× · seen in "
                            f"{_plural(cond_matches, 'match', 'matches')}"],
                    support=cond_count,
                    match_count=max(p.match_count for p in cg),
                ))
                add_edge(aid, cid, "reaction")
                branch_conditions.append(cid)
                parent = cid

            # responses per condition (deterministic: score then key)
            resp_groups: dict[str | None, list[Any]] = {}
            for p in cg:
                resp_groups.setdefault(p.response_key, []).append(p)
            ranked_resps = sorted(
                resp_groups.items(),
                key=lambda item: (-_score(item[1][0]), item[0] or ""),
            )[: max(1, min(definition.max_responses_per_condition, 8))]

            for rk, rg in ranked_resps:
                r0 = rg[0]
                rcount = sum(p.count for p in rg)
                is_hybrid = (action_key, ck, rk) in expert_lookup
                src: SourceKind = _HYBRID if is_hybrid else _OBSERVED
                source_counts[src] += 1
                known = r0.success_count + r0.failure_count
                success_rate = _r4(r0.success_count / known) if known >= 3 else None

                if rk is None:
                    rid = outcome_id(action_key, ck, None)
                    add(FlowchartNode(
                        id=rid,
                        key="denied",
                        kind="outcome",
                        title="Denied",
                        subtitle="Nothing followed",
                        detail=[ln for ln in [
                            f"{rcount}× the exchange stopped here",
                            *_provenance(r0, definition.athlete_key, limit=1),
                        ] if ln],
                        source=src,
                        support=rcount,
                        match_count=r0.match_count,
                        success_rate=success_rate,
                        confidence=r0.confidence,
                        warning=True,
                        evidence_ids=_evidence_ids(r0)[:8],
                    ))
                    add_edge(parent, rid, "response")
                    continue

                rid = response_id(action_key, ck, rk)
                resp_title = rk.title()
                if r0.outcome_type == "submission":
                    oid = outcome_id(action_key, ck, rk)
                    add(FlowchartNode(
                        id=oid,
                        key=rk,
                        kind="outcome",
                        title=resp_title,
                        subtitle="Submission",
                        category="submission",
                        detail=[ln for ln in [
                            f"{rcount}× attempted",
                            _rate_line(sum(p.success_count for p in rg),
                                       sum(p.failure_count for p in rg)),
                            *_provenance(r0, definition.athlete_key),
                        ] if ln],
                        source=src,
                        support=rcount,
                        match_count=r0.match_count,
                        success_rate=success_rate,
                        confidence=r0.confidence,
                        evidence_ids=_evidence_ids(r0)[:8],
                    ))
                    add_edge(parent, oid, "response")
                    continue

                add(FlowchartNode(
                    id=rid,
                    key=rk,
                    kind="response",
                    title=resp_title,
                    subtitle="Response",
                    category=r0.outcome_type,
                    detail=[ln for ln in [
                        f"{rcount}× attempted",
                        _rate_line(sum(p.success_count for p in rg),
                                   sum(p.failure_count for p in rg)),
                        *_provenance(r0, definition.athlete_key),
                    ] if ln],
                    source=src,
                    support=rcount,
                    match_count=r0.match_count,
                    success_rate=success_rate,
                    confidence=r0.confidence,
                    evidence_ids=_evidence_ids(r0)[:8],
                ))
                add_edge(parent, rid, "response")

                # results-in: next position (not the root, not the response itself).
                # A response that IS a stable state already names the position it puts the
                # athlete in — extract_patterns sets resulting_position_key to that very
                # node_key — so drawing a second box for it says the same thing twice.
                result_pos = r0.resulting_position_key
                if (result_pos and result_pos != rk
                        and not _canonical_root(result_pos, root_key)):
                    pid = node_id("position", result_pos)
                    add(FlowchartNode(
                        id=pid,
                        key=result_pos,
                        kind="position",
                        title=result_pos.title(),
                        subtitle="Continues in",
                        category="position",
                        url=f"the-ocean.html#{result_pos.replace(' ', '-')}",
                    ))
                    add_edge(rid, pid, "results-in")

        branch_matches: set[str] = set()
        branch_opponents: set[str] = set()
        for p in pool:
            for ev in getattr(p, "evidence", []) or []:
                if getattr(ev, "match_id", ""):
                    branch_matches.add(ev.match_id)
                if getattr(ev, "opponent_id", ""):
                    branch_opponents.add(ev.opponent_id)
        branches.append(FlowchartBranch(
            id=f"branch:{rank + 1}",
            action_key=action_key,
            score=_score(p0),
            conditions=branch_conditions,
            depth=1,
            support=act_count,
            # evidence is capped per pattern, so these are lower bounds — a branch never
            # claims more breadth than the retained evidence can show
            match_count=len(branch_matches) or None,
            opponent_count=len(branch_opponents) or None,
        ))

    # expert branches: dashed "Expected" nodes. An action not yet observed
    # gets a full branch; a known action gets the expert condition + response
    # attached to its existing branch. Node ids are context-scoped, so
    # existence checks use scoped ids.
    existing_actions = {n.key for n in nodes if n.kind == "athlete-action"}
    existing_node_ids = {n.id for n in nodes}
    for eb in expert:
        if eb.response_key is None:
            continue
        aid = node_id("athlete-action", eb.action_key)
        cid = cond_id(eb.action_key, eb.condition_key)
        rid = response_id(eb.action_key, eb.condition_key, eb.response_key)
        # fully covered by observed/hybrid nodes (condition + response or
        # outcome for the same technique) → expert contributes nothing.
        covered = (cid in existing_node_ids and (
            rid in existing_node_ids or
            outcome_id(eb.action_key, eb.condition_key, eb.response_key)
            in existing_node_ids))
        if covered:
            continue
        added_any = False
        if eb.action_key not in existing_actions:
            add(FlowchartNode(
                id=aid,
                key=eb.action_key,
                kind="athlete-action",
                title=eb.action_key.title(),
                subtitle="Expected",
                source=_EXPERT,
            ))
            existing_actions.add(eb.action_key)
            existing_node_ids.add(aid)
            add_edge(node_id("root-position", root_key), aid, "action")
            branches.append(FlowchartBranch(
                id=f"branch:{len(branches) + 1}",
                action_key=eb.action_key,
                score=0.0,
                conditions=[cid],
                depth=1,
            ))
            added_any = True
        else:
            branch = next(b for b in branches
                          if b.action_key == eb.action_key)
            if cid not in branch.conditions:
                branch.conditions.append(cid)
        if cid not in existing_node_ids:
            add(FlowchartNode(
                id=cid,
                key=eb.condition_key or "unknown",
                kind="opponent-condition",
                title=(eb.condition_key or "unknown reaction").removeprefix("cond:").title(),
                subtitle="Expected",
                source=_EXPERT,
            ))
            existing_node_ids.add(cid)
            added_any = True
        add_edge(aid, cid, "reaction")
        if rid not in existing_node_ids:
            add(FlowchartNode(
                id=rid,
                key=eb.response_key,
                kind="response",
                title=eb.response_key.title(),
                subtitle="Expected",
                source=_EXPERT,
            ))
            existing_node_ids.add(rid)
            added_any = True
        add_edge(cid, rid, "response")
        if added_any:
            source_counts[_EXPERT] += 1

    # portals: a response key reused across >= 2 condition nodes and present
    # as its own action branch → portal node (edge kind "portal")
    resp_occurrences: dict[str, int] = {}
    for e in edges:
        if e.kind != "response":
            continue
        node = by_id.get(e.target)
        if node is not None and node.kind == "response":
            resp_occurrences[node.key] = resp_occurrences.get(node.key, 0) + 1
    action_keys = {b.action_key for b in branches}
    for nid, node in list(by_id.items()):
        if (node.kind == "response" and resp_occurrences.get(node.key, 0) >= 2
                and node.key in action_keys):
            portal = FlowchartNode(
                id=nid,
                key=node.key,
                kind="portal",
                title=f"↪ {node.title}",
                subtitle="Continues below",
                source=node.source,
                support=node.support,
                match_count=node.match_count,
                confidence=node.confidence,
                evidence_ids=node.evidence_ids,
            )
            nodes[nodes.index(node)] = portal
            by_id[nid] = portal

    # evidence registry: only entries referenced by a node's evidenceIds.
    # compiler stays pure — display metadata (label/year/videoId) arrives via
    # evidence_meta from the builder; timestamps come from the patterns.
    ev_by_id: dict[str, Any] = {}
    for p in patterns:
        for ev in p.evidence:
            ev_by_id.setdefault(f"m:{ev.match_slug}:i:{ev.action_index}", ev)
    meta = evidence_meta or {}
    evidence: dict[str, dict[str, Any]] = {}
    for node in nodes:
        for eid in node.evidence_ids:
            if eid in evidence:
                continue
            ev = ev_by_id.get(eid)
            slug = ev.match_slug if ev is not None else str(eid).split(":", 3)[1]
            info = meta.get(slug, {})
            entry: dict[str, Any] = {"matchSlug": slug}
            if ev is not None and ev.timestamp_seconds is not None:
                entry["timestampSeconds"] = ev.timestamp_seconds
            if info.get("matchLabel"):
                entry["matchLabel"] = info["matchLabel"]
            if info.get("year") is not None:
                entry["year"] = info["year"]
            if info.get("videoId"):
                entry["videoId"] = info["videoId"]
            evidence[eid] = entry

    return FlowchartSpec(
        schema_version=1,
        key=definition.key,
        title=definition.title,
        athlete=definition.athlete_key,
        athlete_label=athlete_label,
        root_position_key=root_key,
        root_position_label=root_label,
        nodes=nodes,
        edges=edges,
        branches=branches,
        exchanges=total_exchanges,
        matches=len(match_ids) if match_ids else max(
            (p.match_count for p in patterns), default=0),
        sources=dict(sorted(source_counts.items())),
        layout_version=layout_version,
        evidence=evidence,
    )


def load_definitions(path: Path) -> list[FlowchartDefinition]:
    """Load curated flowchart definitions from a JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"flowchart definitions must be a JSON list, got {type(raw).__name__}")
    out: list[FlowchartDefinition] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"flowchart definition entry must be an object, got {entry!r}")
        required = ("key", "title", "athlete_key", "root_position_key")
        for field_name in required:
            if field_name not in entry:
                raise ValueError(f"flowchart definition missing '{field_name}': {entry!r}")
        if "published" not in entry:
            entry = {**entry, "published": True}
        out.append(FlowchartDefinition(**entry))
    return out


def spec_to_dict(spec: FlowchartSpec) -> dict[str, Any]:
    """Serialize a spec to the contract payload shape (deterministic order)."""
    return {
        "schemaVersion": spec.schema_version,
        "key": spec.key,
        "title": spec.title,
        "athlete": spec.athlete,
        "athleteLabel": spec.athlete_label,
        "rootPositionKey": spec.root_position_key,
        "rootPositionLabel": spec.root_position_label,
        "exchanges": spec.exchanges,
        "matches": spec.matches,
        "sources": spec.sources,
        "evidence": spec.evidence,
        "nodes": [_node_to_dict(n) for n in spec.nodes],
        "edges": [_edge_to_dict(e) for e in spec.edges],
        "branches": [_branch_to_dict(b) for b in spec.branches],
        "compilerVersion": spec.compiler_version,
        "layoutVersion": spec.layout_version,
    }


def _node_to_dict(n: FlowchartNode) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": n.id,
        "key": n.key,
        "kind": n.kind,
        "title": n.title,
        "subtitle": n.subtitle,
        "category": n.category,
        "detail": list(n.detail),
        "source": n.source,
        "support": n.support,
        "matchCount": n.match_count,
        "confidence": n.confidence,
        "evidenceIds": n.evidence_ids,
    }
    if n.success_rate is not None:
        d["successRate"] = n.success_rate
    if n.warning:
        d["warning"] = True
    if n.url:
        d["url"] = n.url
    return d


def _edge_to_dict(e: FlowchartEdge) -> dict[str, Any]:
    d: dict[str, Any] = {"id": e.id, "source": e.source, "target": e.target, "kind": e.kind}
    if e.label:
        d["label"] = e.label
    return d


def _branch_to_dict(b: FlowchartBranch) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": b.id,
        "actionKey": b.action_key,
        "score": b.score,
        "conditions": b.conditions,
        "depth": b.depth,
    }
    # omitted rather than null on expert-only branches: absent means "not observed",
    # which is the truth, where 0 would read as "observed and never happened"
    if b.support is not None:
        d["support"] = b.support
    if b.match_count is not None:
        d["matchCount"] = b.match_count
    if b.opponent_count is not None:
        d["opponentCount"] = b.opponent_count
    return d
