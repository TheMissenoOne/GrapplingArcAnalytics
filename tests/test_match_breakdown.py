"""Tests for the public-site match-breakdown exporter (pure builders, no DB)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from export.match_breakdown import (
    _method,
    build_match_breakdown,
    match_slug,
    slugify,
)


def _athlete(aid: str, name: str, elo: float = 800.0,
             series: list[float] | None = None) -> Any:
    return SimpleNamespace(
        id=aid, name=name, nickname=None, team=None, weight_class="185",
        elo=elo, elo_series=series or [],
    )


def _match(seq: list[dict[str, Any]], winner_id: str | None) -> Any:
    return SimpleNamespace(
        sequence=seq, winner_id=winner_id, year=2025, event="UFC 319",
        weight_class="185", win_type="DECISION", submission=None, video_url=None,
    )


# Dricus (A) vs Khamzat (B): two Khamzat takedowns (one repeated label), a mount,
# and one Dricus sweep — exercises side mapping, tallies, repeated-node usage, edges.
SEQ: list[dict[str, Any]] = [
    {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "B", "successful": True},
    {"label": "Mount", "type": "control", "actor_id": "B"},
    {"label": "Sweep / Reversal", "type": "sweep", "actor_id": "A", "successful": True},
    {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "B", "successful": True},
]


def _build() -> dict[str, Any]:
    a = _athlete("A", "Dricus du Plessis", elo=794.6, series=[800.0, 794.6])
    b = _athlete("B", "Khamzat Chimaev", elo=811.0, series=[800.0, 811.0, 811.0])
    return build_match_breakdown(_match(SEQ, "B"), a, b)


class TestSlugs:
    def test_slugify_strips_punct(self) -> None:
        assert slugify("Georges St-Pierre") == "georges-stpierre"

    def test_match_slug(self) -> None:
        a = _athlete("A", "Dricus du Plessis")
        b = _athlete("B", "Khamzat Chimaev")
        assert match_slug(a, b, 2025) == "dricus-du-plessis-vs-khamzat-chimaev-2025"

    def test_match_slug_tbd_year(self) -> None:
        a, b = _athlete("A", "A B"), _athlete("B", "C D")
        assert match_slug(a, b, None).endswith("-tbd")


class TestMethod:
    def test_submission(self) -> None:
        assert _method("SUBMISSION", "Rear Naked Choke") == "Submission (Rear Naked Choke)"

    def test_decision(self) -> None:
        assert _method("DECISION", None) == "Decision"

    def test_none(self) -> None:
        assert _method(None, None) == "No contest / draw"


class TestSequenceView:
    def test_sides_and_names(self) -> None:
        bd = _build()
        seq = bd["sequence"]
        assert [e["side"] for e in seq] == ["b", "b", "a", "b"]
        assert seq[0]["name"] == "Khamzat Chimaev"
        assert seq[2]["name"] == "Dricus du Plessis"
        assert seq[0]["successful"] is True
        assert "successful" not in seq[1]  # control events carry no success flag


class TestStats:
    def test_tallies(self) -> None:
        s = _build()["stats"]
        assert s["b"]["takedowns_landed"] == 2
        assert s["b"]["takedowns_attempted"] == 2
        assert s["b"]["controls"] == 1
        assert s["a"]["sweeps"] == 1

    def test_momentum_favors_aggressor(self) -> None:
        s = _build()["stats"]
        assert s["momentum"]["b"] > s["momentum"]["a"]
        assert abs(s["momentum"]["a"] + s["momentum"]["b"] - 1.0) < 1e-9

    def test_transitions_and_conversion(self) -> None:
        s = _build()["stats"]
        # Khamzat: 2 takedowns + a mount = 3 events; Dricus: lone sweep.
        assert s["b"]["transitions"] == 3
        assert s["a"]["transitions"] == 1
        # Khamzat's first takedown converts to mount (1 of 2 entries); Dricus' sweep never.
        assert s["b"]["positional_conversion"] == 0.5
        assert s["a"]["positional_conversion"] == 0.0

    def test_momentum_series_tracks_each_event(self) -> None:
        s = _build()["stats"]
        series = s["momentum_series"]
        assert len(series) == 4  # one running point per event
        assert all(0.0 <= v <= 1.0 for v in series)


class TestTransitionGraph:
    def test_node_usage_and_edges(self) -> None:
        g = _build()["transition_graph"]
        nodes = {n["id"]: n for n in g["nodes"]}
        assert set(nodes) == {"double leg takedown", "mount", "sweep reversal"}
        assert nodes["double leg takedown"]["data"]["usageCount"] == 2
        # ONE unified timeline: takedown→mount (b), mount→sweep (a, Dricus takes over),
        # sweep→takedown (b, Khamzat re-takes). Edge colour = the grappler who moved.
        triples = {(e["source"], e["target"], e["data"]["side"]) for e in g["edges"]}
        assert triples == {
            ("double leg takedown", "mount", "b"),
            ("mount", "sweep reversal", "a"),
            ("sweep reversal", "double leg takedown", "b"),
        }


class TestTimestamps:
    """ts (absolute video seconds) flows storage → sequence view → graph nodes (video seek)."""

    SEQ_TS: list[dict[str, Any]] = [
        {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "B",
         "successful": True, "ts": 4115},
        {"label": "Mount", "type": "control", "actor_id": "B", "ts": 4200},
        {"label": "Mount", "type": "control", "actor_id": "B", "ts": 4300},
        {"label": "Sweep / Reversal", "type": "sweep", "actor_id": "A", "successful": True},
    ]

    def _build(self) -> dict[str, Any]:
        a = _athlete("A", "Dricus du Plessis")
        b = _athlete("B", "Khamzat Chimaev")
        return build_match_breakdown(_match(self.SEQ_TS, "B"), a, b)

    def test_sequence_rows_carry_ts(self) -> None:
        seq = self._build()["sequence"]
        assert [e.get("ts") for e in seq] == [4115, 4200, 4300, None]

    def test_graph_node_gets_first_seen_ts(self) -> None:
        nodes = {n["id"]: n for n in self._build()["transition_graph"]["nodes"]}
        assert nodes["double leg takedown"]["data"]["ts"] == 4115
        assert nodes["mount"]["data"]["ts"] == 4200  # first occurrence, not the repeat
        assert "ts" not in nodes["sweep reversal"]["data"]  # untimed event stays untimed

    def test_timestamp_parse_round_trips(self) -> None:
        from scripts.insert_ufc_matches import _parse_timestamp

        assert _parse_timestamp("1:08:35") == 4115
        assert _parse_timestamp("23:26") == 1406
        assert _parse_timestamp("?") is None
        assert _parse_timestamp("") is None
        for secs in (0, 59, 61, 3599, 3600, 4115, 13199):
            h, r = divmod(secs, 3600)
            mi, s = divmod(r, 60)
            text = f"{h}:{mi:02d}:{s:02d}" if h else f"{mi}:{s:02d}"
            assert _parse_timestamp(text) == secs

    def test_clean_events_keeps_ts(self) -> None:
        from scripts.insert_ufc_matches import _clean_events

        events = [
            {"label": "Mount", "type": "control", "actor": "Khabib Nurmagomedov",
             "timestamp": "1:08:35"},
            {"label": "Armbar", "type": "submission", "actor": "Khabib Nurmagomedov",
             "successful": True},  # no timestamp → no ts key
        ]
        out = _clean_events("Khabib Nurmagomedov", "Justin Gaethje", events)
        assert out[0]["ts"] == 4115
        assert "ts" not in out[1]


class TestMeta:
    def test_winner_and_fighters(self) -> None:
        bd = _build()
        assert bd["meta"]["slug"] == "dricus-du-plessis-vs-khamzat-chimaev-2025"
        assert bd["meta"]["winner"] == {"side": "b", "name": "Khamzat Chimaev"}
        assert bd["fighters"]["a"]["graph_elo"] == 794.6
        assert bd["fighters"]["b"]["elo_series"] == [800.0, 811.0, 811.0]
        assert bd["fighters"]["b"]["career_graph_ref"] == "fighters/khamzat-chimaev.json"

    def test_elo_delta(self) -> None:
        bd = _build()
        # last two snapshots: Dricus 794.6−800.0 = −5.4; Khamzat 811.0−811.0 = 0.0.
        assert bd["fighters"]["a"]["elo_delta"] == -5.4
        assert bd["fighters"]["b"]["elo_delta"] == 0.0

    def test_graph_nodes_tagged_by_owning_side(self) -> None:
        nodes = {n["id"]: n for n in _build()["transition_graph"]["nodes"]}
        assert nodes["double leg takedown"]["data"]["side"] == "b"
        assert nodes["sweep reversal"]["data"]["side"] == "a"
