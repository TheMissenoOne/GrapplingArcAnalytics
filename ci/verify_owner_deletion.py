"""Prove, against a real Postgres, that deleting an identity leaves nothing owner-scoped behind.

Three `public` tables carry a uuid owner column with no foreign key, and they are not equivalent:

  - ``graphs.owner_id`` is polymorphic, so a column FK is impossible; 0023's
    ``handle_user_delete`` trigger covers it.
  - ``matches.created_by`` is curation provenance, not user data.
  - ``bundle_imports.owner_id`` was genuinely unhandled until 0038.

Two of those three are mechanisms the SQLite test suite cannot execute — a trigger and a
cascade — so "the account is really gone" is asserted here, where the database is real. The
Edge Function that performs deletion is tested separately and for a different thing: its
ORDERING. This is about what the schema does on its own, which is the part that has to stay
true when someone deletes a user from the dashboard instead.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg


def main() -> None:
    url = os.environ["DATABASE_URL"]
    owner = str(uuid.uuid4())
    graph_id = str(uuid.uuid4())

    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.cursor()
        # `handle_new_user` reads a column the CI scaffold does not create.
        cur.execute("alter table auth.users add column if not exists raw_user_meta_data jsonb")
        cur.execute("insert into auth.users (id) values (%s)", (owner,))
        cur.execute("insert into profiles (id) values (%s) on conflict do nothing", (owner,))
        cur.execute(
            "insert into graphs (id, owner_kind, owner_id, schema_version) values (%s,'user',%s,3)",
            (graph_id, owner),
        )
        cur.execute(
            "insert into bundle_imports (id, owner_id, raw) values (%s, %s, %s::jsonb)",
            (str(uuid.uuid4()), owner, json.dumps({"sessions": ["private"]})),
        )

        # Deleting the identity is the ONLY thing done here — no application code, no function.
        cur.execute("delete from auth.users where id = %s", (owner,))

        checks = {
            "profiles": ("select count(*) from profiles where id = %s", (owner,)),
            "graphs": ("select count(*) from graphs where id = %s", (graph_id,)),
            "bundle_imports": ("select count(*) from bundle_imports where owner_id = %s", (owner,)),
        }
        for table, (sql, params) in checks.items():
            cur.execute(sql, params)
            row = cur.fetchone()
            assert row is not None and row[0] == 0, (
                f"{table} still holds rows for a deleted identity"
            )

    print("owner deletion: OK — profile, graph and bundle all went with the identity")


if __name__ == "__main__":
    main()
