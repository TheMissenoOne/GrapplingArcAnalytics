"""LOCAL PRIVATE preview of "the Atlas" (the solar-system Path view) over ONE owner's own
App-fed training log.

Root CLAUDE.md ("Public vs Private Data" — ethics + LGPD): everything this script reads is
``owner_kind='user'`` data — a raw App bundle export, private/consent-scoped to that one
owner. The output serves ONLY that owner (their own training log rendered back to them) and
must NEVER become a corpus/dataset/site artefact: no DB write, no upload, output lands under
a gitignored ``out/`` dir. Run it, look at it on your own machine, throw it away.

Reuses, deliberately not rewritten:
- ``scripts.render_map_prototypes.partition_by_sequence``/``_resolve_group``/``_ACTOR_SIDE`` —
  the same session -> sequence-group -> library-resolved-label -> side adapter variant 13
  ("Bundle do dono") already runs over this exact bundle shape.
- ``analysis.corpus_paths.aggregate_bouts``/``path_payload(layout="ring")`` — the exact
  pipeline that already produces the public site's Atlas payload, unmodified — fed TWO-sided
  bout rows (owner ``side='a'``, training partner ``side='b'``, ``collapse_actors`` left at
  its default ``False``), the same Ocean/dossier two-sided convention. Owner rule (binding):
  a user-level map always carries actor colour coding. ``site/atlas.js`` already has the
  colour-by-fighter path built in (``hasSideDuality`` — see its own comment on
  ``traceColorOf``, added for exactly this "own vs opponent" reading) — feeding both sides is
  the whole change; no client code needed for the colouring itself.

Usage::

    uv run python -m scripts.render_atlas_user --bundle <path> --out out/atlas-user/
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path
from typing import Any

from analysis.corpus_paths import aggregate_bouts, path_payload
from analysis.taxonomy_kind import resolve_library_entry
from export.site_data import _ATLAS_STYLE, _icon
from schemas.app_types import UserBundle
from scripts.render_map_prototypes import _ACTOR_SIDE, _resolve_group, partition_by_sequence

# The site's own Atlas client bundle — vendored three.js + the module that draws it. Renamed
# the-system.html/system.js -> atlas.html/atlas.js on 2026-09-03; a caller written against the
# older name would copy nothing. i18n.js is additive here — not in the original 4-file list —
# because ocean-data.js/atlas.html omit the dual-language markup (`_bi`) this preview never
# uses, so it is not actually loaded; left out on purpose (ponytail: single-language pt-BR
# copy, no i18n needed for a one-owner local preview).
_SITE_DIR = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site"
_ASSETS = ("atlas.js", "three", "site.css", "icons.js")

# `site/atlas.js` FIG palette (own/opponent blue/orange) — copied, not imported (Python HTML
# vs. a JS ES module, same call `export/site_data.py` already makes for its own icon paths).
# Keep in sync by hand if the vendored palette ever moves.
_FIG_A, _FIG_B = "#4d86ff", "#fc4c02"  # você (you), parceiro (partner)

# `atlas.js`'s corpus-wide meta line (`metaTxt`) ends in a "% of the ink is shared" clause that
# only makes sense pooled across many athletes — replaced, in the COPY under `out/` only (never
# the vendored source in `GrapplingArc/site/`), with a "você · parceiro" stroke count computed
# from this payload's own two-sided links. ponytail: one `str.replace` on a known vendored
# line rather than templating atlas.js — if the vendored line's text ever changes this raises
# instead of silently doing nothing (see `_patch_meta_line`).
_METALINE_OLD = (
    "(metaStats.sharedActionPct != null ? ` · ${metaStats.sharedActionPct}"
    "% of the ink is shared` : '');"
)


def _metaline_new(you_strokes: int, partner_strokes: int) -> str:
    return (
        f"(metaStats.youStrokes != null ? ` · você {you_strokes} · parceiro "
        f"{partner_strokes}` : '');"
    )


def _patch_meta_line(atlas_js_path: Path, you_strokes: int, partner_strokes: int) -> None:
    text = atlas_js_path.read_text(encoding="utf-8")
    if _METALINE_OLD not in text:
        raise RuntimeError(
            "site/atlas.js's meta-line text changed — _METALINE_OLD/_patch_meta_line in "
            "render_atlas_user.py need updating to match the new vendored line."
        )
    atlas_js_path.write_text(
        text.replace(_METALINE_OLD, _metaline_new(you_strokes, partner_strokes)),
        encoding="utf-8",
    )


def _owner_bouts(bundle: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], set[str]]:
    """Bundle sessions -> ``aggregate_bouts`` bouts, TWO-sided.

    Owner rule (binding): a user-level map always carries actor colour coding — you vs.
    partner. Reuses the session/round/sequence-group partition + library-label resolution
    ``render_map_prototypes.build_aggregate`` runs (do not rewrite it), then reshapes each
    resolved group into the flat ``{label, type, side}`` row ``aggregate_bouts`` takes —
    ``'you'`` -> ``side='a'``, ``'partner'`` -> ``side='b'`` (``_ACTOR_SIDE``, same mapping
    ``build_aggregate`` uses), exactly the Ocean/dossier two-sided convention
    (``collapse_actors`` stays at its default ``False`` at the call site, so ``'a'``/``'b'``
    never merge). Also returns every raw label the App technique library could not resolve
    (report-only; classification already falls back to the raw label/type for these,
    ``_resolve_group``'s own contract).
    """
    bouts: list[list[dict[str, Any]]] = []
    unresolved: set[str] = set()
    for session in bundle.get("sessions", []):
        for round_ in session.get("rounds", []):
            entries = round_.get("entries", []) or []
            for group in partition_by_sequence(entries):
                for e in group:
                    raw_label = str(e.get("label", "") or "")
                    if raw_label and resolve_library_entry(raw_label) is None:
                        unresolved.add(raw_label)
                resolved_group, _display = _resolve_group(group)
                bout = [
                    {
                        "label": str(e.get("label", "")), "type": str(e.get("type", "")),
                        "side": side,
                        **({"successful": e["successful"]} if e.get("successful") is not None else {}),
                    }
                    for e in resolved_group
                    if (side := _ACTOR_SIDE.get(e.get("actor"))) is not None
                ]
                if bout:
                    bouts.append(bout)
    return bouts, unresolved


def _owner_first_name(bundle: dict[str, Any]) -> str:
    """Validates the bundle shape via the real App-bundle parser (private data — fail loud on
    a malformed export rather than silently rendering an empty page) and returns just the
    owner's first name for the page title."""
    parsed = UserBundle.from_json(bundle)
    full = parsed.user.full_name if parsed.user else ""
    parts = full.split()
    return parts[0] if parts else "Owner"


def _atlas_html(payload: dict[str, Any], first_name: str) -> str:
    """The site's Atlas page template (``export.site_data.render_atlas_page``), trimmed to a
    standalone local preview: no site nav/footer (they link to public pages that don't exist
    in ``out/``, and pull remote fonts this preview doesn't need), title + legend copy say
    "seus treinos" (never "corpus"), ``noindex`` meta. ``_ATLAS_STYLE``/``_icon`` are the SAME
    CSS/markup pieces the real page uses, imported not copied."""
    title = f"Atlas — {first_name}"
    fallback_items = "".join(
        f"<li>{html.escape(str(n.get('label') or ''))}</li>"
        for n in sorted(
            (
                n for n in (payload.get("nodes") or [])
                if n.get("kind") in ("state", "anchor") and n.get("label")
            ),
            key=lambda n: str(n.get("label") or ""),
        )
    )
    fallback = (
        '<div class="system-fallback">'
        "<p>Este mapa 3D exige um navegador com JavaScript e WebGL. "
        f"Toda posição dos seus treinos:</p><ul>{fallback_items}</ul></div>"
    )
    body = f"""<section class="ocean-stage">
  <div id="system-root" class="ocean-canvas">
    {fallback}
  </div>
  <div class="ocean-hud">
    <div class="ocean-h">
      <h1>{html.escape(title)}</h1><p class="muted" id="systemMeta"></p>
      <label for="hudMoreToggle" class="hud-more-btn" aria-label="Buscar posição">{_icon("search")}</label>
    </div>
    <input type="checkbox" id="hudMoreToggle" class="hud-more-check" hidden/>
    <div class="hud-more">
      <input id="atlasSearch" class="ocean-search" list="atlasStates" type="search"
        placeholder="Buscar posição" aria-label="Buscar posição"/>
      <datalist id="atlasStates"></datalist>
      <p class="muted" id="atlasLegend">Prévia privada — seus treinos, nunca sai desta máquina.
        Finalização = finalização por submissão.</p>
      <p class="muted" id="atlasFighterKey">
        <span style="color:{_FIG_A}">●</span> Você
        <span style="color:{_FIG_B}">●</span> Parceiro
      </p>
    </div>
  </div>
  <aside id="oceanPanel" class="ocean-panel" hidden>
    <button id="oceanClose" class="ocean-close" aria-label="fechar">{_icon("x")}</button>
    <h2 id="opName"></h2><div id="opMeta"></div>
    <div id="opMetrics" class="op-metrics"></div>
    <div id="opNeighbours"></div><div id="opEdges"></div><div id="opUndrawn"></div>
    <button id="opDetails" type="button" class="tag">Detalhes</button>
  </aside>
  <button id="systemReset" type="button" class="ocean-close" style="position:absolute;top:auto;bottom:18px;right:18px;width:auto;height:auto;padding:8px 14px;border:1px solid var(--line);border-radius:8px;font-size:12px;pointer-events:auto">Redefinir</button>
</section>"""
    panel_js = (
        "{root:document.getElementById('oceanPanel'),name:document.getElementById('opName'),"
        "meta:document.getElementById('opMeta'),metrics:document.getElementById('opMetrics'),"
        "neighbours:document.getElementById('opNeighbours'),edges:document.getElementById('opEdges'),"
        "undrawn:document.getElementById('opUndrawn'),close:document.getElementById('oceanClose'),"
        "reset:document.getElementById('systemReset'),metaLabel:document.getElementById('systemMeta'),"
        "details:document.getElementById('opDetails')}"
    )
    head = f"""<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex,nofollow"/>
<title>{html.escape(title)} — prévia privada</title>
<link rel="stylesheet" href="site.css"/></head><body>"""
    return (
        head + _ATLAS_STYLE
        + f'<header class="site-head"><div class="wrap"><strong>{html.escape(title)}</strong>'
          " — dado privado, uso local apenas</div></header>"
        + body
        + '<script src="ocean-data.js"></script>'
          '<script type="importmap">{"imports":{"three":"./three/three.module.min.js",'
          '"three/addons/":"./three/addons/"}}</script>'
          '<script type="module">import {mountSystem} from "./atlas.js";'
          "mountSystem(document.getElementById('system-root'),(window.GA_OCEAN||{}).pathGraph,"
          "{panel:" + panel_js + "});</script></body></html>"
    )


def render(bundle: dict[str, Any], out: Path) -> dict[str, Any]:
    """Builds the payload + writes every file under ``out``. Returns the payload (report)."""
    first_name = _owner_first_name(bundle)
    bouts, unresolved = _owner_bouts(bundle)
    agg = aggregate_bouts(bouts)
    payload = path_payload(agg, layout="ring", max_variants=None, max_fold_groups=None)

    out.mkdir(parents=True, exist_ok=True)
    (out / "ocean-data.js").write_text(
        "/* PRIVATE — one owner's own training log, generated by "
        "scripts.render_atlas_user. Never commit, never publish. */\n"
        f"window.GA_OCEAN = {json.dumps({'pathGraph': payload}, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    (out / "atlas.html").write_text(_atlas_html(payload, first_name), encoding="utf-8")
    for asset in _ASSETS:
        src, dst = _SITE_DIR / asset, out / asset
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    you_strokes = sum(1 for link in payload["links"] if link.get("fighter") == "a")
    partner_strokes = sum(1 for link in payload["links"] if link.get("fighter") == "b")
    _patch_meta_line(out / "atlas.js", you_strokes, partner_strokes)

    payload["_unresolved_labels"] = sorted(unresolved)  # report-only, not part of the site contract
    payload["_you_strokes"], payload["_partner_strokes"] = you_strokes, partner_strokes
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("out/atlas-user"))
    args = ap.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    payload = render(bundle, args.out)

    unresolved = payload.pop("_unresolved_labels")
    you_strokes, partner_strokes = payload.pop("_you_strokes"), payload.pop("_partner_strokes")
    rings = sorted({n["ring"] for n in payload["nodes"] if "ring" in n})
    nodes_you = sum(1 for n in payload["nodes"] if n.get("fighter") == "a")
    nodes_partner = sum(1 for n in payload["nodes"] if n.get("fighter") == "b")
    paths_you = sum(1 for p in payload["paths"] if p.get("actor") == "a")
    paths_partner = sum(1 for p in payload["paths"] if p.get("actor") == "b")
    print(f"nodes: {len(payload['nodes'])} (você {nodes_you} · parceiro {nodes_partner})")
    print(f"paths: {len(payload['paths'])} (você {paths_you} · parceiro {paths_partner})")
    print(f"strokes: você {you_strokes} · parceiro {partner_strokes}")
    print(f"rings: {len(rings)} {rings}")
    print(
        f"unresolved labels: {len(unresolved)}" + (f" {unresolved}" if unresolved else "")
    )
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
