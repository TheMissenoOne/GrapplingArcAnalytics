#!/usr/bin/env python
"""Score every next-move ranker on ONE bout-level split and write ``data/next_moves/eval.csv``.

    uv run python -m scripts.eval_next_moves                    # markov + mpnet (offline, free)
    uv run python -m scripts.eval_next_moves --gemini           # + gemini-embedding-2 (cached)
    uv run python -m scripts.eval_next_moves --refresh-corpus   # re-pull matches from the DB

Read-only against prod: one `SELECT` over `matches WHERE status='final'`, cached to
``data/next_moves/corpus.json`` so every later run is offline and byte-reproducible. Writes
nothing to any database, ever.

**Privacy class A.** The query selects the public competition corpus only — ``matches`` rows,
which is the athlete side by construction. No user graph, session or profile is read; a
next-move ranking is published to third parties and ranks techniques, so the private half of
the database is out of bounds (root ``CLAUDE.md``).

## Pre-registered, before any number was produced

* **Split**: 80/20 **by bout**, ``seed=20260902`` (``next_moves.split_by_bout``). Fixed for
  every variant; α is chosen on TRAIN and reported on VALIDATION.
* **Metrics**: top-1 / top-3 / top-5 accuracy and truncated MRR over the action label.
  ``joint_top3`` (label AND relative actor) is reported on the ``perspective_reliable`` subset
  only.
* **Verdict rule**: the embedding or hybrid "wins" only if its **validation top-3 is at least
  5 percentage points above the Markov baseline's**. Anything smaller is not a win at this
  sample size and is reported as a tie.

## Cost

Candidates are embedded once (≈200 texts). Queries are embedded once per DISTINCT situation
text, not per decision point — the cache (``data/next_moves/emb_cache.json``, keyed
``model\\ntext``) makes a re-run free. Token usage is summed from the API response and printed.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.next_moves import (  # noqa: E402
    UNK,
    MarkovNextMoves,
    build_vocab,
    corpus_points,
    evaluate,
    library_actions,
    markov_rank_fn,
    split_by_bout,
)
from analysis.next_moves_embed import (  # noqa: E402
    ALPHAS,
    EmbedRanker,
    build_candidate_texts,
    hybrid_rank_fn,
    query_text,
)

logger = logging.getLogger("eval_next_moves")

OUT_DIR = REPO / "data" / "next_moves"
CORPUS_PATH = OUT_DIR / "corpus.json"
CACHE_PATH = OUT_DIR / "emb_cache.json"
CSV_PATH = OUT_DIR / "eval.csv"

GEMINI_MODEL = "gemini-embedding-2"
GEMINI_DIM = 768  # ≤1024 for cost; 768 makes it directly comparable to mpnet
GEMINI_BATCH = 100
MPNET_MODEL = "all-mpnet-base-v2"

#: Top-3 percentage points the challenger must clear to be called a win. Pre-registered.
WIN_MARGIN = 0.05

#: A validation state with fewer than this many TRAIN decision points is a "cold start" — the
#: Markov context has effectively nothing and backs off. Reported as its own stratum.
COLD_SUPPORT = 5


# ── corpus ──────────────────────────────────────────────────────────────────────


def load_corpus(refresh: bool = False) -> list[dict[str, Any]]:
    """The public bout corpus, from disk or (once) from the database."""
    if CORPUS_PATH.exists() and not refresh:
        return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    from sqlalchemy import text

    from db.base import get_session_factory

    sql = text(
        "select id::text, athlete_a_id::text, athlete_b_id::text, event, year, win_type, sequence"
        " from matches where status = 'final' and sequence is not null order by id"
    )
    with get_session_factory()() as s:
        rows = s.execute(sql).all()
    bouts = [
        {"id": r[0], "a": r[1], "b": r[2], "event": r[3], "year": r[4],
         "win_type": r[5], "sequence": r[6]}
        for r in rows
        if r[6]
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(json.dumps(bouts, ensure_ascii=False), encoding="utf-8")
    logger.info("pulled %d bouts from the database → %s", len(bouts), CORPUS_PATH)
    return bouts


# ── embedding cache ─────────────────────────────────────────────────────────────


class EmbedCache:
    """``{model + text: vector}`` on disk. The only thing standing between a re-run and a bill.

    Vectors are stored rounded to 6 decimals — the L2 norm stays within 1e-4 of 1 and the file
    is a third the size of full float repr. Keyed by model AND text so the two sources can
    share one file without ever colliding.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.data: dict[str, list[float]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(model: str, text: str) -> str:
        return f"{model}\n{text}"

    def get(self, model: str, text: str) -> list[float] | None:
        v = self.data.get(self.key(model, text))
        if v is None:
            self.misses += 1
        else:
            self.hits += 1
        return v

    def put(self, model: str, text: str, vec: list[float]) -> None:
        self.data[self.key(model, text)] = [round(float(x), 6) for x in vec]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        logger.info("cache: %d vectors (%d hits, %d misses) → %s",
                    len(self.data), self.hits, self.misses, self.path)


def embed_mpnet(texts: list[str], cache: EmbedCache) -> np.ndarray:
    """Local ``all-mpnet-base-v2`` — the model already in this repo, zero marginal cost."""
    missing = [t for t in texts if cache.get(MPNET_MODEL, t) is None]
    if missing:
        from analysis.embeddings import embed_texts

        logger.info("mpnet: encoding %d new texts locally", len(missing))
        vecs = embed_texts(missing)
        for t, v in zip(missing, vecs, strict=True):
            cache.put(MPNET_MODEL, t, list(v))
    return _matrix(MPNET_MODEL, texts, cache)


def embed_gemini(texts: list[str], cache: EmbedCache, task_type: str) -> tuple[np.ndarray, int]:
    """``gemini-embedding-2`` at 768-d. Returns the matrix and the tokens this run paid for."""
    missing = [t for t in texts if cache.get(GEMINI_MODEL, t) is None]
    tokens = 0
    if missing:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        cfg = types.EmbedContentConfig(
            task_type=task_type, output_dimensionality=GEMINI_DIM
        )
        for i in range(0, len(missing), GEMINI_BATCH):
            chunk = missing[i : i + GEMINI_BATCH]
            # A plain list[str] is read as ONE content with many parts and returns ONE vector.
            # Explicit Content objects are what make this a batch. Verified 2026-09-02.
            contents: Any = [types.Content(parts=[types.Part.from_text(text=t)]) for t in chunk]
            for attempt in range(4):
                try:
                    resp = client.models.embed_content(
                        model=GEMINI_MODEL, contents=contents, config=cfg
                    )
                    break
                except Exception as exc:  # noqa: BLE001 — retry any transient API failure
                    if attempt == 3:
                        raise
                    logger.warning("embed retry %d after %s", attempt + 1, type(exc).__name__)
                    time.sleep(2 ** attempt)
            for t, e in zip(chunk, resp.embeddings or [], strict=True):
                cache.put(GEMINI_MODEL, t, list(e.values or []))
            meta = getattr(resp, "metadata", None)
            tokens += int(getattr(meta, "billable_character_count", 0) or 0)
            logger.info("gemini: %d/%d", min(i + GEMINI_BATCH, len(missing)), len(missing))
    # No usage metadata on this endpoint → fall back to the character count we sent.
    if missing and not tokens:
        tokens = sum(len(t) for t in missing)
    return _matrix(GEMINI_MODEL, texts, cache), tokens


def _matrix(model: str, texts: list[str], cache: EmbedCache) -> np.ndarray:
    m = np.array([cache.data[EmbedCache.key(model, t)] for t in texts], dtype=np.float64)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms > 0, norms, 1.0)  # re-normalise after rounding


# ── the run ─────────────────────────────────────────────────────────────────────


def _q_text(p: Any) -> str:
    """Eval-time query: state + history, actor left UNKNOWN.

    At a decision point we do not know whose action comes next — that is half of what is being
    predicted. Claiming a side in the query would leak the answer's actor into the input on the
    43.9% of bouts where the field is unreliable AND on the rest. ``guidance_block`` takes an
    explicit actor because its caller genuinely has one.
    """
    return query_text(p.state, p.history, UNK, state_type=p.state_type)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh-corpus", action="store_true")
    ap.add_argument("--gemini", action="store_true", help="also score gemini-embedding-2")
    ap.add_argument("--no-mpnet", action="store_true")
    ap.add_argument("--out", type=Path, default=CSV_PATH)
    a = ap.parse_args()

    bouts = load_corpus(a.refresh_corpus)
    points, stats = corpus_points(bouts)
    train, val = split_by_bout(points)
    lib = library_actions()
    vocab = build_vocab(train, lib)
    logger.info(
        "%d bouts → %d decision points (%d train / %d val), vocab %d",
        stats["bouts"], len(points), len(train), len(val), len(vocab),
    )
    oov = sum(1 for p in val if p.target not in vocab)
    uncurated = len(set(vocab) - {str(e["en"]) for e in lib})

    rows: list[dict[str, Any]] = []

    def record(name: str, split: str, res: dict[str, Any], **extra: Any) -> None:  # noqa: D401
        rows.append({"variant": name, "split": split, **res, **extra})
        logger.info(
            "%-34s %-5s n=%4d top1=%.3f top3=%.3f top5=%.3f mrr=%.3f joint3=%.3f",
            name, split, res["n"], res["top1"], res["top3"], res["top5"],
            res["mrr"], res["joint_top3"],
        )

    # -- baselines + ablations -------------------------------------------------
    models: dict[int, MarkovNextMoves] = {}
    for order, label in ((0, "markov-unigram"), (1, "markov-state"), (2, "markov-state-prev")):
        m = MarkovNextMoves(vocab, max_order=order).fit(train)
        models[order] = m
        record(label, "val", evaluate(markov_rank_fn(m), val, ci=True))
    baseline = models[2]
    record("markov-state-prev", "train", evaluate(markov_rank_fn(baseline), train))

    # A vocabulary-frequency floor: rank by nothing but the label. Shows how much of top-5 is
    # just "the corpus has 198 candidates and 5 guesses".
    fixed = sorted(vocab)
    record("alphabetical-floor", "val",
           evaluate(lambda p, k: [(lb, 0.0) for lb in fixed[:k]], val, ci=True))

    # -- embedding variants ----------------------------------------------------
    cache = EmbedCache()
    cand_texts = build_candidate_texts(vocab, lib)
    q_texts = sorted({_q_text(p) for p in points})
    logger.info("candidates %d, distinct query texts %d", len(cand_texts), len(q_texts))

    sources: list[tuple[str, np.ndarray, dict[str, np.ndarray], int]] = []
    if not a.no_mpnet:
        cm = embed_mpnet(cand_texts, cache)
        qm = embed_mpnet(q_texts, cache)
        sources.append(("mpnet", cm, dict(zip(q_texts, qm, strict=True)), 0))
        cache.save()
    if a.gemini:
        cg, t1 = embed_gemini(cand_texts, cache, "RETRIEVAL_DOCUMENT")
        cache.save()
        qg, t2 = embed_gemini(q_texts, cache, "RETRIEVAL_QUERY")
        cache.save()
        sources.append(("gemini", cg, dict(zip(q_texts, qg, strict=True)), t1 + t2))

    best: dict[str, tuple[float, bool, float]] = {}
    for name, cmat, qmap, tokens in sources:
        ranker = EmbedRanker(vocab, cmat)

        def qvec(p: Any, _qmap: dict[str, np.ndarray] = qmap) -> np.ndarray:
            return _qmap[_q_text(p)]

        for std in (True, False):
            for alpha in ALPHAS:
                fn = hybrid_rank_fn(baseline, ranker, qvec, alpha=alpha, standardize=std)
                tag = f"{name}-a{alpha:g}{'' if std else '-raw'}"
                tr_res = evaluate(fn, train)
                record(tag, "train", tr_res, chars_billed=tokens)
                key = name
                if key not in best or tr_res["top3"] > best[key][2]:
                    best[key] = (alpha, std, tr_res["top3"])
        # Validation is scored ONLY at the α chosen on train — that is what makes it held out.
        alpha, std, _ = best[name]
        fn = hybrid_rank_fn(baseline, ranker, qvec, alpha=alpha, standardize=std)
        record(f"{name}-BEST-a{alpha:g}-z{int(std)}", "val", evaluate(fn, val, ci=True),
               chars_billed=tokens)
        # Pure embedding, for the record, whatever train said.
        record(f"{name}-a1-pure", "val",
               evaluate(hybrid_rank_fn(baseline, ranker, qvec, alpha=1.0), val, ci=True),
               chars_billed=tokens)

        # COLD START — the only place an embedding has a structural reason to help: validation
        # points whose STATE the training bouts barely saw, where the Markov context has almost
        # nothing to condition on and backs off to the unigram. If similarity adds anything at
        # all it has to show up here; if it does not, it does not add anything anywhere.
        support = Counter(p.state for p in train)
        cold = [p for p in val if support[p.state] < COLD_SUPPORT]
        if cold:
            record(f"{name}-cold-markov", "val-cold",
                   evaluate(markov_rank_fn(baseline), cold, ci=True))
            record(f"{name}-cold-a1", "val-cold",
                   evaluate(hybrid_rank_fn(baseline, ranker, qvec, alpha=1.0), cold, ci=True))
            record(f"{name}-cold-a0.25", "val-cold",
                   evaluate(hybrid_rank_fn(baseline, ranker, qvec, alpha=0.25), cold, ci=True))

    # -- verdict ---------------------------------------------------------------
    base3 = next(r["top3"] for r in rows if r["variant"] == "markov-state-prev"
                 and r["split"] == "val")
    for r in rows:
        if r["split"] == "val":
            r["delta_top3_vs_markov"] = round(r["top3"] - base3, 4)
            r["beats_margin"] = int(r["top3"] >= base3 + WIN_MARGIN)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["variant", "split", "n", "top1", "top3", "top3_lo", "top3_hi", "top5", "mrr",
            "joint_n", "joint_top3", "delta_top3_vs_markov", "beats_margin", "chars_billed"]
    with a.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    meta = {
        "bouts": stats["bouts"], "bouts_rel_readable": stats["bouts_rel_readable"],
        "actions": stats["actions"], "actions_without_state": stats["actions_without_state"],
        "points": len(points), "train": len(train), "val": len(val),
        "train_bouts": len({p.bout_id for p in train}),
        "val_bouts": len({p.bout_id for p in val}),
        "vocab": len(vocab), "val_oov": oov, "uncurated_labels": uncurated,
        "distinct_queries": len(q_texts), "split_seed": 20260902, "win_margin": WIN_MARGIN,
    }
    (a.out.parent / "eval_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    cache.save()
    logger.info("wrote %s (%d rows) + eval_meta.json", a.out, len(rows))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
