"""Fase 5d — the RENDER budget: what a dense path payload draws, and what it folds.

The owner's rule, literally: *"agrupamento é RENDER, nunca topologia canônica"*. Nothing here
touches a variant's ``count``, a family's ``support`` or any rating — §13 of
``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` stays true by construction, because this module
takes ``RenderPath``s in and gives ``RenderPath``s out and never reads or writes a metric.

Three moves, in this order:

1. **Budget.** Rank every occurrence by (support, strength, count) and keep the top ``budget``
   as they are. This replaces the static ``min_count=2`` gate the ocean uses today
   (``analysis.corpus_paths.path_payload``), and the difference that matters is that a path
   below the line is **folded, never dropped** — the ocean's gate silently deletes 1 974 of
   2 370 paths, and a reader has no way to know they existed.
2. **Category fold.** Among what fell below the line, occurrences of the SAME family
   (``source``, ``target``, ``actor``) whose actions are all of one type collapse into one
   drawn stroke — "Finalizações ×4". Mixed chains of that same family collapse into
   "outros caminhos ×N". Every folded occurrence keeps its own row in the panel and comes back
   on selection; the fold owns no data of its own beyond a display sum.
3. **Label repetition.** ``[Triangle, Triangle, Triangle]`` reads "Triangle ×3". Display only —
   ``RenderPath.actions`` is never rewritten, only the string a renderer prints.

A folded stroke carries a SYNTHETIC action key (``$fold:...``), unique per fold, so
``analysis.path_bundling`` can never make it share ink with a real action: a fold is an
editorial object and must not look like a technique that happened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis.path_bundling import RenderPath

__all__ = [
    "CATEGORY_PLURAL",
    "BudgetResult",
    "FoldedPath",
    "apply_budget",
    "compress_actions",
    "fold_id",
]

#: pt-BR plural of each action category, for a category fold's own label. The keys are the
#: categories ``scripts.render_map_prototypes._cat_of`` / ``analysis.corpus_paths._cat_of``
#: already produce, so a type this table does not know still folds — it just names itself.
CATEGORY_PLURAL = {
    "guard": "Guardas",
    "pass": "Passagens",
    "sweep": "Raspagens",
    "takedown": "Quedas",
    "control": "Controles",
    "submission": "Finalizações",
    "escape": "Escapadas",
    "transition": "Transições",
}


def compress_actions(labels: Sequence[str]) -> list[str]:
    """Collapse a CONSECUTIVE run of the same label into ``"<label> ×N"``. Display only.

    Consecutive and not global on purpose: ``[Armbar, Sweep, Armbar]`` is a real back-and-forth
    and printing "Armbar ×2 → Sweep" would claim an order the occurrence never walked.
    """
    out: list[str] = []
    for label in labels:
        if out and _base(out[-1]) == label:
            out[-1] = f"{label} ×{_count(out[-1]) + 1}"
        else:
            out.append(label)
    return out


def _base(text: str) -> str:
    head, sep, tail = text.rpartition(" ×")
    return head if (sep and tail.isdigit()) else text


def _count(text: str) -> int:
    head, sep, tail = text.rpartition(" ×")
    return int(tail) if (sep and tail.isdigit()) else 1


@dataclass(frozen=True)
class FoldedPath:
    """One drawn stroke standing for several occurrences that did not make the budget."""

    path: RenderPath          #: the synthetic occurrence actually drawn
    members: tuple[str, ...]  #: the original ``path_id``s it stands for, sorted
    kind: str                 #: ``'category'`` | ``'mixed'``
    category: str | None      #: the shared action category, for a ``'category'`` fold
    label: str                #: "Finalizações ×4" / "outros caminhos ×7"


@dataclass(frozen=True)
class BudgetResult:
    drawn: tuple[RenderPath, ...]      #: kept originals + synthetic folds, sorted by id
    folds: tuple[FoldedPath, ...]      #: sorted by the synthetic path id
    kept: tuple[str, ...]              #: original ids drawn as themselves
    folded: tuple[str, ...]            #: original ids that live inside a fold

    @property
    def fold_of(self) -> dict[str, str]:
        """Original ``path_id`` -> the synthetic id that stands for it."""
        return {m: f.path.path_id for f in self.folds for m in f.members}


def fold_id(index: int) -> str:
    """Synthetic occurrence id for the ``index``-th fold, in sorted-group order."""
    return f"f{index}"


def apply_budget(
    paths: Sequence[RenderPath],
    *,
    budget: int,
    score: Mapping[str, tuple[float, float]],
    category_of: Mapping[str, str],
) -> BudgetResult:
    """Keep the strongest ``budget`` occurrences; fold the rest, never drop one.

    ``score`` is ``path_id -> (support, strength)`` — the two numbers the owner named for the
    ranking. A path missing from it, or carrying a ``None`` strength the caller mapped to
    ``0.0``, still ranks: ``count`` and then the id break every tie, so the order is total and
    two runs agree. ``category_of`` maps an action KEY to its category; an action the caller
    cannot classify simply never lets its path into a category fold.

    ``budget <= 0`` means "no budget" — every occurrence is drawn as itself, which is the
    hairball the page exists to show next to the folded version.
    """
    ordered = sorted(
        paths,
        key=lambda p: (
            -score.get(p.path_id, (0.0, 0.0))[0],
            -score.get(p.path_id, (0.0, 0.0))[1],
            -p.count,
            p.path_id,
        ),
    )
    if budget <= 0 or len(ordered) <= budget:
        return BudgetResult(tuple(sorted(paths, key=lambda p: p.path_id)), (),
                            tuple(sorted(p.path_id for p in paths)), ())

    kept = ordered[:budget]
    overflow = ordered[budget:]

    # group key -> members. A group is a FAMILY plus, when every action of the occurrence
    # shares one category, that category. Endpoints stay in the key because a stroke has to
    # start and end somewhere real: folding two different targets into one line would draw a
    # transition that never happened.
    groups: dict[tuple[str, str, str, str], list[RenderPath]] = {}
    for p in overflow:
        cats = {category_of.get(a, "") for a in p.actions}
        cat = cats.pop() if len(cats) == 1 else ""
        groups.setdefault((p.source, p.target, p.actor, cat), []).append(p)

    # A single-member category group is not a fold — "Finalizações ×1" says less than the
    # occurrence's own label. It joins its family's mixed bucket instead.
    mixed: dict[tuple[str, str, str, str], list[RenderPath]] = {}
    final: dict[tuple[str, str, str, str], list[RenderPath]] = {}
    for key, members in groups.items():
        if key[3] and len(members) >= 2:
            final[key] = members
        else:
            mixed.setdefault((key[0], key[1], key[2], ""), []).extend(members)
    for key, members in mixed.items():
        final.setdefault(key, []).extend(members)

    drawn: list[RenderPath] = list(kept)
    folds: list[FoldedPath] = []
    folded_ids: list[str] = []
    for index, key in enumerate(sorted(final)):
        members = sorted(final[key], key=lambda p: p.path_id)
        source, target, actor, cat = key
        if len(members) == 1:  # nothing to save — draw the occurrence itself
            drawn.append(members[0])
            continue
        n = len(members)
        synthetic = fold_id(index)
        label = (f"{CATEGORY_PLURAL.get(cat, cat.title())} ×{n}" if cat
                 else f"outros caminhos ×{n}")
        drawn.append(RenderPath(
            path_id=synthetic, source=source, target=target,
            # unique synthetic key: a fold must never share bundled ink with a real action
            actions=(f"$fold:{synthetic}",), actor=actor,
            count=sum(m.count for m in members),
        ))
        folds.append(FoldedPath(
            path=drawn[-1], members=tuple(m.path_id for m in members),
            kind="category" if cat else "mixed", category=cat or None, label=label,
        ))
        folded_ids.extend(m.path_id for m in members)

    return BudgetResult(
        drawn=tuple(sorted(drawn, key=lambda p: p.path_id)),
        folds=tuple(sorted(folds, key=lambda f: f.path.path_id)),
        kept=tuple(sorted(p.path_id for p in paths if p.path_id not in set(folded_ids))),
        folded=tuple(sorted(folded_ids)),
    )
