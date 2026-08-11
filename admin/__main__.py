"""Local entry point for the admin dashboard — ``uv run --extra web python -m admin``.

Loads ``.env`` from the repo root (so DATABASE_URL / ADMIN_PASSWORD_HASH resolve without
shell gymnastics), then serves the dashboard on 127.0.0.1. Nothing here touches the
public internet: local personal control panel only.

Env overrides:
  ADMIN_HOST  (default 127.0.0.1)  ADMIN_PORT  (default 8765)
  ADMIN_NO_AUTH=1 disables the login gate + CSRF (single-operator local convenience).

Port 8765 on purpose — 8000 is the RPi realtime service (realtime/) convention, and a
local dashboard should never collide with a deployed service on the same box.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Secure cookies are dropped by browsers over plain http, so the default would make a
# local-only dashboard unable to log in. Only flip when the operator explicitly sets it.
os.environ.setdefault("ADMIN_COOKIE_SECURE", "false")

# `.env` carries ADMIN_PASSWORD in plain (operator-friendly); auth.py only knows
# ADMIN_PASSWORD_HASH. Derive the hash at boot when it's missing, so `python -m admin`
# works off the same `.env` as everything else. If NEITHER is set, auth.py still refuses
# to boot with a password-less default — the security invariant holds.
# Importing admin.auth reads ADMIN_PASSWORD_HASH at import time (it raises if unset), so
# the placeholder must be in place BEFORE the import; the real hash replaces it right after.
_NO_AUTH = os.environ.get("ADMIN_NO_AUTH", "0").strip().lower() in ("1", "true", "yes", "on")
if _NO_AUTH and not os.environ.get("ADMIN_PASSWORD_HASH") and not os.environ.get("ADMIN_PASSWORD"):
    # No-auth mode + neither password key set: give auth.py any string so it boots; the
    # middleware never checks it when ADMIN_NO_AUTH=1.
    os.environ["ADMIN_PASSWORD_HASH"] = "no-auth-disabled"

if not os.environ.get("ADMIN_PASSWORD_HASH"):
    plain = os.environ.get("ADMIN_PASSWORD")
    if plain:
        os.environ["ADMIN_PASSWORD_HASH"] = "pending-derivation"
        import admin.auth as auth

        auth._PASSWORD_HASH = auth.hash_password(plain)


def main() -> None:
    import uvicorn

    host = os.environ.get("ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("ADMIN_PORT", "8765"))
    uvicorn.run("admin.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
