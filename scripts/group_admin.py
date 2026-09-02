"""Create a group, mint an invite, print the roster — the dono's CLI, not a client path.

The professor's real UI is piece C (the web app). Until it exists (and even after — an
academy account is born ONLY here, never through client-facing `create_group()`), this is
how a gym gets a group and a code to hand out. Everything below writes directly through a
SQLAlchemy session, bypassing RLS entirely — this is the admin console's path, never the
App/Web's (they go through `create_group()`/`join_group()`, alembic 0024/0050).

CLI (dry-run by default — pass ``--apply`` to actually write):

    uv run python -m scripts.group_admin create --owner-email a@b.com --name "Gracie Barra"
    uv run python -m scripts.group_admin invite --group-id <uuid> --role professor
    uv run python -m scripts.group_admin roster --group-id <uuid> [--role professor]
    uv run python -m scripts.group_admin groups [--owner-email a@b.com]
"""

from __future__ import annotations

import argparse
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from db.models import Group, GroupInvite, GroupMember

logger = logging.getLogger("group_admin")

# Characters a person can read off a whiteboard and type without a second try:
# no 0/O, no 1/I/L, no 5/S, no 8/B.
_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"

_ROLES = ("student", "professor")


def _code() -> str:
    return "GA-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def create_group(session, owner_id: str, name: str) -> Group:
    group = Group(owner_id=owner_id, name=name)
    session.add(group)
    session.flush()
    session.add(GroupMember(group_id=group.id, profile_id=owner_id, role="owner"))
    session.commit()
    return group


def mint_invite(
    session, group_id: str, created_by: str, days: int = 7, role: str = "student"
) -> GroupInvite:
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}, got {role!r}")
    invite = GroupInvite(
        code=_code(),
        group_id=group_id,
        created_by=created_by,
        role=role,
        expires_at=datetime.now(UTC) + timedelta(days=days),
    )
    session.add(invite)
    session.commit()
    return invite


def roster(session, group_id: str) -> list[tuple[str, str]]:
    rows = session.query(GroupMember).filter_by(group_id=group_id).all()
    return [(m.profile_id, m.role) for m in rows]


def resolve_owner_email(session, email: str) -> str | None:
    """``profiles`` carries no email (it lives only in Supabase's ``auth.users``,
    ``profiles.id`` = ``auth.users.id`` per the FK added in alembic 0023). Raw SQL, not an
    ORM model — ``auth.users`` is Supabase-managed schema, never mirrored in `db/models.py`.
    Returns ``None`` if no profile row exists yet (e.g. the user has an auth account but
    never opened the app, so `handle_new_user()` never fired)."""
    row = session.execute(
        text("""
            select p.id from auth.users u
            join public.profiles p on p.id = u.id
            where u.email = :email
        """),
        {"email": email},
    ).first()
    return row[0] if row else None


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="new academy — owner + group + owner membership")
    p_create.add_argument("--owner-email", required=True, help="email of the academy's auth.users row")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--apply", action="store_true", help="write. default: report only")

    p_invite = sub.add_parser("invite", help="mint an invite code")
    p_invite.add_argument("--group-id", required=True)
    p_invite.add_argument("--created-by", help="profile id; defaults to the group's owner")
    p_invite.add_argument("--role", choices=_ROLES, default="student")
    p_invite.add_argument("--days", type=int, default=7)
    p_invite.add_argument("--apply", action="store_true", help="write. default: report only")

    p_roster = sub.add_parser("roster", help="list a group's members")
    p_roster.add_argument("--group-id", required=True)
    p_roster.add_argument("--role", choices=("owner", *_ROLES), help="filter to one role")

    p_groups = sub.add_parser("groups", help="list groups, optionally by owner")
    p_groups.add_argument("--owner-email")

    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from db.base import get_session_factory

    with get_session_factory()() as session:
        if args.cmd == "create":
            owner_id = resolve_owner_email(session, args.owner_email)
            if owner_id is None:
                logger.info("no profile for %s — they must sign in at least once first.", args.owner_email)
                return
            if not args.apply:
                logger.info(
                    "dry run — would create group %r owned by %s (%s). Pass --apply to write.",
                    args.name, args.owner_email, owner_id,
                )
                return
            group = create_group(session, owner_id, args.name)
            logger.info("created group %s %r, owner %s", group.id, group.name, owner_id)

        elif args.cmd == "invite":
            created_by = args.created_by
            if created_by is None:
                group = session.get(Group, args.group_id)
                if group is None:
                    logger.info("no group %s", args.group_id)
                    return
                created_by = group.owner_id
            if not args.apply:
                logger.info(
                    "dry run — would mint a %s invite for group %s (%d days). Pass --apply to write.",
                    args.role, args.group_id, args.days,
                )
                return
            invite = mint_invite(session, args.group_id, created_by, days=args.days, role=args.role)
            logger.info("invite %s — role %s, expires %s", invite.code, invite.role, invite.expires_at)

        elif args.cmd == "roster":
            rows = roster(session, args.group_id)
            if args.role:
                rows = [r for r in rows if r[1] == args.role]
            if not rows:
                logger.info("no members" + (f" with role {args.role}" if args.role else ""))
            for profile_id, role in rows:
                logger.info("  %s  %s", role, profile_id)

        elif args.cmd == "groups":
            query = session.query(Group)
            if args.owner_email:
                owner_id = resolve_owner_email(session, args.owner_email)
                if owner_id is None:
                    logger.info("no profile for %s", args.owner_email)
                    return
                query = query.filter_by(owner_id=owner_id)
            for group in query.all():
                logger.info("  %s  %s  owner=%s", group.id, group.name, group.owner_id)


if __name__ == "__main__":
    _main()
