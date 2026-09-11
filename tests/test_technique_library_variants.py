"""Guard against ambiguous variants in ``analysis/data/technique_library.json``.

``analysis/technique_match._index`` indexes every entry's ``en``/``pt``/``variants`` in one
pass (``setdefault`` — first entry in file order wins), so a variant string that is also
someone else's canonical name is resolved safely (canonical order in the file protects it),
but two DIFFERENT entries listing the same variant is a silent, order-dependent collision —
same defect class as the App's ``localizeTechniqueLabel`` alias map
(``GrapplingArcApp/src/utils/__tests__/localizeTechniqueLabel.test.ts``).
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.names import _normalize_name

_LIB_PATH = Path(__file__).resolve().parent.parent / "analysis" / "data" / "technique_library.json"

# Reviewed 2026-09-11 (library-pressao-collision, same pass as the App-side fix). Two
# categories, both left alone on purpose — any NEW key here is a real bug (see the
# "pressão" / "biceps slicer" fixes in the same pass):
#
# 1. Genuine BJJ terminology overlap (mirrors the App's whitelist exactly — same 4 terms,
#    App library has no "escudo de joelho"/"ganchos"/"roll"/"chave de pe" entries so those
#    4 don't show up there):
#    knee shield / escudo de joelho (pt), ashi garami, side mount, ude garami, ganchos
#    ("hooks" — butterfly hooks vs. back-control hooks-in), roll (rolling vs. roll-through),
#    chave de pe (foot lock generically vs. the toe-hold variant of it).
# 2. Likely DUPLICATE library entries, not ambiguous variants — reported, not merged here
#    (merging changes node identity for every match already labeled with these names, out
#    of this task's scope): "Armbar" and "Arm Lock" share the same `pt` ("Chave de Braço")
#    and near-identical variant lists; "Reverse De La Riva" and "Inverted De La Riva Guard"
#    likewise (both De La Riva-inverted, overlapping variants incl. "rdlr"). Neither "Arm
#    Lock" nor "Reverse De La Riva"/"Inverted De La Riva Guard" (as standalone entries) nor
#    "Foot Lock"/"Hooks In"/"Roll-Through" exist in the App's node library at all — this
#    library has drifted from the "slim copy of the app's node library" the module docstring
#    claims. Flagged for the orchestrator; not touched here.
KNOWN_AMBIGUOUS_TERMS = {
    "knee shield", "escudo de joelho", "ashi garami", "side mount", "ude garami",
    "ganchos", "roll", "chave de pe",
    "armlock", "rdlr",
}


def _load_library() -> list[dict]:
    return json.loads(_LIB_PATH.read_text(encoding="utf-8"))


def test_no_variant_is_ambiguous_across_two_different_entries() -> None:
    entries = _load_library()

    canonical_owner: dict[str, str] = {}
    for tech in entries:
        identity = str(tech.get("en", "")).strip()
        for value in (tech.get("en", ""), tech.get("pt", "")):
            key = _normalize_name(str(value))
            if key:
                canonical_owner[key] = identity

    variant_owners: dict[str, set[str]] = {}
    for tech in entries:
        identity = str(tech.get("en", "")).strip()
        for variant in tech.get("variants", []) or []:
            key = _normalize_name(str(variant))
            if not key or key in canonical_owner:
                continue
            variant_owners.setdefault(key, set()).add(identity)

    collisions = sorted(
        f"{key} -> {', '.join(sorted(owners))}"
        for key, owners in variant_owners.items()
        if len(owners) > 1 and key not in KNOWN_AMBIGUOUS_TERMS
    )
    assert collisions == []
