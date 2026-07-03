"""url_mapping → per-bout video URL index + the site-side URL parser (video-seek contract)."""

from __future__ import annotations

from types import SimpleNamespace

import scripts.dump_import as dump_import
from export.site_data import _node_video_refs, _video_ref

_MAPPING = {
    "EVT": {
        "video_url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
        "matches": [
            # clean pair with a start
            {"athlete": "Gordon Ryan", "opponent": "Felipe Pena", "year": 2022,
             "winner": "Gordon Ryan", "seconds": 4571},
            # "athlete" holds the whole matchup + a stage tag; opponent redundant
            {"athlete": "Craig Jones vs Kyle Boehm QF", "opponent": "Kyle Boehm",
             "year": 2022, "winner": "Craig Jones", "seconds": None},
            # mapping quirk: opponent mirrors athlete; the other participant is "winner"
            {"athlete": "Kamal Shalorus", "opponent": "Kamal Shalorus",
             "winner": "Khabib Nurmagomedov", "year": 2012, "seconds": 25},
            # transcript-timestamp suffix on the name
            {"athlete": "Nick Rodriguez [1:52:02]", "opponent": "John Hansen",
             "year": 2022, "winner": "Nick Rodriguez", "seconds": 6722},
        ],
    },
}


def _index(monkeypatch):
    monkeypatch.setattr(dump_import, "_load_url_mapping", lambda: _MAPPING)
    return dump_import.video_index()


def _key(a: str, b: str, year: int):
    from analysis.names import athlete_key

    return (frozenset((athlete_key(a), athlete_key(b))), year)


class TestVideoIndex:
    def test_pair_with_start_gets_t_param(self, monkeypatch) -> None:
        idx = _index(monkeypatch)
        assert idx[_key("Felipe Pena", "Gordon Ryan", 2022)].endswith("&t=4571s")

    def test_vs_key_and_stage_suffix(self, monkeypatch) -> None:
        idx = _index(monkeypatch)
        assert _key("Craig Jones", "Kyle Boehm", 2022) in idx
        # no seconds → plain event url
        assert idx[_key("Craig Jones", "Kyle Boehm", 2022)].endswith("watch?v=AAAAAAAAAAA")

    def test_opponent_mirror_falls_back_to_winner(self, monkeypatch) -> None:
        idx = _index(monkeypatch)
        assert _key("Kamal Shalorus", "Khabib Nurmagomedov", 2012) in idx

    def test_timestamp_suffix_in_name(self, monkeypatch) -> None:
        idx = _index(monkeypatch)
        assert _key("Nick Rodriguez", "John Hansen", 2022) in idx


class TestVideoRef:
    def test_parses_id_and_start(self) -> None:
        assert _video_ref("https://www.youtube.com/watch?v=AAAAAAAAAAA&t=4571s") == \
            ("AAAAAAAAAAA", 4571)
        assert _video_ref("https://youtu.be/AAAAAAAAAAA") == ("AAAAAAAAAAA", 0)
        assert _video_ref(None) is None
        assert _video_ref("not a url") is None


class TestNodeVideoRefs:
    def test_first_timestamped_use_per_node(self) -> None:
        a = SimpleNamespace(id="A", name="Gordon Ryan")
        b = SimpleNamespace(id="B", name="Felipe Pena")
        session = SimpleNamespace(get=lambda _cls, aid: a if aid == "A" else b)
        m = SimpleNamespace(
            athlete_a_id="A", athlete_b_id="B", year=2022,
            video_url="https://www.youtube.com/watch?v=AAAAAAAAAAA&t=100s",
            sequence=[
                {"label": "Back Take", "type": "control", "actor_id": "A", "ts": 120},
                {"label": "Back Take", "type": "control", "actor_id": "A", "ts": 300},
                {"label": "Mount", "type": "control", "actor_id": "B", "ts": 200},  # not A's
                {"label": "Armbar", "type": "submission", "actor_id": "A"},  # no ts
            ],
        )
        refs = _node_video_refs("A", [m], session)
        assert refs == {"back take": {
            "vid": "AAAAAAAAAAA", "ts": 120, "slug": "gordon-ryan-vs-felipe-pena-2022"}}
