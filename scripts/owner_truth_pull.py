#!/usr/bin/env python
"""Pull a reference-owner's OWN hand-annotated round timeline out of ``user_sessions`` as a
benchmark-truth file for ``scripts/round_audit.py benchmark``.

    uv run python -m scripts.owner_truth_pull --owner-id <uuid> --slug <slug> [--out data/video/owner/truth]

``<slug>`` is the same string ``scripts.round_audit._slug`` derives from the local video's
filename -- this script matches it against every round's ``media[].filename``/``media[].id``
(``RoundSnapshot.media``, synced metadata-only via ``sessionSync.toMediaMeta`` -- the App never
uploads the file itself, only these pointers) under the SAME normalization, so a round the
owner annotated in the App for that same footage is found regardless of exact casing.

PRIVATE (root ``CLAUDE.md`` / this repo's ``CLAUDE.md``, "Public vs Private Data"): reads ONE
consented reference-owner's own ``user_sessions`` rows (``analysis.reference_owner``, env-only
consent list) -- refuses any ``owner_id`` that is not on that list. Purpose is evaluating the
automatic round reader for that SAME owner's product experience; nothing here may ever reach
``data/finetune``, a CV/vision dataset, the athlete corpus, an archetype centroid, an athlete's
ELO or the ``site/`` export. Output lands under ``data/video/owner/truth/`` (gitignored, see
``data/video/`` in .gitignore) -- an evaluation set only, never training data. Prints no
session text, only counts. Extending this to a second person's rounds is a new, documented
consent decision, not an extension of this one.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.reference_owner import reference_owner_ids  # noqa: E402
from db.base import db_session  # noqa: E402
from db.models import UserSession  # noqa: E402
from scripts.round_audit import _slug  # noqa: E402

logger = logging.getLogger("owner_truth_pull")

TRUTH_ROOT = REPO / "data" / "video" / "owner" / "truth"
_ENTRY_FIELDS = ("ts", "actor", "label", "type", "successful")


def round_matches_slug(rnd: dict[str, Any], slug: str) -> bool:
    """True if any of this round's ``media`` items (``filename`` or ``id``) normalizes to
    ``slug`` under the same rule a local ``.mov`` filename gets in ``round_audit._slug``."""
    for m in rnd.get("media") or []:
        for key in ("filename", "id"):
            v = m.get(key)
            if v and _slug(Path(str(v))) == slug:
                return True
    return False


def round_truth_events(rnd: dict[str, Any]) -> dict[str, Any]:
    """Pure: one ``RoundSnapshot`` dict -> the ``{events, resets}`` shape both ``read.json``
    and :func:`analysis.round_benchmark.score_read` use (``docs/PROMPT_gemini_round_reading.md``).
    Entries with no ``ts`` are dropped -- unplaceable on the video timeline. ``resets`` marks
    every ``sequenceId`` boundary (the annotator's own invariant: entries sharing a
    ``sequenceId`` are one chain, sorted ascending by ``ts`` within it) as the midpoint between
    the two neighbouring timestamps, the same value :func:`analysis.round_analysis.build_sequences`
    would split on."""
    entries = sorted(
        (e for e in (rnd.get("entries") or []) if e.get("ts") is not None),
        key=lambda e: float(e["ts"]),
    )
    events = [{k: e[k] for k in _ENTRY_FIELDS if e.get(k) is not None} for e in entries]
    resets = [
        (float(prev["ts"]) + float(cur["ts"])) / 2
        for prev, cur in zip(entries, entries[1:], strict=False)
        if cur.get("sequenceId") != prev.get("sequenceId")
    ]
    return {"events": events, "resets": resets}


def find_truth_for_slug(
    sessions_data: list[dict[str, Any]], slug: str
) -> dict[str, Any] | None:
    """First round across ``sessions_data`` (already-fetched ``user_sessions.data`` documents
    for ONE owner) whose media matches ``slug``. Pure -- no DB, no filesystem."""
    for data in sessions_data:
        for rnd in data.get("rounds") or []:
            if round_matches_slug(rnd, slug):
                return round_truth_events(rnd)
    return None


def pull_owner_sessions(owner_id: str) -> list[dict[str, Any]]:
    """Every live (non-tombstone), non-null ``data`` document for ``owner_id`` -- refuses
    unless ``owner_id`` is a consented reference-owner account."""
    with db_session() as session:
        if owner_id not in reference_owner_ids(session):
            raise PermissionError(
                f"owner {owner_id} is not a consented reference-owner account "
                "(REFERENCE_OWNER_EMAILS)"
            )
        rows = session.execute(
            select(UserSession.data).where(
                UserSession.owner_id == owner_id,
                UserSession.deleted_at.is_(None),
                UserSession.data.is_not(None),
            )
        ).scalars().all()
    return [d for d in rows if d]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner-id", required=True,
                    help="profiles.id -- refused unless a consented reference-owner account")
    ap.add_argument("--slug", required=True,
                    help="round_audit slug (safe-cased local video filename) to match "
                         "against each round's media")
    ap.add_argument("--out", type=Path, default=TRUTH_ROOT)
    a = ap.parse_args()

    sessions_data = pull_owner_sessions(a.owner_id)
    truth = find_truth_for_slug(sessions_data, a.slug)
    if truth is None:
        logger.warning("%s: no matching round found across %d session(s)",
                       a.slug, len(sessions_data))
        return 1

    a.out.mkdir(parents=True, exist_ok=True)
    out_path = a.out / f"{a.slug}.json"
    out_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    logger.info("%s: %d event(s), %d reset(s) -> %s",
               a.slug, len(truth["events"]), len(truth["resets"]), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
