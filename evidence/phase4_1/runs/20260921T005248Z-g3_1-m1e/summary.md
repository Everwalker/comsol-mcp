# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T00:52:57.829680Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T005248Z-g3_1-m1e`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | FAIL | 10 | 7 | expression evaluation returned One or more expressions could not be evaluated. |
| W13_T015_units | W13 | PASS | 13 | 6 |  |
| R04_LIVE | R | PASS | 15 | 9 |  |
| GUARD_T010 | GUARD | PASS | 12 | 5 |  |
| GUARD_T005 | GUARD | PASS | 8 | 4 |  |
| GUARD_T033 | GUARD | PASS | 14 | 6 |  |

cases: PASS 5 / FAIL 1 / BLOCKED 0 / NOT_RUN 0

## Subcase detail

### W13_T006_variables — FAIL
- `PASS` [static] static_variable_ops_availability
- `PASS` [static] static_legacy_variable_route_available
- `PASS` [live] variables_two_in_one_group
- `PASS` [live] varnames_contains_modified_variable
- `PASS` [live] no_bogus_name_expr_variables
- `FAIL` [live] expression_evaluates_after_modification — expression evaluation returned One or more expressions could not be evaluated.
- `NOT_RUN` [live] component_and_global_scope — the component variable evaluation did not pass; the global-scope probe was not attempted

### W13_T015_units — PASS
- `PASS` [static] static_physics_unit_ops_availability
- `PASS` [live] surface_source_W_per_m2
- `PASS` [live] volume_source_W_per_m3
- `PASS` [live] coordinate_unit_m_and_mm
- `PASS` [live] wrong_unit_warns_or_fails
- `PASS` [live] no_implicit_thickness_or_absorptivity

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
- recorded UNKNOWN jobs: 11 (released: 11, unreleased: 0)
- calls that stayed refused after the release path ran: 1

- `c79da243-843f-40bd-852e-14ea7302bc17` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `974a93e1-f3b9-489e-aa02-f92fd9fc934c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `0d8022e5-842d-424b-bea8-0334040514b2` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `0a658bd9-ad8d-4030-9698-c64019c7a93c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `f60c9d9d-67be-4a3b-838d-02072001a042` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `50f0cacb-75bb-43e0-9b3e-44293a37d606` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `b6b3d6e7-056b-49e8-adab-0ba9de8f3a2d` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `fa22bc9a-14d7-4271-8caf-da53a7d8da18` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `38e295ee-58ec-46f4-9e94-fba3a65a2780` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `9a90357d-8281-4605-b0c7-278af31420c3` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `8b6cd7e6-2d77-46c1-9400-d445b285bd81` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **IMPLEMENTATION_GAP** (classified): expression evaluation returned One or more expressions could not be evaluated.

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
  - established: comp1 → SATISFIED (the component 'comp1' was created and read back from the component list)
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `R04_LIVE`: isolation=own_model (applied=True) — the stale-revision probe must not dirty the shared model; prerequisites=SATISFIED; shared binding restored: mcp10
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T033`: isolation=own_model (applied=True) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=SATISFIED; shared binding restored: mcp10

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

- logical requests planned (run/case/step/sequence keys): 91
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 2; same-key replays recorded: 2 (identical body verified: 1)
- dispatch stages recorded: dispatched 29, dispatched_without_mutation 3, refused_before_engine 1, unknown 7
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 1
- unfinished jobs known to the context: 0
