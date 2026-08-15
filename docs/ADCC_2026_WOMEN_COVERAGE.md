# ADCC 2026 Women — Scouting Coverage Gaps

Generated from `data/scouting/adcc_2026_women.json` via:

```
uv run python -m analysis.scouting_report --manifest data/scouting/adcc_2026_women.json --division ADCC-2026-65kg --audit
uv run python -m analysis.scouting_report --manifest data/scouting/adcc_2026_women.json --division ADCC-2026-mais-65kg --audit
```

Raw JSON: [`docs/scouting/adcc-2026-audit-65kg.json`](scouting/adcc-2026-audit-65kg.json),
[`docs/scouting/adcc-2026-audit-mais-65kg.json`](scouting/adcc-2026-audit-mais-65kg.json)
(saved under `docs/` because `reports/` is gitignored — untracked JSON artifacts don't
belong in version control, but these audit snapshots are meant to be reviewed in-repo).

**Gates are UNCHANGED and this report changes none of them**: a fighter needs
`MIN_SEQUENCE_BOUTS = 3` sequence-bearing bouts in the manifest's `target_uniform`
(no-gi), AND `MIN_DOSSIER_EVENTS = 15` of their own grappling events across those bouts,
before `generate_reports()` will build their dossier. Report generation for BOTH divisions
is currently blocked by **ingestion coverage, not code** — every branch (`--audit`,
`--tabelas`, report generation) reads the same gated corpus.

## Coverage Table

| Athlete | Division | Selected bouts | Target-uniform bouts | Sequence-bearing bouts | Own events | Bouts missing to gate | Events missing to gate | Source modules | Ready |
|---|---|---:|---:|---:|---:|---|---|---|:---:|
| Livia Barasine | 65kg | 0 | 0 | 0 | 0 | 3 | 15 | — | ❌ |
| Ana Carolina Vieira | 65kg | 2 | 1 | 1 | 4 | 2 | 11 | cji2day1, yara_soares | ❌ |
| Injana Goodman | 65kg | 4 | 4 | 4 | 4 | 0 | 11 | polaris33, wwfi2 | ❌ |
| Sarah Galvão | 65kg | 2 | 2 | 2 | 22 | 1 | 0 | cji2day1, cji2day2 | ❌ |
| Nadia Frankland | 65kg | 0 | 0 | 0 | 0 | 3 | 15 | — | ❌ |
| Morgan Black | 65kg | 4 | 4 | 4 | 8 | 0 | 7 | adcc_trials2024_wc, adcc_trials2023_ec_semis, adcc_trials2023_ec_finals, supercut_2024_worlds_65kg | ❌ |
| Ane Svendsen | 65kg | 0 | 0 | 0 | 0 | 3 | 15 | — | ❌ |
| Joslyn Molina | 65kg | 0 | 0 | 0 | 0 | 3 | 15 | — | ❌ |
| Yara Soares | +65kg | 2 | 0 | 0 | 0 | 3 | 15 | yara_soares | ❌ |
| Rafaela Guedes | +65kg | 3 | 3 | 3 | 5 | 0 | 10 | adcc2022_finals, ibjjf2021_nogi_worlds | ❌ |
| Anabel Lopez | +65kg | 1 | 1 | 1 | 2 | 2 | 13 | crevar_singles | ❌ |
| Paige Ivette | +65kg | 1 | 0 | 0 | 0 | 3 | 15 | ibjjf2025_top10 | ❌ |
| Matilda Reid | +65kg | 0 | 0 | 0 | 0 | 3 | 15 | — | ❌ |
| Gabi Garcia | +65kg | 2 | 1 | 1 | 1 | 2 | 14 | adcc2022_women, yara_soares | ❌ |
| Helena Crevar | +65kg | 10 | 10 | 10 | 133 | 0 | 0 | cji2day1, cji2day2, adcc_trials2024_wc, supercut_2024_worlds_65kg, adcc_trials2023_ec_semis, adcc_trials2023_ec_finals, crevar_singles | ✅ |
| Elizabeth Mitrovic | +65kg | 2 | 1 | 1 | 0 | 2 | 15 | adcc_trials2024_wc, ibjjf2023_worlds | ❌ |

16 athletes total. Only **Helena Crevar** (10 bouts / 133 events) clears both gates. **65kg
has zero athletes passing** — this matches the previously measured baseline. (One prior
note said "9 athletes have 0 bouts"; the measured number is 5 with zero *selected* bouts
— Livia Barasine, Nadia Frankland, Ane Svendsen, Joslyn Molina, Matilda Reid — plus 3 more
with bouts selected but zero usable in the target uniform/with a sequence: Yara Soares,
Paige Ivette, Elizabeth Mitrovic (0 own events). 8 athletes at 0 own events either way —
close to the prior "9" but not exact; treat this doc's numbers as the current ground truth.)

## Buckets

- **NO DATA** (0 selected bouts) — 5: Livia Barasine, Nadia Frankland, Ane Svendsen,
  Joslyn Molina, Matilda Reid.
- **PARTIAL** (< 3 sequence-bearing bouts) — 7: Ana Carolina Vieira (1/3), Sarah Galvão
  (2/3 — closest, only needs one more bout), Yara Soares (0/3, has 2 raw bouts but wrong
  uniform/no sequence), Anabel Lopez (1/3), Paige Ivette (0/3, 1 raw bout wrong uniform),
  Gabi Garcia (1/3), Elizabeth Mitrovic (1/3).
- **ENOUGH BOUTS — NOT ENOUGH EVENTS** (≥3 sequence-bearing bouts, <15 own events) — 3:
  Injana Goodman (4 bouts/4 events), Morgan Black (4 bouts/8 events), Rafaela Guedes
  (3 bouts/5 events).
- **READY** — 1: Helena Crevar (10 bouts/133 events).

## Ingestion Backlog (ranked)

- **P0 — own athlete of each division, not ready (both currently zero usable data):**
  - Livia Barasine (65kg own athlete) — 0 selected bouts.
  - Yara Soares (+65kg own athlete) — 2 selected bouts, but wrong uniform / no
    sequence events → 0 usable toward either gate.
- **P1 — zero selected bouts (nothing in the corpus at all):** Nadia Frankland, Ane
  Svendsen, Joslyn Molina, Matilda Reid. (Livia Barasine already covered by P0.)
- **P2 — close to the 3-bout gate:** Sarah Galvão (2/3, 22 events already banked — one
  more sequence-bearing no-gi bout clears her entirely), Ana Carolina Vieira (1/3),
  Anabel Lopez (1/3), Gabi Garcia (1/3), Elizabeth Mitrovic (1/3), Paige Ivette (1 raw
  bout, needs it to land in-uniform with a sequence, then 2 more).
- **P3 — enough bouts, insufficient events:** Rafaela Guedes (3 bouts/5 events, needs
  10 more), Injana Goodman (4 bouts/4 events, needs 11 more), Morgan Black (4 bouts/8
  events, needs 7 more).
- **P4 — quality beyond the gate:** Helena Crevar is ready; further depth (more bouts/
  events) only improves dossier richness, not gate status.

## Bottom line

Both divisions' generation is blocked by **ingestion coverage**, not code — `--division`
scopes the CLI cleanly (`scope_to_division` filters `manifest["divisions"]` once, before
`--audit`/`--tabelas`/report generation all run), but scoping a manifest that has almost
no data per division just proves the gap faster. Neither `MIN_SEQUENCE_BOUTS` (3) nor
`MIN_DOSSIER_EVENTS` (15) moved. Closing P0 (both division's own athletes) is the
highest-leverage next ingestion work.
