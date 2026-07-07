"""Disk-backed per-item cache so the site export only recomputes breakdowns / dossiers whose
inputs changed. The expensive part of an export is the per-item analysis (network metrics,
path-to-victory, style profiles); rendering the result to HTML is cheap. So we key each item
by a hash of *its own* DB inputs, cache the computed payload, and on the next run reuse it when
the hash matches — turning a routine "few bouts changed" update from ~18min into seconds.

Correctness contract:
  * the hash MUST include every DB input that affects the item's payload (caller's job);
  * payloads are round-tripped through JSON so a cache HIT and a cache MISS yield the identical
    (JSON-normalised) object → identical render;
  * a *cold* run (empty cache) must reproduce a full export byte-for-byte — that's the test.

ponytail: the corpus-wide PtV is deliberately NOT hashed per item (it drifts with any new bout,
which would invalidate everything). Momentum on untouched bouts may lag one corpus generation
until a `--full` run; the drift is sub-rounding and self-heals. Pass full=True to bypass.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def item_hash(*parts: Any) -> str:
    return hashlib.sha1(
        json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class ItemCache:
    """One JSON file of ``{key: {"h": hash, "p": payload}}`` for a single export phase."""

    def __init__(self, path: Path, full: bool = False):
        self.path = path
        self.old: dict[str, dict[str, Any]] = {}
        if not full and path.exists():
            try:
                self.old = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.old = {}
        self.new: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: str, h: str, compute: Callable[[], Any]) -> Any:
        """Return the cached payload for ``key`` if its stored hash matches ``h``, else run
        ``compute`` (invoked immediately — no deferral) and cache the JSON-normalised result."""
        rec = self.old.get(key)
        if rec is not None and rec.get("h") == h:
            self.hits += 1
            payload = rec["p"]
        else:
            self.misses += 1
            # normalise through JSON so a HIT (already JSON) and a MISS render identically
            payload = json.loads(json.dumps(compute(), default=str, ensure_ascii=False))
        self.new[key] = {"h": h, "p": payload}
        return payload

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.new, ensure_ascii=False), encoding="utf-8")
