"""`export/app_node_scores.py` — mapping, filters, percentile, determinism, omit-not-fabricate.

Synthetic throughout: no DB. `rrb_by_state`'s own gating math is `analysis.lamas_chain.rrb`'s
job and is pinned in `tests/test_lamas_chain.py`; here we only check that this module reads
that shape correctly (gated rows in, ungated/None rows out).
"""
from __future__ import annotations

from typing import Any, NamedTuple

import pytest

from export.app_node_scores import (
    build_scores,
    elo_baseline,
    lamas_code_for_node,
    percentile_rank,
    rated_athlete_rows,
    rrb_by_state,
)


class _Node(NamedTuple):
    node_key: str
    node_type: str
    computed_elo: float | None


# ── rrb: type/label mapping ──────────────────────────────────────────────────────
def test_lamas_code_for_node_type_first() -> None:
    node = {"type": "submission", "name": "Heel Hook"}
    assert lamas_code_for_node(node) == "SUBA"


def test_lamas_code_for_node_label_level_prefers_english_translation() -> None:
    node = {"type": "control", "name": "Pegada de Costas",
            "translations": {"en": "Back Take", "pt": "Pegada de Costas"}}
    assert lamas_code_for_node(node) == "BTKA"


def test_lamas_code_for_node_falls_back_to_name_without_english() -> None:
    node = {"type": "guard", "name": "Guard Pull"}
    assert lamas_code_for_node(node) == "PGD"


def test_lamas_code_for_node_no_mapping() -> None:
    node = {"type": "concept", "name": "Pressure"}
    assert lamas_code_for_node(node) is None


# ── rrb: reading rrb()'s row shape ───────────────────────────────────────────────
def _rows(*specs: tuple[str, bool, float | None]) -> dict[str, Any]:
    return {"rows": [{"state": s, "gated": g, "sub_share": v} for s, g, v in specs]}


def test_rrb_by_state_keeps_only_gated_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "export.app_node_scores.rrb",
        lambda chains, n_boot=0: _rows(
            ("SUBA", True, 0.62), ("TKDA", False, 0.10), ("GPSA", True, None),
        ),
    )
    monkeypatch.setattr("export.app_node_scores.chain_of", lambda b: b)
    assert rrb_by_state([{"id": "x"}]) == {"SUBA": 0.62}


# ── eloPercentile: filters ───────────────────────────────────────────────────────
def test_rated_athlete_rows_drops_thin_graphs() -> None:
    thin = [_Node("armbar", "submission", 1500.0)]  # 1 < MIN_GRAPH_NODES
    graphs = [("g1", thin)]

    rows = rated_athlete_rows(graphs, rated={"g1"})

    assert rows == []


def test_rated_athlete_rows_keeps_only_rated_graph_ids() -> None:
    a = [_Node("armbar", "submission", n) for n in (1400.0, 1500.0, 1600.0)]
    b = [_Node("triangle choke", "submission", n) for n in (1000.0, 1100.0, 1200.0)]
    graphs = [("g-rated", a), ("g-unrated", b)]

    rows = rated_athlete_rows(graphs, rated={"g-rated"})

    assert [gid for gid, _ in rows] == ["g-rated"]


def test_elo_baseline_scopes_to_athlete_owner_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user graph never reaches the baseline — the population query itself is
    `owner_kind='athlete'`-scoped, asserted at the call boundary."""
    calls: dict[str, Any] = {}

    def fake_graphs_for_clustering(session: Any, owner_kind: str | None = None) -> list[Any]:
        calls["owner_kind"] = owner_kind
        return [("g1", [_Node("armbar", "submission", n) for n in (1400.0, 1500.0, 1600.0)])]

    def fake_rated_athlete_graph_ids(session: Any, run_id: str | None) -> set[str]:
        return {"g1"}

    monkeypatch.setattr("db.repository.graphs_for_clustering", fake_graphs_for_clustering)
    monkeypatch.setattr("db.repository.rated_athlete_graph_ids", fake_rated_athlete_graph_ids)

    by_key, sorted_means = elo_baseline(session=object())

    assert calls["owner_kind"] == "athlete"
    assert by_key["armbar"][0] == 1500.0
    assert sorted_means == [1500.0]


# ── percentile ────────────────────────────────────────────────────────────────────
def test_percentile_rank_is_strictly_below_fraction() -> None:
    means = sorted([1000.0, 1200.0, 1400.0, 1600.0, 1800.0])
    assert percentile_rank(means, 1000.0) == 0     # nothing below the minimum
    assert percentile_rank(means, 1800.0) == 80     # 4 of 5 strictly below the max
    assert percentile_rank([], 1000.0) == 0


# ── build_scores: omit-not-fabricate + collisions + determinism ─────────────────
def _lib_node(name: str, node_type: str, translations: dict[str, str] | None = None,
              variations: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "type": node_type, "translations": translations or {},
            "variations": variations or []}


def test_build_scores_omits_rrb_when_no_mapping_or_ungated() -> None:
    nodes = [_lib_node("Pressure", "concept")]
    scores, _ = build_scores(nodes, rrb_states={}, by_key={}, sorted_means=[])
    assert "pressure" not in scores  # no rrb AND no eloPercentile → entry dropped entirely


def test_build_scores_omits_elo_percentile_without_corpus_presence() -> None:
    nodes = [_lib_node("Heel Hook", "submission")]
    scores, _ = build_scores(
        nodes, rrb_states={"SUBA": 0.55}, by_key={}, sorted_means=[]
    )
    assert scores["heel hook"] == {"rrb": 0.55}
    assert "eloPercentile" not in scores["heel hook"]


def test_build_scores_never_fabricates_a_present_but_wrong_field() -> None:
    nodes = [_lib_node("Twister", "submission")]
    by_key = {"twister": (1500.0, 50.0, 4)}
    scores, _ = build_scores(
        nodes, rrb_states={}, by_key=by_key, sorted_means=[1200.0, 1500.0, 1800.0]
    )
    assert scores["twister"] == {"eloPercentile": 33.0}
    assert "rrb" not in scores["twister"]


def test_build_scores_keys_every_name_variant() -> None:
    nodes = [_lib_node(
        "Twister", "submission",
        translations={"pt": "Twister", "en": "Twister"},
        variations=["twister lock", "espinha torcida"],
    )]
    scores, _ = build_scores(
        nodes, rrb_states={"SUBA": 0.4}, by_key={}, sorted_means=[]
    )
    # type wins for "submission" regardless of label, so every variant gets the same entry.
    for key in ("twister", "twister lock", "espinha torcida"):
        assert scores[key] == {"rrb": 0.4}


def test_build_scores_reports_key_collisions_and_keeps_first() -> None:
    nodes = [
        _lib_node("Heel Hook", "submission"),
        _lib_node("Heel hook", "submission"),  # normalizes to the same key
    ]
    scores, collisions = build_scores(
        nodes, rrb_states={"SUBA": 0.7}, by_key={}, sorted_means=[]
    )
    assert scores["heel hook"] == {"rrb": 0.7}
    assert collisions["heel hook"] == ["Heel hook"]


def test_build_scores_collision_surfaces_even_when_first_owner_is_empty() -> None:
    """Regression: a technique whose OWN entry is empty (e.g. `concept` type, no corpus
    match) must still occupy the key it claims first — a later technique sharing that key
    is a real collision and must be reported, not silently dropped because nobody "won"."""
    nodes = [
        _lib_node("Passagem de Pressão", "pass", variations=["pressao"]),  # -> "pressao" key
        _lib_node("Pressao", "concept"),  # same normalized key, no rrb/eloPercentile of its own
    ]
    scores, collisions = build_scores(
        nodes, rrb_states={"GPSA": 0.5}, by_key={}, sorted_means=[]
    )
    assert scores["pressao"] == {"rrb": 0.5}
    assert collisions["pressao"] == ["Pressao"]


def test_build_scores_is_deterministic() -> None:
    nodes = [_lib_node("Armbar", "submission"), _lib_node("Guard Pass", "pass")]
    rrb_states = {"SUBA": 0.6, "GPSA": 0.4}
    first = build_scores(nodes, rrb_states=rrb_states, by_key={}, sorted_means=[])
    second = build_scores(nodes, rrb_states=rrb_states, by_key={}, sorted_means=[])
    assert first == second
