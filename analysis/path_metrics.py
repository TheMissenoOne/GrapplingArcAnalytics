"""Fase 3 (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §9) — a ``ChainEdge`` IS a path, so it
carries its own statistics. Pure, deterministic, no I/O beyond the already-existing Markov
weights artefact (``analysis.markov_weights.load_markov_weights``) a caller loads once and
passes in.

**Fixed interface** — Fase 4 (the prototype renderer) imports these names as-is; do not rename.

**``strength``.** A weighted mean of the OBSERVED actions' own ratings, weighted by the Markov
mean-1 shares (``analysis.markov_weights.relative_shares``) over each action's own
``analysis.lamas_chain.lamas_state`` code — the exact contract the rating engines already use,
not a reimplementation. An INFERRED action rides the edge (counts toward ``length``) but was
never observed, so it never enters ``strength`` — neither in the numerator nor the weight mass
being averaged over (the remaining weights are renormalised, so ``strength`` stays a true
average of the actions that DO have a rating). An action whose ``rating_of(key)`` is ``None``
(no rating on file for that node_key on this side) is excluded the same way. ``strength`` is
``None`` when nothing in the edge qualifies.

``rating_of`` is injected because the source varies by caller (Glicko-2 per ``(athlete,
node_key)`` for the public corpus, ``computedElo`` off a bundle's graph nodes for the App side)
— this module never opens a DB or a bundle itself.

**``role_delta``.** Compares the edge's two STATE endpoints (``source_key``/``target_key``) via
``analysis.taxonomy_kind.orientation_for_inference`` on ``analysis.taxonomy_kind``'s two
positional axes (topology: top/bottom; control: controlling/controlled — kept separate, same
convention as ``taxonomy_kind._STANCE_AXIS``: a closed guard and a kimura grip make claims on
different axes and are not comparable).

    same axis, same side      -> 'none'             (e.g. mount -> side control, both top)
    axes differ, or either    -> 'unknown'          (no comparable positional claim)
      end reads 'neutral'
    same axis, opposite side, -> 'inversion'         (dominance moved to whoever this edge's
      an action on the edge                          own actions attribute it to — the model's
      is attributed to the                            "reversal": Controle A -> Guarda A =>
      opponent (``actor_is_                           Inversão B)
      opponent=True``)
    same axis, opposite side, -> 'same-actor-shift'  (the chain owner's OWN position flipped —
      no opponent-attributed                          the model's "sweep": Guarda A -> Guarda B
      action on the edge                              => Raspagem A)

ponytail: ``ChainEdge`` carries only the two endpoints' CANONICAL KEYS, not their original
``(type, label)`` — the state's own event ``type`` never survives onto the edge. This module
recovers it via ``taxonomy_kind.resolve_library_entry`` when the key is a known technique-
library entry (most are); for the ~13 standing-control-grip labels that resolve through NEITHER
the declared orientation table nor the library (``orientation_for_inference``'s third,
``attribution``-derived level — measured in §8.2 of the contract doc), the missing type means
that level can't fire and those endpoints read ``'unknown'`` instead of their true stance.
Ceiling: thread the endpoint's real ``type`` onto ``ChainEdge`` (or accept a
``state_type_of: Callable[[str], str | None]`` alongside ``rating_of``) if a consumer needs
those ~13 labels resolved exactly; not worth it for a first pass over 4 role_delta buckets.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from analysis.chain_compiler import ChainAction, ChainEdge
from analysis.lamas_chain import lamas_state
from analysis.markov_weights import relative_shares
from analysis.taxonomy_kind import (
    Stance,
    orientation_for_inference,
    resolve_library_entry,
)

# The same two axes `taxonomy_kind._STANCE_AXIS` keeps separate, for the same reason (a state
# on the `topology` axis and one on the `control` axis make claims that are not comparable).
_AXIS: dict[Stance, str] = {
    "top": "topology", "bottom": "topology",
    "controlling": "control", "controlled": "control",
}


@dataclass(frozen=True)
class PathMetrics:
    length: int            # nº de ações na trilha (observadas + inferidas)
    observed: int           # ações NÃO inferidas
    observed_ratio: float   # observed / length; 0.0 quando length == 0
    support: int             # nº de ocorrências da relação (source, target, actor) — do caller
    terminal: bool           # edge.terminal
    role_delta: str          # 'none' | 'inversion' | 'same-actor-shift' | 'unknown'
    strength: float | None   # média ponderada (Markov mean-1) das ratings das ações OBSERVADAS
                              # com rating; None se nenhuma ação qualifica


def _stance_of(key: str) -> Stance | None:
    resolved = resolve_library_entry(key)
    event_type = resolved[1] if resolved is not None else ""
    reading = orientation_for_inference(event_type, key)
    return reading.value if reading.value != "neutral" else None


def _role_delta(edge: ChainEdge) -> str:
    src, tgt = _stance_of(edge.source_key), _stance_of(edge.target_key)
    src_axis = _AXIS.get(src) if src is not None else None
    tgt_axis = _AXIS.get(tgt) if tgt is not None else None
    if src_axis is None or tgt_axis is None or src_axis != tgt_axis:
        return "unknown"
    if src == tgt:
        return "none"
    return "inversion" if any(a.actor_is_opponent for a in edge.actions) else "same-actor-shift"


def _strength(
    actions: tuple[ChainAction, ...],
    rating_of: Callable[[str], float | None],
    block: Mapping[str, float] | None,
) -> float | None:
    codes = [lamas_state({"type": a.type, "label": a.label}) for a in actions]
    shares = relative_shares(codes, block)
    weighted_total = 0.0
    weight_mass = 0.0
    for action, share in zip(actions, shares, strict=True):
        if action.inferred:
            continue
        rating = rating_of(action.key)
        if rating is None:
            continue
        weighted_total += share * rating
        weight_mass += share
    return weighted_total / weight_mass if weight_mass > 0 else None


def path_metrics(
    edge: ChainEdge,
    *,
    support: int,
    rating_of: Callable[[str], float | None],
    block: Mapping[str, float] | None,
) -> PathMetrics:
    length = len(edge.actions)
    observed = sum(1 for a in edge.actions if not a.inferred)
    return PathMetrics(
        length=length,
        observed=observed,
        observed_ratio=(observed / length) if length else 0.0,
        support=support,
        terminal=edge.terminal,
        role_delta=_role_delta(edge),
        strength=_strength(edge.actions, rating_of, block),
    )


def metrics_for_paths(
    edges: Iterable[ChainEdge],
    *,
    support_of: Callable[[ChainEdge], int],
    rating_of: Callable[[str], float | None],
    block: Mapping[str, float] | None,
) -> list[tuple[ChainEdge, PathMetrics]]:
    return [
        (edge, path_metrics(edge, support=support_of(edge), rating_of=rating_of, block=block))
        for edge in edges
    ]
