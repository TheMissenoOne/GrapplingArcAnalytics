"""scripts/render_atlas_user.py over a tiny synthetic bundle — no real owner data.

Proves what a private local preview must get right: it actually IS a ring payload (the
Atlas frame), every point carries the sector the 3D client reads for latitude, the written
page points at the real client bundle (``./atlas.js``, not the pre-rename ``system.js``),
and — owner rule (binding) — a user-level map always carries actor colour coding: a
training-partner entry survives (two-sided, not dropped) and yields a ``fighter:'b'``
stroke, coloured distinctly from the owner's own ``'a'`` strokes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.render_atlas_user import _SITE_DIR, render

# The preview copies the real client bundle from the sibling public-site checkout; CI checks
# out this repo alone, so the test is a no-op there rather than a false red (2026-09-13).
pytestmark = pytest.mark.skipif(
    not (_SITE_DIR / "atlas.js").exists(), reason="sibling GrapplingArc/site checkout missing"
)

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
                        # training partner's own chain — must survive two-sided and colour
                        # as 'b', same convention as an opponent's on a dossier page
                        {"label": "Half Guard", "type": "guard", "actor": "partner", "assoc": ""},
                        {
                            "label": "Rear Naked Choke", "type": "submission", "actor": "partner",
                            "assoc": "", "successful": True,
                        },
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

    fighter_b_links = [link for link in payload["links"] if link.get("fighter") == "b"]
    assert fighter_b_links, "expected a partner (fighter:'b') stroke"
    assert any(link.get("fighter") == "a" for link in payload["links"])

    html = (out / "atlas.html").read_text(encoding="utf-8")
    assert "./atlas.js" in html
    assert "seus treinos" in html.lower()
    assert "Você" in html and "Parceiro" in html
    assert "a/b" not in html.lower()
    # the page's own copy (title/legend/fallback — not the imported CSS's own comments)
    # never says "corpus"
    copy = html.split("</style>", 1)[1].split("<script", 1)[0]
    assert "corpus" not in copy.lower()

    ocean_js = (out / "ocean-data.js").read_text(encoding="utf-8")
    assert "window.GA_OCEAN" in ocean_js
    assert '"layout": "ring"' in ocean_js

    atlas_js = (out / "atlas.js").read_text(encoding="utf-8")
    assert "of the ink is shared" not in atlas_js
    assert "você" in atlas_js and "parceiro" in atlas_js
