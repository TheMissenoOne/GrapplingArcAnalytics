"""Headless screenshots of the map prototype pages (variants 15/16/17).

    uv run --with playwright python -m scripts.shoot_map_prototypes [--out DIR]

The pages are static ``file://`` HTML with a canvas renderer, so a browser is the only way to
see what they actually draw — every layout number this lote reports was measured in Python, and
this is the pass that checks the numbers describe the picture.

ponytail: no playwright in the project's own dependency graph. It is declared under the ``video``
extra, whose ``manimgl``/``moderngl`` chain does not build on this machine, so the runner asks
for it with ``uv run --with playwright`` and points at the Chromium already in
``~/.cache/ms-playwright`` rather than downloading a second one. Nothing in the test suite
imports this module; it is an operator tool.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "map_prototypes"
_CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1217/chrome-linux64/chrome"

#: (page, file stem, viewport, list of pill labels to click before shooting). A pill is found by
#: its exact text inside its own section, which is how a control-driven page is scripted without
#: giving every pill an id it does not otherwise need.
_SHOTS: list[tuple[str, str, tuple[int, int], list[tuple[str, str]]]] = [
    ("15-orcamento.html", "15-dono-top60", (1280, 800), []),
    ("15-orcamento.html", "15-dono-top10-dobrado", (1280, 800), [("budgets", "top 10")]),
    ("15-orcamento.html", "15-dono-top10-phone", (390, 840), [("budgets", "top 10")]),
    ("15-orcamento.html", "15-corpus-sem-limite", (1280, 800),
     [("sources", "Corpus"), ("budgets", "sem limite")]),
    ("15-orcamento.html", "15-corpus-top60", (1280, 800), [("sources", "Corpus")]),
    ("15-orcamento.html", "15-corpus-top60-phone", (390, 840), [("sources", "Corpus")]),
    ("15-orcamento.html", "15-gordon-top60", (1280, 800), [("sources", "#1 Gordon Ryan")]),
    ("16-aneis.html", "16-dono-arco-fixo", (1280, 800), []),
    ("16-aneis.html", "16-dono-arco-fixo-phone", (390, 840), []),
    ("16-aneis.html", "16-dono-arco-livre", (1280, 800), [("modes", "Âncoras livres (centro fixo)")]),
    ("16-aneis.html", "16-gordon-arco-fixo", (1280, 800), [("sources", "#1 Gordon Ryan")]),
    ("16-aneis.html", "16-corpus-arco-fixo", (1280, 800), [("sources", "Corpus")]),
    ("17-aneis-ancoras.html", "17-dono-arco", (1280, 800), []),
    ("17-aneis-ancoras.html", "17-dono-tercos", (1280, 800),
     [("placements", "Terços (topo / esquerda / baixo)")]),
    ("17-aneis-ancoras.html", "17-dono-bipolar", (1280, 800),
     [("placements", "Bipolar (Neutro = segundo centro)")]),
    ("17-aneis-ancoras.html", "17-dono-tercos-livre", (1280, 800),
     [("placements", "Terços (topo / esquerda / baixo)"),
      ("modes", "Âncoras livres (centro fixo)")]),
    ("17-aneis-ancoras.html", "17-gordon-tercos", (1280, 800),
     [("sources", "#1 Gordon Ryan"), ("placements", "Terços (topo / esquerda / baixo)")]),
    ("17-aneis-ancoras.html", "17-dono-tercos-phone", (390, 840),
     [("placements", "Terços (topo / esquerda / baixo)")]),
    # 18/19 — Onda B3 item 1 (pluggable ring semantics, DEMO): dono/corpus/Gordon at both
    # viewports, plus the "âncoras atribuídas" toggle, mirroring 16/17's own shot density.
    ("18-aneis-tipo.html", "18-dono-fixo", (1280, 800), []),
    ("18-aneis-tipo.html", "18-dono-fixo-phone", (390, 840), []),
    ("18-aneis-tipo.html", "18-dono-atribuida", (1280, 800),
     [("modes", "Âncoras atribuídas (mesma regra)")]),
    ("18-aneis-tipo.html", "18-corpus-fixo", (1280, 800), [("sources", "Corpus")]),
    ("18-aneis-tipo.html", "18-gordon-fixo", (1280, 800), [("sources", "#1 Gordon Ryan")]),
    ("19-aneis-comunidade.html", "19-dono-fixo", (1280, 800), []),
    ("19-aneis-comunidade.html", "19-dono-fixo-phone", (390, 840), []),
    ("19-aneis-comunidade.html", "19-dono-atribuida", (1280, 800),
     [("modes", "Âncoras atribuídas (mesma regra)")]),
    ("19-aneis-comunidade.html", "19-corpus-fixo", (1280, 800), [("sources", "Corpus")]),
    ("19-aneis-comunidade.html", "19-gordon-fixo", (1280, 800), [("sources", "#1 Gordon Ryan")]),
    # 20 — owner follow-up 2026-09-02: 17's own ring, anchors join finish_distance as ordinary
    # states. No toggle (the ring rule leaves nothing left to choose) — dono/corpus/Gordon at
    # both viewports, same shot density as 18/19's fixed-mode shots.
    ("20-aneis-ancoras-distancia.html", "20-dono", (1280, 800), []),
    ("20-aneis-ancoras-distancia.html", "20-dono-phone", (390, 840), []),
    ("20-aneis-ancoras-distancia.html", "20-corpus", (1280, 800), [("sources", "Corpus")]),
    ("20-aneis-ancoras-distancia.html", "20-gordon", (1280, 800), [("sources", "#1 Gordon Ryan")]),
]


def shoot(out: Path) -> int:
    from playwright.sync_api import sync_playwright

    shots = out / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    written = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_CHROMIUM))
        for page_name, stem, (width, height), clicks in _SHOTS:
            target = out / page_name
            if not target.is_file():
                logger.warning("missing page %s — run scripts.render_map_prototypes first", page_name)
                continue
            page = browser.new_page(viewport={"width": width, "height": height},
                                     device_scale_factor=2)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(target.as_uri())
            page.wait_for_timeout(700)
            for section, label in clicks:
                page.locator(f"#{section} .pill", has_text=label).first.click()
                page.wait_for_timeout(500)
            page.wait_for_timeout(600)
            name = f"{stem}-{width}x{height}.png"
            page.screenshot(path=str(shots / name))
            if errors:  # a silent JS error is a blank canvas that looks like a design choice
                raise RuntimeError(f"{page_name}: {errors[0]}")
            logger.info("%s", name)
            written += 1
            page.close()
        browser.close()
    print(f"-> {shots} ({written} screenshots)")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    return shoot(ap.parse_args().out)


if __name__ == "__main__":
    raise SystemExit(main())
