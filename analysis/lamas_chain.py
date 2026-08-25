"""Lamas et al. 2024's twelve-state action chain, rebuilt on this corpus.

Lamas, L., et al. (2024). *No-gi Brazilian jiu-jitsu: a Markovian analysis of elite-level
combat dynamics.* IJSSC, doi:10.1177/17479541231210979 -- 93 WSFC-2019 no-gi matches, the one
peer-reviewed BJJ Markov paper, and the only external number this corpus can be checked
against (``docs/research/04_BIBLIOGRAPHY.md``, section D). This module runs the paper's own
action-oriented analysis on our event sequences so the two matrices can sit side by side.

**The state space is the paper's, not ours.** Twelve states, six of them an attempt/success
pair::

    CDP    disputa de pegada em pé -- os dois de pé, buscando a queda ou a posição dominante
    PGD    puxada para a guarda -- senta ou deita puxando a adversária para dentro da guarda
    SWPA   tentativa de raspagem            SWP    raspagem (quem estava por baixo assume por cima)
    TKDA   tentativa de queda               TKD    queda
    GPSA   tentativa de passagem            GPS    passagem (para 100 kg / norte-sul)
    BTKA   tentativa de pegada de costas    BTK    pegada de costas
    SUBA   tentativa de finalização         SUB    finalização (a luta acaba)

Four rules are PRE-DECLARED here, before any number, because each of them is a place where a
different reading would produce a different matrix:

1. **Mapping is type-first.** The corpus's own ``type`` decides the four action families
   (``takedown``/``sweep``/``pass``/``submission``), and only events typed ``control``,
   ``transition`` or ``guard`` are read at the label level, for the three states the type
   vocabulary has no word for: PGD, BTK/BTKA and CDP. So ``takedown/Snapdown`` is a TKDA even
   though a snapdown is a clinch action -- the corpus typed it a takedown and the type wins.
   Measured collisions of this kind: 46 ``takedown/Snapdown|Snap Down``, 1
   ``sweep/Sweep · Back Take``, 2 ``takedown/Takedown to Back Take``, 1
   ``takedown/Takedown (Back Exposure)``, out of 9,985 corpus events.

2. **Unmapped events are PASSED OVER, not broken into.** Escapes, guard postures and dwell
   states (``Closed Guard``, ``Half Guard``, ``Turtle Position``, ``Escape to Turtle``,
   ``Crucifix``, ``Kimura Grip``), and transitions that are not back-takes, carry no Lamas
   action. The chain links the SURVIVING actions in bout order -- the paper's chains are
   action-to-action, so a guard posture between two actions is not a state, it is the pause
   between them. ``n_events_skipped`` publishes how much of the stream that is.

3. **Attempt vs success reads the ``successful`` flag, and ABSENT reads as ATTEMPT.**
   ``successful`` is present on 34.2% of the events in the scouting corpus (166/486) and 28.9%
   of the corpus at large. Every success rate this module reports is therefore a **LOWER
   BOUND**, and the distortion is not spread evenly: ``control/Back Control`` carries 77
   absent, 12 false and 2 true in the scouting set, so 89 of 91 back-controls -- a position
   that by its name has already been TAKEN -- land in BTKA. The alternative (reading a control
   STATE label as success by definition) is a different, defensible convention; it is not the
   one the owner specified, and mixing the two per state would make the matrix unreadable.
   The caveat travels with every export instead.

4. **SUB is absorbing, but the corpus's tap marker is the BOUT, not the event.** Measured: a
   bout Amy Campo lost on DECISION carries ``submission/Knee Bar successful=true`` at ts 8951
   and runs for seventeen more events -- ``successful`` on a submission means the lock was
   applied, not that anyone tapped. Truncating on the first ``successful`` submission would
   have cut that bout at event 0. So the chain truncates at the first SUB **only when the
   bout's ``win_type`` is SUBMISSION** (32 events in 4 bouts, scouting corpus). A SUB in a bout
   that ended any other way is a submission locked and escaped, and keeps its outgoing
   transitions -- ``sub_outgoing`` counts them rather than hiding them.

Two more choices, stated because they are contestable:

* **Chronology is ARRAY ORDER.** Measured on the scouting corpus: 39 of 40 bouts carry ``ts``
  on every event and NONE of them disagrees with the array; the fortieth carries no ``ts`` at
  all. Array order is also what ``analysis/attribution.py`` reads
  (``rule_code: consecutive_only_array_order``), so the two layers cannot drift apart.
* **Self-loops SURVIVE.** ``network_from_sequences`` drops the A -> A edge and
  ``normalize_chain`` folds consecutive repeats; both would delete exactly the cell Lamas
  publishes (guard pass -> guard pass, 0.30). Nothing is folded here.

**Cross-actor by default.** ``chain_of`` links the bout's actions whoever produced them,
because the paper's chain is the MATCH's flow: CDP is dyadic by definition (both athletes are
standing and disputing), and the dominance states carry their actor implicitly. The
within-actor reading -- ``analysis/poc/e4_ptv_eval.own_transitions``, which PoC-E4 used for
the same anchor -- is also available (``same_actor`` on every transition) and the anchor block
reports BOTH, because the three published cells are same-athlete statements. It is reported
rather than adopted for a measured reason: ``docs/match_event_model.md`` records 307 of 700
corpus bouts filing every event under one athlete, so ``actor_id`` is not trustworthy enough
to be the spine of the matrix. It is trustworthy enough to be a second opinion on three cells.

Privacy class **A, public competition data**: every input here is a ``matches`` row from
published footage. Nothing in this module reads a user graph or a session.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from analysis.names import _deaccent, _normalize_name
from analysis.stats_rigor import (
    MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE,
    Coverage,
    coverage,
    wilson,
)

# ── the state space, in the paper's order ───────────────────────────────────────
# Fixed, and every matrix ships square on it. A state absent from a division is a row and a
# column of zeroes, not a missing key: a renderer that has to guess which states exist cannot
# draw two divisions on the same grid.
STATES: tuple[str, ...] = (
    "CDP", "PGD", "SWPA", "SWP", "TKDA", "TKD", "GPSA", "GPS", "BTKA", "BTK", "SUBA", "SUB",
)
STATE_INDEX = {s: i for i, s in enumerate(STATES)}

# Reader-facing, in the language BracketAnalysis renders. The page must not carry its own
# glossary: a definition that lives in the renderer is a definition that can disagree with the
# mapping that produced the number.
STATE_DEFS: dict[str, str] = {
    "CDP": "Disputa de pegada — as duas em pé, buscando a queda ou a posição dominante.",
    "PGD": "Puxada para a guarda — senta ou deita puxando a adversária para dentro da guarda.",
    "SWPA": "Tentativa de raspagem.",
    "SWP": "Raspagem — quem estava por baixo assume o por cima.",
    "TKDA": "Tentativa de queda.",
    "TKD": "Queda.",
    "GPSA": "Tentativa de passagem de guarda.",
    "GPS": "Passagem de guarda — chega aos 100 kg ou ao norte-sul.",
    "BTKA": "Tentativa de pegada de costas.",
    "BTK": "Pegada de costas.",
    "SUBA": "Tentativa de finalização.",
    "SUB": "Finalização — a luta acaba.",
}
# The pair each family collapses to when attempt and success are read together. Lamas'
# published cells are stated at THIS level ("back control -> submission"), not split.
FAMILY: dict[str, tuple[str, ...]] = {
    "sweep": ("SWPA", "SWP"),
    "takedown": ("TKDA", "TKD"),
    "pass": ("GPSA", "GPS"),
    "back": ("BTKA", "BTK"),
    "submission": ("SUBA", "SUB"),
}

# ── the mapping table ───────────────────────────────────────────────────────────
# Type first. These four families are named by the corpus's own event type, so no label
# reading is involved and no label can be claimed by two rules.
TYPE_ACTION: dict[str, tuple[str, str]] = {
    "takedown": ("TKDA", "TKD"),
    "sweep": ("SWPA", "SWP"),
    "pass": ("GPSA", "GPS"),
    "submission": ("SUBA", "SUB"),
}
# Only these types are read at the label level. `escape` is deliberately absent: an escape is
# not a Lamas action, and admitting the type would let `escape/Back Escape` match the back-take
# vocabulary on the word "back" -- the exact class of false positive the type gate exists to
# stop.
LABEL_TYPES: frozenset[str] = frozenset({"control", "transition", "guard"})

# Tokens are matched against `_key()`, i.e. de-accented, lower-cased, punctuation stripped --
# which is why "twoonone" and "snapdown" appear in that form. The lists were built by
# enumerating the corpus (339 distinct type/label pairs, 9,985 events, read-only, 2026-08-25),
# not from memory; every entry below is a label that exists.
BACK_TAKE_TOKENS: tuple[str, ...] = (
    "back take",        # transition/Back Take (17), control/Arm Drag to Back Take (2),
                        # control/Crab Ride to Back Take (1)
    "back control",     # control/Back Control (1964), control/Standing Back Control (1)
    "hooks in",         # transition/Hooks In (15), control/Hooks In (3)
    "body triangle",    # control/Body Triangle (87); "(Bottom)" is overridden out below
    "rear body lock",   # control/Rear Body Lock (3) -- behind her, back exposed
)
GUARD_PULL_TOKENS: tuple[str, ...] = (
    "guard pull",       # guard/Guard Pull (99), transition/Guard Pull (87),
                        # guard/Double Guard Pull (3)
    "pull guard",       # guard/Pull Guard / Inversion (24), guard/Pull Guard (4),
                        # transition/Pull Guard / Sit Guard (1)
    "pull half guard",  # guard/Pull Half Guard (2)
    "pull closed guard",  # guard/Pull Closed Guard (1)
)
CLINCH_TOKENS: tuple[str, ...] = (
    "collar tie",       # control/Collar Tie (41)
    "body lock",        # control/Body Lock (68) -- standing body lock
    "front headlock",   # control/Front Headlock (139)
    "clinch",           # transition/Clinch (4), control/Clinch Knees (1)
    "russian tie",      # control/Russian Tie (2), transition/Russian Tie (1)
    "twoonone",         # control/Two-on-One Control (3), .../Two-on-One Wrist Control (2+2)
    "underhook",        # control/Underhook (3), control/Double Underhooks (5),
                        # transition/Underhook (7)
    "overhook",
    "wrist control",
    "arm drag",         # control/Arm Drag (2), transition/Arm Drag (8) -- the 203 typed
                        # `takedown` are TKD by rule 1
    "snapdown",         # transition/Snapdown (2); the 44+42 typed `takedown` are TKD
    "snap down",
    "duck under",       # transition/Duck Under (8); the 22 typed `takedown` are TKD
)
# Measured collisions where a token would claim a label it has no business claiming. Same
# device as `scripts/bracket_export.METHOD_FAMILY`: an explicit entry beats the token list, and
# the token list stays for labels this table has not seen. Keys are `_key()` output.
LABEL_OVERRIDES: dict[str, str | None] = {
    # A body lock held from the top on the ground is not a standing clinch dispute.
    "top control body lock": None,
    # `control/Body Triangle (Bottom)` (2) is the person UNDER a body triangle, i.e. the
    # opposite of a back-take. The unqualified `control/Body Triangle` (87) is the back-taker's.
    "body triangle bottom": None,
}
# Deliberate non-members, listed so the omission is a decision rather than an oversight:
#   Crucifix / Mounted Crucifix (34)  -- back-adjacent, but not a back TAKE in Lamas' space
#   Kimura Grip (4)                   -- a grip, standing or on the ground; the corpus does
#                                        not say which, and CDP is defined as "both standing"
#   Turtle Position / Turtle Control / Escape to Turtle (75) -- a dwell state, per rule 2
#   every guard posture (Closed/Half/Butterfly/K/De la Riva/50-50/...) -- per rule 2

CAVEATS: tuple[str, ...] = (
    "`successful` está presente em apenas 28,9% dos eventos do corpus (34,2% no recorte do "
    "scouting). A ausência é lida como TENTATIVA, então toda taxa de sucesso aqui é um PISO, "
    "não uma estimativa — e o desvio não é uniforme: 89 dos 91 eventos `Back Control` do "
    "recorte caem em BTKA, uma posição que pelo próprio nome já foi conquistada.",
    "SUB é absorvente pelo RESULTADO da luta, não pelo evento: a cadeia só é truncada na "
    "primeira SUB quando `win_type` é SUBMISSION. `successful=true` numa finalização significa "
    "que a chave foi encaixada, não que houve toque — há luta vencida por decisão com dois "
    "`Knee Bar successful=true` no meio. As SUBs com saída estão contadas em `sub_outgoing`.",
    "Eventos sem ação de Lamas (fugas, posturas de guarda, estados de permanência, transições "
    "que não são pegada de costas) são PULADOS: a cadeia liga as ações sobreviventes na ordem "
    "da luta. `n_events_skipped` e `skipped_top` dizem quanto do fluxo isso é.",
    "`Front Headlock` é a segunda maior contribuição para CDP (139 eventos no corpus) e pode "
    "ser em pé ou sobre a adversária de quatro. O corpus não registra essa distinção, então "
    "a inclusão não é resolvível — é a maior ambiguidade da tabela de mapeamento.",
    "Matriz CRUZADA entre atletas: a cadeia é o fluxo da luta, não o de uma atleta. As três "
    "células publicadas por Lamas são afirmações sobre a MESMA atleta, então o bloco `anchor` "
    "reporta as duas leituras. A leitura por atleta depende de `actor_id`, e 307 de 700 lutas "
    "do corpus arquivam todos os eventos sob uma única atleta.",
    "Primeira ordem, sem suavização. Uma célula com n baixo tem intervalo largo por "
    "construção; abaixo do corte de cobertura por luta o intervalo é retirado e só a contagem "
    "observada permanece.",
)

# The paper's published cells, verbatim from `analysis/poc/e4_ptv_eval.LAMAS_PUBLISHED` so the
# two runners cannot drift. Stated at family level, which is how the paper states them.
ANCHOR_CELLS: tuple[tuple[str, str, str, float], ...] = (
    ("back control → submission", "back", "submission", 0.45),
    ("takedown → submission", "takedown", "submission", 0.15),
    ("guard pass → guard pass", "pass", "pass", 0.30),
)

PATHWAY_LENGTH = 3      # two transitions into a submission; length 2 IS the matrix's SUB column
PATHWAY_TOP = 5


def _key(label: Any) -> str:
    """The corpus's own label normalisation, so this module cannot fold differently from the
    rest of the repo. `_deaccent` first because `_normalize_name` DELETES an accented character
    rather than folding it."""
    return _normalize_name(_deaccent(str(label or "")))


def lamas_state(event: Mapping[str, Any]) -> str | None:
    """One event → one Lamas action code, or ``None`` when the event carries no action.

    Rules 1 and 3 of the module docstring, in order: type first, then label, and
    ``successful is True`` is the only thing that earns a success code.
    """
    typ = str(event.get("type") or "").strip().lower()
    landed = event.get("successful") is True
    pair = TYPE_ACTION.get(typ)
    if pair is not None:
        return pair[1] if landed else pair[0]
    if typ not in LABEL_TYPES:
        return None
    label = _key(event.get("label"))
    if not label:
        return None
    if label in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[label]
    if any(t in label for t in BACK_TAKE_TOKENS):
        return "BTK" if landed else "BTKA"
    if any(t in label for t in GUARD_PULL_TOKENS):
        return "PGD"
    if any(t in label for t in CLINCH_TOKENS):
        return "CDP"
    return None


class Step(NamedTuple):
    state: str
    actor_id: Any


class Chain(NamedTuple):
    bout_id: str
    steps: list[Step]
    mapped: int
    skipped: int
    skipped_labels: Counter[str]
    after_finish: int       # events dropped by the absorbing rule
    truncated: bool


def chain_of(bout: Mapping[str, Any]) -> Chain:
    """The bout's Lamas actions, in array order, truncated at the finishing SUB.

    Rules 2 and 4. ``skipped_labels`` keeps what was passed over so the coverage note is a
    measurement rather than an assurance.
    """
    seq: Sequence[Mapping[str, Any]] = bout.get("seq") or []
    finishes = str(bout.get("win_type") or "").strip().upper() == "SUBMISSION"
    steps: list[Step] = []
    skipped_labels: Counter[str] = Counter()
    mapped = skipped = 0
    truncated = False
    for i, e in enumerate(seq):
        state = lamas_state(e)
        if state is None:
            skipped += 1
            skipped_labels[f"{e.get('type') or '?'}/{e.get('label') or '?'}"] += 1
            continue
        mapped += 1
        steps.append(Step(state, e.get("actor_id")))
        if finishes and state == "SUB":
            truncated = True
            return Chain(str(bout.get("id")), steps, mapped, skipped, skipped_labels,
                         len(seq) - i - 1, True)
    return Chain(str(bout.get("id")), steps, mapped, skipped, skipped_labels, 0, truncated)


class Transition(NamedTuple):
    src: str
    dst: str
    bout_id: str
    same_actor: bool


def transitions(chains: Iterable[Chain]) -> list[Transition]:
    """Every adjacent pair of actions, cross-actor, with the within-actor flag attached.

    Self-loops survive on purpose (module docstring). ``same_actor`` is ``False`` whenever
    either side has no ``actor_id``, so a missing actor can never be counted as agreement.
    """
    out: list[Transition] = []
    for ch in chains:
        for a, b in zip(ch.steps, ch.steps[1:]):
            same = a.actor_id is not None and a.actor_id == b.actor_id
            out.append(Transition(a.state, b.state, ch.bout_id, same))
    return out


def _cell(k: int, n: int, cov: Coverage) -> dict[str, Any]:
    """A cell estimate, or the count with the interval withheld.

    Same rule as everywhere else in this report (`scripts/bracket_export.gated`): the observed
    proportion is a fact about the corpus and survives; the INTERVAL is a claim about a
    population, and below the cluster gate the row is describing a handful of fights rather
    than sampling a division. Duplicated here rather than imported because `analysis/` must not
    depend on `scripts/`.
    """
    d = wilson(k, n).to_dict()
    if cov.estimable:
        return {**d, "estimable": True, "coverage": cov.grade}
    return {**d, "lo": None, "hi": None, "half": None, "grade": "none", "estimable": False,
            "coverage": cov.grade, "reason_code": cov.reason_code, "clusters": cov.clusters,
            "min_clusters": MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE}


def _matrix(trans: Sequence[Transition]) -> dict[str, Any]:
    """Counts, row-normalised probabilities and per-cell intervals, square on ``STATES``."""
    counts = [[0] * len(STATES) for _ in STATES]
    per_row_bouts: dict[str, Counter[str]] = defaultdict(Counter)
    for t in trans:
        counts[STATE_INDEX[t.src]][STATE_INDEX[t.dst]] += 1
        per_row_bouts[t.src][t.bout_id] += 1

    probs: list[list[float | None]] = []
    ci: list[list[dict[str, Any]]] = []
    rows: list[dict[str, Any]] = []
    for i, src in enumerate(STATES):
        total = sum(counts[i])
        # Clusters are BOUTS. Two hundred transitions out of one fight are not two hundred
        # independent observations of a division, and the coverage gate is the only thing in
        # this repo that says so.
        cov = coverage(list(per_row_bouts[src].values()))
        probs.append([round(c / total, 4) if total else None for c in counts[i]])
        ci.append([_cell(c, total, cov) for c in counts[i]])
        rows.append({"state": src, "n": total, "bouts": len(per_row_bouts[src]),
                     "coverage": cov.to_dict()})
    return {"counts": counts, "probs": probs, "ci": ci, "rows": rows}


def _occupancy(chains: Sequence[Chain]) -> list[dict[str, Any]]:
    c: Counter[str] = Counter()
    per_bout: dict[str, Counter[str]] = defaultdict(Counter)
    for ch in chains:
        for s in ch.steps:
            c[s.state] += 1
            per_bout[s.state][ch.bout_id] += 1
    total = sum(c.values())
    return [{"state": s, "k": c[s], "bouts": len(per_bout[s]),
             "share": _cell(c[s], total, coverage(list(per_bout[s].values())))}
            for s in STATES]


def pathways_to_sub(chains: Sequence[Chain], probs: Sequence[Sequence[float | None]],
                    length: int = PATHWAY_LENGTH, top: int = PATHWAY_TOP) -> list[dict[str, Any]]:
    """The most frequent contiguous routes of ``length`` actions ending in a submission.

    Terminal is SUB **or** SUBA, because rule 3 puts most real finishes in SUBA: requiring the
    success code would rank the paper's "dominant submission pathways" off a handful of events.
    Length 2 is deliberately not reported -- it is the matrix's own submission column, and
    printing it twice would let a reader take one table as corroboration of the other.

    Ranked by observed count, then by chain probability, then by the path itself, so the order
    is total and a rebuild cannot reshuffle ties. ``p_chain`` is the product of the row-
    normalised cells, i.e. what the first-order model says the route is worth GIVEN its start;
    ``bouts`` is how many distinct fights actually contained it, and that interval is
    bout-clustered by construction because the bout is the unit being counted.
    """
    seen: Counter[tuple[str, ...]] = Counter()
    bouts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for ch in chains:
        states = [s.state for s in ch.steps]
        for i in range(len(states) - length + 1):
            path = tuple(states[i:i + length])
            if path[-1] not in ("SUB", "SUBA"):
                continue
            seen[path] += 1
            bouts[path].add(ch.bout_id)
    n_bouts = len(chains)

    def p_chain(path: tuple[str, ...]) -> float | None:
        p = 1.0
        for a, b in zip(path, path[1:]):
            cell = probs[STATE_INDEX[a]][STATE_INDEX[b]]
            if cell is None:
                return None
            p *= cell
        return round(p, 6)

    ranked = sorted(seen.items(),
                    key=lambda kv: (-kv[1], -(p_chain(kv[0]) or 0.0), kv[0]))
    return [{"path": list(path), "label": " → ".join(path), "k": k,
             "bouts": len(bouts[path]),
             "p_chain": p_chain(path),
             "bout_rate": wilson(len(bouts[path]), n_bouts).to_dict()}
            for path, k in ranked[:top]]


def anchor(trans: Sequence[Transition]) -> list[dict[str, Any]]:
    """Our three cells beside Lamas 2024's published values, in both chain conventions.

    Stated at FAMILY level (attempt and success collapsed), because that is how the paper
    states them -- "back control → submission" is not a claim about a landed back-take -- and
    because rule 3 would otherwise put 89 of 91 back-controls on the attempt side and compare
    a cell the paper never published.

    ``cross`` is this module's matrix. ``within`` is `analysis/poc/e4_ptv_eval.own_transitions`'
    convention, kept as a second opinion rather than the spine: `actor_id` is unreliable in
    a large minority of corpus bouts. Neither is a held-out prediction -- this is descriptive,
    on the whole division, and it never enters a criterion (ADR-03).

    ``no_reentry`` is the third arm and it exists because of one measured asymmetry. PoC-E4
    read these cells off the RAW label vocabulary and missed on two of three, attributing the
    misses to a finer state space diluting every cell (``docs/research/poc/e4.md``). Collapsing
    to Lamas' twelve states is exactly the mapping that argument needed, and it recovers guard
    pass → guard pass (0.079 raw → 0.235 / 0.318 here, both covering 0.30). Back control →
    submission does NOT recover, and the reason is visible in the matrix: 39% and 46% of
    everything leaving a back-control goes to ANOTHER back-control event. Our corpus logs a
    held position repeatedly; Lamas' coding occupies the state once. So this arm drops
    same-family re-entries from the denominator -- the closest thing to his coding our events
    can be read into. It is a DIAGNOSTIC on the gap, not a competing estimate, and it is
    undefined where the paper's own cell is a self-transition.
    """
    out: list[dict[str, Any]] = []
    for name, src_fam, dst_fam, published in ANCHOR_CELLS:
        src, dst = set(FAMILY[src_fam]), set(FAMILY[dst_fam])
        row: dict[str, Any] = {"name": name, "lamas": published,
                               "src_states": sorted(src, key=STATES.index),
                               "dst_states": sorted(dst, key=STATES.index)}
        arms: list[tuple[str, list[Transition]]] = [
            ("cross", list(trans)),
            ("within", [t for t in trans if t.same_actor]),
        ]
        if src_fam != dst_fam:
            arms.append(("no_reentry", [t for t in trans if t.dst not in src]))
        for arm, pool in arms:
            from_src = [t for t in pool if t.src in src]
            k = sum(1 for t in from_src if t.dst in dst)
            e = wilson(k, len(from_src))
            row[arm] = {**e.to_dict(),
                        "agrees": bool(e.lo is not None and e.hi is not None
                                       and e.lo <= published <= e.hi)}
        if src_fam == dst_fam:
            row["no_reentry"] = {"available": False,
                                 "reason_code": "published_cell_is_the_reentry"}
        out.append(row)
    return out


def markov_block(bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Everything one division publishes: the matrix, its occupancy, its routes, its anchor.

    ``bouts`` is a division's bout set as the caller already built it -- this module does not
    select fights, so the markov section can never describe a different universe from the
    sequence section beside it.
    """
    chains = [chain_of(b) for b in bouts]
    trans = transitions(chains)
    m = _matrix(trans)
    skipped: Counter[str] = Counter()
    for ch in chains:
        skipped.update(ch.skipped_labels)
    sub_out = sum(1 for t in trans if t.src == "SUB")
    return {
        "states": [{"code": s, "definition": STATE_DEFS[s]} for s in STATES],
        "counts": m["counts"],
        "probs": m["probs"],
        "ci": m["ci"],
        "rows": m["rows"],
        "occupancy": _occupancy(chains),
        "pathways_to_sub": pathways_to_sub(chains, m["probs"]),
        "anchor": anchor(trans),
        "n_bouts": len(bouts),
        "n_transitions": len(trans),
        "n_events_mapped": sum(ch.mapped for ch in chains),
        "n_events_skipped": sum(ch.skipped for ch in chains),
        "skipped_top": skipped.most_common(12),
        "absorbed": {"bouts_truncated": sum(1 for ch in chains if ch.truncated),
                     "events_after_finish": sum(ch.after_finish for ch in chains),
                     "sub_outgoing": sub_out,
                     "rule_code": "truncate_first_sub_when_win_type_submission"},
        "within_actor_transitions": sum(1 for t in trans if t.same_actor),
        "order": 1,
        "chain": "cross_actor",
        "chronology": "array_order",
        "caveats": list(CAVEATS),
        "source": "Lamas et al. 2024, doi:10.1177/17479541231210979",
    }


def _demo() -> None:
    """One runnable check of the non-trivial logic, no framework."""
    bout = {"id": "b1", "win_type": "SUBMISSION", "seq": [
        {"type": "control", "label": "Collar Tie"},                      # CDP
        {"type": "takedown", "label": "Single Leg Takedown", "successful": True},   # TKD
        {"type": "guard", "label": "Half Guard"},                        # skipped
        {"type": "pass", "label": "Knee Cut Pass"},                      # GPSA
        {"type": "control", "label": "Back Control"},                    # BTKA
        {"type": "submission", "label": "Rear Naked Choke", "successful": True},    # SUB
        {"type": "submission", "label": "Tap", "successful": True},      # after the finish
    ]}
    ch = chain_of(bout)
    assert [s.state for s in ch.steps] == ["CDP", "TKD", "GPSA", "BTKA", "SUB"], ch.steps
    assert (ch.mapped, ch.skipped, ch.after_finish) == (5, 1, 1), ch
    assert lamas_state({"type": "escape", "label": "Back Escape"}) is None
    assert lamas_state({"type": "control", "label": "Top Control (Body Lock)"}) is None
    assert lamas_state({"type": "takedown", "label": "Snapdown"}) == "TKDA"
    block = markov_block([bout])
    for row in block["probs"]:
        s = sum(p for p in row if p)
        assert s == 0 or abs(s - 1.0) < 1e-9, row
    print("lamas_chain self-check ok")


if __name__ == "__main__":
    _demo()
