#!/usr/bin/env python
"""Insert the WNO 31 grappling card (``scripts/wno31_data.py``) as global matches.

Thin wrapper over ``scripts.dump_import`` (shared bjj-match-analyzer importer): tags each
bout ``event="WNO 31"`` and canonicalises labels to the technique library. Idempotent.

    uv run python -m scripts.insert_wno31            # write to DB
    uv run python -m scripts.insert_wno31 --dry-run  # parse + report only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from scripts.dump_import import run_dump  # noqa: E402
from scripts.dumps.wno31_data import RAW  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert the WNO 31 card as global matches")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run_dump(RAW, event="WNO 31", label="WNO 31", dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
