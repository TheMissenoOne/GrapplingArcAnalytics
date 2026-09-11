"""Guard against the 0040 scar: a `delete` on shared/graph tables with no predicate.

0040 (`docs/...technique_provenance_cleanup`) shipped a delete on `technique_nodes`
that an earlier draft narrowed by only half the required predicate (see that file's
own comment on `_DELETE_PRIVATE`) — a `where` that looked scoped but was not athlete-
safe. This scans every migration for `delete from`/`op.execute(...delete...)` touching
the vocabulary/graph tables and asserts each one carries a `where` clause. It cannot
verify the predicate is CORRECT (that needs the domain read, done by hand for 0040 and
0037), only that nobody ships an unconditional wipe again.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

GUARDED_TABLES = {"technique_nodes", "graph_nodes", "graph_edges", "graphs"}

# revision -> reason. 0040 is the historical exception this test is named after: its
# `_DELETE_PRIVATE` delete on technique_nodes DOES carry a `where` (verified by eye,
# both halves of the predicate present) but is allow-listed explicitly rather than
# trusted to the regex below, since a narrowed-but-still-present `where` is exactly
# the failure mode that shipped once already.
ALLOWED = {"0040"}

# `delete from <table> ... where` (DOTALL: the where may be lines later) or
# `delete from <table> ...;` with no where before the terminating `;`.
_DELETE_STMT = re.compile(
    r"delete\s+from\s+(?:public\.)?(?P<table>\w+)\b(?P<body>.*?);", re.IGNORECASE | re.DOTALL
)


def _offenders() -> list[str]:
    offenders = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        revision = path.stem.split("_", 1)[0]
        text = path.read_text()
        for m in _DELETE_STMT.finditer(text):
            table = m.group("table")
            if table not in GUARDED_TABLES:
                continue
            if revision in ALLOWED:
                continue
            if not re.search(r"\bwhere\b", m.group("body"), re.IGNORECASE):
                offenders.append(f"{path.name}: unguarded delete on {table}")
    return offenders


def test_every_guarded_table_delete_has_a_where_clause() -> None:
    offenders = _offenders()
    assert offenders == [], "unguarded deletes found:\n" + "\n".join(offenders)


def test_0040_is_the_only_allow_listed_exception() -> None:
    assert ALLOWED == {"0040"}
