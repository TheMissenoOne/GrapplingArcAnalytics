"""Import an App user's offline bundle export into their Supabase account, and — optionally —
link their profile to their own row in the public athlete corpus and turn their competition
bouts into sessions the App will pull.

**Why sessions, not a graph push.** The App only PUSHES its graph to the server
(`rpc('replace_user_graph', ...)`, alembic 0037) — it never pulls one. What it DOES pull is
`user_sessions` (`sessionSync.ts:getUserSessionsSince`, filtered on `updated_at >= last_sync_at`
from `user_sync_meta`), merges them locally (`mergeSessionsByUpdatedAt`), and — whenever that
merge actually changes something — replays the WHOLE session history through
`reprocessAllSessions()` to rebuild the graph (`syncEngine.ts`). So the only durable way to get
a competition history INTO a user's on-device map is as sessions, and every row written here
needs `updated_at = now()` or the App's incremental pull cursor skips it entirely.

**Two things this script does, independently:**

1. Push every session in an offline bundle export (`schemaVersion: 2`, `sessions[]`, the shape
   ``backupBundleService``/an admin export produces) into `user_sessions`, id-for-id. The dump
   is passed through as-is (``db.repository.upsert_user_session``'s ``data`` column) — no
   reshaping, because it is already a real ``SessionState``.
2. (``--athlete``) Link ``profiles.athlete_id`` (alembic 0051) and convert every RELIABLE global
   match that athlete fought into a synthetic session, so the App's own map ends up carrying
   their competition history alongside their logged training.

**The reliability gate.** ``analysis.attribution.bout_flags`` flags a bout ``one_sided`` when
every recorded event is filed under one athlete — the other fighter's side was simply never
logged, so the App would read "you" and "your opponent" off a coin flip. A one-sided bout is
skipped, not converted with a guess; it is reported in the summary instead. No flag forces it
through — that would be a new, separate decision.

**Determinism.** Every id derived from a match (session id, entry ids) is a pure function of
``match.id``, so re-running this script against the same corpus produces the exact same rows —
safe to re-run after a fresh match is added or fixed upstream. `updated_at` at the row level is
the one exception, and it has to be: a re-run must still look "fresh" to the App's pull cursor.

Usage:
    uv run python -m scripts.import_user_bundle --profile <uuid> --bundle <path> [--athlete <uuid>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── dump sessions: dedupe ────────────────────────────────────────────────────


def load_bundle(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def dedupe_sessions_by_id(sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Collapse ``sessions[]`` to one row per ``id``, keeping the LAST occurrence (dump order —
    a later entry for the same id is the more recently edited one). Returns
    ``(deduped, ids_with_a_duplicate)`` — the second list is for the dry-run/write summary."""
    last_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    dup_ids: list[str] = []
    for s in sessions:
        sid: str = s["id"]
        if sid in last_by_id and sid not in dup_ids:
            dup_ids.append(sid)
        if sid not in last_by_id:
            order.append(sid)
        last_by_id[sid] = s
    return [last_by_id[sid] for sid in order], dup_ids


# ── match -> synthetic session ───────────────────────────────────────────────

# ponytail: fixed placeholder round/topic shape (duration/difficulty/intensity) — a competition
# bout has no real "session length" to derive one from. Upgrade if the product ever wants these
# to mean something for an imported bout specifically.
_PLACEHOLDER_ROUND = {"difficulty": 5, "intensity": 5, "durationMin": 5}


def match_session_id(match_id: str) -> str:
    """Deterministic session id — first 12 chars of the match's own uuid, so re-running this
    script imports the SAME row rather than accumulating duplicates."""
    return f"s-match-{str(match_id)[:12]}"


def _match_created_at(match: Any) -> str:
    if match.year:
        return datetime(int(match.year), 1, 1, tzinfo=UTC).isoformat()
    created_at = getattr(match, "created_at", None)
    if created_at is not None:
        return created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    return datetime(1970, 1, 1, tzinfo=UTC).isoformat()


def _match_outcome(match: Any, athlete_id: str) -> str | None:
    """RoundOutcome ('succeeded' | 'partial' | 'failed' | 'no_attempt' | null) is the App's
    real enum — there is no 'win'/'loss' value on the type. A win reads as 'succeeded', a loss
    as 'failed'; a draw or an unreviewed/undecided bout (``winner_id is None``) stays null
    rather than inventing a value the type doesn't have."""
    if match.winner_id is None:
        return None
    return "succeeded" if match.winner_id == athlete_id else "failed"


def match_to_session(match: Any, athlete_id: str, opponent_name: str) -> dict[str, Any]:
    """One global ``Match``, from ``athlete_id``'s side, as a synthetic ``SessionState``.

    Caller is responsible for the reliability gate (``bout_flags(...)["role_reliable"]``) —
    this function assumes it already passed and does not re-check it.
    """
    from db.repository import _perspective_view

    view = _perspective_view(match, athlete_id)
    sid = match_session_id(match.id)

    entries: list[dict[str, Any]] = []
    for i, ev in enumerate(view.sequence):
        entry: dict[str, Any] = {
            "id": f"{sid}-e{i}",
            "label": ev["label"],
            "assoc": "",
            "type": ev["type"],
            # _perspective_view already remaps to 'you'/'opponent'; the App's actor vocabulary
            # is 'you'/'partner' (RoundEntry.actor) — 'opponent' has no App-side meaning.
            "actor": "you" if ev["actor"] == "you" else "partner",
        }
        if "successful" in ev:
            entry["successful"] = ev["successful"]
        entries.append(entry)

    created_at = _match_created_at(match)
    round0 = {
        **_PLACEHOLDER_ROUND,
        "itemType": "control",
        "itemInput": "",
        "entries": entries,
        "position": None,
        "outcome": _match_outcome(match, athlete_id),
        "media": [],
    }

    return {
        "id": sid,
        "createdAt": created_at,
        # Fixed (== createdAt), not "now" — keeps the DATA blob itself deterministic across
        # re-runs; only the user_sessions ROW's `updated_at` column (set by the caller) needs
        # to be fresh, because that's the only field the App's pull cursor reads.
        "updatedAt": created_at,
        "showModal": False,
        "duration": 10,
        "topicType": "control",
        "topicInput": "",
        "topicAssocPos": "",
        "topics": [],
        "goal": None,
        "round": {**_PLACEHOLDER_ROUND, "itemType": "control", "itemInput": "", "entries": []},
        "rounds": [round0],
        "title": f"Competição — vs {opponent_name}",
        "reflection": "",
        "media": [],
        "videos": [],
        "projectId": None,
        "pendingGraphUpdates": [],
        # Write-only field on the App side (grepped: sessionSaveService.ts sets it, nothing
        # reads it) — set for shape-parity with a real save, not because anything gates on it.
        # Ingestion is driven entirely by the session landing in `user_sessions` with a fresh
        # `updated_at`; see the module docstring.
        "processed": True,
        # Extra key, not part of SessionState — survives untouched: the App never validates
        # unknown keys on a pulled/loaded session (`mergeSessionsByUpdatedAt`/`loadSessionsStrict`
        # both spread the object as-is). Marks provenance for anything that inspects the row later.
        "source": {"kind": "competition", "matchId": str(match.id)},
    }


def convert_athlete_matches(
    athlete_id: str, session: Any
) -> tuple[list[dict[str, Any]], list[tuple[Any, str]]]:
    """Every RELIABLE match of ``athlete_id`` converted to a synthetic session (ordered same as
    ``get_matches_for_athlete``). Returns ``(sessions, skipped)`` — ``skipped`` is
    ``(match, opponent_name)`` for every bout ``bout_flags`` marked NOT role-reliable."""
    from analysis.attribution import bout_flags
    from db.models import Athlete
    from db.repository import get_matches_for_athlete

    matches = get_matches_for_athlete(athlete_id, session)
    athlete_names: dict[str, str] = {}

    def name_of(aid: str) -> str:
        if aid not in athlete_names:
            other = session.get(Athlete, aid)
            athlete_names[aid] = (other.name if other and other.name else None) or "adversário"
        return athlete_names[aid]

    sessions: list[dict[str, Any]] = []
    skipped: list[tuple[Any, str]] = []
    for match in matches:
        other_id = match.athlete_b_id if match.athlete_a_id == athlete_id else match.athlete_a_id
        opponent = name_of(other_id)
        flags = bout_flags(match.sequence or [], match.athlete_a_id, match.athlete_b_id)
        if not flags["role_reliable"]:
            skipped.append((match, opponent))
            continue
        sessions.append(match_to_session(match, athlete_id, opponent))
    return sessions, skipped


# ── dry-run summary: unresolved labels ───────────────────────────────────────


def _labels_from_dump_sessions(sessions: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for s in sessions:
        for r in s.get("rounds") or []:
            for e in r.get("entries") or []:
                if e.get("label"):
                    labels.append(str(e["label"]))
    return labels


def _labels_from_match_sessions(sessions: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for s in sessions:
        for r in s.get("rounds") or []:
            for e in r.get("entries") or []:
                labels.append(str(e["label"]))
    return labels


def unresolved_labels(labels: list[str]) -> list[tuple[str, int]]:
    """``(label, count)`` for every label that ``data/taxonomy/library_lookup.json`` does not
    recognise, most-frequent first. Not a blocker — the App creates a custom node for an
    unknown label — purely informational for the dry-run summary."""
    from analysis.taxonomy_kind import resolve_library_entry

    counts = Counter(labels)
    unresolved = {lbl: n for lbl, n in counts.items() if resolve_library_entry(lbl) is None}
    return sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0]))


# ── CLI ───────────────────────────────────────────────────────────────────────


def _print_summary(
    *,
    dump_sessions: list[dict[str, Any]],
    dup_ids: list[str],
    match_sessions: list[dict[str, Any]],
    skipped: list[tuple[Any, str]],
    unresolved: list[tuple[str, int]],
) -> None:
    print(f"Dump sessions: {len(dump_sessions)} unique (collapsed {len(dup_ids)} duplicate id(s))")
    if dup_ids:
        print(f"  duplicate ids: {', '.join(dup_ids)}")
    if match_sessions or skipped:
        print(f"Matches converted: {len(match_sessions)}")
        for s in match_sessions:
            n = len(s["rounds"][0]["entries"])
            print(f"  {s['id']}  {s['title']}  ({n} lançamentos)")
        if skipped:
            print(f"Matches puladas (atribuição não confiável): {len(skipped)}")
            for match, opponent in skipped:
                print(f"  {match.id}  vs {opponent} ({match.year})")
    if unresolved:
        print(f"Rótulos sem match na biblioteca ({len(unresolved)}):")
        for label, n in unresolved:
            print(f"  {label!r} x{n}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", required=True, help="profiles.id (uuid) to import into")
    ap.add_argument("--bundle", required=True, type=Path, help="offline bundle export JSON")
    ap.add_argument("--athlete", help="athletes.id (uuid) to link + convert bouts from")
    ap.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    args = ap.parse_args()

    bundle = load_bundle(args.bundle)
    dump_sessions, dup_ids = dedupe_sessions_by_id(bundle.get("sessions") or [])

    match_sessions: list[dict[str, Any]] = []
    skipped: list[tuple[Any, str]] = []
    now = datetime.now(UTC)

    if args.athlete:
        from db.base import db_session

        with db_session() as db:
            match_sessions, skipped = convert_athlete_matches(args.athlete, db)
            labels = _labels_from_dump_sessions(dump_sessions) + _labels_from_match_sessions(
                match_sessions
            )
            unresolved = unresolved_labels(labels)

            if args.dry_run:
                _print_summary(
                    dump_sessions=dump_sessions, dup_ids=dup_ids,
                    match_sessions=match_sessions, skipped=skipped, unresolved=unresolved,
                )
                # Read-only path: nothing staged, let db_session's commit no-op.
                return 0

            from db.models import Profile
            from db.repository import upsert_user_session

            profile = db.get(Profile, args.profile)
            if profile is None:
                raise SystemExit(f"No profiles row for {args.profile}")
            profile.athlete_id = args.athlete

            for s in dump_sessions:
                upsert_user_session(args.profile, s["id"], s, now, db)
            for s in match_sessions:
                upsert_user_session(args.profile, s["id"], s, now, db)

            _print_summary(
                dump_sessions=dump_sessions, dup_ids=dup_ids,
                match_sessions=match_sessions, skipped=skipped, unresolved=unresolved,
            )
        return 0

    # No --athlete: the dump-sessions path needs no DB at all, dry-run or not.
    unresolved = unresolved_labels(_labels_from_dump_sessions(dump_sessions))
    if args.dry_run:
        _print_summary(
            dump_sessions=dump_sessions, dup_ids=dup_ids,
            match_sessions=match_sessions, skipped=skipped, unresolved=unresolved,
        )
        return 0

    from db.base import db_session
    from db.repository import upsert_user_session

    with db_session() as db:
        for s in dump_sessions:
            upsert_user_session(args.profile, s["id"], s, now, db)
    _print_summary(
        dump_sessions=dump_sessions, dup_ids=dup_ids,
        match_sessions=match_sessions, skipped=skipped, unresolved=unresolved,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
