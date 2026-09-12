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
    (
        "no_mapped_entries",
        [
            {"type": "guard", "label": "Quatro Apoios", "actor": "you", "successful": None},
            {"type": "control", "label": "Guarda Fechada", "actor": "partner", "successful": None},
        ],
    ),
    ("empty_round", []),
]


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
