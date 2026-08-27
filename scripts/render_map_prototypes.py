"""Phase 1 (actions/states migration) — 6 comparison prototypes of ONE user's own map.

    uv run python -m scripts.render_map_prototypes --bundle PATH [--out DIR]

Reads a raw App user bundle (LGPD: private, owner-only data — the JSON never leaves this
machine, output is local-only). For every round, entries are partitioned by ``sequenceId``
(undefined -> the single legacy chain, same convention as the App's
``services/sequencePartition.ts``), then compiled actor-aware with
``analysis.chain_compiler.compile_two_sided`` (you/partner -> side 'a'/'b'). Results are
aggregated across the whole bundle into unique (node_key, actor) states and
(source, target, action_key, actor) edges, each carrying a usage/occurrence count and an
``inferred`` flag (True only if EVERY occurrence was structurally inferred, never observed).

Renders 6 self-contained HTMLs (``site/graph.js`` copied alongside, same pattern as
``export/grappling_map.py``) + an ``index.html`` + ``metrics.json``. Deterministic: dict/list
order follows bundle read order (no unordered ``set`` in the render path), and
``metrics.json`` is written with ``sort_keys=True`` as a second belt.

**graph.js contract (read before editing, do not invent fields):** node {id,label,cat,
size 1-3,fighter 'a'|'b'|'x',color?}; link {from,to,fighter,weight 1-3,arrow,dashed}. No
per-link ``label`` and no per-link ``color`` are ever read by the renderer (only ``fighter``
drives link colour, via the fixed 3-slot ``FIG`` palette) — two limitations this module works
around rather than papers over:
  - action labels (variant 2+) go in an HTML side list next to the canvas, not on the edge.
  - action-TYPE colouring (variant 5) reuses the 3-slot ``fighter`` field as a 3-bucket
    approximation (submission/takedown -> 'a', pass/sweep -> 'b', escape/transition/other ->
    'x'), NOT true per-type colour. A real 6-colour edge legend needs graph.js to read an
    ``l.color`` -- that becomes a Phase 5 requirement on the App's own renderer, not something
    to sneak into this repo's copy of the shared file.
"""

# ruff: noqa: E501  (HTML/JS template strings are content)

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from analysis.chain_compiler import ChainEdge, ChainState, compile_two_sided
from analysis.taxonomy_kind import load_inference_table

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "map_prototypes"
_GRAPH_JS = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site" / "graph.js"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}

_ACTOR_SIDE = {"you": "a", "partner": "b"}
_TYPE_BUCKET = {  # variant 5's action-type -> fighter-slot approximation, see module docstring
    "submission": "a", "takedown": "a",
    "pass": "b", "sweep": "b",
}


def _clamp3(n: float) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


# ── 1. bundle -> compiled chains ────────────────────────────────────────────────

def partition_by_sequence(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Port of the App's ``sequencePartition.partitionEntriesBySequence`` — consecutive runs
    by ``sequenceId``; ``None`` (JSON: absent/undefined) groups together too, the legacy
    single chain. Never reorders/merges/drops."""
    if not entries:
        return []
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_id: str | None = None
    for i, e in enumerate(entries):
        sid = e.get("sequenceId")
        if i == 0 or sid != current_id:
            if current:
                groups.append(current)
            current = []
            current_id = sid
        current.append(e)
    if current:
        groups.append(current)
    return groups


def _side_of(e: dict[str, Any]) -> str | None:
    return _ACTOR_SIDE.get(e.get("actor"))


def _actor_of(e: dict[str, Any]) -> str | None:
    return e.get("actor")


class Aggregate:
    """Unique (node_key, actor) states / (source, target, action_key, actor) edges across the
    whole bundle, each with an occurrence count + an inferred flag (True iff EVERY occurrence
    was inferred). Also keeps the RAW (non-deduped) inferred counts for the corpus-wide rate."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.raw_states_total = 0
        self.raw_states_inferred = 0
        self.raw_edges_total = 0
        self.raw_edges_inferred = 0

    def add_state(self, s: ChainState) -> None:
        if s.actor not in ("you", "partner"):
            return
        self.raw_states_total += 1
        self.raw_states_inferred += 1 if s.inferred else 0
        key = (s.node_key, s.actor)
        row = self.states.get(key)
        if row is None:
            self.states[key] = {"node_key": s.node_key, "label": s.label, "type": s.type,
                                 "actor": s.actor, "count": 1, "inferred": s.inferred}
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and s.inferred

    def add_edge(self, e: ChainEdge) -> None:
        if e.actor not in ("you", "partner"):
            return
        self.raw_edges_total += 1
        self.raw_edges_inferred += 1 if e.inferred else 0
        key = (e.source_key, e.target_key, e.action_key, e.actor)
        row = self.edges.get(key)
        if row is None:
            self.edges[key] = {"source": e.source_key, "target": e.target_key,
                                "action_key": e.action_key, "action_label": e.action_label,
                                "action_type": e.action_type, "actor": e.actor,
                                "count": 1, "inferred": e.inferred}
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and e.inferred


def build_aggregate(bundle: dict[str, Any]) -> Aggregate:
    table = load_inference_table()
    agg = Aggregate()
    for session in bundle.get("sessions", []):
        for round_ in session.get("rounds", []):
            entries = round_.get("entries", []) or []
            for group in partition_by_sequence(entries):
                compiled = compile_two_sided(group, _side_of, actor_of=_actor_of,
                                              inference_table=table)
                for side in ("a", "b"):
                    for s in compiled[side].states:
                        agg.add_state(s)
                    for e in compiled[side].edges:
                        agg.add_edge(e)
    return agg


# ── 2. variant node/link builders ───────────────────────────────────────────────

def _cat_of(type_: str) -> str:
    return type_ if type_ in _CATS else "control"


def _baseline_graphview(bundle: dict[str, Any]) -> dict[str, Any]:
    """Variant 1 — the graph as the App renders it TODAY: ``bundle.graph`` verbatim."""
    graph = bundle.get("graph") or {}
    nodes = []
    for n in graph.get("nodes", []):
        d = n.get("data", {}) or {}
        nodes.append({"id": n["id"], "label": n.get("label", ""),
                      "cat": _cat_of(d.get("type", "")), "size": _clamp3(d.get("usageCount", 1))})
    links = [{"from": e["source"], "to": e["target"], "weight": 1}
             for e in graph.get("edges", []) if e.get("source") and e.get("target")]
    return {"nodes": nodes, "links": links}


def _own_graphview(agg: Aggregate) -> dict[str, Any]:
    """Variant 2 — you only. No fighter colouring needed (single actor)."""
    states = {k: v for k, v in agg.states.items() if v["actor"] == "you"}
    edges = [v for v in agg.edges.values() if v["actor"] == "you"]
    nodes = [{"id": v["node_key"], "label": v["label"], "cat": _cat_of(v["type"]),
              "size": _clamp3(v["count"])} for v in states.values()]
    links = [{"from": v["source"], "to": v["target"], "weight": _clamp3(v["count"]), "arrow": True}
              for v in edges]
    return {"nodes": nodes, "links": links}, edges


def _two_sided_graphview(states: dict, edges: list) -> dict[str, Any]:
    nodes, seen = [], set()
    for (node_key, actor), v in states.items():
        if node_key in seen:
            continue
        seen.add(node_key)
        agg_count = sum(vv["count"] for kk, vv in states.items() if kk[0] == node_key)
        nodes.append({"id": node_key, "label": v["label"], "cat": _cat_of(v["type"]),
                      "size": _clamp3(agg_count),
                      "fighter": _fighter_for_states_subset(states, node_key)})
    links = [{"from": v["source"], "to": v["target"], "weight": _clamp3(v["count"]),
              "arrow": True, "fighter": _ACTOR_SIDE[v["actor"]]} for v in edges]
    return {"nodes": nodes, "links": links}


def _fighter_for_states_subset(states: dict[tuple[str, str], dict[str, Any]], node_key: str) -> str:
    actors = {v["actor"] for k, v in states.items() if k[0] == node_key}
    if len(actors) >= 2:
        return "x"
    return _ACTOR_SIDE.get(next(iter(actors), None), "a")


def _complete_two_sided(agg: Aggregate) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Variant 3 — every you + every partner element."""
    states = {k: v for k, v in agg.states.items()}
    edges = list(agg.edges.values())
    return _two_sided_graphview(states, edges), edges


def _sole_bridge_partner_nodes(you_node_keys: set[str], edges: list[dict[str, Any]]) -> set[str]:
    """BFS-simple bridge check: a low-usage partner node is kept if it connects two OTHERWISE
    disjoint you-components — i.e. it is the sole path between you elements, not just extra
    partner-side noise. 'you components' = connectivity using only edges where both ends are
    you nodes."""
    # union-find over you-only edges
    parent = {k: k for k in you_node_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    you_you_edges = [e for e in edges if e["actor"] == "you"]
    for e in you_you_edges:
        s, t = e["source"], e["target"]
        if s in parent and t in parent:
            union(s, t)

    partner_neighbours: dict[str, set[str]] = {}
    for e in edges:
        if e["actor"] != "partner":
            continue
        for endpoint, other in ((e["source"], e["target"]), (e["target"], e["source"])):
            if other in you_node_keys:
                partner_neighbours.setdefault(endpoint, set()).add(find(other))

    return {p for p, comps in partner_neighbours.items() if len(comps) >= 2}


def _selective_states_and_edges(agg: Aggregate) -> tuple[dict, list[dict[str, Any]]]:
    """Variant 4's element set: partner state/edge enters only if usageCount>=2 OR it is the
    sole bridge to a you element. Returned separately (not just the graphview) so variants 5/6
    can reuse the SAME filtered set instead of reverse-engineering it from rendered nodes."""
    you_keys = {k[0] for k in agg.states if k[1] == "you"}
    all_edges = list(agg.edges.values())
    bridges = _sole_bridge_partner_nodes(you_keys, all_edges)

    kept_partner_keys = {
        k[0] for k, v in agg.states.items()
        if v["actor"] == "partner" and (v["count"] >= 2 or k[0] in bridges)
    }
    kept_keys = you_keys | kept_partner_keys
    states = {k: v for k, v in agg.states.items()
              if v["actor"] == "you" or k[0] in kept_partner_keys}
    edges = [e for e in all_edges if e["source"] in kept_keys and e["target"] in kept_keys]
    return states, edges


def _hubs_graphview(states: dict, edges: list) -> dict[str, Any]:
    """Variant 5 — node size by DEGREE percentile (not usage), edges coloured by a 3-bucket
    action-type approximation (see module docstring: graph.js has no per-link colour field)."""
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    max_deg = max(degree.values(), default=1) or 1

    nodes, seen = [], set()
    for (node_key, _actor), v in states.items():
        if node_key in seen:
            continue
        seen.add(node_key)
        d = degree.get(node_key, 0)
        nodes.append({"id": node_key, "label": v["label"], "cat": _cat_of(v["type"]),
                      "size": 1 + round(2 * d / max_deg)})
    links = [{"from": e["source"], "to": e["target"], "weight": _clamp3(e["count"]), "arrow": True,
              "fighter": _TYPE_BUCKET.get(e["action_type"], "x")} for e in edges]
    return {"nodes": nodes, "links": links}


def _ghost_graphview(states: dict, edges: list) -> dict[str, Any]:
    """Variant 6 — variant 4's selective set, with inferred elements rendered as ghosts:
    dashed + minimum weight/size, node colour a translucent grey (``color`` IS a real node
    field graph.js reads — see module docstring)."""
    nodes, seen = [], set()
    for (node_key, _actor), v in states.items():
        if node_key in seen:
            continue
        seen.add(node_key)
        agg_count = sum(vv["count"] for kk, vv in states.items() if kk[0] == node_key)
        all_inferred = all(vv["inferred"] for kk, vv in states.items() if kk[0] == node_key)
        node = {"id": node_key, "label": v["label"], "cat": _cat_of(v["type"]),
                "size": 1 if all_inferred else _clamp3(agg_count),
                "fighter": _fighter_for_states_subset(states, node_key)}
        if all_inferred:
            node["color"] = "rgba(150,150,160,0.35)"
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": e["source"], "to": e["target"], "arrow": True,
                "fighter": _ACTOR_SIDE[e["actor"]]}
        if e["inferred"]:
            link["weight"], link["dashed"] = 1, True
        else:
            link["weight"] = _clamp3(e["count"])
        links.append(link)
    return {"nodes": nodes, "links": links}


# ── 3. HTML rendering ────────────────────────────────────────────────────────────

_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>{title}</title>
<style>
:root{{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}}
#canvas{{flex:1;position:relative}}#canvas canvas{{width:100%;height:100%;display:block}}
#side{{width:360px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}}
h1{{font-size:15px;margin:0 0 4px}}.muted{{color:var(--ink2);font-size:12px;margin-bottom:10px}}
.row{{padding:6px 8px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px}}
.g{{opacity:.45;border-style:dashed}}
.legend{{font-size:11px;color:var(--ink2);margin:10px 0;line-height:1.6}}
</style></head><body>
<div id="canvas"></div>
<div id="side"><h1>{title}</h1><div class="muted">{subtitle}</div>
<div class="legend">{legend}</div>
<div id="list">{list_html}</div></div>
<script src="graph.js"></script>
<script>const GV = {graphview};
GAGraph.mount(document.getElementById('canvas'),{{mode:'map',nodes:GV.nodes,links:GV.links,pan:true,zoom:true,collide:true}});
</script></body></html>"""


def _edge_list_html(edges: list[dict[str, Any]], id_to_label: dict[str, str]) -> str:
    rows = []
    for e in sorted(edges, key=lambda e: (-e["count"], e["source"], e["target"])):
        src = id_to_label.get(e["source"], e["source"])
        tgt = id_to_label.get(e["target"], e["target"])
        cls = " g" if e.get("inferred") else ""
        rows.append(
            f'<div class="row{cls}">{src} —<b>{e["action_label"]}</b>→ {tgt} '
            f'<span class="muted">({e["actor"]}, x{e["count"]})</span></div>'
        )
    return "".join(rows)


def _write_page(out: Path, filename: str, title: str, subtitle: str, legend: str,
                 graphview: dict[str, Any], edges: list[dict[str, Any]] | None) -> None:
    id_to_label = {n["id"]: n["label"] for n in graphview["nodes"]}
    list_html = _edge_list_html(edges, id_to_label) if edges is not None else ""
    html = _PAGE.format(title=title, subtitle=subtitle, legend=legend, list_html=list_html,
                         graphview=json.dumps(graphview, ensure_ascii=False))
    (out / filename).write_text(html, encoding="utf-8")


_VARIANT_DESCRIPTIONS = [
    ("1-baseline.html", "the app's CURRENT graph — technique=node — the comparison ruler"),
    ("2-migrado-proprio.html", "new model, YOU only — states=nodes, edges=action"),
    ("3-migrado-oponente-completo.html", "+ every partner element (blue you / orange partner / grey both)"),
    ("4-migrado-oponente-seletivo.html", "partner enters only if used >=2x or sole bridge to a you node"),
    ("5-hubs.html", "node size by degree, edges bucketed by action type (see legend)"),
    ("6-ghost-inferidos.html", "variant 4 + inferred states/edges rendered as ghosts (dashed/grey)"),
]

_INDEX_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<title>Map prototypes — Phase 1</title>
<style>body{{background:#0b0b0f;color:#e9e9ee;font:14px/1.6 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}
a{{color:#4d86ff}}li{{margin-bottom:10px}}</style></head><body>
<h1>Map prototypes — actions/states migration, Phase 1</h1>
<ul>{items}</ul>
</body></html>"""


def render_all(bundle: dict[str, Any], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if _GRAPH_JS.exists():
        shutil.copy(_GRAPH_JS, out / "graph.js")

    agg = build_aggregate(bundle)
    metrics: dict[str, Any] = {"variants": {}}

    # 1 — baseline (not compiled, no edge-label list)
    gv1 = _baseline_graphview(bundle)
    _write_page(out, "1-baseline.html", "1 — Baseline (current app graph)",
                "technique=node, App's own graph today", "no legend — plain baseline",
                gv1, None)
    metrics["variants"]["1-baseline"] = _variant_metrics(gv1, None, 0)

    # 2 — you only
    gv2, edges2 = _own_graphview(agg)
    _write_page(out, "2-migrado-proprio.html", "2 — Migrated, you only",
                "states=nodes, edges=action (label in the list, graph.js has no link label)",
                "arrows = your own chain order", gv2, edges2)
    metrics["variants"]["2-migrado-proprio"] = _variant_metrics(gv2, edges2, 0)

    # 3 — full two-sided
    gv3, edges3 = _complete_two_sided(agg)
    partner3 = sum(1 for k in agg.states if k[1] == "partner") + sum(1 for e in edges3 if e["actor"] == "partner")
    _write_page(out, "3-migrado-oponente-completo.html", "3 — + full opponent",
                "every you + every partner element",
                "blue=you orange=partner grey=both touched this node", gv3, edges3)
    metrics["variants"]["3-migrado-oponente-completo"] = _variant_metrics(gv3, edges3, partner3)

    # 4 — selective opponent (states4/edges4 reused by 5 and 6: same partner-noise gate)
    states4, edges4 = _selective_states_and_edges(agg)
    gv4 = _two_sided_graphview(states4, edges4)
    partner4 = sum(1 for k in states4 if k[1] == "partner") + sum(1 for e in edges4 if e["actor"] == "partner")
    _write_page(out, "4-migrado-oponente-seletivo.html", "4 — Selective opponent",
                "partner element kept only if used >=2x or sole bridge to a you element",
                "same colours as (3), fewer partner nodes", gv4, edges4)
    metrics["variants"]["4-migrado-oponente-seletivo"] = _variant_metrics(gv4, edges4, partner4)

    # 5 — hubs (same element set as 4, different sizing/colour)
    gv5 = _hubs_graphview(states4, edges4)
    _write_page(out, "5-hubs.html", "5 — Hubs (degree size, action-type colour)",
                "node size = degree percentile",
                "APPROXIMATION: graph.js has no per-link colour field, so action type reuses "
                "the 3-slot fighter palette — blue=submission/takedown, orange=pass/sweep, "
                "grey=escape/transition/other. True per-type colour needs an App-side graph.js "
                "change (Phase 5).",
                gv5, edges4)
    metrics["variants"]["5-hubs"] = _variant_metrics(gv5, edges4, partner4)

    # 6 — ghost inferred (same element set as 4, inferred elements ghosted)
    gv6 = _ghost_graphview(states4, edges4)
    _write_page(out, "6-ghost-inferidos.html", "6 — Ghost inferred",
                "variant 4 + inferred states/edges rendered as ghosts",
                "dashed/grey = structurally inferred (D2 gap-fill), never an observed event",
                gv6, edges4)
    metrics["variants"]["6-ghost-inferidos"] = _variant_metrics(gv6, edges4, partner4)

    metrics["corpus_inference_rate"] = {
        "states_total": agg.raw_states_total,
        "states_inferred": agg.raw_states_inferred,
        "states_inferred_pct": _pct(agg.raw_states_inferred, agg.raw_states_total),
        "edges_total": agg.raw_edges_total,
        "edges_inferred": agg.raw_edges_inferred,
        "edges_inferred_pct": _pct(agg.raw_edges_inferred, agg.raw_edges_total),
    }

    items = "".join(
        f'<li><a href="{fname}">{fname}</a> — {desc}</li>' for fname, desc in _VARIANT_DESCRIPTIONS
    )
    (out / "index.html").write_text(_INDEX_PAGE.format(items=items), encoding="utf-8")
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return metrics


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def _variant_metrics(gv: dict[str, Any], edges: list[dict[str, Any]] | None, partner_elements: int) -> dict[str, Any]:
    n_nodes, n_edges = len(gv["nodes"]), len(gv["links"])
    inferred_edges = sum(1 for e in edges if e.get("inferred")) if edges is not None else None
    return {
        "nodes": n_nodes,
        "edges": n_edges,
        "edges_per_node": round(n_edges / n_nodes, 2) if n_nodes else 0.0,
        "pct_inferred_edges": _pct(inferred_edges, len(edges)) if edges else (0.0 if edges is not None else None),
        "partner_elements": partner_elements,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Render 6 actions/states map prototypes from a user bundle")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    metrics = render_all(bundle, args.out)

    print(f"{'variant':32} {'nodes':>6} {'edges':>6} {'e/n':>6} {'%inf':>6} {'partner':>8}")
    for name, m in metrics["variants"].items():
        pct = m["pct_inferred_edges"]
        print(f"{name:32} {m['nodes']:>6} {m['edges']:>6} {m['edges_per_node']:>6} "
              f"{'—' if pct is None else pct:>6} {m['partner_elements']:>8}")
    cir = metrics["corpus_inference_rate"]
    print(f"\ncorpus inference rate: states {cir['states_inferred_pct']}% "
          f"({cir['states_inferred']}/{cir['states_total']}), "
          f"edges {cir['edges_inferred_pct']}% ({cir['edges_inferred']}/{cir['edges_total']})")
    print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
