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
"""
# ruff: noqa: E501  (HTML/JS template strings are content, not wrappable code)

from __future__ import annotations

import argparse
import html
import json
import logging
import re
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
from analysis.counter_moves import counter_moves
from analysis.defense_rate import defense_profile
from analysis.event_profile import build_event_profile, event_names
from analysis.names import _normalize_name
from analysis.network_metrics import network_from_sequences
from analysis.path_to_victory import dilemmas, path_to_victory
from analysis.style_profile import MIN_DOSSIER_EVENTS, build_style_profile, qualifies
from db.models import Archetype, Athlete
from export.match_breakdown import (
    _final_matches,
    _headline,
    build_match_breakdown,
    export_fighter_graph,
    match_slug,
    slugify,
)
from export.narrative import event_narrative, match_narrative, profile_narrative

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}

# Grapple-Like radar = the App analytics tab's axes (SpiderChart categoryOrder,
# clockwise from the top), so the site and the app read the same fingerprint.
_RADAR_AXES = ["pass", "control", "submission", "escape", "guard", "sweep", "takedown"]
_RADAR_LABELS = ["Pass", "Control", "Submission", "Escape", "Guard", "Sweep", "Takedown"]


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
        nodes.append(node)
    links = []
    for e in app_graph.get("edges", []):
        d = e.get("data", {})
        links.append({
            "from": e["source"], "to": e["target"],
            "fighter": d.get("side", default_side),
            "weight": _clamp3(int(d.get("count", 1))),
        })
    return {"nodes": nodes, "links": links}


def _truncate_graph(g: dict[str, Any], limit: int) -> dict[str, Any]:
    """Keep the ``limit`` busiest nodes (+ their links) so cards/dossiers stay legible."""
    nodes = sorted(g["nodes"], key=lambda n: n["size"], reverse=True)[:limit]
    keep = {n["id"] for n in nodes}
    links = [lk for lk in g["links"] if lk["from"] in keep and lk["to"] in keep]
    return {"nodes": nodes, "links": links}


def _career_graphview(athlete: Athlete, profile: dict[str, Any], session: Session,
                      limit: int = 12) -> dict[str, Any]:
    """Fighter's career graph (adapted + truncated), falling back to signature transitions."""
    g = export_fighter_graph(athlete, session)
    if g and g.get("nodes"):
        return _truncate_graph(_to_graphview(g, "a"), limit)
    nodes: dict[str, dict[str, Any]] = {}
    links = []
    for t in profile.get("signature_transitions", []):
        for lb in (t["from"], t["to"]):
            key = _normalize_name(lb)
            nodes.setdefault(key, {"id": key, "label": lb, "cat": "control",
                                   "size": 2, "fighter": "a"})
        links.append({"from": _normalize_name(t["from"]), "to": _normalize_name(t["to"]),
                      "fighter": "a", "weight": _clamp3(int(t["count"]))})
    return {"nodes": list(nodes.values()), "links": links}


# ── data files ───────────────────────────────────────────────────────────────
def _featured_stats(bd: dict[str, Any]) -> list[dict[str, Any]]:
    sa, sb = bd["stats"]["a"], bd["stats"]["b"]
    return [
        {"k": "Positional conversion", "va": _pct(sa["positional_conversion"]),
         "vb": _pct(sb["positional_conversion"])},
        {"k": "Control positions", "va": sa["controls"], "vb": sb["controls"]},
        {"k": "Sub attempts", "va": sa["submission_attempts"], "vb": sb["submission_attempts"]},
        {"k": "Transitions", "va": sa["transitions"], "vb": sb["transitions"]},
    ]


def build_breakdowns(
    session: Session,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]], dict[str, Any] | None]:
    """Returns (GA_BREAKDOWNS rows, [(slug, full breakdown)], GA_FEATURED) for sequence bouts.

    The featured bout = the decided match with the highest combined opponent rank_elo, so the
    homepage spotlight is real (names, method, mini-stats) and auto-updates with the data.
    """
    standings = _elo_standings(session)
    rows: list[dict[str, Any]] = []
    full: list[tuple[str, dict[str, Any]]] = []
    featured: dict[str, Any] | None = None
    best_score = -1.0

    # Build corpus PtV once for all breakdowns (pass through to each for momentum calculation).
    from analysis.network_metrics import build_transition_network
    corpus_g = build_transition_network(session)
    ptv_v = path_to_victory(corpus_g)

    # Warm the identity map once so per-match session.get(Athlete) hits cache, not a remote
    # round-trip per distinct athlete.
    list(session.execute(select(Athlete)).scalars())

    for match in _final_matches(session):
        a = session.get(Athlete, match.athlete_a_id)
        b = session.get(Athlete, match.athlete_b_id)
        if a is None or b is None:
            continue
        slug = match_slug(a, b, match.year)
        bd = build_match_breakdown(match, a, b, ptv_v=ptv_v)
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
    return rows, full, featured


def _node_video_refs(
    aid: str, matches: list[Any], session: Session
) -> dict[str, dict[str, Any]]:
    """node_key → {vid, ts, slug}: the first timestamped use of each technique by this
    athlete across their filmed bouts, so a career-graph node can play the actual footage."""
    refs: dict[str, dict[str, Any]] = {}
    for m in matches:
        ref = _video_ref(getattr(m, "video_url", None))
        if ref is None:
            continue
        vid, _ = ref
        a = session.get(Athlete, m.athlete_a_id)
        b = session.get(Athlete, m.athlete_b_id)
        slug = match_slug(a, b, m.year) if a and b else None
        for e in m.sequence or []:
            if not isinstance(e, dict) or e.get("actor_id") != aid:
                continue
            ts = e.get("ts")
            key = _normalize_name(str(e.get("label", "")))
            if ts is None or not key or key in refs:
                continue
            refs[key] = {"vid": vid, "ts": int(ts), "slug": slug}
    return refs


def build_fighters(
    session: Session,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Returns (GA_FIGHTERS card rows, {slug: {athlete, profile, career}}) for ≥3-bout
    fighters. The profile + career graph are computed once and reused for the dossier."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    system_profiles: dict[str, Any] = {}
    # Warm the identity map once so per-match opponent-ELO lookups (defense_profile →
    # opponent_input_elo → session.get) hit cache instead of a remote round-trip each.
    list(session.execute(select(Athlete)).scalars())
    # All final bouts once → per-athlete index, so the per-fighter dilemma build below reuses
    # them instead of a remote SELECT per fighter (was an N+1 over remote Supabase).
    all_finals = _final_matches(session)
    matches_by_athlete: dict[str, list[Any]] = {}
    for _m in all_finals:
        matches_by_athlete.setdefault(_m.athlete_a_id, []).append(_m)
        matches_by_athlete.setdefault(_m.athlete_b_id, []).append(_m)
    for match in all_finals:
        for aid in (match.athlete_a_id, match.athlete_b_id):
            if aid in seen:
                continue
            seen.add(aid)
            athlete = session.get(Athlete, aid)
            if athlete is None or not qualifies(aid, session):
                continue
            profile = build_style_profile(athlete, session)
            # Surface the real emergent archetype (RF01, deviance v3) instead of "Grappler".
            profile["archetype"] = _archetype(athlete, session) or profile.get("archetype")
            # Hide irrelevant dossiers: a striker with a couple of scrambles is noise.
            if profile["grappling_events"] < MIN_DOSSIER_EVENTS:
                continue
            career = _career_graphview(athlete, profile, session, 12)
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
                "arch_en": arche, "arch_pt": arche, "rec": sub,
                "href": f"grapple-{slug}.html",
                "nodes": card["nodes"], "links": card["links"],
                "_rank": rank or 9999,
            })
            ag = from_career_graphview(athlete.name, career)
            system_profile = build_system_profile(athlete.name, ag)
            system_profiles[slug] = system_profile

            # Per-fighter dilemmas: build from their final matches (from the in-memory index;
            # all_finals already filtered to sequence-bearing bouts), get top-3 forks.
            athlete_matches = matches_by_athlete.get(aid, [])[:30]
            athlete_sequences = [m.sequence for m in athlete_matches if m.sequence]
            fighter_dilemmas_list = []
            fighter_counters_list: list[dict[str, Any]] = []
            if athlete_sequences:
                try:
                    fighter_g = network_from_sequences(athlete_sequences)
                    fighter_ptv = path_to_victory(fighter_g)
                    fighter_dilemmas = dilemmas(fighter_g, fighter_ptv)
                    fighter_dilemmas_list = [
                        {
                            "node": d["node"],
                            "branches": [[b, ptv] for b, ptv in d["branches"]],
                        }
                        for d in fighter_dilemmas[:3]
                    ]
                    # Counter moves: the athlete's best-value responses per position;
                    # keep the few positions whose top counter lands highest.
                    cm = counter_moves(fighter_g, fighter_ptv, top_k=2, min_count=2)
                    top_cm = sorted(
                        cm.items(), key=lambda kv: kv[1][0]["ptv"], reverse=True
                    )[:5]
                    fighter_counters_list = [
                        {
                            "technique": node,
                            "counters": [
                                {"move": c["counter"], "leads_to": c["leads_to"]}
                                for c in cs
                            ],
                        }
                        for node, cs in top_cm
                    ]
                except Exception:
                    pass  # graceful fallback if graph is too sparse

            try:
                fighter_defense = defense_profile(aid, athlete_matches, session)
            except Exception:
                fighter_defense = None

            details[slug] = {
                "athlete": athlete,
                "profile": profile,
                "career": career,
                "_systems": profile_to_dict(system_profile),
                "_dilemmas": fighter_dilemmas_list,
                "_counters": fighter_counters_list,
                "_defense": fighter_defense,
                "_videos": _node_video_refs(aid, athlete_matches, session),
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


def build_elo(session: Session, limit: int = 8) -> dict[str, list[list[Any]]]:
    """Per-discipline leaderboards, rows as RELATIVE values (% of that board's #1
    rating) — never the raw number. Shape: {discipline: [[rank, name, "NN%", NN], …]}."""
    from analysis.discipline import ranked_pools

    boards: dict[str, list[list[Any]]] = {}
    for d, rows in ranked_pools(session).items():
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
  <a class="brand" href="index.html"><span class="mark">GA</span>Grappling<span class="o">Arc</span></a>
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
  <a class="brand" href="index.html"><span class="mark">GA</span>Grappling<span class="o">Arc</span></a>
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


def _head(title: str, description: str = "", path: str = "", image: str = "logo.svg") -> str:
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
<link rel="icon" type="image/svg+xml" href="logo.svg"/>
<link rel="stylesheet" href="site.css"/></head><body>"""


def _prose_html(sections: list[tuple[str, list[str]]]) -> str:
    parts = []
    for heading, paras in sections:
        parts.append(f'<h2 class="sec-label">{html.escape(heading)}</h2>')
        body = "".join(f"<p>{html.escape(p)}</p>" for p in paras)
        parts.append(f'<div class="editorial">{body}</div>')
    return "\n".join(parts)


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
  gaPlayer.seekTo(Math.max(0,(t|0)-5),true);gaPlayer.playVideo();  // -5s → show the setup
  document.getElementById('ytFrame').scrollIntoView({behavior:'smooth',block:'center'});
}
// decisive sequence graph
GAGraph.mount(document.getElementById('seqGraph'),{mode:'map',swim:true,pan:true,zoom:true,nodes:BD.graph.nodes,links:BD.graph.links,
  onSelect:n=>{if(n&&n.ts!=null)gaSeek(n.ts);}});
const lc=[['takedown','Takedown'],['control','Control'],['guard','Guard'],['pass','Passing'],['sweep','Sweep'],['submission','Submission'],['escape','Escape'],['transition','Transition']];
const dot=c=>'<span class="dot" style="background:'+c+'"></span>';
document.getElementById('seqLegend').innerHTML=
  lc.filter(([k])=>BD.graph.nodes.some(n=>n.cat===k)).map(([k,l])=>'<span>'+dot(GAGraph.CAT[k])+l+'</span>').join('')
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
        if fslug in dossier_slugs:
            btns.append(f'<a class="btn" href="grapple-{fslug}.html">'
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
    payload = {
        "a": a["name"], "b": b["name"],
        "graph": bd["transition_graph_gv"],
        "stats": {"momentum_series": stats.get("momentum_series", []),
                  "momentum_ts": stats.get("momentum_ts", [])},
        "timeline": bd.get("event_timeline", []),
        "vid": ref[0] if ref else None,
    }
    has_seek = bool(ref) and any(n.get("ts") is not None
                                 for n in bd["transition_graph_gv"]["nodes"])
    seq_hint = ("Each node is a position; each edge a transition, coloured by who initiated it. "
                + ("Click a node to jump the video to that moment; hover to isolate its connections."
                   if has_seek else "Hover any node to isolate its connections."))
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
  <div class="prose"><p class="lead art-sum">{html.escape(sections[0][1][0])}</p></div>
</div></section>
{_youtube_embed(meta.get('video_url'))}
<article class="art">
  <section class="block"><div class="wrap viz"><div class="statgrid">{stat_grid}</div></div></section>
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
  <section class="block"><div class="wrap prose">{_prose_html(sections[1:])}</div></section>
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
    img = f"assets/fighters/{slugify(a['name'])}.jpg"
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
// click a career node → play the first filmed use of that position (P.videos: key→{vid,ts,slug})
function gaWatch(ref){
  const wrap=document.getElementById('dossierVideo'); if(!wrap||!ref) return;
  wrap.style.display='block';
  wrap.innerHTML='<div style="position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:inherit">'
    +'<iframe src="https://www.youtube-nocookie.com/embed/'+ref.vid+'?start='+Math.max(0,ref.ts|0)+'&autoplay=1"'
    +' title="Technique footage" frameborder="0" style="position:absolute;inset:0;width:100%;height:100%"'
    +' allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>'
    +(ref.slug?'<p class="graph-hint" style="padding:8px 12px;margin:0">Footage from <a href="breakdown-'+ref.slug+'.html" style="color:var(--ink-2);text-decoration:underline">this bout</a></p>':'');
  wrap.scrollIntoView({behavior:'smooth',block:'center'});
}
// career graph
GAGraph.mount(document.getElementById('careerGraph'),{mode:'map',swim:true,pan:true,zoom:true,nodes:P.graph.nodes,links:P.graph.links,
  onSelect:n=>{if(n&&P.videos)gaWatch(P.videos[n.id]);}});
const lg=[['guard','Guard'],['pass','Passing'],['control','Control'],['submission','Submission'],['takedown','Takedown'],['transition','Transition']];
document.getElementById('legend').innerHTML=lg.map(([k,l])=>'<span><span class="dot" style="background:'+GAGraph.CAT[k]+'"></span>'+l+'</span>').join('');
// signature frequency
document.getElementById('sigFreq').innerHTML=P.signature.map(s=>{const pct=Math.round(s.pct*100);
  return '<div class="freq-row"><div class="top"><span class="name">'+s.label+'</span><span class="pct">'+pct+'%</span></div>'
    +'<div class="freq-track"><div class="freq-fill" style="width:'+Math.max(6,pct)+'%"></div></div></div>';}).join('');
// response tree
document.getElementById('tree').innerHTML=P.responses.map(b=>{
  const resp=b.moves.map(r=>'<div class="resp"><span class="pct">'+Math.round(r.pct*100)+'%</span>'
    +'<span class="rtxt">'+r.move+'</span><span class="rbar"><span class="rfill" style="width:'+Math.max(6,Math.round(r.pct*100))+'%"></span></span></div>').join('');
  return '<div class="branch"><div class="q"><span class="ic">'+b.icon+'</span><span class="qt">When '+b.situation+'…</span></div>'+resp+'</div>';}).join('') || '<p class="editorial">Not enough mapped exchanges yet.</p>';
// linked matches
document.getElementById('linked').innerHTML=P.bouts.slice(0,3).map(m=>
  '<a class="mcard" href="breakdown-'+m.slug+'.html"><div class="ev">'+(m.year||'')+'</div><div class="op">'+m.result+'</div><div class="rs">'+(m.win_type||'')+'</div></a>').join('');
if(document.body.classList.contains('lang-pt')) GALang.set('pt');
"""


def render_profile_page(profile: dict[str, Any]) -> str:
    f = profile["fighter"]
    fin = profile["finishing"]
    fam = fin.get("submission_family", {})
    sections = profile_narrative(profile)
    rec = f["record"]
    rank = f.get("elo_rank")
    icons = {"taken down": "T", "guard passed": "P", "back taken": "B", "swept": "S"}
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
    payload = {
        "radar": {"labels": _RADAR_LABELS, "values": radar_values},
        "graph": profile["_career_gv"],
        "signature": profile["signature_techniques"],
        "responses": [
            {"situation": sit, "icon": icons.get(sit, "?"), "moves": data["moves"]}
            for sit, data in profile["responses"].items()
        ],
        "bouts": profile["bouts"],
        "videos": profile.get("_videos") or {},
    }
    graph_hint = "Drag to pan, scroll to zoom · touch to swim · hover to isolate a pathway"
    if payload["videos"]:
        graph_hint += " · click a position to watch it in a real bout"
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
    bio = sections[0][1][0] if sections else ""
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
    <h2 class="h-lg mt16">What he stops, weighted by who threw it</h2></div>
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
    <h2 class="h-lg mt16">His highest-value answer from each position</h2></div>
  <div class="forks"><div class="fork-rows">{rows}</div></div>
  <p class="graph-hint">Ranked by Path-to-Victory value of where the response lands.</p>
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
      <span class="arch">● {html.escape(arche)}</span>
      <h1>{_name_break(html.escape(f['name']))}</h1>
      <div class="sub">{sub_meta}</div>
      <p class="editorial bio">{html.escape(bio)}</p>
    </div>
    <div class="radar-card"><div class="rt">Style fingerprint</div>
      <div class="radar-wrap"><canvas id="radar" width="320" height="300"></canvas></div></div>
  </div>
</div></section>
<section class="wrap career">
  <div class="sec-head" style="margin-bottom:14px">
    <span class="eyebrow">The system, not the match</span>
    <h2 class="h-lg mt16">One graph for an entire grappling game</h2></div>
  <div class="graph-card"><canvas id="careerGraph" class="graph-canvas" style="height:440px"></canvas>
    <div class="graph-legend" id="legend"></div></div>
  <p class="graph-hint">{graph_hint}</p>
  <div id="dossierVideo" class="graph-card" style="display:none;margin-top:14px"></div>
</section>
{systems_html}
<section class="mod"><div class="wrap"><div class="mod-grid">
  <div class="mod-intro"><span class="eyebrow">Signature game</span>
    <h2 class="mt16">What he reaches for first</h2>
    <div class="editorial">{_prose_html([sections[1]]) if len(sections) > 1 else ''}</div></div>
  <div class="freq" id="sigFreq"></div>
</div></div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow orange">Response patterns</span>
    <h2 class="h-lg mt16">How he reacts when the position changes</h2></div>
  <div class="tree" id="tree"></div>
</div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">Finishing profile</span>
    <h2 class="h-lg mt16">Where the matches end</h2></div>
  <div class="fingrid">{fincards}</div>
</div></section>
{defense_html}
{counters_html}
<section class="mod"><div class="wrap">
  <div class="sec-head flex jb ac wrap-fx" style="gap:14px"><div>
    <span class="eyebrow">From abstract to concrete</span>
    <h2 class="h-lg mt16">See the system in action</h2></div>
    <a class="btn" href="breakdowns.html">All breakdowns →</a></div>
  <div class="mgrid" id="linked"></div>
  <p class="graph-hint" style="margin-top:30px">Lead photo via <a href="https://commons.wikimedia.org/" style="color:var(--ink-3);text-decoration:underline">Wikimedia Commons</a> (CC BY) — see <a href="assets/fighters/LICENSES.md" style="color:var(--ink-3);text-decoration:underline">credits</a>.</p>
</div></section>
<section class="sec-pad-sm"><div class="wrap"><div class="appstrip">
  <div class="orb">GA</div>
  <div style="flex:1;min-width:240px">
    <h2 class="h-lg">Grapple like {html.escape(f['name'])}</h2>
    <p class="muted mt8" style="max-width:48ch">Turn this game into a Project in the GrapplingArc app — it maps {html.escape(f['name'].split()[0])}'s signature entries against your own graph and shows exactly which positions to add.</p>
  </div>
  <a class="btn app lg" href="index.html#app">Start this Project →</a>
</div></div></section>
{_FOOTER}
<script src="graph.js"></script><script src="i18n.js"></script>
<script>const P = {json.dumps(payload, ensure_ascii=False)};
{_PROFILE_JS}</script></body></html>"""
    pslug = slugify(f["name"])
    arche = profile.get("archetype") or "grappler"
    desc = (f"How {f['name']} wins, mapped from match data — a {arche} dossier: signature "
            f"entries, response patterns and finishing profile. The system, not the match.")
    return _head("Grapple Like " + f["name"], description=desc,
                 path=f"grapple-{pslug}.html", image=f"assets/fighters/{pslug}.jpg") + body


# ── event (card) pages ───────────────────────────────────────────────────────
def build_events(
    session: Session,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Returns (GA_EVENTS card rows, [(slug, event profile)]) for cards with ≥3 bouts."""
    rows: list[dict[str, Any]] = []
    details: list[tuple[str, dict[str, Any]]] = []
    for name in event_names(session):
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


def render_event_page(slug: str, ep: dict[str, Any]) -> str:
    sections = event_narrative(ep)
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
    bout_cards = "".join(
        f'<a class="mcard" href="breakdown-{b["slug"]}.html">'
        f'<div class="ev">{html.escape(str(b["year"] or ""))}</div>'
        f'<div class="op">{html.escape(b["a"] + " vs " + b["b"])}</div>'
        f'<div class="rs">{html.escape((b["winner"] or "—") + " · " + b["method"])}</div></a>'
        for b in ep["bouts"]
    )
    body = f"""{_nav('events')}
<section class="art-hero"><div class="wrap">
  <div class="center"><a href="events.html" class="tag" style="text-decoration:none">← Events</a></div>
  <h1 class="art-title">{html.escape(name)}</h1>
  <div class="result-bar">{tagrow}</div>
  <div class="prose"><p class="lead art-sum">{html.escape(sections[0][1][0])}</p></div>
</div></section>
<article class="art">
  <section class="block"><div class="wrap viz"><div class="sig-cards">{stat_cards}</div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose">{_prose_html(sections[1:])}</div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><h2 class="sec-label">Every bout</h2></div>
    <div class="wrap viz"><div class="mgrid">{bout_cards}</div></div></section>
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
.ocean-close{position:absolute;top:13px;right:16px;background:none;border:none;color:var(--ink-3);font-size:23px;cursor:pointer;line-height:1}
.ocean-panel h2{font-size:21px;margin:0 30px 8px 0;letter-spacing:-.3px}
.op-metrics{margin-top:18px;display:flex;flex-direction:column;gap:12px}
.op-metric .op-mh{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:5px}
.op-bar{height:7px;background:#1a1a22;border-radius:5px;overflow:hidden}
.op-fill{height:100%;background:linear-gradient(90deg,var(--blue),var(--orange));border-radius:5px}
.op-sec{font-family:var(--mono);font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--ink-3);margin:18px 0 8px}
.op-tags{display:flex;flex-wrap:wrap;gap:6px}
.muted{color:var(--ink-3);font-size:12px}
@media(max-width:600px){
  .ocean-panel{top:auto;bottom:0;height:auto;max-height:52vh;width:100%;border-left:none;border-top:1px solid var(--line);box-shadow:0 -22px 44px rgba(0,0,0,.4)}
  .ocean-hud{max-width:none;right:18px}
}
</style>"""

_OCEAN_BODY = """<section class="ocean-stage">
  <canvas id="oceanGraph" class="ocean-canvas"></canvas>
  <div class="ocean-hud">
    <div class="ocean-h"><h1>The Ocean</h1><p class="muted" id="oceanMeta"></p></div>
    <input id="oceanSearch" class="ocean-search" placeholder="find a technique…" autocomplete="off"/>
    <div id="oceanLegend" class="ocean-legend"></div>
  </div>
  <aside id="oceanPanel" class="ocean-panel" hidden>
    <button id="oceanClose" class="ocean-close" aria-label="close">&times;</button>
    <h2 id="opName"></h2><div id="opMeta"></div>
    <div id="opMetrics" class="op-metrics"></div>
    <div id="opNeighbours"></div><div id="opEdges"></div>
  </aside>
</section>"""

_OCEAN_JS = """
var O = window.GA_OCEAN || {nodes:[],links:[],regions:[],meta:{}};
var byId = {}; O.nodes.forEach(function(n){ byId[n.id]=n; });
document.getElementById('oceanMeta').textContent =
  (O.meta.positions||0)+' techniques · '+(O.meta.transitions||0)+' transitions · '+(O.regions||[]).length+' regions';
document.getElementById('oceanLegend').innerHTML = (O.regions||[]).map(function(r){
  return '<span class="ocean-chip"><i style="background:'+r.color+'"></i>'+r.name+'</span>'; }).join('');
var panel = document.getElementById('oceanPanel');
var g = GAGraph.mount(document.getElementById('oceanGraph'), {mode:'map',
  nodes:O.nodes.map(function(n){return {id:n.id,label:n.label,cat:n.type,size:n.size,color:n.color};}),
  links:O.links, onSelect:onSelect,
  pan:true, zoom:true, zoomOnSelect:true,        // drag to pan, wheel to zoom, click zooms in
  collide:true, swim:true,                       // no node overlap; mobile = zoomed-in thick-water nav
  charge:7000, linkDist:64, gravity:0.0009, bounded:false});  // spread out, no border tension
function bar(title, m){
  if(!m) return '';
  var top = 100 - m.pct;
  var note = (m.ratio && m.ratio>0) ? (' · ×'+m.ratio+' avg') : '';
  return '<div class="op-metric"><div class="op-mh"><span>'+title+'</span>'+
    '<span class="muted">top '+top+'%'+note+'</span></div>'+
    '<div class="op-bar"><div class="op-fill" style="width:'+Math.max(3,m.pct)+'%"></div></div></div>';
}
function onSelect(node){
  var s=document.getElementById('oceanSearch'), lg=document.getElementById('oceanLegend');
  if(!node || !byId[node.id]){ panel.hidden=true; if(s) s.style.display=''; if(lg) lg.style.display=''; return; }
  if(s){ s.style.display='none'; s.blur(); }   // hide search + region legend while a node is focused
  if(lg){ lg.style.display='none'; }
  var n = byId[node.id], mt = n.metrics||{};
  var region = ((O.regions||[])[n.region]||{}).name || 'Unclustered';
  var nb = (n.neighbours||[]).map(function(x){var t=byId[x.node_key];
    return '<span class="tag">'+(t?t.label:x.node_key)+'</span>';}).join('');
  var outs = O.links.filter(function(e){return e.from===n.id;}).map(function(e){
    var t=byId[e.to];return t?t.label:e.to;}).slice(0,8).join(', ');
  document.getElementById('opName').textContent = n.label;
  document.getElementById('opMeta').innerHTML =
    '<span class="tag" style="border-color:'+n.color+';color:'+n.color+'">'+region+'</span> '+
    '<span class="muted">'+n.type+' · seen '+n.occ+'×</span>';
  document.getElementById('opMetrics').innerHTML =
    bar('Frequency',mt.frequency)+bar('Centrality',mt.centrality)+bar('Bridging',mt.bridging)+
    bar('Favorability',mt.favorability)+bar('Effectiveness',mt.effectiveness);
  document.getElementById('opNeighbours').innerHTML = nb ? '<div class="op-sec">Similar positions</div><div class="op-tags">'+nb+'</div>' : '';
  document.getElementById('opEdges').innerHTML = outs ? '<div class="op-sec">Leads to</div><div class="muted">'+outs+'</div>' : '';
  panel.hidden=false;
}
document.getElementById('oceanClose').addEventListener('click', function(){ panel.hidden=true; g.select(null); });
function locate(){
  var q=(document.getElementById('oceanSearch').value||'').toLowerCase().trim(); if(!q) return;
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


def export_site(session: Session, out: Path) -> dict[str, int]:
    from time import perf_counter as _pc

    def _phase(label: str, t0: float) -> float:
        logger.info("  [export] %s: %.1fs", label, _pc() - t0)
        return _pc()

    out.mkdir(parents=True, exist_ok=True)
    _ARCH_CACHE.clear()  # fresh archetype reads per export run
    # Prune stale generated detail pages so hidden fighters / dropped bouts don't orphan
    # (keep the hand-written static grapple-like.html index).
    for old in (*out.glob("breakdown-*.html"), *out.glob("grapple-*.html"),
                *out.glob("event-*.html")):
        if old.name != "grapple-like.html":
            old.unlink()
    _t = _pc()
    rows, full, featured = build_breakdowns(session)
    _t = _phase("build_breakdowns", _t)
    fighters, details = build_fighters(session)
    _t = _phase("build_fighters", _t)
    events, event_details = build_events(session)
    _t = _phase("build_events", _t)
    elo = build_elo(session)
    _t = _phase("build_elo", _t)

    bd_js = _js_file("GA_BREAKDOWNS", rows)
    bd_js += f"window.GA_FEATURED = {json.dumps(featured, ensure_ascii=False)};\n"
    (out / "breakdowns-data.js").write_text(bd_js, encoding="utf-8")
    (out / "fighters-data.js").write_text(_js_file("GA_FIGHTERS", fighters), encoding="utf-8")
    (out / "events-data.js").write_text(_js_file("GA_EVENTS", events), encoding="utf-8")
    (out / "elo-data.js").write_text(_js_file("GA_ELO", elo), encoding="utf-8")

    from analysis.ocean import build_ocean
    ocean = build_ocean(session)
    (out / "ocean-data.js").write_text(_js_file("GA_OCEAN", ocean), encoding="utf-8")
    (out / "the-ocean.html").write_text(render_ocean_page(), encoding="utf-8")
    _t = _phase("build_ocean + data.js", _t)

    # per-match detail pages (attach archetypes + adapted graph for the template)
    dossier_slugs = frozenset(details)  # fighters that actually have a Grapple Like dossier
    slow = ("", 0.0)
    for slug, bd in full:
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
        profile["_systems"] = d.get("_systems") or {}
        profile["_analogues"] = d.get("analogues") or []
        profile["_videos"] = d.get("_videos") or {}
        profile["_counters"] = d.get("_counters") or []
        profile["_defense"] = d.get("_defense") or None
        (out / f"grapple-{slug}.html").write_text(
            render_profile_page(profile), encoding="utf-8")
        if _pc() - _s > slow[1]:
            slow = (slug, _pc() - _s)
    logger.info("  [export] render dossiers: %.1fs (slowest %s %.2fs)", _pc() - _t, *slow)
    _t = _pc()

    # per-event card pages
    for slug, ep in event_details:
        (out / f"event-{slug}.html").write_text(
            render_event_page(slug, ep), encoding="utf-8")
    _t = _phase("render events", _t)

    # robots.txt + sitemap.xml (acquisition baseline — the site was invisible to crawlers).
    static_pages = ["index.html", "breakdowns.html", "events.html", "grapple-like.html",
                    "the-data.html", "the-ocean.html"]
    urls = static_pages + [f"breakdown-{s}.html" for s, _ in full] \
        + [f"grapple-{s}.html" for s in details] + [f"event-{s}.html" for s, _ in event_details]
    locs = "\n".join(f"  <url><loc>{SITE_BASE}/{u}</loc></url>" for u in urls)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{locs}\n</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n", encoding="utf-8")

    return {"breakdowns": len(full), "fighters": len(details),
            "events": len(event_details), "elo": sum(len(rows) for rows in elo.values()),
            "ocean": len(ocean["nodes"])}


def run(out: Path) -> int:
    from db.base import db_session
    with db_session() as session:
        counts = export_site(session, out)
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
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="site output dir")
    args = ap.parse_args()
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
