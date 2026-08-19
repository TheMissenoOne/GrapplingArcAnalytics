"""Prove, against a real Postgres, that a private graph label cannot reach the public library.

The rest of this repo's tests run on SQLite in-memory, which round-trips the model shape and
cannot execute a policy, a view or a ``plpgsql`` function. The one invariant that matters most
here lives entirely in those three: ``replace_user_graph`` must write a user's own words into
``graph_nodes`` and never into ``technique_nodes``. So it is checked where it is real — the
migrations-smoke job, which already has a Postgres with the whole chain applied.

Becoming a user is done the way PostgREST does it — ``set local request.jwt.claims`` plus
``set local role`` — because the scaffold's ``auth.uid()`` is Supabase's real definition and
reads that claim. An earlier version of this script redefined ``auth.uid()`` itself and restored
a NULL stub afterwards, which silently disabled every policy for whatever ran next against the
same database. The claim is transaction-scoped and leaves nothing behind.
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

        # One transaction, as the owner: the claim and the role both revert on commit.
        with conn.transaction():
            claims = f'{{"sub":"{OWNER}"}}'
            cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
            cur.execute("set local role authenticated")
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

        # Authority comes from the session, so a caller without one gets nothing. Separate
        # transaction: the refusal aborts whichever one it happens in.
        try:
            with conn.transaction():
                cur = conn.cursor()
                cur.execute("select set_config('request.jwt.claims', '', true)")
                cur.execute("set local role authenticated")
                cur.execute("select public.replace_user_graph(1.0, '[]'::jsonb, '[]'::jsonb)")
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:  # pragma: no cover - only reached when the guard regresses
            raise AssertionError("an unauthenticated caller was allowed to replace a graph")

        # The release gate, as a query rather than a promise: no row in the world-readable
        # library may be named ONLY by a user graph. 0040 cleaned the 48 that were; this is
        # what catches the forty-ninth, whichever writer produces it.
        cur.execute(
            """
            select tn.node_key, tn.label
              from technique_nodes tn
             where exists (
                     select 1 from graph_nodes gn join graphs g on g.id = gn.graph_id
                      where gn.node_key = tn.node_key and g.owner_kind = 'user'
                   )
               and not exists (
                     select 1 from graph_nodes gn join graphs g on g.id = gn.graph_id
                      where gn.node_key = tn.node_key and g.owner_kind = 'athlete'
                   )
               -- Curated vocabulary that no athlete graph happens to reference yet is not
               -- a private label. "Nobody published it" is not "a user invented it".
               and tn.source <> 'library'
            """
        )
        private_in_library = cur.fetchall()
        assert not private_in_library, (
            "labels named only by a user graph are sitting in the public library: "
            f"{[label for _key, label in private_in_library]}"
        )

        # The mirror of the check above, and the one that was missing.
        #
        # "No private label is in the public library" is one-directional: deleting too MUCH
        # does not violate it. 0040 removed 18 keys of ordinary public vocabulary — Back
        # Control, Mount, Single Leg Takedown — because they happened to be named by a user
        # graph as well as by athlete graphs, and every existing assertion still passed.
        #
        # An athlete graph node is by definition public vocabulary, so it must resolve to a
        # library row. A one-sided invariant is how a cleanup becomes a deletion.
        cur.execute(
            """
            select distinct gn.node_key, gn.label
              from graph_nodes gn
              join graphs g on g.id = gn.graph_id
             where g.owner_kind = 'athlete'
               and gn.node_key <> ''
               and not exists (
                     select 1 from technique_nodes tn where tn.node_key = gn.node_key
                   )
            """
        )
        missing = cur.fetchall()
        assert not missing, (
            "athlete graph nodes with no row in the public library: "
            f"{[label for _key, label in missing]}"
        )

    print("private graph nodes: OK — library holds the public vocabulary and none of the private")


if __name__ == "__main__":
    main()
