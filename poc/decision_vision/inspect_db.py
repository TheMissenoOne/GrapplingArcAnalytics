"""Read-only helper to list DB matches usable by the POC."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import find_analytics_root
from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analytics-root", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    load_dotenv()
    find_analytics_root(args.analytics_root)

    from sqlalchemy import select

    from db.base import db_session
    from db.models import Athlete, Match

    with db_session() as session:
        athletes = {
            str(row.id): row.name
            for row in session.execute(select(Athlete)).scalars()
        }
        matches = (
            session.execute(
                select(Match)
                .where(Match.status == "final")
                .where(Match.video_url.is_not(None))
                .order_by(Match.created_at.desc())
                .limit(args.limit)
            )
            .scalars()
            .all()
        )

        for match in matches:
            raw = list(match.timeline or match.sequence or [])
            timed = sum(
                1
                for event in raw
                if isinstance(event, dict)
                and (
                    event.get("ts") is not None
                    or event.get("timestamp_seconds") is not None
                    or event.get("timestamp") is not None
                )
            )
            print(
                "\t".join([
                    str(match.id),
                    athletes.get(str(match.athlete_a_id), str(match.athlete_a_id)),
                    athletes.get(str(match.athlete_b_id), str(match.athlete_b_id)),
                    f"events={len(raw)}",
                    f"timed={timed}",
                    str(match.video_url),
                ])
            )


if __name__ == "__main__":
    main()
