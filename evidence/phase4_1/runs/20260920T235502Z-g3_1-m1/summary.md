# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-20T23:55:12.688438Z
- run directory: `$REPO/evidence/phase4_1/runs/20260920T235502Z-g3_1-m1`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | BLOCKED | 9 | 7 | variable.group_create returned variable.group_create failed with NODE_NOT_FOUND (component 'comp1' does not exist); the callback did not declare a pre-dispatch  |
| W13_T015_units | W13 | FAIL | 12 | 6 | a W/m^2 expression was accepted for a W/m^3 volumetric source without any warning |
| R04_LIVE | R | PASS | 16 | 9 |  |
| GUARD_T010 | GUARD | FAIL | 12 | 5 | the retry with the same idempotency key produced a different operation identity |
| GUARD_T005 | GUARD | PASS | 8 | 4 |  |
| GUARD_T033 | GUARD | BLOCKED | 14 | 6 | the build reports ephemeral_mutation=true but publishes no temporary-node ownership inventory, so node ownership cannot be established (reported keys: count, da |

cases: PASS 2 / FAIL 2 / BLOCKED 2 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — BLOCKED
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `BLOCKED` [live] variables_two_in_one_group — variable.group_create returned variable.group_create failed with NODE_NOT_FOUND (component 'comp1' does not exist); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `NOT_RUN` [live] varnames_contains_modified_variable — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] no_bogus_name_expr_variables — prerequisite subcase variables_two_in_one_group did not pass
- `NOT_RUN` [live] expression_evaluates_after_modification — prerequisite subcase varnames_contains_modified_variable did not pass
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — FAIL
- `PASS` [static] static_physics_unit_ops_availability
- `PASS` [live] surface_source_W_per_m2
- `PASS` [live] volume_source_W_per_m3
- `PASS` [live] coordinate_unit_m_and_mm
- `FAIL` [live] wrong_unit_warns_or_fails — a W/m^2 expression was accepted for a W/m^3 volumetric source without any warning
- `NOT_RUN` [live] no_implicit_thickness_or_absorptivity — prerequisite subcase wrong_unit_warns_or_fails did not pass

### R04_LIVE — PASS
- `PASS` [static] static_transaction_ops_executable
- `PASS` [protocol] static_unsupported_invariant_rejected_pre_write
- `PASS` [protocol] root_node_read_refusal_is_structured
- `PASS` [live] invariant_violation_not_reported_as_pass
- `PASS` [live] verified_transaction_status_split
- `PASS` [live] stale_revision_rejected
- `PASS` [live] cross_model_transaction_record_rejected
- `PASS` [live] recorded_pass_with_changed_live_property
- `PASS` [live] checkpoint_recovery_after_failed_invariant — a bound checkpoint exists for the failed-invariant transaction and the restore option was returned

### GUARD_T010 — FAIL
- `PASS` [static] static_idempotency_contract_published
- `FAIL` [live] repeated_key_not_reexecuted — the retry with the same idempotency key produced a different operation identity
- `PASS` [live] request_hash_conflict_rejected
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

### GUARD_T005 — PASS
- `PASS` [static] static_evaluation_ops_available
- `PASS` [live] user_derived_nodes_preserved
- `PASS` [live] invalid_expression_does_not_delete_nodes
- `PASS` [live] temporary_nodes_cleaned

### GUARD_T033 — BLOCKED
- `PASS` [static] static_evaluation_policy_source_recorded
- `PASS` [static] static_evaluation_policy_documented
- `PASS` [live] pure_read_rejects_or_isolates
- `PASS` [live] ephemeral_mutation_recorded_and_serial
- `BLOCKED` [live] only_own_temporary_nodes_cleaned — the build reports ephemeral_mutation=true but publishes no temporary-node ownership inventory, so node ownership cannot be established (reported keys: count, datetime, ephemeral_mutation, evaluation_policy, label, log_path, max_result_size_ignored, operations_path, results, server, timestamp, tool)
- `PASS` [live] evaluation_expression_kinds_routed — some kinds were not routed on this model; their routing conditions are recorded per kind and none was silently treated as a value

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 11 (released: 11, unreleased: 0)
- calls that stayed refused after the release path ran: 0

- `7222d2c4-d8e3-4ff6-8c0e-f6b3c58401af` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `c9558838-2003-4c34-94e0-55d809d417cb` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `2bcbdc28-d14c-427d-9f44-04e69f2d7836` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `5771c08f-419c-4a11-ae0d-fd850f9484e3` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `ea68248d-1841-4982-a61c-c74373ef6caf` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `d5e58870-d3ab-4597-9d14-d14fce35913d` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `fa1ba265-fc69-490a-8031-14e8f0f51436` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `ebcaf7ae-5fc7-46a7-9cc1-69adf5f4fdb5` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `db82289f-b106-407b-9a7d-070e74b31e17` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b7afa1f7-3153-4cc5-93a1-92f54f748938` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `a2f34949-5403-46b7-ba03-878f14843675` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 4, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **IMPLEMENTATION_GAP** (classified): variable.group_create returned variable.group_create failed with NODE_NOT_FOUND (component 'comp1' does not exist); the callback did not declare a pre-dispatch 
- `W13_T015_units` → **IMPLEMENTATION_GAP** (classified): a W/m^2 expression was accepted for a W/m^3 volumetric source without any warning
- `GUARD_T010` → **IMPLEMENTATION_GAP** (classified): the retry with the same idempotency key produced a different operation identity
- `GUARD_T033` → **IMPLEMENTATION_GAP** (classified): the build reports ephemeral_mutation=true but publishes no temporary-node ownership inventory, so node ownership cannot be established (reported keys: count, da

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `R04_LIVE`: isolation=own_model (applied=True) — the stale-revision probe must not dirty the shared model; prerequisites=SATISFIED
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T033`: isolation=own_model (applied=True) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=SATISFIED

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

- logical requests planned (run/case/step/sequence keys): 92
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 3; same-key replays recorded: 2
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 1
- unfinished jobs known to the context: 0
