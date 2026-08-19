"""Give public vocabulary back its provenance, and take the private labels out.

``technique_nodes`` is world-readable — ``using (true)`` for ``anon``. 0037 stopped the App
writing user labels into it and gave every graph its own private node identity, but it
deliberately touched no existing row: a deployed App still wrote the old way, and breaking
graph sync for people who had not updated would have been the worse privacy outcome. The App
has since shipped. This is the repair 0037 deferred.

**Measured on production, not estimated.** Of 328 rows carrying ``source='user'``:

  - **215** are used by ATHLETE graphs — ordinary competition vocabulary ("Jab", "Riding
    Time", "Stalling Warning") with the wrong provenance stamped on it. Not private, and
    deleting them would have gutted the public corpus. They become ``'library'``.
  - **65** are referenced by no graph at all. They are also public: the closest one was
    created **43.8 hours** from the nearest App sync, and they arrive in batch clusters —
    28 of them in a single minute, all MMA striking and scoring events. Transcript
    vocabulary that lost its edges when matches were re-imported, not app-fed data. They
    become ``'library'`` with the rest.
  - **48** are used only by USER graphs: "Guarda Fechada", "Chave de Braço", "Estrutura".
    One person's words for their own game, readable by anonymous. These are deleted.

Deleting them loses nothing. 0037's backfill already gave every one of those labels a row in
``graph_nodes`` under the graph that uses it — verified, all 48 are linked — so the athlete
keeps their own label. The FK from ``graph_nodes.canonical_node_key`` is ``on delete set
null``, so the private node simply stops pointing at a curated entry it never was.

Two ``source='library'`` rows are also used only by a user graph. They are NOT touched: they
are genuinely curated vocabulary that no athlete graph happens to reference yet, and "no
athlete uses it" is not "a user invented it".

**Both writers are closed, not just the client's.** Revoking
``technique_nodes_user_insert`` stops the App. It does nothing about the server side, which
runs as ``service_role`` — and the athlete ingestion path was the source of the 215, because
``_register_techniques`` hard-coded ``source='user'`` for everything including athlete
vocabulary. That is fixed in ``db/repository.py`` in the same change; a migration that
cleaned the data while the writer kept producing it would be a chore, not a fix.

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


# Everything the delete did NOT take is public by elimination, so this runs SECOND and needs no
# condition of its own beyond the provenance stamp.
#
# Ordering the two the other way around required each to carry half of the same predicate, and
# a key named by BOTH an athlete and a user graph fell between them: not deleted (correctly) and
# not recanonicalised either (incorrectly), keeping user provenance on ordinary vocabulary.
# Delete-then-sweep makes them complementary by construction rather than by matching conditions.
_RECANONICALISE = """
update public.technique_nodes tn
   set source = 'library'
 where tn.source = 'user';
"""

# Private by evidence: a user graph names it and NO athlete graph does. The label survives in
# graph_nodes either way.
#
# BOTH halves are load-bearing. "A user graph names it" is not the test on its own — an athlete
# logs "Back Control" and "Single Leg Takedown" too, so a key can be named by both, and treating
# that as private deletes ordinary public vocabulary. The measurement that sized this migration
# always said `priv AND NOT pub`; an earlier version of this statement dropped the second half.
_DELETE_PRIVATE = """
delete from public.technique_nodes tn
 where tn.source = 'user'
   and exists (
       select 1
         from public.graph_nodes gn
         join public.graphs g on g.id = gn.graph_id
        where gn.node_key = tn.node_key
          and g.owner_kind = 'user'
   )
   and not exists (
       select 1
         from public.graph_nodes gn
         join public.graphs g on g.id = gn.graph_id
        where gn.node_key = tn.node_key
          and g.owner_kind = 'athlete'
   );
"""


def upgrade() -> None:
    # Delete first, then sweep. See the note on _RECANONICALISE for why the order matters.
    op.execute(_DELETE_PRIVATE)
    op.execute(_RECANONICALISE)

    # The client's write path into the shared library, closed. Nothing in the App has
    # needed it since 0037 — graph sync goes through `replace_user_graph`, which does not
    # mention this table. A client that can still insert here can still publish a label.
    op.execute("drop policy if exists technique_nodes_user_insert on public.technique_nodes;")
    op.execute("revoke insert, update, delete on public.technique_nodes from anon, authenticated;")

    # `bundle_imports` went with `db.ingest`, the offline file-import path it audited. That
    # path wrote user labels into this same library, created `profiles` rows for ids that
    # need not exist in `auth.users`, and bypassed the consent gate entirely — a second
    # private-data writer serving no user, with zero rows to its name.
    op.drop_table("bundle_imports")


def downgrade() -> None:
    op.create_table(
        "bundle_imports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.execute("grant insert on public.technique_nodes to authenticated;")
    op.execute(
        """
        create policy technique_nodes_user_insert on public.technique_nodes for insert
          to authenticated
          with check (source = 'user');
        """
    )
    # The rows are NOT restored. Which label was private and which merely carried the wrong
    # provenance is not recoverable from the schema, and re-inserting a guess would put
    # someone's own words back into a world-readable table — the exact thing this revision
    # exists to undo.
