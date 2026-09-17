"""Reference-owner resolution — consented accounts whose round videos/annotations may be
used as a BENCHMARK for the automatic round reader (docs/video_jobs.md, root CLAUDE.md
"Public vs Private Data"). Consent lives ONLY in the ``REFERENCE_OWNER_EMAILS`` env var
(comma-separated), never in code or git — same reasoning as ``ADMIN_PASSWORD_HASH``.

Purpose limitation: outputs of using this list stay under ``data/video/owner/`` (gitignored,
see ``data/video/`` in .gitignore) and evaluate/improve the reader for that SAME owner. Never
corpus, dataset, site, centroid, or ELO input — see CLAUDE.md.
"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.orm import Session


def reference_owner_emails() -> list[str]:
    """Parse ``REFERENCE_OWNER_EMAILS`` — comma-split, lower/strip, empty entries dropped."""
    raw = os.environ.get("REFERENCE_OWNER_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def reference_owner_ids(session: Session) -> list[str]:
    """Resolve consented emails to ``profiles.id`` via ``auth.users.email`` (raw SQL — Supabase-
    managed schema, never mirrored in db/models.py, same pattern as
    scripts/group_admin.py:resolve_owner_email). Raises if the env var is empty — a caller
    asking for the reference list with none configured is a bug, not an empty result."""
    emails = reference_owner_emails()
    if not emails:
        raise RuntimeError(
            "REFERENCE_OWNER_EMAILS not set — no consented reference-owner accounts configured"
        )
    rows = session.execute(
        text("""
            select p.id from auth.users u
            join public.profiles p on p.id = u.id
            where lower(u.email) = any(:emails)
        """),
        {"emails": emails},
    ).all()
    return [row[0] for row in rows]


def is_reference_owner(owner_id: str, session: Session) -> bool:
    """True iff ``owner_id`` is one of the consented reference-owner profile ids."""
    return owner_id in reference_owner_ids(session)


if __name__ == "__main__":
    # ponytail: manual smoke check, no fixture. Real coverage in tests/test_reference_owner.py.
    os.environ["REFERENCE_OWNER_EMAILS"] = " A@B.com ,, c@d.com "
    assert reference_owner_emails() == ["a@b.com", "c@d.com"]
    del os.environ["REFERENCE_OWNER_EMAILS"]
    assert reference_owner_emails() == []
    print("ok")
