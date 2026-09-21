# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T07:58:03.115804Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T075750Z-g3_1-chainAC3`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W16_T019_chainA_steady | W16 | PASS | 26 | 14 |  |
| W16_T019_chainC_continue | W16 | FAIL | 16 | 7 | study.run returned ENGINE_CALL_FAILED |

cases: PASS 1 / FAIL 1 / BLOCKED 0 / NOT_RUN 0

## Subcase detail

### W16_T019_chainA_steady — PASS
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `PASS` [live] benchmark_spec_registered_and_sane
- `PASS` [live] empty_model_geometry_block
- `PASS` [live] constant_material_assigned
- `PASS` [live] boundary_temperatures_and_insulation
- `PASS` [live] local_mesh_built
- `PASS` [live] pre_solve_readback_matches_spec
- `PASS` [live] stationary_study_and_solver
- `PASS` [live] solve_produced_solution
- `PASS` [live] linear_profile_relative_error_le_1e-4
- `PASS` [live] heat_flux_and_power_balance
- `PASS` [live] sample_table_and_units_recorded
- `PASS` [live] saved_mph_hash_recorded

### W16_T019_chainC_continue — FAIL
- `PASS` [static] static_chain_c_ops_availability
- `PASS` [fixture] user_style_model_available
- `PASS` [live] target_only_modified
- `PASS` [live] non_target_nodes_preserved
- `PASS` [live] manual_solver_preserved
- `PASS` [live] derived_values_and_data_association_preserved
- `FAIL` [live] solve_after_continuation — study.run returned ENGINE_CALL_FAILED

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainC_continue` → **IMPLEMENTATION_GAP** (classified): study.run returned ENGINE_CALL_FAILED

## Case isolation and prerequisites (C03)

- `W16_T019_chainA_steady`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W16_T019_chainC_continue`: isolation=checkpoint_restore (applied=False) — the user-style fixture is restored from a verified checkpoint, never edited in place; prerequisites=SATISFIED

## Slice (this run)

- stage: none (the full case order)
- live: True
- selected cases: W16_T019_chainA_steady, W16_T019_chainC_continue
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 40
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 0; same-key replays recorded: 0 (identical body verified: 0)
- dispatch stages recorded: dispatched 38, dispatched_without_mutation 1, unknown 1
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
