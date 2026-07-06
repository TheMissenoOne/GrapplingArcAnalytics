"""Phase-2 canonicalization: cluster the shared ``technique_nodes`` into duplicate
groups and propose one canonical node per cluster — a REVIEW ARTIFACT ONLY, no DB writes.

Feeds the human-confirm step of the taxonomy plan (auto-cluster + confirm). Reuses the
backfilled pgvector embeddings (``analysis.embeddings.load_matrix``) for semantic clustering
and ``grappling_map._synonymish`` for the string near-duplicate signal.

Two guards that keep ELO safe (see docs/TAXONOMY plan F6 — the scorer reads ``node_type``):
  * clustering is **within one ``node_type``** by default, so a merge never changes a node's
    type. Cross-type near-duplicates (high cosine, different type = a mislabel) are reported
    *separately* for human attention, never auto-clustered.
  * ``Attempt`` nodes are flagged (the outcome belongs on the edge, not a distinct node), but
    the report only proposes — nothing is merged or stripped here.

    uv run python -m analysis.canonicalize                 # write report to docs/
    uv run python -m analysis.canonicalize --threshold 0.9 # looser clusters
    uv run python -m analysis.canonicalize --check         # in-memory self-check, no DB
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# node_types outside the grappling taxonomy (plan Part 5). strike/penalty/match → an ``event``
# class; concept/defensive → mislabeled techniques to remap. Reported, not acted on here.
_EVENT_TYPES = {"strike", "penalty", "match"}
_REMAP_TYPES = {"concept", "defensive"}
_ATTEMPT_RE = re.compile(r"\battempt(s|ed|ing)?\b", re.IGNORECASE)

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "docs" / "canonicalization_report"


@dataclass
class Node:
    key: str
    label: str
    node_type: str
    source: str  # 'library' | 'user'


@dataclass
class Cluster:
    canonical: Node
    aliases: list[Node] = field(default_factory=list)

    @property
    def size(self) -> int:
        return 1 + len(self.aliases)


def _tokens(label: str) -> set[str]:
    return set(re.findall(r"[a-z]+", label.lower()))


def _synonymish(a: str, b: str) -> bool:
    """One label's word-set ⊆ the other's — a true near-duplicate, not a sibling. Distinguishes
    "Heel Hook" ⊆ "Heel Hook Attempt" (dup) from "Single Leg" vs "Double Leg" (distinct)."""
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def elect_canonical(members: list[Node]) -> Node:
    """Pick the survivor: library beats user, then shortest label, then alphabetical.

    A curated ``library`` node is authoritative; among ties the shortest clean label is the
    least-decorated spelling ("Heel Hook" over "Heel Hook Attempt Inside")."""
    return min(
        members,
        key=lambda n: (n.source != "library", len(n.label), n.label.lower()),
    )


def build_clusters(
    nodes: list[Node], matrix: np.ndarray, threshold: float = 0.92, *, same_type: bool = True
) -> list[Cluster]:
    """Union-find over cosine ≥ ``threshold`` (unit vectors → dot product). ``same_type`` keeps
    merges within one node_type so ELO's type sets are never disturbed. Singletons dropped —
    a cluster of one is not a duplicate."""
    n = len(nodes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    # unit embeddings → sims[i,j] = cosine. Full 437² is trivial; threshold + type gate the joins.
    sims = matrix @ matrix.T
    for i in range(n):
        row = sims[i]
        for j in range(i + 1, n):
            if row[j] < threshold:
                continue
            if same_type and nodes[i].node_type != nodes[j].node_type:
                continue
            union(i, j)

    groups: dict[int, list[Node]] = {}
    for i, node in enumerate(nodes):
        groups.setdefault(find(i), []).append(node)

    clusters: list[Cluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canon = elect_canonical(members)
        clusters.append(Cluster(canon, [m for m in members if m.key != canon.key]))
    clusters.sort(key=lambda c: c.size, reverse=True)
    return clusters


def cross_type_neardups(
    nodes: list[Node], matrix: np.ndarray, threshold: float = 0.92
) -> list[tuple[Node, Node, float]]:
    """High-cosine pairs with *different* node_type — likely mislabels (e.g. a submission also
    logged as a concept). Surfaced for review; never auto-merged (would corrupt ELO's type)."""
    sims = matrix @ matrix.T
    out: list[tuple[Node, Node, float]] = []
    n = len(nodes)
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i][j] >= threshold and nodes[i].node_type != nodes[j].node_type:
                out.append((nodes[i], nodes[j], round(float(sims[i][j]), 3)))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def build_report(nodes: list[Node], matrix: np.ndarray, threshold: float) -> dict[str, Any]:
    """Assemble the full review artifact from pure inputs (no DB, no I/O)."""
    clusters = build_clusters(nodes, matrix, threshold)
    merged = sum(len(c.aliases) for c in clusters)
    return {
        "threshold": threshold,
        "total_nodes": len(nodes),
        "duplicate_clusters": len(clusters),
        "nodes_that_would_merge_away": merged,
        "projected_canonical_count": len(nodes) - merged,
        "attempt_nodes": sorted(n.key for n in nodes if _ATTEMPT_RE.search(n.label)),
        "out_of_scope": {
            "event_class": sorted(n.key for n in nodes if n.node_type in _EVENT_TYPES),
            "remap": sorted(n.key for n in nodes if n.node_type in _REMAP_TYPES),
        },
        "cross_type_neardups": [
            {"a": a.key, "a_type": a.node_type, "b": b.key, "b_type": b.node_type, "cosine": s}
            for a, b, s in cross_type_neardups(nodes, matrix, threshold)
        ],
        "clusters": [
            {
                "node_type": c.canonical.node_type,
                "canonical": {"key": c.canonical.key, "label": c.canonical.label,
                              "source": c.canonical.source},
                # weak = semantic-only match (no token-subset overlap with canonical) → a likely
                # sibling, not a dup (Single vs Double Leg). Review these before merging.
                "aliases": [{"key": a.key, "label": a.label, "source": a.source,
                             "weak": not _synonymish(c.canonical.label, a.label)}
                            for a in c.aliases],
            }
            for c in clusters
        ],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Canonicalization review — technique_nodes",
        "",
        f"Threshold (cosine ≥): **{report['threshold']}** · same-type only.",
        f"Nodes: **{report['total_nodes']}** → projected canonical "
        f"**{report['projected_canonical_count']}** "
        f"({report['duplicate_clusters']} clusters merge {report['nodes_that_would_merge_away']} "
        f"aliases away).",
        "",
        "> Review artifact. No DB writes. Confirm/override each cluster before any merge.",
        "",
        f"## Attempt nodes ({len(report['attempt_nodes'])}) — outcome → edge, not a node",
        ", ".join(report["attempt_nodes"]) or "_none_",
        "",
        "## Out-of-scope node_types",
        f"- **event class** ({len(report['out_of_scope']['event_class'])}): "
        + (", ".join(report["out_of_scope"]["event_class"]) or "_none_"),
        f"- **remap** ({len(report['out_of_scope']['remap'])}): "
        + (", ".join(report["out_of_scope"]["remap"]) or "_none_"),
        "",
        f"## Cross-type near-duplicates ({len(report['cross_type_neardups'])}) — likely mislabels",
    ]
    for d in report["cross_type_neardups"]:
        lines.append(f"- `{d['a']}` ({d['a_type']}) ≈ `{d['b']}` ({d['b_type']}) — {d['cosine']}")
    lines += ["", f"## Duplicate clusters ({report['duplicate_clusters']})", ""]
    for c in report["clusters"]:
        canon = c["canonical"]
        lines.append(f"### [{c['node_type']}] {canon['label']} `({canon['source']})`")
        for a in c["aliases"]:
            flag = " ⚠️ review — semantic only" if a["weak"] else ""
            lines.append(f"- {a['label']} `{a['key']}` ({a['source']}){flag}")
        lines.append("")
    return "\n".join(lines)


def generate(session: Any, threshold: float = 0.92, out: Path = _DEFAULT_OUT) -> dict[str, Any]:
    """Load nodes+embeddings from the DB, build the report, write ``.json`` + ``.md``."""
    from sqlalchemy import select

    from db.models import TechniqueNode as T

    rows = list(session.execute(
        select(T.node_key, T.label, T.node_type, T.source, T.embedding)
        .where(T.embedding.isnot(None))
    ))
    nodes = [Node(k, lbl, nt or "", src or "user") for k, lbl, nt, src, _ in rows]
    matrix = np.asarray([np.asarray(r[4], dtype=np.float64) for r in rows])
    # normalize to unit length so dot product == cosine (embeddings may not be pre-normalized).
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12

    report = build_report(nodes, matrix, threshold)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    out.with_suffix(".md").write_text(_render_markdown(report))
    logger.info("Report → %s.{json,md}: %d nodes → %d canonical (%d clusters)",
                out, report["total_nodes"], report["projected_canonical_count"],
                report["duplicate_clusters"])
    return report


def _check() -> None:
    """In-memory self-check — clustering, election, and the ELO-safety type gate."""
    nodes = [
        Node("heel hook", "Heel Hook", "submission", "library"),
        Node("heel hook attempt", "Heel Hook Attempt", "submission", "user"),
        Node("heelhook", "Heelhook Finish", "submission", "user"),
        Node("closed guard", "Closed Guard", "guard", "library"),
        Node("armbar", "Armbar", "submission", "user"),
    ]
    # heel-hook trio near-identical; closed guard + armbar are their own things.
    vecs = {
        "heel hook": [1.0, 0.0, 0.0], "heel hook attempt": [0.99, 0.01, 0.0],
        "heelhook": [0.98, 0.02, 0.0], "closed guard": [0.0, 1.0, 0.0],
        "armbar": [0.0, 0.0, 1.0],
    }
    mat = np.asarray([vecs[n.key] for n in nodes], dtype=np.float64)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    clusters = build_clusters(nodes, mat, threshold=0.9)
    assert len(clusters) == 1, clusters
    c = clusters[0]
    assert c.canonical.key == "heel hook", c.canonical  # library + shortest wins
    assert {a.key for a in c.aliases} == {"heel hook attempt", "heelhook"}

    # a mislabeled duplicate: same technique tagged with a different node_type must NOT merge.
    typed = [
        Node("guard pull", "Guard Pull", "guard", "library"),
        Node("guard pull concept", "Guard Pull", "concept", "user"),
    ]
    m2 = np.asarray([[1.0, 0.0], [1.0, 0.0]])
    assert build_clusters(typed, m2, threshold=0.9) == [], "cross-type must not auto-merge"
    assert len(cross_type_neardups(typed, m2, 0.9)) == 1, "but must be reported"

    rep = build_report(nodes, mat, 0.9)
    assert rep["attempt_nodes"] == ["heel hook attempt"]
    assert rep["projected_canonical_count"] == 3  # 5 nodes − 2 merged aliases
    print("canonicalize self-check OK")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Canonicalization cluster report (read-only)")
    ap.add_argument("--threshold", type=float, default=0.92, help="cosine merge threshold")
    ap.add_argument("--check", action="store_true", help="in-memory self-check, no DB")
    args = ap.parse_args()
    if args.check:
        _check()
        return 0
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from db.base import db_session

    with db_session() as session:
        generate(session, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
