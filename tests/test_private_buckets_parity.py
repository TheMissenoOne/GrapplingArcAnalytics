"""The deletion path must know about every private bucket that exists.

A bucket added to the schema and not to `delete-account` is not a cosmetic drift: the function
enumerates objects under the user's prefix and then reports `deleted: true`. Files in a bucket
it never visited stay on the server under a response that says the account is gone — the exact
outcome that function was written to prevent.

Nothing else links the two files, so this is the link. It is a text check on purpose: the
alternative is a live project, and the thing being checked is whether two lists agree.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "alembic" / "versions"
EDGE_FUNCTION = ROOT / "supabase" / "functions" / "delete-account" / "index.ts"

_BUCKET_INSERT = re.compile(
    r"insert\s+into\s+storage\.buckets.*?values\s*\(\s*'([^']+)'", re.IGNORECASE | re.DOTALL
)
_BUCKET_LIST = re.compile(r"PRIVATE_BUCKETS\s*=\s*\[(.*?)\]", re.DOTALL)


def _buckets_in_migrations() -> set[str]:
    found: set[str] = set()
    for path in MIGRATIONS.glob("[0-9]*.py"):
        found.update(_BUCKET_INSERT.findall(path.read_text()))
    return found


def _buckets_in_deletion() -> set[str]:
    match = _BUCKET_LIST.search(EDGE_FUNCTION.read_text())
    assert match, "delete-account no longer declares PRIVATE_BUCKETS"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_deletion_sweeps_every_bucket_the_migrations_create() -> None:
    migrations = _buckets_in_migrations()
    assert migrations, "no storage bucket found in the migrations — the regex stopped matching"
    missed = migrations - _buckets_in_deletion()
    assert not missed, (
        f"buckets created by a migration but never swept on account deletion: {sorted(missed)}. "
        "Add them to PRIVATE_BUCKETS in supabase/functions/delete-account/index.ts."
    )


def test_deletion_does_not_sweep_a_bucket_that_does_not_exist() -> None:
    # The other direction is cheaper to get wrong quietly: a stale name means one wasted list
    # call per deletion, forever, and it hides the fact that nobody re-read this list.
    phantom = _buckets_in_deletion() - _buckets_in_migrations()
    assert not phantom, f"PRIVATE_BUCKETS names buckets no migration creates: {sorted(phantom)}"
