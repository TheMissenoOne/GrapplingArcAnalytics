"""Markov action weights — how much of a bout's ELO move each action is worth.

The artefact is ``data/rating/markov_action_weights.json``, produced separately from the
Lamas action chain (:mod:`analysis.lamas_chain`). This module is the CONSUMER side: it
loads the file, picks the right block for a bout, and turns a list of per-event action
codes into a share vector. It computes no weights of its own.

**The one shared contract.** :func:`relative_shares` is the function the App's
``src/services/markovActionWeights.ts`` must reproduce exactly, and
``data/rating/markov_weights_golden.json`` (written by
``scripts/export_markov_weight_fixtures.py``) is what proves it did. Everything either
side does with the resulting vector — the scalar it multiplies it by, which ratings it
lands on — is engine-local and deliberately NOT part of the contract, because the two
engines normalise differently and cannot be made to agree without changing one of them's
numbers (see ``analysis/athlete_elo.replay_matches``).

**Three rules, stated before any number.**

1. **An unmapped action is not a worthless one.** ``lamas_state`` returns ``None`` for
   guard postures, escapes and dwell states — by its own rule 2, those are the pause
   between actions, not actions — and they are a large share of any real sequence. Giving
   them weight 0 would freeze every guard node in the corpus at its seed ELO forever. They
   take :func:`unmapped_weight`, the MEAN of the block in use, which is the "no
   information" value: it says nothing about the action rather than saying it is worthless.
   The same rule covers a code the block simply does not carry.

2. **Equal weights reproduce today's behaviour exactly.** With every weight in the block
   equal, :func:`relative_shares` returns a uniform vector, so a caller that multiplies by
   its own scalar lands exactly where it landed before this module existed. That is the
   property that makes the change auditable — it is asserted in
   ``tests/test_markov_weights.py`` — and it is why the mean, not zero, is rule 1's floor.

3. **Absent artefact → no artefact.** :func:`load_markov_weights` returns ``None`` rather
   than raising or inventing a default, and every caller must degrade to its prior
   behaviour on ``None``. Nothing about athlete ELO changes until the file ships AND a full
   replay is run.

**Family selection.** A bout's ruleset family comes from
``analysis.ruleset_scoring.family_of(match.event)`` — the same versioned
``data/scouting/event_rulesets.json`` map every other ruleset-aware layer reads, so a
bout cannot be ADCC here and IBJJF there. Only ``adcc`` and ``ibjjf`` can select a
family block, and only when that block is present in the artefact; ``cji``, ``other``,
``non_grappling`` and ``unknown`` take ``global``. A family that did not clear the
artefact's own gates is simply absent, and falling back to ``global`` is the whole point
of publishing it that way.

Privacy class **A, public competition data** on the athlete path: the weights are derived
from ``matches`` rows and applied to athlete graphs. The App bundles a copy of the
``global`` block only and applies it to the user's own on-device rating, which never
leaves the device — a public artefact informing a private rating, the permitted direction.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "rating" / "markov_action_weights.json"
)

#: The only two families that can carry a block of their own. Kept explicit rather than
#: derived from the artefact's keys so a stray key in the file cannot quietly create a
#: third ruleset the rest of the repo has never heard of.
FAMILY_BLOCKS: tuple[str, ...] = ("adcc", "ibjjf")


class MarkovWeightsError(ValueError):
    pass


@lru_cache(maxsize=4)
def load_markov_weights(path: str | None = None) -> dict[str, Any] | None:
    """The weights artefact, or ``None`` when it is not on disk.

    Absent is a normal state and returns ``None`` (rule 3). A file that EXISTS but is
    unreadable or malformed raises — that is a broken artefact, not a missing one, and
    silently degrading past it would hide the breakage behind unchanged numbers.
    """
    p = Path(path) if path else DEFAULT_WEIGHTS_PATH
    if not p.exists():
        return None
    try:
        doc: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarkovWeightsError(f"não foi possível ler {p}: {exc}") from exc
    block = doc.get("global")
    if not isinstance(block, dict) or not block:
        raise MarkovWeightsError(f"{p} sem bloco 'global' utilizável")
    for name, values in doc.items():
        if name not in ("global", *FAMILY_BLOCKS) or not isinstance(values, dict):
            continue
        for code, w in values.items():
            if not isinstance(w, int | float) or isinstance(w, bool) or w < 0:
                raise MarkovWeightsError(f"{p}: peso inválido em {name}.{code}: {w!r}")
    return doc


def block_for_family(
    family: str | None, doc: Mapping[str, Any] | None
) -> dict[str, float] | None:
    """The weight block a bout of this ruleset family scores under.

    ``adcc``/``ibjjf`` take their own block when the artefact carries one, and fall back to
    ``global`` when it does not (a family that missed the artefact's gates is absent by
    design). Every other family — and every bout whose event nobody classified — takes
    ``global``. ``None`` in, ``None`` out, so an absent artefact stays absent all the way
    down to the caller that has to degrade.
    """
    if doc is None:
        return None
    if family in FAMILY_BLOCKS:
        block = doc.get(family)
        if isinstance(block, dict) and block:
            return {str(k): float(v) for k, v in block.items()}
    g = doc.get("global")
    return {str(k): float(v) for k, v in g.items()} if isinstance(g, dict) and g else None


def unmapped_weight(block: Mapping[str, float]) -> float:
    """The weight an action with no Lamas code (or no entry in this block) is worth.

    The block's arithmetic mean — rule 1. Not zero, which would freeze every guard posture
    and escape in the corpus; not one, which would be a different unit from whatever scale
    the artefact publishes on.
    """
    values = [float(v) for v in block.values()]
    return sum(values) / len(values) if values else 1.0


def weight_of(code: str | None, block: Mapping[str, float]) -> float:
    """One action code's weight, with rule 1's fallback for ``None`` and for absent codes."""
    if code is not None:
        w = block.get(code)
        if w is not None:
            return float(w)
    return unmapped_weight(block)


def relative_shares(
    codes: Sequence[str | None], block: Mapping[str, float] | None
) -> list[float]:
    """**The cross-module contract.** Action codes → shares summing to 1.

    ``codes[i]`` is the Lamas action code of the i-th scoring unit (``lamas_state``'s
    output on the athlete side, the App port's on the user side), or ``None`` where the
    event carries no action. The result is ``w_i / Σw``.

    Uniform (``1/n`` each) whenever there is nothing to differentiate on: no block (rule
    3), an empty block, or a block whose weights sum to zero. Each engine multiplies this
    vector by its OWN scalar — ``athlete_elo`` by the count of participating nodes so its
    graph mean moves exactly as far as it does today, the App by the round's whole move —
    and that scalar is not part of this contract.
    """
    n = len(codes)
    if n == 0:
        return []
    if not block:
        return [1.0 / n] * n
    weights = [weight_of(c, block) for c in codes]
    total = sum(weights)
    if total <= 0:
        return [1.0 / n] * n
    return [w / total for w in weights]
