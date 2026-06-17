"""
Technique Library Export — enriched technique library for the GrapplingArc app.

Produces JSON matching NodeLibraryItem[] format with ADCC effectiveness scores.

Sources:
  - grappling_techniques dataset (76 techniques, 4 origins)
  - ADCC historical matches (1,028 matches, 32 submission types)
  - Existing app grappling-arch.nodes.json (137 nodes)

Output:
  - data/processed/technique_library.json       → NodeLibraryItem[] ready for app import
  - data/processed/technique_effectiveness.json → technique_name → effectiveness metrics
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis.names import _normalize_adcc_sub, _normalize_name, _resolve_aliases
from pipelines.adcc_historical import ADCCHistoricalPipeline
from pipelines.etl import PROCESSED_DIR
from pipelines.grappling_techniques import GrapplingTechniquesPipeline

logger = logging.getLogger(__name__)


def _name_in_nodes(name: str, nodes: list[dict[str, Any]]) -> bool:
    """Check if a normalized technique name exists in existing app nodes."""
    resolved = _resolve_aliases(name)
    for n in nodes:
        if _resolve_aliases(_normalize_name(str(n.get("name", "")))) == resolved:
            return True
        en_val = n.get("translations", {}).get("en", "")
        if _resolve_aliases(_normalize_name(str(en_val))) == resolved:
            return True
        pt_val = n.get("translations", {}).get("pt", "")
        if _resolve_aliases(_normalize_name(str(pt_val))) == resolved:
            return True
    return False

# ── Type maps: dataset → app ─────────────────────────────────

TECHNIQUE_TYPE_MAP: dict[str, str] = {
    "guard": "guard",
    "submissions": "submission",
    "sweeps": "sweep",
    "takedowns": "takedown",
    "escapes": "escape",
    "control": "control",
    "mount": "control",
    "transitions": "transition",
    "miscellaneous": "concept",
    "takedown defense": "defensive",
    "defensive": "defensive",
}

# Portuguese translations for technique names
DEFAULT_PT_TRANSLATIONS: dict[str, str] = {
    "armbar": "Chave de Braço",
    "triangle choke": "Triângulo",
    "rear naked choke": "Mata-Leão",
    "guillotine choke": "Guilhotina",
    "kimura": "Kimura",
    "americana": "Americana",
    "omoplata": "Omoplata",
    "heel hook": "Chave de Calcanhar",
    "ankle lock": "Chave de Tornozelo",
    "kneebar": "Chave de Joelho",
    "toe hold": "Chave de Pé",
    "wrist lock": "Chave de Pulso",
    "closed guard": "Guarda Fechada",
    "open guard": "Guarda Aberta",
    "half guard": "Meia Guarda",
    "butterfly guard": "Guarda Borboleta",
    "full mount": "Montada",
    "s-mount": "Montada Alta",
    "back mount": "Montada nas Costas",
    "side control": "Cien",
    "knee on belly": "Joelho na Barriga",
    "north-south position": "Norte-Sul",
    "scissor sweep": "Tesoura",
    "flower sweep": "Flor",
    "hip bump sweep": "Bate-Quadrado",
    "hook sweep": "Gancho",
    "elevator sweep": "Elevador",
    "balloon sweep": "Balão",
    "bridge and roll": "Ponte",
    "x-guard sweep": "Guarda X",
    "de la riva sweep": "De La Riva",
    "shrimp escape": "Camarão",
    "hip escape (granby roll)": "Granby",
    "knee slide escape": "Joelho Deslizante",
    "upa (bridge) escape": "Upa",
    "darce choke": "Darce",
    "north south choke": "Mata-Leão Norte-Sul",
    "ezekiel choke": "Ezequiel",
    "bow and arrow choke": "Arco e Flecha",
    "leg drag pass": "Passagem Puxando Perna",
    "berimbolo": "Berimbolo",
    "worm guard": "Guarda Minhoca",
    "lapel guard": "Guarda de Lapela",
    "footlock": "Chave de Pé",
    "inside heel hook": "Chave de Calcanhar Interna",
    "outside heel hook": "Chave de Calcanhar Externa",
    "calf slicer": "Músculo",
    "anaconda": "Anaconda",
    "katagatame": "Katagatame",
    "dogbar": "Dogbar",
    "estima lock": "Estima Lock",
    "headlock": "Gravata",
    "cross face": "Pressão Facial",
    "guillotine": "Guilhotina",
    "triangle": "Triângulo",
    "ezekiel": "Ezequiel",
    "shoulder lock": "Chave de Ombro",
    "wristlock": "Chave de Pulso",
    "leg lock": "Chave de Perna",
    "twister": "Twister",
    "z lock": "Z Lock",
}

# App type → color/icon mapping (for metadata)
TYPE_DISPLAY: dict[str, str] = {
    "guard": "🛡️ Guarda",
    "submission": "🔒 Finalização",
    "sweep": "🌊 Varrida",
    "takedown": "⬇️ Queda",
    "escape": "🏃 Fuga",
    "control": "✋ Controle",
    "transition": "↔️ Transição",
    "concept": "🧠 Conceito",
    "pass": "🦶 Passagem",
    "defensive": "🛡️ Defensivo",
}


# ── Helpers ──────────────────────────────────────────────────

def _slugify(name: str) -> str:
    return name.lower().strip().replace(" ", "-").replace("/", "-") \
        .replace("(", "").replace(")", "")


def _make_oid(index: int) -> dict[str, str]:
    """Generate a stable ObjectID-like identifier starting from a high base."""
    base = 0x700000000000000000000000
    hex_str = f"{base + index:024x}"[:24]
    return {"$oid": hex_str}


# ── Core ─────────────────────────────────────────────────────

def load_all_data() -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Load grappling_techniques, ADCC historical, and existing app nodes."""
    tech_df = GrapplingTechniquesPipeline().run()
    adcc_df = ADCCHistoricalPipeline().run()

    # Load existing app nodes (grappling-arch.nodes.json)
    existing_path = Path(__file__).resolve().parent.parent.parent / \
        "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"
    try:
        with open(existing_path) as f:
            existing_nodes: list[dict[str, Any]] = json.load(f)
        logger.info("Loaded %d existing app nodes from %s", len(existing_nodes), existing_path)
    except FileNotFoundError:
        logger.warning("Existing app nodes file not found at %s", existing_path)
        existing_nodes = []

    return tech_df, adcc_df, existing_nodes


def build_effectiveness(adcc_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build effectiveness scores for all ADCC submission techniques.

    Effectiveness = composite of:
      - sub_count          (total ADCC wins via this sub)
      - stage_depth        (weighted avg: R1=1 → F=5)
      - weight_class_span  (unique weight classes)
      - finals_rate        (% of these subs that happened in finals)
      - sex_versatility    (used across both sexes?)
    """
    subs = adcc_df.dropna(subset=["submission"]).copy()

    # Normalize submission names (merge heel hook variants + common aliases)
    subs["sub_clean"] = subs["submission"].apply(_normalize_adcc_sub)

    # Stage numeric depth
    stage_depth_map = {
        "E1": 1, "R1": 2, "R2": 2, "8F": 2,
        "4F": 3, "SF": 4, "3RD": 4, "3PLC": 4,
        "F": 5, "SPF": 5,
    }
    subs["stage_num"] = subs["stage"].map(stage_depth_map).fillna(2)

    grouped = subs.groupby("sub_clean")

    effectiveness: dict[str, dict[str, Any]] = {}

    for name, grp in grouped:
        count = len(grp)
        avg_stage = grp["stage_num"].mean()
        weight_classes = grp["weight_class"].nunique()
        finals = grp[grp["stage"].isin(["F", "SPF"])]
        finals_pct = len(finals) / count if count > 0 else 0.0
        sexes = grp["sex"].nunique()
        years_span = grp["year"].max() - grp["year"].min()
        win_points_avg = grp["winner_points"].replace(-1, np.nan).mean()
        win_points_avg = 0.0 if pd.isna(win_points_avg) else float(win_points_avg)

        effectiveness[name] = {
            "sub_count": int(count),
            "sub_share": float(count / len(subs)),  # % of all subs
            "stage_depth": round(float(avg_stage), 2),
            "weight_class_span": int(weight_classes),
            "finals_rate": round(finals_pct, 3),
            "sex_span": int(sexes),
            "years_span": int(years_span),
            "avg_winner_points": round(win_points_avg, 2),
        }

    min_sub_count = 3

    # Calculate normalized composite effectiveness score (0-1)
    scores = np.array([e["sub_count"] for e in effectiveness.values()])
    if scores.max() > 0:
        for name, e in effectiveness.items():
            if e["sub_count"] < min_sub_count:
                e["effectiveness_score"] = round(max(0.0, e["sub_count"] / scores.max()), 4)
                continue
            raw = (
                e["sub_count"] / scores.max() * 0.35 +
                e["stage_depth"] / 5 * 0.25 +
                e["weight_class_span"] / 8 * 0.15 +
                e["finals_rate"] * 0.15 +
                min(e["sex_span"] / 2, 1) * 0.10
            )
            e["effectiveness_score"] = round(float(raw), 3)

    return effectiveness


def build_technique_library(
    tech_df: pd.DataFrame,
    effectiveness: dict[str, dict[str, Any]],
    existing_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build enriched NodeLibraryItem[] from all sources."""

    library: list[dict[str, Any]] = []
    seen_normalized: set[str] = set()
    index = 0

    # ── 1. Map from grappling_techniques dataset ──
    for _, row in tech_df.iterrows():
        name_en = str(row.get("technique_name", row.get("Name", ""))).strip()
        if not name_en:
            continue
        # Clean up origin suffixes
        origin = str(row.get("Origin", "")).strip()
        if origin == "Wrestling" and name_en.endswith(" (Wrestling)"):
            name_en = name_en.replace(" (Wrestling)", "").strip()
        if origin == "BJJ" and name_en.endswith(" (BJJ)"):
            name_en = name_en.replace(" (BJJ)", "").strip()
        if origin == "Judo" and name_en.endswith(" (Judo)"):
            name_en = name_en.replace(" (Judo)", "").strip()

        norm = _normalize_name(name_en)
        if norm in seen_normalized:
            continue
        seen_normalized.add(norm)

        tech_type_raw = str(row.get("Type", "")).lower().strip()
        app_type = TECHNIQUE_TYPE_MAP.get(tech_type_raw, "concept")

        pt_name = DEFAULT_PT_TRANSLATIONS.get(norm, name_en)
        translations = {"en": name_en, "pt": pt_name}

        # Check if already exists in app library (with alias resolution)
        existing = _name_in_nodes(norm, existing_nodes)

        entry = {
            "_id": _make_oid(index),
            "name": pt_name,
            "type": app_type,
            "translations": translations,
            "variations": _generate_variations(name_en, pt_name),
            "source": "grappling_techniques_dataset",
            "already_in_app": existing,
        }

        # Attach effectiveness if available
        adcc_eff = effectiveness.get(norm)
        if adcc_eff:
            entry["effectiveness"] = adcc_eff

        library.append(entry)
        index += 1

    # ── 2. Add ADCC-only submissions not in technique dataset ──
    for sub_name, eff in effectiveness.items():
        if sub_name in seen_normalized:
            continue
        # Skip generic entries
        if sub_name in ("submission", "verbal tap", "short choke", "choke"):
            continue
        # All ADCC win-method entries are submissions
        app_type = "submission"

        name_en = sub_name.title()
        pt_name = DEFAULT_PT_TRANSLATIONS.get(sub_name, name_en)
        norm = _normalize_name(name_en)
        if norm in seen_normalized:
            continue
        seen_normalized.add(norm)

        existing = _name_in_nodes(norm, existing_nodes)

        entry = {
            "_id": _make_oid(index),
            "name": pt_name,
            "type": app_type,
            "translations": {"en": name_en, "pt": pt_name},
            "variations": _generate_variations(name_en, pt_name),
            "source": "adcc_submission_data",
            "already_in_app": existing,
            "effectiveness": eff,
        }
        library.append(entry)
        index += 1

    # ── 3. Sort by effectiveness descending (submissions first), then alpha ──
    # Scored entries first (descending score), unscored last, alpha tiebreak
    library.sort(key=lambda x: (
        -x["effectiveness"]["effectiveness_score"] if "effectiveness" in x else 1,
        x["name"],
    ))

    return library


def _generate_variations(en: str, pt: str) -> list[str]:
    """Generate search variations from English and Portuguese names."""
    vars: list[str] = []
    seen = set()
    for src in [en, pt]:
        n = src.lower().strip()
        if n not in seen:
            vars.append(n)
            seen.add(n)
        # Common alternates
        alts = {
            "rear naked choke": ["rnc", "hadaka jime", "mata leao"],
            "guillotine choke": ["guillotine", "guilhotina"],
            "armbar": ["chave de braco", "arm lock"],
            "triangle choke": ["triangle", "sankaku", "triangulo"],
            "heel hook": ["achilles lock", "calcanhar"],
            "kimura": ["double wrist lock", "chave de ombro"],
            "americana": ["paintbrush", "chave de braco inversa"],
            "omoplata": ["shoulder lock", "chave de ombro"],
            "kneebar": ["leg lock", "joelho"],
            "toe hold": ["foot lock", "pe"],
            "ankle lock": ["tornozelo", "foot lock"],
            "darce choke": ["darce", "d'arce", "d arce"],
            "anaconda": ["anaconda choke"],
            "north south choke": ["north-south", "norte sul", "kuzure kami shiho gatame"],
            "calf slicer": ["musculo", "calf crusher"],
            "footlock": ["chave de pe", "foot lock"],
            "guillotine": ["guilhotina", "guillotine choke"],
            "triangle": ["sankaku jime", "triangulo", "triangle choke"],
            "ezekiel": ["ezekiel choke", "ezequiel"],
            "katagatame": ["kata gatame", "shoulder choke"],
            "headlock": ["gravata", "head lock"],
            "leg lock": ["chave de perna", "leglock"],
            "cross face": ["pressao facial", "crossface"],
            "dogbar": ["dog bar"],
            "shoulder lock": ["chave de ombro", "shoulder crank"],
            "twister": ["spinal lock", "body twister"],
            "wristlock": ["chave de pulso", "wrist lock"],
            "z lock": ["z-lock"],
        }
        for alt in alts.get(n, []):
            if alt not in seen:
                vars.append(alt)
                seen.add(alt)
    return vars


def export_library(library: list[dict[str, Any]]) -> tuple[Path, Path]:
    """Export technique library as JSON files."""
    output_dir = PROCESSED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    lib_path = output_dir / "technique_library.json"
    with open(lib_path, "w") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    logger.info("Exported %d techniques to %s", len(library), lib_path)

    # Also export just effectiveness lookup
    eff = {e["name"]: e.get("effectiveness", {}) for e in library if "effectiveness" in e}
    eff_path = output_dir / "technique_effectiveness.json"
    with open(eff_path, "w") as f:
        json.dump(eff, f, indent=2, ensure_ascii=False)
    logger.info("Exported %d effectiveness entries to %s", len(eff), eff_path)

    return lib_path, eff_path


def write_summary_report(library: list[dict[str, Any]], effectiveness: dict[str, Any]) -> str:
    """Write a human-readable analysis report."""
    lines = ["# Technique Library Analysis Report", ""]
    lines.append(f"**Total techniques:** {len(library)}")
    from_dataset = sum(1 for e in library
                       if e.get("source") == "grappling_techniques_dataset")
    from_adcc = sum(1 for e in library
                    if e.get("source") == "adcc_submission_data")
    already = sum(1 for e in library if e.get("already_in_app"))
    new_additions = sum(1 for e in library if not e.get("already_in_app"))
    lines.append(f"**From grappling_techniques dataset:** {from_dataset}")
    lines.append(f"**From ADCC submission data:** {from_adcc}")
    lines.append(f"**Already in app library:** {already}")
    lines.append(f"**New additions for app:** {new_additions}")
    lines.append("")

    # Type breakdown
    from collections import Counter
    type_counts = Counter(e["type"] for e in library)
    lines.append("## Type Distribution")
    for t, c in type_counts.most_common():
        display = TYPE_DISPLAY.get(t, t)
        lines.append(f"- {display}: {c}")
    lines.append("")

    # Top effectiveness
    with_eff = [e for e in library if "effectiveness" in e]
    with_eff.sort(key=lambda x: x["effectiveness"]["effectiveness_score"], reverse=True)
    lines.append("## Top 20 Most Effective ADCC Techniques")
    lines.append("")
    hdr = "| Rank | Technique | Score | Count | Stage Depth | Finals Rate | Weight Classes |"
    sep = "|------|-----------|-------|-------|-------------|-------------|----------------|"
    lines.append(hdr)
    lines.append(sep)
    for i, e in enumerate(with_eff[:20], 1):
        eff = e["effectiveness"]
        name = e["translations"]["en"]
        lines.append(
            f"| {i:2d}  | {name:20s} | {eff['effectiveness_score']:.3f} | "
            f"{eff['sub_count']:3d} | {eff['stage_depth']:.2f} | {eff['finals_rate']:.0%} | "
            f"{eff['weight_class_span']} |"
        )
    lines.append("")

    # Missing from app
    missing = [e for e in library if not e.get("already_in_app") and "effectiveness" in e]
    missing.sort(key=lambda x: x["effectiveness"]["effectiveness_score"], reverse=True)
    if missing:
        lines.append("## High-Effectiveness Techniques Missing from App")
        for e in missing[:10]:
            eff = e["effectiveness"]
            name = e['translations']['en']
            lines.append(
                f"- {name} (score={eff['effectiveness_score']:.3f}, "
                f"{eff['sub_count']} ADCC wins)"
            )
        lines.append("")

    # Techniques by type with avg effectiveness
    lines.append("## Average Effectiveness by Type")
    for t, _ in type_counts.most_common():
        entries = [e for e in with_eff if e["type"] == t]
        if entries:
            avg = sum(e["effectiveness"]["effectiveness_score"] for e in entries) / len(entries)
            lines.append(f"- {TYPE_DISPLAY.get(t, t)}: {avg:.3f} avg ({len(entries)} techniques)")
    lines.append("")

    # ADCC-only submissions (orphans)
    adcc_only = [e for e in library if e.get("source") == "adcc_submission_data"]
    if adcc_only:
        lines.append("## ADCC-Only Submissions (not in techniques dataset)")
        def sort_key(x: dict[str, Any]) -> int | float:
            return x.get("effectiveness", {}).get("sub_count", 0) or 0
        for e in sorted(adcc_only, key=sort_key, reverse=True):
            eff = e.get("effectiveness", {})
            name = e['translations']['en']
            sub_count = eff.get('sub_count', 0)
            score = eff.get('effectiveness_score', 0)
            lines.append(
                f"- {name}: {sub_count} ADCC wins, "
                f"score={score:.3f}"
            )
        lines.append("")

    return "\n".join(lines)


# ── Main entry point ─────────────────────────────────────────

def export_tech_library() -> dict[str, Any]:
    """Run full export pipeline and return summary."""
    logger.info("=" * 60)
    logger.info("Technique Library Export")
    logger.info("=" * 60)

    tech_df, adcc_df, existing_nodes = load_all_data()
    logger.info("Data loaded: %d techniques, %d ADCC matches, %d existing nodes",
                len(tech_df), len(adcc_df), len(existing_nodes))

    effectiveness = build_effectiveness(adcc_df)
    logger.info("Built effectiveness scores for %d submission techniques", len(effectiveness))

    library = build_technique_library(tech_df, effectiveness, existing_nodes)

    lib_path, eff_path = export_library(library)

    report = write_summary_report(library, effectiveness)
    report_path = PROCESSED_DIR / "TECHNIQUE_LIBRARY_REPORT.md"
    with open(report_path, "w") as f:
        f.write(report)
    logger.info("Report written to %s", report_path)

    from_dataset = sum(1 for e in library
                       if e.get("source") == "grappling_techniques_dataset")
    from_adcc = sum(1 for e in library
                    if e.get("source") == "adcc_submission_data")
    already = sum(1 for e in library if e.get("already_in_app"))
    new_count = sum(1 for e in library if not e.get("already_in_app"))
    return {
        "total": len(library),
        "from_dataset": from_dataset,
        "from_adcc": from_adcc,
        "already_in_app": already,
        "new": new_count,
        "with_effectiveness": len(effectiveness),
        "library_path": str(lib_path),
        "effectiveness_path": str(eff_path),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    summary = export_tech_library()
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
