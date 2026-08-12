# Attribution — external datasets (Decision Vision POC)

Sources used only for task-specific representation learning (position prior,
pose backend, ROI prior). They never increment Decision Space occurrence counts;
only real GrapplingArc match frames produce `A → C → B` observations.

| Key | Name / project | Source URL | License | POC use |
|---|---|---|---|---|
| bjj3 | bjj3 (bjj-885sh) | https://universe.roboflow.com/bjj-885sh/bjj3 | MIT | Position prior (primary) — consumed through Analytics `cv.roboflow_classifier` |
| bjj_recognition | BJJ Recognition Model (BJJRecognition) | https://universe.roboflow.com/bjjrecognition/bjj-recognition-model | CC BY 4.0 | BJJ-specific pose backend (follow-up; keypoint order must be verified first) |
| grappling_set | grappling set (InitialFightDataset) | https://universe.roboflow.com/initialfightdataset/grappling-set | CC BY 4.0 | Offline position/configuration pretraining |
| bjj_ai | BJJ AI (Ds Workspace) | https://universe.roboflow.com/ds-workspace-kkloo/bjj-ai | Public Domain | Optional grappler-pair/ROI detector |
| mma_set | MMA set (InitialFightDataset) | https://universe.roboflow.com/initialfightdataset/mma-set-def9q | CC BY 4.0 | Broad grappling semantics (grappling subset only) |
| rbflw_sample | Rbflw-sample (bjj-885sh) | https://universe.roboflow.com/bjj-885sh/rbflw-sample | MIT | Low priority — possible lineage/overlap with bjj3 |
| bjj_techniques | BJJ Techniques | (see registry) | UNVERIFIED | **Quarantined** — disabled until license verified |
| vicos_bjj | ViCoS BJJ Positions (Valter Hudovernik & Danijel Skočaj, ViCoS Lab, Univ. of Ljubljana) | https://vicos.si/resources/jiujitsu/ | CC BY-NC-SA 4.0 | **Research/benchmark only** — state reconstruction (position + top/bottom + identity), leave-one-sequence-out. Non-commercial: never for production model training. Citations: Hudovernik & Skočaj, *Video-Based Detection of Combat Positions and Automatic Scoring in Jiu-jitsu*, ACM MMSports'22. |

Not enabled for commercial training: `bjj_techniques` (unverified license),
`vicos_bjj` (non-commercial). `bjj3` + `grappling_set` carry
`provenance_review_required` — see `EXTERNAL_DATASETS.md` → Provenance flags.
Downloaded raw data lives under gitignored
`data/external/decision_vision/<dataset-key>/` with a `SOURCE.json` provenance
record — never committed.