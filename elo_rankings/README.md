# UFC Elo rankings (reference data)

Precomputed UFC fighter Elo, grabbed from **NBAtrev/UFC-Elo-Engine**
(<https://github.com/NBAtrev/UFC-Elo-Engine>) — a flat chess-style Elo over every UFC fight
scraped from ufcstats.com. Kept for future use (e.g. seeding `rank_elo` targets for MMA
crossover athletes). The reusable engine itself is extracted in `analysis/ufc_elo_engine.py`.

| file | columns | what |
|---|---|---|
| `k_factor_adjust_current.csv` | Fighter, Elo Rating | current Elo, K=40 with +15% on KO/SUB finishes |
| `k_adjust_fighter_peak_elo.csv` | Fighter, Peak Elo | peak Elo, same K-adjusted engine |
| `k_adjust_k200_peak_elo.csv` | Fighter, Peak Elo | peak Elo, high-K (≈200) variant — exaggerated spread |
| `OGcurrent_fighters_elo.csv` | Fighter, Elo Rating | current Elo, base engine (flat K=40, no finish bump) |
| `OGfighter_peak_elo.csv` | Fighter, Peak Elo | peak Elo, base engine |

Initial Elo 1000; expected score `1/(1+10^((b-a)/400))`. Top of the K-adjusted current list:
Jon Jones, Islam Makhachev. Not wired into the pipeline — reference only.
