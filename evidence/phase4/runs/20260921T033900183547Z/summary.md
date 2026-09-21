# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T03:39:01.947310Z
- run directory: `$REPO/evidence/phase4/runs/20260921T033900183547Z`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W16_T019_chainA_steady | W16 | BLOCKED | 10 | 14 | the chain component could not be created: ISOLATION_PROOF_REQUIRED |

cases: PASS 0 / FAIL 0 / BLOCKED 1 / NOT_RUN 0

## Subcase detail

### W16_T019_chainA_steady — BLOCKED
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `PASS` [live] benchmark_spec_registered_and_sane
- `BLOCKED` [live] empty_model_geometry_block — the chain component could not be created: ISOLATION_PROOF_REQUIRED
- `NOT_RUN` [live] constant_material_assigned — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] boundary_temperatures_and_insulation — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] local_mesh_built — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] stationary_study_and_solver — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] solve_produced_solution — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] linear_profile_relative_error_le_1e-4 — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] heat_flux_and_power_balance — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] sample_table_and_units_recorded — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] saved_mph_hash_recorded — prerequisite subcase empty_model_geometry_block did not pass
- `NOT_RUN` [live] pre_solve_readback_matches_spec — planned subcase recorded no observation

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainA_steady` → **IMPLEMENTATION_GAP** (classified): the chain component could not be created: ISOLATION_PROOF_REQUIRED

## Case isolation and prerequisites (C03)

- `W16_T019_chainA_steady`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED

## Slice (this run)

- stage: none (the full case order)
- live: True
- selected cases: W16_T019_chainA_steady
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 1
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 0; same-key replays recorded: 0 (identical body verified: 0)
- dispatch stages recorded: unknown 1
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
