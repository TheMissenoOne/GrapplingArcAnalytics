"""SQLAlchemy engine and session factory — connect via DATABASE_URL env var."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _make_engine() -> Engine:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL not set")
    # Build from parsed components rather than the raw string. Supabase passwords
    # routinely contain characters that are special in a URL (notably ``@``); left
    # unencoded they make a naive parser mis-split the host. ``urlsplit`` separates
    # userinfo on the LAST ``@`` so the host is correct, and psycopg reads the
    # password from the URL object's attribute (no re-encoding round-trip). We ship
    # psycopg v3 (the ``postgres`` extra), so target ``postgresql+psycopg`` instead
    # of the default psycopg2.
    parts = urlsplit(raw)
    if parts.hostname and (parts.username or parts.password):
        url = URL.create(
            "postgresql+psycopg",
            username=parts.username,
            password=parts.password,
            host=parts.hostname,
            port=parts.port,
            database=parts.path.lstrip("/") or None,
            query=dict(parse_qsl(parts.query)),
        )
        return create_engine(url, pool_pre_ping=True)
    return create_engine(raw, pool_pre_ping=True)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def db_session() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
