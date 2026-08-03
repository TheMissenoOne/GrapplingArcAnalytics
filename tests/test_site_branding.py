"""Brand-asset contract for HTML emitted by the static-site exporter."""

from __future__ import annotations

import sys

import pytest
from bs4 import BeautifulSoup

from export import site_data

LEGACY_LOCKUP = (
    '<a class="brand" href="index.html"><span class="mark">GA</span>'
    'Grappling<span class="o">Arc</span></a>'
)
PREVIOUS_LOCKUP = (
    '<a class="brand" href="index.html" aria-label="GrapplingArc">'
    '<img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/>'
    '<span class="brand-wordmark">GrapplingArc</span></a>'
)
NEW_LOCKUP = (
    '<a class="brand" href="index.html" aria-label="GrapplingArc">'
    '<img class="brand-symbol" src="brand-symbol.svg" alt="" aria-hidden="true"/>'
    '<span class="brand-wordmark">Grappling'
    '<span class="brand-wordmark-accent">Arc</span></span></a>'
)
LEGACY_FAVICON = '<link rel="icon" type="image/svg+xml" href="logo.svg"/>'
NEW_FAVICON = '<link rel="icon" type="image/svg+xml" href="brand-mark.svg"/>'


def _legacy_page(image: str, *, dossier: bool = False) -> str:
    orb = '  <div class="orb">GA</div>\n' if dossier else ""
    return (
        "<!doctype html>\n"
        f'<meta property="og:image" content="{image}"/>\n'
        f'<meta name="twitter:image" content="{image}"/>\n'
        f"{LEGACY_FAVICON}\n"
        f"<header>{LEGACY_LOCKUP}</header>\n"
        '<script>window.UNRELATED = "byte-for-byte";</script>\n'
        f"<footer>{LEGACY_LOCKUP}</footer>\n"
        f"{orb}</html>\n"
    )


def test_generated_nav_and_footer_use_a_single_accessible_wordmark() -> None:
    for chrome in (site_data._nav("home"), site_data._FOOTER):
        soup = BeautifulSoup(chrome, "html.parser")
        brand = soup.select_one("a.brand")
        assert brand is not None
        assert brand.get("aria-label") == "GrapplingArc"

        symbol = brand.select_one('img[src="brand-symbol.svg"]')
        assert symbol is not None
        assert symbol.get("alt") == ""
        assert symbol.get("aria-hidden") == "true"

        wordmarks = brand.select(".brand-wordmark")
        assert len(wordmarks) == 1
        assert wordmarks[0].get_text(strip=True) == "GrapplingArc"
        accents = wordmarks[0].select(".brand-wordmark-accent")
        assert len(accents) == 1
        assert accents[0].get_text(strip=True) == "Arc"
        assert not any(text.strip() == "GA" for text in soup.stripped_strings)


def test_default_head_uses_raster_og_and_standalone_mark_favicon() -> None:
    head = site_data._head("Home")

    image = "https://themissenoone.github.io/GrapplingArc/site/brand-og.png"
    assert f'og:image" content="{image}"' in head
    assert f'twitter:image" content="{image}"' in head
    assert '<link rel="icon" type="image/svg+xml" href="brand-mark.svg"/>' in head


def test_explicit_athlete_og_image_is_preserved() -> None:
    head = site_data._head("Athlete", image="assets/fighters/athlete.jpg")

    image = "https://themissenoone.github.io/GrapplingArc/site/assets/fighters/athlete.jpg"
    assert f'og:image" content="{image}"' in head
    assert f'twitter:image" content="{image}"' in head


def test_dossier_app_strip_has_no_redundant_ga_orb(monkeypatch) -> None:
    monkeypatch.setattr(site_data, "profile_narrative", lambda _profile: [])
    page = site_data.render_profile_page({
        "fighter": {
            "name": "Test Fighter",
            "slug": "test-fighter",
            "record": {"wins": 1, "losses": 0},
            "finish_rate": 0.5,
            "elo_rank": None,
            "elo_percentile": None,
        },
        "finishing": {
            "finish_rate": 0.5,
            "submission_family": {},
            "decision_rate": 0.5,
            "record_vs_elite": {"wins": 0, "losses": 0},
        },
        "style_mix": {},
        "signature_techniques": [],
        "responses": {},
        "bouts": [],
        "_career_gv": {"nodes": [], "links": []},
    })

    assert 'class="orb"' not in page
    assert ">GA<" not in page


def test_branding_only_updates_generated_pages_without_touching_handwritten_files(tmp_path) -> None:
    default_image = "https://example.test/site/logo.svg"
    athlete_image = "https://example.test/site/assets/fighters/athlete.jpg"
    pages = {
        "breakdown-one.html": _legacy_page(default_image),
        "grapple-athlete.html": _legacy_page(athlete_image, dossier=True),
        "event-open.html": _legacy_page(default_image),
        "event-previous.html": _legacy_page(default_image).replace(
            LEGACY_LOCKUP, PREVIOUS_LOCKUP
        ),
        "the-ocean.html": _legacy_page(default_image),
    }
    for name, source in pages.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    handwritten = _legacy_page(default_image)
    (tmp_path / "grapple-like.html").write_text(handwritten, encoding="utf-8")
    (tmp_path / "index.html").write_text(handwritten, encoding="utf-8")

    counts = site_data.migrate_branding(tmp_path)

    assert counts == {"scanned": 5, "changed": 5}
    for name, source in pages.items():
        migrated = (tmp_path / name).read_text(encoding="utf-8")
        expected = source.replace(LEGACY_LOCKUP, NEW_LOCKUP).replace(
            PREVIOUS_LOCKUP, NEW_LOCKUP
        ).replace(
            LEGACY_FAVICON, NEW_FAVICON
        )
        if default_image in source:
            expected = expected.replace(
                default_image, "https://example.test/site/brand-og.png"
            )
        expected = expected.replace('  <div class="orb">GA</div>\n', "")
        assert migrated == expected
    dossier = (tmp_path / "grapple-athlete.html").read_text(encoding="utf-8")
    assert athlete_image in dossier
    assert 'class="orb"' not in dossier
    assert "https://example.test/site/brand-og.png" in (
        tmp_path / "breakdown-one.html"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "grapple-like.html").read_text(encoding="utf-8") == handwritten
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == handwritten


def test_branding_only_is_idempotent(tmp_path) -> None:
    page = tmp_path / "breakdown-one.html"
    page.write_text(_legacy_page("https://example.test/site/logo.svg"), encoding="utf-8")
    site_data.migrate_branding(tmp_path)
    after_first = page.read_bytes()

    counts = site_data.migrate_branding(tmp_path)

    assert counts == {"scanned": 1, "changed": 0}
    assert page.read_bytes() == after_first


def test_branding_only_fails_when_no_generated_pages_exist(tmp_path) -> None:
    with pytest.raises(ValueError, match="No generated detail pages"):
        site_data.migrate_branding(tmp_path)


def test_branding_only_cli_bypasses_database_export(tmp_path, monkeypatch) -> None:
    (tmp_path / "event-open.html").write_text(
        _legacy_page("https://example.test/site/logo.svg"), encoding="utf-8"
    )
    data_bundle = tmp_path / "ocean-data.js"
    sentinel = b'window.GA_OCEAN = {"sentinel":"must remain byte-identical"};\r\n'
    data_bundle.write_bytes(sentinel)
    monkeypatch.setattr(site_data, "run", lambda *_args, **_kwargs: pytest.fail("DB export called"))
    monkeypatch.setattr(
        sys, "argv", ["export.site_data", "--branding-only", "--out", str(tmp_path)]
    )

    assert site_data.main() == 0
    assert data_bundle.read_bytes() == sentinel


def test_branding_only_cli_requires_explicit_out(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["export.site_data", "--branding-only"])

    with pytest.raises(SystemExit) as exc:
        site_data.main()

    assert exc.value.code == 2


def test_og_image_falls_back_when_the_photo_is_missing() -> None:
    """An og:image pointing at a file the bundle does not ship renders a broken social
    card. Only a handful of athletes have photos, so the fallback is the common path."""
    from export.site_data import _AVAILABLE_IMAGES, _og_image

    _AVAILABLE_IMAGES.clear()
    assert _og_image("assets/fighters/nobody.jpg") == "brand-og.png"

    _AVAILABLE_IMAGES.add("assets/fighters/gordon-ryan.jpg")
    assert _og_image("assets/fighters/gordon-ryan.jpg") == "assets/fighters/gordon-ryan.jpg"
    assert _og_image("assets/fighters/nobody.jpg") == "brand-og.png"
    _AVAILABLE_IMAGES.clear()


def test_same_pair_twice_in_a_year_gets_distinct_breakdown_slugs() -> None:
    """dump_import keeps both bouts when a pair meets twice in one year (two divisions of
    one card). match_slug is (a, b, year), so without a qualifier the second page
    overwrites the first and the dossier links both bouts to whichever survived."""
    from export.match_breakdown import slugify

    taken: set[str] = set()
    made: list[str] = []
    for stage in ("Advanced Division", "Round of 16 — Intermediate Division"):
        slug = "bruno-vs-hugo-2026"
        if slug in taken:
            q = slugify(stage)
            slug = f"{slug}-{q}" if q else f"{slug}-deadbeef"
        taken.add(slug)
        made.append(slug)

    assert len(set(made)) == 2, "both bouts must get their own page"
    assert made[0] == "bruno-vs-hugo-2026", "the first keeps the canonical slug"
