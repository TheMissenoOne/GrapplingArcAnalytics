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

**Reward-risk** (``reward_risk``) is the one layer here that DOES depend on ``actor_id``, and it
inherits its convention from ``analysis/transitions/build_graph.py`` /
``network_metrics.reward_risk_ranking`` rather than inventing one: same denominator rule
(appearances that have a successor), two disjoint rates, unknown attribution never charged,
composite = reward − risk. What is translated is the event class, not the structure — see that
function's docstring. Because the metric stands entirely on the actor field, bouts are REFUSED
before they enter it (``_actor_reliability``), which the matrix never needs to do.

Two more layers stand on the same chain and answer different questions off it. **RRB**
(``rrb``) restores Lamas' original submission anchor by PROPAGATING it: the twelve states are
lifted by side (24 sided states, the side flipping exactly when the exchange changes hands),
and the chain is solved as an absorbing one with three destinations -- her finish, the
opponent's finish, and no finish at all -- so a state is scored by where its chains END rather
than by one sparse immediate cell. **``chain_factor``** asks whether an action induces a RUN:
the probability that the two actions after it are both hers. Both depend on ``actor_id`` at
least as heavily as ``reward_risk`` does and pass through the same refusal, and ``rrb`` adds a
gate of its own on the number of bouts that actually absorb -- four to six per corpus, and
ZERO in the ADCC 2024 corpus, which is why that corpus publishes no absorption estimate at all.

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
    bootstrap_ci,
    compare_proportions,
    coverage,
    grade,
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
# Cluster bootstrap draws for the reward-risk composite. `bootstrap_ci` is deterministic by
# seed, so this is a precision knob and not a source of run-to-run drift.
N_BOOT = 2000

REWARD_RISK_CAVEATS: tuple[str, ...] = (
    "Esta é a ÚNICA camada do bloco que depende de `actor_id`. A matriz é cruzada entre "
    "atletas e não lê esse campo; aqui ele é o instrumento inteiro. Por isso as lutas passam "
    "por uma recusa antes de entrar (`bouts_refused`): `one_sided` é o veredito do próprio "
    "corpus (307 de 700 lutas arquivam tudo sob uma atleta) e `single_actor` fecha o buraco "
    "que ele deixa — luta curta com um só nome pontuaria reward 1,00 por construção.",
    "A convenção de dono do evento é `actor` = a atleta de cujo JOGO o nó é, não quem está "
    "ganhando a troca (docs/match_event_model.md): um nó de guarda é da guardeira, a passagem "
    "é da passadora. Cada evento arquivado fora dessa convenção troca um reward por um risco "
    "aqui. É a maior fonte de erro desta tabela e não é corrigível só a partir dos eventos.",
    "Aparição sem sucessora fica FORA do denominador (convenção de build_graph): um estado que "
    "encerra a cadeia — toda SUB que finaliza a luta, por exemplo — não é pontuado.",
    "Atribuição desconhecida não é cobrada de nenhum lado e permanece no denominador, então um "
    "estado cujas sucessoras são majoritariamente não atribuíveis tende a score 0. Isso é "
    "proposital: falta de atribuição deve ler como AUSÊNCIA de afirmação, não como afirmação.",
    "O score é uma diferença de duas taxas e não tem forma fechada; o intervalo vem de "
    "bootstrap percentil AGRUPADO POR LUTA. Abaixo do corte de cobertura nenhum intervalo é "
    "publicado — nem o de Wilson dos braços, nem o do score.",
)


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
    # Whether THIS bout's `actor_id` field can carry a directional reading at all. Only the
    # reward-risk layer reads it -- the matrix is cross-actor and does not depend on the actor
    # field. `None` when the bout carried no `a_id`/`b_id` to check against.
    actor_reliable: bool | None = None
    actor_refusal: str | None = None
    # How this bout ENDED, relative to the chain's last actor -- `"self"` when the athlete who
    # produced the last action is the one who won by submission, `"other"` when the finish is
    # the opponent's, `None` when the bout did not end in a submission (or the result is not
    # readable). Only `rrb` reads it; see `_absorbing_side`.
    absorbs: str | None = None


def _absorbing_side(bout: Mapping[str, Any], steps: Sequence[Step]) -> str | None:
    """Did this bout end in a submission, and was it the LAST ACTOR's or the opponent's?

    Rule 4 of the module docstring says the tap marker is the BOUT, not the event. This is that
    rule carried one step further, to the question the matrix never had to ask -- *whose*
    finish. Two decisions, both measured on this corpus (read-only, 2026-08-25):

    **The side comes from ``winner``, never from the finishing event's ``actor_id``.** Of the 24
    chains the ADCC cycle truncates at a flagged ``SUB``, **seven** file that submission under
    the athlete who LOST the bout (six of them in the Trials corpus alone; two in +65 kg, one in
    the Worlds corpus, none in 65 kg). Reading the side off the event would hand a third of the
    cycle's finishes to the wrong athlete. The bout result is the least actor-dependent evidence
    available and it decides.

    **A submission-won bout absorbs even when the chain never reached a flagged ``SUB``.** The
    absorbing transition hangs on the chain's LAST step whatever that step is, because the fact
    being recorded is "this bout ended in a submission by W", which is true of the bout and not
    of any one event. Measured: 2 such bouts in 65 kg, 1 in +65 kg, 2 in the Worlds corpus, 0 in
    the Trials corpus. Excluding them would systematically undercount finishes, and the
    alternative -- requiring the flag -- is exactly the naive rule §1.5 already refused.

    ``None`` (i.e. absorb into the third state, "no-sub end") whenever the bout was not won by
    submission, carries no ``winner``, or ends on a step with no actor. A missing fact is never
    read as a finish.
    """
    if str(bout.get("win_type") or "").strip().upper() != "SUBMISSION":
        return None
    winner = bout.get("winner")
    if winner is None or not steps or steps[-1].actor_id is None:
        return None
    return "self" if str(steps[-1].actor_id) == str(winner) else "other"


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
    after = 0
    for i, e in enumerate(seq):
        state = lamas_state(e)
        if state is None:
            skipped += 1
            skipped_labels[f"{e.get('type') or '?'}/{e.get('label') or '?'}"] += 1
            continue
        mapped += 1
        steps.append(Step(state, e.get("actor_id")))
        if finishes and state == "SUB":
            truncated, after = True, len(seq) - i - 1
            break
    reliable, refusal = _actor_reliability(bout, steps)
    return Chain(str(bout.get("id")), steps, mapped, skipped, skipped_labels, after, truncated,
                 reliable, refusal, _absorbing_side(bout, steps))


def _actor_reliability(bout: Mapping[str, Any],
                       steps: Sequence[Step]) -> tuple[bool | None, str | None]:
    """Can this bout's ``actor_id`` support a REWARD-RISK reading? Two refusals, both measured.

    ``one_sided``     `analysis.attribution.bout_flags`' own verdict (`perspective_reliable`) --
                      every event of a bout with >= 6 events filed under one athlete, 43.9% of
                      the corpus. The field carries no information there.
    ``single_actor``  the mapped CHAIN names fewer than two athletes. This is not redundant with
                      the first: `bout_flags` only calls a bout one-sided at >= 6 events, so a
                      short bout filed entirely under one name passes it while scoring
                      reward = 1.00 by construction. Measured on the scouting corpus, this
                      catches 7 and 8 bouts per division against the first rule's 1 and 3 --
                      it is the bigger hole, and leaving it open would have handed the metric a
                      pile of free reward.

    The matrix does NOT consult this. It is cross-actor and never reads `actor_id`; only
    reward-risk does, which is why the refusal lives here rather than in the bout selection.
    """
    a_id, b_id = bout.get("a_id"), bout.get("b_id")
    if a_id is None or b_id is None:
        return None, "no_sides_recorded"
    from analysis.attribution import bout_flags
    if not bout_flags(bout.get("seq") or [], str(a_id), str(b_id))["perspective_reliable"]:
        return False, "one_sided"
    if len({s.actor_id for s in steps if s.actor_id is not None}) < 2:
        return False, "single_actor"
    return True, None


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


def reward_risk(chains: Sequence[Chain], n_boot: int = N_BOOT) -> dict[str, Any]:
    """Per-state reward-risk in the Lamas action space, translated from `build_graph`.

    **The convention it inherits.** ``analysis/transitions/build_graph.py`` scores each node
    (Lamas et al. 2024) over *appearances that have a successor* -- a node that simply ends the
    recorded sequence is out of the denominator -- with two disjoint rates on that one
    denominator, unknown attribution charged to neither, composed as
    ``(reward - risk) / denom``. ``network_metrics.reward_risk_ranking`` then orders nodes by
    that composite. All four properties are kept here verbatim: same denominator rule, two
    disjoint rates, unknown never charged, difference-of-rates composite.

    **What is translated.** `build_graph` anchors both arms on a *finished submission* -- own
    next action is a landed sub (reward) vs the very next event is the OPPONENT's landed sub
    (risk). That anchor does not survive the move to this state space: rule 3 of the module
    docstring puts almost every real finish in ``SUBA``, leaving 10 and 14 ``SUB`` events per
    division, so a submission-anchored numerator would be 0-3 for nearly every state and the
    table would measure the corpus's `successful` coverage rather than the grappling.

    So the *event class* widens while the structure stays. Every action that survives into a
    Lamas chain is an ATTACKING action by construction (rule 2 skips escapes, guard postures
    and dwell states), so "did the exchange advance" reduces to **who acts next**:

        reward(s) = P(the next action is by the SAME athlete   | appearance of s with a successor)
        risk(s)   = P(the next action is by the OPPONENT       | same denominator)
        score(s)  = reward(s) - risk(s)          # == (reward_k - risk_k) / denom, build_graph's

    An appearance whose own actor or whose successor's actor is unknown scores neither and
    stays in the denominator -- `build_graph`'s "left neutral, never charged". That is
    deliberate: it pulls a state whose successors are mostly unattributable toward score 0, so
    missing attribution reads as *no claim* rather than as a strong one.

    **Actor noise is handled by refusal, not by a footnote.** This metric is built entirely on
    ``actor_id``, which is exactly the field `docs/match_event_model.md` measured as
    uninformative in 307 of 700 corpus bouts, and which §1.3 rule 4 refuses to build the matrix
    on. A bout that files every event under one athlete would score reward = 1.00 and risk =
    0.00 for every state in it. So bouts are gated by `_actor_reliability` BEFORE they enter
    this layer (`bouts_refused` says how many and why), and the surviving denominator is
    reported per state.

    Also note the guard/pass ownership convention this corpus writes under -- ``actor`` is the
    fighter whose GAME the node belongs to, not who is winning the exchange
    (`docs/match_event_model.md`). It is a documented convention that entry paths have violated
    before, and every event it mis-files flips a reward into a risk here. It is the single
    largest source of error in this table and it is not correctable from the events alone.

    **Intervals.** Wilson on each arm (both are binomial over one denominator). The composite
    has no closed form, so it gets `stats_rigor.bootstrap_ci` over per-appearance values of
    +1 / -1 / 0 -- whose mean IS the composite -- **clustered on the bout**, which is the right
    resampling unit because appearances inside one fight are not independent. Everything is
    gated on the same bout-cluster `coverage` as the matrix cells, and below the gate the counts
    survive while every interval is withheld (`bootstrap_ci`'s own docstring: gate first).
    """
    from statistics import fmean

    usable = [ch for ch in chains if ch.actor_reliable]
    refused: Counter[str] = Counter()
    for ch in chains:
        if not ch.actor_reliable:
            refused[ch.actor_refusal or "unknown"] += 1

    vals: dict[str, list[float]] = defaultdict(list)
    bouts: dict[str, list[str]] = defaultdict(list)
    for ch in usable:
        for a, b in zip(ch.steps, ch.steps[1:]):
            if a.actor_id is None or b.actor_id is None:
                v = 0.0                       # unknown: neutral, never charged
            else:
                v = 1.0 if a.actor_id == b.actor_id else -1.0
            vals[a.state].append(v)
            bouts[a.state].append(ch.bout_id)

    rows: list[dict[str, Any]] = []
    for s in STATES:
        scored, bl = vals[s], bouts[s]
        denom = len(scored)
        r_k = sum(1 for x in scored if x > 0)
        k_k = sum(1 for x in scored if x < 0)
        cov = coverage(list(Counter(bl).values()))
        score = round((r_k - k_k) / denom, 4) if denom else None
        lo: float | None = None
        hi: float | None = None
        if cov.estimable and denom and n_boot:
            _, b_lo, b_hi = bootstrap_ci(scored, fmean, n_boot=n_boot, groups=bl)
            lo, hi = round(b_lo, 4), round(b_hi, 4)
        rows.append({
            "state": s, "n": denom, "bouts": len(set(bl)),
            "reward": _cell(r_k, denom, cov),
            "risk": _cell(k_k, denom, cov),
            "neutral": denom - r_k - k_k,
            "score": score, "score_lo": lo, "score_hi": hi,
            "gated": cov.estimable,
            "coverage": cov.to_dict(),
        })
    # Estimable rows first, then best score, then the fixed state order. A denom-of-one state
    # scoring 1.00 would otherwise top a table the gate exists to keep it off.
    rows.sort(key=lambda r: (not r["gated"], -(r["score"] if r["score"] is not None else -9),
                             STATE_INDEX[r["state"]]))
    return {
        "rows": rows,
        "method": ("reward = P(próxima ação é da MESMA atleta); risco = P(é da adversária); "
                   "score = reward − risco, sobre as aparições que têm sucessora. Convenção "
                   "herdada de analysis/transitions/build_graph.py (Lamas et al. 2024): mesmo "
                   "denominador, duas taxas disjuntas, atribuição desconhecida nunca é "
                   "cobrada, composição por diferença."),
        "bouts_used": len(usable),
        "bouts_refused": dict(refused),
        "n_appearances": sum(len(v) for v in vals.values()),
        "boot": {"n": n_boot, "unit": "bout", "kind": "cluster percentile"},
        "caveats": list(REWARD_RISK_CAVEATS),
    }


def reward_risk_comparison(a_rows: Sequence[Mapping[str, Any]],
                           b_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """65 kg against +65 kg, one row per state, in the fixed state order.

    ``delta`` is published for every state that HAS a score on both sides, and
    ``both_estimable`` says whether either division earned an interval — a delta between two
    refused cells is arithmetic, not evidence, and the flag is what stops a renderer treating
    the two the same. The ``contrast`` (Agresti-Caffo difference + interval + p-value, via
    `stats_rigor.compare_proportions`) is computed on the REWARD arm only: it is the one
    genuine proportion here, and risk is its mirror once the neutral share is fixed.
    """
    a = {r["state"]: r for r in a_rows}
    b = {r["state"]: r for r in b_rows}
    out: list[dict[str, Any]] = []
    for s in STATES:
        ra, rb = a.get(s), b.get(s)
        if ra is None or rb is None:
            continue
        d65, d65p = ra["score"], rb["score"]
        both = bool(ra["gated"] and rb["gated"])
        row: dict[str, Any] = {
            "state": s, "d65": d65, "d65p": d65p,
            "delta": round(d65 - d65p, 4) if d65 is not None and d65p is not None else None,
            "both_estimable": both,
            "n65": ra["n"], "n65p": rb["n"],
            "bouts65": ra["bouts"], "bouts65p": rb["bouts"],
        }
        if both:
            row["contrast"] = compare_proportions(
                ra["reward"]["k"], ra["n"], rb["reward"]["k"], rb["n"]).to_dict()
        out.append(row)
    return out


# ── RRB: two-sided submission absorption ────────────────────────────────────────
# `reward_risk` above answers "who acts next". This answers "who FINISHES", propagated through
# the chain instead of read off one cell -- Lamas' reward-risk question with its original
# submission anchor restored, which the one-step reading had to give up (see `reward_risk`'s
# docstring: 10-20 `SUB` events per division make a one-step submission numerator 0-3 nearly
# everywhere). Propagation is what buys the anchor back: every appearance of a state contributes
# through the routes leaving it, not only through its own immediate successor.
#
# The state space is LIFTED BY SIDE. Lamas' twelve states carry no owner, so "own submission"
# has no meaning in them; each state is split into (state, side) with side relative to the
# athlete who performed the state we started from -- 24 transient states. A transition keeps the
# side when `same_actor` and flips it otherwise, which is the only thing the side coordinate
# ever does, so the sided kernel is fully determined by the 12x12x2 array of (src, dst,
# same/switch) counts.
N_SIDED = 2 * len(STATES)
_OWN, _OPP, _END = N_SIDED, N_SIDED + 1, N_SIDED + 2
N_COLS = N_SIDED + 3
# The three absorbing states, in column order. Published so a renderer names them from the
# export rather than from its own glossary.
ABSORBING_STATES: tuple[str, ...] = ("SUB_OWN", "SUB_OPP", "NO_SUB_END")
# `stats_rigor.bootstrap_ci`'s own default seed. One seed across the report, and `random.Random`
# rather than numpy's Generator because numpy does not promise a stable stream across versions
# and this report claims a rebuild reproduces every number.
SEED = 20260820

RRB_CAVEATS: tuple[str, ...] = (
    "O LADO da finalização vem de `winner`, nunca do `actor_id` do evento de finalização. "
    "Das 24 cadeias que o ciclo ADCC trunca numa SUB marcada, SETE arquivam essa finalização "
    "sob a atleta que PERDEU. Medido também o efeito hoje: entre as lutas que passam pela "
    "recusa por atribuição, as duas regras concordam em CEM POR CENTO dos casos (zero "
    "divergências de lado nos cinco recortes) — as sete mal-arquivadas estão justamente nas "
    "lutas que o portão recusa. A regra é uma garantia contra o portão afrouxar, não um "
    "conserto de números atuais.",
    "Uma luta vencida por finalização absorve MESMO quando a cadeia nunca chegou a uma SUB "
    "marcada: a transição absorvente é pendurada no último passo da cadeia, porque o fato "
    "registrado é 'esta luta acabou em finalização de W', que é da LUTA e não de um evento. "
    "ESTE braço da regra move número: ele traz 1 luta em 65 kg e 1 em +65 kg, e é essa "
    "segunda que leva +65 kg de 4 para 5 lutas absorventes, ou seja, do lado recusado para o "
    "lado estimável do portão.",
    "Esta camada depende de `actor_id` MAIS que o `reward_risk`: o campo decide o lado de cada "
    "transição (o levantamento por lado) E de quem é a aparição de partida. As lutas passam "
    "pela MESMA recusa (`one_sided` + `single_actor`) antes de entrar, e a taxa de recusa é a "
    "mesma do bloco `reward_risk`.",
    "A EVIDÊNCIA ABSORVENTE VEM DE POUQUÍSSIMAS LUTAS. Depois da recusa por atribuição sobram "
    "4 lutas com finalização em 65 kg, 5 em +65 kg, 6 nas Trials, 6 no ciclo completo e ZERO "
    "no Mundial 2024. `absorption.absorbing_bouts` é o número que governa a tabela inteira: "
    "abaixo do corte de cobertura nenhum intervalo é publicado, e em zero nem as estimativas "
    "pontuais são — doze `0,000` diriam 'medimos risco nenhum' quando o certo é 'não medimos'.",
    "A PROPAGAÇÃO ACHATA O SINAL, e isso é uma propriedade da cadeia, não um defeito da "
    "implementação. O lado troca em 22% a 47% das transições, enquanto a absorção leva de 4,6 a "
    "13,9 ações nas linhas que passam pelo portão (`expected_actions`): a cadeia esquece de "
    "quem era a iniciativa muito antes de acabar. Fora de `SUB`, nenhum estado passa de "
    "±0,074 de `balance` em recorte nenhum. É a resposta, não um ruído a ser removido — quem "
    "quiser o sinal de um passo tem o `reward_risk` ao lado e o `by_next_mover` aqui dentro.",
    "A linha `SUB` é a exceção, e por construção: boa parte das aparições de SUB É o passo "
    "terminal que absorve (`n_terminal`), então o valor dela reafirma a regra de truncamento "
    "do §1.5 mais do que faz uma afirmação prospectiva.",
    "Comparável DENTRO de um recorte, não ENTRE recortes. O resultado absorvente (`win_type` + "
    "`winner`) é imune ao lote de anotação, mas as LINHAS são por estado e a regra 3 manda "
    "`successful` ausente para o estado de tentativa — então a partição das aparições segue o "
    "lote, exatamente como no `reward_risk`.",
)

CHAIN_FACTOR_CAVEATS: tuple[str, ...] = (
    "PROFUNDIDADE 2 é deliberada. A profundidade 1 — 'a próxima ação é da mesma atleta' — já "
    "está publicada: é literalmente o braço `reward` do `reward_risk`. Um fator de "
    "encadeamento que parasse no primeiro passo seria esse número com outro nome.",
    "Aparição com menos de duas ações seguintes na cadeia fica FORA do denominador "
    "(`n_short`), a mesma convenção de `analysis/transitions/build_graph.py` que o "
    "`reward_risk` herda: aparição sem sucessora não é pontuada. O viés que isso cria está "
    "nomeado — as cadeias que acabam rápido são justamente as que uma finalização encerrou, "
    "então o fator descreve o fluxo que sobreviveu.",
    "Janela com atriz desconhecida em qualquer um dos três passos sai do denominador "
    "(`n_unknown_actor`), porque uma proporção de duas saídas não tem valor neutro: contá-la "
    "como falha faria o fator medir a cobertura da anotação. Medido: ZERO janelas assim nos "
    "cinco recortes, já que a recusa por atribuição entra antes.",
    "Intervalo duplo pelo mesmo motivo do `reward_risk`: Wilson sobre as aparições (a "
    "convenção de toda célula deste bloco) e bootstrap percentil AGRUPADO POR LUTA sobre os "
    "valores 0/1, cuja média É o fator. Abaixo do corte de cobertura por luta os dois são "
    "retirados.",
    "Comparável DENTRO de um recorte, não ENTRE recortes: as linhas são por estado e a "
    "partição segue o lote de anotação (§7.2).",
)

CHAIN_FACTOR_DEPTH = 2


def _sided(state: str, side: int) -> int:
    """Row/column index of ``(state, side)``; side 0 is the reference athlete, 1 the opponent."""
    return 2 * STATE_INDEX[state] + side


def _chain_counts(ch: Chain) -> tuple[Any, int]:
    """One chain's ``(24, 27)`` sided transition + absorption counts, and what it dropped.

    Every appearance contributes exactly one unit of row mass: a transition when it has a
    successor, an absorbing transition when it is the chain's last step. That is what makes the
    rows honest -- the three absorbing columns are not an afterthought, they are where the
    appearances that end the fight go.

    Both sides of every appearance are counted (the row for ``(s, own)`` and the row for
    ``(s, opp)``), because the kernel is a statement about the ACTION, not about which athlete
    is being followed. Reading it from the opponent's side is the same evidence mirrored, which
    is what makes the two rows exact mirrors of each other -- asserted in the tests.

    A transition whose own or whose successor's actor is unknown carries no side and is
    DROPPED, not neutralised: `reward_risk` can leave an unknown at 0 because its score is
    signed, but a sided kernel has no neutral side to put it on and inventing one would be
    worse than losing it. The count comes back so the loss is published (measured: zero in all
    five corpora -- the actor gate removes those bouts first).
    """
    import numpy as np

    m = np.zeros((N_SIDED, N_COLS))
    st = ch.steps
    dropped = 0
    for a, b in zip(st, st[1:]):
        if a.actor_id is None or b.actor_id is None:
            dropped += 1
            continue
        same = a.actor_id == b.actor_id
        for side in (0, 1):
            m[_sided(a.state, side), _sided(b.state, side if same else 1 - side)] += 1
    if st:
        last = st[-1].state
        for side in (0, 1):
            if ch.absorbs is None:
                col = _END
            elif (ch.absorbs == "self") == (side == 0):
                col = _OWN
            else:
                col = _OPP
            m[_sided(last, side), col] += 1
    return m, dropped


def _absorption(counts: Any) -> tuple[Any, Any, Any, Any]:
    """Absorption probabilities, both next-mover arms, and expected actions, for a batch.

    ``counts`` is ``(D, 24, 27)``. Returns ``(B, arm_own, arm_opp, steps)`` where ``B`` is
    ``(D, 24, 3)`` -- the probability of each absorbing outcome from each sided state -- and the
    two arms are ``(D, 12, 3)``, the same thing conditioned on who moves next.

    **The solve.** Row-normalise, split into the transient block ``Q`` (24x24) and the absorbing
    block ``R`` (24x3), and take the fundamental-matrix answer ``B = (I - Q)^-1 R``. Exact, not
    iterated: `path_to_victory`'s value iteration is the right tool when a discount factor makes
    the operator a contraction, and here there is no discount -- the contraction comes from
    absorption itself, which in this corpus takes 5 to 14 steps, so iterating to a useful
    tolerance would cost hundreds of sweeps per bootstrap draw for an answer linear algebra
    gives in one.

    **Why this does not call `analysis/systems_path_strength.absorption`,** which is the repo's
    other absorbing-chain solver and was checked before this one was written. Two reasons, both
    concrete. It is built for **exactly two** absorbing states -- one named `desired`, absorbing
    at 1, and one implicit EXIT taking whatever a row does not account for -- so three outcomes
    with three separate probabilities would mean running it twice and reinterpreting EXIT
    differently on each pass. And it is a pure-Python Gauss-Seidel iteration to 1e-9, which is
    the right shape for the handful of solves a systems report needs and the wrong one for the
    **ten thousand** this layer runs (2000 bootstrap draws x 5 corpora). The two agree on the
    mathematics and on the honesty rule -- a third state for "did not get there" -- and they
    should stay two functions.

    **``I - Q`` cannot be singular here, and the reason is structural rather than lucky.** Every
    state that appears at all appears inside some chain, and every chain ENDS -- its last step
    carries an absorbing transition by construction. So from any appearing state there is a
    positive-probability path, along the very chain the appearance sits in, to an absorbing
    column; a closed recurrent class among the transient states is therefore impossible. A state
    that does NOT appear has an all-zero row, which leaves ``I - Q``'s row as the identity's:
    still non-singular, and its answer is the zero vector, which is never read because such a
    state is refused upstream for having no appearances at all.

    **The arms.** ``by_next_mover`` conditions on the first step: the own arm re-weights the
    successors reached WITHOUT changing hands and reads their ``(t, own)`` rows; the opp arm
    takes the successors that changed hands and reads their ``(t, opp)`` rows. Both are
    conditional on the appearance HAVING a successor, so the immediate finish -- an appearance
    that is itself the last step -- is out of both by construction. Said plainly in the method
    text, because it is the one way the arms can fail to average back to the unconditional row.
    """
    import numpy as np

    tot = counts.sum(-1, keepdims=True)
    p = np.divide(counts, tot, out=np.zeros_like(counts), where=tot > 0)
    q, r = p[..., :N_SIDED], p[..., N_SIDED:]
    fundamental = np.linalg.inv(np.eye(N_SIDED) - q)
    b = fundamental @ r
    steps = fundamental.sum(-1)
    # (D, 12, 12, 2): from each sided row, the successor counts split by state and by whether
    # the exchange changed hands. Only the reference-side rows (even indices) start an arm.
    split = counts[..., :N_SIDED].reshape(counts.shape[0], N_SIDED, len(STATES), 2)[:, ::2]
    arms = []
    for side in (0, 1):
        w = split[..., side]                                   # (D, 12, 12)
        denom = w.sum(-1, keepdims=True)
        w = np.divide(w, denom, out=np.zeros_like(w), where=denom > 0)
        arms.append(w @ b[:, side::2, :])                       # rows (t, side)
    return b, arms[0], arms[1], steps


def _cluster_weights(n_bouts: int, n_boot: int, seed: int = SEED) -> Any:
    """``(n_boot, n_bouts)`` multiplicities of a bout-clustered bootstrap.

    Drawing multiplicities instead of gathering rows is the same resample -- a bout drawn twice
    contributes twice -- and it is what keeps the memory flat: the alternative materialises
    ``n_boot x n_bouts`` count matrices, 400 MB at 40 bouts and 2000 draws, for an answer that
    is one 2000x40 matrix product away.
    """
    import random

    import numpy as np

    rng = random.Random(seed)
    w = np.zeros((n_boot, n_bouts))
    for d in range(n_boot):
        for _ in range(n_bouts):
            w[d, rng.randrange(n_bouts)] += 1
    return w


def _p(x: float) -> float:
    return round(max(0.0, min(1.0, float(x))), 4)


def _absorption_estimate(p: float | None, lo: float | None, hi: float | None,
                         n: int) -> dict[str, Any]:
    """`Estimate`-shaped, with ``k`` explicitly null and ``kind`` naming what it is.

    A renderer reads ``p``/``lo``/``hi``/``grade`` off this exactly as it reads a Wilson cell,
    which is the point -- but there is no ``k`` here and putting the appearance count in that
    field would make a solved quantity look like a counted one. The grade comes from
    `stats_rigor.grade` on the bootstrap half-width, so a confidence badge means the same thing
    on this table as on every other one in the report.
    """
    half = None if lo is None or hi is None else round((hi - lo) / 2, 4)
    return {"k": None, "n": n, "p": p, "lo": lo, "hi": hi, "half": half,
            "grade": grade(n, half), "kind": "absorption"}


def _pct(draws: Any, keep: Any) -> tuple[float | None, float | None]:
    """2.5/97.5 percentiles over the draws where the state was present at all."""
    import numpy as np

    if keep.sum() < MIN_BOOT_DRAWS:
        return None, None
    lo, hi = np.percentile(draws[keep], [2.5, 97.5])
    return round(float(lo), 4), round(float(hi), 4)


# A percentile over a handful of surviving draws is noise wearing an interval. Draws in which a
# state never appears carry no information about it and are dropped; if that leaves too few, the
# interval is withheld like any other ungated one.
MIN_BOOT_DRAWS = 100


def rrb(chains: Sequence[Chain], n_boot: int = N_BOOT) -> dict[str, Any]:
    """Reward-risk BALANCE: the probability each side finishes, propagated through the chain.

    Per state ``s``, taken as performed by the reference athlete:

        p_sub_own(s)  = P(the bout ends in a submission BY HER      | an appearance of s)
        p_sub_opp(s)  = P(the bout ends in a submission by the OTHER| an appearance of s)
        balance(s)    = p_sub_own - p_sub_opp
        sub_share(s)  = p_sub_own / (p_sub_own + p_sub_opp)

    plus a third absorbing outcome, no-sub end, which is not published as a column because it is
    ``1 - own - opp`` and a renderer that prints all three invites a reader to check arithmetic
    instead of reading the finding.

    **Why two relations and not one.** ``balance`` is pre-registered as the PRIMARY, for one
    reason that is about this repo and not about statistics: `reward_risk` already composes its
    two arms as a difference (`build_graph`'s convention, kept verbatim), and a second composite
    on the same page composing them a different way would make the two tables silently
    incomparable. ``sub_share`` is the SECONDARY and answers the question the difference cannot:
    at ``own = 0.05, opp = 0.01`` the difference is +0.04, which reads as nothing, while the
    share is 0.83, which reads as the state being five times likelier to end in her finish than
    the opponent's. **The plain ratio is rejected**: it is unbounded, undefined wherever
    ``p_sub_opp`` is zero -- the common case in this data -- and the share is its bounded
    monotone transform ``r / (1 + r)``, so nothing is lost but the blow-up.

    **Three gates, in order.**

    ``absorbing_bouts == 0``   no estimate at all. Every probability is ``null`` with
                               ``reason_code: no_absorbing_bouts``. Twelve zeroes would say
                               "we measured no risk"; the truth is "we measured nothing", and
                               this is the case in the ADCC 2024 corpus, where the actor gate
                               and the finish rule leave zero submission-won bouts.
    corpus coverage refused    the point estimates survive -- they are a deterministic function
                               of the counts, like ``pathways_to_sub``' ``p_chain`` -- and every
                               interval is withheld with ``reason_code: few_absorbing_bouts``.
                               This is `bootstrap_ci`'s own instruction taken seriously: with a
                               handful of clusters it is unstable in both directions, and here
                               the clusters that matter are the FINISHES, not the appearances.
                               65 kg has four of them and is refused by this gate.
    row coverage refused       the usual per-row bout-cluster gate, same rule as the matrix.

    The two outer gates are the ones this layer adds, and they are the ones that matter: a row
    can sit on ten bouts' worth of appearances while every gram of its absorbing mass traces
    back to the same four finishes, and nothing else in the report would have said so.

    **What it does NOT do.** It does not answer "what actually happened after this state" -- the
    empirical forward-looking version of the same question, which credits every appearance in a
    bout with that bout's own ending. That reading discriminates far more sharply (65 kg `PGD`
    -0.500 against this table's -0.029) and is not published, because its effective sample is
    the number of absorbing BOUTS, four to six, wearing an appearance count of up to sixty-seven
    as its n. Every row of it is refused by the same gate as soon as the gate is applied to the
    right unit. It is named here so the omission is a decision.
    """
    import numpy as np

    usable = [ch for ch in chains if ch.actor_reliable]
    refused: Counter[str] = Counter()
    for ch in chains:
        if not ch.actor_reliable:
            refused[ch.actor_refusal or "unknown"] += 1

    per_state: dict[str, Counter[str]] = defaultdict(Counter)
    arm_bouts: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    terminal: Counter[str] = Counter()
    mats = np.zeros((max(1, len(usable)), N_SIDED, N_COLS))
    dropped = 0
    for i, ch in enumerate(usable):
        mats[i], d = _chain_counts(ch)
        dropped += d
        for st in ch.steps:
            per_state[st.state][ch.bout_id] += 1
        for a, b in zip(ch.steps, ch.steps[1:]):
            if a.actor_id is not None and b.actor_id is not None:
                arm_bouts[(a.state, 0 if a.actor_id == b.actor_id else 1)][ch.bout_id] += 1
        if ch.steps:
            terminal[ch.steps[-1].state] += 1

    absorbing = [ch for ch in usable if ch.absorbs is not None]
    # One absorption per bout, so the counts ARE ones and `effective_n` is the bout count. That
    # is the honest shape of this evidence: a fight contributes one ending, however many
    # appearances it also contributed.
    cov_abs = coverage([1] * len(absorbing))
    have_any = bool(len(absorbing))
    corpus_reason = (None if cov_abs.estimable
                     else ("no_absorbing_bouts" if not have_any else "few_absorbing_bouts"))

    total = mats.sum(0)[None]
    b_abs, arm_own, arm_opp, steps = _absorption(total)
    appear = total[0].sum(-1)

    d_b: Any = None       # (n_boot, 24, 3) absorption per draw, or None when nothing was drawn
    d_own: Any = None
    d_opp: Any = None
    d_app: Any = None
    if n_boot and cov_abs.estimable and usable:
        drawn = np.tensordot(_cluster_weights(len(usable), n_boot), mats, axes=(1, 0))
        d_b, d_own, d_opp, _ = _absorption(drawn)
        d_app = drawn.sum(-1)

    rows: list[dict[str, Any]] = []
    for s in STATES:
        i = _sided(s, 0)
        n = int(appear[i])
        cov = coverage(list(per_state[s].values()))
        gated = bool(cov.estimable and cov_abs.estimable and n)
        row: dict[str, Any] = {
            "state": s, "n": n, "bouts": len(per_state[s]),
            "n_terminal": terminal[s],
            # Actions from this one until the bout ends, INCLUSIVE -- (I - Q)^-1 . 1 counts
            # the visit to the starting state too. This is the number that makes a flat
            # `balance` legible: a chain that runs fifteen more actions has forgotten whose
            # initiative it started on long before it absorbs.
            "expected_actions": round(float(steps[0, i]), 2) if n else None,
            "gated": gated, "coverage": cov.to_dict(),
            "reason_code": corpus_reason or cov.reason_code,
        }
        if not have_any or not n:
            row.update({"p_sub_own": _absorption_estimate(None, None, None, n),
                        "p_sub_opp": _absorption_estimate(None, None, None, n),
                        "balance": None, "balance_lo": None, "balance_hi": None,
                        "sub_share": None, "share_lo": None, "share_hi": None,
                        "by_next_mover": {a: _arm_row(None, 0, Counter(), False, None, None)
                                          for a in ("own", "opp")},
                        "reason_code": row["reason_code"] or "no_appearances"})
            rows.append(row)
            continue

        own, opp = _p(b_abs[0, i, 0]), _p(b_abs[0, i, 1])
        keep = None if d_app is None else d_app[:, i] > 0
        blo = bhi = slo = shi = olo = ohi = plo = phi = None
        if d_b is not None and keep is not None and gated:
            olo, ohi = _pct(d_b[:, i, 0], keep)
            plo, phi = _pct(d_b[:, i, 1], keep)
            blo, bhi = _pct(d_b[:, i, 0] - d_b[:, i, 1], keep)
            tot_d = d_b[:, i, 0] + d_b[:, i, 1]
            slo, shi = _pct(np.divide(d_b[:, i, 0], tot_d, out=np.zeros(len(tot_d)),
                                      where=tot_d > 0), keep & (tot_d > 0))
        row.update({
            "p_sub_own": _absorption_estimate(own, olo, ohi, n),
            "p_sub_opp": _absorption_estimate(opp, plo, phi, n),
            "balance": round(own - opp, 4),
            "balance_lo": blo, "balance_hi": bhi,
            "sub_share": round(own / (own + opp), 4) if own + opp > 0 else None,
            "share_lo": slo, "share_hi": shi,
            "by_next_mover": {
                name: _arm_row(
                    (arm_own if side == 0 else arm_opp)[0, STATE_INDEX[s]],
                    sum(arm_bouts[(s, side)].values()), arm_bouts[(s, side)],
                    cov_abs.estimable,
                    None if d_own is None else (d_own if side == 0 else d_opp)[
                        :, STATE_INDEX[s]],
                    keep)
                for side, name in ((0, "own"), (1, "opp"))},
        })
        rows.append(row)

    return {
        "rows": rows,
        "method": (
            "Absorção em cadeia com o espaço de estados LEVANTADO POR LADO: cada uma das doze "
            "ações vira (ação, lado) relativo à atleta que a executou — 24 estados transitórios "
            "— e a transição mantém o lado quando é da mesma atleta e o inverte quando troca de "
            "mãos. Três destinos absorventes: finalização PRÓPRIA, finalização da ADVERSÁRIA e "
            "fim SEM finalização, de modo que cada linha soma 1. `p_sub_own`/`p_sub_opp` são "
            "as probabilidades de absorção partindo do estado, pela matriz fundamental "
            "(I − Q)⁻¹R; `balance` = own − opp (primário, mesma composição por diferença do "
            "`reward_risk`); `sub_share` = own / (own + opp) (secundário — a razão pura foi "
            "REJEITADA por ser ilimitada e indefinida onde `p_sub_opp` é zero). O lado da "
            "absorção é decidido pelo `winner` da luta, nunca pelo `actor_id` do evento. "
            "`by_next_mover` condiciona no primeiro passo e por isso exclui, nos dois braços, a "
            "aparição que É o passo terminal."),
        "absorption": {
            "usable_bouts": len(usable),
            "bouts_refused": dict(refused),
            "absorbing_bouts": len(absorbing),
            "absorbed_self": sum(1 for ch in absorbing if ch.absorbs == "self"),
            "absorbed_other": sum(1 for ch in absorbing if ch.absorbs == "other"),
            "absorbed_without_flagged_sub": sum(1 for ch in absorbing if not ch.truncated),
            "coverage": cov_abs.to_dict(),
            "estimable": cov_abs.estimable,
            "reason_code": corpus_reason,
            "unknown_actor_transitions": dropped,
            "absorbing_states": list(ABSORBING_STATES),
            "side_rule_code": "winner_decides_side_never_event_actor",
            "solve": "fundamental matrix (I - Q)^-1 R, 24 transient states",
            "boot": {"n": n_boot if d_b is not None else 0, "unit": "bout",
                     "kind": "cluster percentile", "seed": SEED,
                     "min_draws": MIN_BOOT_DRAWS},
        },
        "caveats": list(RRB_CAVEATS),
    }


def _arm_row(vec: Any, n: int, bouts: Counter[str], corpus_ok: bool,
             draws: Any, keep: Any) -> dict[str, Any]:
    """One ``by_next_mover`` arm: the absorption vector reached through that first step."""
    cov = coverage(list(bouts.values()))
    gated = bool(corpus_ok and cov.estimable and n)
    if vec is None or not n:
        return {"n": n, "bouts": len(bouts), "p_sub_own": None, "p_sub_opp": None,
                "balance": None, "balance_lo": None, "balance_hi": None, "gated": False,
                "coverage": cov.to_dict(),
                "reason_code": "no_transitions" if not n else cov.reason_code}
    own, opp = _p(vec[0]), _p(vec[1])
    lo = hi = None
    if gated and draws is not None and keep is not None:
        lo, hi = _pct(draws[:, 0] - draws[:, 1], keep)
    return {"n": n, "bouts": len(bouts), "p_sub_own": own, "p_sub_opp": opp,
            "balance": round(own - opp, 4), "balance_lo": lo, "balance_hi": hi,
            "gated": gated, "coverage": cov.to_dict(),
            "reason_code": None if gated else cov.reason_code}


def chain_factor(chains: Sequence[Chain], n_boot: int = N_BOOT) -> dict[str, Any]:
    """How much an action INDUCES a sequential run of the same athlete's own actions.

    Pre-registered definition, one line and no alternatives applied per state::

        chain_factor(s) = P(the TWO actions following an appearance of s are BOTH by the
                            athlete who performed s | that appearance has two following actions)

    **Why depth two and not one.** Depth one is already published: "the next action is by the
    same athlete" is literally `reward_risk`'s ``reward`` arm, and a chain factor that stopped
    there would be that number wearing a second name. Depth two is the shortest window that says
    something the initiative table does not -- whether the exchange KEEPS going -- and the two
    are meant to be read side by side: measured on the whole ADCC cycle, `CDP` retains at 0.54
    for one step and chains at 0.32 for two, while `BTK` retains at 0.95 and chains at 0.64.

    **Why the joint and not the expected run length.** The alternative pre-registration -- mean
    length of the same-actor run following the action -- is an expectation over a heavy right
    tail, where one bout's run of eight moves the state's number more than the other bouts
    together, and the bout is exactly the unit this corpus cannot spare. The joint probability
    is bounded, is a proportion, and drops straight into the gating and interval machinery every
    other cell in this block already uses. The run length is one accumulator away if anyone ever
    needs a magnitude rather than a rate.

    **The denominator.** Appearances with fewer than two following actions are OUT
    (``n_short``), which is `build_graph`'s own rule -- an appearance with no successor is not
    scored -- carried to depth two. The bias it creates is named rather than corrected: chains
    that end quickly are disproportionately the ones a submission ended, so this describes the
    flow that survived. Windows with an unknown actor are also out (``n_unknown_actor``, and
    measured zero in every corpus, because the actor gate removes those bouts first): a
    two-valued statistic has no neutral outcome to park an unknown on, and scoring it as a
    failure would turn the factor into a measure of annotation coverage.

    Intervals are the same pair `reward_risk` publishes -- Wilson over appearances for the cell,
    bout-clustered percentile bootstrap for the same quantity -- and both are withheld together
    below the bout-cluster gate.
    """
    from statistics import fmean

    usable = [ch for ch in chains if ch.actor_reliable]
    vals: dict[str, list[float]] = defaultdict(list)
    bl: dict[str, list[str]] = defaultdict(list)
    short: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for ch in usable:
        st = ch.steps
        for i, step in enumerate(st):
            window = st[i:i + CHAIN_FACTOR_DEPTH + 1]
            if len(window) <= CHAIN_FACTOR_DEPTH:
                short[step.state] += 1
                continue
            if any(w.actor_id is None for w in window):
                unknown[step.state] += 1
                continue
            vals[step.state].append(
                1.0 if all(w.actor_id == window[0].actor_id for w in window[1:]) else 0.0)
            bl[step.state].append(ch.bout_id)

    rows: list[dict[str, Any]] = []
    for s in STATES:
        v, g = vals[s], bl[s]
        n, k = len(v), int(sum(v))
        cov = coverage(list(Counter(g).values()))
        lo: float | None = None
        hi: float | None = None
        if cov.estimable and n and n_boot:
            _, b_lo, b_hi = bootstrap_ci(v, fmean, n_boot=n_boot, groups=g)
            lo, hi = round(b_lo, 4), round(b_hi, 4)
        rows.append({
            "state": s, "n": n, "bouts": len(set(g)), "k": k,
            "factor": _cell(k, n, cov),
            "factor_lo": lo, "factor_hi": hi,
            "n_short": short[s], "n_unknown_actor": unknown[s],
            "gated": bool(cov.estimable and n),
            "coverage": cov.to_dict(),
        })
    return {
        "rows": rows,
        "definition": (
            f"fator de encadeamento(s) = P(as {CHAIN_FACTOR_DEPTH} ações seguintes à aparição "
            f"de s são TODAS da mesma atleta que executou s | a aparição tem pelo menos "
            f"{CHAIN_FACTOR_DEPTH} ações seguintes na cadeia). Profundidade "
            f"{CHAIN_FACTOR_DEPTH} porque a profundidade 1 já é o braço `reward` do "
            f"`reward_risk`. Aparição rasa demais e janela com atriz desconhecida ficam fora do "
            f"denominador (`n_short`, `n_unknown_actor`), a convenção de "
            f"analysis/transitions/build_graph.py levada a duas ações."),
        "depth": CHAIN_FACTOR_DEPTH,
        "usable_bouts": len(usable),
        "windows": sum(len(v) for v in vals.values()),
        "boot": {"n": n_boot, "unit": "bout", "kind": "cluster percentile", "seed": SEED},
        "caveats": list(CHAIN_FACTOR_CAVEATS),
    }


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


def markov_block(bouts: Sequence[Mapping[str, Any]], n_boot: int = N_BOOT) -> dict[str, Any]:
    """Everything one division publishes: the matrix, its occupancy, its routes, its anchor.

    ``bouts`` is a division's bout set as the caller already built it -- this module does not
    select fights, so the markov section can never describe a different universe from the
    sequence section beside it.

    ``n_boot = 0`` skips every bootstrap and publishes the counts with their intervals withheld.
    That is not a quality knob: `scripts/bracket_export.markov_layer` rebuilds this block once
    per point of the cut space purely to COUNT how many rows the gate would refuse there, and
    paying for three bootstraps per rebuild bought nothing but runtime.
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
        # `comparison` is filled in by the caller that holds BOTH divisions
        # (`scripts/bracket_export.markov_layer`); one division cannot compare itself.
        "reward_risk": {**reward_risk(chains, n_boot), "comparison": []},
        # Who FINISHES, propagated (`rrb`), and how much an action induces a run of the same
        # athlete's own actions (`chain_factor`). Both sit beside `reward_risk` rather than
        # inside it because all three answer different questions off one chain: who acts next,
        # who taps whom in the end, and whether the exchange keeps going.
        "rrb": rrb(chains, n_boot),
        "chain_factor": chain_factor(chains, n_boot),
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
    def ev(t: str, label: str, actor: str = "a") -> dict[str, Any]:
        return {"type": t, "label": label, "actor_id": actor}

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

    # reward-risk, hand-computable: X acts, X acts (reward), Y acts (risk), Y acts (reward).
    rr = reward_risk([chain_of({"id": "r", "seq": [
        ev("control", "Collar Tie", "X"), ev("takedown", "Trip", "X"),
        ev("pass", "Pass", "Y"), ev("submission", "Armbar", "Y"),
    ], "a_id": "X", "b_id": "Y"})])
    by = {r["state"]: r for r in rr["rows"]}
    assert by["CDP"]["score"] == 1.0 and by["CDP"]["reward"]["k"] == 1          # CDP → same
    assert by["TKDA"]["score"] == -1.0 and by["TKDA"]["risk"]["k"] == 1         # TKDA → other
    assert by["GPSA"]["score"] == 1.0                                          # GPSA → same
    assert by["SUBA"]["n"] == 0                                                # no successor
    assert rr["bouts_used"] == 1

    # RRB, hand-computable. Y clinches; X then takes down, passes and submits, and X WINS.
    # The single chain is deterministic, so every absorption probability is 0 or 1: from CDP
    # (Y's action) the finish is the OPPONENT's, from TKDA (X's) it is her own.
    won = {"id": "w", "win_type": "SUBMISSION", "winner": "X", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "Y"), ev("takedown", "Trip", "X"),
        ev("pass", "Knee Cut", "X"),
        {"type": "submission", "label": "RNC", "actor_id": "X", "successful": True},
    ]}
    block = rrb([chain_of(won)])
    by_s = {r["state"]: r for r in block["rows"]}
    assert block["absorption"]["absorbing_bouts"] == 1, block["absorption"]
    assert (by_s["CDP"]["p_sub_own"]["p"], by_s["CDP"]["p_sub_opp"]["p"]) == (0.0, 1.0)
    assert (by_s["TKDA"]["p_sub_own"]["p"], by_s["TKDA"]["p_sub_opp"]["p"]) == (1.0, 0.0)
    assert by_s["TKDA"]["balance"] == 1.0 and by_s["SUB"]["n_terminal"] == 1
    # Same events, opposite result: the side comes from `winner`, so every row flips.
    by_l = {r["state"]: r for r in rrb([chain_of({**won, "winner": "Y"})])["rows"]}
    assert (by_l["CDP"]["p_sub_own"]["p"], by_l["CDP"]["p_sub_opp"]["p"]) == (1.0, 0.0)

    # chain factor: CDP's window is Y, X, X -- not a run; TKDA's is X, X, X -- one.
    cf = {r["state"]: r for r in chain_factor([chain_of(won)])["rows"]}
    assert (cf["CDP"]["n"], cf["CDP"]["k"]) == (1, 0), cf["CDP"]
    assert (cf["TKDA"]["n"], cf["TKDA"]["k"]) == (1, 1), cf["TKDA"]
    assert cf["GPSA"]["n"] == 0 and cf["GPSA"]["n_short"] == 1
    print("lamas_chain self-check ok")


if __name__ == "__main__":
    _demo()
