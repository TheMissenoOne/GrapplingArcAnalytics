# Round-reader benchmark (`scripts/round_audit.py benchmark`)

Scores a `round_audit.py read`'s `out/<slug>/read.json` against a human truth timeline for
that same round -- event precision/recall/F1, actor accuracy, success-flag accuracy, and a
signed offset histogram. Matching lives in `analysis/round_benchmark.py:score_read`, pure.

    uv run python -m scripts.round_audit benchmark --in data/video/owner/rounds --only <slug> \
        [--truth <path>] [--from-sessions] [--window 5.0] [--force]

## Privacy (read this before running against prod)

**PRIVATE** (root `CLAUDE.md` / this repo's `CLAUDE.md`, "Public vs Private Data") -- every
input is the SAME owner's own footage and labels; purpose is evaluating the automatic round
reader for that same owner's product experience. Outputs stay under
`data/video/owner/out/<slug>/` (gitignored, `data/video/` in `.gitignore`) -- never
`data/finetune`, a CV/vision dataset, the athlete corpus, an archetype centroid, an athlete's
ELO, or the `site/` export. **Evaluation set only -- never training data, never a fine-tune
corpus.** Extending this to a second person's rounds is a new, documented consent decision.

## Truth sources, in priority order

1. **`out/<slug>/events_corrected.json`** -- written by the admin audit tool
   (`admin/audit.py:build_corrected_timeline`) once the owner has reviewed that round's read in
   `/admin/audit`. The highest-trust source: a human already corrected THIS read, event by
   event. Used automatically whenever it exists; `--truth`/`--from-sessions` are ignored.
2. **`--truth <path>`** -- any file in either shape `_load_truth_events` accepts: a flat list
   (`events_corrected.json`'s own shape) or `{"events": [...], "resets": [...]}`
   (`owner_truth_pull.py`'s own output, same shape as `read.json`).
3. **`--from-sessions`** -- pulled live from the consented reference-owner account(s)'
   (`analysis.reference_owner.reference_owner_ids`, `REFERENCE_OWNER_EMAILS` env) own
   `user_sessions` rows via `scripts/owner_truth_pull.py`'s pure helpers: the first round
   across every session whose `media[].filename`/`media[].id` normalizes to the same slug as
   the local video (`RoundEntry` timelines the owner typed into the App's round annotator,
   `ts`/`actor`/`label`/`type`/`successful` -- these already survive `stripMediaForSync`/
   `sessionForCloud`, no App-side export button needed).

`scripts/owner_truth_pull.py` also runs standalone to write a reusable truth file:

    uv run python -m scripts.owner_truth_pull --owner-id <uuid> --slug <slug> \
        [--out data/video/owner/truth]

Refuses any `--owner-id` that is not on the consented list. Prints no session text, only
counts.

## Matching rule

Greedy one-to-one, nearest `|Δts|` first. A pair is admissible when
`analysis.names._normalize_name(label)` agrees on both sides AND `|Δts| <= window` (default
5.0s, `--window`). Precision/recall are over event COUNTS; actor/success accuracy are over
matched pairs only (`None` when nothing matched). A duplicate label inside the window matches
at most once -- the nearer instance wins, the other stays unmatched (an extra false positive).

## Output

`out/<slug>/benchmark.json` (the full `BenchmarkResult`, resume-safe -- `--force` to redo) +
`out/<slug>/BENCHMARK.md` (the same numbers, readable) + `out/BENCHMARK.md` (one row per slug
that has a benchmark, rebuilt from every `benchmark.json` under `OUT_ROOT`, same
scan-everything convention as `report`'s own `out/README.md`).
