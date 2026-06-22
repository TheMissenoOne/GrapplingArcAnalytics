"""Cookie-session admin auth — password from ADMIN_PASSWORD env var."""

from __future__ import annotations

import os
import secrets

from fastapi import Cookie, HTTPException, Request, status

_SESSIONS: dict[str, bool] = {}
_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
_COOKIE_NAME = "admin_session"


def require_auth(admin_session: str | None = Cookie(default=None)) -> None:
    if not admin_session or admin_session not in _SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(_COOKIE_NAME)
    return bool(token and token in _SESSIONS)


def create_session() -> str:
    token = secrets.token_hex(32)
    _SESSIONS[token] = True
    return token


def destroy_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def check_password(password: str) -> bool:
    return secrets.compare_digest(password, _PASSWORD)
