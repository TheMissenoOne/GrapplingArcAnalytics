"""Match raw match-sequence technique names to the canonical technique library.

Match transcripts (and scraped drafts) carry free-text technique labels — variants,
abbreviations, and Portuguese names ("Mata Leão", "Guarda Fechada", "RNC"). This maps
each to the **canonical English library name** so a fighter's graph nodes line up across
matches (and with the app's node library) instead of fragmenting into near-duplicates.

The library is ``analysis/data/technique_library.json`` — a slim, committed copy of the
app's node library (``GrapplingArcApp/src/data/grappling-arch.nodes.json``): each entry
has ``en`` (canonical), ``pt`` (translation) and ``variants`` (aliases). Lookup is by the
shared ``analysis.names._normalize_name`` over every name/translation/variant, plus the
existing ``NAME_ALIASES`` resolution, so it stays char-for-char with node keys elsewhere.

Pure + deterministic. ``clean_label`` leaves an unrecognised label untouched (never
guesses), so cleanup only ever *canonicalises* known techniques.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, _resolve_aliases

_LIB_PATH = Path(__file__).resolve().parent / "data" / "technique_library.json"

# "<X> Attempt / Attempted / Attempting" — an unfinished attempt is not a distinct
# technique, it's <X>. Only ~15 library entries carry an explicit "<x> attempt" variant,
# so without this the other ~50 attempt labels fragment into their own nodes.
_ATTEMPT_RE = re.compile(r"\battempt(?:s|ed|ing)?\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[str, str]]:
    """normalized term (en / pt / variant) → (canonical English name, technique type)."""
    data = json.loads(_LIB_PATH.read_text(encoding="utf-8"))
    idx: dict[str, tuple[str, str]] = {}
    for tech in data:
        en = str(tech.get("en", "")).strip()
        if not en:
            continue
        typ = _normalize_name(str(tech.get("type", "")))
        terms = [en, tech.get("pt", ""), *tech.get("variants", [])]
        for term in terms:
            key = _normalize_name(str(term))
            if key:
                idx.setdefault(key, (en, typ))  # first (alphabetical) wins on collision
    return idx


def clean_label(label: str, type_hint: str = "") -> str:
    """Canonical library name for ``label`` (or the original, stripped, if unrecognised).

    ``type_hint`` is the event's technique type (takedown/sweep/…): when it disagrees with
    the library entry's type, the match is rejected — so an over-broad variant can't turn a
    takedown ("Trip") into a sweep ("Tripod Sweep").
    """
    raw = str(label or "").strip()
    if not raw:
        return raw
    # "<X> Attempt/Attempted/Attempting" is never a distinct technique — strip the attempt
    # word and canonicalise the base <X>. Done BEFORE the library lookup so it also covers
    # the ~15 techniques that carry an explicit "<x> attempt" variant even when the event's
    # type disagrees with the base (which would otherwise leave the attempt label untouched).
    if _ATTEMPT_RE.search(raw):
        base = _ATTEMPT_RE.sub(" ", raw)
        base = re.sub(r"\s{2,}", " ", base).strip(" /-")  # tidy leftover separators
        if base and _normalize_name(base) != _normalize_name(raw):
            return clean_label(base, type_hint)
    norm = _normalize_name(raw)
    idx = _index()
    hit = idx.get(norm) or idx.get(_resolve_aliases(norm))
    if hit is None:
        return raw
    en, lib_type = hit
    hint = _normalize_name(type_hint)
    if hint and lib_type and hint != lib_type:
        return raw  # don't canonicalise across technique types
    return en


def clean_sequence(
    sequence: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Canonicalise every event ``label`` in a match sequence (non-mutating).

    Returns ``(new_sequence, n_changed)``.
    """
    out: list[dict[str, Any]] = []
    changed = 0
    for e in sequence or []:
        if isinstance(e, dict) and e.get("label"):
            cleaned = clean_label(str(e["label"]), str(e.get("type", "")))
            if cleaned != e["label"]:
                e = {**e, "label": cleaned}
                changed += 1
        out.append(e)
    return out, changed
