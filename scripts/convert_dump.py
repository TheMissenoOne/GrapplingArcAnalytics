#!/usr/bin/env python
"""Convert a root bjj-match-analyzer dump (``<Event>.py`` dict literal(s)) into a
``scripts/<slug>_data.py`` RAW module consumable by ``scripts.dump_import``.

    uv run python scripts/convert_dump.py ../ADCC2022Women.py adcc2022_women
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def main() -> int:
    src_path, slug = Path(sys.argv[1]), sys.argv[2]
    tree = ast.parse(src_path.read_text())
    blocks = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Dict)
    ]
    if not blocks:
        print(f"{src_path}: no dict literals found", file=sys.stderr)
        return 1
    out = Path(__file__).resolve().parent / f"{slug}_data.py"
    out.write_text(
        f'"""{src_path.stem} match dump (bjj-match-analyzer schema) — converted for import.\n\n'
        f'Generated from {src_path.name} by convert_dump.py; keyed by (athlete_a_name, year).\n'
        'Do not edit by hand."""\n'
        "# ruff: noqa: E501  (single-line serialized data literal)\n\n"
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        f"RAW: list[dict[tuple[str, int], dict[str, Any]]] = {blocks!r}\n"
    )
    print(f"{out.name}: {sum(len(b) for b in blocks)} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
