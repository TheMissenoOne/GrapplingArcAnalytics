"""Admin dashboard tests — TestClient with mocked DB session."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Any real deploy must set a real hash (see admin/auth.py). The failure path itself
# is proven in isolation by test_import_fails_without_password_hash (subprocess, no
# shared state) — every other test in this file needs *a* value just to import.
os.environ.setdefault("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")

import admin.auth as auth_mod  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_PASSWORD = "unit-test-correct-horse-battery"


def _make_athlete(name: str = "Gordon Ryan", is_published: bool = False) -> MagicMock:
    a = MagicMock()
    a.id = str(uuid.uuid4())
    a.name = name
    a.nickname = "The King"
    a.team = "New Wave"
    a.weight_class = "-99kg"
    a.belt = "black"
    a.elo = 1800.0
    a.source = "manual"
    a.is_published = is_published
    return a


@pytest.fixture()
def client():
    """FastAPI TestClient with auth bypassed and DB patched."""
    from fastapi.testclient import TestClient

    with (
        patch("admin.server.is_authenticated", return_value=True),
        patch("admin.server.db_session"),
        patch("admin.server._build_node_options", return_value=[]),
    ):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture()
def real_password(monkeypatch):
    """Configure a real, known password hash for tests that exercise the real auth path."""
    monkeypatch.setattr(auth_mod, "_PASSWORD_HASH", auth_mod.hash_password(TEST_PASSWORD))
    yield
    auth_mod._LOGIN_ATTEMPTS.clear()
    auth_mod._SESSIONS.clear()
    auth_mod._CSRF_TOKENS.clear()


@pytest.fixture()
def authed_client(real_password):
    """A real (non-bypassed) TestClient that has actually logged in via /admin/login."""
    from fastapi.testclient import TestClient

    with (
        patch("admin.server.db_session"),
        patch("admin.server._build_node_options", return_value=[]),
    ):
        from admin.server import create_admin_app
        app = create_admin_app()
        # https base_url: the session cookie is Secure by default (ADMIN_COOKIE_SECURE),
        # and a Secure cookie is never re-sent by the client over a plain http origin.
        with TestClient(
            app,
            raise_server_exceptions=False,
            client=("10.0.0.1", 1),
            base_url="https://testserver",
        ) as c:
            resp = c.post(
                "/admin/login", data={"password": TEST_PASSWORD}, follow_redirects=False
            )
            assert resp.status_code == 303
            yield c


def _csrf_for(client) -> str:
    token = client.cookies.get(auth_mod._COOKIE_NAME)
    return auth_mod._CSRF_TOKENS[token]


def test_login_page_renders():
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app) as c:
            resp = c.get("/admin/login", follow_redirects=False)
    assert resp.status_code == 200
    assert b"Sign in" in resp.content


def test_login_wrong_password():
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app) as c:
            resp = c.post("/admin/login", data={"password": "wrong"}, follow_redirects=False)
    assert resp.status_code == 200
    assert b"Invalid" in resp.content


def test_athletes_redirects_when_unauthenticated():
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app) as c:
            resp = c.get("/admin/athletes", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)


def test_analytics_page_authenticated(client):
    with patch("admin.server.db_session") as mock_ctx:
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value = []
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        resp = client.get("/admin/analytics")
    assert resp.status_code == 200
    assert b"Analytics" in resp.content


# ── GA-005 hardening ─────────────────────────────────────────────────────────


def test_import_fails_without_password_hash():
    """Prove the fail-fast in isolation (subprocess = no shared module state)."""
    env = {k: v for k, v in os.environ.items() if k != "ADMIN_PASSWORD_HASH"}
    result = subprocess.run(
        [sys.executable, "-c", "import admin.auth"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ADMIN_PASSWORD_HASH" in result.stderr


def test_login_correct_password_succeeds_with_real_hash(real_password):
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, follow_redirects=False, client=("10.0.0.9", 1)) as c:
            resp = c.post("/admin/login", data={"password": TEST_PASSWORD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/athletes"


def test_login_sets_hardened_cookie(real_password):
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, follow_redirects=False, client=("10.0.0.10", 1)) as c:
            resp = c.post("/admin/login", data={"password": TEST_PASSWORD})
    set_cookie = resp.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "secure" in set_cookie.lower()  # ADMIN_COOKIE_SECURE defaults True
    assert "max-age=" in set_cookie.lower()


def test_expired_session_is_rejected(authed_client):
    token = authed_client.cookies.get(auth_mod._COOKIE_NAME)
    auth_mod._SESSIONS[token] = time.time() - 1  # force expiry
    resp = authed_client.get("/admin/athletes", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_invalid_cookie_is_rejected():
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, follow_redirects=False) as c:
            c.cookies.set(auth_mod._COOKIE_NAME, "not-a-real-token")
            resp = c.get("/admin/athletes", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_post_without_csrf_token_is_rejected(authed_client):
    with patch("admin.server.seed_athletes_from_leaderboard"):
        resp = authed_client.post("/admin/athletes/seed", follow_redirects=False)
    assert resp.status_code == 403


def test_post_with_invalid_csrf_token_is_rejected(authed_client):
    with patch("admin.server.seed_athletes_from_leaderboard"):
        resp = authed_client.post(
            "/admin/athletes/seed",
            data={"csrf_token": "totally-wrong"},
            follow_redirects=False,
        )
    assert resp.status_code == 403


def test_post_with_valid_csrf_token_succeeds(authed_client):
    csrf = _csrf_for(authed_client)
    with patch("admin.server.seed_athletes_from_leaderboard") as mock_seed:
        resp = authed_client.post(
            "/admin/athletes/seed", data={"csrf_token": csrf}, follow_redirects=False
        )
    assert resp.status_code == 303
    mock_seed.assert_called_once()


def test_logout_invalidates_session(authed_client):
    token = authed_client.cookies.get(auth_mod._COOKIE_NAME)
    resp = authed_client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 303
    # Even if a client resends the old (now-destroyed) cookie, it must not work.
    authed_client.cookies.set(auth_mod._COOKIE_NAME, token)
    resp2 = authed_client.get("/admin/athletes", follow_redirects=False)
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/admin/login"


def test_login_rate_limit_blocks_after_five_failures(real_password):
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, follow_redirects=False, client=("10.0.0.2", 1)) as c:
            for _ in range(5):
                resp = c.post("/admin/login", data={"password": "wrong"})
                assert resp.status_code == 200
            # 6th attempt — even with the CORRECT password — is rate-limited.
            blocked = c.post("/admin/login", data={"password": TEST_PASSWORD})
    assert blocked.status_code == 429


def test_write_route_without_auth_is_rejected():
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, follow_redirects=False) as c:
            resp = c.post("/admin/athletes/seed", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_audit_log_emitted_on_login(real_password, caplog):
    from fastapi.testclient import TestClient

    with patch("admin.server._build_node_options", return_value=[]):
        from admin.server import create_admin_app
        app = create_admin_app()
        with (
            caplog.at_level("INFO", logger="admin.audit"),
            TestClient(app, follow_redirects=False, client=("10.0.0.3", 1)) as c,
        ):
            c.post("/admin/login", data={"password": TEST_PASSWORD})
    assert any(
        "action=login" in r.message and "result=success" in r.message for r in caplog.records
    )
    for r in caplog.records:
        assert TEST_PASSWORD not in r.message


def test_post_form_fields_survive_the_middleware(authed_client):
    """The middleware reads the body to check CSRF — the route must still get the fields.

    BaseHTTPMiddleware consuming `await request.form()` and then calling `call_next` is a
    known way to hand the downstream route an empty body. The seed route above proves a
    CSRF-bearing POST is *allowed* through; it carries no other fields, so it cannot prove
    the fields arrive. This one does.
    """
    csrf = _csrf_for(authed_client)
    with patch("admin.server.upsert_athlete", return_value="athlete-1") as mock_upsert:
        resp = authed_client.post(
            "/admin/athletes",
            data={
                "csrf_token": csrf,
                "name": "Gordon Ryan",
                "nickname": "King",
                "team": "New Wave",
                "weight_class": "+99",
                "belt": "black",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["name"] == "Gordon Ryan"
    assert kwargs["nickname"] == "King"
    assert kwargs["team"] == "New Wave"


def test_expired_rate_limit_entries_are_pruned(real_password):
    """An IP that fails once and never returns must not sit in the dict forever.

    _LOGIN_ATTEMPTS only lost entries on a *subsequent* attempt from the same
    IP (pop on success, overwrite on failure) — an IP that never comes back
    stayed in memory indefinitely. This asserts pruning happens on read, the
    same pattern _prune_sessions already uses for _SESSIONS.
    """
    auth_mod.record_login_attempt("10.0.0.9", success=False)
    assert "10.0.0.9" in auth_mod._LOGIN_ATTEMPTS

    # Simulate the rate-limit window having elapsed with nobody checking back in.
    count, _ = auth_mod._LOGIN_ATTEMPTS["10.0.0.9"]
    auth_mod._LOGIN_ATTEMPTS["10.0.0.9"] = (
        count,
        time.time() - auth_mod._RATE_LIMIT_WINDOW_SECONDS - 1,
    )

    auth_mod.check_rate_limit("10.0.1.1")  # any read should trigger the prune

    assert "10.0.0.9" not in auth_mod._LOGIN_ATTEMPTS
