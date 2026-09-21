# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T03:51:15.895161Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T035200Z-g3_1-m2a-chainA`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W16_T019_chainA_steady | W16 | FAIL | 28 | 14 | the read-back could not be compared item by item against the specification: not observed ['thermalconductivity', 'density', 'heatcapacity', 'length_m', 'width_m |

cases: PASS 0 / FAIL 1 / BLOCKED 0 / NOT_RUN 0

## Subcase detail

### W16_T019_chainA_steady — FAIL
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `PASS` [live] benchmark_spec_registered_and_sane
- `PASS` [live] empty_model_geometry_block
- `PASS` [live] constant_material_assigned
- `PASS` [live] boundary_temperatures_and_insulation
- `PASS` [live] local_mesh_built
- `BLOCKED` [live] pre_solve_readback_matches_spec — the read-back could not be compared item by item against the specification: not observed ['thermalconductivity', 'density', 'heatcapacity', 'length_m', 'width_m', 'height_m', 'initial_value', 'hot_boundary'], not comparable []
- `PASS` [live] stationary_study_and_solver
- `PASS` [live] solve_produced_solution
- `FAIL` [live] linear_profile_relative_error_le_1e-4 — result.sample_path returned ENGINE_CALL_FAILED
- `FAIL` [live] heat_flux_and_power_balance — the reported boundary heat flux is missing or differs from k*ΔT/L by more than 0.1%
- `FAIL` [live] sample_table_and_units_recorded — no sample table was written
- `PASS` [live] saved_mph_hash_recorded

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 5 (released: 5, unreleased: 0)
- calls that stayed refused after the release path ran: 0

- `805d84d6-814d-495d-967e-88ef185a058c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `80a39cd5-9e29-48d9-badc-b868889e1b5d` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `90e4236b-5231-4f13-9481-da7628824ef3` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `c0329c1e-e668-49de-a2f1-fe27b816f73a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `faa40353-77c5-4ba9-914e-1511e63ea97a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainA_steady` → **IMPLEMENTATION_GAP** (classified): the read-back could not be compared item by item against the specification: not observed ['thermalconductivity', 'density', 'heatcapacity', 'length_m', 'width_m

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

- logical requests planned (run/case/step/sequence keys): 27
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 0; same-key replays recorded: 1 (identical body verified: 0)
- dispatch stages recorded: dispatched 19, dispatched_without_mutation 6, unknown 2
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
