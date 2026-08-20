"""Where a bout starts in its video, and which clock its event timestamps are on.

A bout is normally a few minutes inside a multi-hour broadcast. Today the only place that
offset can live is the ``t=`` parameter inside ``video_url``, which makes it a substring to
be re-parsed rather than a value to be read, and leaves nowhere at all to record it when
the offset is known but the URL is not.

The corpus has the data already: ``url_mapping.json`` carries ``start``/``seconds`` per bout
for 307 entries across 39 events. 296 matches sit in events where a verified stream URL
exists and could not be attached for want of this column.

**Two columns, because the offset alone is not enough.** The corpus mixes two clocks and
nothing records which one a row uses:

    matches WITH a link    median first ts 4081s, 81% start past 1200s  -> video-absolute
    matches WITHOUT a link median first ts  108s, 65% start under 300s  -> bout-relative

Given only an offset, a consumer cannot tell whether to add it to ``sequence[].ts`` or not,
and guessing wrong puts every frame in the wrong part of the broadcast while still looking
plausible. That is exactly the failure documented in ``docs/analytics_audit/AA-010`` — two
different bouts claiming the same seconds of one video — and the reason 31 verified links
were held back rather than written.

``ts_origin`` is deliberately nullable with no default: NULL means "nobody has established
which clock this row is on", which is the honest state for most of the corpus today. It is
NOT a synonym for either value, and a reader must treat NULL as "cannot locate this bout"
rather than assuming absolute.

**Backfill.** ``video_start_seconds`` is populated from the ``t=`` already present in
``video_url`` — a pure re-expression of a value the row already carries, not a new claim.
``ts_origin`` is left NULL everywhere: inferring it from the first timestamp is a heuristic,
and this migration does not smuggle a heuristic into a schema change.

Privacy class: **A, public competition data.** Both columns describe published broadcast
footage of a published bout. Nothing here touches user-fed data.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres migration (0019's scope note). The columns are covered by the model
round-trip in ``tests/test_db.py``; the DDL is covered by the migrations-smoke job.

**Apply this migration and the model fields in the same breath.** The `Match` model fields
are deliberately NOT in ``db/models.py`` yet: adding them before the column exists makes
every ORM read of ``matches`` select a missing column, and the site export dies on its first
query. Paste these into ``Match`` immediately after ``video_url`` once ``alembic upgrade``
has run::

    # Where this bout starts inside ``video_url`` (alembic 0047). Backfilled from the ``t=``
    # already in the URL; also settable when the offset is known but the link is not.
    video_start_seconds: Mapped[int | None] = mapped_column(Integer)
    # Which clock ``sequence[].ts`` is on: 'video_absolute' (offsets into the whole video)
    # or 'bout_relative' (offsets from the bout's own start). The corpus mixes both and NULL
    # means nobody has established which -- a reader must treat NULL as "cannot locate",
    # never as a default of either. Guessing wrong silently places frames in a different
    # fight (AA-010).
    ts_origin: Mapped[str | None] = mapped_column(String(16))

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

TS_ORIGIN_CK = "ck_matches_ts_origin"


def upgrade() -> None:
    op.add_column("matches", sa.Column("video_start_seconds", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("ts_origin", sa.String(length=16), nullable=True))

    # only two clocks are meaningful; anything else is a typo, and NULL stays legal
    op.execute(f"alter table matches drop constraint if exists {TS_ORIGIN_CK}")
    op.execute(
        f"alter table matches add constraint {TS_ORIGIN_CK} "
        "check (ts_origin is null or ts_origin in ('video_absolute', 'bout_relative'))"
    )

    # a non-negative offset is the only sane value; catch a sign error at write time
    op.execute("alter table matches drop constraint if exists ck_matches_video_start_seconds")
    op.execute(
        "alter table matches add constraint ck_matches_video_start_seconds "
        "check (video_start_seconds is null or video_start_seconds >= 0)"
    )

    # backfill from the offset already embedded in video_url; no new claim is made
    op.execute(
        r"""
        update matches
           set video_start_seconds = cast(substring(video_url from '[?&]t=([0-9]+)') as integer)
         where video_url is not null
           and video_url ~ '[?&]t=[0-9]+'
           and video_start_seconds is null
        """
    )

    # the common read is "bouts on this video, in order" when locating one inside a broadcast
    op.execute(
        "create index if not exists ix_matches_video_start "
        "on matches (video_url, video_start_seconds) where video_url is not null"
    )


def downgrade() -> None:
    op.execute("drop index if exists ix_matches_video_start")
    op.execute("alter table matches drop constraint if exists ck_matches_video_start_seconds")
    op.execute(f"alter table matches drop constraint if exists {TS_ORIGIN_CK}")
    op.drop_column("matches", "ts_origin")
    op.drop_column("matches", "video_start_seconds")
