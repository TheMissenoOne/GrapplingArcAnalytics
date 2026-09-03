# Next-move ranking — method, measurement, verdict

**Modules:** `analysis/next_moves.py` (Markov baseline) · `analysis/next_moves_embed.py`
(embedding + hybrid + prompt guidance) · **Runner:** `scripts/eval_next_moves.py` ·
**Artefacts:** `data/next_moves/eval.csv`, `data/next_moves/eval_meta.json` ·
**Tests:** `tests/test_next_moves.py` (28)

The question: *given the position on the mat right now, rank the techniques most likely to come
next.* Three consumers were in view — an App/Web suggestion, a dossier insight, and (the one
that motivated the round) a **guidance block appended to the Gemini vision prompt** that reads
frame sheets, so the reader knows what usually follows the position it is looking at.

**Verdict up front: the smoothed Markov count model wins. Neither embedding source beats it,
and neither should ship as a ranker.** The pre-registered margin was not approached — it was
missed in the wrong direction by every variant. The recommendation is Markov-pure, and the
lever that would actually move the numbers is corpus repair, not modelling.

---

## 1. Privacy class

**A — public competition data only.** Every input is a `matches` row (`status='final'`), which
is the athlete side of the corpus by construction. A next-move ranking is shown to third
parties and it ranks techniques, so it is a **competitive artefact**: under the root
`CLAUDE.md` rule, no `owner_kind='user'` graph, `user_sessions` row or profile may enter it, in
any aggregate. `scripts/eval_next_moves.py` reads exactly one table and writes to none.

---

## 2. What a decision point is

The corpus stores a bout as a flat event list. The walk reads it through
`taxonomy_kind.kind_of_entry` — the same state/action classifier the taxonomy migration uses,
which resolves the label through the App technique library first so a stale logged `type`
cannot misread an action as a state:

* an event classified **state** updates `current_state` (and remembers whose it is);
* an event classified **action** is a decision point: *(state, last 3 action labels)* → *this
  action's label*.

Measured on the corpus pulled 2026-09-02:

| | |
|---|---|
| bouts (`status='final'`, non-null sequence) | **742** |
| bouts whose `actor_id` passes `attribution.bout_flags` (`perspective_reliable`) | **610** (82.2%) |
| action events | **5 585** |
| …of which precede the bout's first state event and are dropped | **1 578** (28.3%) |
| **decision points** | **4 007** |
| candidate vocabulary (train actions ∪ library actions) | **198** |
| validation targets out of vocabulary | 6 (0.7%) |

Two deliberate exclusions, both counted rather than hidden:

1. **Actions before the first state are dropped.** They are real (a bout opens standing), but
   the query the product issues always *carries* a state — the App knows the user's position,
   the vision model reads it off the frame. Scoring on contexts the product never issues would
   flatter the number.
2. **Self-transitions are kept.** `network_from_sequences` drops A→A and `normalize_chain`
   folds consecutive repeats; both would delete a genuinely predictive cell (the repeated pass
   attempt is the modal continuation of a passing sequence). Same choice `lamas_chain` made.

### The actor is a second-class field, and that is measured, not assumed

`docs/match_event_model.md` records 307 of 700 corpus bouts filing **every** event under one
athlete. So the headline target is the action **label**, cross-actor — the bout's flow, the
same reason `lamas_chain.chain_of` chains cross-actor. Relative actor (own/opponent, relative
to whoever owns the current state) rides alongside as `rel`, is `"unk"` on every bout the gate
refuses, and is scored **separately** as `joint_top3` over the 631 gated validation points.
Reporting one 4 000-point number that silently mixes a reliable field with an unreliable one is
the failure this split exists to avoid.

---

## 3. Pre-registration (fixed before any number was produced)

* **Split:** 80/20 **by bout**, `seed = 20260902` (`next_moves.split_by_bout`). Never by
  decision point — two points from the same bout share an athlete pair and a state vocabulary.
  Result: 429 train bouts / 3 173 points, 107 validation bouts / 834 points.
* **α is chosen on TRAIN, reported on VALIDATION.** A variant whose α was picked on validation
  is not held out.
* **Metrics:** top-1 / top-3 / top-5 accuracy and truncated MRR over the label; `joint_top3`
  (label **and** relative actor) on the gated subset.
* **Verdict rule:** the embedding or hybrid wins only if validation **top-3 ≥ Markov + 5
  percentage points**. Anything smaller is a tie at this sample size.

The 5-point margin is honest against the noise: the 95% **cluster** bootstrap over bouts
(`stats_rigor.bootstrap_ci`, `groups=bout_id`) puts the Markov baseline's top-3 at
**0.272 [0.231, 0.313]** — a half-width of ±4.1 pp. Anything under 5 pp is inside the interval.

---

## 4. Models

### Markov (the baseline)

Witten-Bell interpolation over three levels — `(state, prev_action)` → `(state)` → unigram:

```
P_n(a | c) = (count(c, a) + T(c) · P_{n−1}(a)) / (N(c) + T(c))
```

`T(c)` = distinct continuations after `c`, `N(c)` = their total. A context seen once with one
continuation is trusted half; one seen 200 times with 3 continuations is trusted almost fully.
The base level is Lidstone add-one over the fixed vocabulary, which makes every distribution
strictly positive and sum to exactly 1 (asserted). Witten-Bell rather than a swept λ because a
swept λ needs its own fold and this baseline must be reproducible without one.

### Embedding + hybrid

`score = α · cos(query, candidate) + (1 − α) · log P_markov`, α ∈ {0, .25, .5, .75, 1}.

* **`log` P, not P** — the prior is heavy-tailed; a linear blend with raw probability *is* the
  prior for every α below ~0.99 and the sweep would measure nothing.
* **Both terms z-scored per query** by default. Cosine lives in a ~0.05 band; `log P` runs −3 to
  −9. Added raw, α is not a mixing weight. The raw form is measured too (`-raw` rows), so the
  literal formula in the brief is in the table rather than argued about.

Two sources, both 768-d and L2-normalised so cosine is a dot product and the columns compare:

| source | model | text | cost |
|---|---|---|---|
| `mpnet` | `sentence-transformers/all-mpnet-base-v2` (`analysis/embeddings.MODEL_NAME`) | same as gemini | free, local, offline |
| `gemini` | `gemini-embedding-2`, `output_dimensionality=768`, `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` | same as mpnet | 38 API calls, ~881 k chars ≈ 220 k tokens, once |

**Candidate text** = `en · pt · type` + the corpus's own variant spellings + a curated one-line
type gloss (`next_moves_embed.TYPE_GLOSS`). **Query text** = current position + its type gloss +
the last 3 actions with their relative actor + the question — deliberately written in the same
register as the candidates, because cosine between two differently-shaped strings measures the
shape as much as the content. At evaluation time the query claims **no** actor: whose action
comes next is half of what is being predicted.

> The stored `technique_nodes.embedding` column (358 of 383 rows populated) is the **same model**
> but **not the same text** — `embeddings.node_text` encodes `label · type · pt` only. The
> candidate vectors here were re-encoded locally; that takes about four seconds for 198 texts.

Rejected variant: candidates phrased as observed transitions (`"de <estado> para <estado> via
<ação>"`). It makes the candidate matrix state-dependent — 198 actions × 78 states = 15 444
vectors instead of 198 — and the α=1 result below is not a text-phrasing gap, it is a
30-point gap.

---

## 5. Results (`data/next_moves/eval.csv`)

Validation, n = 834 decision points across 107 held-out bouts. `joint_top3` over the 631 gated
points. `Δ` is top-3 against the Markov baseline; the pre-registered win needs Δ ≥ +5.0 pp.

| variant | top-1 | **top-3** | 95% CI | top-5 | MRR | joint top-3 | Δ top-3 | wins? |
|---|---|---|---|---|---|---|---|---|
| alphabetical floor | 0.4% | 0.8% | [0.1, 1.6] | 1.7% | 0.008 | 0.0% | −26.4 | — |
| markov, unigram (state ignored) | 12.2% | 23.7% | [19.2, 28.7] | 30.9% | 0.189 | 15.2% | −3.5 | — |
| markov, state only | 13.5% | 27.0% | [22.3, 32.2] | 36.3% | 0.214 | 18.4% | −0.2 | — |
| **markov, state + prev action** | **15.1%** | **27.2%** | **[23.1, 31.3]** | **37.8%** | **0.228** | **20.8%** | 0.0 | baseline |
| mpnet, pure embedding (α=1) | 4.2% | 11.0% | [8.1, 14.9] | 15.3% | 0.083 | 6.3% | −16.2 | **no** |
| mpnet, best-on-train (α=0.25, raw) | 14.9% | 26.9% | [22.6, 30.9] | 37.5% | 0.227 | 20.3% | −0.4 | **no** |
| gemini, pure embedding (α=1) | 3.2% | 11.8% | [8.9, 14.9] | 16.5% | 0.080 | 8.1% | −15.5 | **no** |
| gemini, best-on-train (α=0, z) | 15.1% | 27.2% | [23.1, 31.3] | 37.8% | 0.228 | 20.8% | 0.0 | **no** |

Three things this table says plainly:

1. **Both embeddings are near-useless alone.** 11.0% and 11.8% top-3 against the count model's
   27.2%. Better than the 0.8% alphabetical floor, so the vectors are not noise — a submission
   query does pull submissions — but the signal is *lexical family*, not *what happens next*.
2. **The hybrid's own training sweep chose α ≈ 0.** Gemini's best α on train was literally 0
   (the pure prior); mpnet's was 0.25 on the *raw*, unstandardised blend, where the prior's
   6-nat spread dominates a 0.02-wide cosine — i.e. also, effectively, α ≈ 0. When the sweep is
   free to weight the embedding, it declines.
3. **Conditioning on the previous action is worth something small and real**: unigram 23.7% →
   state 27.0% → state+prev 27.2% on top-3, and 12.2% → 13.5% → 15.1% on top-1 where the
   context matters most. The state is the big step; the previous action adds a little.

### The one place an embedding had a structural reason to help — and did not

Cold start: the 33 validation points whose **state** the training bouts saw fewer than 5 times,
where the Markov context has almost nothing and backs off toward the unigram. If similarity
adds anything anywhere, it has to show up here.

| cold stratum (n = 33) | top-1 | top-3 | top-5 | MRR |
|---|---|---|---|---|
| markov | 3.0% | **18.2%** | 18.2% | 0.096 |
| mpnet α=1 | 0.0% | 6.1% | 15.2% | 0.050 |
| mpnet α=0.25 | 0.0% | 9.1% | 18.2% | 0.060 |
| gemini α=1 | 3.0% | 12.1% | 15.2% | 0.073 |
| gemini α=0.25 | 3.0% | 15.2% | **24.2%** | **0.106** |

n = 33 is far too small to decide anything (the CI on 18.2% is [3.8, 31.1]). The one row that
edges the baseline is gemini α=0.25 on top-5 and MRR — a hint, not a result, and explicitly
**not** a basis for shipping. It is the only follow-up worth a second look if the corpus grows.

### Cost

3 798 distinct texts (198 candidates + 3 600 distinct query situations, deduped from 4 007
points), 880 918 characters ≈ **220 k tokens**, **38 API calls** — one pass, then cached at
`data/next_moves/emb_cache.json` (61 MB, keyed `model\ntext`) so every re-run is free. Well
inside the round's ~200-call ceiling. mpnet cost nothing.

---

## 6. Qualitative read — where the ranker is right and where the corpus is wrong

Where it is genuinely right (validation hits at rank 1):

```
50/50 Guard · [Body Lock, Body Lock Pass, Straight Ankle Lock] → Straight Ankle Lock  ✓ rank 1
50/50 Guard · [Arm Drag, Knee Bar, Leg Drag Pass]             → Arm Drag             ✓ rank 1
Body Triangle · —                                              → Triangle Choke (29% predicted, 15 of 31 val cases)
```

These are real: 50/50 is a leg-lock position and the model has learned it; a body triangle
leads to a strangle. Where it is wrong, the corpus is usually wrong first:

```
Mount → predicted "Heel Hook 12%"          # nobody heel-hooks from mount
Side Control → predicted "Guard Recovery 41%, Escape to Standing 21%"
                                            # the OPPONENT's moves, ranked as if they were the
                                            # position-holder's — the actor field again
Closed Guard → predicted "Sweep 26%"; real val top is "Triangle Choke ×16"
X-Guard · [Guard Pull] → real "Heel Hook", predicted [Rear Body Lock, Sweep, Armbar]
```

### The measured bottleneck: the corpus's state vocabulary is degenerate

| | |
|---|---|
| distinct state labels | 78 |
| share held by the top 3 (`Back Control`, `Half Guard`, `Mount`) | **66.5%** |
| `Back Control` alone, of all 4 421 state events | **1 951 (44.1%)** |
| decision points whose state is `Back Control` | **1 825 of 4 007 (45.5%)** |
| median events between the state event and the target action | 2 (mean 2.26, p90 4, **max 26**) |
| decision points more than 3 events downstream of their state | 16.2% |

`Back Control` is not a normalisation artefact — every one of the 1 951 is literally
`("Back Control", "control")` in the raw JSON, no synonym collapsing. And it is **not one bad
batch**: Polaris 26/29/30/31/32/35/36 and CJI 2 Day 2 each run 47–68% `Back Control`. The
refiner logs the back as a state far more eagerly than any other position, and it logs
positions far more rarely than actions — so "current state" is stale by two events at the
median and by up to 26 at the tail, which is how a heel hook ends up ranked from mount.

**That is the ceiling this experiment hit.** No ranker over these labels can be much better
than 27% top-3 when 45% of the questions are "you are in back control" and the answer arrives
two unlogged position changes later. Fixing `docs/PROMPT_events_sidecar.md` to log a state
event on every position change is worth more than any model on this list.

---

## 7. Recommendation

**Ship the Markov ranker. Do not ship an embedding ranker.**

`MarkovNextMoves` fitted on the full public corpus, `rank_next_moves(state, history, k)`. It is
a few hundred kB of counts, deterministic, offline, explainable ("this is what the corpus did"),
and it is the best of everything measured. The embedding work is recorded here as a negative
result with its numbers, not deleted — `analysis/next_moves_embed.py` keeps the blend so the
sweep can be re-run in one command if the corpus changes shape.

Present it as **statistics, never as advice**: 27% top-3 is a ranking of tendencies, not a
coaching recommendation, and the App copy must say so the same way Grappling ELO is always
presented relative.

### Where it lands in the product

**Server / export side, never on the device as a fitted model.** The App bundles no corpus.

* **Vision guidance (the immediate consumer).** `next_moves_embed.guidance_block(state,
  history, actor, k=8, model=…)` returns prompt-ready text — top-K with the **Markov**
  probability, canonical `en · pt`, the relative actor where the gate allows it, and the
  disclaimer *"corpus statistics, not ground truth and NOT a constraint"* stated before the
  list. It is pure and network-free; with no `ranker`/`qvec` it ranks by the prior alone, which
  is what the recommendation says to do. Wiring it into `scripts/gemini_read_frames.py` behind
  a `--guidance` flag, page by page with the previous page's last state feeding the next, is a
  separate task and a separate agent — **that script was not touched here.** See §8.
* **App / Web suggestion.** Export the fitted counts the way `export/tech_library.py` exports
  the library: a small JSON of `{state: [[label, p], …]}` truncated to the top ~10 per state
  (78 states × 10 ≈ 780 rows, single-digit kB), bundled or served. The device does a dict
  lookup — zero inference, works in airplane mode, no embedding, no key. If the hybrid ever
  wins a future round, the candidate vectors are precomputed server-side and the device needs
  ONE query embed — which is exactly the cost the App does not currently pay and should not
  start paying for a 0-point gain.

### Not recommended, and why

| rejected | why |
|---|---|
| Gemini **generating** the ranking (the round's original item 2) | superseded by the owner mid-round; and the embedding result makes the prior expectation worse, not better — the corpus prior is the signal, and a generator does not have it unless you paste it in |
| Embedding-only ranker | 11–12% top-3 against 27%. Not close |
| Hybrid at any α | best-on-train α was 0 (gemini) / effectively 0 (mpnet raw); validation Δ is −0.4 pp and 0.0 pp |
| Per-node/per-transition candidate texts | 15 444 vectors for a gap that is not about phrasing |
| Shipping the actor prediction as a headline | `joint_top3` is 20.8% on the 82% of bouts whose actor field survives the gate; it is a caveated secondary number, not a product claim |

---

## 8. Reproducing / re-running

```bash
uv run python -m analysis.next_moves            # module self-check, no DB, no network
uv run python -m analysis.next_moves_embed      # guidance-block self-check, ditto
uv run pytest tests/test_next_moves.py -q       # 28 tests, offline

set -a; source .env; set +a
uv run python -m scripts.eval_next_moves                  # markov + mpnet (free)
uv run python -m scripts.eval_next_moves --gemini         # + gemini (free after first run)
uv run python -m scripts.eval_next_moves --refresh-corpus # re-pull matches (read-only)
```

`data/next_moves/{corpus.json, emb_cache.json}` are regenerable caches (1.3 MB and 61 MB) and
belong in `.gitignore`; `eval.csv` and `eval_meta.json` are the deterministic report and belong
in the repo.

Calling the guidance block (for whoever wires `--guidance`):

```python
from analysis.next_moves import MarkovNextMoves, build_vocab, corpus_points, library_actions
from analysis.next_moves_embed import guidance_block

points, _ = corpus_points(bouts)              # public matches rows only
model = MarkovNextMoves(build_vocab(points, library_actions())).fit(points)
block = guidance_block("Back Control", [("Arm Drag", "own")], actor="own", k=8, model=model)
```

`guidance_block` returns `""` when `model is None`, so a caller with no fitted model degrades to
sending no guidance rather than sending an empty header.

---

## 9. Open, honestly labelled

* **Corpus repair beats modelling.** Log a state event on every position change; break up
  `Back Control` (back control ≠ body triangle ≠ seatbelt-no-hooks). Re-run this exact script
  afterwards — the split seed is fixed, so the delta is readable.
* **The 5-point margin is at the edge of the noise floor** (CI half-width ±4.1 pp on 107
  validation bouts). A future round that wants to detect a 3-point effect needs more bouts, not
  a different metric.
* **Train↔validation gap is large by construction** (top-3 64.6% train vs 27.2% validation).
  That is memorisation of training contexts, not a bug; it is why the validation column is the
  only one quoted in §5 and why α was chosen on train.
* **`gemini α=0.25` on the cold stratum** is the single unresolved hint (§5). n = 33. Revisit
  only with a bigger corpus, and pre-register it as its own question.
* **Not measured here:** whether the guidance block actually improves the vision reader's
  concordance. That is the experiment the next agent runs, and it needs its own held-out
  bouts — the same `20260902` split is the natural choice so the two results compose.
