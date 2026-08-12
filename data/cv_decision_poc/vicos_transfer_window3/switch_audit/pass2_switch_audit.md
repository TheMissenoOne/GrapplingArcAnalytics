# Pass-2: Switch audit — window3 (5292.0-5592.0s, 7dc97750)

STATUS: **gate "manual switch audit mostly correct" NOT MET at 0.6-floor;
fix APPLIED 2026-08-12 (role_conf_min 0.6 -> 0.85 in
poc/decision_vision/live_state.py), window3 regenerated.**

## First audit (0.6 floor — committed switches)

| switch (s) | old->new | corr pre/post | verdict |
|---|---|---|---|
| 5326.0 | a2->a1 | 33% / 44% | weak, borderline |
| 5336.0 | a1->a2 | 38% / 77% | supported (a2 takes over) |
| 5356.0 | a2->a1 | 40% / **0%** | **spurious** |
| 5358.5 | a1->a2 | 64% / 82% | supported |
| 5369.5 | a2->a1 | 18% / **10%** | **spurious** |
| 5377.5 | a1->a2 | 70% / 57% | supported |

## Root cause

Role head posterior flickers at 0.5-1 Hz near conf 0.6-0.81 during scrambles;
`persist=2` counts CONSECUTIVE ROWS (not time), and void/`none` rows never reset
pending — so two agreeing frames separated by seconds can commit a switch.
Frame-level confs at each switch:

- good switches: 5336 (a2 conf 0.99/1.00), 5358.5 (0.96), 5377.5 (0.99+0.89) — all >= 0.96
- spurious: 5356 (max 0.69), 5369.5 (max 0.81)
- weak: 5326 (0.66)

Every supported switch has frames >= 0.96; every contradicted one caps at 0.81.

## Candidate fix (validated offline on raw CSV)

`role_conf_min` 0.6 -> 0.85 as the COMMIT floor:

- kills both spurious switches (5356, 5369.5) and the weak one (5326)
- preserves 5336, and 5358.5/5377.5 become NO-OPs (correct: the a1 excursions
  they corrected never commit)
- coverage stays 100%
- tried first: persist=3 (kills ALL switches — explicit frames never run
  3-consecutive), cooldown 2-5s (suppresses a REAL switch 5377.5 — overcorrection),
  window-cluster 1-3s + instant-conf 0.85 (more flicker, 10-12 switches) —
  all rejected vs conf-floor.

## Regenerated result (2026-08-12, fix applied)

role_switch_events_smoothed: 2 -> 4 (report.json); switches committed 6 -> 4:

| switch (s) | old->new | corr pre/post | verdict |
|---|---|---|---|
| 5313.5 | a2->a1 | 29% / **0%** | **NEW anomaly, needs vision** |
| 5336.0 | a1->a2 | 38% / 77% | supported |
| 5415.0 | a2->a1 | 33% / 50% | weak |
| 5436.0 | a1->a2 | 75% / 100% | supported |

Both known-spurious + the weak 5326 gone; the two a1->a2 reversals with strong
raw evidence survive. 5313.5 is a single-frame-committed a1 that raw evidence
(29/0) contradicts — candidate for persistence-in-time (needs 2 agreeing frames
within ~2s), which is weaker than the conf floor but would catch this case.

## Method (data-driven; no vision)

For each committed role switch t, corroboration = share of explicit raw frames
(conf>=0.6) in pre-window (t-10s, t+0.5s] / post-window (t+0.5s, t+12s] that
agree with the NEW role.

## Open items

1. 5313.5 a2->a1 (corr 29/0) — single-frame commit surviving at 0.85; candidate
   for persistence-in-time (~2s window) on top of the conf floor
2. Late tail 5410-5440 — 5415 (weak) + 5436 (strongly corroborated 75/100) switches
3. 18 frames saved in switch_audit/ — vision-capable model could confirm
   top/bottom at (t-3, t, t+3.5)s for 5313.5/5326/5356/5369.5

## Files

- frames: sw*.png (3 per switch × 6), manifest.csv
- raw rows: ../state_samples_raw.csv; report: ../report.json (regenerated)
