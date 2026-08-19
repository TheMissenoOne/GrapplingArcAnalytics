"""Put back the public vocabulary 0040 deleted, and make the library self-healing.

0040 deleted rows it should have recanonicalised. Its condition was "a user graph names this
key", when the test it was sized against — and stated in its own docstring — was "a user graph
names it AND no athlete graph does". A key can be named by both: an athlete logs "Back Control"
and "Single Leg Takedown" exactly as much as an athlete corpus contains them.

Measured on production after 0040 ran: **18 keys**, referenced by **1,072 athlete graph nodes**,
were deleted from the shared library. Not private labels — core grappling vocabulary: Back
Control, Mount, Single Leg Takedown, Double Leg Takedown, Guard Pull, Knee Cut Pass, Omoplata,
De La Riva Guard.

Nothing was lost that cannot be restored exactly. 0037 gave every graph its own node identity,
so the label and type survived in ``graph_nodes`` under the athlete graphs that use them; the
``on delete set null`` FK merely unlinked them. This reads those rows back.

Written as a REPAIR rather than a fixed list, so it is idempotent and covers any key an athlete
graph names that the library happens to be missing — including the next time something removes
one. 0040's condition is corrected in the same change, so an environment built fresh from the
chain never produces the state this repairs.

The complementary invariant is now asserted in CI. The existing check was one-directional —
"no private label is in the public library" — and deleting too much does not violate it. Its
mirror, "every athlete graph node resolves to a library row", is what would have caught this
before it reached production.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


# The athlete graphs are the record of what the corpus contains, so they are the source the
# library is rebuilt from. `source='library'` because that is what athlete-derived vocabulary
# is — which is the very correction 0040 was written to make.
_RESTORE = """
insert into public.technique_nodes (id, node_key, label, type, node_type, source)
select gen_random_uuid(), gn.node_key, min(gn.label), min(coalesce(gn.type, 'technique')),
       coalesce(min(gn.node_type), ''), 'library'
  from public.graph_nodes gn
  join public.graphs g on g.id = gn.graph_id
 where g.owner_kind = 'athlete'
   and gn.node_key <> ''
   and not exists (
         select 1 from public.technique_nodes tn where tn.node_key = gn.node_key
       )
 group by gn.node_key
on conflict (node_key) do nothing;
"""

# Relink what the delete unlinked. Scoped to rows whose key now resolves, so a genuinely
# private node — which has no library row and must not acquire one — is left alone.
_RELINK = """
update public.graph_nodes gn
   set canonical_node_key = gn.node_key
 where gn.canonical_node_key is null
   and exists (
         select 1 from public.technique_nodes tn where tn.node_key = gn.node_key
       );
"""


def upgrade() -> None:
    op.execute(_RESTORE)
    op.execute(_RELINK)


def downgrade() -> None:
    # Deliberately empty. The upgrade restores vocabulary that should never have been absent;
    # deleting it again would recreate a defect, and there is no version of this schema that is
    # better off without "Back Control" in its library.
    pass
