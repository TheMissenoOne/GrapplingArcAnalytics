"""Create a group, mint an invite, print the roster.

The professor's real UI is piece C (the web app). Until it exists, this is how a
gym gets a group and a code to hand out.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from db.models import Group, GroupInvite, GroupMember

# Characters a person can read off a whiteboard and type without a second try:
# no 0/O, no 1/I/L, no 5/S, no 8/B.
_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"


def _code() -> str:
    return "GA-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def create_group(session, owner_id: str, name: str) -> Group:
    group = Group(owner_id=owner_id, name=name)
    session.add(group)
    session.flush()
    session.add(GroupMember(group_id=group.id, profile_id=owner_id, role="owner"))
    session.commit()
    return group


def mint_invite(session, group_id: str, created_by: str, days: int = 7) -> GroupInvite:
    invite = GroupInvite(
        code=_code(),
        group_id=group_id,
        created_by=created_by,
        expires_at=datetime.now(UTC) + timedelta(days=days),
    )
    session.add(invite)
    session.commit()
    return invite


def roster(session, group_id: str) -> list[tuple[str, str]]:
    rows = session.query(GroupMember).filter_by(group_id=group_id).all()
    return [(m.profile_id, m.role) for m in rows]
