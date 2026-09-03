"""N2 — composite label expansion (docs/taxonomy/04_ONTOLOGIA_CANONICA.md S "N2").

Applied ONCE, at the single point every entry path funnels ``sequence`` through:
``db.repository.register_match``. `chain_compiler` never sees a composite label — it only ever
sees atomic events, exactly as before this module existed. The scar that pins that rule: an
earlier attempt to derive position INSIDE the compiler fabricated 160 phantom actions across
281 bouts (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md S3.2). Expansion belongs in ingestion.

``data/taxonomy/composite_labels.json`` is curated by hand — never regex in production, never a
heuristic here. A raw event whose label matches a curated row (case/punctuation-insensitive,
via ``analysis.names._normalize_name``) expands into 1-2 events; everything else passes through
unchanged. Three shapes:

  {"action": "<label>", "to": "<label>" | "top"|"bottom"|"neutral"}
      Action event, then a second event for ``to`` — UNLESS ``to`` is a bare orientation word,
      in which case it is dropped: the target was vague in the source ("Escape to Standing"),
      and `chain_compiler`'s existing exit-orientation anchor inference (`taxonomy_kind.
      resolve_closing_anchor`) already supplies the same generic landing spot for an action with
      no declared next state. Splicing a literal node here would just reinvent that mechanism.

  {"state": "<label>", "action": "<label>"}
      State event, then action event — the log named a position and, from it, a move
      ("Leg Entanglement / Heel Hook Entry": in leg entanglement, entering the heel hook).

  {"state": "<label>", "perspective": {"actor": "top"|"bottom"}}
      ONE event. The label is UNCHANGED — perspective is metadata, never a second name for a
      state (the contract's own line: "perspectiva é metadado estruturado, nunca um segundo
      nome de estado"). ``perspective`` is added as a new key on the SAME event.

The split-half ``type`` is a fixed generic — "transition" for an action half, "control" for a
state half — never inspected further here. Real classification runs downstream through
``analysis.taxonomy_kind.kind_of_entry``, which resolves through the technique library FIRST and
only falls back to the raw ``type``, so the exact placeholder rarely matters (see that
function's own docstring). Every generated event keeps every field of the original except
``label``/``type`` (so ``ts``/``actor``/``actor_id``/``successful``/``points`` all carry over,
duplicated onto each half when the composite splits) and gains ``source_label`` = the original
raw label, for audit trail.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name

TABLE_PATH = Path(__file__).resolve().parent.parent / "data" / "taxonomy" / "composite_labels.json"

_GENERIC_ORIENTATIONS = frozenset({"top", "bottom", "neutral"})

_table_cache: dict[str, dict[str, Any]] | None = None


def _load_table() -> dict[str, dict[str, Any]]:
    global _table_cache
    if _table_cache is None:
        if TABLE_PATH.is_file():
            raw = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
            _table_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            _table_cache = {}  # N2 table not written yet -- absent reads as empty, not error
    return _table_cache


def expand_composite(event: dict[str, Any]) -> list[dict[str, Any]]:
    """One raw ``sequence`` event in, 1+ events out. A label the curated table does not cover
    (the overwhelming majority) returns unchanged as a single-element list — every caller can
    flatten the result the same way whether or not the label was composite:

        sequence = [ev for raw in sequence for ev in expand_composite(raw)]
    """
    label = str(event.get("label") or "")
    spec = _load_table().get(_normalize_name(label))
    if spec is None:
        return [event]

    if "perspective" in spec:
        out = dict(event)
        out["perspective"] = spec["perspective"]
        out["source_label"] = label
        return [out]

    carried = {k: v for k, v in event.items() if k not in ("label", "type")}

    if "action" in spec and "to" in spec:
        first = {"label": spec["action"], "type": "transition", "source_label": label, **carried}
        to = str(spec["to"])
        if to.lower() in _GENERIC_ORIENTATIONS:
            return [first]
        second = {"label": to, "type": "control", "source_label": label, **carried}
        return [first, second]

    if "state" in spec and "action" in spec:
        first = {"label": spec["state"], "type": "control", "source_label": label, **carried}
        second = {"label": spec["action"], "type": "transition", "source_label": label, **carried}
        return [first, second]

    return [event]  # unrecognised shape -- never happens with a curated table; never crash


def expand_sequence(sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``matches.sequence`` in, expanded ``matches.sequence`` out — the one call every
    ``db.repository`` write path (``register_match``, ``register_matches_bulk``,
    ``update_match``) makes before a bout's ``sequence`` reaches storage."""
    return [expanded for raw in sequence for expanded in expand_composite(raw)]
