"""Config assembly + the thinking-level retry fallback -- both mock the genai client, no
network. What actually reads a real frame sheet is exercised by hand (docs/video_frames_gemini.md
logs the first real run), not here.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.genai")

from google.genai import errors, types

from scripts.gemini_read_frames import build_generate_config, read_frames


def test_build_generate_config_with_thinking():
    cfg = build_generate_config("high")
    assert cfg.response_mime_type == "application/json"
    assert cfg.automatic_function_calling.disable is True
    assert cfg.thinking_config.thinking_level == types.ThinkingLevel.HIGH


def test_build_generate_config_without_thinking():
    cfg = build_generate_config(None)
    assert cfg.thinking_config is None
    assert cfg.automatic_function_calling.disable is True


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def _fake_response(events: int = 1) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps({"bout": {}, "events": [{}] * events})
    resp.usage_metadata = None
    return resp


def test_read_frames_stamps_source_and_thinking(tmp_path):
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"fake")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_response()

    with patch("google.genai.Client", return_value=fake_client):
        answer, resp = read_frames([sheet], "prompt", "gemini-3.6-flash", "high")

    assert "thinking=high" in answer["source"]
    assert "gemini-3.6-flash" in answer["source"]
    _, kwargs = fake_client.models.generate_content.call_args
    assert kwargs["config"].thinking_config.thinking_level == types.ThinkingLevel.HIGH


def test_read_frames_retries_without_thinking_on_client_error(tmp_path):
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"fake")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [
        errors.ClientError(400, {"error": "thinking_level not supported"}),
        _fake_response(),
    ]

    with patch("google.genai.Client", return_value=fake_client):
        answer, resp = read_frames([sheet], "prompt", "some-model", "high")

    assert "thinking=none" in answer["source"]
    assert fake_client.models.generate_content.call_count == 2
    second_call_config = fake_client.models.generate_content.call_args_list[1].kwargs["config"]
    assert second_call_config.thinking_config is None
