"""Build, persist, and export the general grappling map.

    uv run python -m export.grappling_map [--out DIR] [--persist]

Assembles the map (analysis.grappling_map) with the hybrid vector layer (semantic pgvector +
structural), writes ``grappling_map.json`` + a self-contained navigable ``grappling_map.html``
(rendered by the dependency-free GAGraph), and — with ``--persist`` — upserts the global
``map_edges`` table. The viz is an internal exploration artifact (not wired into the public site).
"""

# ruff: noqa: E501  (HTML/JS template strings are content)

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from analysis.embeddings import semantic_neighbours_fn
from analysis.grappling_map import attach_neighbors, build_grappling_map
from analysis.vector_store import structural_neighbours_fn

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "grappling_map"
_GRAPH_JS = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site" / "graph.js"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}


def generate(session: Any) -> dict[str, Any]:
    """Assemble + enrich the map (semantic neighbours empty until embeddings are backfilled)."""
    gmap = build_grappling_map(session)
    graph = gmap.pop("_graph")
    attach_neighbors(
        gmap,
        semantic=semantic_neighbours_fn(session),
        structural=structural_neighbours_fn(graph),
    )
    return gmap


def persist_map_edges(session: Any, gmap: dict[str, Any]) -> int:
    """Replace the global ``map_edges`` table with the current map. Returns row count."""
    from sqlalchemy import delete

    from db.models import MapEdge

    session.execute(delete(MapEdge))
    session.add_all([
        MapEdge(source_key=e["source"], target_key=e["target"],
                count=e["count"], suggested=e["suggested"])
        for e in gmap["edges"]
    ])
    session.commit()
    return len(gmap["edges"])


def _clamp3(n: int) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _to_graphview(gmap: dict[str, Any]) -> dict[str, Any]:
    """app-shaped {nodes,links} for GAGraph — observed positions only (isolated lib nodes omitted)."""
    nodes_in = gmap["nodes"]
    pr_max = max((n["pagerank"] for n in nodes_in.values()), default=0.0) or 1.0
    nodes = [
        {"id": n["node_key"], "label": n["label"],
         "cat": n["type"] if n["type"] in _CATS else "control",
         "size": 1 + round(2 * n["pagerank"] / pr_max)}
        for n in nodes_in.values() if n["observed"]
    ]
    links = [
        {"from": e["source"], "to": e["target"], "weight": _clamp3(e["count"])}
        for e in gmap["edges"] if not e["suggested"]
    ]
    return {"nodes": nodes, "links": links}


_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>Grappling Map</title>
<style>
:root{{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}}
#canvas{{flex:1;position:relative}}#canvas canvas{{width:100%;height:100%;display:block}}
#side{{width:340px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}}
h1{{font-size:15px;margin:0 0 4px}}.muted{{color:var(--ink2);font-size:12px}}
input{{width:100%;margin:12px 0;padding:8px;background:#0c0c11;border:1px solid var(--line);border-radius:8px;color:var(--ink)}}
.row{{padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin-bottom:6px;cursor:pointer}}
.row:hover{{border-color:#3a3a45}}.row .k{{font-weight:600}}.row .s{{color:var(--ink2);font-size:12px}}
.tag{{display:inline-block;font-size:11px;color:var(--ink2);border:1px solid var(--line);border-radius:6px;padding:1px 6px;margin-right:4px}}
.sug{{color:#caa45a}}
</style></head><body>
<div id="canvas"></div>
<div id="side"><h1>Grappling Map</h1><div class="muted" id="meta"></div>
<input id="q" placeholder="search positions…"/><div id="list"></div></div>
<script src="graph.js"></script>
<script>const MAP = {data};</script>
<script>
const byKey = Object.fromEntries(MAP.nodes.map(n=>[n.node_key,n]));
const gv = {graphview};
GAGraph.mount(document.getElementById('canvas'),{{mode:'map',nodes:gv.nodes,links:gv.links}});
document.getElementById('meta').textContent =
  MAP.nodes.filter(n=>n.observed).length+' positions · '+MAP.edges.filter(e=>!e.suggested).length+' transitions · '+MAP.edges.filter(e=>e.suggested).length+' suggested';
const list=document.getElementById('list');
function card(n){{
  const nb=(n.neighbours||[]).map(x=>'<span class="tag">'+(byKey[x.node_key]?byKey[x.node_key].label:x.node_key)+' '+x.score+'</span>').join('');
  const out=MAP.edges.filter(e=>e.source===n.node_key).map(e=>(byKey[e.target]?byKey[e.target].label:e.target)+(e.suggested?' <span class="sug">(suggested)</span>':' ('+e.count+')')).join(', ');
  return '<div class="row"><div class="k">'+n.label+'</div>'+
    '<div class="s">'+n.type+' · seen '+n.occ+' · PR '+n.pagerank.toFixed(3)+' · R/R '+n.reward_risk.toFixed(2)+'</div>'+
    (nb?'<div class="s" style="margin-top:6px">similar: '+nb+'</div>':'')+
    (out?'<div class="s" style="margin-top:6px">→ '+out+'</div>':'')+'</div>';
}}
function render(f){{
  const q=(f||'').toLowerCase();
  list.innerHTML=MAP.nodes.filter(n=>n.label.toLowerCase().includes(q))
    .sort((a,b)=>b.pagerank-a.pagerank).slice(0,60).map(card).join('');
}}
document.getElementById('q').addEventListener('input',e=>render(e.target.value));
render('');
</script></body></html>"""


def render_html(gmap: dict[str, Any]) -> str:
    return _PAGE.format(
        data=json.dumps({"nodes": list(gmap["nodes"].values()), "edges": gmap["edges"]}, ensure_ascii=False),
        graphview=json.dumps(_to_graphview(gmap), ensure_ascii=False),
    )


def run(out: Path, persist: bool = False) -> int:
    from db.base import db_session

    out.mkdir(parents=True, exist_ok=True)
    with db_session() as session:
        gmap = generate(session)
        if persist:
            n = persist_map_edges(session, gmap)
            logger.info("Persisted %d map_edges", n)
    payload = {
        "nodes": list(gmap["nodes"].values()),
        "edges": gmap["edges"],
        "synonym_candidates": gmap.get("synonym_candidates", []),
    }
    (out / "grappling_map.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "grappling_map.html").write_text(render_html(gmap), encoding="utf-8")
    if _GRAPH_JS.exists():
        shutil.copy(_GRAPH_JS, out / "graph.js")
    observed = sum(1 for n in gmap["nodes"].values() if n["observed"])
    suggested = sum(1 for e in gmap["edges"] if e["suggested"])
    logger.info("Map → %s (%d positions, %d observed, %d edges, %d suggested, %d synonym pairs)",
                out, len(gmap["nodes"]), observed, len(gmap["edges"]), suggested,
                len(payload["synonym_candidates"]))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Build + export the general grappling map")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--persist", action="store_true", help="also upsert the map_edges table")
    args = ap.parse_args()
    return run(args.out, persist=args.persist)


if __name__ == "__main__":
    raise SystemExit(main())
