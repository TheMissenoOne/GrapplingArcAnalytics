"""Who an event belongs to, and what side of it each fighter was on.

`docs/match_event_model.md` states the convention every entry path is supposed to follow:
``actor`` is the fighter whose GAME the node belongs to, not who is winning -- a ``guard`` node
belongs to the player underneath, the ``pass`` to the one passing. That convention, if the
corpus obeyed it, would make role a pure function of ``(type, label)``.

**The corpus obeys it for some types and not for others, and that was measured rather than
assumed** (prod, 2026-08-20, 9 600 sequence events across 700 bouts):

  submission   CONFIRMED. In bouts whose `win_type` is SUBMISSION, the last `successful=True`
               submission event's actor is the winner in 225 of 237 (94.9%); matching the
               event label against `matches.submission` gives 233 of 246 (94.7%). That is an
               independent column agreeing with the convention, not a self-check.
  control      CONFIRMED on the case with ground truth: of the back-attack finishes, the last
               `Back Control` event belongs to the winner in 31 of 37 (83.8%).
  guard/pass   REFUTED as a per-event rule. A `guard` event and a `pass` event within 10 s and
               two slots of each other carry the SAME actor in 52 of 82 pairs (63.4%). Under
               the convention that number has to be near zero: the guard is the bottom
               player's and the pass is the top player's, and one fighter cannot be both at
               once. At bout level the athlete owning most guard events also owns most pass
               events in 108 of 166 bouts (65.1%) -- barely under the 73.7% expected if actor
               were drawn at random in proportion to who is dominant in that bout.

The mechanism is visible in the raw rows: **307 of 700 bouts (43.9%) have every single event
filed under ONE athlete**, including bouts that hand her `Half Guard`, `Smash Pass`, `Back
Control` and `Scarf Hold` in the same minute. Those are physically exclusive. What happened is
that some ingest batches set `actor` to whoever the transcript was narrating. It is a property
of the BATCH, not of the corpus: `CJI 2 - Day 1` and `Polaris 18` have zero one-sided bouts of
eight events or more, `WNO 22` has 4 of 7 and `ADCC 2022` 15 of 30.

So role is derived in two steps, and the second one is the reason this module exists:

  1. ``classify(type, label)`` -- the convention, as a table, per (type, label).
  2. ``bout_flags(...)``        -- whether THIS bout's actor assignments can carry a role at
     all. A bout that is entirely one-sided, or that gives one actor a top state and a bottom
     state inside the same half-minute, has no readable role and says so.

Nothing here ever infers a role by elimination. A bout has two athletes, so the TARGET of an
event is the other one and that is safe; the target's ROLE is only ever the one the label
itself defines (`Half Guard` has someone on top by definition). Where the label does not define
it, the role is ``unknown``, the event is preserved raw, and every directional statistic drops
it. Fewer correct numbers beat more inferred ones.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis.names import _normalize_name

# Bump when a table below changes in a way that moves a published number.
RULES_VERSION = 1

# The eight types the model defines (docs/match_event_model.md). Everything else in the column
# is bookkeeping that one import batch left behind -- eight rows labelled "Match" -- and it is
# not a grappling event at all. Refusing it here rather than letting it inherit a `transition`
# role is the difference between "we could not read this event" and "this is not an event".
EVENT_TYPES = frozenset({"guard", "pass", "control", "takedown", "sweep", "submission",
                         "escape", "transition"})

# ── vocabulary ──────────────────────────────────────────────────────────────────
# What kind of thing the node IS. Drives the semantic split the report renders:
# positions/states, actions/techniques, transitions.
STATE, ACTION, TRANSITION = "state", "action", "transition"

# What the actor is doing. Kept apart from the role because "executes" and "top" answer
# different questions and collapsing them loses one of them.
EXECUTES, DEFENDS, HOLDS, PLAYS, ESCAPES, INITIATES = (
    "executes", "defends", "holds", "plays", "escapes", "initiates")

# Roles, one enum over both axes. An ACTION has an executor and a defender; a positional STATE
# has a topology. `neutral` is a real answer, not a missing one -- 50/50 is symmetric by
# construction and calling either side "top" would be an invention.
EXECUTOR, DEFENDER = "executor", "defender"
TOP, BOTTOM, CONTROLLING, CONTROLLED, NEUTRAL, UNKNOWN = (
    "top", "bottom", "controlling", "controlled", "neutral", "unknown")

# Which roles the page may render as a topology suffix ("· por baixo").
TOPOLOGICAL = (TOP, BOTTOM, CONTROLLING, CONTROLLED)

# Two SEPARATE axes, and keeping them separate is load-bearing. Top/bottom is about who is
# underneath; controlling/controlled is about who is holding whom. They are not the same
# question and a position can sit on both -- back control taken from underneath is `controlling`
# and `bottom` at once, which is why a first version that pooled them into one sign reported
# `Half Guard` beside `Back Control` as a contradiction and threw away 53% of the events over
# a distinction it had invented.
_AXES = ({TOP: 1, BOTTOM: -1}, {CONTROLLING: 1, CONTROLLED: -1})

# Seconds within which two states are treated as describing the same moment. Ten, not thirty:
# a real reversal (bottom, sweep, top) takes seconds, so a wide window reads normal grappling
# as a defect. At 10 s this fires on 3 of the 38 roster bouts; at 30 s it fired on 6, and the
# three extra were all legitimate sequences a coach would recognise.
CONTRADICTION_WINDOW = 10
# A bout this long with every event on one athlete is a filing artefact, not a fight. Below it,
# a genuinely one-sided twenty-second armbar is indistinguishable from a mis-filed bout, so no
# claim is made either way.
MIN_EVENTS_FOR_ONE_SIDED = 6


@dataclass(frozen=True)
class Attribution:
    """What ``(type, label)`` alone can say about an event. No bout context, no athletes."""

    category: str            # STATE | ACTION | TRANSITION
    relation: str            # EXECUTES | DEFENDS | HOLDS | PLAYS | ESCAPES | INITIATES
    actor_role: str          # the enum above
    target_role: str
    status: str              # "resolved" | "symmetric" | "unknown"
    source: str              # "table" | "type_default" -- how the row was reached

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "relation": self.relation,
                "actor_role": self.actor_role, "target_role": self.target_role,
                "attribution_status": self.status, "rule_source": self.source}


def _a(category: str, relation: str, actor: str, target: str,
       status: str = "resolved", source: str = "table") -> Attribution:
    return Attribution(category, relation, actor, target, status, source)


# ── per-type defaults ───────────────────────────────────────────────────────────
# Straight out of the ownership table in docs/match_event_model.md. `guard` and `pass` keep
# their convention rows here because the convention IS what the doc says; what the measurement
# refuted is that every BOUT honours it, and that is handled by `bout_flags`, not by pretending
# the model has no opinion.
_TYPE_DEFAULT: dict[str, Attribution] = {
    "guard": _a(STATE, PLAYS, BOTTOM, TOP, source="type_default"),
    "pass": _a(ACTION, EXECUTES, EXECUTOR, DEFENDER, source="type_default"),
    "control": _a(STATE, HOLDS, CONTROLLING, CONTROLLED, source="type_default"),
    "takedown": _a(ACTION, EXECUTES, EXECUTOR, DEFENDER, source="type_default"),
    "sweep": _a(ACTION, EXECUTES, EXECUTOR, DEFENDER, source="type_default"),
    "submission": _a(ACTION, EXECUTES, EXECUTOR, DEFENDER, source="type_default"),
    "escape": _a(ACTION, ESCAPES, EXECUTOR, DEFENDER, source="type_default"),
    "transition": _a(TRANSITION, INITIATES, EXECUTOR, DEFENDER, source="type_default"),
}

# ── positional states: the topology the label itself defines ────────────────────
# Keys are `analysis.names._normalize_name` output, so accents, case and the non-breaking
# hyphens the corpus carries ("Stand‑up Escape") all land on the same row.

# Guards. The guard player is underneath and someone is on top of her -- that is what a guard
# is, and it is the one place the report is allowed to name the opponent's side from the
# actor's label.
_GUARD_BOTTOM = (
    "half guard", "closed guard", "close guard", "butterfly guard", "deep half guard",
    "kguard", "rubber guard", "lasso guard", "spider guard", "worm guard", "xguard",
    "open guard", "guard", "guard recovery", "guard retention", "de la riva guard",
    "inverted de la riva guard", "seated guard", "knee shield half guard",
    "butterfly half guard", "half guard flat", "zguard", "bear trap",
    "guard pull", "pull guard", "pull half guard", "pull closed guard", "jump guard",
    "pull guard inversion", "pull guard inside triangle",
)
# Symmetric or leg-entanglement positions. Neither fighter is on top in any sense a coach would
# recognise, and 50/50 is the textbook case. Calling one side "top" here would be an invention
# dressed as a reading.
_GUARD_NEUTRAL = (
    "5050 guard", "leg entanglement", "leg entanglement 5050", "leg lock entanglement",
    "leg lock exchange", "leg entry", "single leg x", "single leg x guard entry",
    "shin to shin guard", "shin on shin guard", "double guard pull", "inversion",
    "outside ashi garami", "back step to leg entanglement", "imanari roll to leg entanglement",
    "leg entanglement false reap", "leg entanglement heel hook entry",
    "leg entanglement aoki lock entry",
)

# Controls. Split three ways because "controlling" and "on top" are not the same claim.
_CONTROL_TOP = (
    "mount", "side control", "northsouth position", "north south control", "knee on belly",
    "scarf hold", "threequarter mount", "top half guard", "top control", "top control body lock",
    "top control half guard", "smash half guard", "chesttochest half guard",
    "headarm control top", "mounted crucifix", "crucifix", "ground and pound", "half nelson",
    "half guard control", "leg lace", "ride out", "straight jacket",
)
_CONTROL_BACK = (
    "back control", "body triangle", "body lock", "rear body lock", "seatbelt control",
    "truck", "arm drag to back take", "crab ride to back take", "standing back control",
)
# Standing / grip control. Real control, no topology -- both are on their feet.
_CONTROL_GRIP = (
    "collar tie", "underhook", "double underhooks", "russian tie", "kimura grip",
    "twoonone wrist control", "twoonone control", "front headlock", "clinch knees",
    "hooks in", "inside ashi", "cross ashi", "saddle inside sankaku",
)

# ── the exceptions, one row each, with the reason ───────────────────────────────
_LABEL: dict[tuple[str, str], Attribution] = {
    # Turtle is the DEFENSIVE shape. Filed under `control` 14 times, which the type table would
    # read as "she holds a dominant position" -- she does not, she is the one turtled. The
    # actor's own side is knowable (she is turtling); the opponent's is not (riding? standing
    # off? chasing the back?), so the target role stays unknown rather than being guessed.
    ("control", "turtle position"): _a(STATE, DEFENDS, BOTTOM, UNKNOWN, status="unknown"),
    ("control", "turtle control"): _a(STATE, HOLDS, CONTROLLING, CONTROLLED),
    # Filed under `control` 46 times and under `escape` 3 times for the same movement. The
    # actor escaped TO turtle; that is an escape, and it leaves her defensive, not controlling.
    ("control", "escape to turtle"): _a(ACTION, ESCAPES, EXECUTOR, DEFENDER),
    ("control", "body triangle bottom"): _a(STATE, HOLDS, CONTROLLING, CONTROLLED),
    ("control", "near fall"): _a(STATE, HOLDS, TOP, BOTTOM),
    ("control", "nearfall"): _a(STATE, HOLDS, TOP, BOTTOM),
    ("control", "arm drag"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    ("control", "smother"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    # A defence, not an attack. The actor is the one who was shot on and stayed up, so the
    # relation inverts even though the perspective does not: it is still HER event.
    ("takedown", "takedown defense"): _a(ACTION, DEFENDS, DEFENDER, EXECUTOR),
    ("transition", "takedown defense"): _a(ACTION, DEFENDS, DEFENDER, EXECUTOR),
    ("escape", "sprawl"): _a(ACTION, DEFENDS, DEFENDER, EXECUTOR),
    # A sweep ENDS with the sweeper on top; that is what makes it a sweep and not a scramble.
    ("sweep", "sweep top position"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    # Filed under `escape` and under `guard`. Turtling is the actor's own defensive shape.
    ("escape", "turtle position"): _a(ACTION, ESCAPES, EXECUTOR, DEFENDER),
    ("guard", "turtle position"): _a(STATE, DEFENDS, BOTTOM, UNKNOWN, status="unknown"),
    # Generic endings. Measured against `matches.winner_id`: "Tap" 55/64 (86%) and "Finish"
    # 95/112 (85%) belong to the winner against a 62.5% baseline, so the actor is the FINISHER
    # -- the convention holds -- but the label names no technique, so it carries no topology.
    ("submission", "tap"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    ("submission", "finish"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    ("submission", "submission"): _a(ACTION, EXECUTES, EXECUTOR, DEFENDER),
    # 0 of 6 belong to the winner. Six events is not evidence of an inversion and is not
    # evidence against one either, so this refuses instead of picking a side on n=6.
    ("submission", "submit"): _a(ACTION, EXECUTES, UNKNOWN, UNKNOWN, status="unknown"),
    # Bookkeeping rows from one import batch. Not a grappling event at all.
    ("match", "match"): _a(TRANSITION, INITIATES, UNKNOWN, UNKNOWN, status="unknown"),
}


def classify(ev_type: str | None, label: str | None) -> Attribution:
    """Category, relation and both roles for one event, from its type and label alone.

    Pure. No bout, no athletes, no timestamps -- whether the bout's actor assignments can be
    trusted at all is a separate question, answered by `bout_flags`.
    """
    t = (ev_type or "").strip().lower()
    key = _normalize_name(label or "")
    hit = _LABEL.get((t, key))
    if hit is not None:
        return hit
    if t == "guard":
        if key in _GUARD_NEUTRAL:
            return _a(STATE, PLAYS, NEUTRAL, NEUTRAL, status="symmetric")
        if key in _GUARD_BOTTOM:
            return _a(STATE, PLAYS, BOTTOM, TOP)
        # An unseen guard label. The type says she is playing guard, which places her
        # underneath; the opponent's side follows from what a guard is. Marked `type_default`
        # so a reader can tell a curated row from an inherited one.
        return _TYPE_DEFAULT["guard"]
    if t == "control":
        if key in _CONTROL_BACK:
            return _a(STATE, HOLDS, CONTROLLING, CONTROLLED)
        if key in _CONTROL_TOP:
            return _a(STATE, HOLDS, TOP, BOTTOM)
        if key in _CONTROL_GRIP:
            return _a(STATE, HOLDS, CONTROLLING, CONTROLLED)
        return _TYPE_DEFAULT["control"]
    if t == "pass":
        # A pass is an action AND it places the passer on top of a guard. Both are true and
        # the report uses both: the action list counts it, the topology names the sides.
        return _a(ACTION, EXECUTES, TOP, BOTTOM, source="type_default")
    return _TYPE_DEFAULT.get(t) or _a(TRANSITION, INITIATES, UNKNOWN, UNKNOWN, status="unknown")


# ── bout-level: can this bout carry a role at all ───────────────────────────────
def bout_flags(seq: Sequence[Mapping[str, Any]], a_id: str, b_id: str) -> dict[str, Any]:
    """Whether THIS bout's actor field can support a directional reading.

    Two refusals, both of them measurements rather than judgements:

    ``one_sided``      every event of a bout with at least `MIN_EVENTS_FOR_ONE_SIDED` events
                       is filed under one athlete. The other fighter did not stand still for
                       nine minutes; her side was not recorded. 43.9% of bouts corpus-wide.
    ``contradictory``  one actor is given a top-side state and a bottom-side state inside
                       `CONTRADICTION_WINDOW` seconds. Nobody is mounted and in half guard at
                       the same time, so at least one of the two rows is mis-filed and there
                       is no way to tell which.

    Either one makes ROLE unreadable for the whole bout. Perspective -- whose event it is --
    survives `contradictory` (the actor field may still name the right fighter for most rows)
    but not `one_sided`, where the field carries no information at all.
    """
    events = [e for e in seq if e.get("actor_id")]
    actors = {e["actor_id"] for e in events}
    one_sided = len(events) >= MIN_EVENTS_FOR_ONE_SIDED and len(actors) <= 1

    clashes: list[dict[str, Any]] = []
    stamped = [(e, classify(e.get("type"), e.get("label"))) for e in events]
    for axis in _AXES:
        positional = [(e, a) for e, a in stamped
                      if a.actor_role in axis and e.get("ts") is not None]
        for i, (e1, at1) in enumerate(positional):
            for e2, at2 in positional[i + 1:]:
                if e1["actor_id"] != e2["actor_id"]:
                    continue
                if abs(int(e2["ts"]) - int(e1["ts"])) > CONTRADICTION_WINDOW:
                    continue
                if axis[at1.actor_role] + axis[at2.actor_role] == 0:
                    clashes.append({"ts": e1["ts"], "a": e1.get("label"), "b": e2.get("label"),
                                    "a_role": at1.actor_role, "b_role": at2.actor_role})
    role_reliable = not one_sided and not clashes
    return {
        "events": len(events),
        "actors": len(actors),
        "one_sided": one_sided,
        "contradictions": clashes[:6],
        "contradiction_count": len(clashes),
        "role_reliable": role_reliable,
        "perspective_reliable": not one_sided,
        # Exactly one reason, the first that applies, so the page prints a cause and not a list.
        "reason_code": ("one_sided" if one_sided else
                        "self_contradictory" if clashes else None),
        "window": CONTRADICTION_WINDOW,
        "min_events_for_one_sided": MIN_EVENTS_FOR_ONE_SIDED,
        "unused_side": b_id if a_id in actors and b_id not in actors else
                       a_id if b_id in actors and a_id not in actors else None,
    }


def attribute(event: Mapping[str, Any], subject_id: str, opponent_id: str,
              flags: Mapping[str, Any]) -> dict[str, Any]:
    """One event, seen from ONE athlete's corner.

    ``perspective`` is ``favor`` when the event is the subject's own and ``contra`` when it is
    her opponent's. That second half is not an inversion by elimination: a bout has exactly two
    people, the actor is named, and "Helena applied a single leg on Ane" is by construction a
    thing that happened TO Ane. What is never inverted is the ROLE -- the subject's own side of
    the event is `target_role`, which is set only where the label defines both sides.
    """
    at = classify(event.get("type"), event.get("label"))
    actor = event.get("actor_id")
    favor = actor == subject_id
    # Role as the SUBJECT experiences it: her own when the event is hers, the other side of it
    # when it is her opponent's.
    subject_role = at.actor_role if favor else at.target_role
    status = at.status
    if not flags.get("role_reliable"):
        status = "unknown"
    elif subject_role == UNKNOWN:
        status = "unknown"
    return {
        "label": event.get("label"),
        "type": event.get("type"),
        "ts": event.get("ts"),
        "successful": event.get("successful"),
        "actor_id": actor,
        # Derivable because a bout has two athletes. Used to name who the event happened to,
        # never to flip a role.
        "target_id": opponent_id if favor else subject_id,
        "perspective": "favor" if favor else "contra",
        "category": at.category,
        "relation": at.relation,
        "actor_role": at.actor_role,
        "target_role": at.target_role,
        "subject_role": subject_role if status in ("resolved", "symmetric") else UNKNOWN,
        "attribution_status": status,
        "attribution_confidence": "high" if flags.get("perspective_reliable") else "low",
        "rule_source": at.source,
    }


def is_event(ev: Mapping[str, Any]) -> bool:
    """Is this row a grappling event at all, as opposed to import bookkeeping?"""
    return (ev.get("type") or "").strip().lower() in EVENT_TYPES


def usable_perspective(ev: Mapping[str, Any]) -> bool:
    """May this event be counted as A FAVOR or CONTRA at all?

    No, when the bout filed every event under one athlete: there the actor field carries no
    information, so "hers" and "her opponent's" are not two things the data distinguishes.
    """
    return ev.get("attribution_confidence") == "high"


def usable_role(ev: Mapping[str, Any]) -> bool:
    """May this event carry a topology -- por cima / por baixo / controlando?

    Separate from the question above, and deliberately: a bout that contradicts itself may
    still name the right fighter on most rows while being unable to say which side of the
    position she was on. Losing the role is not a reason to lose the event.
    """
    return ev.get("attribution_status") in ("resolved", "symmetric")


def directional(ev: Mapping[str, Any]) -> bool:
    """Both of the above. The rule the whole module exists to enforce: an event whose
    attribution could not be resolved is kept, shown and counted in the totals, and excluded
    from anything with a direction."""
    return usable_perspective(ev) and usable_role(ev)


# ── sequence normalization ──────────────────────────────────────────────────────
def normalize_chain(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[str], dict[str, int | dict[str, int]]]:
    """A bout-side's event chain with consecutive repeats folded, for transition analysis only.

    A transition graph built straight off the raw rows is dominated by edges that say nothing.
    ``Half Guard -> Half Guard`` is not a transition: it is one occupancy of one position that
    the refiner logged three times, and every A -> A edge crowds out the A -> B edges the graph
    exists to show.

    **Only CONSECUTIVE repeats fold.** ``A -> B -> A`` is a real trajectory -- she was passed,
    recovered, was passed again -- and survives untouched. Two separate single-leg attempts
    with anything in between stay two attempts.

    The rule for two IDENTICAL rows sitting next to each other, and why it is what it is:

    ``state``     (guard, control) -- collapsed, and this is not a judgement call. A position
                  is an occupancy, not an act; being in half guard across three logged rows is
                  one visit to half guard.
    everything    (actions, transitions) -- **indeterminable, and folded anyway for the graph
    else          while the raw count is preserved everywhere else.** Two consecutive
                  ``Single Leg Takedown`` rows might be two attempts or one attempt logged
                  twice, and this corpus cannot tell: `successful` is present on 29% of events,
                  `matches.ts_origin` is NULL on all 864 rows, so a `ts` gap is a position in
                  an unanchored timeline and not a duration. **The only ordering signal that
                  exists is the array itself**, and array order cannot separate those two
                  cases. So the count survives -- `nodes`, `type_by_outcome` and every total
                  still see both rows -- and the PATH loses the self-loop, because an A -> A
                  edge is a claim about a transition that no reading of the data supports.

    The repetition is not thrown away: it comes back as `re_entries`, which is a fact about a
    node ("she went back to it") rather than an edge in a graph.

    Returns the folded chain of labels and the counters `sequence_normalization` publishes.
    """
    chain: list[str] = []
    prev_key: tuple[str, str] | None = None
    stats = {"raw": 0, "normalized": 0, "state_collapses": 0, "action_repeats_folded": 0}
    re_entries: Counter[str] = Counter()
    for e in events:
        label = str(e.get("label") or "")
        if not label:
            continue
        stats["raw"] += 1
        key = ((e.get("type") or "").lower(), label)
        if key == prev_key:
            # Same event, same row-neighbour: a self-loop and nothing else.
            if (e.get("category") or classify(e.get("type"), label).category) == STATE:
                stats["state_collapses"] += 1
            else:
                stats["action_repeats_folded"] += 1
            re_entries[label] += 1
            continue
        chain.append(label)
        prev_key = key
    stats["normalized"] = len(chain)
    stats["self_loops_removed"] = stats["state_collapses"] + stats["action_repeats_folded"]
    return chain, {**stats, "re_entries": dict(re_entries)}
