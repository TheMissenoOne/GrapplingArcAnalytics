"""Targeted merge: 'Anabel Lopez Beard' -> 'Anabel Lopez' (same person, two rows).

Created 2026-08-25: the audited women-65 frame batch imported 7 bouts under
'Anabel Lopez' while prod already carried 'Anabel Lopez Beard' (2 bouts) — the
anti-phantom check matches on surname and 'Beard' != 'Lopez'. Canonical kept as
'Anabel Lopez' because the scouting roster / records builder key on that form.

Mirrors scripts/dedupe_athletes.py's steps for one pair (the ad51df2 scar: FK
repoint alone leaves ghost actor_ids inside sequence JSONB):
  1. repoint matches.athlete_a_id/athlete_b_id/winner_id
  2. rewrite actor_id inside every affected match's sequence JSONB
  3. delete the dup row's athlete graph (+edges), delete the dup athlete row
  4. replay the canonical athlete's graph

Run (repo root, .env loaded):
    uv run python runs/graph_cleanup/merge_anabel_lopez.py            # dry-run
    uv run python runs/graph_cleanup/merge_anabel_lopez.py --write
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import text  # noqa: E402

from db.base import db_session  # noqa: E402
from db.models import Athlete  # noqa: E402
from db.repository import replay_and_persist_athlete  # noqa: E402

KEEP_NAME = "Anabel Lopez"
DUP_NAME = "Anabel Lopez Beard"


def main() -> int:
    write = "--write" in sys.argv
    with db_session() as s:
        keep = s.execute(text("select id from athletes where name=:n"), {"n": KEEP_NAME}).scalar()
        dup = s.execute(text("select id from athletes where name=:n"), {"n": DUP_NAME}).scalar()
        if not keep or not dup:
            print(f"nothing to do (keep={keep}, dup={dup})")
            return 0
        ms = s.execute(text(
            "select id, sequence from matches where athlete_a_id=:d or athlete_b_id=:d "
            "or winner_id=:d"), {"d": dup}).fetchall()
        print(f"keep {str(keep)[:8]} | dup {str(dup)[:8]} | matches to repoint: {len(ms)}")
        if not write:
            print("dry-run — pass --write to execute")
            return 0
        s.execute(text("update matches set athlete_a_id=:k where athlete_a_id=:d"),
                  {"k": keep, "d": dup})
        s.execute(text("update matches set athlete_b_id=:k where athlete_b_id=:d"),
                  {"k": keep, "d": dup})
        s.execute(text("update matches set winner_id=:k where winner_id=:d"),
                  {"k": keep, "d": dup})
        n = 0
        for mid, seq in ms:
            if not seq:
                continue
            changed = False
            for e in seq:
                if isinstance(e, dict) and e.get("actor_id") == str(dup):
                    e["actor_id"] = str(keep)
                    changed = True
                    n += 1
            if changed:
                s.execute(text("update matches set sequence=(:s)::jsonb where id=:i"),
                          {"s": json.dumps(seq), "i": mid})
        print(f"actor_ids rewritten: {n}")
        s.execute(text("delete from graph_edges where graph_id in "
                       "(select id from graphs where owner_id=:d and owner_kind='athlete')"),
                  {"d": dup})
        s.execute(text("delete from graphs where owner_id=:d and owner_kind='athlete'"),
                  {"d": dup})
        s.execute(text("delete from athletes where id=:d"), {"d": dup})
        canonical = s.get(Athlete, keep)
        assert canonical is not None
        replay_and_persist_athlete(canonical, s)
        s.commit()
        left = s.execute(text("select count(*) from athletes where name like 'Anabel Lopez%'"))\
            .scalar()
        print(f"done — Anabel rows remaining: {left}; canonical graph replayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
