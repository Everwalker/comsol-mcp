# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T03:34:31.892409Z
- run directory: `$REPO/evidence/phase4/runs/20260921T033428688647Z`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W16_T019_chainA_steady | W16 | FAIL | 23 | 14 | the engine already carries a feature at tag 'ins1' but did not report a type or label that identifies it as an insulation feature (None), so the insulation part |

cases: PASS 0 / FAIL 1 / BLOCKED 0 / NOT_RUN 0

## Subcase detail

### W16_T019_chainA_steady — FAIL
- `PASS` [fixture] analytic_reference_preregistered
- `PASS` [static] static_chain_a_ops_availability
- `PASS` [live] benchmark_spec_registered_and_sane
- `PASS` [live] empty_model_geometry_block
- `PASS` [live] constant_material_assigned
- `BLOCKED` [live] boundary_temperatures_and_insulation — the engine already carries a feature at tag 'ins1' but did not report a type or label that identifies it as an insulation feature (None), so the insulation part of this line was not established
- `FAIL` [live] local_mesh_built — mesh.feature_create was refused
- `BLOCKED` [live] pre_solve_readback_matches_spec — the read-back could not be compared item by item against the specification: not observed ['thermalconductivity', 'density', 'heatcapacity', 'length_m', 'width_m', 'height_m', 'initial_value', 'hot_boundary'], not comparable []
- `PASS` [live] stationary_study_and_solver
- `NOT_RUN` [live] solve_produced_solution — a prerequisite subcase did not pass (boundary_temperatures_and_insulation=BLOCKED; local_mesh_built=FAIL)
- `NOT_RUN` [live] linear_profile_relative_error_le_1e-4 — a prerequisite subcase did not pass (boundary_temperatures_and_insulation=BLOCKED; local_mesh_built=FAIL)
- `NOT_RUN` [live] heat_flux_and_power_balance — a prerequisite subcase did not pass (boundary_temperatures_and_insulation=BLOCKED; local_mesh_built=FAIL)
- `NOT_RUN` [live] sample_table_and_units_recorded — a prerequisite subcase did not pass (boundary_temperatures_and_insulation=BLOCKED; local_mesh_built=FAIL)
- `NOT_RUN` [live] saved_mph_hash_recorded — a prerequisite subcase did not pass (boundary_temperatures_and_insulation=BLOCKED; local_mesh_built=FAIL)

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 6 (released: 6, unreleased: 0)
- calls that stayed refused after the release path ran: 0

- `c6b8e5af-d775-479c-87ed-1aeb8564c2c4` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `268961a5-c423-49dc-a7c5-81e99d017d8a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `7cea89ce-bc7b-4fd5-b16c-f8715cb4b2be` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `1038fc36-05ec-4487-aeb4-91ebf4ebce18` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `62059bc5-d23d-4400-8543-5ee86e9f23b8` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `a709cbd0-817d-4cd1-a369-0789ae791170` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W16_T019_chainA_steady` → **IMPLEMENTATION_GAP** (classified): the engine already carries a feature at tag 'ins1' but did not report a type or label that identifies it as an insulation feature (None), so the insulation part

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

- logical requests planned (run/case/step/sequence keys): 25
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 1; same-key replays recorded: 1 (identical body verified: 0)
- dispatch stages recorded: dispatched 15, dispatched_without_mutation 6, unknown 3
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
