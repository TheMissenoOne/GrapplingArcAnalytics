"""Golden fixture for the E4 RRB-dominance-calibration artefact (prereg §E4).

For a handful of hand-picked round shapes: the raw ``actions_states`` ``Z``/``P``, the calibrated
``P`` (via `data/rating/rrb_dominance_calibration.json`), the resulting Elo offset, and the
per-action contribution shares (prereg §E3's ``c_k``). Intended to be byte-identical in an
eventual App port (`services/rating/rrbDominance.ts`, not written here — out of scope for this
Analytics-only study, prereg §E5).

No DB, no network, no clock: reads the committed calibration artefact (not the live corpus) and
hand-written cases, so re-running is byte-identical.

    uv run python -m scripts.export_rrb_dominance_fixtures
    uv run python -m scripts.export_rrb_dominance_fixtures --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.rating_v2.rrb_dominance import (  # noqa: E402
    calibration_apply,
    canonical_code,
    contribution_shares,
    elo_offset,
    granular_score,
    p_own,
)

CALIBRATION_PATH = REPO / "data" / "rating" / "rrb_dominance_calibration.json"
OUT = REPO / "data" / "fixtures" / "rrbDominanceGolden.json"

#: Hand-picked round shapes — pt-BR + English, own + partner sides, mapped + unmapped actions,
#: mirroring the shapes prereg addendum §A1 found in the owner's real log.
CASES: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "own_dominant_takedown_and_control",
        [
            {"type": "takedown", "label": "Queda de Perna Única", "actor": "you", "successful": True},
            {"type": "control", "label": "Costas", "actor": "you", "successful": True},
            {"type": "submission", "label": "Rear Naked Choke", "actor": "you", "successful": True},
        ],
    ),
    (
        "partner_dominant_sweep_and_pass",
        [
            {"type": "sweep", "label": "Butterfly Sweep", "actor": "partner", "successful": True},
            {"type": "pass", "label": "Leg Drag", "actor": "partner", "successful": True},
        ],
    ),
    (
        "mixed_sides_one_mapped_one_not",
        [
            {"type": "control", "label": "Montada", "actor": "you", "successful": True},
            {"type": "guard", "label": "Meia Guarda", "actor": "partner", "successful": None},
            {"type": "submission", "label": "Armbar", "actor": "you", "successful": False},
        ],
    ),
    # ── clean_label rule coverage (App port `rrbDominance.ts` is missing all six below) ──
    (
        # "<X> Attempt" stripped before the library lookup (`_ATTEMPT_RE`) -> canonicalises to
        # "Armbar" -> `successful=True` makes this the ONE terminal (landed) submission case.
        "attempt_suffix_stripped_terminal_submission",
        [
            {"type": "submission", "label": "Armbar attempt", "actor": "you", "successful": True},
        ],
    ),
    (
        # "<X> Attempted" — same strip rule, plural/tense variant, partner side, not landed.
        "attempted_suffix_stripped_partner_not_landed",
        [
            {"type": "submission", "label": "Armlock attempted", "actor": "partner", "successful": False},
        ],
    ),
    (
        # pt-BR "Tentativa de X" is NOT covered by `_ATTEMPT_RE` (English word only) — the label
        # reaches the library lookup unstripped, fails to match, and comes back untouched. Kept
        # mapped here only because `type=submission` decides the Lamas code from the EVENT TYPE
        # regardless of the label (`lamas_state`'s rule 1) — the gap is invisible on this event
        # type and would only bite a control/transition/guard-typed pt-BR "tentativa" label,
        # where the label content is what `lamas_state` reads.
        "ptbr_tentativa_prefix_not_stripped_by_clean_label",
        [
            {"type": "submission", "label": "Tentativa de armlock", "actor": "you", "successful": False},
        ],
    ),
    (
        # Label normalises to a library entry ("Armbar", type submission) but the event's own
        # `type` hint ("guard") disagrees -> `clean_label` REJECTS the match and returns the raw
        # label unchanged -> that raw pt-BR label has no Lamas token of its own -> unmapped.
        "type_hint_rejects_cross_type_match",
        [
            {"type": "guard", "label": "Chave de Braço", "actor": "partner", "successful": None},
        ],
    ),
    (
        # Scrambled case still resolves through `_normalize_name` to "Closed Guard" — correctly
        # STILL unmapped afterwards (guard postures are deliberately outside the Lamas token
        # list, `lamas_chain` rule 2), so this is a canonicalisation check, not a mapping one.
        "mixed_case_closed_guard_still_unmapped",
        [
            {"type": "guard", "label": "GuArDa FeChAda", "actor": "you", "successful": None},
        ],
    ),
    (
        # Mixed-case pt-BR canonicalises to "Butterfly Sweep"; `type=sweep` then maps regardless
        # of label (rule 1), landed -> SWP.
        "mixed_case_butterfly_sweep_mapped",
        [
            {"type": "sweep", "label": "RASPAGEM de GANCHO", "actor": "partner", "successful": True},
        ],
    ),
    (
        "no_mapped_entries",
        [
            {"type": "guard", "label": "Quatro Apoios", "actor": "you", "successful": None},
            {"type": "control", "label": "Guarda Fechada", "actor": "partner", "successful": None},
        ],
    ),
    ("empty_round", []),
]

#: Snapshot of the five ORIGINAL cases (pre-2026-09-12), rounded to the fixture's own precision.
#: This is the "existing cases keep their exact values" guard the task calls for: the six new
#: cases above must never perturb these through some shared-state accident (e.g. `lru_cache` on
#: the library index). Compared field-by-field in `build_fixture()`, `--check` covers the file
#: as a whole but this fails fast, and by name, if any of these five ever move.
_ORIGINAL_GOLDEN: dict[str, dict[str, Any]] = {
    "own_dominant_takedown_and_control": {
        "expected_codes": ["TKD", "BTK", "SUB"], "n_mapped": 3,
        "z": 0.239371179, "raw_p": 0.5595586804, "calibrated_p": 0.9739944979,
        "elo_offset": -629.396504,
    },
    "partner_dominant_sweep_and_pass": {
        "expected_codes": ["SWP", "GPS"], "n_mapped": 2,
        "z": -0.1911810637, "raw_p": 0.4523497811, "calibrated_p": 0.0524660289,
        "elo_offset": 502.686638,
    },
    "mixed_sides_one_mapped_one_not": {
        "expected_codes": [None, None, "SUBA"], "n_mapped": 1,
        "z": 0.2338600301, "raw_p": 0.5582, "calibrated_p": 0.9717959793,
        "elo_offset": -614.905629,
    },
    "no_mapped_entries": {
        "expected_codes": [None, None], "n_mapped": 0,
        "z": None, "raw_p": None, "calibrated_p": None, "elo_offset": None,
    },
    "empty_round": {
        "expected_codes": [], "n_mapped": 0,
        "z": None, "raw_p": None, "calibrated_p": None, "elo_offset": None,
    },
}


def build_fixture() -> dict[str, Any]:
    calib = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    vals: dict[str, float] = calib["definition"]["value_table"]
    params = calib["calibration"]["parameters"]

    cases: list[dict[str, Any]] = []
    for name, entries in CASES:
        steps = [(canonical_code(e, library=True), e.get("actor") != "partner") for e in entries]
        z, n = granular_score(steps, vals, granularity="actions_states", gamma=1.0)
        row: dict[str, Any] = {
            "name": name,
            "entries": entries,
            "expected_codes": [c for c, _ in steps],
            "n_mapped": n,
        }
        if n:
            raw_p = p_own(z)
            cal_p = calibration_apply([z], params)[0]
            row["z"] = round(z, 10)
            row["raw_p"] = round(raw_p, 10)
            row["calibrated_p"] = round(cal_p, 10)
            row["elo_offset"] = round(elo_offset(cal_p), 6)
            row["contributions"] = [
                {"code": c, "own": o, "c_k": round(c_k, 10)}
                for c, o, c_k in contribution_shares(steps, vals)
            ]
        else:
            row["z"] = None
            row["raw_p"] = None
            row["calibrated_p"] = None
            row["elo_offset"] = None
            row["contributions"] = []
        cases.append(row)

    for row in cases:
        want = _ORIGINAL_GOLDEN.get(row["name"])
        if want is None:
            continue
        for key, expected in want.items():
            got = row[key]
            assert got == expected, (
                f"original case {row['name']!r} field {key!r} moved: {got!r} != {expected!r}"
            )

    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_rrb_dominance_fixtures.py",
        "calibration_source": "data/rating/rrb_dominance_calibration.json",
        "calibration_method": calib["calibration"]["method"],
        "contract": (
            "actions_states Z (gamma=1, terminal=marginal) -> raw_p = sigmoid(Z) -> "
            "calibrated_p via the artefact's method -> elo_offset = 400*log10(p/(1-p)) with the "
            "sign flipped (partner offset); contributions c_k sum |c_k| = 1 over mapped steps."
        ),
        "cases": cases,
    }


def render(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="do not write; fail if disk diverges")
    args = ap.parse_args()

    if not CALIBRATION_PATH.exists():
        print(f"MISSING {CALIBRATION_PATH} — run scripts.build_rrb_dominance_calibration first")
        return 1

    text = render(build_fixture())
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"DIVERGENTE: {OUT}")
            return 1
        print("fixture em dia")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"escrito: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
