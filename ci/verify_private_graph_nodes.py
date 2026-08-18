"""Prove, against a real Postgres, that a private graph label cannot reach the public library.

The rest of this repo's tests run on SQLite in-memory, which round-trips the model shape and
cannot execute a policy, a view or a ``plpgsql`` function. The one invariant that matters most
here lives entirely in those three: ``replace_user_graph`` must write a user's own words into
``graph_nodes`` and never into ``technique_nodes``. So it is checked where it is real — the
migrations-smoke job, which already has a Postgres with the whole chain applied.

``auth.uid()`` is a NULL-returning stub in the CI scaffold (there is no request to derive a JWT
from), so this replaces it with a fixed uuid for the duration, then restores it. That is the
only way to exercise a ``security definer`` function whose entire authority model is
``auth.uid()``.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg

OWNER = "11111111-1111-1111-1111-111111111111"

PRIVATE_NODES = [
    {"node_key": "guarda fechada", "label": "Guarda Fechada", "type": "guard", "node_type": ""},
    {
        "node_key": "meu chokezinho",
        "label": "Meu Chokezinho",
        "type": "submission",
        "node_type": "",
    },
]
PRIVATE_EDGES = [
    {
        "edge_key": "guarda fechada→meu chokezinho",
        "source_key": "guarda fechada",
        "target_key": "meu chokezinho",
        "elo": 42.0,
        "setup": "grip",
    }
]


def main() -> None:
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.cursor()

        # `handle_new_user` reads a column the scaffold does not create; this trigger is not
        # what is under test.
        cur.execute("alter table auth.users add column if not exists raw_user_meta_data jsonb")
        cur.execute("insert into auth.users (id) values (%s) on conflict do nothing", (OWNER,))
        cur.execute("insert into profiles (id) values (%s) on conflict do nothing", (OWNER,))
        # One curated library row, so the "known technique" branch has something to link to.
        cur.execute(
            "insert into technique_nodes (id, node_key, label, type, node_type, source)"
            " values (%s, 'guarda fechada', 'Guarda Fechada', 'guard', '', 'library')"
            " on conflict (node_key) do nothing",
            (str(uuid.uuid4()),),
        )

        cur.execute(
            f"create or replace function auth.uid() returns uuid language sql stable"
            f" as $$ select '{OWNER}'::uuid $$;"
        )
        try:
            cur.execute(
                "select public.replace_user_graph(%s, %s::jsonb, %s::jsonb)",
                (1234.5, json.dumps(PRIVATE_NODES), json.dumps(PRIVATE_EDGES)),
            )
            row = cur.fetchone()
            assert row is not None
            graph_id = row[0]

            # THE invariant. A label the user invented has no curated equivalent and must stay
            # where only its owner can read it.
            cur.execute("select count(*) from technique_nodes where node_key = 'meu chokezinho'")
            leaked = cur.fetchone()
            assert leaked is not None and leaked[0] == 0, (
                "replace_user_graph wrote a private label into the public technique library"
            )

            cur.execute(
                "select node_key, canonical_node_key from graph_nodes"
                " where graph_id = %s order by node_key",
                (graph_id,),
            )
            nodes: dict[str, str | None] = dict(cur.fetchall())
            assert nodes == {
                "guarda fechada": "guarda fechada",  # known technique links outward
                "meu chokezinho": None,  # invented one links to nothing, and still syncs
            }, nodes

            # Pruning: the client-side writer never removed anything, so a deleted edge lived
            # in the cloud forever and returned on the next device that pulled.
            cur.execute(
                "select public.replace_user_graph(%s, %s::jsonb, '[]'::jsonb)",
                (1300.0, json.dumps(PRIVATE_NODES)),
            )
            cur.execute("select count(*) from graph_edges where graph_id = %s", (graph_id,))
            remaining = cur.fetchone()
            assert remaining is not None and remaining[0] == 0, "stale edges were not pruned"

            # Authority comes from the session, so a caller without one gets nothing.
            cur.execute(
                "create or replace function auth.uid() returns uuid language sql stable"
                " as $$ select null::uuid $$;"
            )
            try:
                cur.execute("select public.replace_user_graph(1.0, '[]'::jsonb, '[]'::jsonb)")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:  # pragma: no cover - only reached when the guard regresses
                raise AssertionError("an unauthenticated caller was allowed to replace a graph")
        finally:
            cur.execute(
                "create or replace function auth.uid() returns uuid language sql stable"
                " as $$ select null::uuid $$;"
            )

    print("private graph nodes: OK — no private label reached technique_nodes")


if __name__ == "__main__":
    main()
