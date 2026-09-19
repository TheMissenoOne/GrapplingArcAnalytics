# `technique_studies` — per-technique corpus digest (Pro)

Read side of the App's "study a problem" flow (plan: `you-are-taking-over-cozy-llama.md`
§ "ESTUDO SOB MEDIDA A PARTIR DE UM PROBLEMA"). One row per corpus `node_key`, batch-published
weekly from the **public** competition corpus (`matches` `status='final'` + `athletes`) — never
a user session or user graph. See root `CLAUDE.md` § "Public vs Private Data".

## Table (alembic `0067_technique_studies_and_matches_rls.py`, `db/models.py:TechniqueStudy`)

```
technique_studies(
  node_key       text primary key,   -- analysis.names._normalize_name + canonicalize
  schema_version integer not null default 1,
  payload        jsonb   not null default '{}',
  generated_at   timestamptz not null default now(),
)
```

No FK anywhere — the corpus label space is not `technique_nodes.node_key` identity (a study can
exist for a label with no shared-library row yet). RLS mirrors `athlete_dossiers_pro_select`
(0023): `technique_studies_pro_select` policy, SELECT-only, `profiles.is_pro = true`; writer is
the service-role batch job, which bypasses RLS.

Same migration also closes a dead grant leak on `matches` (RLS was already enabled there with
zero policies, but `anon`/`authenticated` still held table-level CRUD grants) — see the
migration's own docstring for the full story; unrelated to the `technique_studies` shape.

## Publisher

```bash
uv run --extra postgres python -m jobs.publish_technique_studies [--dry-run] [--node-key <k>]
```

`jobs/publish_technique_studies.py:build_all` — one pass over the PUBLIC corpus
(`export.match_breakdown._final_matches`, `analysis.network_metrics.build_transition_network`):
nodes with `occ >= MIN_OCC (3)` in the aggregate transition network get a study. Per node:

- `stats` — `frequency`/`success_rate`/`bouts` from the aggregate network + own-actor pass,
  `centrality` = PageRank (`analysis.network_metrics.node_centralities`), `reward_risk` summed
  across any synonym-merged labels sharing the `node_key`.
- `next_moves` — top 5 from `analysis.next_moves.MarkovNextMoves` fit once on the whole corpus.
- `counters` — top counter-moves from `analysis.counter_moves.counter_moves` (PtV-ranked).
- `chains` — up to 8 fixed-length (`CHAIN_LEN=3`) own-actor windows starting at the node, from
  one pass over `matches.sequence` (ponytail: simpler than `analysis.corpus_paths`'s
  path-bundling — upgrade there if a caller needs full paths instead of raw windows).
- `refs` — up to 8 video references, one per `(match, actor)`, ranked by
  `(actor ELO desc, year desc, match_id)`. `t_secs`/`precision` reuse
  `scripts.dictionary_seed.classify_ts_origin`/`absolute_ts` (evidence-based, never the raw DB
  `ts_origin` flag — see AA-010): `precision:'event'` only when the classifier is confident for
  that match, else `precision:'bout'` with `t_secs = video_start_seconds ?? t (from the video
  URL's own `t=`) ?? 0`. No video is ever probed for duration (`video_duration=None` always), so
  a match with no `video_start_seconds` classifies `'unknown'` rather than guessed
  `'absolute_from_zero'`.

`publish()` upserts every node by PK and, on a full run (no `--node-key`), deletes rows whose key
vanished from the current build. An empty full-run result is treated as a failure (refuses to
wipe the table) — a `--node-key` run never prunes anything else.

## Payload contract (App: `GrapplingArcApp/src/services/techniqueStudies.ts`)

Keys are snake_case **except the literal top-level `schemaVersion`** (camelCase — matches the
App's `sanitizeTechniqueStudy` destructure exactly; a mismatch there silently drops the whole
study). Extra keys are tolerated (dropped by the App's sanitizer); the required set:

```
{
  schemaVersion: 1,
  node_key: string,
  generated_at: string,           // ISO 8601
  stats: {
    frequency: number, success_rate: number, centrality: number,
    reward_risk: number, bouts: number,
  },
  next_moves: [{ key, label, p, count }],
  counters:   [{ key, label, count }],       // ptv/success/leads_to ride along as extras
  chains:     [{ actions: string[], count, bout_ids: string[] }],
  refs: [{
    match_id, slug, event, year: number,
    athletes: { a, b },
    vid, t_secs: number, precision: 'event' | 'bout',
    actor_name, successful: boolean | null, ts_origin: string | null,
  }],
}
```

Node key derivation is identical on both sides: `_normalize_name(clean_label(label))` (Analytics)
/ `corpusKeyFor` (App, `B2` in the plan) — same axis as every other public-corpus artefact
(`analysis.names`). No cross-repo golden fixture here (this is a one-way generated artefact, like
the athlete dossiers, not a shared pure function).

## Operations

Weekly systemd user timer, same shape as `grapplingarc-pro-analytics-weekly`:
`systemd/user/grapplingarc-technique-studies-weekly.{service,timer}` (Sun 05:00 UTC, staggered
15 min after the pro-analytics weekly run at 04:15 to avoid overlap). Not yet installed/enabled
on the host — see `docs/PRO_ANALYTICS_LOCAL_PUBLISHER.md` for the `systemctl --user enable
--now` steps, same pattern.

`--dry-run` prints the node count + a truncated sample of the first 3 payloads, writes nothing.
`--node-key <k>` republishes one node without touching (or pruning) the rest of the table.
