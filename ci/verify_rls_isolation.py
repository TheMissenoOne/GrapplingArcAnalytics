"""Two users, one anonymous, and every private table between them.

RLS is the only thing separating one athlete's training data from another's. A Supabase project
grants ALL on every new `public` table to `anon` and `authenticated` by default, so the policies
are not one layer of several — they are the layer. Nothing else is standing there.

Until now none of that was tested. The pytest suite runs on SQLite, which has no RLS at all, and
the CI scaffold's `auth.uid()` returned NULL unconditionally, so policies were only ever checked
for syntax. A policy that denies everyone passes a syntax check exactly as well as one that
denies the right people, and the second is the only interesting kind.

This becomes a specific user the way PostgREST does — `set local role` plus
`set local request.jwt.claims` — and asserts the properties that have to hold:

  - the owner can read their own rows (without this the rest is vacuous: a table nobody can read
    passes every isolation check ever written);
  - the other authenticated user reads nothing, updates nothing, deletes nothing, and cannot
    insert a row belonging to someone else;
  - the anonymous caller reads nothing private;
  - published athlete data IS readable, and unpublished athlete data is not;
  - a user's own node labels do not surface through the published athlete views.

Run against a real Postgres with the whole migration chain applied — see the migrations-smoke job.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import psycopg

A = str(uuid.uuid4())
B = str(uuid.uuid4())
# A third identity that owns data but is NOT Pro, so the entitlement half of the
# `user_performance_snapshots` policy is proved as well as the ownership half.
C_FREE = str(uuid.uuid4())
A_GRAPH = str(uuid.uuid4())
PUB_ATHLETE = str(uuid.uuid4())
PUB_GRAPH = str(uuid.uuid4())
PRIV_ATHLETE = str(uuid.uuid4())
PRIV_GRAPH = str(uuid.uuid4())

# Every owner-scoped table the App writes, with the column that names the owner. `graph_nodes`
# and `graph_edges` are owned transitively through `graphs`, so they are checked separately.
OWNED_TABLES: list[tuple[str, str]] = [
    ("user_sessions", "owner_id"),
    ("user_projects", "owner_id"),
    ("user_node_names", "owner_id"),
    ("user_sync_meta", "owner_id"),
    ("user_performance_snapshots", "owner_id"),
]

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


@contextmanager
def acting_as(
    conn: psycopg.Connection, role: str, uid: str | None = None
) -> Iterator[psycopg.Cursor]:
    """Become `anon` or `authenticated`, exactly as PostgREST does for a request."""
    with conn.transaction():
        cur = conn.cursor()
        # Claims first: setting them after the role switch is not permitted for a non-superuser.
        if uid is not None:
            claims = f'{{"sub":"{uid}"}}'
            cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        else:
            cur.execute("select set_config('request.jwt.claims', '', true)")
        cur.execute(f"set local role {role}")
        # No explicit reset: `set local` is transaction-scoped, so the role reverts on commit
        # OR rollback. Resetting in a `finally` would run inside an already-aborted transaction
        # whenever a policy refuses a write — and a refused write is exactly what several of
        # these checks are trying to observe.
        yield cur


def seed(cur: psycopg.Cursor) -> None:
    cur.execute("alter table auth.users add column if not exists raw_user_meta_data jsonb")
    # A and B are BOTH Pro on purpose: if only the owner were Pro, "B sees nothing" would be
    # explained by entitlement rather than by ownership, and the isolation claim would be
    # untested. C is deliberately not.
    for uid, is_pro in ((A, True), (B, True), (C_FREE, False)):
        cur.execute("insert into auth.users (id) values (%s)", (uid,))
        cur.execute("insert into profiles (id) values (%s) on conflict do nothing", (uid,))
        cur.execute("update profiles set is_pro = %s where id = %s", (is_pro, uid))

    cur.execute(
        "insert into graphs (id, owner_kind, owner_id, schema_version, user_elo)"
        " values (%s,'user',%s,3,1200)",
        (A_GRAPH, A),
    )
    # A private label, the kind that used to end up in the world-readable library.
    for key, label in (("guarda fechada", "Guarda Fechada"), ("meu chokezinho", "Meu Chokezinho")):
        cur.execute(
            "insert into graph_nodes (graph_id, node_key, label, type) values (%s,%s,%s,'guard')",
            (A_GRAPH, key, label),
        )
    cur.execute(
        "insert into graph_edges"
        " (id, graph_id, edge_key, source_key, target_key, owner_kind, elo, setup)"
        " values (%s, %s, 'guarda fechada→meu chokezinho',"
        " 'guarda fechada', 'meu chokezinho', 'user', 42, '')",
        (str(uuid.uuid4()), A_GRAPH),
    )

    cur.execute(
        "insert into user_sessions (id, owner_id, data, updated_at)"
        " values (%s,%s,'{\"reflection\":\"my knee hurts\"}'::jsonb, now())",
        (f"s-{uuid.uuid4()}", A),
    )
    cur.execute(
        "insert into user_projects (id, owner_id, data, updated_at)"
        " values (%s, %s, '{}'::jsonb, now())",
        (f"p-{uuid.uuid4()}", A),
    )
    cur.execute(
        "insert into user_node_names (owner_id, node_key, preferred_name)"
        " values (%s, 'closed guard', 'Fechada')",
        (A,),
    )
    cur.execute(
        "insert into user_sync_meta (owner_id, last_sync_at, session_count) values (%s, now(), 1)",
        (A,),
    )
    for uid in (A, C_FREE):
        cur.execute(
            "insert into user_performance_snapshots"
            " (id, owner_id, cadence, period_start, period_end, schema_version, status, metrics)"
            " values (%s, %s, 'weekly', current_date - 7, current_date, 1, 'ready', '{}'::jsonb)",
            (str(uuid.uuid4()), uid),
        )

    # Public side: one published athlete graph and one unpublished, so "readable" is proved to
    # mean published rather than simply "athlete".
    for athlete_id, graph_id, published, name in (
        (PUB_ATHLETE, PUB_GRAPH, True, "Published Athlete"),
        (PRIV_ATHLETE, PRIV_GRAPH, False, "Draft Athlete"),
    ):
        cur.execute(
            "insert into athletes (id, name, is_published) values (%s,%s,%s)",
            (athlete_id, name, published),
        )
        cur.execute(
            "insert into graphs (id, owner_kind, owner_id, schema_version)"
            " values (%s, 'athlete', %s, 3)",
            (graph_id, athlete_id),
        )
        cur.execute(
            "insert into graph_nodes (graph_id, node_key, label, type)"
            " values (%s, 'armbar', 'Armbar', 'submission')",
            (graph_id,),
        )
        cur.execute(
            "insert into graph_edges"
            " (id, graph_id, edge_key, source_key, target_key, owner_kind, elo, setup)"
            " values (%s,%s,'armbar→armbar','armbar','armbar','athlete',10,'')",
            (str(uuid.uuid4()), graph_id),
        )


def scalar(cur: psycopg.Cursor, sql: str, params: tuple[object, ...] = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


# Denied by a missing GRANT, which is stricter than an empty result: the role cannot reach the
# table at all. Some revisions (0030, 0031) revoke from `anon` outright; most rely on RLS. Both
# are correct answers to "can this role see it", so the probe distinguishes them and accepts
# either — while still reporting which one applied, because a table silently losing its grant
# would break the app rather than protect it.
DENIED_BY_GRANT = -1


def rows_visible_to(
    conn: psycopg.Connection, role: str, uid: str | None, sql: str, params: tuple[object, ...] = ()
) -> int:
    """Count rows this role can actually see, or DENIED_BY_GRANT if it cannot query at all.

    One transaction per probe: a permission error aborts the transaction, so sharing one would
    make every check after the first failure meaningless.
    """
    try:
        with acting_as(conn, role, uid) as cur:
            return scalar(cur, sql, params)
    except psycopg.errors.InsufficientPrivilege:
        return DENIED_BY_GRANT


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        seed(conn.cursor())

        # ── The owner sees their own data ────────────────────────────────────────
        # Load-bearing: without it every assertion below would pass on a schema where the
        # policies deny everyone, which is a broken product, not a secure one.
        with acting_as(conn, "authenticated", A) as cur:
            check(scalar(cur, "select count(*) from graphs where id = %s", (A_GRAPH,)) == 1,
                  "the owner cannot read their own graph")
            check(
                scalar(cur, "select count(*) from graph_nodes where graph_id = %s",
                       (A_GRAPH,)) == 2,
                  "the owner cannot read their own graph nodes")
            check(
                scalar(cur, "select count(*) from graph_edges where graph_id = %s",
                       (A_GRAPH,)) == 1,
                  "the owner cannot read their own graph edges")
            for table, col in OWNED_TABLES:
                check(scalar(cur, f"select count(*) from {table} where {col} = %s", (A,)) == 1,
                      f"the owner cannot read their own {table}")

        # ── The other authenticated user sees and touches nothing ────────────────
        with acting_as(conn, "authenticated", B) as cur:
            check(scalar(cur, "select count(*) from graphs where id = %s", (A_GRAPH,)) == 0,
                  "another user can READ someone else's graph")
            check(
                scalar(cur, "select count(*) from graph_nodes where graph_id = %s",
                       (A_GRAPH,)) == 0,
                  "another user can READ someone else's private node labels")
            check(
                scalar(cur, "select count(*) from graph_edges where graph_id = %s",
                       (A_GRAPH,)) == 0,
                  "another user can READ someone else's graph edges")

            for table, col in OWNED_TABLES:
                check(scalar(cur, f"select count(*) from {table} where {col} = %s", (A,)) == 0,
                      f"another user can READ someone else's {table}")

            cur.execute("update graph_nodes set label = 'hijacked' where graph_id = %s", (A_GRAPH,))
            check(cur.rowcount == 0, "another user can UPDATE someone else's private node labels")

            cur.execute("delete from user_sessions where owner_id = %s", (A,))
            check(cur.rowcount == 0, "another user can DELETE someone else's sessions")

            cur.execute("delete from graph_nodes where graph_id = %s", (A_GRAPH,))
            check(cur.rowcount == 0, "another user can DELETE someone else's graph nodes")

        # Writing a row that claims someone else as its owner has to be refused outright, not
        # silently written and then hidden.
        for table, col, extra in (
            (
                "user_sessions",
                "owner_id",
                "(id, owner_id, data, updated_at) values ('s-x', %s, '{}'::jsonb, now())",
            ),
            ("user_node_names", "owner_id", "(owner_id, node_key) values (%s, 'x')"),
        ):
            try:
                with acting_as(conn, "authenticated", B) as cur:
                    cur.execute(f"insert into {table} {extra}", (A,))
                failures.append(f"another user can INSERT a row owned by someone else into {table}")
            except psycopg.errors.InsufficientPrivilege:
                pass

        # ── The Pro gate on snapshots is entitlement AND ownership, not either ──
        # Getting this backwards in either direction is a real product failure: a free account
        # reading Pro output, or a paying one locked out of its own.
        check(rows_visible_to(
            conn, "authenticated", C_FREE,
            "select count(*) from user_performance_snapshots where owner_id = %s", (C_FREE,),
        ) in (0, DENIED_BY_GRANT), "a non-Pro account can read its own Pro performance snapshots")

        check(rows_visible_to(
            conn, "authenticated", B,
            "select count(*) from user_performance_snapshots where owner_id = %s", (A,),
        ) in (0, DENIED_BY_GRANT), "a Pro account can read ANOTHER Pro account's snapshots")

        # ── Anonymous ────────────────────────────────────────────────────────────
        private_probes: Sequence[tuple[str, str, tuple[object, ...]]] = [
            ("user graphs", "select count(*) from graphs where owner_kind = 'user'", ()),
            ("private node labels",
             "select count(*) from graph_nodes where graph_id = %s", (A_GRAPH,)),
            ("private graph edges",
             "select count(*) from graph_edges where graph_id = %s", (A_GRAPH,)),
        ] + [(t, f"select count(*) from {t}", ()) for t, _ in OWNED_TABLES]

        for what, sql, params in private_probes:
            seen = rows_visible_to(conn, "anon", None, sql, params)
            check(seen in (0, DENIED_BY_GRANT), f"anon can read {what}")

        # Public data is supposed to be public — the point is that it is PUBLISHED data.
        public_probes: Sequence[tuple[str, str, tuple[object, ...], int]] = [
            ("a PUBLISHED athlete graph, which defeats the public site",
             "select count(*) from published_athlete_graphs where id = %s", (PUB_GRAPH,), 1),
            ("published athlete graph nodes",
             "select count(*) from published_athlete_graph_nodes where graph_id = %s",
             (PUB_GRAPH,), 1),
        ]
        for what, sql, params, expected in public_probes:
            check(rows_visible_to(conn, "anon", None, sql, params) == expected,
                  f"anon cannot read {what}")

        for what, sql, params in (
            ("an UNPUBLISHED athlete graph",
             "select count(*) from published_athlete_graphs where id = %s", (PRIV_GRAPH,)),
            ("an UNPUBLISHED athlete's graph nodes",
             "select count(*) from published_athlete_graph_nodes where graph_id = %s",
             (PRIV_GRAPH,)),
        ):
            check(rows_visible_to(conn, "anon", None, sql, params) in (0, DENIED_BY_GRANT),
                  f"anon can read {what}")

        # The whole reason `graph_nodes` exists: a user's own words must not reach a public view,
        # for anyone — anonymous or another signed-in account.
        leak_sql = (
            "select count(*) from published_athlete_graph_nodes where label = 'Meu Chokezinho'"
        )
        for role, uid, who in (("anon", None, "anon"), ("authenticated", B, "another user")):
            check(rows_visible_to(conn, role, uid, leak_sql) in (0, DENIED_BY_GRANT),
                  f"{who} can see a private node label through the published athlete view")

        # ── No table holds a privilege RLS cannot gate ───────────────────────────
        # Supabase grants ALL on every new table to anon/authenticated by default. TRUNCATE,
        # REFERENCES and TRIGGER are not row-level concepts, so no policy can restrain them —
        # 0039 revokes all three schema-wide and fixes the default. This catches the next table
        # that arrives through some path the default does not cover.
        cur = conn.cursor()
        cur.execute(
            """
            select c.relname, g.grantee, g.privilege_type
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
              join information_schema.role_table_grants g
                on g.table_name = c.relname and g.table_schema = 'public'
             where c.relkind = 'r'
               and g.grantee in ('anon', 'authenticated')
               and g.privilege_type in ('TRUNCATE', 'REFERENCES', 'TRIGGER')
             order by 1, 2, 3
            """
        )
        for table, grantee, privilege in cur.fetchall():
            failures.append(f"{grantee} holds {privilege} on {table}, which no RLS policy can gate")

    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        noun = "property" if len(failures) == 1 else "properties"
        raise SystemExit(f"RLS isolation: {len(failures)} {noun} violated")

    print("RLS isolation: OK — owner reads own, nobody else reads any, public stays public")


if __name__ == "__main__":
    main()
