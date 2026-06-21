"""CLI: python -m db.ingest <bundle.json> [bundle2.json ...]

Parses each file as a UserBundle and upserts its graph into Postgres.
Requires DATABASE_URL env var (service role).
"""

from __future__ import annotations

import json
import sys

from db.base import db_session
from db.repository import upsert_graph_from_bundle
from schemas.app_types import UserBundle


def ingest_file(path: str) -> None:
    with open(path) as f:
        data = json.load(f)
    bundle = UserBundle.from_json(data)
    with db_session() as session:
        graph_id = upsert_graph_from_bundle(bundle, session)
    owner = bundle.user.id if bundle.user else "unknown"
    print(f"[ingest] {path} → graph {graph_id} (owner={owner})")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m db.ingest <bundle.json> [...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        try:
            ingest_file(path)
        except Exception as exc:
            print(f"[ingest] ERROR {path}: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
