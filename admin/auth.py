"""Cookie-session admin auth — scrypt-hashed password, TTL sessions, CSRF, rate limit, audit."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time

from fastapi import Request

_COOKIE_NAME = "admin_session"
SESSION_TTL_SECONDS = 12 * 60 * 60  # 12h — public: server.py needs it for cookie max_age
_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
_RATE_LIMIT_MAX_ATTEMPTS = 5
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1

_audit_log = logging.getLogger("admin.audit")


def _env_password_hash() -> str:
    val = os.environ.get("ADMIN_PASSWORD_HASH")
    if not val:
        raise RuntimeError(
            "ADMIN_PASSWORD_HASH is not set — refusing to start with an insecure default. "
            "Generate one with: python -m admin.auth --hash"
        )
    return val


# ponytail: skip the fail-fast only for `python -m admin.auth --hash` itself (the tool
# that generates this value in the first place) — every other entrypoint requires it.
_PASSWORD_HASH = "" if __name__ == "__main__" else _env_password_hash()

# session token -> expiry unix ts. ponytail: in-process dict, admin runs single-process;
# upgrade path if this ever runs multi-worker/multi-process = a sessions table in Postgres.
_SESSIONS: dict[str, float] = {}
_CSRF_TOKENS: dict[str, str] = {}

# login rate limiting: ip -> (attempt_count, window_start_ts). ponytail: in-process,
# resets on restart; upgrade path if this runs multi-worker = a shared store (Redis/Postgres).
_LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}


def _prune_sessions() -> None:
    now = time.time()
    for token in [t for t, exp in _SESSIONS.items() if exp <= now]:
        _SESSIONS.pop(token, None)
        _CSRF_TOKENS.pop(token, None)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Format: scrypt$n$r$p$salt_b64$hash_b64 — params travel with the hash."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        ]
    )


def check_password(password: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = _PASSWORD_HASH.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
    )
    return secrets.compare_digest(derived, expected)


def check_rate_limit(ip: str) -> bool:
    """True if `ip` may attempt another login right now."""
    count, window_start = _LOGIN_ATTEMPTS.get(ip, (0, time.time()))
    if time.time() - window_start > _RATE_LIMIT_WINDOW_SECONDS:
        return True
    return count < _RATE_LIMIT_MAX_ATTEMPTS


def record_login_attempt(ip: str, *, success: bool) -> None:
    now = time.time()
    count, window_start = _LOGIN_ATTEMPTS.get(ip, (0, now))
    if now - window_start > _RATE_LIMIT_WINDOW_SECONDS:
        count, window_start = 0, now
    if success:
        _LOGIN_ATTEMPTS.pop(ip, None)
    else:
        _LOGIN_ATTEMPTS[ip] = (count + 1, window_start)


def create_session() -> tuple[str, str]:
    """New (session_token, csrf_token) pair; call once per successful login."""
    _prune_sessions()
    token = secrets.token_hex(32)
    csrf = secrets.token_hex(32)
    _SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
    _CSRF_TOKENS[token] = csrf
    return token, csrf


def destroy_session(token: str) -> None:
    _SESSIONS.pop(token, None)
    _CSRF_TOKENS.pop(token, None)


def is_authenticated(request: Request) -> bool:
    _prune_sessions()
    token = request.cookies.get(_COOKIE_NAME)
    return bool(token and token in _SESSIONS)


def csrf_token_for(request: Request) -> str | None:
    token = request.cookies.get(_COOKIE_NAME)
    return _CSRF_TOKENS.get(token) if token else None


def check_csrf(request: Request, submitted: str | None) -> bool:
    expected = csrf_token_for(request)
    return bool(expected and submitted and secrets.compare_digest(expected, submitted))


def audit(actor: str, action: str, entity: str, result: str) -> None:
    """Structured audit line. Never pass password/token/cookie/PII as an argument."""
    _audit_log.info(
        "actor=%s action=%s entity=%s result=%s ts=%d",
        actor,
        action,
        entity,
        result,
        int(time.time()),
    )


def _cli() -> None:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Admin auth helpers")
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Prompt for a password, print its ADMIN_PASSWORD_HASH value",
    )
    args = parser.parse_args()
    if args.hash:
        pw = getpass.getpass("Password to hash: ")
        print(hash_password(pw))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
