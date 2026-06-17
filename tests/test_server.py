"""Tests for the realtime server entrypoint (no network)."""

from __future__ import annotations

import realtime.server as srv


def test_build_app_without_key(monkeypatch) -> None:
    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    monkeypatch.setattr(srv, "_maybe_store", lambda: None)
    app = srv.build_app()
    assert app.state.roboflow is None  # falls back to pose+sklearn


def test_build_app_with_key(monkeypatch) -> None:
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key")
    monkeypatch.setattr(srv, "_maybe_store", lambda: None)
    app = srv.build_app()
    assert app.state.roboflow is not None
    assert app.state.roboflow.model_id == "bjj3/1"
