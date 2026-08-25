"""Targeted merge of one duplicate athlete pair (generalized from merge_anabel_lopez.py,
third use of the pattern). Repoints match FKs, rewrites actor_id inside sequence JSONB
(the ad51df2 scar), deletes the dup row's graph + row, replays the canonical athlete.

    uv run python runs/graph_cleanup/merge_athlete_pair.py \
        --keep "Nicky Ryan" --dup "Nikki Ryan" [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import text  # noqa: E402

from db.base import db_session  # noqa: E402
from db.models import Athlete  # noqa: E402
from db.repository import replay_and_persist_athlete  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", required=True)
    ap.add_argument("--dup", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    with db_session() as s:
        keep = s.execute(text("select id from athletes where name=:n"), {"n": a.keep}).scalar()
        dup = s.execute(text("select id from athletes where name=:n"), {"n": a.dup}).scalar()
        if not keep or not dup:
            print(f"nothing to do (keep={keep}, dup={dup})")
            return 0
        ms = s.execute(text(
            "select id, sequence from matches where athlete_a_id=:d or athlete_b_id=:d "
            "or winner_id=:d"), {"d": dup}).fetchall()
        print(f"keep {str(keep)[:8]} ({a.keep}) | dup {str(dup)[:8]} ({a.dup}) | "
              f"matches to repoint: {len(ms)}")
        if not a.write:
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
        print("done — canonical graph replayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
