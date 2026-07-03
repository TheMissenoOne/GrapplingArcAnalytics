#!/usr/bin/env python
"""Insert the UFC.py card dump (``scripts/ufc_card_data.py``) as global matches.

Thin wrapper over ``scripts.dump_import``. ``event`` left null (not named in the dump);
labels are canonicalised to the technique library. Idempotent.

    uv run python -m scripts.insert_ufc_card            # write to DB
    uv run python -m scripts.insert_ufc_card --dry-run  # parse + report only
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
from scripts.dumps.ufc_card_data import RAW  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert the UFC.py card as global matches")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run_dump(RAW, event=None, label="UFC card", dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
