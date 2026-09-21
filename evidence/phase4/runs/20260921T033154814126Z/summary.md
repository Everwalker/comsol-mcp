# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T03:31:55.347498Z
- run directory: `$REPO/evidence/phase4/runs/20260921T033154814126Z`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | BLOCKED | 5 | 7 |  |

cases: PASS 0 / FAIL 0 / BLOCKED 1 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — BLOCKED
- `NOT_RUN` [static] static_variable_ops_availability — dependency not established: bound_model, observed_revision
- `NOT_RUN` [static] static_legacy_variable_route_available — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] variables_two_in_one_group — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] varnames_contains_modified_variable — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] no_bogus_name_expr_variables — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] expression_evaluates_after_modification — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] component_and_global_scope — dependency not established: bound_model, observed_revision

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 1, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 0, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model, observed_revision (no model_ref is bound: every live step of this case depends on one; the

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=UNSATISFIED; missing: bound_model, observed_revision

## Slice (this run)

- stage: none (the full case order)
- live: True
- selected cases: W13_T006_variables
- note: a slice is a documented run plan, not a weakened acceptance: every selected case still records its own subcases at their declared evidence level, and a stage subset never turns an unselected case into a pass
- M0: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038
- M1: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
- M2: W16_T019_chainA_steady, W16_T019_chainB_transient
- M3: R01_LIVE, R03_LIVE, R_READBACK, W13_T048_selection_drift, W13_T016_2D_data, W14_T009_geometry_edit, W14_T034_local_paths, W15_T007_selections, W15_T017_material, W15_T042_license, W16_T018_mesh, W16_T019_chainC_continue, W16_T020_solver, GUARD_T035, GUARD_T038

## Execution context (C02)

- logical requests planned (run/case/step/sequence keys): 0
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 0; same-key replays recorded: 0 (identical body verified: 0)
- dispatch stages recorded: none
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 0
- unfinished jobs known to the context: 0
