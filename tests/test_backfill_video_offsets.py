"""backfill_video_offsets: an overlay merged into url_mapping.json can carry a non-dict
top-level value (e.g. a ``_comment`` list) — ``_retirement_report`` must skip it, not crash."""

from __future__ import annotations

import scripts.dump_import as dump_import
from scripts.backfill_video_offsets import _retirement_report

_MIXED_MAPPING = {
    "_comment": ["overlay note line one", "line two"],
    "EVT": {
        "video_url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
        "matches": [
            {"athlete": "Gordon Ryan", "opponent": "Felipe Pena", "year": 2022},
        ],
    },
}


def test_retirement_report_skips_non_dict_mapping_values(monkeypatch) -> None:
    monkeypatch.setattr(dump_import, "_load_url_mapping", lambda: _MIXED_MAPPING)
    _retirement_report(declared={})  # must not raise AttributeError on the list value
