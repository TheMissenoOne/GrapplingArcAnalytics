# ruff: noqa: E501  (long recommendation f-strings are content)
"""Generate combined user insights from user data + competition data.

Loads user JSON via ``UserBundle.from_json()`` and competition data from
``_analytics_export.json`` (the flat 281-match dump). Produces:

- User style profile (8-bucket type vector)
- Archetype classification (matched via nearest competition fighters)
- Nearest competition fighters by cosine similarity
- Technique benchmark vs pro share
- Positional strengths / weaknesses
- Training recommendations
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from analysis.user_profile import (
    TYPES,
    extract_technique_counts,
    extract_transition_bigrams,
    extract_type_vector,
    normalize_technique_name,
    user_graph_profile,
)

logger = logging.getLogger(__name__)


def _athlete_key(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", "", name.lower().strip())
    return re.sub(r"\s+", " ", n).strip()


# ── Competition data loading ──

COMP_SUB_FAMILIES = {
    "strangle": ["choke", "rnc", "rear naked", "guillotine", "triangle", "darce",
                 "ezekiel", "north south", "bow and arrow", "strangle"],
    "leglock": ["heel", "kneebar", "knee bar", "toe hold", "footlock", "foot lock",
                "calf", "leg lock", "leglock"],
    "armlock": ["armbar", "kimura", "americana", "omoplata", "wrist"],
}
COMP_WIN_TYPES = ["SUBMISSION", "DECISION", "POINTS"]


def load_competition_data(path: str | Path = "_analytics_export.json") -> dict[str, Any]:
    """Load the flat 281-match export and build per-fighter profiles.

    Returns dict with:
      - ``matches``: raw match list
      - ``fighters``: set of all fighter names
      - ``profiles``: dict[fighter_key, {type_vec, win_type_mix, sub_families, total}]
      - ``events``: set of event names
    """
    with open(path) as f:
        matches: list[dict] = json.load(f)

    fighters: set[str] = set()
    for m in matches:
        fighters.add(m["fighter"])
        fighters.add(m["opponent"])

    # Build per-fighter type vectors from event sequences
    type_counts: dict[str, Counter] = defaultdict(Counter)
    win_type_counts: dict[str, Counter] = defaultdict(Counter)
    sub_family_counts: dict[str, Counter] = defaultdict(Counter)
    match_counts: dict[str, int] = defaultdict(int)

    for m in matches:
        winner = m["fighter"]
        win_type = m.get("win_type")
        submission = m.get("submission") or ""
        events = m.get("sequence", [])

        match_counts[winner] += 1

        if win_type:
            win_type_counts[winner][win_type] += 1

        if submission:
            sl = submission.lower()
            for fam, kws in COMP_SUB_FAMILIES.items():
                if any(k in sl for k in kws):
                    sub_family_counts[winner][fam] += 1
                    break

        for e in events:
            actor = e.get("actor", "")
            if _athlete_key(actor) != _athlete_key(winner):
                continue
            t = e.get("type", "").lower().strip()
            if t in TYPES:
                type_counts[winner][t] += 1

    profiles: dict[str, dict] = {}
    for fname in fighters:
        key = _athlete_key(fname)
        tc = type_counts.get(fname, Counter())
        total_events = sum(tc.values())
        if total_events < 3:
            continue
        type_vec = np.array([tc.get(t, 0) / total_events for t in TYPES], dtype=np.float64)
        nrm = np.linalg.norm(type_vec)
        if nrm > 0:
            type_vec = type_vec / nrm

        wtc = win_type_counts.get(fname, Counter())
        wt_total = sum(wtc.values()) or 1
        win_type_mix = {wt: wtc.get(wt, 0) / wt_total for wt in COMP_WIN_TYPES}

        sfc = sub_family_counts.get(fname, Counter())
        sf_total = sum(sfc.values()) or 1
        sub_families = {fam: sfc.get(fam, 0) / sf_total for fam in COMP_SUB_FAMILIES}

        profiles[key] = {
            "name": fname,
            "type_vec": type_vec,
            "win_type_mix": win_type_mix,
            "sub_families": sub_families,
            "total_matches": match_counts.get(fname, 0),
            "total_events": total_events,
        }

    return {
        "matches": matches,
        "fighters": sorted(fighters),
        "profiles": profiles,
        "events": sorted(set(m.get("event", "") for m in matches)),
    }


# ── Archetype matching ──

def match_archetype(
    user_vec: np.ndarray,
    comp: dict[str, Any],
    k: int = 3,
) -> dict[str, Any]:
    """Match user to an archetype by weighted nearest competition fighters.

    Uses cosine similarity on the 8-bucket type vector, then takes the
    win_type_mix weighted majority of the top-k nearest fighters.

    Returns dict: {archetype, confidence, nearest_fighters, win_type_mix_est}.
    """
    profiles = comp["profiles"]
    if len(profiles) < k:
        return {"archetype": "Unknown", "confidence": 0.0,
                "nearest_fighters": [], "win_type_mix_est": {}}

    sims: list[tuple[str, float, dict]] = []
    for key, p in profiles.items():
        pv = p["type_vec"]
        if np.linalg.norm(pv) == 0:
            continue
        sim = float(user_vec @ pv)
        sims.append((key, sim, p))

    sims.sort(key=lambda x: -x[1])
    top = sims[:k]

    # Heuristic archetype from weighted win-type mix of nearest fighters
    total_w = sum(max(s, 0) for _, s, _ in top)
    if total_w == 0:
        return {"archetype": "Unknown", "confidence": 0.0,
                "nearest_fighters": [], "win_type_mix_est": {}}

    wt_pooled: dict[str, float] = defaultdict(float)
    for key, s, p in top:
        wt = p.get("win_type_mix", {})
        w = max(s, 0) / total_w
        for wt_name, wt_share in wt.items():
            wt_pooled[wt_name] += wt_share * w

    sub_share = wt_pooled.get("SUBMISSION", 0)
    dec_share = wt_pooled.get("DECISION", 0)
    pts_share = wt_pooled.get("POINTS", 0)

    if sub_share > 0.6:
        archetype = "Submission Hunter"
    elif pts_share > 0.5:
        archetype = "Point Fighter"
    elif dec_share > 0.6:
        archetype = "Decision Artist"
    elif sub_share > 0.4:
        archetype = "Mixed Finisher"
    else:
        archetype = "Balanced"

    nearest = [{"name": p["name"], "similarity": round(s, 3),
                "archetype": "?", "total": p["total_matches"]}
               for key, s, p in top]

    return {
        "archetype": archetype,
        "confidence": round(total_w / len(top), 3),
        "nearest_fighters": nearest,
        "win_type_mix_est": {k: round(v, 3) for k, v in wt_pooled.items()},
    }


# ── Nearest fighters ──

def nearest_fighters(
    user_vec: np.ndarray,
    comp: dict[str, Any],
    n: int = 6,
) -> list[dict[str, Any]]:
    """Top-N competition fighters closest to user style vector."""
    profiles = comp["profiles"]
    sims: list[tuple[str, float, dict]] = []
    for key, p in profiles.items():
        pv = p["type_vec"]
        if np.linalg.norm(pv) == 0:
            continue
        sim = float(user_vec @ pv)
        sims.append((key, sim, p))

    sims.sort(key=lambda x: -x[1])
    results = []
    for key, s, p in sims[:n]:
        results.append({
            "name": p["name"],
            "similarity": round(s, 3),
            "total_matches": p["total_matches"],
            "total_events": p["total_events"],
            "win_type_mix": p.get("win_type_mix", {}),
        })
    return results


# ── Type-level benchmark ──

def _avg_type_vector(comp: dict[str, Any]) -> np.ndarray:
    """Average type vector across all competition fighters with profiles."""
    profiles = comp["profiles"]
    if not profiles:
        return np.zeros(len(TYPES), dtype=np.float64)
    vecs = [p["type_vec"] for p in profiles.values() if np.linalg.norm(p["type_vec"]) > 0]
    if not vecs:
        return np.zeros(len(TYPES), dtype=np.float64)
    return np.mean(vecs, axis=0)


def type_benchmark(
    user_vec: np.ndarray,
    comp: dict[str, Any],
) -> dict[str, Any]:
    """Compare user's type distribution vs competition average.

    Returns per-type deviation (user_share - comp_avg) and emphasis flags.
    """
    avg = _avg_type_vector(comp)
    results = []
    for i, t in enumerate(TYPES):
        dev = float(user_vec[i] - avg[i]) if len(user_vec) > i else 0.0
        emphasis = "high" if dev > 0.02 else ("low" if dev < -0.02 else "normal")
        results.append({
            "type": t,
            "user_share": round(float(user_vec[i]), 3),
            "comp_avg": round(float(avg[i]), 3),
            "deviation": round(dev, 3),
            "emphasis": emphasis,
        })
    return {"types": results, "comp_fighters": len(comp["profiles"])}


# ── Technique-level benchmark ──

def technique_benchmark(
    user_technique_counts: dict[str, int],
    comp: dict[str, Any],
) -> dict[str, Any]:
    """Compare user's technique frequency vs competition.

    Normalizes Portuguese technique names to English via ``normalize_technique_name``
    so labels like "Mata-Leão" match "Rear Naked Choke" in competition data.

    Returns per-technique comparison and summary.
    """
    comp_tech_counts: Counter[str] = Counter()
    for m in comp["matches"]:
        sub = m.get("submission") or ""
        if sub:
            comp_tech_counts[sub.lower().strip()] += 1
        method = m.get("method", "").lower()
        if "decision" in method:
            comp_tech_counts["decision"] += 1
        elif "points" in method:
            comp_tech_counts["points"] += 1

    total_comp = sum(comp_tech_counts.values()) or 1
    total_user = sum(user_technique_counts.values()) or 1

    techniques = []
    for tech, uc in sorted(user_technique_counts.items(), key=lambda x: -x[1]):
        eng = normalize_technique_name(tech)
        tc = comp_tech_counts.get(eng.lower().strip(), 0)
        user_share = uc / total_user
        comp_share = tc / total_comp
        ratio = user_share / (comp_share + 1e-6)
        emphasis = "high" if ratio > 2.0 else ("low" if ratio < 0.5 else "normal")
        techniques.append({
            "technique": tech,
            "english_name": eng,
            "user_count": uc,
            "user_share": round(user_share, 3),
            "comp_count": tc,
            "comp_share": round(comp_share, 3),
            "ratio": round(ratio, 2),
            "emphasis": emphasis,
        })

    valid = [t for t in techniques if t["comp_count"] > 0]
    most_over = max(valid, key=lambda t: t["ratio"])["technique"] if valid and any(t["ratio"] > 1 for t in valid) else None
    most_under = min(valid, key=lambda t: t["ratio"])["technique"] if valid and any(t["ratio"] < 1 for t in valid) else None
    summary = {
        "top_technique": techniques[0]["technique"] if techniques else None,
        "most_overused": most_over,
        "most_underused": most_under,
        "matched_count": len(valid),
        "total_user_techniques": len(user_technique_counts),
    }

    return {"techniques": techniques, "summary": summary}


# ── Recommendations ──

def generate_recommendations(
    insight: dict[str, Any],
) -> list[dict[str, str]]:
    """Synthesize training recommendations from insight data."""
    recs = []

    # From archetype
    arch = insight.get("archetype", {}).get("archetype", "Unknown")
    if arch == "Submission Hunter":
        recs.append({
            "area": "archetype",
            "suggestion": f"You match {arch} style — drill submission chains from your best positions",
            "priority": "medium",
        })
    elif arch == "Point Fighter":
        recs.append({
            "area": "archetype",
            "suggestion": "You favor points — work on submissions to increase finishing rate",
            "priority": "high",
        })
    elif arch == "Decision Artist":
        recs.append({
            "area": "archetype",
            "suggestion": "You win by decision — add submission threats to force openings",
            "priority": "high",
        })

    # From type benchmark — find biggest gaps
    type_bench = insight.get("type_benchmark", {})
    for t in type_bench.get("types", []):
        if t.get("emphasis") == "low" and t.get("deviation", 0) < -0.05:
            recs.append({
                "area": "type_gap",
                "suggestion": f"Low '{t['type']}' share ({t['user_share']:.1%} vs comp avg {t['comp_avg']:.1%}) — add more {t['type']} techniques",
                "priority": "high",
            })

    # From benchmark over/under use
    bench = insight.get("benchmark", {})
    summary = bench.get("summary", {})
    most_over = summary.get("most_overused")
    most_under = summary.get("most_underused")
    if most_under:
        recs.append({
            "area": "technique_gap",
            "suggestion": f"Technique gap: '{most_under}' — common in competition, absent in your sessions",
            "priority": "high",
        })
    if most_over:
        recs.append({
            "area": "over_focus",
            "suggestion": f"You over-index on '{most_over}' vs competition — diversify your game",
            "priority": "medium",
        })

    # From nearest fighters
    nearest = insight.get("nearest_fighters", [])
    if nearest:
        names = ", ".join(n["name"] for n in nearest[:3])
        recs.append({
            "area": "study",
            "suggestion": f"Study these fighters' matches (similar style): {names}",
            "priority": "low",
        })

    # From graph profile
    user_graph = insight.get("user_graph", {})
    if user_graph.get("node_count", 0) < 5:
        recs.append({
            "area": "graph_building",
            "suggestion": "Log more techniques to build your graph and get better matching",
            "priority": "low",
        })

    return recs


# ── Main ──

def generate_insights(
    user_json_path: str | Path,
    competition_path: str | Path = "_analytics_export.json",
) -> dict[str, Any]:
    """Full insights pipeline from user JSON + competition data.

    Returns a dict suitable for JSON export containing all insights.
    """
    # Load user data (raw JSON — works with any export format)
    user_path = Path(user_json_path)
    with open(user_path) as f:
        raw: dict[str, Any] = json.load(f)

    # Load competition data
    comp = load_competition_data(competition_path)

    # Build user profile
    user_type_vec = extract_type_vector(raw)
    user_vec_norm = np.linalg.norm(user_type_vec)
    has_profile = user_vec_norm > 0 and user_type_vec.sum() > 0

    user_tech_counts = extract_technique_counts(raw)
    user_transitions = extract_transition_bigrams(raw)

    # Graph profile
    graph_profile = user_graph_profile(raw)

    # User info
    auth = raw.get("user", {}).get("auth", {})
    belt = auth.get("beltRank", "?")
    name = auth.get("fullName", "User")

    insights: dict[str, Any] = {
        "user": {
            "name": name,
            "belt": belt,
            "total_sessions": len(raw.get("sessions", [])),
            "total_graph_nodes": graph_profile["node_count"],
        },
        "user_profile": {
            "type_vector": user_type_vec.tolist() if has_profile else None,
            "type_labels": TYPES,
            "technique_counts": user_tech_counts,
            "top_transitions": [
                {"from": k[0], "to": k[1], "count": v}
                for k, v in user_transitions.most_common(10)
            ],
            "graph_profile": graph_profile,
        },
    }

    if has_profile:
        # Archetype matching
        arch = match_archetype(user_type_vec, comp)
        insights["archetype"] = arch

        # Nearest fighters
        insights["nearest_fighters"] = nearest_fighters(user_type_vec, comp)

        # Type-level benchmark
        insights["type_benchmark"] = type_benchmark(user_type_vec, comp)

        # Technique-level benchmark
        insights["benchmark"] = technique_benchmark(user_tech_counts, comp)

        # Success rates
        from analysis.user_profile import extract_success_rates
        insights["success_rates"] = {
            k: round(v, 2) for k, v in
            sorted(extract_success_rates(raw).items(),
                   key=lambda x: -x[1])
        }
    else:
        insights["archetype"] = {"archetype": "Insufficient data",
                                 "confidence": 0,
                                 "nearest_fighters": []}
        insights["nearest_fighters"] = []
        insights["benchmark"] = {"techniques": [], "summary": {}}
        insights["success_rates"] = {}

    # Recommendations
    insights["recommendations"] = generate_recommendations(insights)

    return insights


def _to_json_safe(obj: Any) -> Any:
    """Recursively convert numpy types to native Python for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_json_safe(v) for v in obj]
    if hasattr(obj, "dtype"):
        return obj.item() if obj.ndim == 0 else obj.tolist()
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def export_insights(
    user_json_path: str | Path,
    output_path: str | Path = "user_insights.json",
    competition_path: str | Path = "_analytics_export.json",
) -> str:
    """Generate and write insights JSON."""
    insights = generate_insights(user_json_path, competition_path)
    safe = _to_json_safe(insights)
    out = Path(output_path)
    with open(out, "w") as f:
        json.dump(safe, f, indent=2, default=str, ensure_ascii=False)
    logger.info("Exported insights (%d keys) → %s", len(insights), out)
    return str(out)
