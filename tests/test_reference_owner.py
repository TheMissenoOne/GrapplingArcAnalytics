"""analysis/reference_owner.py — env parsing + id resolution (mocked session, no Postgres)."""

from __future__ import annotations

import os
from typing import cast

import pytest
from sqlalchemy.orm import Session

from analysis.reference_owner import (
    is_reference_owner,
    reference_owner_emails,
    reference_owner_ids,
)


class _FakeResult:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str]]:
        return self._rows


class _FakeSession:
    """Stands in for a SQLAlchemy Session against a fixed email -> profile id map."""

    def __init__(self, email_to_id: dict[str, str]) -> None:
        self._email_to_id = email_to_id

    def execute(self, _stmt: object, params: dict[str, object]) -> _FakeResult:
        emails = params["emails"]
        assert isinstance(emails, list)
        rows = [(pid,) for email, pid in self._email_to_id.items() if email in emails]
        return _FakeResult(rows)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFERENCE_OWNER_EMAILS", raising=False)


def test_reference_owner_emails_empty_when_unset() -> None:
    assert reference_owner_emails() == []


def test_reference_owner_emails_empty_string() -> None:
    os.environ["REFERENCE_OWNER_EMAILS"] = ""
    assert reference_owner_emails() == []


def test_reference_owner_emails_parses_comma_lower_strip() -> None:
    os.environ["REFERENCE_OWNER_EMAILS"] = " Owner@Example.com , second@x.com ,, "
    assert reference_owner_emails() == ["owner@example.com", "second@x.com"]


def test_reference_owner_ids_raises_when_env_empty() -> None:
    with pytest.raises(RuntimeError):
        reference_owner_ids(cast(Session, _FakeSession({})))


def test_reference_owner_ids_resolves_emails() -> None:
    os.environ["REFERENCE_OWNER_EMAILS"] = "owner@example.com"
    session = cast(
        Session, _FakeSession({"owner@example.com": "uid-1", "other@example.com": "uid-2"})
    )
    assert reference_owner_ids(session) == ["uid-1"]


def test_is_reference_owner_true_and_false() -> None:
    os.environ["REFERENCE_OWNER_EMAILS"] = "owner@example.com"
    session = cast(
        Session, _FakeSession({"owner@example.com": "uid-1", "other@example.com": "uid-2"})
    )
    assert is_reference_owner("uid-1", session) is True
    assert is_reference_owner("uid-2", session) is False
