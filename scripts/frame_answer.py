"""The shape a frame-reading answer must have, and the checks a human should never have to run.

An answer is one JSON file, ``answer.json``, written next to the frames it describes. This
module is the only definition of that shape -- the reader's prompt, the review UI and any
future importer all read it from here, so there is one contract and not three.

**Why a validator before a reviewer.** Human review is the scarce resource on this pipeline.
Every defect a script can catch mechanically -- a label the library does not have, a
timestamp outside the frames that exist, ``points: 0`` standing in for "I could not tell" --
is a defect that must not reach a person's attention. What is left for the human is the only
thing a script cannot do: look at the frame and say whether the event is really there.

    uv run python scripts/frame_answer.py                    # every folder under out/
    uv run python scripts/frame_answer.py <folder> [...]     # just these

Exit status is 1 when any answer has a problem, so this drops into a check step unchanged.

Privacy class: **A, public competition data.** Published broadcasts of published bouts.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = REPO / "data" / "frame_pdf" / "out"
LIBRARY = REPO / "data" / "frame_pdf" / "node_library.json"

EVENT_TYPES = {"control", "submission", "guard", "takedown", "pass", "transition",
               "sweep", "escape"}
CONFIDENCE = {"high", "low"}

# Bout-level fields. `identity_discriminator` is required and is not bureaucracy: every event
# names an `actor`, so an answer that never says HOW the two bodies were told apart is a pile
# of attributions resting on nothing. Stating it makes the assumption reviewable -- and when
# it is wrong, every actor in the answer flips together rather than being wrong one at a time.
# Only the two names are required. `identity_discriminator` was mandatory while a MODEL did
# the reading -- an answer that never said how it told two bodies apart was a pile of
# attributions resting on nothing. A person at the bench is that check, and their names are
# bound to bodies by the same eyes that logged each event. It stays optional, and worth
# filling for a bout whose kit was genuinely hard to tell apart.
BOUT_REQUIRED = ("athlete_a", "athlete_b")
BOUT_OPTIONAL = ("event", "year", "winner", "win_type", "bout_start_seconds", "bout_end_seconds",
                 "identity_discriminator", "identity_verified_by",
                 "final_score", "advantages", "notes",
                 # Penalties are scoreboard facts, readable off footage for the first time --
                 # and deliberately NOT events. `events` feed the technique transition graph,
                 # where every entry becomes a node in an athlete's game; a penalty is not a
                 # technique and would arrive as one. So it is recorded at bout level, where
                 # it is preserved without being mistaken for something the athlete did.
                 # Free text, e.g. "Svendsen ~t30 (1); Le Vern ~t550 (1)".
                 "penalties")
EVENT_REQUIRED = ("ts", "label", "actor", "successful", "type")
EVENT_OPTIONAL = ("points", "confidence", "note", "new_label")


def load_labels() -> set[str]:
    """Every label the library allows, folded the way a reader would have typed it."""
    if not LIBRARY.exists():
        return set()
    lib = json.loads(LIBRARY.read_text(encoding="utf-8"))
    return {str(n["label"]).strip().casefold() for n in lib["nodes"]}


def frame_times(folder: Path) -> list[int]:
    f = folder / "frames.jsonl"
    if not f.exists():
        return []
    return [int(json.loads(line)["ts"]) for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _check_score(bout: Mapping[str, Any], where: str) -> list[str]:
    """`final_score` must be a name→points map, never a bare string.

    "9-5" against competitors [Lopez, Ste-Marie] has no declared orientation, and the buzzer
    board proved it was winner-first — the positional reading handed the loser the 9. Four
    bouts in one batch had a scoreline whose positional reading contradicted the winner, so
    this is a systematic ambiguity, not an edge case. A map cannot be read the wrong way round.
    """
    score = bout.get(where)
    if score is None:
        return []
    if isinstance(score, str):
        return [f"bout.{where} is a string ({score!r}); use a name→points map so the "
                "orientation is declared and cannot be read backwards"]
    if not isinstance(score, dict):
        return [f"bout.{where} must be a name→points map"]
    people = {str(bout.get("athlete_a") or ""), str(bout.get("athlete_b") or "")} - {""}
    problems = []
    for name, pts in score.items():
        if people and name not in people:
            problems.append(f"bout.{where} names {name!r}, who is neither competitor")
        if isinstance(pts, bool) or not isinstance(pts, int) or pts < 0:
            problems.append(f"bout.{where}[{name!r}] must be a non-negative integer")
    return problems


def validate(answer: dict[str, Any], labels: set[str], times: list[int]) -> list[str]:
    """Every problem, not the first one. A reader fixing an answer wants the whole list."""
    problems: list[str] = []
    bout = answer.get("bout")
    if not isinstance(bout, dict):
        return ["`bout` is missing or is not an object"]
    for k in BOUT_REQUIRED:
        if not str(bout.get(k) or "").strip():
            problems.append(f"bout.{k} is required and empty")
    for k in bout:
        if k not in BOUT_REQUIRED + BOUT_OPTIONAL:
            problems.append(f"bout.{k} is not a field of this schema")

    problems += _check_score(bout, "final_score")
    problems += _check_score(bout, "advantages")

    events = answer.get("events")
    if not isinstance(events, list):
        return [*problems, "`events` is missing or is not a list"]

    span = (min(times), max(times)) if times else None
    people = {str(bout.get("athlete_a") or "").casefold(),
              str(bout.get("athlete_b") or "").casefold()} - {""}

    for i, e in enumerate(events):
        at = f"events[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{at} is not an object")
            continue
        for k in EVENT_REQUIRED:
            if k not in e:
                problems.append(f"{at}.{k} is required")
        for k in e:
            if k not in EVENT_REQUIRED + EVENT_OPTIONAL:
                problems.append(f"{at}.{k} is not a field of this schema")

        ts = e.get("ts")
        if not isinstance(ts, int) or isinstance(ts, bool):
            problems.append(f"{at}.ts must be an integer second, got {ts!r}")
        elif span and not (span[0] <= ts <= span[1]):
            # An event outside the sampled window was not read off these frames, whatever
            # else it was read off.
            problems.append(f"{at}.ts {ts} is outside the frames ({span[0]}..{span[1]})")

        label = str(e.get("label") or "").strip()
        if not label:
            problems.append(f"{at}.label is empty")
        elif labels and label.casefold() not in labels and not e.get("new_label"):
            # Not fatal by itself -- but an unlisted label WITHOUT `new_label: true` is the
            # silent node-splitting case the whole closed vocabulary exists to prevent.
            problems.append(f"{at}.label {label!r} is not in the library "
                            "and is not flagged `new_label: true`")

        actor = str(e.get("actor") or "").strip()
        if not actor:
            problems.append(f"{at}.actor is empty")
        elif people and actor.casefold() not in people:
            problems.append(f"{at}.actor {actor!r} is neither competitor")

        if not isinstance(e.get("successful"), bool):
            problems.append(f"{at}.successful must be true or false")
        if e.get("type") not in EVENT_TYPES:
            problems.append(f"{at}.type {e.get('type')!r} is not one of {sorted(EVENT_TYPES)}")

        if "points" in e:
            pts = e["points"]
            if isinstance(pts, bool) or not isinstance(pts, int) or pts < 0:
                problems.append(f"{at}.points must be a non-negative integer, got {pts!r}")
            elif pts == 0:
                # The distinction the schema exists to hold: an action that scored nothing and
                # an action nobody could read are different facts, and only the ABSENT field
                # holds the second. A written 0 collapses them.
                problems.append(f"{at}.points is 0 -- omit the field instead; 0 means "
                                "'the scoreboard awarded nothing', never 'I could not tell'")
        if "confidence" in e and e["confidence"] not in CONFIDENCE:
            problems.append(f"{at}.confidence must be one of {sorted(CONFIDENCE)}")

    tss = [e["ts"] for e in events if isinstance(e.get("ts"), int)]
    if tss != sorted(tss):
        problems.append("events are not in timestamp order")
    return problems


ANSWER_FILES = ("events.json", "answer.json")


def check(folder: Path, labels: set[str]) -> tuple[str, list[str]]:
    path = next((folder / n for n in ANSWER_FILES if (folder / n).exists()), None)
    if path is None:
        return "unregistered", []
    try:
        answer = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return "invalid", [f"answer.json is not valid JSON: {exc}"]
    problems = validate(answer, labels, frame_times(folder))
    return ("ok" if not problems else "invalid"), problems


def main() -> int:
    args = [Path(a) for a in sys.argv[1:]]
    folders = args or sorted(d for d in OUT.iterdir() if (d / "frames.jsonl").exists())
    labels = load_labels()
    if not labels:
        print(f"warning: {LIBRARY.name} not found -- label checking is off")

    bad = 0
    for d in folders:
        state, problems = check(d, labels)
        n = 0
        if state == "ok":
            f = next(d / x for x in ANSWER_FILES if (d / x).exists())
            n = len(json.loads(f.read_text(encoding="utf-8"))["events"])
        print(f"{state:12s} {d.name}" + (f"  ({n} events)" if state == "ok" else ""))
        for p in problems:
            print(f"            - {p}")
        bad += state == "invalid"
    print(f"\n{len(folders)} folders, {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
