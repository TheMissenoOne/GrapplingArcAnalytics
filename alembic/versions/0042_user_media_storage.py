"""A second private bucket, because the first one has a professor in it.

Study attachments need somewhere to live, and the obvious move is to reuse ``session-videos``
— the path shape is identical (``{ownerId}/{recordId}/{mediaId}``) and the owner-prefix policy
would carry over untouched. It is the wrong move, and not for naming reasons.

``session_videos_professor_read`` (0024) lets a professor SELECT any object under any of their
members' prefixes in that bucket. That is deliberate and correct for session video: a student
records a round and their professor reviews it — that is the whole feature. It is exactly wrong
for a study attachment, which is private journal material the user never offered to anyone. A
bucket is the unit that policy is written against, so putting the two kinds of file in one
bucket means the professor's read reaches both, silently, the day the first attachment lands.
Two buckets is the smaller change than teaching one policy to tell the file kinds apart by
inspecting their paths — and a path-parsing access rule is a bad thing to depend on.

So: ``user-media``, private, one owner-prefix policy and nothing else. Same
``{ownerId}/{recordId}/{mediaId}`` layout, because that leading segment is what
``storage.foldername(name)[1] = auth.uid()`` compares — it is the access rule, not a filing
convention, and the App's upload queue keeps it for every target kind.

Nothing moves. ``session-videos`` keeps every object it has and stays the destination for
session video; this bucket is for the media kinds that were never the professor's to read.

Deleting an account now has to sweep BOTH buckets. That is in this same change, in the
``delete-account`` Edge Function — a new bucket that the deletion path does not know about is
a function that reports success while leaving the user's files on the server, which is the one
outcome that function exists to prevent.

Privacy class: **C, user cloud-synced private data**. Owner-scoped, never aggregated, never an
input to anything public or competitive.

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        insert into storage.buckets (id, name, public)
        values ('user-media', 'user-media', false)
        on conflict (id) do nothing;
        """
    )

    # `for all` with both USING and WITH CHECK: USING gates what you can see and modify,
    # WITH CHECK gates what you can leave behind. Only one of the two would let an
    # authenticated user write into someone else's prefix (or read out of it).
    op.execute("drop policy if exists user_media_owner_all on storage.objects;")
    op.execute(
        """
        create policy user_media_owner_all on storage.objects for all to authenticated
        using (bucket_id = 'user-media'
               and (storage.foldername(name))[1] = auth.uid()::text)
        with check (bucket_id = 'user-media'
                    and (storage.foldername(name))[1] = auth.uid()::text);
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists user_media_owner_all on storage.objects;")
    # The bucket itself is NOT dropped. `delete from storage.buckets` on a non-empty bucket
    # either errors on the objects FK or, worse, takes the user's files with it. Downgrading a
    # schema revision must not be a data-destroying operation — an empty bucket left behind
    # costs nothing and re-upgrading is a no-op on conflict.


