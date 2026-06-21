"""Admin dashboard tests — TestClient with mocked DB session."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


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
