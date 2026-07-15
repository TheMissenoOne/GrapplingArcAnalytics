#!/usr/bin/env python
"""Split mashed-together event labels into their real two-technique events.

Some scraped/refined event labels jam two distinct grappling actions into one node
(``Crucifix / Omoplata``, ``Snap Down / Front Headlock``, ...) or aren't a technique at
all (``Kimura Counter`` — a defensive reaction, not an attack). This replaces each mashed
event with its two real events (label/type/actor per ``docs/match_event_model.md``
ownership table) or drops the non-technique event outright.

**Dry-run by default** — this touches ``matches.sequence`` structurally (unlike a plain
rename), so unlike this repo's other cleanup scripts (dry-run is opt-in there) writing
requires the explicit ``--apply`` flag:

    uv run python -m scripts.split_mashed_events            # report only (default)
    uv run python -m scripts.split_mashed_events --apply     # write + commit

A few near-variants are reported but never auto-applied (ambiguous scope / actor) — see
``FLAGGED_ONLY`` below. Re-run ``uv run python -m scripts.clean_match_techniques`` after
applying to canonicalise the new labels against the technique library (``Snap Down`` →
``Snapdown``, ``Omoplata`` → ``Omoplata (Shoulder Lock)``, etc).
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

Event = dict[str, Any]


def _ts_pair(ts: int | None) -> tuple[int | None, int | None]:
    if ts is None:
        return None, None
    return ts, ts + 1


def _with_ts(ev: Event, ts: int | None) -> Event:
    if ts is not None:
        ev["ts"] = ts
    return ev


def split_crucifix_omoplata(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    return [
        _with_ts({"label": "Crucifix", "type": "control", "actor_id": actor}, ts1),
        # ponytail: original event carried no `successful` — omoplata attempt is followed
        # by a further submission attempt + escape in the one sampled match, so False
        # (attempted/defended) is the closer read than omitting; flagged in report.
        _with_ts(
            {"label": "Omoplata", "type": "submission", "actor_id": actor, "successful": False}, ts2
        ),
    ]


def split_snap_down_front_headlock(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    # Front Headlock (the entry) precedes Snap Down (the finish) chronologically.
    # ~93% of occurrences are stored as type=submission/successful=True even on
    # DECISION-result matches (no finish) — that flag reads as "the entry landed", not
    # "the fight ended here". Reclassified: neither split event is a submission, so
    # `successful`/`submission` is dropped entirely, not carried forward.
    return [
        _with_ts({"label": "Front Headlock", "type": "control", "actor_id": actor}, ts1),
        _with_ts({"label": "Snap Down", "type": "takedown", "actor_id": actor}, ts2),
    ]


def split_escape_gave_up_back(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    escaper = e["actor_id"]
    # ACTOR FLIP: back control belongs to the fighter who now holds it, not the escaper.
    opponent = m.athlete_b_id if escaper == m.athlete_a_id else m.athlete_a_id
    return [
        _with_ts({"label": "Escape to Standing", "type": "escape", "actor_id": escaper}, ts1),
        _with_ts({"label": "Back Control", "type": "control", "actor_id": opponent}, ts2),
    ]


def split_arm_drag_single_leg(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    return [
        _with_ts({"label": "Arm Drag", "type": "takedown", "actor_id": actor}, ts1),
        _with_ts(
            {"label": "Single Leg Takedown", "type": "takedown", "actor_id": actor,
             "successful": True},
            ts2,
        ),
    ]


def split_duck_under_side_control(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    # FLAGGED: "Duck Under" is context-a-sweep/reversal here, but the technique library's
    # intrinsic type for "Duck Under" is `takedown` (20 existing occurrences vs 9
    # `transition`, 0 `sweep`) — using `takedown` keeps it canonicalisable + consistent
    # with the corpus rather than the context-literal `sweep`.
    return [
        _with_ts(
            {"label": "Duck Under", "type": "takedown", "actor_id": actor, "successful": True}, ts1
        ),
        _with_ts({"label": "Side Control", "type": "control", "actor_id": actor}, ts2),
    ]


def split_ankle_aoki(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    return [
        # FLAGGED: no signal distinguishes the two sub-attempts; False (attempted, superseded
        # by the aoki lock) is inferred, not observed.
        _with_ts(
            {"label": "Straight Ankle Lock", "type": "submission", "actor_id": actor,
             "successful": False},
            ts1,
        ),
        _with_ts(
            {"label": "Aoki Lock", "type": "submission", "actor_id": actor, "successful": True}, ts2
        ),
    ]


def remove_kimura_counter(e: Event, m: Any) -> list[Event] | None:
    return None


def split_sweep_side_control(e: Event, m: Any) -> list[Event] | None:
    ts1, ts2 = _ts_pair(e.get("ts"))
    actor = e["actor_id"]
    # `sweep` is the dataset-native type for bare "Sweep" (technique_library.json /
    # seed_technique_nodes._TYPE_TO_NODE_TYPE), not `takedown`. Sweep marked
    # successful:True — it's the entry that lands the position, same role as "Duck
    # Under" in the sibling "Sweep (Duck Under to Side Control)" split.
    return [
        _with_ts({"label": "Sweep", "type": "sweep", "actor_id": actor, "successful": True}, ts1),
        _with_ts({"label": "Side Control", "type": "control", "actor_id": actor}, ts2),
    ]


# label (lowercased, exact stored form) -> builder. `None` return = drop the event.
RULES: dict[str, Any] = {
    "crucifix / omoplata": split_crucifix_omoplata,
    "snap down / front headlock": split_snap_down_front_headlock,
    "snap down to front headlock": split_snap_down_front_headlock,
    "escape to standing (gave up back)": split_escape_gave_up_back,
    "arm‑drag‑to‑single leg takedown": split_arm_drag_single_leg,
    "drag‑to‑single leg takedown (tb)": split_arm_drag_single_leg,
    "sweep (duck under to side control)": split_duck_under_side_control,
    "sweep to side control": split_sweep_side_control,
    "straight ankle lock / aoki lock": split_ankle_aoki,
    "kimura counter": remove_kimura_counter,
}

# Reported but never auto-applied — genuinely ambiguous scope, needs a human call first.
FLAGGED_ONLY: dict[str, str] = {}


def run(apply: bool) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from db.base import db_session
    from db.models import Match

    applied: Counter[str] = Counter()
    flagged: Counter[str] = Counter()
    matches_changed = 0

    with db_session() as session:
        matches = list(session.execute(select(Match)).scalars())
        for m in matches:
            seq = m.sequence or []
            if not seq:
                continue
            new_seq: list[Any] = []
            changed = False
            for e in seq:
                if not isinstance(e, dict):
                    new_seq.append(e)
                    continue
                label = str(e.get("label", "")).strip().lower()
                if label in RULES:
                    result = RULES[label](e, m)
                    applied[label] += 1
                    logger.info(
                        "%s match=%s idx=%d  %s -> %s",
                        "REMOVE" if result is None else "SPLIT",
                        m.id,
                        len(new_seq),
                        e,
                        result,
                    )
                    changed = True
                    if result is not None:
                        new_seq.extend(result)
                    continue
                if label in FLAGGED_ONLY:
                    flagged[label] += 1
                    logger.warning(
                        "FLAGGED (not applied) match=%s: %s -- %s", m.id, e, FLAGGED_ONLY[label]
                    )
                new_seq.append(e)
            if changed:
                matches_changed += 1
                if apply:
                    m.sequence = new_seq
                    flag_modified(m, "sequence")
        if apply:
            session.commit()

    logger.info("%s: %d match(es) touched", "APPLIED" if apply else "DRY-RUN", matches_changed)
    for label, n in applied.most_common():
        logger.info("  %3d  %s", n, label)
    if flagged:
        logger.info("Flagged (not applied), needs human decision:")
        for label, n in flagged.most_common():
            logger.info("  %3d  %s", n, label)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description="Split mashed-together match event labels")
    ap.add_argument(
        "--apply", action="store_true", help="write + commit (default: dry-run report only)"
    )
    args = ap.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
