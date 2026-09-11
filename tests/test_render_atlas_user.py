"""scripts/render_atlas_user.py over a tiny synthetic bundle — no real owner data.

Proves the three things a private local preview must get right: it actually IS a ring
payload (the Atlas frame), every point carries the sector the 3D client reads for latitude,
and the written page points at the real client bundle (``./atlas.js``, not the pre-rename
``system.js``).
"""
from __future__ import annotations

from pathlib import Path

from scripts.render_atlas_user import render

_BUNDLE = {
    "user": {"auth": {"fullName": "Test Owner"}},
    "sessions": [
        {
            "id": "s1",
            "rounds": [
                {
                    "id": "r1",
                    "entries": [
                        {"label": "Closed Guard", "type": "guard", "actor": "you", "assoc": ""},
                        {
                            "label": "Triangle Choke", "type": "submission", "actor": "you",
                            "assoc": "", "successful": True,
                        },
                        # training partner's own action — dropped, same as an opponent's on
                        # a dossier page (never renders as the owner's own chain)
                        {"label": "Knee Cut", "type": "pass", "actor": "partner", "assoc": ""},
                    ],
                },
            ],
        },
    ],
}


def test_render_atlas_user(tmp_path: Path) -> None:
    out = tmp_path / "atlas-user"
    payload = render(_BUNDLE, out)

    assert payload["layout"] == "ring"
    assert payload["nodes"], "expected at least one state/anchor node"
    for node in payload["nodes"]:
        assert "sector" in node, node

    html = (out / "atlas.html").read_text(encoding="utf-8")
    assert "./atlas.js" in html
    assert "seus treinos" in html.lower()
    # the page's own copy (title/legend/fallback — not the imported CSS's own comments)
    # never says "corpus"
    copy = html.split("</style>", 1)[1].split("<script", 1)[0]
    assert "corpus" not in copy.lower()

    ocean_js = (out / "ocean-data.js").read_text(encoding="utf-8")
    assert "window.GA_OCEAN" in ocean_js
    assert '"layout": "ring"' in ocean_js
