# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T04:28:58.935296Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T042854Z-g3_1-m2b-chainB`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W16_T019_chainB_transient | W16 | BLOCKED | 14 | 8 | study.step_create was refused: the engine state was unknown (control could not establish the request state) |

cases: PASS 0 / FAIL 0 / BLOCKED 1 / NOT_RUN 0

## Subcase detail

### W16_T019_chainB_transient — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_b_ops_availability
- `PASS` [live] benchmark_spec_registered_and_sane
- `BLOCKED` [live] transient_study_and_initial_value — study.step_create was refused: the engine state was unknown (control could not establish the request state)
- `NOT_RUN` [live] transient_solve_produced_solution — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] normalized_max_error_le_1e-3 — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] time_points_and_mesh_recorded — prerequisite subcase transient_study_and_initial_value did not pass
- `NOT_RUN` [live] pre_solve_readback_matches_spec — planned subcase recorded no observation

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainB_transient` → **IMPLEMENTATION_GAP** (classified): study.step_create was refused: the engine state was unknown (control could not establish the request state)

## Case isolation and prerequisites (C03)

- `W16_T019_chainB_transient`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED

## Slice (this run)

- stage: none (the full case order)
- live: True
- selected cases: W16_T019_chainB_transient
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 20
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 1; same-key replays recorded: 0 (identical body verified: 0)
- dispatch stages recorded: dispatched 17, dispatched_without_mutation 1, unknown 1
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
