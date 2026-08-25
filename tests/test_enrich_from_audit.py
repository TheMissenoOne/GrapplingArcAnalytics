"""Pure-function tests for scripts.enrich_from_audit — no DB, no network."""

from __future__ import annotations

import copy
from typing import Any

from scripts.enrich_from_audit import (
    build_event,
    is_duplicate,
    merge_sequence,
    node_key,
    plan_match_enrichment,
    resolve_actor,
)

ID_A = "aaaaaaaa-0000-0000-0000-000000000000"
ID_B = "bbbbbbbb-0000-0000-0000-000000000000"
NAME_A = "Gordon Ryan"
NAME_B = "Andre Galvao"


class TestNodeKey:
    def test_shares_the_clean_label_normalize_canonicalize_chain(self) -> None:
        # "Ankle Pick Takedown" isn't a library variant of "Ankle Pick" (clean_label leaves
        # it untouched) — the two only collapse via the SYNONYMS canonicalize step, same as
        # every other graph/map/ocean consumer.
        assert node_key("Ankle Pick", "takedown") == node_key("Ankle Pick Takedown", "takedown")

    def test_unrelated_labels_differ(self) -> None:
        assert node_key("Armbar", "submission") != node_key("Triangle Choke", "submission")


class TestResolveActor:
    def test_direct_name_match(self) -> None:
        assert resolve_actor(NAME_A, {}, NAME_A, ID_A, NAME_B, ID_B) == ID_A
        assert resolve_actor(NAME_B, {}, NAME_A, ID_A, NAME_B, ID_B) == ID_B

    def test_db_name_map_applied_first(self) -> None:
        db_name_map = {"G Ryan": NAME_A}
        assert resolve_actor("G Ryan", db_name_map, NAME_A, ID_A, NAME_B, ID_B) == ID_A

    def test_unresolvable_actor_returns_none(self) -> None:
        assert resolve_actor("Some Stranger", {}, NAME_A, ID_A, NAME_B, ID_B) is None

    def test_case_and_accent_insensitive_via_athlete_key(self) -> None:
        assert resolve_actor("andre galvao", {}, NAME_A, ID_A, NAME_B, ID_B) == ID_B


class TestIsDuplicate:
    def test_same_label_same_actor_within_window_is_dup(self) -> None:
        existing = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 100}]
        key = node_key("Armbar", "submission")
        assert is_duplicate(existing, ID_A, key, 105) is True

    def test_same_label_same_actor_outside_window_is_not_dup(self) -> None:
        existing = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 100}]
        key = node_key("Armbar", "submission")
        assert is_duplicate(existing, ID_A, key, 200) is False

    def test_different_actor_is_kept(self) -> None:
        existing = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 100}]
        key = node_key("Armbar", "submission")
        assert is_duplicate(existing, ID_B, key, 100) is False

    def test_synonym_label_via_canonicalize_is_dup(self) -> None:
        existing = [{"label": "Ankle Pick", "type": "takedown", "actor_id": ID_A, "ts": 100}]
        key = node_key("Ankle Pick Takedown", "takedown")  # different raw label, same key
        assert is_duplicate(existing, ID_A, key, 105) is True

    def test_existing_event_without_ts_is_always_dup(self) -> None:
        existing = [{"label": "Armbar", "type": "submission", "actor_id": ID_A}]  # no ts
        key = node_key("Armbar", "submission")
        assert is_duplicate(existing, ID_A, key, 99999) is True

    def test_new_event_without_ts_against_existing_with_ts_is_not_dup(self) -> None:
        existing = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 100}]
        key = node_key("Armbar", "submission")
        assert is_duplicate(existing, ID_A, key, None) is False


class TestBuildEvent:
    def test_carries_optional_fields_only_when_present(self) -> None:
        ev = build_event({"label": "Armbar", "type": "submission"}, ID_A)
        assert ev == {"label": "Armbar", "type": "submission", "actor_id": ID_A}

    def test_includes_ts_and_successful_when_present(self) -> None:
        ev = build_event(
            {"label": "Armbar", "type": "submission", "successful": True, "ts": 42}, ID_A
        )
        assert ev == {
            "label": "Armbar", "type": "submission", "actor_id": ID_A,
            "successful": True, "ts": 42,
        }

    def test_label_is_canonicalized(self) -> None:
        ev = build_event({"label": "RNC", "type": "submission"}, ID_A)
        assert ev["label"] == "Rear Naked Choke"


class TestMergeSequence:
    def test_appends_without_resort_when_existing_has_no_ts(self) -> None:
        existing = [{"label": "Closed Guard", "type": "guard", "actor_id": ID_A}]
        new = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 5}]
        assert merge_sequence(existing, new) == existing + new

    def test_resorts_by_ts_when_existing_carries_ts(self) -> None:
        existing = [
            {"label": "Closed Guard", "type": "guard", "actor_id": ID_A, "ts": 10},
            {"label": "Back Control", "type": "control", "actor_id": ID_A, "ts": 200},
        ]
        new = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 100}]
        merged = merge_sequence(existing, new)
        assert [e["label"] for e in merged] == ["Closed Guard", "Armbar", "Back Control"]

    def test_events_without_ts_anchor_near_original_neighbours(self) -> None:
        existing: list[dict[str, Any]] = [
            {"label": "Closed Guard", "type": "guard", "actor_id": ID_A, "ts": 10},
            {"label": "Guard Pass", "type": "pass", "actor_id": ID_B},  # no ts
            {"label": "Back Control", "type": "control", "actor_id": ID_A, "ts": 200},
        ]
        new = [{"label": "Armbar", "type": "submission", "actor_id": ID_A, "ts": 20}]
        merged = merge_sequence(existing, new)
        # "Guard Pass" (no ts) stays right after "Closed Guard" (its forward-filled anchor),
        # not shoved to the end or the front.
        assert [e["label"] for e in merged] == [
            "Closed Guard", "Guard Pass", "Armbar", "Back Control",
        ]


class TestPlanMatchEnrichment:
    def _plan_kwargs(
        self, existing: list[dict[str, Any]], kept: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return dict(
            match_id="m1", name_a=NAME_A, id_a=ID_A, name_b=NAME_B, id_b=ID_B,
            existing_sequence=existing, kept_events=kept, db_name_map={},
            cur_video_start_seconds=None, cur_ts_origin=None, bout_start=None,
        )

    def test_never_mutates_existing_sequence(self) -> None:
        existing = [{"label": "Closed Guard", "type": "guard", "actor_id": ID_A, "ts": 10}]
        before = copy.deepcopy(existing)
        kept = [{"label": "Armbar", "type": "submission", "actor": NAME_A, "ts": 50}]
        plan_match_enrichment(**self._plan_kwargs(existing, kept))
        assert existing == before  # input list/dicts untouched

    def test_inserts_new_resolved_event(self) -> None:
        existing: list[dict[str, Any]] = []
        kept = [{"label": "Armbar", "type": "submission", "actor": NAME_A, "ts": 50}]
        plan = plan_match_enrichment(**self._plan_kwargs(existing, kept))
        assert len(plan.insert_events) == 1
        assert plan.dup_count == 0
        assert plan.unresolved == []

    def test_unresolvable_actor_is_reported_and_skipped(self) -> None:
        existing: list[dict[str, Any]] = []
        kept = [{"label": "Armbar", "type": "submission", "actor": "Nobody", "ts": 50}]
        plan = plan_match_enrichment(**self._plan_kwargs(existing, kept))
        assert plan.insert_events == []
        assert plan.unresolved == ["Nobody"]

    def test_idempotent_second_run_finds_zero_inserts(self) -> None:
        existing: list[dict[str, Any]] = []
        kept = [
            {"label": "Armbar", "type": "submission", "actor": NAME_A, "ts": 50},
            {"label": "Guard Pull", "type": "transition", "actor": NAME_B, "ts": 5},
        ]
        first = plan_match_enrichment(**self._plan_kwargs(existing, kept))
        assert len(first.insert_events) == 2

        second = plan_match_enrichment(**self._plan_kwargs(first.new_sequence, kept))
        assert second.insert_events == []
        assert second.dup_count == 2
