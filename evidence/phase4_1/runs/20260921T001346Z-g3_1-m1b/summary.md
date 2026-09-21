# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T00:13:47.850271Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T001346Z-g3_1-m1b`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | BLOCKED | 5 | 7 |  |
| W13_T015_units | W13 | BLOCKED | 5 | 6 |  |
| R04_LIVE | R | BLOCKED | 6 | 8 |  |
| GUARD_T010 | GUARD | BLOCKED | 6 | 5 |  |
| GUARD_T005 | GUARD | BLOCKED | 6 | 4 |  |
| GUARD_T033 | GUARD | BLOCKED | 6 | 6 |  |

cases: PASS 0 / FAIL 0 / BLOCKED 6 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — BLOCKED
- `NOT_RUN` [static] static_variable_ops_availability — dependency not established: bound_model, observed_revision
- `NOT_RUN` [static] static_legacy_variable_route_available — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] variables_two_in_one_group — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] varnames_contains_modified_variable — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] no_bogus_name_expr_variables — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] expression_evaluates_after_modification — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] component_and_global_scope — dependency not established: bound_model, observed_revision

### W13_T015_units — BLOCKED
- `NOT_RUN` [static] static_physics_unit_ops_availability — dependency not established: bound_model
- `NOT_RUN` [live] surface_source_W_per_m2 — dependency not established: bound_model
- `NOT_RUN` [live] volume_source_W_per_m3 — dependency not established: bound_model
- `NOT_RUN` [live] coordinate_unit_m_and_mm — dependency not established: bound_model
- `NOT_RUN` [live] wrong_unit_warns_or_fails — dependency not established: bound_model
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — dependency not established: bound_model

### R04_LIVE — BLOCKED
- `NOT_RUN` [static] static_transaction_ops_executable — dependency not established: bound_model, observed_revision
- `NOT_RUN` [protocol] static_unsupported_invariant_rejected_pre_write — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] invariant_violation_not_reported_as_pass — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] verified_transaction_status_split — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] stale_revision_rejected — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] cross_model_transaction_record_rejected — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] recorded_pass_with_changed_live_property — dependency not established: bound_model, observed_revision
- `NOT_RUN` [live] checkpoint_recovery_after_failed_invariant — dependency not established: bound_model, observed_revision

### GUARD_T010 — BLOCKED
- `NOT_RUN` [static] static_idempotency_contract_published — dependency not established: bound_model
- `NOT_RUN` [live] repeated_key_not_reexecuted — dependency not established: bound_model
- `NOT_RUN` [live] same_tag_same_type_duplicate_rejected_or_idempotent — dependency not established: bound_model
- `NOT_RUN` [live] same_tag_different_type_rejected — dependency not established: bound_model
- `NOT_RUN` [live] request_hash_conflict_rejected — dependency not established: bound_model

### GUARD_T005 — BLOCKED
- `NOT_RUN` [static] static_evaluation_ops_available — dependency not established: bound_model
- `NOT_RUN` [live] user_derived_nodes_preserved — dependency not established: bound_model
- `NOT_RUN` [live] invalid_expression_does_not_delete_nodes — dependency not established: bound_model
- `NOT_RUN` [live] temporary_nodes_cleaned — dependency not established: bound_model

### GUARD_T033 — BLOCKED
- `NOT_RUN` [static] static_evaluation_policy_documented — dependency not established: bound_model, observed_revision, isolated_model
- `NOT_RUN` [static] static_evaluation_policy_source_recorded — dependency not established: bound_model, observed_revision, isolated_model
- `NOT_RUN` [live] pure_read_rejects_or_isolates — dependency not established: bound_model, observed_revision, isolated_model
- `NOT_RUN` [live] ephemeral_mutation_recorded_and_serial — dependency not established: bound_model, observed_revision, isolated_model
- `NOT_RUN` [live] evaluation_expression_kinds_routed — dependency not established: bound_model, observed_revision, isolated_model
- `NOT_RUN` [live] only_own_temporary_nodes_cleaned — dependency not established: bound_model, observed_revision, isolated_model

## Unknown-job ledger (driver side)

- gate refusals: 1 (model_create×1)
- recorded UNKNOWN jobs: 1 (released: 0, unreleased: 1)
- calls that stayed refused after the release path ran: 1

- `0f23863c-fbe8-4dfc-b43d-88e605ebb73d` (model_create): released=False, attempts=2, reconciled_quiescent=False, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 6, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 0, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model, observed_revision (no model_ref is bound: every live step of this case depends on one; the
- `W13_T015_units` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model (no model_ref is bound: every live step of this case depends on one)
- `R04_LIVE` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model, observed_revision (no model_ref is bound: every live step of this case depends on one; the
- `GUARD_T010` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model (no model_ref is bound: every live step of this case depends on one)
- `GUARD_T005` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model (no model_ref is bound: every live step of this case depends on one)
- `GUARD_T033` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's declared prerequisite(s) are not established: bound_model, observed_revision, isolated_model (no model_ref is bound: every live step of this case dep

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=UNSATISFIED; missing: bound_model, observed_revision
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=UNSATISFIED; missing: bound_model
- `R04_LIVE`: isolation=own_model (applied=False) — the stale-revision probe must not dirty the shared model; prerequisites=UNSATISFIED; missing: bound_model, observed_revision
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=UNSATISFIED; missing: bound_model
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=UNSATISFIED; missing: bound_model
- `GUARD_T033`: isolation=own_model (applied=False) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=UNSATISFIED; missing: bound_model, observed_revision, isolated_model

## Slice (this run)

- stage: M1
- live: True
- selected cases: W13_T006_variables, W13_T015_units, R04_LIVE, GUARD_T010, GUARD_T005, GUARD_T033
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
- unfinished jobs known to the context: 2
