# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T00:15:00.292667Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T001454Z-g3_1-m1c`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | BLOCKED | 8 | 7 |  |
| W13_T015_units | W13 | BLOCKED | 8 | 6 | definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callb |
| R04_LIVE | R | PASS | 16 | 9 |  |
| GUARD_T010 | GUARD | PASS | 12 | 5 |  |
| GUARD_T005 | GUARD | PASS | 8 | 4 |  |
| GUARD_T033 | GUARD | PASS | 14 | 6 |  |

cases: PASS 4 / FAIL 0 / BLOCKED 2 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — BLOCKED
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `NOT_RUN` [live] variables_two_in_one_group — dependency not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)
- `NOT_RUN` [live] varnames_contains_modified_variable — dependency not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)
- `NOT_RUN` [live] no_bogus_name_expr_variables — dependency not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)
- `NOT_RUN` [live] expression_evaluates_after_modification — dependency not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)
- `NOT_RUN` [live] component_and_global_scope — dependency not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)

### W13_T015_units — BLOCKED
- `PASS` [static] static_physics_unit_ops_availability
- `BLOCKED` [live] surface_source_W_per_m2 — definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `BLOCKED` [live] volume_source_W_per_m3 — definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `BLOCKED` [live] coordinate_unit_m_and_mm — definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `BLOCKED` [live] wrong_unit_warns_or_fails — definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged
- `BLOCKED` [live] no_implicit_thickness_or_absorptivity — definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callback did not declare a pre-dispatch stage, so the engine state cannot be shown to be unchanged

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

### GUARD_T010 — PASS
- `PASS` [static] static_idempotency_contract_published
- `PASS` [live] repeated_key_not_reexecuted
- `PASS` [live] request_hash_conflict_rejected
- `PASS` [live] same_tag_same_type_duplicate_rejected_or_idempotent
- `PASS` [live] same_tag_different_type_rejected

### GUARD_T005 — PASS
- `PASS` [static] static_evaluation_ops_available
- `PASS` [live] user_derived_nodes_preserved
- `PASS` [live] invalid_expression_does_not_delete_nodes
- `PASS` [live] temporary_nodes_cleaned

### GUARD_T033 — PASS
- `PASS` [static] static_evaluation_policy_source_recorded
- `PASS` [static] static_evaluation_policy_documented
- `PASS` [live] pure_read_rejects_or_isolates
- `PASS` [live] ephemeral_mutation_recorded_and_serial
- `PASS` [live] only_own_temporary_nodes_cleaned
- `PASS` [live] evaluation_expression_kinds_routed — some kinds were not routed on this model; their routing conditions are recorded per kind and none was silently treated as a value

## Unknown-job ledger (driver side)

- gate refusals: 0 (none)
- recorded UNKNOWN jobs: 14 (released: 14, unreleased: 0)
- calls that stayed refused after the release path ran: 1

- `2d981494-9492-43dc-aa57-590a8c89cd8a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `0da60590-935b-4369-b928-3107a4ddf090` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `02900f5e-57c1-4af8-91a4-78e8fd625702` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `a5b88881-759a-4205-bd32-db75b48a8e32` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `a60029b2-ffe2-44c3-b6ab-fc2d90195e9a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `645e8376-9c2f-4913-a672-7031453868fa` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `4aa5726a-abc3-41c7-8d40-97cf00feae9c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `56cfe324-c044-4060-a6ed-48dbcafb848b` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `ccfff8c1-bd25-45f0-a9af-f193b1f1056a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `0ef6575d-2b2e-4596-b46e-94872f9bfd2e` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `256c8e15-861a-4736-834e-1433e8b101d5` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `699ea219-94e9-4910-9956-6b46a22f804a` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `fa5a745a-3437-42aa-b6b4-1b8bb0162799` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `ac2829f4-7f1a-43f3-b2d2-6417425189ec` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 1, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **DEPENDENCY_BLOCKED** (DEPENDENCY_BLOCKED): the case's own prerequisite is not established: component:comp1 (the component list could not be read (INVALID_REQUEST): missing required operation arguments: t
- `W13_T015_units` → **IMPLEMENTATION_GAP** (classified): definition.component_manage returned EXECUTION_STATE_UNKNOWN: definition.component_manage failed with TAG_CONFLICT (component 'comp1' already exists); the callb

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
  - established: comp1 → UNSATISFIED (the component list could not be read (INVALID_REQUEST): missing required operation arguments: tag)
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `R04_LIVE`: isolation=own_model (applied=True) — the stale-revision probe must not dirty the shared model; prerequisites=SATISFIED; shared binding restored: mcp4
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T033`: isolation=own_model (applied=True) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=SATISFIED; shared binding restored: mcp4

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

- logical requests planned (run/case/step/sequence keys): 83
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 5; same-key replays recorded: 2 (identical body verified: 1)
- dispatch stages recorded: dispatched 29, refused_before_engine 1, unknown 10
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 1
- unfinished jobs known to the context: 0
