#!/usr/bin/env python
"""Insert Khabib Nurmagomedov's UFC career (``scripts/khabib_data.py``) as global matches.

Thin wrapper over ``scripts.dump_import``. ``event`` is left null — each bout is a different
UFC card, not named in the dump. Labels are canonicalised to the technique library.

    uv run python -m scripts.insert_khabib            # write to DB
    uv run python -m scripts.insert_khabib --dry-run  # parse + report only
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
from scripts.dumps.khabib_data import RAW  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert Khabib's UFC career as global matches")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run_dump(RAW, event=None, label="Khabib", dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
