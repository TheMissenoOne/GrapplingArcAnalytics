"""Next-move ranking by EMBEDDING similarity, blended with the corpus prior.

The companion to :mod:`analysis.next_moves`. That module answers "what does the corpus say
happens next" by counting; this one asks whether a text embedding of the situation
(*"Closed Guard, she just hit an arm drag, her turn"*) sits closer to some candidate techniques
than to others, and whether that similarity adds anything the counts do not already have.

**Pure and offline.** Nothing here calls an API, opens a database or loads a model. Vectors
come IN (``scripts/eval_next_moves.py`` fetches and caches them); the text builders, the blend
and the guidance renderer are all deterministic functions of their arguments, so every claim
this module makes is testable without a network. Privacy class **A, public competition data** —
same as ``next_moves``: the prior is fitted on ``matches`` rows only, and a next-move ranking is
a competitive artefact, so a user graph may never enter it.

## Two embedding sources, deliberately comparable

Both are 768-dimensional and L2-normalised, so cosine is a dot product and the two columns of
the report are on the same scale:

``mpnet``   ``sentence-transformers/all-mpnet-base-v2`` — the model this repo ALREADY runs
            (``analysis/embeddings.py``, ``MODEL_NAME``; 358 of 383 ``technique_nodes`` rows
            already carry one of its vectors). Same MODEL as the pgvector column, NOT the same
            text: ``embeddings.node_text`` encodes ``label · type · pt`` while
            :func:`candidate_text` adds the variants and the type gloss, so the stored vectors
            cannot be reused as-is — they are re-encoded locally, which costs about four
            seconds for the whole candidate set. Zero marginal cost, runs offline, no key.
``gemini``  ``gemini-embedding-2`` at ``output_dimensionality=768``, ``task_type``
            ``RETRIEVAL_DOCUMENT`` for candidates and ``RETRIEVAL_QUERY`` for the situation
            text. Costs tokens once, then cached on disk forever.

## The blend

``score = α · cos + (1 − α) · log P_markov``, α swept on TRAIN and reported on VALIDATION, with
α=0 the pure Markov baseline and α=1 the pure embedding. Two things about it are worth stating
because they are choices, not defaults:

1. **``log`` P, not P.** The corpus prior is heavy-tailed; a linear blend with raw probability
   is the prior alone for every α under ~0.99, which would make the sweep meaningless.
2. **Both terms are z-scored per query by default** (``standardize=True``). Cosine lives in
   ``[-1, 1]`` with a real spread near 0.05; ``log P`` runs from about −3 to −9. Added raw, α
   is not a mixing weight, it is an arbitrary reparameterisation of "prior wins" — the
   crossover happens in a sliver near α=0.99 and the five-point sweep the brief asks for would
   see nothing. The raw form is kept (``standardize=False``) and reported alongside, so the
   literal formula is measured rather than argued about.

## Why an embedding could help at all — and why it probably will not

The honest prior expectation, stated before the numbers: cosine similarity between a
*situation* sentence and a *technique name* is a lexical-semantic relation, and the thing that
makes "heel hook" the right answer from a saddle is not that the two sentences are about
similar topics — it is that one entails the other biomechanically. Embeddings are known to
score near-duplicate LABELS as neighbours ("Armbar" ⊆ "Armbar Attempt"), which is the failure
``analysis/grappling_map._synonymish`` already exists to filter. Expect the embedding to be
useful mainly as a TIEBREAKER on the long tail the counts have never seen, i.e. at small α.
The evaluation is what settles it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from analysis.next_moves import HISTORY_N, OPP, OWN, MarkovNextMoves, log_prior

_LIBRARY_PATH = Path(__file__).resolve().parent / "data" / "technique_library.json"

#: One line of domain context per event type, appended to a candidate's own text. Actions are
#: named the same in every gym; what distinguishes them for an embedding is who does what to
#: whom, which the bare label does not say. Curated, not generated — same class of artefact as
#: ``data/taxonomy/inference_table.json``.
TYPE_GLOSS: dict[str, str] = {
    "takedown": "queda: leva a luta do combate em pé para o chão, executada por quem derruba",
    "sweep": "raspagem: quem está por baixo inverte a posição e assume por cima",
    "pass": "passagem de guarda: quem está por cima ultrapassa as pernas do adversário",
    "submission": "finalização: alavanca ou estrangulamento que encerra o combate",
    "escape": "fuga: quem está na posição inferior se recupera e sai dela",
    "transition": "transição: movimento de ligação que muda a posição sem finalizar nem passar",
    "guard": "guarda: posição de quem está por baixo controlando com as pernas",
    "control": "controle: posição dominante mantida por quem está por cima",
}

ALPHAS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


# ── text builders (the only thing that ever gets embedded) ──────────────────────


def candidate_text(entry: Mapping[str, Any]) -> str:
    """One candidate technique → the string whose vector represents it.

    ``en · pt · type · variants`` plus the type's gloss line, per the brief. Variants are the
    corpus's own synonym spellings and are what make a query phrased in Portuguese, or with a
    gym nickname, land near the right candidate at all.
    """
    en = str(entry.get("en", "")).strip()
    pt = str(entry.get("pt", "")).strip()
    etype = str(entry.get("type", "")).strip()
    variants = [str(v).strip() for v in (entry.get("variants") or []) if str(v).strip()]
    head = " · ".join(p for p in (en, pt, etype) if p)
    parts = [head]
    if variants:
        parts.append("também: " + ", ".join(variants))
    gloss = TYPE_GLOSS.get(etype)
    if gloss:
        parts.append(gloss)
    return "\n".join(parts)


def transition_text(state: str, action: str, target_state: str, rel: str) -> str:
    """An OBSERVED corpus transition as a candidate string — the brief's second item form.

    ``"de <estado> para <estado> via <ação> (<ator>)"``. Kept for the variant that ranks
    observed transitions rather than library entries; unused by the default run, which ranks
    the library because the App needs to be able to suggest a move nobody in the corpus has
    hit yet from this exact position.
    """
    who = {OWN: "ela mesma", OPP: "a adversária"}.get(rel, "ator indeterminado")
    return f"de {state} para {target_state} via {action} ({who})"


def query_text(
    state: str,
    history: Sequence[Any] = (),
    actor: str = OWN,
    *,
    state_type: str = "",
    history_n: int = HISTORY_N,
) -> str:
    """The situation on the mat → the string whose vector is the query.

    Deliberately written the way the candidate texts are written (canonical label, Portuguese,
    type gloss), because cosine between two differently-shaped strings measures the shape as
    much as the content.
    """
    lines = [f"posição atual: {state}"]
    gloss = TYPE_GLOSS.get(state_type)
    if gloss:
        lines.append(gloss)
    recent = list(history)[-history_n:]
    if recent:
        told = []
        for h in recent:
            label = h if isinstance(h, str) else str(h[0])
            rel = "" if isinstance(h, str) else str(h[1])
            told.append(f"{label} ({ {OWN: 'ela', OPP: 'adversária'}.get(rel, '?') })")
        lines.append("ações recentes: " + " → ".join(told))
    lines.append(
        {
            OWN: "a próxima ação é dela. Qual técnica vem agora?",
            OPP: "a próxima ação é da adversária. Qual técnica vem agora?",
        }.get(actor, "a próxima ação pode ser de qualquer uma das duas. Qual técnica vem agora?")
    )
    return "\n".join(lines)


def build_candidate_texts(
    vocab: Sequence[str], library: Sequence[Mapping[str, Any]] | None = None
) -> list[str]:
    """``vocab`` → one text per label, in the same order.

    A label the library does not carry (it exists in the corpus but nobody curated an entry)
    falls back to the bare label. That is a real gap, not an error — it is reported by
    ``scripts/eval_next_moves.py`` as ``n_uncurated`` so the number is visible rather than
    absorbed.
    """
    lib = library if library is not None else json.loads(
        _LIBRARY_PATH.read_text(encoding="utf-8")
    )
    by_label = {str(e.get("en", "")): e for e in lib}
    return [candidate_text(by_label.get(lb, {"en": lb})) for lb in vocab]


# ── the blend ───────────────────────────────────────────────────────────────────


def _z(x: np.ndarray) -> np.ndarray:
    """Zero-mean unit-variance, and all-zeros when there is nothing to standardise."""
    s = float(x.std())
    return (x - float(x.mean())) / s if s > 0 else np.zeros_like(x)


def blend(
    cos: np.ndarray, logp: np.ndarray, alpha: float, *, standardize: bool = True
) -> np.ndarray:
    """``α · cos + (1 − α) · log P``, optionally z-scored per query (see module docstring)."""
    c, p = (_z(cos), _z(logp)) if standardize else (cos, logp)
    return alpha * c + (1.0 - alpha) * p


class EmbedRanker:
    """A candidate matrix plus the blend — ranks a query vector against the vocabulary.

    ``matrix`` is ``(len(vocab), d)`` and MUST be L2-normalised row-wise (both sources are:
    ``embed_texts`` passes ``normalize_embeddings=True``, and ``gemini-embedding-2`` returns
    unit vectors at ``output_dimensionality=768``). Checked on construction rather than
    trusted — an un-normalised row silently turns cosine into an unbounded dot product and the
    α sweep would be measuring vector length.
    """

    def __init__(self, vocab: Sequence[str], matrix: np.ndarray) -> None:
        m = np.asarray(matrix, dtype=np.float64)
        if m.ndim != 2 or m.shape[0] != len(vocab):
            raise ValueError(f"matrix {m.shape} does not match {len(vocab)} candidates")
        norms = np.linalg.norm(m, axis=1)
        if m.size and not np.allclose(norms, 1.0, atol=1e-3):
            raise ValueError("candidate matrix rows are not L2-normalised")
        self.vocab = list(vocab)
        self.matrix = m

    def cosines(self, qvec: np.ndarray) -> np.ndarray:
        q = np.asarray(qvec, dtype=np.float64)
        n = float(np.linalg.norm(q))
        return self.matrix @ (q / n if n > 0 else q)

    def rank(
        self,
        qvec: np.ndarray,
        logp: Mapping[str, float],
        *,
        alpha: float = 0.5,
        k: int = 5,
        standardize: bool = True,
    ) -> list[tuple[str, float]]:
        """Top-``k`` ``[(label, blended score)]``. Ties broken on the label (total order)."""
        cos = self.cosines(qvec)
        lp = np.array([logp[lb] for lb in self.vocab], dtype=np.float64)
        s = blend(cos, lp, alpha, standardize=standardize)
        order = sorted(range(len(self.vocab)), key=lambda i: (-s[i], self.vocab[i]))
        return [(self.vocab[i], float(s[i])) for i in order[: max(0, k)]]


def hybrid_rank_fn(
    model: MarkovNextMoves,
    ranker: EmbedRanker,
    qvec_of: Any,
    *,
    alpha: float,
    standardize: bool = True,
) -> Any:
    """``(point, k) -> [(label, score, rel)]`` for :func:`analysis.next_moves.evaluate`.

    ``qvec_of(point)`` supplies the query vector — a lookup into the disk cache in the eval
    script, so this stays offline.
    """

    def fn(p: Any, k: int) -> list[tuple[str, float, str]]:
        lp = log_prior(model, p.state, p.history)
        out = ranker.rank(qvec_of(p), lp, alpha=alpha, k=k, standardize=standardize)
        return [(lb, sc, model.rel_of(p.state, lb)[0]) for lb, sc in out]

    return fn


# ── guidance for the vision reader ──────────────────────────────────────────────

_PT_BY_LABEL: dict[str, str] | None = None


def _pt_of(label: str) -> str:
    global _PT_BY_LABEL
    if _PT_BY_LABEL is None:
        lib = json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
        _PT_BY_LABEL = {str(e.get("en", "")): str(e.get("pt", "")) for e in lib}
    return _PT_BY_LABEL.get(label, "")


GUIDANCE_DISCLAIMER = (
    "These are CORPUS STATISTICS, not ground truth and NOT a constraint. They say what "
    "usually follows this position in past competition footage. If what you actually see is "
    "not on the list, answer what you see — a move outside the list is expected and correct "
    "whenever the footage shows it. Never let this list change a reading you can make from "
    "the image."
)


def guidance_block(
    state: str,
    history: Sequence[Any] = (),
    actor: str = OWN,
    k: int = 8,
    *,
    model: MarkovNextMoves | None = None,
    ranker: EmbedRanker | None = None,
    qvec: np.ndarray | None = None,
    alpha: float = 0.25,
    state_type: str = "",
) -> str:
    """Prompt-ready text: the top-``k`` likely next moves from ``state``, with probabilities.

    Pure and network-free. With ``ranker``/``qvec`` it ranks by the hybrid score; without them
    (or without a fitted ``model``) it falls back to the Markov prior alone, which is why this
    is testable offline and why a caller with no embedding cache still gets a usable block.

    **The printed number is always the Markov probability**, even when the ORDER comes from the
    hybrid. A blended score is not a probability and printing one as if it were would be the
    kind of number a reader trusts and should not — the corpus frequency is the only figure
    here with a meaning outside this module.

    Intended consumer: the frame-reading vision prompt (``docs/PROMPT_gemini_frame_reading.md``,
    driven by ``scripts/gemini_read_frames.py``), appended per page with the previous page's
    last state as ``state``. Wiring is another agent's task; this function is the contract.
    """
    if model is None:
        return ""
    lp = log_prior(model, state, history)
    if ranker is not None and qvec is not None:
        order = [lb for lb, _ in ranker.rank(qvec, lp, alpha=alpha, k=k)]
    else:
        order = [lb for lb, _ in model.rank_next_moves(state, history, k)]

    probs = model.dist(state, history)
    who = {OWN: "the athlete in this position", OPP: "her opponent"}.get(actor, "either athlete")
    lines = [
        f"## Likely next moves from “{state}” ({who})",
        "",
        GUIDANCE_DISCLAIMER,
        "",
    ]
    recent = list(history)[-HISTORY_N:]
    if recent:
        told = [h if isinstance(h, str) else str(h[0]) for h in recent]
        lines.append(f"Recent actions: {' → '.join(told)}")
        lines.append("")
    for lb in order:
        pt = _pt_of(lb)
        side, share = model.rel_of(state, lb)
        who_s = {OWN: "hers", OPP: "opponent's"}.get(side)
        tail = f", usually {who_s} ({share:.0%} of attributable cases)" if who_s else ""
        name = f"{lb} · {pt}" if pt and pt != lb else lb
        lines.append(f"- {name} — {probs[lb]:.1%}{tail}")
    return "\n".join(lines)


def _demo() -> None:
    """Self-check — no network, no database, no model download."""
    from analysis.next_moves import MarkovNextMoves, decision_points

    seq = [
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Armbar", "type": "submission", "actor_id": "A"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Triangle Choke", "type": "submission", "actor_id": "A"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Armbar", "type": "submission", "actor_id": "A"},
    ]
    pts = decision_points(seq, "b1")
    vocab = ["Armbar", "Triangle Choke", "Heel Hook"]
    m = MarkovNextMoves(vocab).fit(pts)

    txt = query_text("Closed Guard", [("Armbar", OWN)], OWN, state_type="guard")
    assert "Closed Guard" in txt and "Armbar" in txt

    eye = np.eye(3)
    r = EmbedRanker(vocab, eye)
    lp = log_prior(m, "Closed Guard", ())
    # α=0 is the pure prior: same order the Markov model gives on its own.
    assert [lb for lb, _ in r.rank(eye[0], lp, alpha=0.0, k=3)] == [
        lb for lb, _ in m.rank_next_moves("Closed Guard", (), 3)
    ]
    # α=1 is the pure embedding: the candidate whose vector IS the query wins.
    assert r.rank(eye[2], lp, alpha=1.0, k=1)[0][0] == "Heel Hook"

    g = guidance_block("Closed Guard", [("Armbar", OWN)], OWN, k=2, model=m)
    assert "corpus statistics" in g.lower() and "%" in g
    assert "Armbar" in g
    print("next_moves_embed demo ok")
    print(g)


if __name__ == "__main__":
    _demo()
