"""``scripts.shadow_chain_compiler._side_of`` — frozen-key vs live-alias desync.

2026-09-14: `analysis.names.ATHLETE_ALIASES` gained `"bia mesquita": "beatriz mesquita"`
(commit 806e338). A dump's `athlete_a_key`/`athlete_b_key` are frozen at export time; the
private corpus dump this repo tests against (never committed, LGPD) predates that alias, so
its `athlete_b_key` still read the pre-alias form while `_side_of` ran the live `actor` string
through the post-alias `athlete_key()` — matching neither side and silently dropping one
bout's whole `b` side (3 observed actions). Root-caused in `_side_of` itself: the frozen keys
must go through the same `athlete_key()` canonicalization the live actor does.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import analysis.names as names_mod
from scripts.shadow_chain_compiler import _side_of


def test_side_of_resolves_a_frozen_key_through_an_alias_added_after_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alias added AFTER the dump was frozen: the dump's stored `athlete_b_key` is the
    # PRE-alias form ("old form"), the live actor string canonicalizes to the SAME pre-alias
    # form first, then through the alias to "new canonical form".
    monkeypatch.setitem(names_mod.ATHLETE_ALIASES, "old form", "new canonical form")

    match: dict[str, Any] = {"athlete_a_key": "someone else", "athlete_b_key": "old form"}
    ev: Mapping[str, Any] = {"actor": "Old Form"}

    side_of = _side_of(match)
    assert side_of(ev) == "b"


def test_side_of_still_resolves_a_key_with_no_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sanity: canonicalizing an already-canonical, alias-free key must be a no-op.
    match: dict[str, Any] = {"athlete_a_key": "athlete a", "athlete_b_key": "athlete b"}
    side_of = _side_of(match)
    assert side_of({"actor": "Athlete A"}) == "a"
    assert side_of({"actor": "Athlete B"}) == "b"
    assert side_of({"actor": "Referee"}) is None


def test_side_of_handles_a_missing_frozen_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A match dict missing athlete_a_key/b_key (None) must not crash and must resolve nothing.
    match: dict[str, Any] = {}
    side_of = _side_of(match)
    assert side_of({"actor": "Anyone"}) is None
