"""url_mapping → per-bout video URL index + the site-side URL parser (video-seek contract)."""

from __future__ import annotations

from types import SimpleNamespace

import scripts.dump_import as dump_import
from export.site_data import _SLUG_BY_MATCH, _node_video_refs, _video_ref

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


class TestDumpBoutStartS:
    def test_prefers_int_bout_start_s(self) -> None:
        assert dump_import._dump_bout_start_s({"bout_start_s": 90, "start": "0:05"}) == 90

    def test_falls_back_to_raw_start_string(self) -> None:
        assert dump_import._dump_bout_start_s({"start": "1:30"}) == 90

    def test_none_when_neither_present(self) -> None:
        assert dump_import._dump_bout_start_s({}) is None
        assert dump_import._dump_bout_start_s({"start": ""}) is None


class TestResolveVideo:
    """Precedence rules for scripts.dump_import._resolve_video (Q5 video plumbing)."""

    def test_existing_nonnull_url_is_never_overwritten(self) -> None:
        # Simulates a hand fix applied via scripts/apply_video_fixes.py surviving a reimport.
        url, seconds, origin = dump_import._resolve_video(
            old_url="https://youtu.be/HANDFIXED", old_seconds=None, old_ts_origin=None,
            mapped_url="https://youtu.be/MAPPED&t=10s", explicit_seconds=None,
            explicit_ts_origin=None, dump_bout_start_s=999,
        )
        assert url == "https://youtu.be/HANDFIXED"
        # seconds still fills from the dump even though the URL is protected
        assert seconds == 999 and origin == "video_absolute"

    def test_null_url_filled_from_mapping_with_dump_offset_folded_in(self) -> None:
        url, seconds, origin = dump_import._resolve_video(
            old_url=None, old_seconds=None, old_ts_origin=None,
            mapped_url="https://youtu.be/ABC", explicit_seconds=None,
            explicit_ts_origin=None, dump_bout_start_s=120,
        )
        assert url == "https://youtu.be/ABC?t=120s"
        assert seconds == 120 and origin == "video_absolute"

    def test_existing_nonnull_seconds_not_overwritten_by_dump(self) -> None:
        url, seconds, origin = dump_import._resolve_video(
            old_url=None, old_seconds=42, old_ts_origin="bout_relative",
            mapped_url="https://youtu.be/ABC", explicit_seconds=None,
            explicit_ts_origin=None, dump_bout_start_s=120,
        )
        assert seconds == 42 and origin == "bout_relative"
        assert url == "https://youtu.be/ABC?t=42s"

    def test_explicit_frame_pdf_seconds_always_win(self) -> None:
        # frame-pdf's own video_start_seconds/ts_origin is the declared source: it wins even
        # over an existing DB value and even over the dump's own bout_start_s.
        url, seconds, origin = dump_import._resolve_video(
            old_url=None, old_seconds=42, old_ts_origin="bout_relative",
            mapped_url=None, explicit_seconds=7, explicit_ts_origin="video_absolute",
            dump_bout_start_s=120,
        )
        assert seconds == 7 and origin == "video_absolute"

    def test_no_offset_anywhere_leaves_seconds_none(self) -> None:
        url, seconds, origin = dump_import._resolve_video(
            old_url=None, old_seconds=None, old_ts_origin=None,
            mapped_url="https://youtu.be/ABC", explicit_seconds=None,
            explicit_ts_origin=None, dump_bout_start_s=None,
        )
        assert url == "https://youtu.be/ABC"
        assert seconds is None and origin is None

    def test_mapped_url_own_t_param_used_when_dump_has_no_offset(self) -> None:
        url, seconds, origin = dump_import._resolve_video(
            old_url=None, old_seconds=None, old_ts_origin=None,
            mapped_url="https://youtu.be/ABC&t=55s", explicit_seconds=None,
            explicit_ts_origin=None, dump_bout_start_s=None,
        )
        assert seconds == 55 and origin == "video_absolute"
        assert url == "https://youtu.be/ABC?t=55s"


class TestNodeVideoRefs:
    def test_first_timestamped_use_per_node(self) -> None:
        a = SimpleNamespace(id="A", name="Gordon Ryan")
        b = SimpleNamespace(id="B", name="Felipe Pena")
        session = SimpleNamespace(get=lambda _cls, aid: a if aid == "A" else b)
        m = SimpleNamespace(
            id="m1", athlete_a_id="A", athlete_b_id="B", year=2022,
            video_url="https://www.youtube.com/watch?v=AAAAAAAAAAA&t=100s",
            sequence=[
                {"label": "Back Take", "type": "control", "actor_id": "A", "ts": 120},
                {"label": "Back Take", "type": "control", "actor_id": "A", "ts": 300},
                {"label": "Mount", "type": "control", "actor_id": "B", "ts": 200},  # not A's
                {"label": "Armbar", "type": "submission", "actor_id": "A"},  # no ts
            ],
        )
        # Wave 8: slug now routes through _SLUG_BY_MATCH/_bout_href (the published-page
        # authority), not a bare match_slug() recompute — populate it like build_breakdowns
        # would for a bout that got a page.
        _SLUG_BY_MATCH.clear()
        _SLUG_BY_MATCH["m1"] = "gordon-ryan-vs-felipe-pena-2022"
        try:
            refs = _node_video_refs("A", [m], session)
        finally:
            _SLUG_BY_MATCH.clear()
        assert refs == {"back take": {
            "vid": "AAAAAAAAAAA", "ts": 120, "slug": "gordon-ryan-vs-felipe-pena-2022"}}
