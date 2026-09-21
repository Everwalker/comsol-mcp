# Phase 4 (G3) production stdio acceptance run

- generated: 2026-09-21T07:52:01.764780Z
- run directory: `$REPO/evidence/phase4_1/runs/20260921T075153Z-g3_1-m1`

| case | package | status | assertions | subcases | first finding |
| --- | --- | --- | --- | --- | --- |
| W13_T006_variables | W13 | FAIL | 10 | 7 | expression evaluation returned One or more expressions could not be evaluated. |
| W13_T015_units | W13 | PASS | 13 | 6 |  |
| R04_LIVE | R | PASS | 15 | 9 |  |
| GUARD_T010 | GUARD | PASS | 12 | 5 |  |
| GUARD_T005 | GUARD | PASS | 8 | 4 |  |
| GUARD_T033 | GUARD | PASS | 13 | 6 |  |

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
- recorded UNKNOWN jobs: 9 (released: 9, unreleased: 0)
- calls that stayed refused after the release path ran: 1

- `f85fb6be-f960-472b-91ff-ddc9de52138c` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `be191612-1642-4350-a7e3-9ad50cc3ff9e` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `8bc49bba-9a3b-4b9f-bc5e-98e9347268ba` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `c8865b35-cfcc-4630-9476-2affc77ed14a` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `c0224f26-9088-4439-81f0-1bd0d2f7d2e1` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `815de89a-e3cc-4662-a0f8-616a16d24c73` (operation_call): released=True, attempts=2, reconciled_quiescent=True, status=UNKNOWN
- `d384f808-5e68-4bd2-965d-968e195307e7` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `13abaff9-5d98-416b-8497-0e6231ece650` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN
- `3fec563b-e884-452b-80b5-4834efd9e067` (create_feature): released=True, attempts=1, reconciled_quiescent=True, status=UNKNOWN

## First causes (C03)

- per class: DEPENDENCY_BLOCKED 0, HARNESS_FAILURE 0, IMPLEMENTATION_GAP 1, EXTERNAL_BLOCKER 0
- classification rule: each case contributes at most one first cause; downstream subcases list DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects
- `W13_T006_variables` → **IMPLEMENTATION_GAP** (classified): expression evaluation returned One or more expressions could not be evaluated.

## Case isolation and prerequisites (C03)

- `W13_T006_variables`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
  - established: comp1 → SATISFIED (the component 'comp1' was created and read back from the component list)
- `W13_T015_units`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `R04_LIVE`: isolation=own_model (applied=True) — the stale-revision probe must not dirty the shared model; prerequisites=SATISFIED; shared binding restored: mcp43
- `GUARD_T010`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T005`: isolation=shared_bound (applied=False) — reads/writes the run's bound model; the run is serial; prerequisites=SATISFIED
- `GUARD_T033`: isolation=own_model (applied=True) — the evaluation-policy probes need a clean, validly bound model that no earlier refusal has dirtied; prerequisites=SATISFIED; shared binding restored: mcp43

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

- logical requests planned (run/case/step/sequence keys): 103
- refusals classified as: stale_expected 0, external_observation 0, unknown_job 0, generation_mismatch 0, ambiguous_envelope 0, explicit_key_reuse 0
- recorded new plans (replans): 1; same-key replays recorded: 2 (identical body verified: 1)
- dispatch stages recorded: dispatched 29, dispatched_without_mutation 4, refused_before_engine 1, unknown 6
- generation replacements observed: 0
- deliberate negative probes (never auto-repaired): 1
- unfinished jobs known to the context: 0
