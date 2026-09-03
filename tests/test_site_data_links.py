"""Tests for the public-site graph-link direction/dash helpers (pure, no DB), plus atlas.html
nav/page/redirect contract ("Atlas", 2026-09-03 — replaces the-ocean.html AND its own
short-lived former name the-system.html, both now redirects)."""

from __future__ import annotations

from typing import Any

from analysis.network_metrics import network_from_sequences
from export.site_data import (
    _direct_career_links,
    _nav,
    _render_retired_page,
    _to_graphview,
    render_atlas_page,
)


def _e(label: str, typ: str, actor: str, ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def test_to_graphview_breakdown_links_are_always_a_plain_arrow() -> None:
    app_graph = {
        "nodes": [{"id": "a", "data": {"type": "guard"}}, {"id": "b", "data": {"type": "pass"}}],
        "edges": [{"source": "a", "target": "b", "data": {"count": 1}}],
    }
    gv = _to_graphview(app_graph)
    assert gv["links"][0]["arrow"] is True and gv["links"][0]["dashed"] is False


def test_direct_career_links_collapses_pair_and_orients_by_net_weight() -> None:
    net = network_from_sequences([
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Back Control", "control", "A"), _e("Rear Naked Choke", "submission", "A", True)],
        [_e("Rear Naked Choke", "submission", "A", True), _e("Back Control", "control", "A")],
    ])
    links = [
        {"from": "back control", "to": "rear naked choke", "fighter": "a", "weight": 1},
        {"from": "rear naked choke", "to": "back control", "fighter": "a", "weight": 1},
    ]
    node_type = {"back control": "control", "rear naked choke": "submission"}
    out = _direct_career_links(links, node_type, net)
    assert len(out) == 1  # reciprocal pair collapsed to one link
    lk = out[0]
    assert lk["from"] == "back control" and lk["to"] == "rear naked choke"  # 3 > 1, majority wins
    assert lk["arrow"] is True  # 1 < 0.34*3 → one direction dominates
    assert lk["weight"] == 3  # real net weight, not the placeholder 1 the caller passed in


def test_direct_career_links_dashes_low_landing() -> None:
    # Fixed rule: dash iff weight >= 5, gated target type, success < 0.40.
    miss = [_e("Closed Guard", "guard", "A"), _e("Armbar", "submission", "A", False)]
    land = [_e("Closed Guard", "guard", "A"), _e("Armbar", "submission", "A", True)]
    net = network_from_sequences([miss, miss, miss, miss, land])  # 5x, 1 landing → 0.2
    links = [{"from": "closed guard", "to": "armbar", "fighter": "a", "weight": 1}]
    node_type = {"closed guard": "guard", "armbar": "submission"}
    out = _direct_career_links(links, node_type, net)
    assert out[0]["dashed"] is True  # weight 5, success 0.2 < 0.40, submission gated

    net_ok = network_from_sequences([miss, land, land])  # 3x → below weight-5 floor
    out_ok = _direct_career_links(links, node_type, net_ok)
    assert out_ok[0]["dashed"] is False  # weight 3 < 5 floor


def test_nav_and_footer_point_at_atlas_not_the_system_or_the_ocean() -> None:
    nav = _nav("atlas")
    assert 'href="atlas.html"' in nav and 'class="on"' in nav
    assert "Atlas" in nav
    assert "the-ocean.html" not in nav  # old link fully replaced, not just added-to
    assert "the-system.html" not in nav  # its own short-lived former name, also gone


def test_render_atlas_page_wires_globals_importmap_and_one_h1() -> None:
    page = render_atlas_page([
        {"kind": "state", "label": "Closed Guard"},
        {"kind": "anchor", "label": "Finish"},
        {"kind": "state", "label": ""},  # no label -> excluded from the fallback list
    ])
    assert page.count("<h1>") == 1
    assert "<h1>Atlas</h1>" in page
    assert '<html lang="en">' in page
    assert '<link rel="canonical" href="https://' in page
    assert 'atlas.html"/>' in page
    assert '<meta property="og:image" content="https://' in page
    assert 'id="system-root"' in page
    assert '"imports":{"three":"./three/three.module.min.js"' in page
    assert "mountSystem(document.getElementById('system-root')" in page
    assert 'from "./atlas.js"' in page  # site repo renamed system.js -> atlas.js
    assert "window.GA_OCEAN" in page
    assert "<li>Closed Guard</li>" in page and "<li>Finish</li>" in page


def test_render_atlas_page_meta_description_says_ring_order_not_distance() -> None:
    page = render_atlas_page()
    assert "ring order" in page
    assert "strokes it takes to get there" not in page  # the false claim the critique caught


def test_render_atlas_page_has_search_input_wired_to_a_datalist() -> None:
    page = render_atlas_page()
    assert 'id="atlasSearch"' in page and 'list="atlasStates"' in page
    assert 'class="ocean-search"' in page  # reuses the existing style, no new orphan rule
    assert '<datalist id="atlasStates">' in page


def test_render_atlas_page_documents_what_finish_means() -> None:
    page = render_atlas_page()
    assert "submission finish" in page
    assert "última posição" in page  # PT half present too (bilingual _bi span)


def test_render_atlas_page_reset_button_is_not_stretched() -> None:
    page = render_atlas_page()
    btn_start = page.index('id="systemReset"')
    btn_end = page.index(">", btn_start)
    style = page[btn_start:btn_end]
    # top+bottom both set (with height:auto) stretches the element per the CSS spec —
    # top:auto neutralises the shared .ocean-close class's top:2px.
    assert "bottom:18px" in style
    assert "top:2px" not in style


def test_render_atlas_page_fallback_lives_inside_system_root() -> None:
    page = render_atlas_page([{"kind": "state", "label": "Closed Guard"}])
    root_start = page.index('id="system-root"')
    root_open_tag_end = page.index(">", root_start)
    # the fallback markup (grapple-like link + position list) must be a DESCENDANT of
    # #system-root so CSS can hide it once JS mounts a <canvas> there; a sibling/noscript
    # block stays visible even when WebGL succeeds silently with JS still enabled.
    fallback_pos = page.index("grapple-like.html", root_open_tag_end)
    assert root_open_tag_end < fallback_pos
    # a <li> for the seeded node must appear before #system-root's matching </div>
    li_pos = page.index("<li>Closed Guard</li>")
    assert root_open_tag_end < li_pos
    assert "<noscript>" not in page  # folded into plain HTML, no longer noscript-gated
    assert "data-lang-en" in page[fallback_pos - 400:fallback_pos + 400] or \
        "data-lang-pt" in page[fallback_pos - 400:fallback_pos + 400]


def test_render_atlas_page_has_bilingual_reset_button() -> None:
    page = render_atlas_page()
    assert "data-lang-en>Reset view<" in page and "data-lang-pt>Redefinir<" in page


def test_retired_pages_point_at_atlas_with_canonical() -> None:
    for old_title, old_path in (("The System", "the-system.html"), ("The Ocean", "the-ocean.html")):
        page = _render_retired_page(old_title)
        assert '<meta http-equiv="refresh" content="0; url=atlas.html"/>' in page
        assert ('<link rel="canonical" href="https://themissenoone.github.io/GrapplingArc/site/'
                'atlas.html"/>' in page)
        assert 'href="atlas.html"' in page
        assert f"<h1>{old_title} has moved</h1>" in page
        assert page.count("<h1>") == 1
        assert old_path not in page  # the OWN retired filename never appears as a link target
