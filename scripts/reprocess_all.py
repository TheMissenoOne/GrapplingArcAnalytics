#!/usr/bin/env python
"""Mass-reprocess the full refined match corpus, then re-export the public site assets.

Runs every event dump through the shared importer (``scripts.dump_import.run_dump``) — each
call de-dupes by ``frozenset(participants)+year``, idempotently replaces bouts, and double-pass
replays both athletes — then exports all final bouts once via ``export.match_breakdown``. The
enriched breakdowns carry the additive ``decision_space`` block (RF14 / DS-12).

Per-event tags group bouts into card pages (``export.site_data``); the four ADCC 2022 division
files share one ``ADCC 2022`` tag. Khabib stays ``event=None`` (MMA, no card page).

    uv run python -m scripts.reprocess_all              # import all + re-export
    uv run python -m scripts.reprocess_all --dry-run    # parse + report, no DB writes, no export
    uv run python -m scripts.reprocess_all --no-export  # import only
    uv run python -m scripts.reprocess_all --only WNO22 ADCC2022-88kg   # subset by label
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# (data module under scripts/, event tag, human label). Order = chronological-ish; replay
# folds full history regardless, so order only affects intermediate idempotent rewrites.
DATASETS: list[tuple[str, str | None, str]] = [
    ("scripts.khabib_data", None, "Khabib"),
    ("scripts.adcc2022_88kg_data", "ADCC 2022", "ADCC2022-88kg"),
    ("scripts.adcc2022_99kg_data", "ADCC 2022", "ADCC2022+99kg"),
    ("scripts.adcc2022_abs_data", "ADCC 2022", "ADCC2022-ABS"),
    ("scripts.adcc2022_finals_data", "ADCC 2022", "ADCC2022-Finals"),
    ("scripts.adcc2024_abs_data", "ADCC 2024", "ADCC2024-ABS"),
    ("scripts.ibjjf2023_worlds_data", "IBJJF Worlds 2023", "IBJJF2023-Worlds"),
    ("scripts.spyder_kok_data", "Spyder: King of Kings", "SpyderKingOfKings"),
    ("scripts.polaris37_data", "Polaris 37", "Polaris37"),
    ("scripts.wno20_data", "WNO 20", "WNO20"),
    ("scripts.wno22_data", "WNO 22", "WNO22"),
    ("scripts.wno24_data", "WNO 24", "WNO24"),
    ("scripts.wno31_data", "WNO 31", "WNO31"),
    ("scripts.cji_data", "CJI", "CJI"),
    ("scripts.adcc2022_women_data", "ADCC 2022", "ADCC2022-Women"),
    ("scripts.adcc2024_p99kg_data", "ADCC 2024", "ADCC2024+99kg"),
    ("scripts.adcc_trials2022_sa_data", "ADCC Trials 2022 South America", "ADCCTrials2022SA"),
    ("scripts.adcc_trials2023_ec_finals_data", "ADCC Trials 2023 East Coast",
     "ADCCTrials2023EC-Finals"),
    ("scripts.adcc_trials2023_ec_semis_data", "ADCC Trials 2023 East Coast",
     "ADCCTrials2023EC-Semis"),
    ("scripts.adcc_trials2024_wc_data", "ADCC Trials 2024 West Coast", "ADCCTrials2024WC"),
    ("scripts.ibjjf2025_top10_data", "IBJJF 2025 Top 10", "IBJJF2025top10"),
    ("scripts.ufc325_data", "UFC 325", "UFC325"),
    ("scripts.ncaa2024_data", "NCAA 2024", "2024NCAA"),
    ("scripts.ncaa2025_data", "NCAA 2025", "2025NCAA"),
    ("scripts.ncaa2026_data", "NCAA 2026", "2026NCAA"),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Reprocess the full match corpus + re-export site")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    ap.add_argument("--no-export", action="store_true", help="import only, skip site re-export")
    ap.add_argument("--only", nargs="+", metavar="LABEL", help="restrict to these dataset labels")
    args = ap.parse_args()

    from scripts.dump_import import run_dump

    selected = DATASETS
    if args.only:
        wanted = {s.lower() for s in args.only}
        selected = [d for d in DATASETS if d[2].lower() in wanted]
        missing = wanted - {d[2].lower() for d in selected}
        if missing:
            logger.error("Unknown dataset label(s): %s", ", ".join(sorted(missing)))
            return 2

    for module_path, event, label in selected:
        raw = importlib.import_module(module_path).RAW
        logger.info("── %s (event=%r) ──", label, event)
        run_dump(raw, event=event, label=label, dry_run=args.dry_run)

    if args.dry_run or args.no_export:
        return 0

    from export.match_breakdown import _DEFAULT_OUT
    from export.match_breakdown import run as export_run

    logger.info("── Re-exporting site assets → %s ──", _DEFAULT_OUT)
    return export_run(_DEFAULT_OUT, None)


if __name__ == "__main__":
    raise SystemExit(main())
