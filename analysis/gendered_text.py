"""Single place every prose generator picks a gender-agreeing phrase from.

``athletes.gender`` (alembic 0049) is ``'f' | 'm' | None`` — ``None`` means no evidence, not
"assume masculine". **Product rule: prose never defaults to masculine on missing data.**

Callers own their own three phrasings (this module only owns the selection + the rule):

- ``m`` / ``f`` may use a gendered pronoun ("he"/"she", "ele"/"ela") — gender is known.
- ``neutral`` MUST read correctly on its own, with no gendered pronoun. In English that
  usually means repeating the athlete's name or dropping a possessive ("their" is also fine).
  In Portuguese it means dropping the pronoun/article rather than writing "o(a)" or a slash —
  drop "dele"/"dela" and repeat the name, or restructure the sentence, per call site.

Grammatical gender that agrees with a Portuguese NOUN rather than the athlete (e.g. "o grafo"
-> "ele", "o terreno" -> "ele") is not in scope here — that's not the athlete's gender, it
doesn't change with it, and forcing it through this helper would just be noise.
"""

from __future__ import annotations

from typing import Literal

Gender = Literal["f", "m"] | None


def pick(gender: Gender, *, m: str, f: str, neutral: str) -> str:
    """Select the phrasing for ``gender``. Anything other than exactly ``'f'``/``'m'``
    (including ``None``) is unknown and gets ``neutral`` — never ``m``."""
    if gender == "f":
        return f
    if gender == "m":
        return m
    return neutral
